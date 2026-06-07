# -*- coding: utf-8 -*-
"""Cursor usage statistics adapter for AgentStatistics."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_cli_auth import read_cli_auth_bundle
from cursor_discover import build_workos_session_token, decode_jwt_sub, iter_session_token_candidates
from cursor_limits import build_cursor_risk_rows, default_limits_cache_path, probe_cursor_limits
from cursor_sync import (
    derive_account_id,
    ensure_tokscale_credentials,
    sync_cursor_cache,
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
USAGE_ACCOUNT_ONLINE_TTL_SEC = 600


def _account_store_dir(cache_dir: Path) -> Path:
    return cache_dir / "accounts"


def normalize_cursor_account_id(account_id: str | None) -> str:
    text = str(account_id or "").strip()
    if "|" in text:
        suffix = text.rsplit("|", 1)[-1]
        if suffix.startswith("user_"):
            return suffix
    return text


def _account_folder(cache_dir: Path, account_id: str) -> Path:
    canonical_id = normalize_cursor_account_id(account_id)
    digest = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]
    return _account_store_dir(cache_dir) / digest


def _current_usage_account(cache_dir: Path) -> dict[str, Any] | None:
    return _read_json_object(cache_dir / "usage-account.json")


def _usage_account_is_recent(account: dict[str, Any]) -> bool:
    updated_at = str(account.get("updatedAt") or "").strip()
    if not updated_at:
        return False
    try:
        parsed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
    return 0 <= age <= USAGE_ACCOUNT_ONLINE_TTL_SEC


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
    current_account = _current_usage_account(cache_dir) or {}
    stored_account = normalize_cursor_account_id((usage_document or {}).get("accountId"))
    declared_account = normalize_cursor_account_id(current_account.get("accountId"))
    effective_account = stored_account or declared_account or normalize_cursor_account_id(account_id)
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
            or current_account.get("email")
            or (email if effective_account == account_id else None)
            or existing.get("email")
        ),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    if usage_document and usage_path.is_file() and (not declared_account or stored_account == declared_account or stored_account == effective_account):
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
    grouped: dict[str, dict[str, Any]] = {}
    store = _account_store_dir(cache_dir)
    if not store.is_dir():
        return []
    for metadata_path in sorted(store.glob("*/account.json")):
        metadata = _read_json_object(metadata_path)
        if not metadata:
            continue
        account_id = normalize_cursor_account_id(metadata.get("accountId"))
        if not account_id:
            continue
        account_dir = metadata_path.parent
        loaded = load_tokscale_usage(account_dir, "cursor", days, cache_path)
        if not loaded.get("events"):
            continue
        account = grouped.setdefault(
            account_id,
            {
                "id": account_id,
                "email": metadata.get("email"),
                "root": account_dir,
                "loaded": {"sessions": [], "events": [], "ttfb_events": [], "failure_events": [], "limits": None},
                "_event_keys": set(),
                "_session_keys": set(),
            },
        )
        if not account.get("email") and metadata.get("email"):
            account["email"] = metadata.get("email")
        for event in loaded.get("events") or []:
            key = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
            if key not in account["_event_keys"]:
                account["_event_keys"].add(key)
                account["loaded"]["events"].append(event)
        for session in loaded.get("sessions") or []:
            key = json.dumps(session, ensure_ascii=False, sort_keys=True, default=str)
            if key not in account["_session_keys"]:
                account["_session_keys"].add(key)
                account["loaded"]["sessions"].append(session)
        account["loaded"]["ttfb_events"].extend(loaded.get("ttfb_events") or [])
        account["loaded"]["failure_events"].extend(loaded.get("failure_events") or [])
    accounts = list(grouped.values())
    for account in accounts:
        account.pop("_event_keys", None)
        account.pop("_session_keys", None)
        account["loaded"]["events"].sort(key=lambda item: int(item.get("ts") or 0))
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
    do_sync: bool = False,
    force_sync: bool = False,
    skip_cloud: bool = False,
) -> dict[str, Any]:
    cli_auth = read_cli_auth_bundle() or {}
    cli_session_token = build_workos_session_token(
        str(cli_auth.get("accessToken") or ""),
        str(cli_auth.get("subject") or ""),
    )
    resolved = enrich_cursor_identity(
        {
            "token": cli_session_token,
            "source": "cursor-cli",
            "email": cli_auth.get("email"),
            "accountId": cli_auth.get("accountId"),
            "path": cli_auth.get("path"),
        }
        if cli_session_token
        else None
    )
    sync_result: dict[str, Any] | None = None
    if do_sync:
        ensure_tokscale_credentials()
        sync_result = sync_cursor_cache(cache_dir, force=force_sync, skip_cloud=skip_cloud)
        if sync_result.get("synced") and sync_result.get("path"):
            invalidate_parse_cache_entries(cache_path, sync_result["path"], cache_dir / "usage.json")

    current_usage_account = _current_usage_account(cache_dir) or {}
    usage_document = _read_json_object(cache_dir / "usage.json") or {}
    synced_account_id = normalize_cursor_account_id(usage_document.get("accountId"))
    cli_account_id = normalize_cursor_account_id(cli_auth.get("accountId"))
    verified_account_id = cli_account_id
    current_online = bool(verified_account_id)
    token = resolved.get("token") if resolved else None
    legacy_account_id = normalize_cursor_account_id(current_usage_account.get("accountId"))
    account_id = cli_account_id or synced_account_id or legacy_account_id
    if not account_id and resolved:
        account_id = normalize_cursor_account_id(derive_account_id(str(token))) if token else ""
    account_email = str(
        (cli_auth.get("email") if account_id == cli_account_id else None)
        or usage_document.get("email")
        or current_usage_account.get("email")
        or ""
    ).strip()
    if not account_email:
        account_email = str(resolved.get("email") or "").strip() if resolved else ""
    if account_id:
        archive_current_account_sources(cache_dir, account_id, account_email or None)
    accounts = load_cursor_accounts(cache_dir, days, cache_path)
    loaded = combine_cursor_accounts(accounts) if accounts else load_tokscale_usage(cache_dir, "cursor", days, cache_path)

    limits_cache_path = default_limits_cache_path()
    limits_probe = probe_cursor_limits(
        token,
        limits_cache_path,
        max_cache_age_sec=0,
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
                    "Cursor CLI API"
                    if limits_probe.get("cliApi")
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
        identity = normalize_cursor_account_id(account["id"])
        email = str(account.get("email") or "").strip()
        account_sync_status = (
            str(sync_status.get("status") or "ok")
            if normalize_cursor_account_id(sync_status.get("accountId")) == identity
            else "ok"
        )
        account_payloads.append(
            {
                "id": identity,
                "email": email or None,
                "label": email or f"Cursor 账号 · {identity[-8:]}",
                "idSuffix": identity[-8:],
                "isCurrent": identity == verified_account_id,
                "isOnline": identity == verified_account_id,
                "syncStatus": account_sync_status if account_sync_status in {"ok", "partial", "error"} else "error",
                "syncMessage": sync_status.get("message") if account_sync_status != "ok" else None,
                "views": account_payload["views"],
                "records": account_payload["records"],
            }
        )
    payload["accounts"] = account_payloads
    payload["activeAccountId"] = verified_account_id or None
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
    elif cli_auth:
        payload["auth"] = {
            "source": "cursor-cli",
            "email": cli_auth.get("email"),
            "path": cli_auth.get("path"),
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
        help="skip Cursor API sync and read the local cache only",
    )
    args = parser.parse_args(argv)
    cache = None if args.no_cache else Path(args.cache)
    payload = build_payload(
        Path(args.cache_dir),
        args.days,
        cache,
        args.sync,
        force_sync=args.force_sync,
        skip_cloud=args.skip_cloud_sync,
    )
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
