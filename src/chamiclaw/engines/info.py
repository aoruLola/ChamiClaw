from __future__ import annotations

from datetime import date, datetime, timezone

from chamiclaw.clients.brave import BraveClient
from chamiclaw.core.models import ForecastConsensus, ForecastSnapshot, InfoSignal, WeatherMarketMeta


class InfoEngine:
    """Brave-backed info scorer with deterministic fallback path."""

    def __init__(
        self,
        brave_client: BraveClient | None = None,
        openmeteo_client: object | None = None,
        nws_client: object | None = None,
    ):
        self.brave_client = brave_client
        self.openmeteo_client = openmeteo_client
        self.nws_client = nws_client

    @staticmethod
    def _tier_for_domain(domain: str) -> int:
        d = domain.lower()
        if d.endswith('.gov') or d.endswith('.edu') or 'official' in d:
            return 1
        if any(x in d for x in ('reuters', 'apnews', 'bloomberg', 'ft.com')):
            return 2
        return 3

    async def analyze_with_brave(
        self,
        market_id: str,
        query: str,
        *,
        event_detected: bool = False,
        clarification_flag: bool = False,
    ) -> InfoSignal:
        if self.brave_client is None:
            return self.analyze(
                market_id=market_id,
                source_tiers=[3],
                event_detected=event_detected,
                clarification_flag=clarification_flag,
            )
        results = await self.brave_client.search(query, count=5)
        tiers: list[int] = []
        top_sources: list[dict[str, str | int]] = []
        for item in results:
            url = str(item.get('url') or '')
            domain = str(item.get('profile', {}).get('name') or item.get('domain') or '')
            tier = self._tier_for_domain(domain or url)
            tiers.append(tier)
            top_sources.append(
                {
                    'domain': domain or url,
                    'tier': tier,
                    'title': str(item.get('title') or ''),
                    'time': datetime.now(timezone.utc).isoformat(),
                }
            )
        signal = self.analyze(
            market_id=market_id,
            source_tiers=tiers or [3],
            event_detected=event_detected,
            clarification_flag=clarification_flag,
        )
        signal.top_sources = top_sources
        return signal

    async def fetch_weather_signal(self, meta: WeatherMarketMeta, *, forecast_date: date) -> InfoSignal:
        snapshots: list[ForecastSnapshot] = []
        if self.openmeteo_client is not None:
            snapshots.append(
                await self.openmeteo_client.fetch_precipitation_forecast(
                    latitude=meta.latitude,
                    longitude=meta.longitude,
                    market_id=meta.market_id,
                )
            )
        if self.nws_client is not None:
            snapshots.append(
                await self.nws_client.fetch_precipitation_forecast(
                    latitude=meta.latitude,
                    longitude=meta.longitude,
                    market_id=meta.market_id,
                )
            )
        return self.analyze_weather_market(meta, forecast_date=forecast_date, snapshots=snapshots)

    def analyze(
        self,
        market_id: str,
        source_tiers: list[int],
        event_detected: bool,
        clarification_flag: bool = False,
    ) -> InfoSignal:
        if clarification_flag:
            return InfoSignal(
                market_id=market_id,
                event_detected=event_detected,
                clarification_flag=True,
                risk_score=0.8,
                confirmation_level=0,
            )

        confirmation_level = min(3, len(set(t for t in source_tiers if t in {1, 2})))
        risk_score = 0.2
        if event_detected and 1 not in source_tiers:
            risk_score = 0.5
        if event_detected and 3 in source_tiers and confirmation_level == 0:
            risk_score = 0.75

        return InfoSignal(
            market_id=market_id,
            event_detected=event_detected,
            risk_score=risk_score,
            confirmation_level=confirmation_level,
            clarification_flag=False,
            top_sources=[
                {
                    'domain': 'example.com',
                    'tier': t,
                    'title': 'signal',
                    'time': datetime.now(timezone.utc).isoformat(),
                }
                for t in source_tiers
            ],
        )

    def analyze_weather_market(
        self,
        meta: WeatherMarketMeta,
        *,
        forecast_date: date,
        snapshots: list[ForecastSnapshot],
        stale_after_minutes: int = 180,
    ) -> InfoSignal:
        if not snapshots:
            return InfoSignal(
                market_id=meta.market_id,
                clarification_flag=True,
                risk_score=0.9,
                confirmation_level=0,
                weather_risk_tags=['missing_forecast'],
            )

        consensus = self.build_forecast_consensus(
            market_id=meta.market_id,
            location=meta.location,
            forecast_date=forecast_date,
            snapshots=snapshots,
            stale_after_minutes=stale_after_minutes,
        )
        risk_tags: list[str] = []
        if consensus.stale:
            risk_tags.append('stale_forecast')
        if consensus.dispersion >= 0.2:
            risk_tags.append('forecast_divergence')
        if consensus.confidence < 0.55:
            risk_tags.append('low_consensus_confidence')

        if consensus.stale:
            risk_score = 0.8
        else:
            risk_score = round(min(1.0, 0.2 + (consensus.dispersion * 0.75)), 4)
            if len(consensus.snapshots) == 1:
                risk_score = max(risk_score, 0.45)

        return InfoSignal(
            market_id=meta.market_id,
            event_detected=consensus.consensus_probability >= 0.5,
            risk_score=risk_score,
            confirmation_level=min(3, len({item.source for item in consensus.snapshots})),
            clarification_flag=consensus.stale,
            top_sources=[
                {
                    'domain': snapshot.source,
                    'tier': 1 if snapshot.source == 'nws' else 2,
                    'title': meta.location or meta.question,
                    'time': snapshot.updated_at.isoformat(),
                }
                for snapshot in consensus.snapshots
            ],
            forecast_consensus=consensus,
            weather_risk_tags=risk_tags,
            data_freshness_minutes=consensus.freshness_minutes,
        )

    def build_forecast_consensus(
        self,
        *,
        market_id: str,
        location: str,
        forecast_date: date,
        snapshots: list[ForecastSnapshot],
        stale_after_minutes: int = 180,
    ) -> ForecastConsensus:
        probabilities = [snapshot.precip_probability for snapshot in snapshots]
        consensus_probability = round(sum(probabilities) / len(probabilities), 4)
        dispersion = round(max(probabilities) - min(probabilities), 4) if len(probabilities) > 1 else 0.0
        freshness_minutes = min(
            max(int((datetime.now(timezone.utc) - snapshot.updated_at).total_seconds() // 60), 0)
            for snapshot in snapshots
        )
        stale = freshness_minutes >= stale_after_minutes
        confidence = round(max(0.0, min(1.0, 1.0 - dispersion)), 4)
        primary_source = min(snapshots, key=lambda item: item.updated_at).source if snapshots else ''
        return ForecastConsensus(
            market_id=market_id,
            location=location,
            forecast_date=forecast_date,
            consensus_probability=consensus_probability,
            confidence=confidence,
            dispersion=dispersion,
            freshness_minutes=freshness_minutes,
            stale=stale,
            snapshots=[snapshot.model_copy(deep=True) for snapshot in snapshots],
            primary_source=primary_source,
        )
