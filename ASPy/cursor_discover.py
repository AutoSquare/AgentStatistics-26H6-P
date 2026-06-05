# -*- coding: utf-8 -*-
"""Discover Cursor Session Token from local IDE storage (state.vscdb)."""
from __future__ import annotations

import base64
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from cursor_auth import normalize_session_token


def default_state_vscdb_path() -> Path | None:
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if not appdata:
            return None
        return Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    home = Path.home()
    if os.name == "darwin":
        return home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    return home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def decode_jwt_sub(access_token: str) -> str | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    sub = data.get("sub") if isinstance(data, dict) else None
    return str(sub).strip() if isinstance(sub, str) and sub.strip() else None


def build_workos_session_token(access_token: str, subject: str | None = None) -> str | None:
    token = access_token.strip()
    if not token:
        return None
    sub = subject or decode_jwt_sub(token)
    if sub:
        return f"{sub}%3A%3A{token}"
    return token


def read_item_table_value(db_path: Path, key: str) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT value FROM ItemTable WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    text = str(row[0]).strip()
    return text or None


def discover_local_session_token(db_path: Path | None = None) -> dict[str, Any] | None:
    path = db_path or default_state_vscdb_path()
    if path is None or not path.is_file():
        return None
    access_token = read_item_table_value(path, "cursorAuth/accessToken")
    if not access_token:
        return None
    session_token = build_workos_session_token(access_token)
    session_token = normalize_session_token(session_token)
    if not session_token:
        return None
    email = read_item_table_value(path, "cursorAuth/cachedEmail")
    membership = read_item_table_value(path, "cursorAuth/stripeMembershipType")
    return {
        "token": session_token,
        "source": "state.vscdb",
        "path": str(path),
        "email": email,
        "membershipType": membership,
    }


def resolve_session_token() -> dict[str, Any] | None:
    from cursor_sync import read_credentials

    stored = read_credentials()
    if stored:
        return {"token": stored, "source": "credentials"}
    discovered = discover_local_session_token()
    if discovered:
        return discovered
    return None
