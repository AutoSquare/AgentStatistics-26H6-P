# -*- coding: utf-8 -*-
"""Cursor usage statistics adapter for AgentStatistics."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from cursor_auth import normalize_session_token
from cursor_discover import resolve_session_token
from cursor_limits import build_cursor_risk_rows, probe_cursor_limits
from cursor_sync import sync_cursor_cache, write_credentials
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


def default_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "cursor_usage_cache.json"


def resolve_data_status(events: list[dict[str, Any]], sync_result: dict[str, Any] | None, do_sync: bool) -> str:
    if events:
        return "ok"
    if do_sync and sync_result:
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
) -> dict[str, Any]:
    normalized_token = normalize_session_token(session_token) if session_token else None
    sync_result: dict[str, Any] | None = None
    if do_sync and normalized_token:
        write_credentials(normalized_token)
        sync_result = sync_cursor_cache(cache_dir, normalized_token)
        if sync_result.get("synced") and sync_result.get("path"):
            invalidate_parse_cache_entries(cache_path, sync_result["path"])
    elif do_sync:
        sync_result = sync_cursor_cache(cache_dir)
        if sync_result.get("synced") and sync_result.get("path"):
            invalidate_parse_cache_entries(cache_path, sync_result["path"])

    loaded = load_tokscale_usage(cache_dir, "cursor", days, cache_path)
    resolved = resolve_session_token() if not normalized_token else {"token": normalized_token, "source": "argument"}
    token = resolved.get("token") if resolved else None
    limits_probe = probe_cursor_limits(token) if token else {"ok": False, "usage": None}
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
            {"metric": "Token 消耗", "source": "tokscale cursor-cache CSV", "status": "ok" if loaded["events"] else "empty"},
            {"metric": "真实额度", "source": "Cursor usage-summary API", "status": "ok" if limits_probe.get("ok") else "empty"},
            {"metric": "会话排行", "source": "Cursor CSV session/agent id", "status": "ok" if session_catalog else "empty"},
            {"metric": "模型排行", "source": "Cursor CSV model", "status": "ok" if loaded["events"] else "empty"},
        ],
        limits_meta={
            "raw": limits_probe.get("usage") or {},
            "planType": (limits_probe.get("usage") or {}).get("membershipType"),
            "sync": sync_result,
        },
        risk_builder=risk_builder,
    )
    payload["sync"] = sync_result or {"synced": False, "rows": 0}
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
    parser.add_argument("--token", default="", help="Cursor WorkosCursorSessionToken")
    args = parser.parse_args(argv)
    cache = None if args.no_cache else Path(args.cache)
    token = normalize_session_token(args.token.strip()) if args.token.strip() else None
    payload = build_payload(Path(args.cache_dir), args.days, cache, token, args.sync or bool(token))
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
