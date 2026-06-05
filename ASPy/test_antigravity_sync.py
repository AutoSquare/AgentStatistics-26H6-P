# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity_sync as sync


class AntigravitySyncTests(unittest.TestCase):
    def test_normalize_session_metadata(self) -> None:
        metadata = [
            {
                "chatModel": {
                    "responseModel": "gemini-3-flash",
                    "chatStartMetadata": {"createdAt": 1711200000000},
                    "retryInfos": [
                        {
                            "usage": {
                                "inputTokens": 10,
                                "outputTokens": 3,
                                "cacheReadTokens": 1,
                                "thinkingOutputTokens": 0,
                                "responseId": "resp-1",
                            }
                        }
                    ],
                }
            }
        ]
        lines = sync.normalize_session_metadata("session-1", metadata)
        self.assertEqual(len(lines), 2)
        first = json.loads(lines[0])
        second = json.loads(lines[1])
        self.assertEqual(first["type"], "session_meta")
        self.assertEqual(second["type"], "usage")
        self.assertEqual(second["input"], 10)

    def test_sync_without_running_ide_reports_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            with patch("antigravity_sync.detect_connections", return_value=[]):
                result = sync.sync_antigravity_cache(cache_dir)
            self.assertFalse(result.get("synced"))
            self.assertIn("未运行", str(result.get("error") or ""))


if __name__ == "__main__":
    unittest.main()
