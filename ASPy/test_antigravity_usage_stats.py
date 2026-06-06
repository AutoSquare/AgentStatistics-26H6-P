# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
import json
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

    def test_total_tokens_include_cache_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            sessions = cache_dir / "sessions"
            sessions.mkdir(parents=True)
            sessions.joinpath("demo.jsonl").write_text(
                '{"type":"usage","sessionId":"demo","modelId":"gemini-3-flash","timestamp":1711200000000,"input":56000,"output":3000,"cacheRead":380000,"cacheWrite":0,"reasoning":0}\n',
                encoding="utf-8",
            )
            payload = ag_stats.build_payload(cache_dir, 0, None, probe_quota=False)
            self.assertEqual(payload["summary"]["inputTokens"], 436000)
            self.assertEqual(payload["summary"]["cachedTokens"], 380000)
            self.assertEqual(payload["summary"]["outputTokens"], 3000)
            self.assertEqual(payload["summary"]["totalTokens"], 439000)

    def test_cache_hit_uses_input_plus_cached_denominator(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            sessions = cache_dir / "sessions"
            sessions.mkdir(parents=True)
            sessions.joinpath("demo.jsonl").write_text(
                '{"type":"usage","sessionId":"demo","modelId":"gemini-3-flash","timestamp":1711200000000,"input":10,"output":3,"cacheRead":90,"cacheWrite":0,"reasoning":0}\n',
                encoding="utf-8",
            )
            payload = ag_stats.build_payload(cache_dir, 0, None, probe_quota=False)
            self.assertEqual(payload["summary"]["cacheHit"], 90.0)
            self.assertEqual(payload["summary"]["cacheHitLabel"], "90.0%")

    def test_sync_offline_still_serves_local_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            sessions = cache_dir / "sessions"
            sessions.mkdir(parents=True)
            sessions.joinpath("demo.jsonl").write_text(
                json.dumps(
                    {
                        "type": "usage",
                        "sessionId": "demo",
                        "modelId": "gemini-3-flash",
                        "timestamp": 1711200000000,
                        "input": 25,
                        "output": 5,
                        "cacheRead": 10,
                        "cacheWrite": 0,
                        "reasoning": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            offline_sync = {
                "synced": False,
                "sessions": 0,
                "connections": 0,
                "error": "Antigravity CLI 未运行，无法通过本机 Connect RPC 同步新数据；将尝试读取本地缓存。",
            }
            with (
                patch("antigravity_usage_stats.sync_antigravity_cache", return_value=offline_sync),
                patch("antigravity_usage_stats.detect_connections", return_value=[]),
                patch("antigravity_usage_stats.probe_antigravity_quota", return_value={"ok": False, "quota": None, "error": "Antigravity CLI 未运行"}),
            ):
                payload = ag_stats.build_payload(cache_dir, 0, None, probe_quota=True, do_sync=True)

            self.assertEqual(payload["dataStatus"], "ok")
            self.assertGreaterEqual(payload["summary"]["requests"], 1)
            self.assertEqual(payload["sync"]["synced"], False)
            self.assertEqual(payload["auth"]["running"], False)
            self.assertGreaterEqual(payload["auth"]["cacheSessionFiles"], 1)


if __name__ == "__main__":
    unittest.main()
