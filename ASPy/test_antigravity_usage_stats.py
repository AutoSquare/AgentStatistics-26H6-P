# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity_usage_stats as ag_stats


class AntigravityUsageStatsTests(unittest.TestCase):
    def test_build_payload_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            sessions = cache_dir / "sessions"
            sessions.mkdir(parents=True)
            sessions.joinpath("demo.jsonl").write_text(
                '{"type":"usage","sessionId":"demo","modelId":"gemini-3-flash","timestamp":1711200000000,"input":15,"output":3,"cacheRead":2,"cacheWrite":0,"reasoning":0}\n',
                encoding="utf-8",
            )
            with patch("antigravity_usage_stats.probe_antigravity_quota", return_value={"ok": False, "error": "Antigravity CLI 未运行", "quota": None}):
                payload = ag_stats.build_payload(cache_dir, 0, None, probe_quota=True)
            self.assertEqual(payload["schemaVersion"], 2)
            self.assertEqual(payload["source"], "antigravity")
            self.assertGreaterEqual(payload["summary"]["requests"], 1)
            self.assertEqual(len(payload["records"][0]), 8)


if __name__ == "__main__":
    unittest.main()
