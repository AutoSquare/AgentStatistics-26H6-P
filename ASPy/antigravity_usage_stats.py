# -*- coding: utf-8 -*-
"""Antigravity usage statistics adapter for AgentStatistics."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from antigravity_connect import detect_connections
from antigravity_jsonl import invalidate_parse_cache_entries, load_antigravity_usage
from antigravity_paths import antigravity_cli_root
from antigravity_quota import build_antigravity_risk_rows, probe_antigravity_quota
from antigravity_sync import sync_antigravity_cache
from antigravity_transcript import load_antigravity_transcript_usage, merge_loaded_usage
from tokscale_csv import build_session_catalog, default_tokscale_cache_dir
from usage_common import build_standard_payload

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

ANTIGRAVITY_PRICING_RULES = [
    {"label": "claude-sonnet-4-5", "patterns": ["claude-sonnet-4-5", "claude-sonnet-4.5"], "input": 3.00, "cached": 0.30, "output": 15.00},
    {"label": "claude-opus-4-6", "patterns": ["claude-opus-4-6", "claude-opus-4.6"], "input": 15.00, "cached": 1.50, "output": 75.00},
    {"label": "gemini-3-flash", "patterns": ["gemini-3-flash", "gemini-3.0-flash"], "input": 0.35, "cached": 0.035, "output": 1.05},
    {"label": "gemini-3-pro", "patterns": ["gemini-3-pro", "gemini-3.0-pro"], "input": 1.25, "cached": 0.125, "output": 5.00},
    {"label": "gemini-2.5", "patterns": ["gemini-2.5"], "input": 0.30, "cached": 0.03, "output": 2.50},
]


def default_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "antigravity_usage_cache.json"


def default_quota_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "antigravity_quota_cache.json"


def quota_has_pools(quota_probe: dict[str, Any]) -> bool:
    quota = quota_probe.get("quota")
    return isinstance(quota, dict) and bool(quota.get("pools"))


def resolve_data_status(events: list[dict[str, Any]], sync_result: dict[str, Any] | None, do_sync: bool) -> str:
    if events:
        return "ok"
    if do_sync and sync_result:
        error_text = str(sync_result.get("error") or "")
        if sync_result.get("error") and not sync_result.get("synced"):
            if "将尝试读取本地" in error_text:
                return "empty"
            return "sync_failed"
        if int(sync_result.get("sessions") or 0) <= 0:
            return "parse_empty"
    return "empty"


def build_payload(
    cache_dir: Path,
    days: int,
    cache_path: Path | None,
    probe_quota: bool = True,
    do_sync: bool = False,
) -> dict[str, Any]:
    connections = detect_connections()
    sync_result: dict[str, Any] | None = None
    if do_sync:
        sync_result = sync_antigravity_cache(cache_dir, connections)
        if sync_result.get("synced"):
            invalidate_parse_cache_entries(cache_path)

    cache_loaded = load_antigravity_usage(cache_dir, days, cache_path)
    transcript_loaded = load_antigravity_transcript_usage(days, cache_path)
    loaded = merge_loaded_usage(cache_loaded, transcript_loaded)
    quota_cache_path = default_quota_cache_path()
    quota_probe = (
        probe_antigravity_quota(connections, quota_cache_path)
        if probe_quota
        else {"ok": False, "quota": None}
    )
    loaded["limits"] = quota_probe
    session_catalog = build_session_catalog(loaded, "antigravity")

    def risk_builder(loaded_data: dict[str, Any], cache_hit: float) -> list[dict[str, Any]]:
        return build_antigravity_risk_rows(loaded_data.get("limits"), cache_hit)

    payload = build_standard_payload(
        "antigravity",
        cache_dir,
        days,
        loaded,
        session_catalog,
        ANTIGRAVITY_PRICING_RULES,
        [
            {
                "metric": "Token 消耗",
                "source": "antigravity-cache JSONL + CLI transcript",
                "status": "ok" if loaded["events"] else "empty",
            },
            {
                "metric": "真实额度",
                "source": "Antigravity CLI Connect RPC" if not quota_probe.get("cached") else "本地额度缓存",
                "status": "ok" if quota_has_pools(quota_probe) else "empty",
            },
            {"metric": "会话排行", "source": "Antigravity session id", "status": "ok" if session_catalog else "empty"},
            {"metric": "模型排行", "source": "Antigravity model", "status": "ok" if loaded["events"] else "empty"},
        ],
        limits_meta={
            "raw": quota_probe.get("quota") or {},
            "planType": None,
            "cached": bool(quota_probe.get("cached")),
            "cachedAt": quota_probe.get("cachedAt"),
            "probeError": quota_probe.get("error") or quota_probe.get("probeError"),
            "sync": sync_result,
        },
        risk_builder=risk_builder,
    )
    payload["sync"] = sync_result or {"synced": False, "sessions": 0}
    payload["dataStatus"] = resolve_data_status(loaded["events"], sync_result, do_sync)
    cli_root = antigravity_cli_root()
    payload["auth"] = {
        "source": "antigravity-cli" if cli_root.is_dir() else "language_server",
        "connections": len(connections),
        "running": bool(connections),
        "cliDataDir": str(cli_root) if cli_root.is_dir() else None,
        "transcriptFiles": int(transcript_loaded.get("transcriptFiles") or 0),
        "cacheSessionFiles": int(cache_loaded.get("sessionFiles") or 0),
    }
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Antigravity usage statistics JSON.")
    parser.add_argument("--cache-dir", default=str(default_tokscale_cache_dir("antigravity")), help="tokscale antigravity-cache directory")
    parser.add_argument("--days", type=int, default=30, help="history window in days; <=0 means all")
    parser.add_argument("--cache", default=str(default_cache_path()), help="local parse cache path")
    parser.add_argument("--no-cache", action="store_true", help="disable local parse cache")
    parser.add_argument("--no-probe", action="store_true", help="skip live quota probe")
    parser.add_argument("--sync", action="store_true", help="sync JSONL artifacts from running Antigravity CLI")
    args = parser.parse_args(argv)
    cache = None if args.no_cache else Path(args.cache)
    payload = build_payload(Path(args.cache_dir), args.days, cache, probe_quota=not args.no_probe, do_sync=args.sync)
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
