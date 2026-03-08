import asyncio
from datetime import date, datetime, timezone

import pytest

from chamiclaw.clients.openai_compatible import OpenAICompatibleClient
from chamiclaw.clients.webhook import WebhookNotifier
from chamiclaw.clients.open_meteo import OpenMeteoClient
from chamiclaw.clients.nws import NwsClient
from chamiclaw.core.models import LlmReviewDecision, LlmReviewRequest


def test_openai_compatible_client_parses_structured_review_response(monkeypatch):
    payload = {
        "choices": [
            {
                "message": {
                    "content": '{"decision":"resize","size_multiplier":0.5,"confidence":0.82,"risk_tags":["forecast_divergence"],"reason_summary":"conflicting official source"}'
                }
            }
        ]
    }

    class FakeResponse:
        content = b"ok"

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json():
            return payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, json=None, headers=None):
            assert json is not None
            assert headers is not None
            return FakeResponse()

    monkeypatch.setattr("chamiclaw.clients.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    client = OpenAICompatibleClient(base_url="https://llm.example.com", api_key="secret", model="gpt-weather")
    request = LlmReviewRequest(
        market_id="m1",
        market_question="Will it rain in NYC tomorrow?",
        market_rule="Official precipitation measurement decides resolution.",
        location="New York, NY",
        forecast_date=date(2026, 1, 5),
        market_probability=0.41,
        consensus_probability=0.58,
        consensus_confidence=0.77,
        data_freshness_minutes=22,
        edge=0.17,
        suggested_size_usd=25.0,
        risk_tags=["daily_precipitation"],
    )

    decision = asyncio.run(client.review_trade(request))

    assert isinstance(decision, LlmReviewDecision)
    assert decision.decision == "resize"
    assert decision.size_multiplier == 0.5
    assert decision.confidence == 0.82


def test_openai_compatible_client_rejects_invalid_json(monkeypatch):
    payload = {"choices": [{"message": {"content": "not-json"}}]}

    class FakeResponse:
        content = b"ok"

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json():
            return payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, _url, json=None, headers=None):
            return FakeResponse()

    monkeypatch.setattr("chamiclaw.clients.openai_compatible.httpx.AsyncClient", FakeAsyncClient)
    client = OpenAICompatibleClient(base_url="https://llm.example.com", api_key="secret", model="gpt-weather")
    request = LlmReviewRequest(
        market_id="m1",
        market_question="Will it rain in NYC tomorrow?",
        market_rule="Official precipitation measurement decides resolution.",
        location="New York, NY",
        forecast_date=date(2026, 1, 5),
        market_probability=0.41,
        consensus_probability=0.58,
        consensus_confidence=0.77,
        data_freshness_minutes=22,
        edge=0.17,
        suggested_size_usd=25.0,
        risk_tags=["daily_precipitation"],
    )

    with pytest.raises(ValueError):
        asyncio.run(client.review_trade(request))


def test_open_meteo_client_builds_precipitation_snapshot(monkeypatch):
    payload = {
        "latitude": 40.7128,
        "longitude": -74.0060,
        "generationtime_ms": 1.1,
        "utc_offset_seconds": 0,
        "timezone": "UTC",
        "hourly": {
            "time": ["2026-01-05T00:00", "2026-01-05T01:00", "2026-01-05T02:00"],
            "precipitation_probability": [10, 60, 50],
            "precipitation": [0.0, 1.2, 0.8],
        },
    }

    class FakeResponse:
        content = b"ok"

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json():
            return payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, params=None):
            assert params is not None
            return FakeResponse()

    monkeypatch.setattr("chamiclaw.clients.open_meteo.httpx.AsyncClient", FakeAsyncClient)
    client = OpenMeteoClient(base_url="https://api.open-meteo.com/v1")

    snapshot = asyncio.run(client.fetch_precipitation_forecast(latitude=40.7128, longitude=-74.0060, market_id="m1"))

    assert snapshot.market_id == "m1"
    assert snapshot.source == "open-meteo"
    assert snapshot.precip_probability == pytest.approx(0.6)
    assert snapshot.precipitation_mm == pytest.approx(2.0)
    assert snapshot.valid_at.date().isoformat() == "2026-01-05"


def test_nws_client_builds_precipitation_snapshot(monkeypatch):
    points_payload = {
        "properties": {
            "forecastHourly": "https://api.weather.gov/gridpoints/OKX/33,37/forecast/hourly"
        }
    }
    forecast_payload = {
        "properties": {
            "updated": "2026-01-04T22:00:00+00:00",
            "periods": [
                {
                    "startTime": "2026-01-05T00:00:00+00:00",
                    "endTime": "2026-01-05T01:00:00+00:00",
                    "probabilityOfPrecipitation": {"value": 20},
                    "isDaytime": False,
                },
                {
                    "startTime": "2026-01-05T01:00:00+00:00",
                    "endTime": "2026-01-05T02:00:00+00:00",
                    "probabilityOfPrecipitation": {"value": 70},
                    "isDaytime": False,
                },
            ]
        }
    }

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.content = b"ok"

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            self.calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None):
            self.calls.append(url)
            if url.endswith('/points/40.7128,-74.006'):
                return FakeResponse(points_payload)
            return FakeResponse(forecast_payload)

    monkeypatch.setattr("chamiclaw.clients.nws.httpx.AsyncClient", FakeAsyncClient)
    client = NwsClient(base_url="https://api.weather.gov")

    snapshot = asyncio.run(client.fetch_precipitation_forecast(latitude=40.7128, longitude=-74.0060, market_id="m1"))

    assert snapshot.market_id == "m1"
    assert snapshot.source == "nws"
    assert snapshot.precip_probability == pytest.approx(0.7)
    assert snapshot.updated_at == datetime(2026, 1, 4, 22, 0, tzinfo=timezone.utc)



def test_webhook_notifier_posts_structured_event(monkeypatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        content = b"ok"

        @staticmethod
        def raise_for_status() -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr("chamiclaw.clients.webhook.httpx.AsyncClient", FakeAsyncClient)
    notifier = WebhookNotifier(
        url="https://hooks.example.com/weather",
        service_name="chamiclaw",
        environment="prod",
    )

    delivered = asyncio.run(
        notifier.send(
            event_type="weather_batch_completed",
            summary="batch finished",
            details={"executed": 2, "rejected": 1},
        )
    )

    assert delivered is True
    assert captured["url"] == "https://hooks.example.com/weather"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["json"]["event_type"] == "weather_batch_completed"
    assert captured["json"]["service"] == "chamiclaw"
    assert captured["json"]["environment"] == "prod"
    assert captured["json"]["details"]["executed"] == 2
    assert notifier.failures_total == 0
    assert notifier.last_success_ts is not None


def test_webhook_notifier_swallow_failures_and_tracks_state(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, json=None, headers=None):
            raise RuntimeError("network down")

    monkeypatch.setattr("chamiclaw.clients.webhook.httpx.AsyncClient", FakeAsyncClient)
    notifier = WebhookNotifier(
        url="https://hooks.example.com/weather",
        service_name="chamiclaw",
        environment="prod",
    )

    delivered = asyncio.run(
        notifier.send(
            event_type="execution_error",
            summary="order failed",
            details={"market_id": "m1"},
        )
    )

    assert delivered is False
    assert notifier.failures_total == 1
    assert notifier.last_failure_ts is not None
