# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path

from usage_common import build_standard_payload, price_event


PRICING_RULES = [
    {"label": "cursor-auto", "patterns": ["cursor-auto"], "input": 2.0, "cached": 0.2, "output": 8.0},
]


class UsageCommonCostTests(unittest.TestCase):
    def test_price_event_uses_official_cost_and_scales_parts(self) -> None:
        event = {
            "ts": 1,
            "sid": "cursor:s1",
            "model": "cursor-auto",
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 200,
                "output_tokens": 100,
                "reasoning_output_tokens": 0,
                "total_tokens": 1100,
            },
            "cost": 0.42,
        }
        cost = price_event(event, PRICING_RULES)
        self.assertAlmostEqual(float(cost["total"]), 0.42)
        self.assertAlmostEqual(
            float(cost["input"]) + float(cost["cached"]) + float(cost["output"]) + float(cost["reasoning"]),
            0.42,
        )
        self.assertEqual(cost["unpricedTokens"], 0)

    def test_price_event_falls_back_to_pricing_rules_without_official_cost(self) -> None:
        event = {
            "ts": 1,
            "sid": "cursor:s1",
            "model": "cursor-auto",
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 0,
                "output_tokens": 100,
                "reasoning_output_tokens": 0,
                "total_tokens": 1100,
            },
            "cost": 0.0,
        }
        cost = price_event(event, PRICING_RULES)
        self.assertAlmostEqual(float(cost["total"]), 0.0028)

    def test_price_event_splits_official_cost_by_tokens_without_pricing_rule(self) -> None:
        event = {
            "ts": 1,
            "sid": "cursor:s1",
            "model": "composer-2.5-fast",
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 700,
                "output_tokens": 300,
                "reasoning_output_tokens": 0,
                "total_tokens": 1300,
            },
            "cost": 1.0,
        }
        cost = price_event(event, PRICING_RULES)
        self.assertAlmostEqual(float(cost["total"]), 1.0)
        self.assertAlmostEqual(float(cost["input"]), 1000 / 2000)
        self.assertAlmostEqual(float(cost["cached"]), 700 / 2000)
        self.assertAlmostEqual(float(cost["output"]), 300 / 2000)

    def test_standard_payload_records_include_official_cost(self) -> None:
        loaded = {
            "sessions": [{"sid": "cursor:s1", "cwd": "s1", "model": "cursor-auto"}],
            "events": [
                {
                    "ts": 1,
                    "sid": "cursor:s1",
                    "model": "cursor-auto",
                    "usage": {
                        "input_tokens": 1000,
                        "cached_input_tokens": 0,
                        "output_tokens": 100,
                        "reasoning_output_tokens": 0,
                        "total_tokens": 1100,
                    },
                    "cost": 0.42,
                }
            ],
            "ttfb_events": [],
            "failure_events": [],
            "limits": {},
        }
        payload = build_standard_payload(
            "cursor",
            Path("cursor-cache"),
            0,
            loaded,
            {"cursor:s1": {"name": "s1", "model": "cursor-auto"}},
            PRICING_RULES,
            [],
        )
        self.assertEqual(len(payload["records"][0]), 9)
        self.assertEqual(payload["records"][0][8], 0.42)
        self.assertAlmostEqual(payload["cost"]["total"], 0.42)


if __name__ == "__main__":
    unittest.main()
