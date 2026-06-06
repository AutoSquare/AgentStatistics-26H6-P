# -*- coding: utf-8 -*-
"""Parse tokscale cursor/antigravity usage CSV caches into unified usage events."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_usage_api import default_usage_json_path, load_normalized_events_from_json
from usage_common import cutoff_for_days, load_generic_cache, parse_time, tail, unix_ms, write_generic_cache


def default_tokscale_cache_dir(client: str) -> Path:
    return Path.home() / ".config" / "tokscale" / f"{client}-cache"


def collect_usage_files(cache_dir: Path) -> list[Path]:
    if not cache_dir.exists():
        return []
    files = sorted(cache_dir.glob("usage*.csv"))
    return [path for path in files if path.is_file()]


def parse_cost(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text or text in {"-", "Included", "included", "N/A", "n/a"}:
        return 0.0
    try:
        return max(0.0, float(text))
    except ValueError:
        return 0.0


def parse_int(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return 0
    try:
        return max(0, int(float(text)))
    except ValueError:
        return 0


def normalize_model_name(model: str, client: str) -> str:
    raw = str(model or "").strip()
    if not raw:
        return "unknown"
    if client == "cursor" and raw.lower() == "auto":
        return "cursor-auto"
    return raw


def normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())


def header_index(headers: list[str]) -> dict[str, int]:
    return {normalize_header(name): index for index, name in enumerate(headers)}


def pick_column(index: dict[str, int], *aliases: str) -> int | None:
    for alias in aliases:
        key = normalize_header(alias)
        if key in index:
            return index[key]
    return None


def parse_csv_text(text: str, client: str, cutoff: datetime) -> list[dict[str, Any]]:
    if not text.strip():
        return []
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []
    headers = rows[0]
    index = header_index(headers)
    if pick_column(index, "date", "timestamp", "createdat", "created", "time") is None:
        return []

    date_col = pick_column(index, "date", "timestamp", "createdat", "created", "time")
    model_col = pick_column(index, "model")
    cache_write_col = pick_column(index, "inputwithcachewrite", "input (w/ cache write)", "cachewrite", "cache write")
    input_col = pick_column(index, "inputwithoutcachewrite", "input (w/o cache write)", "input")
    cache_read_col = pick_column(index, "cacheread", "cache read")
    output_col = pick_column(index, "outputtokens", "output tokens", "output")
    total_col = pick_column(index, "totaltokens", "total tokens", "total")
    cost_col = pick_column(index, "costtoyou", "cost to you", "cost")
    kind_col = pick_column(index, "kind")
    agent_col = pick_column(index, "cloudagentid", "cloud agent id", "agentid")
    automation_col = pick_column(index, "automationid", "automation id")
    session_col = pick_column(index, "sessionid", "session id", "session")

    events: list[dict[str, Any]] = []
    for row in rows[1:]:
        if not row or all(not str(cell).strip() for cell in row):
            continue
        if date_col is None or date_col >= len(row):
            continue
        ts = parse_time(str(row[date_col]).strip())
        if ts is None:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
                try:
                    ts = datetime.strptime(str(row[date_col]).strip(), fmt).replace(tzinfo=timezone.utc)
                    break
                except ValueError:
                    ts = None
        if ts is None or ts < cutoff:
            continue

        model = normalize_model_name(row[model_col] if model_col is not None and model_col < len(row) else "", client)
        cache_write = parse_int(row[cache_write_col]) if cache_write_col is not None and cache_write_col < len(row) else 0
        input_no_cache = parse_int(row[input_col]) if input_col is not None and input_col < len(row) else 0
        cache_read = parse_int(row[cache_read_col]) if cache_read_col is not None and cache_read_col < len(row) else 0
        output_tokens = parse_int(row[output_col]) if output_col is not None and output_col < len(row) else 0
        total_tokens = parse_int(row[total_col]) if total_col is not None and total_col < len(row) else 0
        cost = parse_cost(row[cost_col]) if cost_col is not None and cost_col < len(row) else 0.0

        input_tokens = input_no_cache + cache_write + cache_read
        cached_input_tokens = cache_read + cache_write
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens
        if input_tokens <= 0 and total_tokens > 0:
            input_tokens = max(0, total_tokens - output_tokens)
            cached_input_tokens = min(cached_input_tokens, input_tokens)

        sid_parts = []
        if session_col is not None and session_col < len(row) and str(row[session_col]).strip():
            sid_parts.append(str(row[session_col]).strip())
        if agent_col is not None and agent_col < len(row) and str(row[agent_col]).strip():
            sid_parts.append(str(row[agent_col]).strip())
        if automation_col is not None and automation_col < len(row) and str(row[automation_col]).strip():
            sid_parts.append(str(row[automation_col]).strip())
        if kind_col is not None and kind_col < len(row) and str(row[kind_col]).strip():
            sid_parts.append(str(row[kind_col]).strip())
        if sid_parts:
            sid = f"{client}:" + ":".join(sid_parts)
        else:
            digest = hashlib.sha256(f"{client}:{ts.isoformat()}:{model}:{total_tokens}".encode("utf-8")).hexdigest()[:10]
            sid = f"{client}:session-{digest}"

        events.append(
            {
                "ts": unix_ms(ts),
                "sid": sid,
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cached_input_tokens,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": 0,
                    "total_tokens": total_tokens,
                },
                "cost": cost,
            }
        )
    events.sort(key=lambda item: item["ts"])
    return events


def load_csv_events(cache_dir: Path, client: str, cutoff: datetime, cache_path: Path | None, days: int) -> dict[str, Any]:
    files = collect_usage_files(cache_dir)
    cache_files = load_generic_cache(cache_path, days) if cache_path else {}
    all_events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    next_cache: dict[str, Any] = {}

    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = cache_files.get(str(path))
        if (
            isinstance(cached, dict)
            and cached.get("mtime_ns") == stat.st_mtime_ns
            and cached.get("size") == stat.st_size
            and isinstance(cached.get("events"), list)
        ):
            file_events = cached["events"]
        else:
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            file_events = parse_csv_text(text, client, cutoff)
            next_cache[str(path)] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "events": file_events}
        all_events.extend(file_events)
        for event in file_events:
            sid = event.get("sid") or "unknown"
            if sid in seen_sessions:
                continue
            seen_sessions.add(sid)
            label = sid.split(":", 1)[-1]
            sessions.append({"sid": sid, "file": str(path), "cwd": label, "model": event.get("model") or "unknown"})

    if cache_path:
        write_generic_cache(cache_path, days, next_cache)

    all_events.sort(key=lambda item: item["ts"])
    return {
        "sessions": sessions,
        "events": all_events,
        "ttfb_events": [],
        "failure_events": [],
        "limits": None,
    }


def build_session_catalog(loaded: dict[str, Any], client: str) -> dict[str, dict[str, str]]:
    catalog: dict[str, dict[str, str]] = {}
    for session in loaded["sessions"]:
        sid = session.get("sid") or "unknown"
        label = session.get("cwd") or tail(sid, 8)
        catalog[sid] = {"name": label, "model": session.get("model") or "unknown"}
    for event in loaded["events"]:
        sid = event.get("sid") or "unknown"
        catalog.setdefault(sid, {"name": tail(sid.split(":", 1)[-1], 8) or f"{client} session", "model": event.get("model") or "unknown"})
    return catalog


def load_json_events(cache_dir: Path, client: str, days: int, cache_path: Path | None) -> dict[str, Any] | None:
    json_path = default_usage_json_path(cache_dir)
    if not json_path.is_file():
        return None
    try:
        stat = json_path.stat()
    except OSError:
        return None
    cache_files = load_generic_cache(cache_path, days) if cache_path else {}
    cached = cache_files.get(str(json_path))
    if (
        isinstance(cached, dict)
        and cached.get("mtime_ns") == stat.st_mtime_ns
        and cached.get("size") == stat.st_size
        and isinstance(cached.get("events"), list)
    ):
        file_events = cached["events"]
    else:
        file_events = load_normalized_events_from_json(cache_dir, days)
        if cache_path:
            cache_files[str(json_path)] = {
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "events": file_events,
            }
            write_generic_cache(cache_path, days, cache_files)
    sessions: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    for event in file_events:
        sid = event.get("sid") or "unknown"
        if sid in seen_sessions:
            continue
        seen_sessions.add(sid)
        label = sid.split(":", 1)[-1]
        sessions.append({"sid": sid, "file": str(json_path), "cwd": label, "model": event.get("model") or "unknown"})
    return {
        "sessions": sessions,
        "events": file_events,
        "ttfb_events": [],
        "failure_events": [],
        "limits": None,
    }


def load_tokscale_usage(cache_dir: Path, client: str, days: int, cache_path: Path | None) -> dict[str, Any]:
    if client == "cursor":
        json_loaded = load_json_events(cache_dir, client, days, cache_path)
        now = datetime.now(timezone.utc)
        cutoff = cutoff_for_days(days, now)
        csv_loaded = load_csv_events(cache_dir, client, cutoff, cache_path, days)
        if json_loaded and json_loaded.get("events"):
            if not csv_loaded.get("events"):
                return json_loaded
            return merge_cursor_usage_sources(json_loaded, csv_loaded, client)
        return csv_loaded
    now = datetime.now(timezone.utc)
    cutoff = cutoff_for_days(days, now)
    return load_csv_events(cache_dir, client, cutoff, cache_path, days)


def merge_cursor_usage_sources(json_loaded: dict[str, Any], csv_loaded: dict[str, Any], client: str) -> dict[str, Any]:
    """Merge Cursor Dashboard JSON with exported CSV history, deduping matching events."""
    json_events = json_loaded.get("events") if isinstance(json_loaded.get("events"), list) else []
    csv_events = csv_loaded.get("events") if isinstance(csv_loaded.get("events"), list) else []
    merged_events: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for event in [*csv_events, *json_events]:
        key = cursor_event_identity(event)
        if key in seen:
            continue
        seen.add(key)
        merged_events.append(event)
    merged_events.sort(key=lambda item: int(item.get("ts") or 0))

    merged = {
        "sessions": build_session_catalog_from_events(
            [*as_list(csv_loaded.get("sessions")), *as_list(json_loaded.get("sessions"))],
            merged_events,
            client,
        ),
        "events": merged_events,
        "ttfb_events": [],
        "failure_events": [],
        "limits": csv_loaded.get("limits") or json_loaded.get("limits"),
    }
    return merged


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def cursor_event_identity(event: dict[str, Any]) -> tuple[Any, ...]:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    return (
        int(event.get("ts") or 0),
        normalize_model_name(str(event.get("model") or "unknown"), "cursor"),
        int(usage.get("input_tokens") or 0),
        int(usage.get("cached_input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
        int(usage.get("total_tokens") or 0),
    )


def build_session_catalog_from_events(
    source_sessions: list[Any],
    events: list[dict[str, Any]],
    client: str,
) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for session in source_sessions:
        if not isinstance(session, dict):
            continue
        sid = session.get("sid")
        if not isinstance(sid, str) or not sid or sid in seen:
            continue
        seen.add(sid)
        sessions.append(session)
    for event in events:
        sid = event.get("sid") or "unknown"
        if sid in seen:
            continue
        seen.add(sid)
        label = sid.split(":", 1)[-1]
        sessions.append({"sid": sid, "file": "merged", "cwd": label or f"{client} session", "model": event.get("model") or "unknown"})
    return sessions


def total_tokens(events: list[dict[str, Any]]) -> int:
    total = 0
    for event in events:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        value = usage.get("total_tokens")
        if isinstance(value, int) and not isinstance(value, bool):
            total += max(0, value)
    return total


def invalidate_parse_cache_entries(cache_path: Path | None, *file_paths: str | Path) -> None:
    """Remove stale parse-cache entries after CSV sync or file replacement."""
    if cache_path is None or not cache_path.exists() or not file_paths:
        return
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return
    files = payload.get("files")
    if not isinstance(files, dict):
        return
    keys = {str(path) for path in file_paths}
    changed = False
    for key in list(files.keys()):
        if key in keys:
            del files[key]
            changed = True
    if not changed:
        return
    write_generic_cache(cache_path, int(payload.get("window_days") or payload.get("windowDays") or 30), files)
