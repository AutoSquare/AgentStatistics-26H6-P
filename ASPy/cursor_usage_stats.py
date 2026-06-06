# -*- coding: utf-8 -*-
"""Cursor usage statistics adapter for AgentStatistics."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from cursor_auth import normalize_session_token
from cursor_discover import decode_jwt_sub, iter_session_token_candidates, resolve_session_token
from cursor_limits import build_cursor_risk_rows, default_limits_cache_path, probe_cursor_limits
from cursor_sync import (
    derive_account_id,
    ensure_tokscale_credentials,
    sync_cursor_cache,
    tokscale_credentials_path,
    write_credentials,
)
from cursor_sync import _count_csv_rows as count_usage_csv_rows
from tokscale_csv import (
    build_session_catalog,
    default_tokscale_cache_dir,
    invalidate_parse_cache_entries,
    load_tokscale_usage,
)
from usage_common import build_standard_payload

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

CURSOR_PRICING_RULES = [
    {"label": "claude-3.5-sonnet", "patterns": ["claude-3.5-sonnet", "claude-3-5-sonnet"], "input": 3.00, "cached": 0.30, "output": 15.00},
    {"label": "claude-3.7-sonnet", "patterns": ["claude-3.7-sonnet", "claude-3-7-sonnet"], "input": 3.00, "cached": 0.30, "output": 15.00},
    {"label": "claude-sonnet-4", "patterns": ["claude-sonnet-4", "claude-4-sonnet"], "input": 3.00, "cached": 0.30, "output": 15.00},
    {"label": "gpt-4o", "patterns": ["gpt-4o"], "input": 2.50, "cached": 1.25, "output": 10.00},
    {"label": "cursor-auto", "patterns": ["cursor-auto", "auto"], "input": 2.00, "cached": 0.20, "output": 8.00},
    {"label": "o3", "patterns": ["o3-mini", "o3"], "input": 1.10, "cached": 0.55, "output": 4.40},
]


def _account_store_dir(cache_dir: Path) -> Path:
    return cache_dir / "accounts"


def _account_folder(cache_dir: Path, account_id: str) -> Path:
    digest = hashlib.sha256(account_id.encode("utf-8")).hexdigest()[:16]
    return _account_store_dir(cache_dir) / digest


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _token_subject(token: str | None) -> str:
    text = str(token or "").strip()
    if not text:
        return ""
    if "%3A%3A" in text:
        text = text.split("%3A%3A", 1)[1]
    elif "::" in text:
        text = text.split("::", 1)[1]
    return str(decode_jwt_sub(text) or "").strip()


def enrich_cursor_identity(resolved: dict[str, Any] | None) -> dict[str, Any] | None:
    """Fill missing account metadata from equivalent local login candidates."""
    if not resolved:
        return None
    enriched = dict(resolved)
    if str(enriched.get("email") or "").strip():
        return enriched
    subject = _token_subject(str(enriched.get("token") or ""))
    if not subject:
        return enriched
    for candidate in iter_session_token_candidates():
        if _token_subject(str(candidate.get("token") or "")) != subject:
            continue
        email = str(candidate.get("email") or "").strip()
        if email:
            enriched["email"] = email
            enriched["emailSource"] = candidate.get("source")
            break
    return enriched


def archive_current_account_sources(
    cache_dir: Path,
    account_id: str,
    email: str | None,
) -> None:
    """Persist the current root snapshot under its Cursor account."""
    usage_path = cache_dir / "usage.json"
    usage_document = _read_json_object(usage_path)
    stored_account = str((usage_document or {}).get("accountId") or "").strip()
    effective_account = stored_account or account_id
    if not effective_account:
        return

    account_dir = _account_folder(cache_dir, effective_account)
    account_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = account_dir / "account.json"
    existing = _read_json_object(metadata_path) or {}
    metadata = {
        "version": 1,
        "accountId": effective_account,
        "email": (
            (usage_document or {}).get("email")
            or (email if effective_account == account_id else None)
            or existing.get("email")
        ),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if usage_document and usage_path.is_file():
        shutil.copy2(usage_path, account_dir / "usage.json")

    legacy_csv = cache_dir / "usage.csv"
    account_csv = account_dir / "usage.csv"
    has_other_accounts = any(
        path.is_file()
        for path in _account_store_dir(cache_dir).glob("*/account.json")
        if path != metadata_path
    )
    if legacy_csv.is_file() and (account_csv.is_file() or not has_other_accounts):
        shutil.copy2(legacy_csv, account_csv)


def load_cursor_accounts(
    cache_dir: Path,
    days: int,
    cache_path: Path | None,
) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    store = _account_store_dir(cache_dir)
    if not store.is_dir():
        return accounts
    for metadata_path in sorted(store.glob("*/account.json")):
        metadata = _read_json_object(metadata_path)
        if not metadata:
            continue
        account_id = str(metadata.get("accountId") or "").strip()
        if not account_id:
            continue
        account_dir = metadata_path.parent
        loaded = load_tokscale_usage(account_dir, "cursor", days, cache_path)
        if not loaded.get("events"):
            continue
        accounts.append(
            {
                "id": account_id,
                "email": metadata.get("email"),
                "root": account_dir,
                "loaded": loaded,
            }
        )
    return accounts


def combine_cursor_accounts(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    combined = {
        "sessions": [],
        "events": [],
        "ttfb_events": [],
        "failure_events": [],
        "limits": None,
    }
    for account in accounts:
        account_id = str(account["id"])
        loaded = account["loaded"]
        for event in loaded.get("events") or []:
            clone = dict(event)
            clone["account_id"] = account_id
            clone["sid"] = f"{account_id}:{event.get('sid') or 'unknown'}"
            combined["events"].append(clone)
        for session in loaded.get("sessions") or []:
            clone = dict(session)
            clone["sid"] = f"{account_id}:{session.get('sid') or 'unknown'}"
            combined["sessions"].append(clone)
    combined["events"].sort(key=lambda item: int(item.get("ts") or 0))
    return combined


def default_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "cursor_usage_cache.json"


def default_sync_status_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "cursor_usage_sync_status.json"


def resolve_data_status(events: list[dict[str, Any]], sync_result: dict[str, Any] | None, do_sync: bool) -> str:
    if events:
        return "ok"
    if do_sync and sync_result:
        if sync_result.get("engine") in {"local-csv", "local-json", "local-read"}:
            return "empty"
        if not sync_result.get("synced"):
            return "sync_failed"
        if int(sync_result.get("rows") or 0) <= 0:
            return "parse_empty"
    return "empty"


def build_payload(
    cache_dir: Path,
    days: int,
    cache_path: Path | None,
    session_token: str | None = None,
    do_sync: bool = False,
    force_sync: bool = False,
    skip_cloud: bool = False,
) -> dict[str, Any]:
    normalized_token = normalize_session_token(session_token) if session_token else None
    resolved = enrich_cursor_identity(
        resolve_session_token() if not normalized_token else {"token": normalized_token, "source": "argument"}
    )
    sync_result: dict[str, Any] | None = None
    if do_sync:
        ensure_tokscale_credentials(normalized_token)
    if do_sync and normalized_token:
        write_credentials(normalized_token, tokscale_credentials_path())
        sync_result = sync_cursor_cache(
            cache_dir,
            normalized_token,
            force=force_sync,
            skip_cloud=skip_cloud,
        )
        if sync_result.get("synced") and sync_result.get("path"):
            invalidate_parse_cache_entries(cache_path, sync_result["path"], cache_dir / "usage.json")
    elif do_sync:
        sync_result = sync_cursor_cache(cache_dir, force=force_sync, skip_cloud=skip_cloud)
        if sync_result.get("synced") and sync_result.get("path"):
            invalidate_parse_cache_entries(cache_path, sync_result["path"], cache_dir / "usage.json")
            resolved_for_persist = resolve_session_token()
            if resolved_for_persist and resolved_for_persist.get("source") == "state.vscdb":
                try:
                    token_text = str(resolved_for_persist["token"])
                    write_credentials(token_text, tokscale_credentials_path())
                    write_credentials(token_text)
                except ValueError:
                    pass

    token = resolved.get("token") if resolved else None
    account_id = derive_account_id(str(token)) if token else ""
    account_email = str(resolved.get("email") or "").strip() if resolved else ""
    if account_id:
        archive_current_account_sources(cache_dir, account_id, account_email or None)
    accounts = load_cursor_accounts(cache_dir, days, cache_path)
    loaded = combine_cursor_accounts(accounts) if accounts else load_tokscale_usage(cache_dir, "cursor", days, cache_path)

    limits_cache_path = default_limits_cache_path()
    limits_probe = probe_cursor_limits(
        token,
        limits_cache_path,
        max_cache_age_sec=300 if do_sync else 0,
    )
    loaded["limits"] = limits_probe

    session_catalog = build_session_catalog(loaded, "cursor")

    def risk_builder(loaded_data: dict[str, Any], cache_hit: float) -> list[dict[str, Any]]:
        return build_cursor_risk_rows(loaded_data.get("limits"), cache_hit)

    payload = build_standard_payload(
        "cursor",
        cache_dir,
        days,
        loaded,
        session_catalog,
        CURSOR_PRICING_RULES,
        [
            {"metric": "Token 消耗", "source": "Cursor CSV + Dashboard JSON", "status": "ok" if loaded["events"] else "empty"},
            {
                "metric": "真实额度",
                "source": (
                    "Cursor IDE API"
                    if limits_probe.get("ideApi")
                    else "Cursor usage-summary API"
                    if not limits_probe.get("cached")
                    else "本地额度缓存"
                ),
                "status": "ok" if limits_probe.get("ok") else "empty",
            },
            {"metric": "会话排行", "source": "Cursor usage event id", "status": "ok" if session_catalog else "empty"},
            {"metric": "模型排行", "source": "Cursor usage event model", "status": "ok" if loaded["events"] else "empty"},
        ],
        limits_meta={
            "raw": limits_probe.get("usage") or {},
            "planType": (limits_probe.get("usage") or {}).get("membershipType"),
            "cached": bool(limits_probe.get("cached")),
            "cachedAt": limits_probe.get("cachedAt"),
            "probeError": limits_probe.get("error") or limits_probe.get("probeError"),
            "sync": sync_result,
        },
        risk_builder=risk_builder,
    )
    account_payloads: list[dict[str, Any]] = []
    sync_status = _read_json_object(default_sync_status_path()) or {}
    for account in accounts:
        account_loaded = account["loaded"]
        account_loaded["limits"] = limits_probe if account["id"] == account_id else None
        account_catalog = build_session_catalog(account_loaded, "cursor")
        account_payload = build_standard_payload(
            "cursor",
            account["root"],
            days,
            account_loaded,
            account_catalog,
            CURSOR_PRICING_RULES,
            [],
            risk_builder=risk_builder,
        )
        identity = str(account["id"])
        email = str(account.get("email") or "").strip()
        account_sync_status = (
            str(sync_status.get("status") or "ok")
            if str(sync_status.get("accountId") or "") == identity
            else "ok"
        )
        account_payloads.append(
            {
                "id": identity,
                "email": email or None,
                "label": email or f"Cursor 账号 · {identity[-8:]}",
                "idSuffix": identity[-8:],
                "isCurrent": identity == account_id,
                "syncStatus": account_sync_status if account_sync_status in {"ok", "partial", "error"} else "error",
                "syncMessage": sync_status.get("message") if account_sync_status != "ok" else None,
                "views": account_payload["views"],
                "records": account_payload["records"],
            }
        )
    payload["accounts"] = account_payloads
    payload["activeAccountId"] = account_id or None
    payload["syncAttempted"] = do_sync
    if sync_result is not None:
        payload["sync"] = sync_result
    elif loaded["events"]:
        usage_csv = cache_dir / "usage.csv"
        payload["sync"] = {
            "synced": True,
            "rows": count_usage_csv_rows(usage_csv) or len(loaded["events"]),
            "path": str(usage_csv),
            "engine": "local-read",
        }
    else:
        payload["sync"] = {"synced": False, "rows": 0}
    payload["dataStatus"] = resolve_data_status(loaded["events"], sync_result, do_sync)
    if resolved:
        payload["auth"] = {
            "source": resolved.get("source"),
            "email": resolved.get("email"),
            "path": resolved.get("path"),
        }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Cursor usage statistics JSON.")
    parser.add_argument("--cache-dir", default=str(default_tokscale_cache_dir("cursor")), help="tokscale cursor-cache directory")
    parser.add_argument("--days", type=int, default=30, help="history window in days; <=0 means all")
    parser.add_argument("--cache", default=str(default_cache_path()), help="local parse cache path")
    parser.add_argument("--no-cache", action="store_true", help="disable local parse cache")
    parser.add_argument("--sync", action="store_true", help="sync CSV from Cursor API before parsing")
    parser.add_argument("--force-sync", action="store_true", help="ignore cursor-cache freshness and always sync")
    parser.add_argument(
        "--skip-cloud-sync",
        action="store_true",
        help="skip Python HTTP sync; read WebView2-written local cache only",
    )
    parser.add_argument("--token", default="", help="Cursor WorkosCursorSessionToken")
    args = parser.parse_args(argv)
    cache = None if args.no_cache else Path(args.cache)
    token = normalize_session_token(args.token.strip()) if args.token.strip() else None
    payload = build_payload(
        Path(args.cache_dir),
        args.days,
        cache,
        token,
        args.sync or bool(token),
        force_sync=args.force_sync,
        skip_cloud=args.skip_cloud_sync,
    )
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
