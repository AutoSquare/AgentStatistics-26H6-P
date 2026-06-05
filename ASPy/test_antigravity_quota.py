# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from antigravity_quota import build_antigravity_risk_rows, parse_user_status


class AntigravityQuotaTests(unittest.TestCase):
    def test_parse_user_status_models(self) -> None:
        payload = {
            "userStatus": {
                "email": "user@example.com",
                "planStatus": {
                    "availablePromptCredits": 80,
                    "planInfo": {"monthlyPromptCredits": 100},
                },
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {
                            "label": "Gemini Flash",
                            "modelOrAlias": {"model": "gemini-3-flash"},
                            "quotaInfo": {"remainingFraction": 0.4, "resetTime": "2026-06-05T12:00:00Z"},
                        }
                    ]
                },
            }
        }
        parsed = parse_user_status(payload)
        self.assertEqual(parsed["email"], "user@example.com")
        self.assertEqual(len(parsed["models"]), 1)
        self.assertEqual(parsed["models"][0]["usedPercentage"], 60.0)

    def test_build_risk_rows_offline(self) -> None:
        rows = build_antigravity_risk_rows({"ok": False, "error": "Antigravity CLI 未运行"}, 12.5)
        self.assertEqual(rows[0]["label"], "等待数据")
        self.assertEqual(rows[-1]["name"], "缓存")


if __name__ == "__main__":
    unittest.main()
