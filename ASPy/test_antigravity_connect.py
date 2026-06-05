# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import antigravity_connect as connect


class AntigravityConnectTests(unittest.TestCase):
    def test_parse_cli_log_ports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_path = root / "cli.log"
            log_path.write_text(
                "I0605 12:31:30.142828 27256 server.go:492] Language server listening on random port at 51210 for HTTPS (gRPC)\n"
                "I0605 12:31:30.143343 27256 server.go:499] Language server listening on random port at 51211 for HTTP\n",
                encoding="utf-8",
            )
            with mock.patch("antigravity_connect.cli_log_paths", return_value=[log_path]):
                ports = connect.parse_cli_log_ports()
            self.assertEqual(ports, [51210, 51211])

    def test_is_cli_runtime_process_detects_agy(self) -> None:
        self.assertTrue(connect.is_cli_runtime_process("", r"C:\Users\me\AppData\Local\agy\bin\agy.exe"))
        self.assertTrue(connect.is_cli_runtime_process("agy.exe --foo", ""))


if __name__ == "__main__":
    unittest.main()
