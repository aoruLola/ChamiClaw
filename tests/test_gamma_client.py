import json
import asyncio

from chamiclaw.clients.gamma import GammaClient, _parse_outcomes


def test_parse_outcomes_accepts_json_encoded_string():
    raw = json.dumps(["Yes", "No"])
    outcomes = _parse_outcomes(raw)
    assert outcomes == ["YES", "NO"]


def test_parse_outcomes_accepts_csv_string():
    outcomes = _parse_outcomes("yes, no")
    assert outcomes == ["YES", "NO"]


def test_gamma_client_prefers_clob_token_id_for_market_id(monkeypatch):
    payload = [
        {
            "id": 123,
            "question": "Q",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": ["10001", "10002"],
            "status": "active",
            "end_date_iso": "2026-01-01T00:00:00Z",
        }
    ]

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

    monkeypatch.setattr("chamiclaw.clients.gamma.httpx.AsyncClient", FakeAsyncClient)
    client = GammaClient(base_url="https://gamma")

    cards = asyncio.run(client.fetch_markets(limit=1))
    assert cards[0].market_id == "10001"
