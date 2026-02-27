from chamiclaw.engines.info import InfoEngine


def test_info_engine_sets_high_risk_when_only_weak_sources():
    engine = InfoEngine()
    signal = engine.analyze(market_id="m1", source_tiers=[3, 3], event_detected=True)
    assert signal.risk_score >= 0.7
    assert signal.confirmation_level == 0
