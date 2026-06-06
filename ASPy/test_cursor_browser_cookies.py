# -*- coding: utf-8 -*-
import unittest

from cursor_browser_cookies import discover_browser_dashboard_token


class CursorBrowserCookiesTests(unittest.TestCase):
    def test_discover_returns_none_or_token(self) -> None:
        result = discover_browser_dashboard_token()
        if result is None:
            return
        self.assertIn("token", result)
        self.assertTrue(str(result["token"]))


if __name__ == "__main__":
    unittest.main()
