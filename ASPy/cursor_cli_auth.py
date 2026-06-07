# -*- coding: utf-8 -*-
"""Read Cursor CLI identity and tokens from the CLI's own local storage."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from cursor_discover import decode_jwt_sub, derive_dashboard_user_id


def default_cli_config_path() -> Path:
    return Path.home() / ".cursor" / "cli-config.json"


def default_cli_auth_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Cursor" / "auth.json"


def _read_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def normalize_cli_auth_id(value: Any) -> str:
    text = str(value or "").strip()
    return derive_dashboard_user_id(text) if text else ""


def read_cli_auth_bundle(
    config_path: Path | None = None,
    auth_path: Path | None = None,
) -> dict[str, Any] | None:
    config_file = config_path or default_cli_config_path()
    auth_file = auth_path or default_cli_auth_path()
    config = _read_object(config_file)
    auth = _read_object(auth_file)
    if not config or not auth:
        return None

    auth_info = config.get("authInfo") if isinstance(config.get("authInfo"), dict) else {}
    access_token = str(auth.get("accessToken") or "").strip()
    if not access_token:
        return None

    token_subject = str(decode_jwt_sub(access_token) or "").strip()
    configured_subject = str(auth_info.get("authId") or "").strip()
    if configured_subject and token_subject and configured_subject != token_subject:
        return None

    account_id = normalize_cli_auth_id(configured_subject or token_subject)
    if not account_id:
        return None
    return {
        "accessToken": access_token,
        "refreshToken": str(auth.get("refreshToken") or "").strip() or None,
        "accountId": account_id,
        "subject": configured_subject or token_subject,
        "email": str(auth_info.get("email") or "").strip() or None,
        "displayName": str(auth_info.get("displayName") or "").strip() or None,
        "source": "cursor-cli",
        "path": str(auth_file),
    }

