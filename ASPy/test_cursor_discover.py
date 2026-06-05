# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import cursor_discover as discover


def make_access_token(sub: str) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


class CursorDiscoverTests(unittest.TestCase):
    def test_build_workos_session_token(self) -> None:
        access = make_access_token("github|user_test")
        token = discover.build_workos_session_token(access)
        self.assertEqual(token, f"github|user_test%3A%3A{access}")

    def test_discover_from_sqlite(self) -> None:
        access = make_access_token("github|user_test")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.vscdb"
            conn = sqlite3.connect(db_path)
            conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO ItemTable(key, value) VALUES (?, ?)", ("cursorAuth/accessToken", access))
            conn.execute("INSERT INTO ItemTable(key, value) VALUES (?, ?)", ("cursorAuth/cachedEmail", "a@example.com"))
            conn.commit()
            conn.close()
            found = discover.discover_local_session_token(db_path)
            self.assertIsNotNone(found)
            assert found is not None
            self.assertEqual(found["source"], "state.vscdb")
            self.assertEqual(found["email"], "a@example.com")
            self.assertTrue(found["token"].endswith(access))


if __name__ == "__main__":
    unittest.main()
