# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

import cursor_dashboard_auth as auth


class CursorDashboardAuthTests(unittest.TestCase):
    def test_build_session_token_candidates_prefers_user_id(self) -> None:
        access = "jwt-token"
        candidates = auth.build_session_token_candidates(access, "github|user_abc")
        self.assertGreaterEqual(len(candidates), 1)
        self.assertEqual(candidates[0], "user_abc%3A%3Ajwt-token")
        self.assertIn("github|user_abc%3A%3Ajwt-token", candidates)

    def test_bootstrap_without_launch_does_not_open_browser(self) -> None:
        with (
            patch("cursor_dashboard_auth.discover_browser_dashboard_token", return_value=None),
            patch("cursor_dashboard_auth.ensure_fresh_access_token", return_value=None),
            patch("cursor_dashboard_auth.read_credentials", return_value=None),
            patch("cursor_dashboard_auth.webbrowser.open") as browser_open,
        ):
            result = auth.bootstrap_dashboard_session(launch_browser=False, wait_seconds=0)
        self.assertEqual(result["method"], "failed")
        self.assertFalse(result["launchedBrowser"])
        browser_open.assert_not_called()

    def test_dashboard_url_uses_chinese_usage_page(self) -> None:
        self.assertEqual(auth.DASHBOARD_URL, "https://cursor.com/cn/dashboard/usage")


if __name__ == "__main__":
    unittest.main()
