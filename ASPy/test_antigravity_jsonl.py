# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import antigravity_jsonl as jsonl


class AntigravityJsonlTests(unittest.TestCase):
    def test_parse_jsonl_usage_rows(self) -> None:
        sample = "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "sessionId": "abc",
                        "modelId": "claude-sonnet-4.6",
                    }
                ),
                json.dumps(
                    {
                        "type": "usage",
                        "sessionId": "abc",
                        "timestamp": 1711200000000,
                        "input": 12,
                        "output": 4,
                        "cacheRead": 2,
                        "cacheWrite": 0,
                        "reasoning": 1,
                        "responseId": "resp-1",
                    }
                ),
            ]
        )
        events = jsonl.parse_jsonl_text(sample, cutoff_ms=0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["sid"], "antigravity:abc")
        self.assertEqual(events[0]["usage"]["input_tokens"], 12)
        self.assertEqual(events[0]["usage"]["reasoning_output_tokens"], 1)

    def test_load_antigravity_usage_from_sessions_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            sessions = cache_dir / "sessions"
            sessions.mkdir(parents=True)
            artifact = sessions / "abc-deadbeef.jsonl"
            artifact.write_text(
                json.dumps(
                    {
                        "type": "usage",
                        "sessionId": "abc",
                        "modelId": "gemini-3-flash",
                        "timestamp": 1711200000000,
                        "input": 5,
                        "output": 2,
                        "cacheRead": 1,
                        "cacheWrite": 0,
                        "reasoning": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            loaded = jsonl.load_antigravity_usage(cache_dir, 0, None)
            self.assertEqual(len(loaded["events"]), 1)
            self.assertEqual(loaded["events"][0]["model"], "gemini-3-flash")


if __name__ == "__main__":
    unittest.main()
