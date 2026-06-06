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


def derive_dashboard_user_id(subject: str) -> str:
    """Derive Dashboard cookie user id from JWT sub (provider|user_id -> user_id)."""
    text = str(subject or "").strip()
    if "|" in text:
        return text.split("|", 1)[1].strip() or text
    return text


def build_workos_session_token(access_token: str, subject: str | None = None) -> str | None:
    token = access_token.strip()
    if not token:
        return None
    sub = subject or decode_jwt_sub(token)
    if sub:
        return f"{derive_dashboard_user_id(sub)}%3A%3A{token}"
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


def read_ide_access_token(db_path: Path | None = None) -> str | None:
    """Read Cursor IDE JWT access token from state.vscdb."""
    path = db_path or default_state_vscdb_path()
    if path is None or not path.is_file():
        return None
    access_token = read_item_table_value(path, "cursorAuth/accessToken")
    if not access_token:
        return None
    return access_token.strip() or None


def iter_session_token_candidates() -> list[dict[str, Any]]:
    """Return unique Cursor session tokens, preferring Dashboard browser cookies."""
    from cursor_browser_cookies import discover_browser_dashboard_token
    from cursor_sync import read_credentials

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_candidate(item: dict[str, Any] | None) -> None:
        if not item:
            return
        token = normalize_session_token(str(item.get("token") or ""))
        if not token or token in seen:
            return
        seen.add(token)
        candidates.append({**item, "token": token})

    add_candidate(discover_browser_dashboard_token())
    stored = read_credentials()
    if stored:
        add_candidate({"token": stored, "source": "credentials"})
    add_candidate(discover_local_session_token())
    return candidates


def resolve_session_token() -> dict[str, Any] | None:
    candidates = iter_session_token_candidates()
    return candidates[0] if candidates else None
