# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cursor_limits import load_limits_cache, probe_cursor_limits, save_limits_cache


class CursorLimitsTests(unittest.TestCase):
    def test_probe_limits_falls_back_to_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "limits_cache.json"
            save_limits_cache(
                cache_path,
                {
                    "ok": True,
                    "usage": {
                        "planPercent": 42.0,
                        "autoPercent": 10.0,
                        "apiPercent": 20.0,
                        "billingCycleEnd": "2026-07-01T00:00:00Z",
                    },
                },
            )
            with (
                patch("cursor_limits._probe_live", return_value={"ok": False, "usage": None, "error": "Cursor usage-summary 请求失败"}),
                patch("cursor_limits.read_ide_access_token", return_value=None),
                patch("cursor_limits.probe_ide_limits", return_value={"ok": False, "usage": None, "error": "IDE API 不可用"}),
            ):
                result = probe_cursor_limits("token", cache_path)

        self.assertTrue(result["ok"])
        self.assertTrue(result["cached"])
        self.assertEqual(result["usage"]["planPercent"], 42.0)

    def test_probe_limits_reuses_fresh_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "limits_cache.json"
            save_limits_cache(cache_path, {"ok": True, "usage": {"planPercent": 9.0}})
            with patch("cursor_limits._probe_live") as live_mock:
                result = probe_cursor_limits("token", cache_path, max_cache_age_sec=300)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("cacheFresh"))
        live_mock.assert_not_called()

    def test_probe_limits_falls_back_to_ide_api(self) -> None:
        with patch("cursor_limits._probe_live", return_value={"ok": False, "usage": None, "error": "blocked", "errorKind": "vercel_checkpoint"}):
            with patch("cursor_limits.read_ide_access_token", return_value="ide-token"):
                with patch(
                    "cursor_limits.probe_ide_limits",
                    return_value={"ok": True, "usage": {"planPercent": 12.0, "source": "ide-api"}, "ideApi": True},
                ):
                    result = probe_cursor_limits("token", None)
        self.assertTrue(result["ok"])
        self.assertTrue(result["ideApi"])

    def test_save_and_load_limits_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "limits_cache.json"
            save_limits_cache(cache_path, {"ok": True, "usage": {"planPercent": 12.0}})
            loaded = load_limits_cache(cache_path)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["usage"]["planPercent"], 12.0)


if __name__ == "__main__":
    unittest.main()
