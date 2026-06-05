# -*- coding: utf-8 -*-
"""Codex usage statistics adapter for AgentStatistics.

The parsing and aggregation rules intentionally mirror CodexScope's
generate_codex_data.go. Only usage metadata is retained; prompt text,
assistant text, tool output, and file contents are ignored.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from usage_common import (
    DAY_MS,
    build_standard_payload,
    choose_history_granularity,
    cutoff_for_days,
    iso_time,
    load_generic_cache,
    parse_time,
    project_name,
    tail,
    unix_ms,
    write_generic_cache,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    def nonzero(self) -> bool:
        return any(
            (
                self.input_tokens,
                self.cached_input_tokens,
                self.output_tokens,
                self.reasoning_output_tokens,
                self.total_tokens,
            )
        )


@dataclass
class ParsedFile:
    sid: str = ""
    file: str = ""
    cwd: str = ""
    model: str = "unknown"
    usage_events: list[dict[str, Any]] = field(default_factory=list)
    completion_events: list[dict[str, Any]] = field(default_factory=list)
    failure_events: list[dict[str, Any]] = field(default_factory=list)
    latest_limits: dict[str, Any] | None = None
    latest_limits_ts: str = ""
    last_total: dict[str, int] | None = None
    has_last_total: bool = False


PRICING_RULES = [
    {"label": "gpt-5.5", "patterns": ["gpt-5.5"], "input": 5.00, "cached": 0.50, "output": 30.00},
    {
        "label": "gpt-5.4 mini",
        "patterns": ["gpt-5.4-mini", "gpt_5.4_mini", "gpt 5.4 mini"],
        "input": 0.75,
        "cached": 0.075,
        "output": 4.50,
    },
    {"label": "gpt-5.4", "patterns": ["gpt-5.4"], "input": 2.50, "cached": 0.25, "output": 15.00},
    {
        "label": "gpt-5.3 codex spark",
        "patterns": ["gpt-5.3-codex-spark", "gpt_5.3_codex_spark", "gpt 5.3 codex spark"],
        "input": 1.75,
        "cached": 0.175,
        "output": 14.00,
    },
    {
        "label": "gpt-5.3 codex",
        "patterns": ["gpt-5.3-codex", "gpt_5.3_codex", "gpt 5.3 codex"],
        "input": 1.75,
        "cached": 0.175,
        "output": 14.00,
    },
    {
        "label": "gpt-5.2 codex",
        "patterns": ["gpt-5.2-codex", "gpt_5.2_codex", "gpt 5.2 codex"],
        "input": 1.75,
        "cached": 0.175,
        "output": 14.00,
    },
    {
        "label": "gpt-5 / 5.1 codex",
        "patterns": ["gpt-5.1-codex", "gpt_5.1_codex", "gpt 5.1 codex", "gpt-5-codex", "gpt_5_codex", "gpt 5 codex", "gpt-5"],
        "input": 1.25,
        "cached": 0.125,
        "output": 10.00,
    },
]


def rate_limit_priority(limits: dict[str, Any] | None) -> int:
    if not limits:
        return 0
    limit_id = str(limits.get("limit_id") or "").lower()
    if limit_id == "codex":
        return 30
    if limit_id.startswith("codex_"):
        return 20
    if "codex" in limit_id:
        return 10
    return 1


def prefer_rate_limits(candidate: dict[str, Any], candidate_ts: datetime | None, current: dict[str, Any] | None, current_ts: datetime | None) -> bool:
    candidate_priority = rate_limit_priority(candidate)
    current_priority = rate_limit_priority(current)
    if candidate_priority != current_priority:
        return candidate_priority > current_priority
    if current is None:
        return True
    if candidate_ts is None:
        return False
    if current_ts is None:
        return True
    return candidate_ts >= current_ts


def parsed_from_cache(raw: dict[str, Any]) -> ParsedFile:
    parsed = ParsedFile(
        sid=str(raw.get("sid") or ""),
        file=str(raw.get("file") or ""),
        cwd=str(raw.get("cwd") or ""),
        model=str(raw.get("model") or "unknown"),
        usage_events=list(raw.get("usage_events") or raw.get("usageEvents") or []),
        completion_events=list(raw.get("completion_events") or raw.get("completionEvents") or []),
        failure_events=list(raw.get("failure_events") or raw.get("failureEvents") or []),
        latest_limits=raw.get("latest_limits") or raw.get("latestLimits"),
        latest_limits_ts=str(raw.get("latest_limits_ts") or raw.get("latestLimitsTs") or ""),
        last_total=raw.get("last_total") or raw.get("lastTotal"),
        has_last_total=bool(raw.get("has_last_total") or raw.get("hasLastTotal")),
    )
    return parsed


def parsed_to_cache(parsed: ParsedFile) -> dict[str, Any]:
    return asdict(parsed)


def parse_session_file(path: Path, cutoff: datetime, cached: ParsedFile | None = None, offset: int = 0) -> ParsedFile:
    parsed = cached or ParsedFile(sid=path.stem, file=str(path), model="unknown")
    if not parsed.sid:
        parsed.sid = path.stem
    if not parsed.file:
        parsed.file = str(path)
    if not parsed.model:
        parsed.model = "unknown"
    prev_total, has_prev_total = usage_from_obj(parsed.last_total or {})
    if not parsed.has_last_total:
        has_prev_total = False
    latest_limits_ts = parse_time(parsed.latest_limits_ts)

    try:
        with path.open("rb") as fh:
            if offset > 0:
                fh.seek(offset)
            for raw_line in fh:
                if not raw_line.strip():
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                top_type = obj.get("type")
                payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
                payload_type = payload.get("type")
                if top_type == "session_meta":
                    meta = payload
                    if meta.get("id"):
                        parsed.sid = str(meta["id"])
                    if meta.get("cwd"):
                        parsed.cwd = str(meta["cwd"])
                elif top_type == "turn_context":
                    if payload.get("model"):
                        parsed.model = str(payload["model"])
                    if payload.get("cwd"):
                        parsed.cwd = str(payload["cwd"])
                elif payload_type == "token_count":
                    ts = parse_time(obj.get("timestamp"))
                    limits = payload.get("rate_limits")
                    if isinstance(limits, dict) and prefer_rate_limits(limits, ts, parsed.latest_limits, latest_limits_ts):
                        parsed.latest_limits = limits
                        if ts is not None:
                            latest_limits_ts = ts
                            parsed.latest_limits_ts = iso_time(ts)
                    info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                    last_usage, has_last = usage_from_obj(info.get("last_token_usage"))
                    total_usage, has_total = usage_from_obj(info.get("total_token_usage"))
                    prev = prev_total
                    had_prev = has_prev_total
                    if has_total:
                        prev_total = total_usage
                        has_prev_total = True
                        parsed.last_total = asdict(total_usage)
                        parsed.has_last_total = True
                    if ts is not None and ts >= cutoff:
                        usage = Usage()
                        has_usage = False
                        if has_total and had_prev:
                            usage, has_usage = usage_delta(total_usage, prev)
                        elif has_last:
                            usage, has_usage = last_usage, True
                        if has_usage:
                            event: dict[str, Any] = {
                                "ts": iso_time(ts),
                                "sid": parsed.sid,
                                "usage": asdict(usage),
                                "model": parsed.model,
                            }
                            if has_total:
                                event["snapshot"] = asdict(total_usage)
                                event["hasSnapshot"] = True
                            parsed.usage_events.append(event)
                elif payload_type == "task_complete":
                    ts = parse_time(obj.get("timestamp"))
                    if ts is not None and ts >= cutoff:
                        event = {"ts": iso_time(ts), "sid": parsed.sid, "model": parsed.model}
                        if "duration_ms" in payload:
                            event["duration_ms"] = int(payload.get("duration_ms") or 0)
                        if "time_to_first_token_ms" in payload:
                            event["ttfb_ms"] = int(payload.get("time_to_first_token_ms") or 0)
                        parsed.completion_events.append(event)
                elif payload_type in ("error", "turn_aborted"):
                    ts = parse_time(obj.get("timestamp"))
                    if ts is not None and ts >= cutoff:
                        parsed.failure_events.append({"ts": iso_time(ts), "sid": parsed.sid, "model": parsed.model})
    except OSError:
        return parsed
    return parsed


def collect_session_files(root: Path, cutoff: datetime) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    if not root.exists() or not root.is_dir():
        return files
    cutoff_ts = cutoff.timestamp()
    for path in root.rglob("*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if stat.st_mtime < cutoff_ts:
            continue
        files.append({"path": str(path), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
    files.sort(key=lambda item: item["path"])
    return files


def can_append_from(path: Path, offset: int) -> bool:
    if offset <= 0:
        return False
    try:
        with path.open("rb") as fh:
            fh.seek(offset - 1)
            return fh.read(1) == b"\n"
    except OSError:
        return False


def load_sessions(root: Path, cutoff: datetime, cache_path: Path | None, days: int) -> dict[str, Any]:
    cache_files = load_generic_cache(cache_path, days) if cache_path else {}
    file_candidates = collect_session_files(root, cutoff)
    parsed_files: list[tuple[dict[str, Any], ParsedFile]] = []
    next_cache: dict[str, Any] = {}
    for file_info in file_candidates:
        path = Path(file_info["path"])
        cached = cache_files.get(str(path))
        parsed: ParsedFile
        if (
            isinstance(cached, dict)
            and cached.get("mtime_ns") == file_info["mtime_ns"]
            and cached.get("size") == file_info["size"]
            and isinstance(cached.get("parsed"), dict)
        ):
            parsed = parsed_from_cache(cached["parsed"])
        elif (
            isinstance(cached, dict)
            and isinstance(cached.get("parsed"), dict)
            and file_info["size"] > int(cached.get("size") or 0)
            and (cached["parsed"].get("has_last_total") or cached["parsed"].get("hasLastTotal"))
            and can_append_from(path, int(cached.get("size") or 0))
        ):
            parsed = parse_session_file(path, cutoff, parsed_from_cache(cached["parsed"]), int(cached.get("size") or 0))
        else:
            parsed = parse_session_file(path, cutoff)
        parsed_files.append((file_info, parsed))
        next_cache[str(path)] = {"mtime_ns": file_info["mtime_ns"], "size": file_info["size"], "parsed": parsed_to_cache(parsed)}
    if cache_path:
        write_generic_cache(cache_path, days, next_cache)

    loaded = {"sessions": [], "events": [], "ttfb_events": [], "failure_events": [], "limits": None}
    latest_limits_ts: datetime | None = None
    seen_usage: set[str] = set()
    for _, parsed in parsed_files:
        loaded["sessions"].append({"sid": parsed.sid, "file": parsed.file, "cwd": parsed.cwd, "model": parsed.model or "unknown"})
        parsed_limits_ts = parse_time(parsed.latest_limits_ts)
        if parsed.latest_limits and prefer_rate_limits(parsed.latest_limits, parsed_limits_ts, loaded["limits"], latest_limits_ts):
            loaded["limits"] = parsed.latest_limits
            latest_limits_ts = parsed_limits_ts
        for event in parsed.usage_events:
            ts = parse_time(event.get("ts"))
            if ts is None or ts < cutoff:
                continue
            sid = event.get("sid") or parsed.sid
            model = event.get("model") or parsed.model or "unknown"
            usage = event.get("usage") or {}
            key = "\0".join([str(sid), str(model), str(usage.get("input_tokens", 0)), str(usage.get("cached_input_tokens", 0)), str(usage.get("output_tokens", 0)), str(usage.get("reasoning_output_tokens", 0)), str(usage.get("total_tokens", 0))])
            if key in seen_usage:
                continue
            seen_usage.add(key)
            loaded["events"].append({"ts": unix_ms(ts), "sid": sid, "model": model, "usage": usage})
        for event in parsed.completion_events:
            ts = parse_time(event.get("ts"))
            if ts is not None and ts >= cutoff and int(event.get("ttfb_ms") or 0) > 0:
                loaded["ttfb_events"].append({"ts": unix_ms(ts), "sid": event.get("sid") or parsed.sid, "model": event.get("model") or parsed.model or "unknown", "ttfb_ms": int(event.get("ttfb_ms") or 0)})
        for event in parsed.failure_events:
            ts = parse_time(event.get("ts"))
            if ts is not None and ts >= cutoff:
                loaded["failure_events"].append({"ts": unix_ms(ts), "sid": event.get("sid") or parsed.sid, "model": event.get("model") or parsed.model or "unknown"})

    loaded["events"].sort(key=lambda row: row["ts"])
    loaded["ttfb_events"].sort(key=lambda row: row["ts"])
    loaded["failure_events"].sort(key=lambda row: row["ts"])
    return loaded


def usage_from_obj(obj: Any) -> tuple[Usage, bool]:
    if not isinstance(obj, dict):
        return Usage(), False

    def read(key: str) -> int:
        raw = obj.get(key, 0)
        if isinstance(raw, bool):
            return 0
        try:
            n = int(raw)
        except (TypeError, ValueError):
            return 0
        return max(0, n)

    usage = Usage(
        input_tokens=read("input_tokens"),
        cached_input_tokens=read("cached_input_tokens"),
        output_tokens=read("output_tokens"),
        reasoning_output_tokens=read("reasoning_output_tokens"),
        total_tokens=read("total_tokens"),
    )
    return usage, usage.nonzero()


def usage_delta(current: Usage, previous: Usage) -> tuple[Usage, bool]:
    usage = Usage(
        input_tokens=max(0, current.input_tokens - previous.input_tokens),
        cached_input_tokens=max(0, current.cached_input_tokens - previous.cached_input_tokens),
        output_tokens=max(0, current.output_tokens - previous.output_tokens),
        reasoning_output_tokens=max(0, current.reasoning_output_tokens - previous.reasoning_output_tokens),
        total_tokens=max(0, current.total_tokens - previous.total_tokens),
    )
    return usage, usage.nonzero()


def build_payload(root: Path, days: int, cache_path: Path | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = cutoff_for_days(days, now)
    loaded = load_sessions(root, cutoff, cache_path, days)
    session_catalog: dict[str, dict[str, str]] = {}
    for session in loaded["sessions"]:
        sid = session.get("sid") or "unknown"
        session_catalog[sid] = {"name": project_name(session.get("cwd") or "", f"session {tail(sid, 6)}"), "model": session.get("model") or "unknown"}
    for event in loaded["events"]:
        sid = event.get("sid") or "unknown"
        session_catalog.setdefault(sid, {"name": f"session {tail(sid, 6)}", "model": event.get("model") or "unknown"})

    limits = loaded.get("limits") or {}
    payload = build_standard_payload(
        "codex",
        root,
        days,
        loaded,
        session_catalog,
        PRICING_RULES,
        [
            {"metric": "Token 消耗", "source": "Codex token_count", "status": "ok" if loaded["events"] else "empty"},
            {"metric": "真实额度", "source": "Codex rate_limits", "status": "ok" if limits else "empty"},
            {"metric": "会话排行", "source": "Codex session metadata", "status": "ok" if session_catalog else "empty"},
            {"metric": "模型排行", "source": "Codex turn_context", "status": "ok" if loaded["events"] else "empty"},
        ],
        limits_meta={
            "raw": limits,
            "planType": limits.get("plan_type"),
            "rateLimitReachedType": limits.get("rate_limit_reached_type"),
        },
    )
    return payload


def default_root() -> Path:
    return Path.home() / ".codex" / "sessions"


def default_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "codex_usage_cache.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Codex usage statistics JSON.")
    parser.add_argument("--root", default=str(default_root()), help="Codex sessions directory")
    parser.add_argument("--days", type=int, default=30, help="history window in days; <=0 means all")
    parser.add_argument("--cache", default=str(default_cache_path()), help="local parse cache path")
    parser.add_argument("--no-cache", action="store_true", help="disable local cache")
    args = parser.parse_args(argv)
    cache = None if args.no_cache else Path(args.cache)
    payload = build_payload(Path(args.root), args.days, cache)
    json.dump(payload, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
