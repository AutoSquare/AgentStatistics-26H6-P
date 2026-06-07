# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

from cursor_cli_api import parse_cli_limits, probe_cli_limits


class CursorCliApiTests(unittest.TestCase):
    def test_parse_cli_limits(self) -> None:
        result = parse_cli_limits(
            {"gpt-4": {"numRequestsTotal": 20, "maxRequestUsage": 100}},
            {"individualMembershipType": "pro_plus"},
        )
        self.assertEqual(result["planPercent"], 20.0)
        self.assertEqual(result["membershipType"], "pro_plus")
        self.assertEqual(result["source"], "cli-api")

    def test_probe_cli_limits(self) -> None:
        with patch(
            "cursor_cli_api.request_cli_json",
            side_effect=[
                {"ok": True, "json": {"gpt-4": {"numRequestsTotal": 10, "maxRequestUsage": 100}}},
                {"ok": True, "json": {"individualMembershipType": "pro"}},
            ],
        ):
            result = probe_cli_limits("token")
        self.assertTrue(result["ok"])
        self.assertTrue(result["cliApi"])


if __name__ == "__main__":
    unittest.main()
