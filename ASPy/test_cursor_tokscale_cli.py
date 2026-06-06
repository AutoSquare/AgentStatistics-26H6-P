# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import cursor_tokscale_cli as tokcli


class CursorTokscaleCliTests(unittest.TestCase):
    def test_parse_sync_json_accepts_prefixed_output(self) -> None:
        payload = tokcli._parse_sync_json('log line\n{"synced": true, "rows": 3, "error": null}')
        self.assertTrue(payload.get("synced"))
        self.assertEqual(payload.get("rows"), 3)

    def test_sync_via_tokscale_cli_success(self) -> None:
        stdout = json.dumps({"synced": True, "rows": 12, "error": None})
        with patch("cursor_tokscale_cli.resolve_tokscale_argv", return_value=["tokscale"]):
            with patch("cursor_tokscale_cli.tokscale_credentials_path", return_value=Path("cred.json")):
                with patch.object(Path, "exists", return_value=True):
                    with patch("cursor_tokscale_cli.subprocess.run") as run_mock:
                        run_mock.return_value.returncode = 0
                        run_mock.return_value.stdout = stdout
                        run_mock.return_value.stderr = ""
                        result = tokcli.sync_via_tokscale_cli()
        self.assertTrue(result.get("synced"))
        self.assertEqual(result.get("rows"), 12)
        self.assertEqual(result.get("engine"), "tokscale-cli")

    def test_sync_via_tokscale_cli_missing_binary(self) -> None:
        with patch("cursor_tokscale_cli.resolve_tokscale_argv", return_value=None):
            result = tokcli.sync_via_tokscale_cli()
        self.assertFalse(result.get("synced"))
        self.assertEqual(result.get("errorKind"), "tokscale_missing")


if __name__ == "__main__":
    unittest.main()
