import asyncio
import json
from datetime import datetime, timedelta, timezone

from chamiclaw.clients.gamma import GammaClient, _parse_end_time, _parse_outcomes


def test_parse_outcomes_accepts_json_encoded_string():
    raw = json.dumps(["Yes", "No"])
    outcomes = _parse_outcomes(raw)
    assert outcomes == ["YES", "NO"]


def test_parse_outcomes_accepts_csv_string():
    outcomes = _parse_outcomes("yes, no")
    assert outcomes == ["YES", "NO"]


def test_parse_end_time_normalizes_naive_datetime_to_utc():
    parsed = _parse_end_time("2026-03-10T12:30:00")
    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-03-10T12:30:00+00:00"


def test_gamma_client_prefers_clob_token_id_for_market_id(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    payload = [
        {
            "id": 123,
            "question": "Q",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": ["10001", "10002"],
            "status": "active",
            "end_date_iso": future,
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


def test_gamma_client_filters_old_closed_and_archived_markets_and_maps_metadata(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    payload = [
        {
            "id": "weather-1",
            "question": "Will it rain in Austin, TX tomorrow?",
            "description": "Daily weather market for Austin, TX",
            "outcomes": ["Yes", "No"],
            "clobTokenIds": ["90001", "90002"],
            "active": True,
            "closed": False,
            "archived": False,
            "end_date_iso": future,
            "category": "weather",
            "subcategory": "rain",
            "eventSlug": "weather-us",
            "slug": "rain-austin-tx",
            "tags": ["weather", "rain", "us"],
            "rules": "Resolves to NWS daily precipitation observation.",
            "resolution_sources": ["NWS"],
        },
        {
            "id": "old-1",
            "question": "Will Trump win the 2020 U.S. presidential election?",
            "outcomes": ["Yes", "No"],
            "active": False,
            "closed": True,
            "archived": True,
            "end_date_iso": past,
            "category": "politics",
            "tags": ["politics"],
        },
    ]
    captured: dict[str, object] = {}

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
            captured["params"] = params
            return FakeResponse()

    monkeypatch.setattr("chamiclaw.clients.gamma.httpx.AsyncClient", FakeAsyncClient)
    client = GammaClient(base_url="https://gamma")

    cards = asyncio.run(client.fetch_markets(limit=20))

    assert captured["params"]["limit"] == 20
    assert captured["params"]["active"] is True
    assert captured["params"]["closed"] is False
    assert captured["params"]["archived"] is False
    assert len(cards) == 1
    assert cards[0].market_id == "90001"
    assert cards[0].category == "weather"
    assert cards[0].subcategory == "rain"
    assert cards[0].event_slug == "weather-us"
    assert cards[0].market_slug == "rain-austin-tx"
    assert cards[0].raw_tags == ["weather", "rain", "us"]
    assert cards[0].rule_summary == "Daily weather market for Austin, TX"


def test_gamma_client_fetches_event_markets_with_pagination_and_filters_non_tradeable(monkeypatch):
    future = (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat().replace("+00:00", "Z")
    pages = [
        [
            {
                "id": "evt-weather-1",
                "title": "Rain in Austin tomorrow?",
                "slug": "rain-austin-tx",
                "description": "Daily rainfall event for Austin, TX",
                "markets": [
                    {
                        "id": "weather-1",
                        "question": "Will it rain in Austin, TX tomorrow?",
                        "description": "Austin, TX rain market",
                        "resolutionSource": "NWS",
                        "slug": "will-it-rain-in-austin",
                        "endDate": future,
                        "active": True,
                        "closed": False,
                        "archived": False,
                        "enableOrderBook": True,
                        "clobTokenIds": ["91001", "91002"],
                    },
                    {
                        "id": "weather-2",
                        "question": "Will it rain in Dallas, TX tomorrow?",
                        "description": "Dallas market without order book",
                        "slug": "will-it-rain-in-dallas",
                        "endDate": future,
                        "active": True,
                        "closed": False,
                        "archived": False,
                        "enableOrderBook": False,
                    },
                ],
            }
        ],
        [],
    ]
    captured: list[tuple[str, dict | None]] = []

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.content = b"ok"

        @staticmethod
        def raise_for_status() -> None:
            return None

        def json(self):
            return self._payload

    class FakeAsyncClient:
        def __init__(self, **_kwargs):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None):
            captured.append((url, params))
            payload = pages[len(captured) - 1]
            return FakeResponse(payload)

    monkeypatch.setattr("chamiclaw.clients.gamma.httpx.AsyncClient", FakeAsyncClient)
    client = GammaClient(base_url="https://gamma")

    cards, stats = asyncio.run(client.fetch_event_markets(page_size=1, max_pages=3))

    assert len(cards) == 1
    assert cards[0].market_id == "91001"
    assert cards[0].event_slug == "rain-austin-tx"
    assert cards[0].event_title == "Rain in Austin tomorrow?"
    assert stats["gamma_events_scanned"] == 1
    assert stats["gamma_markets_expanded"] == 2
    assert stats["gamma_scan_limit_hit"] is False
    assert captured[0][0] == "https://gamma/events"
    assert captured[0][1]["closed"] is False
    assert captured[0][1]["order"] == "id"
    assert captured[0][1]["ascending"] is False
    assert captured[0][1]["offset"] == 0
    assert captured[1][1]["offset"] == 1
