# -*- coding: utf-8 -*-
"""Bootstrap Cursor Dashboard session without manual user steps."""
from __future__ import annotations

import base64
import json
import os
import socket
import time
import urllib.error
import urllib.request
import webbrowser
from typing import Any

from cursor_auth import normalize_session_token
from cursor_browser_cookies import discover_browser_dashboard_token
from cursor_discover import (
    decode_jwt_sub,
    default_state_vscdb_path,
    derive_dashboard_user_id,
    read_item_table_value,
)
from cursor_sync import read_credentials, write_credentials

AUTH0_CLIENT_ID = "KbZUR41cY7W6zRSdpSUJ7I7mLYBKOCmB"
OAUTH_TOKEN_URL = "https://api2.cursor.sh/oauth/token"
DASHBOARD_URL = "https://cursor.com/cn/dashboard/usage"


def _decode_jwt_payload(access_token: str) -> dict[str, Any] | None:
    parts = access_token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * ((4 - len(payload) % 4) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload))
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def jwt_is_expired(access_token: str, *, skew_sec: int = 120) -> bool:
    payload = _decode_jwt_payload(access_token)
    if not payload:
        return False
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return False
    return time.time() >= float(exp) - skew_sec


def refresh_ide_access_token(refresh_token: str, *, timeout: int = 20) -> dict[str, Any]:
    body = json.dumps(
        {
            "grant_type": "refresh_token",
            "client_id": AUTH0_CLIENT_ID,
            "refresh_token": refresh_token.strip(),
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        OAUTH_TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")[:300]
        return {"ok": False, "message": f"refresh HTTP {exc.code}: {text}"}
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
        return {"ok": False, "message": f"refresh failed: {exc}"}
    if not isinstance(payload, dict):
        return {"ok": False, "message": "refresh response is not an object"}
    if payload.get("shouldLogout") is True:
        return {"ok": False, "message": "refresh token invalid (shouldLogout)"}
    access = payload.get("access_token")
    if not isinstance(access, str) or not access.strip():
        return {"ok": False, "message": "refresh response missing access_token"}
    return {"ok": True, "access_token": access.strip()}


def read_ide_auth_bundle() -> dict[str, str | None]:
    path = default_state_vscdb_path()
    if path is None or not path.is_file():
        return {"accessToken": None, "refreshToken": None, "email": None}
    return {
        "accessToken": read_item_table_value(path, "cursorAuth/accessToken"),
        "refreshToken": read_item_table_value(path, "cursorAuth/refreshToken"),
        "email": read_item_table_value(path, "cursorAuth/cachedEmail"),
    }


def ensure_fresh_access_token() -> dict[str, Any] | None:
    bundle = read_ide_auth_bundle()
    access = bundle.get("accessToken")
    refresh = bundle.get("refreshToken")
    if not isinstance(access, str) or not access.strip():
        return None
    access = access.strip()
    if isinstance(refresh, str) and refresh.strip() and jwt_is_expired(access):
        refreshed = refresh_ide_access_token(refresh.strip())
        if refreshed.get("ok") and isinstance(refreshed.get("access_token"), str):
            access = refreshed["access_token"]
    subject = decode_jwt_sub(access)
    return {
        "accessToken": access,
        "subject": subject,
        "email": bundle.get("email"),
        "source": "state.vscdb",
    }


def build_session_token_candidates(access_token: str, subject: str | None = None) -> list[str]:
    token = access_token.strip()
    if not token:
        return []
    sub = (subject or decode_jwt_sub(token) or "").strip()
    candidates: list[str] = []
    if sub:
        user_id = derive_dashboard_user_id(sub)
        candidates.append(f"{user_id}%3A%3A{token}")
        if user_id != sub:
            candidates.append(f"{sub}%3A%3A{token}")
    candidates.append(token)
    normalized: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        value = normalize_session_token(item)
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def launch_dashboard_browser() -> bool:
    try:
        opened = webbrowser.open(DASHBOARD_URL, new=2)
        return bool(opened)
    except OSError:
        return False


def wait_for_browser_dashboard_token(
    *,
    timeout_sec: int = 90,
    interval_sec: float = 2.0,
    launch_browser: bool = False,
) -> dict[str, Any] | None:
    launched = False
    if launch_browser:
        launched = launch_dashboard_browser()
    deadline = time.time() + max(0, timeout_sec)
    while time.time() < deadline:
        found = discover_browser_dashboard_token()
        if found and found.get("token"):
            if launched:
                found["launchedBrowser"] = True
            return found
        time.sleep(max(0.5, interval_sec))
    return None


def bootstrap_dashboard_session(
    *,
    launch_browser: bool = True,
    wait_seconds: int = 90,
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    launched_browser = False

    def add_candidate(token: str | None, source: str, **extra: Any) -> None:
        normalized = normalize_session_token(str(token or ""))
        if not normalized:
            return
        if any(item.get("token") == normalized for item in candidates):
            return
        candidates.append({"token": normalized, "source": source, **extra})

    browser_now = discover_browser_dashboard_token()
    if browser_now and browser_now.get("token"):
        add_candidate(str(browser_now["token"]), "browser-cookies", path=browser_now.get("path"))

    ide = ensure_fresh_access_token()
    if ide and ide.get("accessToken"):
        for token in build_session_token_candidates(str(ide["accessToken"]), ide.get("subject")):
            add_candidate(token, "ide-session", email=ide.get("email"))

    stored = read_credentials()
    if stored:
        add_candidate(stored, "credentials")

    has_browser_cookie = any(item.get("source") == "browser-cookies" for item in candidates)
    if launch_browser and not has_browser_cookie:
        launched_browser = launch_dashboard_browser()
        poll_seconds = max(0, int(wait_seconds))
        if poll_seconds > 0:
            waited = wait_for_browser_dashboard_token(
                timeout_sec=poll_seconds,
                launch_browser=False,
            )
            if waited and waited.get("token"):
                add_candidate(str(waited["token"]), "browser-cookies", path=waited.get("path"))
                try:
                    write_credentials(str(waited["token"]))
                except ValueError:
                    pass

    primary = candidates[0]["token"] if candidates else None
    return {
        "token": primary,
        "candidates": candidates,
        "launchedBrowser": launched_browser,
        "method": candidates[0]["source"] if candidates else "failed",
    }
