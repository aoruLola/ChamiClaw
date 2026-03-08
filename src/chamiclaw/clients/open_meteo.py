from __future__ import annotations

from datetime import datetime, timezone

import httpx

from chamiclaw.core.models import ForecastSnapshot


class OpenMeteoClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_precipitation_forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        market_id: str,
    ) -> ForecastSnapshot:
        url = f"{self.base_url}/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": "precipitation_probability,precipitation",
            "timezone": "UTC",
            "forecast_days": 2,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}

        hourly = payload.get("hourly", {}) if isinstance(payload, dict) else {}
        times = hourly.get("time", []) if isinstance(hourly, dict) else []
        probabilities = hourly.get("precipitation_probability", []) if isinstance(hourly, dict) else []
        precipitations = hourly.get("precipitation", []) if isinstance(hourly, dict) else []
        if not times:
            raise ValueError("open-meteo payload missing hourly time series")

        rows: list[tuple[datetime, float, float]] = []
        for idx, raw_time in enumerate(times):
            ts = datetime.fromisoformat(str(raw_time))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            prob = float(probabilities[idx]) if idx < len(probabilities) and probabilities[idx] is not None else 0.0
            precip = float(precipitations[idx]) if idx < len(precipitations) and precipitations[idx] is not None else 0.0
            rows.append((ts, prob, precip))

        target_date = rows[0][0].date()
        matched = [row for row in rows if row[0].date() == target_date]
        max_probability = max((row[1] for row in matched), default=0.0) / 100.0
        total_precipitation = sum(row[2] for row in matched)
        updated_at = datetime.now(timezone.utc)

        return ForecastSnapshot(
            market_id=market_id,
            source="open-meteo",
            valid_at=datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc),
            updated_at=updated_at,
            precip_probability=max_probability,
            precipitation_mm=total_precipitation,
            source_model="open-meteo-hourly",
            raw=payload if isinstance(payload, dict) else {},
        )
