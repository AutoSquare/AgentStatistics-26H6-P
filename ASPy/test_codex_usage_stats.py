# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import codex_usage_stats as stats


class CodexUsageStatsTests(unittest.TestCase):
    def test_total_usage_delta_and_last_usage_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            session = root / "session.jsonl"
            lines = [
                {"type": "session_meta", "payload": {"id": "s1", "cwd": str(root / "ProjectA")}},
                {"type": "turn_context", "payload": {"model": "gpt-5-codex", "cwd": str(root / "ProjectA")}},
                {
                    "timestamp": "2026-06-01T00:00:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {"last_token_usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 2, "reasoning_output_tokens": 1, "total_tokens": 13}},
                    },
                },
                {
                    "timestamp": "2026-06-01T00:01:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 20, "cached_input_tokens": 5, "output_tokens": 4, "reasoning_output_tokens": 2, "total_tokens": 26}},
                    },
                },
                {
                    "timestamp": "2026-06-01T00:02:00Z",
                    "payload": {
                        "type": "token_count",
                        "info": {"total_token_usage": {"input_tokens": 35, "cached_input_tokens": 8, "output_tokens": 7, "reasoning_output_tokens": 4, "total_tokens": 46}},
                    },
                },
            ]
            session.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

            payload = stats.build_payload(root, 0, None)
            self.assertEqual(payload["summary"]["requests"], 2)
            self.assertEqual(payload["summary"]["totalTokens"], 33)
            self.assertEqual(payload["records"][0][7], 13)
            self.assertEqual(payload["records"][1][7], 20)

    def test_prefers_global_codex_rate_limits(self) -> None:
        global_limits = {"limit_id": "codex", "plan_type": "pro"}
        model_limits = {"limit_id": "codex_bengalfox", "limit_name": "GPT-5.3-Codex-Spark"}
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)

        self.assertFalse(stats.prefer_rate_limits(model_limits, ts, global_limits, ts, ))
        self.assertTrue(stats.prefer_rate_limits(global_limits, ts, model_limits, ts, ))


if __name__ == "__main__":
    unittest.main()
