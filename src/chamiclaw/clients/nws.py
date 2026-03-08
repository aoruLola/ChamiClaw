from __future__ import annotations

from datetime import datetime, timezone

import httpx

from chamiclaw.core.models import ForecastSnapshot


class NwsClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0, user_agent: str = "ChamiClaw/0.5"):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    async def fetch_precipitation_forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        market_id: str,
    ) -> ForecastSnapshot:
        points_url = f"{self.base_url}/points/{latitude:.4f},{longitude:.3f}"
        headers = {"User-Agent": self.user_agent, "Accept": "application/geo+json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            points_resp = await client.get(points_url, headers=headers)
            points_resp.raise_for_status()
            points_payload = points_resp.json() if points_resp.content else {}
            forecast_url = (
                points_payload.get("properties", {}).get("forecastHourly")
                if isinstance(points_payload, dict)
                else None
            )
            if not forecast_url:
                raise ValueError("nws points payload missing forecastHourly URL")
            forecast_resp = await client.get(str(forecast_url), headers=headers)
            forecast_resp.raise_for_status()
            forecast_payload = forecast_resp.json() if forecast_resp.content else {}

        properties = forecast_payload.get("properties", {}) if isinstance(forecast_payload, dict) else {}
        periods = properties.get("periods", []) if isinstance(properties, dict) else []
        if not periods:
            raise ValueError("nws forecast payload missing periods")

        rows: list[tuple[datetime, float]] = []
        for period in periods:
            start = datetime.fromisoformat(str(period.get("startTime")))
            value = period.get("probabilityOfPrecipitation", {}).get("value")
            prob = float(value) if value is not None else 0.0
            rows.append((start, prob))

        target_date = rows[0][0].date()
        matched = [row for row in rows if row[0].date() == target_date]
        updated_raw = properties.get("updated")
        updated_at = datetime.fromisoformat(str(updated_raw)) if updated_raw else datetime.now(timezone.utc)

        return ForecastSnapshot(
            market_id=market_id,
            source="nws",
            valid_at=datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc),
            updated_at=updated_at,
            precip_probability=max((row[1] for row in matched), default=0.0) / 100.0,
            precipitation_mm=0.0,
            source_model="nws-hourly",
            raw=forecast_payload if isinstance(forecast_payload, dict) else {},
        )
