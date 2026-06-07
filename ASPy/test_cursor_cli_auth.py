# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from cursor_cli_auth import read_cli_auth_bundle


def make_access_token(sub: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class CursorCliAuthTests(unittest.TestCase):
    def test_reads_matching_cli_identity_and_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "cli-config.json"
            auth_path = root / "auth.json"
            access_token = make_access_token("auth0|user_cli")
            config_path.write_text(
                json.dumps(
                    {
                        "authInfo": {
                            "authId": "auth0|user_cli",
                            "email": "cli@example.com",
                            "displayName": "CLI User",
                        }
                    }
                ),
                encoding="utf-8",
            )
            auth_path.write_text(
                json.dumps({"accessToken": access_token, "refreshToken": "refresh"}),
                encoding="utf-8",
            )
            bundle = read_cli_auth_bundle(config_path, auth_path)
        self.assertIsNotNone(bundle)
        assert bundle is not None
        self.assertEqual(bundle["accountId"], "user_cli")
        self.assertEqual(bundle["email"], "cli@example.com")
        self.assertEqual(bundle["source"], "cursor-cli")

    def test_rejects_mismatched_cli_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "cli-config.json"
            auth_path = root / "auth.json"
            config_path.write_text(
                json.dumps({"authInfo": {"authId": "auth0|user_config"}}),
                encoding="utf-8",
            )
            auth_path.write_text(
                json.dumps({"accessToken": make_access_token("auth0|user_token")}),
                encoding="utf-8",
            )
            bundle = read_cli_auth_bundle(config_path, auth_path)
        self.assertIsNone(bundle)


if __name__ == "__main__":
    unittest.main()
