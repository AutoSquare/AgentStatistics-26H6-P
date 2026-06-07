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
                patch("cursor_limits.read_cli_auth_bundle", return_value=None),
                patch("cursor_limits.probe_cli_limits", return_value={"ok": False, "usage": None, "error": "CLI API 不可用"}),
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

    def test_probe_limits_prefers_cli_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "limits_cache.json"
            with patch("cursor_limits._probe_live", return_value={"ok": False, "usage": None, "error": "blocked"}):
                with patch(
                    "cursor_limits.read_cli_auth_bundle",
                    return_value={
                        "accessToken": "cli-token",
                        "accountId": "user_cli",
                        "email": "cli@example.com",
                    },
                ):
                    with patch(
                        "cursor_limits.probe_cli_limits",
                        return_value={"ok": True, "usage": {"planPercent": 12.0, "source": "cli-api"}, "cliApi": True},
                    ) as probe:
                        result = probe_cursor_limits("token", cache_path)
        probe.assert_called_once_with("cli-token")
        self.assertTrue(result["ok"])
        self.assertTrue(result["cliApi"])
        self.assertEqual(result["usage"]["source"], "cli-api")
        self.assertEqual(result["usage"]["accountId"], "user_cli")

    def test_save_and_load_limits_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "limits_cache.json"
            save_limits_cache(cache_path, {"ok": True, "usage": {"planPercent": 12.0}})
            loaded = load_limits_cache(cache_path)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["usage"]["planPercent"], 12.0)


if __name__ == "__main__":
    unittest.main()
