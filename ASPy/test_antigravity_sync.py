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
            sessions_dir = cache_dir / "sessions"
            sessions_dir.mkdir(parents=True)
            artifact_path = sessions_dir / "saved.jsonl"
            artifact_path.write_text('{"type":"usage","sessionId":"saved"}\n', encoding="utf-8")
            manifest_path = cache_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "sessions": [
                            {
                                "sessionId": "saved",
                                "artifactPath": "sessions/saved.jsonl",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            artifact_before = artifact_path.read_bytes()
            manifest_before = manifest_path.read_bytes()

            with patch("antigravity_sync.detect_connections", return_value=[]):
                result = sync.sync_antigravity_cache(cache_dir)

            self.assertTrue(result.get("synced"))
            self.assertIn("未运行", str(result.get("error") or ""))
            self.assertEqual(result.get("sessions"), 1)
            self.assertEqual(artifact_path.read_bytes(), artifact_before)
            self.assertEqual(manifest_path.read_bytes(), manifest_before)


    def test_sync_keeps_successful_artifacts_when_later_rpc_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            connection = sync.AntigravityConnection(pid=1, port=2, csrf_token="", fingerprint="pid:1:port:2")
            summaries = [
                sync.TrajectorySummary("ok", 1711200000000, 1, connection.fingerprint),
                sync.TrajectorySummary("broken", 1711200000001, 1, connection.fingerprint),
            ]

            def fake_fetch(summary: sync.TrajectorySummary, _connections: list[sync.AntigravityConnection]) -> dict[str, object] | None:
                if summary.session_id == "broken":
                    raise ConnectionResetError("reset")
                return {
                    "contents": '{"type":"usage","sessionId":"ok","modelId":"gemini-3-flash","timestamp":1711200000000,"input":1,"output":1}\n',
                    "last_modified_ms": summary.last_modified_ms,
                    "step_count": summary.step_count,
                    "artifact_hash": "sha256:test",
                }

            with (
                patch("antigravity_sync.detect_connections", return_value=[connection]),
                patch("antigravity_sync.list_trajectory_summaries", return_value=summaries),
                patch("antigravity_sync.scan_filesystem_session_candidates", return_value=[]),
                patch("antigravity_sync.fetch_session_artifact", side_effect=fake_fetch),
            ):
                result = sync.sync_antigravity_cache(cache_dir)

            self.assertTrue(result.get("synced"))
            self.assertEqual(result.get("sessions"), 1)
            self.assertIn("broken", str(result.get("error") or ""))
            manifest = json.loads((cache_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["sessions"][0]["sessionId"], "ok")


if __name__ == "__main__":
    unittest.main()
