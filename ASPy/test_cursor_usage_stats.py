# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cursor_usage_stats as cursor_stats


class CursorUsageStatsTests(unittest.TestCase):
    def test_enrich_cursor_identity_uses_matching_jwt_subject_email(self) -> None:
        token = "github|user_1%3A%3AeyJhbGciOiJub25lIn0.eyJzdWIiOiJnaXRodWJ8dXNlcl8xIn0."
        equivalent = "user_1%3A%3AeyJhbGciOiJub25lIn0.eyJzdWIiOiJnaXRodWJ8dXNlcl8xIn0."
        with patch(
            "cursor_usage_stats.iter_session_token_candidates",
            return_value=[{"token": equivalent, "email": "user@example.com", "source": "state.vscdb"}],
        ):
            enriched = cursor_stats.enrich_cursor_identity({"token": token, "source": "credentials"})
        self.assertIsNotNone(enriched)
        assert enriched is not None
        self.assertEqual(enriched["email"], "user@example.com")

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
            self.assertEqual(len(payload["records"][0]), 9)
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

    def test_build_payload_archives_legacy_history_by_account(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cache_dir.joinpath("usage.csv").write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
                "2026-06-01T00:00:00Z,auto,0,20,5,8,33,0\n",
                encoding="utf-8",
            )
            token = "github|user_test%3A%3Atest-access-token"
            resolved = {
                "token": token,
                "source": "state.vscdb",
                "email": "user@example.com",
            }
            with (
                patch("cursor_usage_stats.resolve_session_token", return_value=resolved),
                patch("cursor_usage_stats.probe_cursor_limits", return_value={"ok": False, "usage": None}),
                patch("cursor_usage_stats.default_sync_status_path", return_value=cache_dir / "missing-status.json"),
            ):
                payload = cursor_stats.build_payload(cache_dir, 0, None)
            self.assertEqual(len(payload["accounts"]), 1)
            self.assertEqual(payload["accounts"][0]["email"], "user@example.com")
            self.assertEqual(payload["accounts"][0]["views"]["history"]["summary"]["totalTokens"], 33)

    def test_build_payload_sums_distinct_accounts_without_cross_account_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            for index, total in enumerate((100, 200), start=1):
                account_dir = cache_dir / "accounts" / f"account-{index}"
                account_dir.mkdir(parents=True)
                account_dir.joinpath("account.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "accountId": f"account-{index}",
                            "email": f"user{index}@example.com",
                        }
                    ),
                    encoding="utf-8",
                )
                account_dir.joinpath("usage.csv").write_text(
                    "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
                    f"2026-06-01T00:00:00Z,auto,0,{total},0,0,{total},0\n",
                    encoding="utf-8",
                )
            with (
                patch("cursor_usage_stats.resolve_session_token", return_value=None),
                patch("cursor_usage_stats.probe_cursor_limits", return_value={"ok": False, "usage": None}),
                patch("cursor_usage_stats.default_sync_status_path", return_value=cache_dir / "missing-status.json"),
            ):
                payload = cursor_stats.build_payload(cache_dir, 0, None)
            self.assertEqual(len(payload["accounts"]), 2)
            self.assertEqual(payload["views"]["history"]["summary"]["totalTokens"], 300)


if __name__ == "__main__":
    unittest.main()
