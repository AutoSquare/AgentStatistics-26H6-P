# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from cursor_http import classify_http_failure, request_json


class CursorHttpTests(unittest.TestCase):
    def test_classify_vercel_checkpoint(self) -> None:
        failure = classify_http_failure(403, "<title>Vercel Security Checkpoint</title>")
        self.assertEqual(failure["kind"], "vercel_checkpoint")
        self.assertIn("暂时受阻", failure["message"])

    def test_classify_unauthorized(self) -> None:
        failure = classify_http_failure(401, '{"error":"unauthorized"}')
        self.assertEqual(failure["kind"], "unauthorized")

    def test_request_json_parses_payload(self) -> None:
        class FakeResponse:
            def read(self) -> bytes:
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        class FakeUrlopen:
            def __init__(self, request, timeout):
                self.request = request

            def __enter__(self):
                return FakeResponse()

            def __exit__(self, exc_type, exc, tb) -> bool:
                return False

        with unittest.mock.patch("cursor_http.urllib.request.urlopen", FakeUrlopen):
            result = request_json("https://cursor.com/api/usage-summary", "token", timeout=5)

        self.assertTrue(result["ok"])
        self.assertEqual(result["json"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
