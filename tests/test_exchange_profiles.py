import unittest

from chamiclaw.exchange.endpoints import load_clob_endpoints, load_clob_field_map
from chamiclaw.exchange.normalize import parse_order_response, parse_orderbook_response, parse_positions_response


class ExchangeProfileMappingTest(unittest.TestCase):
    def test_legacy_profile_default_endpoints(self):
        cfg = {"apis": {"clob_base": "https://ex.example", "clob_profile": "legacy_v1"}}
        endpoints = load_clob_endpoints(cfg)
        self.assertEqual(endpoints.submit_order, "/order")
        self.assertEqual(endpoints.orderbook, "/orderbook/{market_id}")

    def test_profile_field_map_and_custom_override(self):
        cfg = {
            "apis": {
                "clob_profile": "legacy_v1",
                "clob_field_map": {
                    "order": {
                        "order_id": ["orderRef"],
                        "status": ["state"],
                    },
                    "positions": {
                        "rows": ["items"],
                        "market_id": ["market"],
                        "yes_qty": ["long"],
                        "no_qty": ["short"],
                    },
                    "orderbook": {
                        "container": ["bookData"],
                        "yes_bids": ["buy"],
                        "yes_asks": ["sell"],
                        "price": ["px"],
                        "size": ["sz"],
                    },
                },
            }
        }
        fmap = load_clob_field_map(cfg)

        order = parse_order_response({"orderRef": "abc", "state": "working"}, field_map=fmap["order"])
        self.assertEqual(order["order_id"], "abc")
        self.assertEqual(order["status"], "submitted")

        positions = parse_positions_response(
            {"items": [{"market": "m1", "long": "3.2", "short": "1.1"}]},
            field_map=fmap["positions"],
        )
        self.assertEqual(positions["m1"]["yes_qty"], 3.2)
        self.assertEqual(positions["m1"]["no_qty"], 1.1)

        book = parse_orderbook_response(
            {"bookData": {"buy": [{"px": 0.42, "sz": 11}], "sell": [{"px": 0.44, "sz": 9}]}},
            field_map=fmap["orderbook"],
        )
        assert book is not None
        self.assertAlmostEqual(book["yes_bid"], 0.42)
        self.assertAlmostEqual(book["yes_ask"], 0.44)
        self.assertAlmostEqual(book["depth_usd"], 0.42 * 11 + 0.44 * 9)

    def test_orderbook_supports_tuple_levels(self):
        book = parse_orderbook_response(
            {"book": {"bids": [[0.41, 12]], "asks": [[0.45, 8]]}}
        )
        assert book is not None
        self.assertAlmostEqual(book["yes_bid"], 0.41)
        self.assertAlmostEqual(book["yes_ask"], 0.45)


if __name__ == "__main__":
    unittest.main()
