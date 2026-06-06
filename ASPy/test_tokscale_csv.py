# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import tokscale_csv as csvmod
from cursor_usage_api import build_usage_json_document, write_usage_json


class TokscaleCsvTests(unittest.TestCase):
    def test_parse_v1_csv_rows(self) -> None:
        text = (
            "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
            "2026-06-01T10:00:00Z,auto,100,200,50,80,430,Included,0.12\n"
            "2026-06-02T11:00:00Z,claude-3.5-sonnet,0,300,20,120,440,0.20,0.20\n"
        )
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = csvmod.parse_csv_text(text, "cursor", cutoff)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["model"], "cursor-auto")
        self.assertEqual(events[0]["usage"]["total_tokens"], 430)
        self.assertEqual(events[1]["usage"]["input_tokens"], 320)

    def test_parse_v3_csv_rows(self) -> None:
        text = (
            "Date,Cloud Agent ID,Automation ID,Kind,Model,Max Mode,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
            "2026-06-04T12:00:00Z,agent-1,,chat,claude-sonnet-4,false,5,10,2,8,25,0.05\n"
        )
        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        events = csvmod.parse_csv_text(text, "cursor", cutoff)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["model"], "claude-sonnet-4")
        self.assertTrue(events[0]["sid"].startswith("cursor:"))

    def test_load_cache_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cache_dir.joinpath("usage.csv").write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
                "2026-06-03T08:00:00Z,gemini-3-flash,10,20,5,7,42,0.01,0.01\n",
                encoding="utf-8",
            )
            loaded = csvmod.load_tokscale_usage(cache_dir, "antigravity", 0, None)
            self.assertEqual(len(loaded["events"]), 1)
            self.assertEqual(loaded["events"][0]["model"], "gemini-3-flash")

    def test_cursor_merges_fresh_dashboard_json_with_csv_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            write_usage_json(
                cache_dir,
                build_usage_json_document(
                    [
                        {
                            "timestamp": "2026-06-06T08:00:00Z",
                            "model": "auto",
                            "tokenUsage": {"inputTokens": 2000, "outputTokens": 200},
                        },
                        {
                            "timestamp": "2026-06-06T09:00:00Z",
                            "model": "composer-2.5-fast",
                            "tokenUsage": {"inputTokens": 300, "outputTokens": 30},
                        }
                    ],
                    source="webview-json",
                ),
            )
            cache_dir.joinpath("usage.csv").write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
                "2026-06-05T08:00:00Z,auto,0,1000,0,100,1100,0\n"
                "2026-06-06T08:00:00Z,auto,0,2000,0,200,2200,0\n",
                encoding="utf-8",
            )
            loaded = csvmod.load_tokscale_usage(cache_dir, "cursor", 0, None)
        self.assertEqual(len(loaded["events"]), 3)
        self.assertEqual(sum(item["usage"]["total_tokens"] for item in loaded["events"]), 3630)

    def test_cursor_dedupes_dashboard_json_against_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            write_usage_json(
                cache_dir,
                build_usage_json_document(
                    [
                        {
                            "timestamp": "2026-06-05T08:00:00Z",
                            "model": "auto",
                            "tokenUsage": {"inputTokens": 10, "outputTokens": 1},
                        },
                        {
                            "timestamp": "2026-06-06T08:00:00Z",
                            "model": "composer-2.5-fast",
                            "tokenUsage": {"inputTokens": 200, "outputTokens": 40},
                        },
                    ],
                    source="webview-json",
                ),
            )
            cache_dir.joinpath("usage.csv").write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost\n"
                "2026-06-05T08:00:00Z,auto,0,10,0,1,11,0\n",
                encoding="utf-8",
            )
            loaded = csvmod.load_tokscale_usage(cache_dir, "cursor", 0, None)
        self.assertEqual(len(loaded["events"]), 2)
        self.assertEqual(sum(item["usage"]["total_tokens"] for item in loaded["events"]), 251)

    def test_invalidate_parse_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            csv_path = cache_dir / "usage.csv"
            csv_path.write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
                "2026-06-03T08:00:00Z,gemini-3-flash,10,20,5,7,42,0.01,0.01\n",
                encoding="utf-8",
            )
            cache_path = cache_dir / "cursor_usage_cache.json"
            csvmod.load_tokscale_usage(cache_dir, "cursor", 0, cache_path)
            csvmod.invalidate_parse_cache_entries(cache_path, csv_path)
            csv_path.write_text(
                "Date,Model,Input (w/ Cache Write),Input (w/o Cache Write),Cache Read,Output Tokens,Total Tokens,Cost,Cost to you\n"
                "2026-06-04T08:00:00Z,gemini-3-flash,11,21,6,8,46,0.02,0.02\n",
                encoding="utf-8",
            )
            loaded = csvmod.load_tokscale_usage(cache_dir, "cursor", 0, cache_path)
            self.assertEqual(len(loaded["events"]), 1)
            self.assertEqual(loaded["events"][0]["usage"]["total_tokens"], 46)


if __name__ == "__main__":
    unittest.main()
