# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

from cursor_ide_api import parse_ide_limits, probe_ide_limits


class CursorIdeApiTests(unittest.TestCase):
    def test_parse_ide_limits_maps_requests(self) -> None:
        usage = parse_ide_limits(
            {"gpt-4": {"numRequestsTotal": 7, "maxRequestUsage": 10}, "startOfMonth": "2026-06-01T00:00:00Z"},
            {"membershipType": "pro", "individualMembershipType": "pro_plus"},
        )
        self.assertEqual(usage["requestsUsed"], 7)
        self.assertEqual(usage["requestsLimit"], 10)
        self.assertEqual(usage["planPercent"], 70.0)
        self.assertEqual(usage["membershipType"], "pro_plus")
        self.assertEqual(usage["source"], "ide-api")

    def test_probe_ide_limits_success(self) -> None:
        with patch(
            "cursor_ide_api.request_ide_json",
            side_effect=[
                {"ok": True, "json": {"gpt-4": {"numRequestsTotal": 1, "maxRequestUsage": 4}}},
                {"ok": True, "json": {"membershipType": "pro"}},
            ],
        ):
            result = probe_ide_limits("access-token")
        self.assertTrue(result["ok"])
        self.assertTrue(result["ideApi"])


if __name__ == "__main__":
    unittest.main()
