# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cursor_sync as sync


class CursorSyncTests(unittest.TestCase):
    def test_fetch_usage_csv_timeout_returns_runtime_error(self) -> None:
        with patch(
            "cursor_sync.request_text",
            return_value={"ok": False, "kind": "timeout", "message": "Cursor API 请求超时（>5s）。"},
        ):
            with self.assertRaises(RuntimeError) as ctx:
                sync.fetch_usage_csv("test-token", timeout=5)
        self.assertIn("超时", str(ctx.exception))

    def test_sync_cursor_cache_timeout_does_not_crash(self) -> None:
        with patch("cursor_sync.ensure_tokscale_credentials", return_value=True):
            with patch("cursor_tokscale_cli.is_default_tokscale_cache", return_value=False):
                with patch("cursor_sync.iter_sync_token_candidates", return_value=[("token", {"source": "credentials"})]):
                    with patch("cursor_sync.fetch_usage_json", side_effect=RuntimeError("Cursor 同步超时（>120s）")):
                        with patch("cursor_sync.fetch_usage_csv", side_effect=RuntimeError("Cursor 同步超时（>120s）")):
                            result = sync.sync_cursor_cache(Path("."))
        self.assertFalse(result.get("synced"))
        self.assertIn("超时", str(result.get("error") or ""))

    def test_sync_cursor_cache_prefers_json_api(self) -> None:
        with patch("cursor_sync.ensure_tokscale_credentials", return_value=True):
            with patch("cursor_sync.iter_sync_token_candidates", return_value=[("token", {"source": "credentials"})]):
                with patch("cursor_sync.fetch_usage_json", return_value=[{"model": "gpt-4o", "timestamp": "1748411762359"}]):
                    with tempfile.TemporaryDirectory() as tmp:
                        result = sync.sync_cursor_cache(Path(tmp), force=True)
        self.assertTrue(result.get("synced"))
        self.assertEqual(result.get("engine"), "cursor-json")

    def test_sync_cursor_cache_falls_back_to_local_csv(self) -> None:
        with patch("cursor_sync.ensure_tokscale_credentials", return_value=True):
            with patch("cursor_sync.iter_sync_token_candidates", return_value=[("token", {"source": "credentials"})]):
                with patch("cursor_sync.fetch_usage_json", side_effect=RuntimeError("云端同步暂时受阻")):
                    with patch("cursor_sync.fetch_usage_csv", side_effect=RuntimeError("云端同步暂时受阻")):
                        with patch("cursor_tokscale_cli.is_default_tokscale_cache", return_value=False):
                            with tempfile.TemporaryDirectory() as tmp:
                                cache_dir = Path(tmp)
                                csv_path = cache_dir / "usage.csv"
                                csv_path.write_text(
                                    "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
                                    "2026-06-06T06:19:22.207Z,composer-2.5-fast,0,1,0,2,3,0\n",
                                    encoding="utf-8",
                                )
                                result = sync.sync_cursor_cache(cache_dir, force=True)
        self.assertTrue(result.get("synced"))
        self.assertEqual(result.get("engine"), "local-csv")
        self.assertEqual(result.get("rows"), 1)

    def test_sync_cursor_cache_falls_back_to_tokscale_cli(self) -> None:
        with patch("cursor_sync.ensure_tokscale_credentials", return_value=True):
            with patch("cursor_sync.iter_sync_token_candidates", return_value=[("token", {"source": "credentials"})]):
                with patch("cursor_sync.fetch_usage_json", side_effect=RuntimeError("云端同步暂时受阻")):
                    with patch("cursor_sync.fetch_usage_csv", side_effect=RuntimeError("云端同步暂时受阻")):
                        with patch("cursor_tokscale_cli.is_default_tokscale_cache", return_value=True):
                            with patch(
                                "cursor_tokscale_cli.sync_via_tokscale_cli",
                                return_value={"synced": True, "rows": 9, "engine": "tokscale-cli"},
                            ):
                                with tempfile.TemporaryDirectory() as tmp:
                                    result = sync.sync_cursor_cache(Path(tmp), force=True)
        self.assertTrue(result.get("synced"))
        self.assertEqual(result.get("engine"), "tokscale-cli")


if __name__ == "__main__":
    unittest.main()
