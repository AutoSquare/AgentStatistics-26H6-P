# -*- coding: utf-8 -*-
"""Parse tokscale Antigravity sessions/*.jsonl cache into unified usage events."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from antigravity_sync import load_manifest, manifest_path, sessions_dir
from usage_common import cutoff_for_days, load_generic_cache, tail, unix_ms, write_generic_cache

MODEL_ALIAS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^model_placeholder_m26$", re.I), "claude-opus-4-6"),
    (re.compile(r"^model_placeholder_m84$", re.I), "model_placeholder_m84"),
]


def resolve_model_alias(model_id: str) -> str:
    raw = str(model_id or "").strip() or "unknown"
    for pattern, alias in MODEL_ALIAS_PATTERNS:
        if pattern.fullmatch(raw):
            return alias
    return raw.replace("_", "-").lower() if raw.upper().startswith("MODEL_PLACEHOLDER_") else raw


def collect_session_files(cache_dir: Path) -> list[Path]:
    if not cache_dir.exists():
        return []
    manifest = load_manifest(cache_dir)
    files: list[Path] = []
    seen: set[str] = set()
    for session in manifest.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        relative = session.get("artifactPath")
        if not isinstance(relative, str) or not relative:
            continue
        if relative in seen:
            continue
        seen.add(relative)
        path = cache_dir / relative
        if path.is_file():
            files.append(path)
    sessions_root = sessions_dir(cache_dir)
    if sessions_root.is_dir():
        for path in sorted(sessions_root.glob("*.jsonl")):
            key = str(path)
            if key not in seen:
                files.append(path)
    return files


def parse_timestamp_ms(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts if ts > 0 else None
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.isdigit():
            ts = int(text)
            return ts if ts > 0 else None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def to_safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.strip():
        try:
            return max(0, int(float(value.strip())))
        except ValueError:
            return 0
    return 0


def parse_jsonl_text(text: str, cutoff_ms: int) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    session_model: str | None = None
    seen_response_ids: set[str] = set()
    for line in text.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        try:
            row = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        row_type = str(row.get("type") or "")
        if row_type == "session_meta":
            model_id = row.get("modelId")
            if isinstance(model_id, str) and model_id.strip():
                session_model = resolve_model_alias(model_id)
            continue
        if row_type != "usage":
            continue
        session_id = row.get("sessionId")
        if not isinstance(session_id, str) or not session_id.strip():
            continue
        timestamp = parse_timestamp_ms(row.get("timestamp"))
        if timestamp is None or timestamp < cutoff_ms:
            continue
        model_id = row.get("modelId")
        model = resolve_model_alias(model_id if isinstance(model_id, str) else (session_model or "unknown"))
        input_tokens = to_safe_int(row.get("input"))
        output_tokens = to_safe_int(row.get("output"))
        cache_read = to_safe_int(row.get("cacheRead"))
        cache_write = to_safe_int(row.get("cacheWrite"))
        reasoning = to_safe_int(row.get("reasoning"))
        if input_tokens == 0 and output_tokens == 0 and cache_read == 0 and cache_write == 0 and reasoning == 0:
            continue
        response_id = row.get("responseId")
        if isinstance(response_id, str) and response_id.strip():
            dedup_key = f"{session_id}\0{response_id}"
            if dedup_key in seen_response_ids:
                continue
            seen_response_ids.add(dedup_key)
        total_tokens = input_tokens + output_tokens + reasoning
        events.append(
            {
                "ts": timestamp,
                "sid": f"antigravity:{session_id}",
                "model": model,
                "usage": {
                    "input_tokens": input_tokens,
                    "cached_input_tokens": cache_read + cache_write,
                    "output_tokens": output_tokens,
                    "reasoning_output_tokens": reasoning,
                    "total_tokens": total_tokens,
                },
                "cost": 0.0,
            }
        )
    events.sort(key=lambda item: item["ts"])
    return events


def load_antigravity_usage(cache_dir: Path, days: int, cache_path: Path | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = cutoff_for_days(days, now)
    cutoff_ms = 0 if days <= 0 else unix_ms(cutoff)
    files = collect_session_files(cache_dir)
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
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            file_events = parse_jsonl_text(text, cutoff_ms)
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
        merged_cache = dict(cache_files)
        merged_cache.update(next_cache)
        write_generic_cache(cache_path, days, merged_cache)

    all_events.sort(key=lambda item: item["ts"])
    return {
        "sessions": sessions,
        "events": all_events,
        "ttfb_events": [],
        "failure_events": [],
        "limits": None,
        "manifestPath": str(manifest_path(cache_dir)),
        "sessionFiles": len(files),
    }


def invalidate_parse_cache_entries(cache_path: Path | None) -> None:
    if cache_path is None or not cache_path.exists():
        return
    try:
        cache_path.unlink()
    except OSError:
        pass
