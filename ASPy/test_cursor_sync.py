# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import cursor_sync as sync


class CursorSyncTests(unittest.TestCase):
    def test_fetch_usage_csv_timeout_returns_runtime_error(self) -> None:
        with patch("cursor_sync.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(RuntimeError) as ctx:
                sync.fetch_usage_csv("test-token", timeout=5)
        self.assertIn("超时", str(ctx.exception))

    def test_sync_cursor_cache_timeout_does_not_crash(self) -> None:
        with patch("cursor_sync.resolve_sync_token", return_value=("token", {"source": "credentials"})):
            with patch("cursor_sync.fetch_usage_csv", side_effect=RuntimeError("Cursor 同步超时（>120s）")):
                result = sync.sync_cursor_cache(Path("."))
        self.assertFalse(result.get("synced"))
        self.assertIn("超时", str(result.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
