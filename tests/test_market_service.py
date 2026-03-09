from datetime import datetime, timedelta, timezone

from chamiclaw.core.models import MarketCard
from chamiclaw.engines.market import MarketService

import asyncio


def test_market_service_extracts_us_daily_precip_markets():
    service = MarketService()
    cards = [
        MarketCard(
            market_id="m1",
            question="Will it rain in New York, NY tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(hours=20),
            status="active",
            rule_text="Resolves based on official NOAA precipitation observation.",
            rule_summary="New York, NY",
            resolution_sources=["NOAA"],
            liquidity_score=0.9,
            spread_stability=0.8,
            volume_density=0.7,
            rule_clarity_score=0.95,
        ),
        MarketCard(
            market_id="m2",
            question="Will the Yankees win tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(hours=8),
            status="active",
            rule_text="Sports result market.",
            rule_summary="Bronx, NY",
            liquidity_score=0.95,
            spread_stability=0.9,
            volume_density=0.9,
            rule_clarity_score=0.9,
        ),
    ]

    weather_markets = service.extract_weather_markets(cards, top_n=5)

    assert len(weather_markets) == 1
    assert weather_markets[0].market_id == "m1"
    assert weather_markets[0].location == "New York, NY"


def test_market_service_rejects_non_us_or_non_precip_markets():
    service = MarketService()
    cards = [
        MarketCard(
            market_id="m3",
            question="Will it snow in Toronto tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(hours=18),
            status="active",
            rule_text="Official Environment Canada observation.",
            rule_summary="Toronto, ON",
            resolution_sources=["Environment Canada"],
            liquidity_score=0.9,
            spread_stability=0.8,
            volume_density=0.7,
            rule_clarity_score=0.95,
        ),
        MarketCard(
            market_id="m4",
            question="Will the high temperature in Phoenix exceed 100F tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(hours=18),
            status="active",
            rule_text="Official NWS high temperature observation.",
            rule_summary="Phoenix, AZ",
            resolution_sources=["NWS"],
            liquidity_score=0.9,
            spread_stability=0.8,
            volume_density=0.7,
            rule_clarity_score=0.95,
        ),
    ]

    weather_markets = service.extract_weather_markets(cards, top_n=5)

    assert weather_markets == []


def test_market_service_extracts_weather_without_resolution_sources_when_gamma_metadata_matches():
    service = MarketService()
    cards = [
        MarketCard(
            market_id="wx1",
            question="Will rainfall exceed 0.1mm in Austin, TX tomorrow?",
            end_time=datetime.now(timezone.utc) + timedelta(hours=16),
            status="active",
            active=True,
            closed=False,
            archived=False,
            category="weather",
            subcategory="precipitation",
            event_slug="weather-us",
            market_slug="austin-rainfall",
            raw_tags=["weather", "rain", "daily"],
            rule_text="Official observation determines resolution.",
            rule_summary="Austin, TX",
            liquidity_score=0.8,
            spread_stability=0.75,
            volume_density=0.7,
            rule_clarity_score=0.8,
        ),
    ]

    weather_markets = service.extract_weather_markets(cards, top_n=5)

    assert len(weather_markets) == 1
    assert weather_markets[0].market_id == "wx1"
    assert weather_markets[0].location == "Austin, TX"


