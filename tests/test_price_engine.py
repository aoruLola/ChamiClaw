from chamiclaw.core.models import SpreadStatus
from chamiclaw.engines.price import PriceEngine


def test_price_engine_emits_snapshot_and_signal():
    engine = PriceEngine()
    snapshot, signal = engine.on_quote(
        market_id="m1",
        best_bid=0.49,
        best_ask=0.50,
        last=0.495,
        volume_1m=100,
        trades_1m=5,
    )
    assert snapshot.market_id == "m1"
    assert snapshot.mid == 0.495
    assert signal.spread_status == SpreadStatus.stable
