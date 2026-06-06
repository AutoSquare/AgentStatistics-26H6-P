# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity_transcript as transcript


class AntigravityTranscriptTests(unittest.TestCase):
    def test_parse_transcript_usage_metadata(self) -> None:
        row = {
            "created_at": "2026-05-24T12:27:18Z",
            "model": "gemini-3-flash",
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 4,
                "cachedContentTokenCount": 2,
                "thoughtsTokenCount": 1,
            },
        }
        events = transcript.parse_transcript_text(json.dumps(row) + "\n", "session-1", 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["usage"]["input_tokens"], 14)
        self.assertEqual(events[0]["usage"]["cached_input_tokens"], 2)
        self.assertEqual(events[0]["usage"]["output_tokens"], 3)
        self.assertEqual(events[0]["usage"]["reasoning_output_tokens"], 1)
        self.assertEqual(events[0]["usage"]["total_tokens"], 18)

    def test_load_transcript_from_brain_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "brain" / "conv-1" / ".system_generated" / "logs"
            logs.mkdir(parents=True)
            logs.joinpath("transcript.jsonl").write_text(
                '{"timestamp":1711200000000,"model":"gemini-3-pro","usage":{"inputTokens":8,"outputTokens":2}}\n',
                encoding="utf-8",
            )
            with patch("antigravity_transcript.antigravity_data_roots", return_value=[root]):
                loaded = transcript.load_antigravity_transcript_usage(0, None)
            self.assertEqual(loaded["transcriptFiles"], 1)
            self.assertEqual(len(loaded["events"]), 1)


if __name__ == "__main__":
    unittest.main()
