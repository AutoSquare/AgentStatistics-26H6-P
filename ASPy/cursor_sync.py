# -*- coding: utf-8 -*-
"""Cursor usage sync via dashboard JSON API (preferred) with CSV/tokscale fallback."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_auth import normalize_session_token
from cursor_http import request_text
from cursor_usage_api import (
    build_usage_json_document,
    default_usage_json_path,
    fetch_all_usage_events,
    usage_json_is_fresh,
    write_usage_json,
)

CURSOR_EXPORT_URL = "https://cursor.com/api/dashboard/export-usage-events-csv?strategy=tokens"
CURSOR_SYNC_FRESHNESS_SEC = 300


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
    result = request_text(CURSOR_EXPORT_URL, session_token, timeout=timeout, csv_export=True)
    if not result.get("ok") and result.get("kind") == "vercel_checkpoint":
        time.sleep(1.2)
        result = request_text(CURSOR_EXPORT_URL, session_token, timeout=timeout, csv_export=True)
    if result.get("ok"):
        text = str(result.get("text") or "")
        if text.strip():
            return text
        raise RuntimeError("Cursor API 返回空 CSV。")
    message = str(result.get("message") or "Cursor 同步失败")
    if result.get("kind") == "timeout":
        raise RuntimeError(
            f"{message} 若本地已有 cursor-cache/usage.csv，可先读取缓存。"
        )
    error = RuntimeError(message)
    setattr(error, "kind", result.get("kind"))
    raise error


def resolve_sync_token(session_token: str | None = None) -> tuple[str | None, dict[str, Any] | None]:
    if session_token:
        token = normalize_session_token(session_token)
        return token, {"source": "argument"} if token else (None, None)
    from cursor_discover import iter_session_token_candidates

    for candidate in iter_session_token_candidates():
        token = normalize_session_token(str(candidate.get("token") or ""))
        if token:
            return token, candidate
    return None, None


def iter_sync_token_candidates(session_token: str | None = None) -> list[tuple[str, dict[str, Any] | None]]:
    from cursor_discover import iter_session_token_candidates

    ordered: list[tuple[str, dict[str, Any] | None]] = []
    seen: set[str] = set()
    normalized = normalize_session_token(session_token) if session_token else None
    if normalized and normalized not in seen:
        seen.add(normalized)
        ordered.append((normalized, {"source": "argument"}))
    for candidate in iter_session_token_candidates():
        token = normalize_session_token(str(candidate.get("token") or ""))
        if token and token not in seen:
            seen.add(token)
            ordered.append((token, candidate))
    return ordered


def _count_csv_rows(csv_path: Path) -> int:
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except OSError:
        return 0
    lines = [line for line in text.splitlines() if line.strip()]
    return max(0, len(lines) - 1)


def _count_usage_json_rows(cache_dir: Path) -> int:
    path = default_usage_json_path(cache_dir)
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    if isinstance(payload, dict) and isinstance(payload.get("totalEvents"), int):
        return max(0, int(payload["totalEvents"]))
    events = payload.get("events") if isinstance(payload, dict) else None
    return len(events) if isinstance(events, list) else 0


def _usage_cache_is_fresh(cache_dir: Path, max_age_sec: int = CURSOR_SYNC_FRESHNESS_SEC) -> bool:
    return usage_json_is_fresh(cache_dir, max_age_sec) or _usage_csv_is_fresh(cache_dir, max_age_sec)


def _usage_csv_is_fresh(cache_dir: Path, max_age_sec: int = CURSOR_SYNC_FRESHNESS_SEC) -> bool:
    target = cache_dir / "usage.csv"
    if not target.is_file():
        return False
    try:
        age = time.time() - target.stat().st_mtime
    except OSError:
        return False
    return age >= 0 and age < max_age_sec


def fetch_usage_json(session_token: str, timeout: int = 120) -> list[dict[str, Any]]:
    result = fetch_all_usage_events(session_token, timeout=timeout)
    if not result.get("ok"):
        message = str(result.get("message") or "Cursor 用量 JSON 同步失败")
        error = RuntimeError(message)
        setattr(error, "kind", result.get("kind"))
        raise error
    events = result.get("events")
    if not isinstance(events, list):
        raise RuntimeError("Cursor 用量 JSON 响应缺少 events。")
    return [item for item in events if isinstance(item, dict)]


def ensure_tokscale_credentials(session_token: str | None = None) -> bool:
    """将当前登录态写入 tokscale 凭证文件，供 tokscale cursor sync 使用。"""
    token, _origin = resolve_sync_token(session_token)
    if not token:
        return False
    target = tokscale_credentials_path()
    existing = read_credentials(target)
    if existing == token and target.exists():
        return True
    write_credentials(token, target)
    return True


def _local_cache_sync_result(cache_dir: Path, *, warning: str | None = None) -> dict[str, Any] | None:
    json_path = default_usage_json_path(cache_dir)
    csv_path = cache_dir / "usage.csv"
    json_rows = _count_usage_json_rows(cache_dir) if json_path.is_file() else 0
    csv_rows = _count_csv_rows(csv_path) if csv_path.is_file() else 0
    if json_rows <= 0 and csv_rows <= 0:
        return None
    if json_rows >= csv_rows and json_path.is_file():
        path = str(json_path)
        rows = json_rows
        engine = "local-json"
    else:
        path = str(csv_path)
        rows = csv_rows
        engine = "local-csv"
    result: dict[str, Any] = {"synced": True, "rows": rows, "path": path, "engine": engine}
    if warning:
        result["warning"] = warning
    return result


def _attach_auth_metadata(result: dict[str, Any], origin: dict[str, Any] | None) -> dict[str, Any]:
    if not origin:
        return result
    result["authSource"] = origin.get("source")
    if origin.get("email"):
        result["email"] = origin.get("email")
    if origin.get("path"):
        result["authPath"] = origin.get("path")
    return result


def _save_usage_json(cache_dir: Path, events: list[dict[str, Any]], *, source: str) -> dict[str, Any]:
    document = build_usage_json_document(events, source=source)
    path = write_usage_json(cache_dir, document)
    return {
        "synced": True,
        "rows": len(events),
        "path": str(path),
        "engine": source,
    }


def sync_cursor_cache(
    cache_dir: Path,
    session_token: str | None = None,
    *,
    force: bool = False,
    skip_cloud: bool = False,
) -> dict[str, Any]:
    if skip_cloud:
        local_only = _local_cache_sync_result(cache_dir)
        if local_only is not None:
            return local_only
        return {
            "synced": False,
            "rows": 0,
            "engine": "webview-pending",
            "error": "本地缓存为空。请在本机浏览器登录 cursor.com/dashboard，或由应用内 WebView2 完成同步。",
        }

    json_path = default_usage_json_path(cache_dir)
    if not force and _usage_cache_is_fresh(cache_dir):
        rows = _count_usage_json_rows(cache_dir)
        if rows <= 0:
            rows = _count_csv_rows(cache_dir / "usage.csv")
        fresh_path = str(json_path if json_path.is_file() else cache_dir / "usage.csv")
        return {
            "synced": True,
            "rows": rows,
            "path": fresh_path,
            "engine": "cache-fresh",
        }
    ensure_tokscale_credentials(session_token)
    from cursor_tokscale_cli import is_default_tokscale_cache, sync_via_tokscale_cli

    candidates = iter_sync_token_candidates(session_token)
    if not candidates:
        return {
            "synced": False,
            "rows": 0,
            "error": "未检测到 Cursor 登录态。请在本机 Cursor 完成登录，或保存 WorkosCursorSessionToken 凭证。",
        }

    origin: dict[str, Any] | None = None
    last_error = "Cursor 同步失败"
    last_kind: str | None = None

    for token, candidate in candidates:
        origin = candidate
        try:
            events = fetch_usage_json(token)
            result = _save_usage_json(cache_dir, events, source="cursor-json")
            return _attach_auth_metadata(result, origin)
        except RuntimeError as exc:
            last_error = str(exc)
            last_kind = getattr(exc, "kind", None)
            if last_kind == "vercel_checkpoint":
                break

    local_after_json = _local_cache_sync_result(cache_dir, warning=last_error)
    if local_after_json is not None:
        return local_after_json

    csv_text = ""
    for token, candidate in candidates:
        origin = candidate
        try:
            csv_text = fetch_usage_csv(token)
            break
        except RuntimeError as exc:
            last_error = str(exc)
            last_kind = getattr(exc, "kind", None)
            csv_text = ""

    if not csv_text.strip():
        cli_result: dict[str, Any] | None = None
        if is_default_tokscale_cache(cache_dir):
            cli_result = sync_via_tokscale_cli()
            if cli_result.get("synced"):
                return cli_result
        local_final = _local_cache_sync_result(cache_dir, warning=last_error)
        if local_final is not None:
            return local_final
        if cli_result is not None:
            return cli_result
        result = {"synced": False, "rows": 0, "error": last_error}
        if last_kind:
            result["errorKind"] = last_kind
        return result

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
    result = {"synced": True, "rows": rows, "path": str(target), "engine": "cursor-csv"}
    return _attach_auth_metadata(result, origin)
