# -*- coding: utf-8 -*-
"""Read WorkosCursorSessionToken from local Chromium cookie databases."""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from cursor_auth import COOKIE_NAME, normalize_session_token


def _chromium_cookie_paths() -> list[Path]:
    appdata = os.getenv("APPDATA") or ""
    local = os.getenv("LOCALAPPDATA") or ""
    home = Path.home()
    return [
        Path(appdata) / "Cursor" / "Partitions" / "cursor-browser" / "Network" / "Cookies",
        Path(appdata) / "Cursor" / "Network" / "Cookies",
        Path(local) / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies",
        Path(local) / "Google" / "Chrome" / "User Data" / "Profile 1" / "Network" / "Cookies",
        Path(local) / "Microsoft" / "Edge" / "User Data" / "Default" / "Network" / "Cookies",
        Path(local) / "Microsoft" / "Edge" / "User Data" / "Profile 1" / "Network" / "Cookies",
        home / ".config" / "google-chrome" / "Default" / "Cookies",
        home / ".config" / "chromium" / "Default" / "Cookies",
        home / "Library" / "Application Support" / "Google" / "Chrome" / "Default" / "Cookies",
        home / "Library" / "Application Support" / "Microsoft Edge" / "Default" / "Cookies",
    ]


def _copy_locked_file(source: Path) -> Path | None:
    tmp_dir = Path(tempfile.mkdtemp(prefix="as-cookies-"))
    target = tmp_dir / "Cookies"
    try:
        shutil.copy2(source, target)
        return target
    except OSError:
        pass
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["cmd", "/c", "copy", "/Y", str(source), str(target)],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            if completed.returncode == 0 and target.is_file():
                return target
        except (OSError, subprocess.SubprocessError):
            return None
    return None


def _read_token_from_cookie_db(db_path: Path) -> str | None:
    copied = _copy_locked_file(db_path)
    if copied is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{copied}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            "SELECT value FROM cookies WHERE name = ? AND host_key LIKE '%cursor.com%'",
            (COOKIE_NAME,),
        ).fetchall()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    for row in rows:
        token = normalize_session_token(str(row[0] if row else ""))
        if token:
            return token
    return None


def discover_browser_dashboard_token() -> dict[str, Any] | None:
    """Return Dashboard session token from Chrome/Edge/Cursor browser cookie stores."""
    for path in _chromium_cookie_paths():
        if not path.is_file():
            continue
        token = _read_token_from_cookie_db(path)
        if not token:
            continue
        return {
            "token": token,
            "source": "browser-cookies",
            "path": str(path),
        }
    return None
