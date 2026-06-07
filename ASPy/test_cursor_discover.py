# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch

import cursor_discover as discover


def make_access_token(sub: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class CursorDiscoverTests(unittest.TestCase):
    def test_build_workos_session_token(self) -> None:
        access = make_access_token("github|user_test")
        token = discover.build_workos_session_token(access)
        self.assertEqual(token, f"user_test%3A%3A{access}")

    def test_iter_session_token_candidates_uses_cli_only(self) -> None:
        access = make_access_token("github|user_test")
        with patch(
            "cursor_cli_auth.read_cli_auth_bundle",
            return_value={
                "accessToken": access,
                "subject": "github|user_test",
                "accountId": "user_test",
                "email": "cli@example.com",
                "path": "auth.json",
            },
        ):
            candidates = discover.iter_session_token_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source"], "cursor-cli")
        self.assertEqual(candidates[0]["accountId"], "user_test")

    def test_iter_session_token_candidates_without_cli_is_empty(self) -> None:
        with patch("cursor_cli_auth.read_cli_auth_bundle", return_value=None):
            self.assertEqual(discover.iter_session_token_candidates(), [])


if __name__ == "__main__":
    unittest.main()
