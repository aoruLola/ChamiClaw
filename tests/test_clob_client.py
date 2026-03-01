import asyncio
import json

from chamiclaw.clients.clob import CLOBClient


class FakeSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def send(self, payload: str):
        self.sent.append(payload)

    async def recv(self):
        if not self._messages:
            raise RuntimeError("stream-ended")
        return self._messages.pop(0)


class FakeConnectCtx:
    def __init__(self, socket):
        self._socket = socket

    async def __aenter__(self):
        return self._socket

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_clob_stream_retries_then_yields_messages(monkeypatch):
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    calls = {"n": 0}

    def fake_connect(_url, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first-connect-failed")
        return FakeConnectCtx(FakeSocket([json.dumps({"type": "book", "market_id": "m1"})]))

    monkeypatch.setattr("chamiclaw.clients.clob.websockets.connect", fake_connect)

    async def collect():
        out = []
        async for event in client.stream_orderbook(["m1"], max_retries=2, retry_backoff=0.0):
            out.append(event)
            break
        return out

    events = asyncio.run(collect())
    assert calls["n"] == 2
    assert events[0]["type"] == "book"


def test_clob_stream_stops_after_retry_budget(monkeypatch):
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")

    def always_fail(_url, **_kwargs):
        raise RuntimeError("connect-failed")

    monkeypatch.setattr("chamiclaw.clients.clob.websockets.connect", always_fail)

    async def collect():
        out = []
        async for event in client.stream_orderbook(["m1"], max_retries=1, retry_backoff=0.0):
            out.append(event)
        return out

    events = asyncio.run(collect())
    assert events == []


def test_clob_normalize_valid_book_tick():
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    payload = {
        "type": "book",
        "market_id": "m1",
        "best_bid": 0.48,
        "best_ask": 0.52,
        "last": 0.5,
        "volume_1m": 42.0,
        "trades_1m": 6,
        "ts": "2026-01-01T00:00:00+00:00",
    }

    tick = client.normalize_ws_event(payload)

    assert tick is not None
    assert tick.market_id == "m1"
    assert tick.best_bid == 0.48
    assert tick.best_ask == 0.52
    assert tick.trades_1m == 6


def test_clob_normalize_ignores_malformed_payload():
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    assert client.normalize_ws_event({"foo": "bar"}) is None
    assert client.normalize_ws_event({"type": "book", "market_id": "m1"}) is None


def test_clob_stream_reconnects_with_backoff(monkeypatch):
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    calls = {"n": 0}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float):
        sleeps.append(seconds)

    def fake_connect(_url, **_kwargs):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("connect-failed")
        return FakeConnectCtx(FakeSocket([json.dumps({"type": "book", "market_id": "m1", "best_bid": 0.49, "best_ask": 0.51})]))

    monkeypatch.setattr("chamiclaw.clients.clob.asyncio.sleep", fake_sleep)
    monkeypatch.setattr("chamiclaw.clients.clob.websockets.connect", fake_connect)

    async def collect():
        out = []
        async for event in client.stream_orderbook(
            ["m1"],
            max_retries=4,
            backoff_base_seconds=1.0,
            backoff_max_seconds=5.0,
        ):
            out.append(event)
            break
        return out

    events = asyncio.run(collect())
    assert events
    assert sleeps == [1.0, 2.0]


def test_clob_stream_uses_market_channel_subscription(monkeypatch):
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    socket = FakeSocket(
        [json.dumps({"event_type": "book", "asset_id": "m1", "bids": [["0.49", "10"]], "asks": [["0.51", "8"]]})]
    )

    def fake_connect(_url, **_kwargs):
        return FakeConnectCtx(socket)

    monkeypatch.setattr("chamiclaw.clients.clob.websockets.connect", fake_connect)

    async def collect():
        out = []
        async for event in client.stream_orderbook(["m1"], max_retries=1, retry_backoff=0.0):
            out.append(event)
            break
        return out

    events = asyncio.run(collect())
    assert events
    assert socket.sent
    subscription = json.loads(socket.sent[0])
    assert subscription["type"] == "market"
    assert subscription["assets_ids"] == ["m1"]


def test_clob_normalize_market_channel_book_event():
    client = CLOBClient(rest_url="https://rest", ws_url="wss://ws")
    payload = {
        "event_type": "book",
        "asset_id": "m1",
        "bids": [["0.48", "15"]],
        "asks": [["0.52", "11"]],
        "price": "0.50",
        "volume": "42.0",
        "trades": 6,
        "timestamp": 1735689600,
    }

    tick = client.normalize_ws_event(payload)

    assert tick is not None
    assert tick.market_id == "m1"
    assert tick.best_bid == 0.48
    assert tick.best_ask == 0.52
    assert tick.last == 0.50
    assert tick.volume_1m == 42.0
    assert tick.trades_1m == 6
