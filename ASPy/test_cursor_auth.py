# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from cursor_auth import normalize_session_token


class CursorAuthTests(unittest.TestCase):
    def test_raw_token(self) -> None:
        token = "user%3A%3AeyJhbGciOiJIUzI1NiJ9.test"
        self.assertEqual(normalize_session_token(token), token)

    def test_cookie_header(self) -> None:
        token = "user%3A%3AeyJhbGciOiJIUzI1NiJ9.test"
        raw = f"WorkosCursorSessionToken={token}"
        self.assertEqual(normalize_session_token(raw), token)

    def test_cookie_prefix(self) -> None:
        token = "user%3A%3AeyJhbGciOiJIUzI1NiJ9.test"
        raw = f"Cookie: WorkosCursorSessionToken={token}; other=1"
        self.assertEqual(normalize_session_token(raw), token)

    def test_reject_cookie_name_only(self) -> None:
        self.assertIsNone(normalize_session_token("WorkosCursorSessionToken"))

    def test_reject_whitespace(self) -> None:
        self.assertIsNone(normalize_session_token("token with spaces"))


if __name__ == "__main__":
    unittest.main()
