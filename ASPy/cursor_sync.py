# -*- coding: utf-8 -*-
"""Cursor usage sync via dashboard CSV export API."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_auth import normalize_session_token

CURSOR_EXPORT_URL = "https://cursor.com/api/dashboard/export-usage-events-csv?strategy=tokens"
DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cursor.com/settings",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def app_credentials_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "cursor_credentials.json"


def tokscale_credentials_path() -> Path:
    return Path.home() / ".config" / "tokscale" / "cursor-credentials.json"


def derive_account_id(token: str) -> str:
    if "%3A%3A" in token:
        head = token.split("%3A%3A", 1)[0].strip()
        if head:
            return head
    if "::" in token:
        head = token.split("::", 1)[0].strip()
        if head:
            return head
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return "anon-" + digest[:12]


def read_credentials(path: Path | None = None) -> str | None:
    candidates = [path, tokscale_credentials_path(), app_credentials_path()]
    for file in candidates:
        if file is None or not file.exists():
            continue
        try:
            payload = json.loads(file.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict) and isinstance(payload.get("sessionToken"), str) and payload["sessionToken"].strip():
            return normalize_session_token(payload["sessionToken"].strip())
        accounts = payload.get("accounts") if isinstance(payload, dict) else None
        active = payload.get("activeAccountId") if isinstance(payload, dict) else None
        if isinstance(accounts, dict) and isinstance(active, str):
            account = accounts.get(active)
            if isinstance(account, dict) and isinstance(account.get("sessionToken"), str) and account["sessionToken"].strip():
                return normalize_session_token(account["sessionToken"].strip())
    return None


def write_credentials(token: str, path: Path | None = None) -> Path:
    normalized = normalize_session_token(token)
    if not normalized:
        raise ValueError("无效的 Cursor Session Token，请粘贴 Cookie 的值而非名称。")
    token = normalized
    file = path or app_credentials_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    account_id = derive_account_id(token)
    payload = {
        "version": 1,
        "activeAccountId": account_id,
        "accounts": {
            account_id: {
                "sessionToken": token,
                "userId": account_id if not account_id.startswith("anon-") else None,
                "createdAt": datetime.now(timezone.utc).isoformat(),
                "expiresAt": None,
                "label": None,
            }
        },
    }
    fd, tmp_name = tempfile.mkstemp(prefix=file.name + ".", suffix=".tmp", dir=str(file.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, file)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    return file


def fetch_usage_csv(session_token: str, timeout: int = 120) -> str:
    request = urllib.request.Request(
        CURSOR_EXPORT_URL,
        headers={**DEFAULT_HEADERS, "Cookie": f"WorkosCursorSessionToken={session_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8-sig")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(f"Cursor 同步失败 HTTP {exc.code}: {body}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(
            f"Cursor 同步超时（>{timeout}s）。请检查网络或稍后重试；若本地已有 cursor-cache/usage.csv，可先读取缓存。"
        ) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            raise RuntimeError(
                f"Cursor 同步超时（>{timeout}s）。请检查网络或稍后重试；若本地已有 cursor-cache/usage.csv，可先读取缓存。"
            ) from exc
        raise RuntimeError(f"Cursor 同步网络错误: {reason}") from exc


def resolve_sync_token(session_token: str | None = None) -> tuple[str | None, dict[str, Any] | None]:
    if session_token:
        token = normalize_session_token(session_token)
        return token, {"source": "argument"} if token else None
    stored = read_credentials()
    if stored:
        return stored, {"source": "credentials"}
    from cursor_discover import discover_local_session_token

    discovered = discover_local_session_token()
    if discovered and discovered.get("token"):
        return str(discovered["token"]), discovered
    return None, None


def sync_cursor_cache(cache_dir: Path, session_token: str | None = None) -> dict[str, Any]:
    token, origin = resolve_sync_token(session_token)
    if not token:
        return {
            "synced": False,
            "rows": 0,
            "error": "未检测到 Cursor 登录态。请配置 tokscale / token-monitor 凭证，或使用本机 Cursor 登录缓存（无需保持应用打开）。",
        }
    try:
        csv_text = fetch_usage_csv(token)
    except RuntimeError as exc:
        return {"synced": False, "rows": 0, "error": str(exc)}
    if not csv_text.strip():
        return {"synced": False, "rows": 0, "error": "Cursor API 返回空 CSV"}
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = cache_dir / "usage.csv"
    fd, tmp_name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(cache_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(csv_text if csv_text.endswith("\n") else csv_text + "\n")
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    rows = max(0, csv_text.count("\n") - 1)
    result: dict[str, Any] = {"synced": True, "rows": rows, "path": str(target)}
    if origin:
        result["authSource"] = origin.get("source")
        if origin.get("email"):
            result["email"] = origin.get("email")
        if origin.get("path"):
            result["authPath"] = origin.get("path")
    return result
