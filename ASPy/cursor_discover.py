# -*- coding: utf-8 -*-
"""Resolve Cursor authentication exclusively from Cursor CLI storage."""
from __future__ import annotations

import base64
import json
from typing import Any

from cursor_auth import normalize_session_token


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


def iter_session_token_candidates() -> list[dict[str, Any]]:
    from cursor_cli_auth import read_cli_auth_bundle

    cli_auth = read_cli_auth_bundle()
    if not cli_auth:
        return []
    token = normalize_session_token(
        build_workos_session_token(
            str(cli_auth["accessToken"]),
            str(cli_auth.get("subject") or ""),
        )
    )
    if not token:
        return []
    return [
        {
            "token": token,
            "source": "cursor-cli",
            "path": cli_auth.get("path"),
            "email": cli_auth.get("email"),
            "accountId": cli_auth.get("accountId"),
            "accessToken": cli_auth.get("accessToken"),
        }
    ]


def resolve_session_token() -> dict[str, Any] | None:
    candidates = iter_session_token_candidates()
    return candidates[0] if candidates else None
