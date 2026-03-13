from __future__ import annotations

from datetime import datetime, timezone

import httpx

from chamiclaw.core.models import ForecastSnapshot


class OpenMeteoClient:
    def __init__(self, base_url: str, timeout_seconds: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    async def fetch_temperature_forecast(
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
            "daily": "temperature_2m_max",
            "timezone": "UTC",
            "forecast_days": 5,
        }
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json() if resp.content else {}

        daily = payload.get("daily", {}) if isinstance(payload, dict) else {}
        times = daily.get("time", []) if isinstance(daily, dict) else []
        temp_max = daily.get("temperature_2m_max", []) if isinstance(daily, dict) else []
        if not times:
            raise ValueError("open-meteo payload missing daily time series")

        rows: list[tuple[datetime, float]] = []
        for idx, raw_time in enumerate(times):
            ts = datetime.fromisoformat(str(raw_time))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            t_max = float(temp_max[idx]) if idx < len(temp_max) and temp_max[idx] is not None else 0.0
            rows.append((ts, t_max))

        target_date = rows[0][0].date()
        matched = [row for row in rows if row[0].date() == target_date]
        if not matched:
            raise ValueError("open-meteo payload missing data for target date")
        
        max_temperature = matched[0][1]
        updated_at = datetime.now(timezone.utc)

        return ForecastSnapshot(
            market_id=market_id,
            source="open-meteo",
            valid_at=datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc),
            updated_at=updated_at,
            temperature_celsius=max_temperature,
            source_model="open-meteo-daily",
            raw=payload if isinstance(payload, dict) else {},
        )
