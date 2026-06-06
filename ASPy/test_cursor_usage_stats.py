# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cursor_usage_stats as cursor_stats


class CursorUsageStatsTests(unittest.TestCase):
    def test_build_payload_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cache_dir.joinpath("usage.csv").write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
                "2026-06-01T00:00:00Z,auto,10,20,5,8,43,0.01,0.01\n"
                "2026-06-02T00:00:00Z,auto,12,18,4,6,40,0.01,0.01\n",
                encoding="utf-8",
            )
            with patch("cursor_usage_stats.probe_cursor_limits", return_value={"ok": False, "usage": None}):
                payload = cursor_stats.build_payload(cache_dir, 0, None)
            self.assertEqual(payload["schemaVersion"], 2)
            self.assertEqual(payload["source"], "cursor")
            self.assertIn("today", payload["views"])
            self.assertIn("history", payload["views"])
            self.assertEqual(len(payload["records"][0]), 8)
            self.assertEqual(payload["dataStatus"], "ok")
            self.assertFalse(payload.get("syncAttempted"))
            self.assertEqual(payload["sync"].get("engine"), "local-read")
            self.assertTrue(payload["sync"].get("synced"))

    def test_empty_payload_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            with (
                patch("cursor_usage_stats.probe_cursor_limits", return_value={"ok": False, "usage": None}),
                patch("cursor_usage_stats.resolve_session_token", return_value=None),
                patch(
                    "cursor_usage_stats.sync_cursor_cache",
                    return_value={"synced": False, "rows": 0, "error": "未检测到 Cursor 登录态。"},
                ),
            ):
                payload = cursor_stats.build_payload(cache_dir, 0, None, do_sync=True)
            self.assertTrue(payload.get("syncAttempted"))
            self.assertEqual(payload["dataStatus"], "sync_failed")
            self.assertFalse(payload["sync"]["synced"])
            self.assertIn("未检测到 Cursor 登录态", payload["sync"]["error"])


if __name__ == "__main__":
    unittest.main()
