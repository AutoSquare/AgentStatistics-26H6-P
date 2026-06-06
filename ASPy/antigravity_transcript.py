# -*- coding: utf-8 -*-
"""Parse Antigravity CLI brain transcript JSONL into unified usage events."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from antigravity_jsonl import parse_timestamp_ms, resolve_model_alias, to_safe_int
from antigravity_paths import antigravity_data_roots, transcript_log_globs
from antigravity_usage_fields import build_antigravity_usage
from usage_common import cutoff_for_days, load_generic_cache, unix_ms, write_generic_cache

USAGE_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "input": ("input", "inputTokens", "input_tokens", "prompt_token_count", "promptTokenCount"),
    "output": ("output", "outputTokens", "output_tokens", "candidates_token_count", "candidatesTokenCount"),
    "cacheRead": ("cacheRead", "cacheReadTokens", "cache_read_tokens", "cached_content_token_count", "cachedContentTokenCount"),
    "cacheWrite": ("cacheWrite", "cacheWriteTokens", "cache_write_tokens"),
    "reasoning": (
        "reasoning",
        "reasoningTokens",
        "reasoning_tokens",
        "thinkingOutputTokens",
        "thoughts_token_count",
        "thoughtsTokenCount",
    ),
}


def collect_transcript_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for root in antigravity_data_roots():
        brain_dir = root / "brain"
        if not brain_dir.is_dir():
            continue
        for session_dir in brain_dir.iterdir():
            if not session_dir.is_dir():
                continue
            session_id = session_dir.name.strip()
            if not session_id:
                continue
            logs_dir = session_dir / ".system_generated" / "logs"
            if not logs_dir.is_dir():
                continue
            for name in transcript_log_globs():
                path = logs_dir / name
                key = str(path)
                if path.is_file() and key not in seen:
                    seen.add(key)
                    files.append((path, session_id))
    return files


def pick_int(mapping: dict[str, Any], aliases: tuple[str, ...]) -> int:
    for key in aliases:
        if key in mapping:
            value = to_safe_int(mapping.get(key))
            if value > 0:
                return value
    return 0


def extract_usage_block(row: dict[str, Any]) -> dict[str, int] | None:
    candidates: list[dict[str, Any]] = []
    for key in ("usage", "usageMetadata", "usage_metadata", "tokenUsage", "token_usage"):
        value = row.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        for key in ("usage", "usageMetadata", "usage_metadata"):
            value = metadata.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    response = row.get("response")
    if isinstance(response, dict):
        for key in ("usage", "usageMetadata", "usage_metadata"):
            value = response.get(key)
            if isinstance(value, dict):
                candidates.append(value)
    for block in candidates:
        usage = {
            "input": pick_int(block, USAGE_FIELD_ALIASES["input"]),
            "output": pick_int(block, USAGE_FIELD_ALIASES["output"]),
            "cacheRead": pick_int(block, USAGE_FIELD_ALIASES["cacheRead"]),
            "cacheWrite": pick_int(block, USAGE_FIELD_ALIASES["cacheWrite"]),
            "reasoning": pick_int(block, USAGE_FIELD_ALIASES["reasoning"]),
        }
        if any(value > 0 for value in usage.values()):
            return usage
    return None


def extract_model(row: dict[str, Any], session_model: str | None) -> str:
    for key in ("modelId", "model", "model_id", "chatModel"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return resolve_model_alias(value)
        if isinstance(value, dict):
            nested = value.get("responseModel") or value.get("model")
            if isinstance(nested, str) and nested.strip():
                return resolve_model_alias(nested)
    return resolve_model_alias(session_model or "unknown")


def extract_timestamp(row: dict[str, Any]) -> int | None:
    for key in ("timestamp", "created_at", "createdAt", "time", "ts"):
        ts = parse_timestamp_ms(row.get(key))
        if ts is not None:
            return ts
    return None


def parse_transcript_text(text: str, session_id: str, cutoff_ms: int) -> list[dict[str, Any]]:
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
        if str(row.get("type") or "").upper() == "SESSION_META":
            session_model = extract_model(row, session_model)
            continue
        usage = extract_usage_block(row)
        if usage is None:
            continue
        timestamp = extract_timestamp(row)
        if timestamp is None or timestamp < cutoff_ms:
            continue
        model = extract_model(row, session_model)
        response_id = row.get("responseId") or row.get("response_id")
        if isinstance(response_id, str) and response_id.strip():
            dedup_key = f"{session_id}\0{response_id}"
            if dedup_key in seen_response_ids:
                continue
            seen_response_ids.add(dedup_key)
        events.append(
            {
                "ts": timestamp,
                "sid": f"antigravity:{session_id}",
                "model": model,
                "usage": build_antigravity_usage(
                    usage["input"],
                    usage["output"],
                    usage["cacheRead"],
                    usage["cacheWrite"],
                    usage["reasoning"],
                ),
                "cost": 0.0,
            }
        )
    events.sort(key=lambda item: item["ts"])
    return events


def merge_loaded_usage(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    events = list(primary.get("events") or [])
    seen_keys: set[tuple[Any, ...]] = set()
    for event in events:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        seen_keys.add(
            (
                event.get("sid"),
                event.get("ts"),
                event.get("model"),
                usage.get("input_tokens"),
                usage.get("output_tokens"),
            )
        )
    for event in secondary.get("events") or []:
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        key = (
            event.get("sid"),
            event.get("ts"),
            event.get("model"),
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        if key in seen_keys:
            continue
        seen_keys.add(key)
        events.append(event)
    events.sort(key=lambda item: item["ts"])
    sessions: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    for loaded in (primary, secondary):
        for session in loaded.get("sessions") or []:
            sid = session.get("sid") if isinstance(session, dict) else None
            if not isinstance(sid, str) or sid in seen_sessions:
                continue
            seen_sessions.add(sid)
            sessions.append(session)
    return {
        "sessions": sessions,
        "events": events,
        "ttfb_events": [],
        "failure_events": [],
        "limits": None,
        "manifestPath": primary.get("manifestPath"),
        "sessionFiles": int(primary.get("sessionFiles") or 0) + int(secondary.get("transcriptFiles") or 0),
        "transcriptFiles": int(secondary.get("transcriptFiles") or 0),
    }


def load_antigravity_transcript_usage(days: int, cache_path: Path | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = cutoff_for_days(days, now)
    cutoff_ms = 0 if days <= 0 else unix_ms(cutoff)
    files = collect_transcript_files()
    cache_files = load_generic_cache(cache_path, days) if cache_path else {}
    all_events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    seen_sessions: set[str] = set()
    next_cache: dict[str, Any] = {}

    for path, session_id in files:
        cache_key = f"transcript:{path}"
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = cache_files.get(cache_key)
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
            file_events = parse_transcript_text(text, session_id, cutoff_ms)
            next_cache[cache_key] = {"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "events": file_events}
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
        "transcriptFiles": len(files),
    }
