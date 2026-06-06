# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from cursor_usage_api import (
    build_usage_json_document,
    extract_usage_events,
    load_normalized_events_from_json,
    normalize_usage_event,
    write_usage_json,
)


class CursorUsageApiTests(unittest.TestCase):
    def test_normalize_usage_event_maps_token_usage(self) -> None:
        raw = {
            "timestamp": "1748411762359",
            "model": "claude-sonnet-4",
            "sessionId": "sess-1",
            "tokenUsage": {
                "inputTokens": 100,
                "outputTokens": 40,
                "cacheReadTokens": 10,
                "cacheWriteTokens": 5,
            },
            "chargedCents": 21,
        }
        event = normalize_usage_event(raw)
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["model"], "claude-sonnet-4")
        self.assertEqual(event["usage"]["input_tokens"], 100)
        self.assertEqual(event["usage"]["output_tokens"], 40)
        self.assertEqual(event["cost"], 0.21)
        self.assertIn("sess-1", event["sid"])

    def test_extract_usage_events_supports_display_key(self) -> None:
        payload = {"usageEventsDisplay": [{"model": "gpt-4o", "timestamp": "1748411762359"}]}
        events = extract_usage_events(payload)
        self.assertEqual(len(events), 1)

    def test_write_and_load_usage_json_roundtrip(self) -> None:
        now_ms = str(int(time.time() * 1000))
        raw_events = [
            {
                "timestamp": now_ms,
                "model": "auto",
                "tokenUsage": {"inputTokens": 1, "outputTokens": 2},
                "chargedCents": 1,
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            write_usage_json(cache_dir, build_usage_json_document(raw_events))
            loaded = load_normalized_events_from_json(cache_dir, days=3650)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["model"], "cursor-auto")


if __name__ == "__main__":
    unittest.main()