def test_market_service_refresh_pool_weather_only_filters_non_weather_markets():
    future = datetime.now(timezone.utc) + timedelta(hours=18)
    weather = MarketCard(
        market_id="wx1",
        question="Will it rain in Seattle, WA tomorrow?",
        end_time=future,
        status="active",
        active=True,
        closed=False,
        archived=False,
        category="weather",
        subcategory="precipitation",
        event_slug="weather-us",
        market_slug="seattle-rain",
        raw_tags=["weather", "rain"],
        rule_text="Official observation determines resolution.",
        rule_summary="Seattle, WA",
        liquidity_score=0.8,
        spread_stability=0.8,
        volume_density=0.8,
        rule_clarity_score=0.9,
    )
    politics = MarketCard(
        market_id="old1",
        question="Will Trump win the 2020 U.S. presidential election?",
        end_time=future,
        status="active",
        active=True,
        closed=False,
        archived=False,
        category="politics",
        event_slug="us-election-2020",
        market_slug="trump-2020",
        raw_tags=["politics"],
        rule_text="Political market.",
        liquidity_score=0.95,
        spread_stability=0.9,
        volume_density=0.95,
        rule_clarity_score=0.8,
    )

    class FakeGammaClient:
        async def fetch_markets(self, limit: int = 20, **_kwargs):
            assert limit == 20
            return [politics, weather]

    service = MarketService(gamma_client=FakeGammaClient())

    import asyncio

    cards = asyncio.run(service.refresh_pool(top_n=20, weather_only=True))

    assert [card.market_id for card in cards] == ["wx1"]

def test_market_service_handles_naive_end_times_as_utc_for_current_filtering():
    service = MarketService()
    naive_future = datetime.utcnow() + timedelta(hours=2)
    card = MarketCard(
        market_id="naive1",
        question="Will it rain in Miami, FL tomorrow?",
        end_time=naive_future,
        status="active",
        active=True,
        closed=False,
        archived=False,
        category="weather",
        rule_summary="Miami, FL",
        rule_text="Official observation determines resolution.",
    )

    assert service._is_current_market(card) is True


def test_market_service_extract_weather_markets_handles_naive_end_times():
    service = MarketService()
    card = MarketCard(
        market_id="naive2",
        question="Will rainfall exceed 0.1mm in Miami, FL tomorrow?",
        end_time=datetime.utcnow() + timedelta(hours=6),
        status="active",
        active=True,
        closed=False,
        archived=False,
        category="weather",
        subcategory="precipitation",
        event_slug="weather-us",
        market_slug="miami-rainfall",
        raw_tags=["weather", "rain"],
        rule_summary="Miami, FL",
        rule_text="Official observation determines resolution.",
    )

    weather_markets = service.extract_weather_markets([card], top_n=1)

    assert len(weather_markets) == 1
    assert weather_markets[0].settlement_date is not None


def test_market_service_refresh_pool_weather_only_uses_event_metadata_and_reports_stats():
    future = datetime.now(timezone.utc) + timedelta(hours=18)
    weather = MarketCard(
        market_id="wx2",
        question="Will measurable precipitation fall tomorrow?",
        end_time=future,
        status="active",
        active=True,
        closed=False,
        archived=False,
        event_slug="rain-san-francisco-ca",
        event_title="Rain in San Francisco, CA tomorrow?",
        event_description="Daily rainfall weather event for San Francisco, CA",
        market_slug="precip-san-francisco",
        rule_text="Official observation determines resolution.",
        liquidity_score=0.8,
        spread_stability=0.7,
        volume_density=0.75,
        rule_clarity_score=0.85,
    )
    politics = MarketCard(
        market_id="old2",
        question="Will Trump win the 2020 U.S. presidential election?",
        end_time=future,
        status="active",
        active=True,
        closed=False,
        archived=False,
        category="politics",
        event_title="US election event",
        event_description="Politics event",
        market_slug="trump-2020",
        liquidity_score=0.95,
        spread_stability=0.9,
        volume_density=0.95,
        rule_clarity_score=0.8,
    )

    class FakeGammaClient:
        async def fetch_event_markets(self, **_kwargs):
            return [politics, weather], {
                "gamma_events_scanned": 4,
                "gamma_markets_expanded": 2,
                "gamma_scan_limit_hit": False,
            }

    service = MarketService(gamma_client=FakeGammaClient())

    cards = asyncio.run(service.refresh_pool(top_n=10, weather_only=True))

    assert [card.market_id for card in cards] == ["wx2"]
    assert service.last_pool_stats["gamma_events_scanned"] == 4
    assert service.last_pool_stats["gamma_markets_expanded"] == 2
    assert service.last_pool_stats["weather_markets_total"] == 1
