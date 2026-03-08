import asyncio
from datetime import date, datetime, timedelta, timezone

from chamiclaw.core.models import ForecastSnapshot, WeatherMarketMeta
from chamiclaw.engines.info import InfoEngine


class FakeOpenMeteoClient:
    async def fetch_precipitation_forecast(self, *, latitude: float, longitude: float, market_id: str):
        return ForecastSnapshot(
            market_id=market_id,
            source="open-meteo",
            valid_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=20),
            precip_probability=0.55,
            precipitation_mm=1.1,
        )


class FakeNwsClient:
    async def fetch_precipitation_forecast(self, *, latitude: float, longitude: float, market_id: str):
        return ForecastSnapshot(
            market_id=market_id,
            source="nws",
            valid_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=15),
            precip_probability=0.65,
            precipitation_mm=1.7,
        )


def test_info_engine_sets_high_risk_when_only_weak_sources():
    engine = InfoEngine()
    signal = engine.analyze(market_id="m1", source_tiers=[3, 3], event_detected=True)
    assert signal.risk_score >= 0.7
    assert signal.confirmation_level == 0


def test_info_engine_builds_weather_consensus_from_snapshots():
    engine = InfoEngine()
    valid_at = datetime(2026, 1, 5, tzinfo=timezone.utc)
    meta = WeatherMarketMeta(market_id="m1", location="New York, NY")
    snapshots = [
        ForecastSnapshot(
            market_id="m1",
            source="open-meteo",
            valid_at=valid_at,
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=25),
            precip_probability=0.60,
            precipitation_mm=1.4,
        ),
        ForecastSnapshot(
            market_id="m1",
            source="nws",
            valid_at=valid_at,
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=40),
            precip_probability=0.40,
            precipitation_mm=0.9,
        ),
    ]

    signal = engine.analyze_weather_market(meta, forecast_date=date(2026, 1, 5), snapshots=snapshots)

    assert signal.forecast_consensus is not None
    assert signal.forecast_consensus.consensus_probability == 0.5
    assert signal.forecast_consensus.dispersion == 0.2
    assert signal.forecast_consensus.stale is False
    assert signal.confirmation_level == 2
    assert signal.risk_score < 0.5


def test_info_engine_flags_stale_weather_consensus_for_review():
    engine = InfoEngine()
    valid_at = datetime(2026, 1, 5, tzinfo=timezone.utc)
    meta = WeatherMarketMeta(market_id="m1", location="Chicago, IL")
    snapshots = [
        ForecastSnapshot(
            market_id="m1",
            source="open-meteo",
            valid_at=valid_at,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=5),
            precip_probability=0.70,
            precipitation_mm=2.0,
        )
    ]

    signal = engine.analyze_weather_market(meta, forecast_date=date(2026, 1, 5), snapshots=snapshots)

    assert signal.forecast_consensus is not None
    assert signal.forecast_consensus.stale is True
    assert signal.clarification_flag is True
    assert "stale_forecast" in signal.weather_risk_tags
    assert signal.risk_score >= 0.7


def test_info_engine_fetches_weather_signal_from_clients():
    engine = InfoEngine(openmeteo_client=FakeOpenMeteoClient(), nws_client=FakeNwsClient())
    meta = WeatherMarketMeta(
        market_id="m1",
        location="New York, NY",
        latitude=40.7128,
        longitude=-74.0060,
    )

    signal = asyncio.run(engine.fetch_weather_signal(meta, forecast_date=date(2026, 1, 5)))

    assert signal.forecast_consensus is not None
    assert signal.forecast_consensus.consensus_probability == 0.6
    assert signal.confirmation_level == 2
    assert signal.data_freshness_minutes <= 20
