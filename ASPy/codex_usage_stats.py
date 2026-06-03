# -*- coding: utf-8 -*-
"""Codex usage statistics adapter for AgentStatistics.

The parsing and aggregation rules intentionally mirror CodexScope's
generate_codex_data.go. Only usage metadata is retained; prompt text,
assistant text, tool output, and file contents are ignored.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")

CACHE_VERSION = 6
MIN_READABLE_CACHE_VERSION = 4
DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000


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


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).astimezone(timezone.utc)
    except ValueError:
        return None


def iso_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def unix_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


def from_unix_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, timezone.utc)


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


def cutoff_for_days(days: int, now: datetime) -> datetime:
    if days <= 0:
        return datetime.min.replace(tzinfo=timezone.utc)
    return now - timedelta(days=days)


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


def cache_covers_days(cached_days: int, requested_days: int) -> bool:
    if requested_days <= 0:
        return cached_days <= 0
    if cached_days <= 0:
        return True
    return cached_days >= requested_days


def load_cache(cache_path: Path, days: int) -> dict[str, Any]:
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if payload.get("version", 0) < MIN_READABLE_CACHE_VERSION or payload.get("version", 0) > CACHE_VERSION:
        return {}
    if not cache_covers_days(int(payload.get("window_days") or payload.get("windowDays") or 0), days):
        return {}
    files = payload.get("files")
    return files if isinstance(files, dict) else {}


def write_cache(cache_path: Path, days: int, files: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CACHE_VERSION, "window_days": days, "files": files}
    fd, tmp_name = tempfile.mkstemp(prefix=cache_path.name + ".", suffix=".tmp", dir=str(cache_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_name, cache_path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


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
    cache_files = load_cache(cache_path, days) if cache_path else {}
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
        write_cache(cache_path, days, next_cache)

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


def fmt_int(value: int) -> str:
    value = int(value or 0)
    if abs(value) >= 100_000_000:
        return f"{format_significant(value / 100_000_000)}亿"
    if abs(value) >= 10_000:
        return f"{format_significant(value / 10_000)}万"
    return f"{value:,}"


def format_significant(value: float, digits: int = 4) -> str:
    if value == 0:
        return "0.0"
    integer_digits = len(str(int(abs(value))))
    decimals = max(1, digits - integer_digits)
    return f"{value:.{decimals}f}"


def comma(value: int) -> str:
    return f"{int(value):,}"


def project_name(cwd: str, fallback: str) -> str:
    if not cwd:
        return fallback
    name = Path(cwd).name
    return name or fallback


def tail(value: str, length: int) -> str:
    return value[-length:] if len(value) > length else value


def pricing_for_model(model: str) -> dict[str, Any] | None:
    key = (model or "").lower()
    for rule in PRICING_RULES:
        if any(pattern in key for pattern in rule["patterns"]):
            return rule
    return None


def price_usage(model: str, usage: dict[str, Any]) -> dict[str, float | int]:
    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    cached_raw = max(0, int(usage.get("cached_input_tokens") or 0))
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    reasoning_raw = max(0, int(usage.get("reasoning_output_tokens") or 0))
    cached_tokens = min(cached_raw, input_tokens) if input_tokens > 0 else cached_raw
    billable_input = max(0, input_tokens - cached_tokens)
    visible_output = max(0, output_tokens)
    billed_reasoning = max(0, reasoning_raw)
    priced_tokens = billable_input + cached_tokens + visible_output + billed_reasoning
    rule = pricing_for_model(model)
    if not rule:
        return {"input": 0.0, "cached": 0.0, "output": 0.0, "reasoning": 0.0, "total": 0.0, "pricedTokens": 0, "unpricedTokens": priced_tokens}
    multiplier = 1 / 1_000_000
    input_cost = billable_input * float(rule["input"]) * multiplier
    cached_cost = cached_tokens * float(rule["cached"]) * multiplier
    output_cost = visible_output * float(rule["output"]) * multiplier
    reasoning_cost = billed_reasoning * float(rule["output"]) * multiplier
    return {
        "input": input_cost,
        "cached": cached_cost,
        "output": output_cost,
        "reasoning": reasoning_cost,
        "total": input_cost + cached_cost + output_cost + reasoning_cost,
        "pricedTokens": priced_tokens,
        "unpricedTokens": 0,
    }


def choose_nice_step(duration_ms: int, target_buckets: int) -> int:
    steps_minutes = [1, 2, 5, 10, 15, 30, 60, 120, 240, 360, 720, 1440, 2880, 10080]
    for minutes in steps_minutes:
        step = minutes * 60 * 1000
        if math.ceil(duration_ms / step) <= target_buckets:
            return step
    return steps_minutes[-1] * 60 * 1000


def build_buckets(events: list[dict[str, Any]], start: int, end: int, step: int) -> tuple[list[list[Any]], list[list[Any]]]:
    safe_step = max(60_000, int(step))
    bucket_start = math.floor(start / safe_step) * safe_step
    bucket_end = math.ceil(end / safe_step) * safe_step
    count = max(1, int((bucket_end - bucket_start) / safe_step))
    buckets = [{"ts": bucket_start + i * safe_step, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0}, "calls": 0, "cost": 0.0} for i in range(count)]
    for event in events:
        idx = int((event["ts"] - bucket_start) / safe_step)
        if idx < 0 or idx >= len(buckets):
            continue
        usage = event["usage"]
        bucket = buckets[idx]
        for key in bucket["usage"]:
            bucket["usage"][key] += int(usage.get(key) or 0)
        bucket["calls"] += 1
        bucket["cost"] += float(price_usage(event.get("model") or "unknown", usage)["total"])
    trend = [[b["ts"], b["usage"]["total_tokens"], b["usage"]["cached_input_tokens"], b["usage"]["output_tokens"], b["usage"]["input_tokens"], b["usage"]["reasoning_output_tokens"], b["calls"], round(b["cost"], 6)] for b in buckets]
    distribution = [[b["ts"], b["usage"]["total_tokens"], b["calls"], round(b["cost"], 6)] for b in buckets]
    return trend, distribution


def choose_history_granularity(start: int, end: int, target_buckets: int) -> str:
    duration = max(1, end - start)
    if math.ceil(duration / HOUR_MS) <= target_buckets:
        return "hour"
    if math.ceil(duration / DAY_MS) <= target_buckets:
        return "day"
    if month_span(start, end) <= target_buckets:
        return "month"
    return "year"


def choose_axis_granularity(duration: int) -> str:
    if duration <= DAY_MS:
        return "minute"
    if duration <= 3 * DAY_MS:
        return "hour"
    return "day"


def month_span(start: int, end: int) -> int:
    start_date = from_unix_ms(start).astimezone()
    end_date = from_unix_ms(end).astimezone()
    return max(1, (end_date.year - start_date.year) * 12 + end_date.month - start_date.month + 1)


def calendar_bucket_start(ts: int, granularity: str) -> int:
    date = from_unix_ms(ts).astimezone()
    if granularity == "year":
        bucket = date.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif granularity == "month":
        bucket = date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif granularity == "day":
        bucket = date.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        bucket = date.replace(minute=0, second=0, microsecond=0)
    return unix_ms(bucket)


def build_calendar_buckets(events: list[dict[str, Any]], granularity: str) -> tuple[list[list[Any]], list[list[Any]]]:
    buckets: dict[int, dict[str, Any]] = {}
    for event in events:
        ts = calendar_bucket_start(int(event["ts"]), granularity)
        bucket = buckets.setdefault(
            ts,
            {"ts": ts, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0}, "calls": 0, "cost": 0.0},
        )
        usage = event["usage"]
        for key in bucket["usage"]:
            bucket["usage"][key] += int(usage.get(key) or 0)
        bucket["calls"] += 1
        bucket["cost"] += float(price_usage(event.get("model") or "unknown", usage)["total"])
    rows = [buckets[key] for key in sorted(buckets)]
    trend = [[b["ts"], b["usage"]["total_tokens"], b["usage"]["cached_input_tokens"], b["usage"]["output_tokens"], b["usage"]["input_tokens"], b["usage"]["reasoning_output_tokens"], b["calls"], round(b["cost"], 6)] for b in rows]
    distribution = [[b["ts"], b["usage"]["total_tokens"], b["calls"], round(b["cost"], 6)] for b in rows]
    return trend, distribution


def in_range(rows: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    timestamps = [row["ts"] for row in rows]
    left = bisect.bisect_left(timestamps, start)
    right = bisect.bisect_right(timestamps, end)
    return rows[left:right]


def peak_rate(events: list[dict[str, Any]]) -> tuple[int, int | None]:
    window = 60_000
    total = 0
    left = 0
    peak_total = 0
    peak_ts: int | None = None
    for right, event in enumerate(events):
        total += int(event["usage"].get("total_tokens") or 0)
        while event["ts"] - events[left]["ts"] > window:
            total -= int(events[left]["usage"].get("total_tokens") or 0)
            left += 1
        if total > peak_total:
            peak_total = total
            peak_ts = event["ts"]
    return peak_total, peak_ts


def safe_percent(value: Any) -> float | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    return min(100.0, max(0.0, raw))


def map_value(obj: Any, key: str) -> dict[str, Any]:
    value = obj.get(key) if isinstance(obj, dict) else None
    return value if isinstance(value, dict) else {}


def reset_time_label(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), timezone.utc).astimezone().strftime("%H:%M")
    ts = parse_time(value)
    if ts is None:
        return None
    return ts.astimezone().strftime("%H:%M")


def quota_risk_row(name: str, used: float | None, reset: str | None, tone: str) -> dict[str, Any]:
    if used is None:
        return {"name": name, "value": 0, "label": "等待数据", "percentLabel": "--", "note": "本地日志暂无 rate_limits", "tone": tone}
    return {
        "name": name,
        "value": used,
        "label": f"已用 {used:.0f}%",
        "percentLabel": f"{used:.0f}%",
        "note": f"{reset} 重置" if reset else "等待重置时间",
        "tone": tone,
    }


def cost_parts(costs: dict[str, float]) -> list[dict[str, Any]]:
    rows = [
        ("input", "输入", costs["input"], "cost-input"),
        ("cached", "缓存", costs["cached"], "cost-cache"),
        ("output", "输出", costs["output"], "cost-output"),
        ("reasoning", "推理", costs["reasoning"], "cost-reasoning"),
    ]
    total = costs["total"]
    return [{"key": key, "name": name, "value": value, "className": cls, "percent": (value / total * 100 if total else 0)} for key, name, value, cls in rows]


def format_peak_label(ts: int | None, start: int, end: int) -> str:
    if ts is None:
        return "--"
    dt = from_unix_ms(ts).astimezone()
    if end - start > DAY_MS:
        return dt.strftime("%m/%d %H:%M")
    return dt.strftime("%H:%M")


def success_failure_rates(calls: int, failures: int) -> tuple[float, float]:
    if calls <= 0:
        return (0.0, 100.0) if failures > 0 else (100.0, 0.0)
    failure = min(100.0, max(0.0, failures / calls * 100))
    return 100.0 - failure, failure


def build_view(key: str, label: str, start: int, end: int, loaded: dict[str, Any], session_catalog: dict[str, dict[str, str]]) -> dict[str, Any]:
    events = in_range(loaded["events"], start, end)
    failures = in_range(loaded["failure_events"], start, end)
    ttfb = in_range(loaded["ttfb_events"], start, end)
    totals = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0}
    costs = {"input": 0.0, "cached": 0.0, "output": 0.0, "reasoning": 0.0, "total": 0.0, "unpricedTokens": 0}
    by_session: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    for event in events:
        usage = event["usage"]
        model = event.get("model") or "unknown"
        for field in totals:
            totals[field] += int(usage.get(field) or 0)
        cost = price_usage(model, usage)
        for field in ("input", "cached", "output", "reasoning", "total"):
            costs[field] += float(cost[field])
        costs["unpricedTokens"] += int(cost["unpricedTokens"])
        catalog = session_catalog.get(event.get("sid") or "", {})
        session = by_session.setdefault(event.get("sid") or "unknown", {"name": catalog.get("name") or f"会话 {tail(event.get('sid') or 'unknown', 6)}", "model": model, "tokens": 0, "requests": 0, "status": "ok"})
        session["tokens"] += int(usage.get("total_tokens") or 0)
        session["requests"] += 1
        row = by_model.setdefault(model, {"name": model, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}, "tokens": 0, "requests": 0, "cost": 0.0, "latencyTotal": 0, "latencyCount": 0})
        for field in row["usage"]:
            row["usage"][field] += int(usage.get(field) or 0)
        row["tokens"] += int(usage.get("total_tokens") or 0)
        row["requests"] += 1
        row["cost"] += float(cost["total"])
    for event in failures:
        session = by_session.get(event.get("sid") or "")
        if session:
            session["status"] = "warn"
    for event in ttfb:
        model = event.get("model") or "unknown"
        row = by_model.setdefault(model, {"name": model, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}, "tokens": 0, "requests": 0, "cost": 0.0, "latencyTotal": 0, "latencyCount": 0})
        row["latencyTotal"] += int(event.get("ttfb_ms") or 0)
        row["latencyCount"] += 1
    calls = len(events)
    cache_hit = (totals["cached_input_tokens"] / totals["input_tokens"] * 100) if totals["input_tokens"] else 0
    success_rate, failure_rate = success_failure_rates(calls, len(failures))
    duration = max(1, end - start)
    if key == "history":
        axis_granularity = choose_history_granularity(start, end, 120)
        trend, distribution = build_calendar_buckets(events, axis_granularity)
        trend_step_minutes = None
    else:
        axis_granularity = choose_axis_granularity(duration)
        trend_step = choose_nice_step(duration, 180)
        trend, distribution = build_buckets(events, start, end, trend_step)
        trend_step_minutes = trend_step / 60_000
    peak_total, peak_ts = peak_rate(events)
    sessions = sorted(by_session.values(), key=lambda row: row["tokens"], reverse=True)[:20]
    max_session_tokens = max([1] + [row["tokens"] for row in sessions])
    max_session_requests = max([1] + [row["requests"] for row in sessions])
    session_out = [
        {
            "rank": index + 1,
            "name": row["name"],
            "model": row["model"],
            "tokens": row["tokens"],
            "tokensLabel": fmt_int(row["tokens"]),
            "requests": row["requests"],
            "tokenPercent": round(row["tokens"] / max_session_tokens * 100),
            "requestPercent": round(row["requests"] / max_session_requests * 100),
            "status": row["status"],
        }
        for index, row in enumerate(sessions)
    ]
    models_all = sorted(by_model.values(), key=lambda row: row["tokens"], reverse=True)
    max_model_tokens = max([1] + [row["tokens"] for row in models_all])
    model_out = []
    for row in models_all[:12]:
        latency = row["latencyTotal"] / row["latencyCount"] / 1000 if row["latencyCount"] else 0
        model_out.append(
            {
                "name": row["name"],
                "tokens": row["tokens"],
                "tokensLabel": fmt_int(row["tokens"]),
                "requests": row["requests"],
                "input": row["usage"]["input_tokens"],
                "cached": row["usage"]["cached_input_tokens"],
                "output": row["usage"]["output_tokens"],
                "reasoning": row["usage"]["reasoning_output_tokens"],
                "latency": latency,
                "latencyLabel": f"{latency:.2f}s" if row["latencyCount"] else "--",
                "cost": row["cost"],
                "percent": round(row["tokens"] / max_model_tokens * 100),
            }
        )
    cost_models_all = sorted(models_all, key=lambda row: row["cost"], reverse=True)
    max_model_cost = max([1.0] + [row["cost"] for row in cost_models_all[:4]])
    cost_models = [{"name": row["name"], "rank": i + 1, "cost": row["cost"], "percent": round(row["cost"] / max_model_cost * 100)} for i, row in enumerate(cost_models_all[:4])]
    limits = loaded.get("limits") or {}
    primary = map_value(limits, "primary")
    secondary = map_value(limits, "secondary")
    primary_used = safe_percent(primary.get("used_percent"))
    secondary_used = safe_percent(secondary.get("used_percent"))
    return {
        "key": key,
        "label": label,
        "range": {"start": start, "end": end},
        "summary": {
            "totalTokens": totals["total_tokens"],
            "totalTokensLabel": fmt_int(totals["total_tokens"]),
            "inputTokens": totals["input_tokens"],
            "inputLabel": fmt_int(totals["input_tokens"]),
            "cachedTokens": totals["cached_input_tokens"],
            "cachedLabel": fmt_int(totals["cached_input_tokens"]),
            "outputTokens": totals["output_tokens"],
            "outputLabel": fmt_int(totals["output_tokens"]),
            "reasoningTokens": totals["reasoning_output_tokens"],
            "reasoningLabel": fmt_int(totals["reasoning_output_tokens"]),
            "requests": calls,
            "requestsLabel": comma(calls),
            "failures": len(failures),
            "successRate": success_rate,
            "successRateLabel": f"{success_rate:.1f}%",
            "failureRate": failure_rate,
            "cacheHit": cache_hit,
            "cacheHitLabel": f"{cache_hit:.1f}%",
            "peakTokens": peak_total,
            "peakLabel": fmt_int(peak_total),
            "peakTime": format_peak_label(peak_ts, start, end),
            "peakTpmLabel": f"{fmt_int(peak_total)} TPM",
        },
        "cost": {"total": costs["total"], "average": costs["total"] / calls if calls else 0, "rangeTokensLabel": fmt_int(totals["total_tokens"]), "parts": cost_parts(costs), "unpricedTokens": costs["unpricedTokens"]},
        "trend": trend,
        "trendStepMinutes": trend_step_minutes,
        "axisGranularity": axis_granularity,
        "distribution": distribution,
        "sessions": session_out,
        "models": model_out,
        "costModels": cost_models,
        "risk": [
            quota_risk_row("5h 窗口", primary_used, reset_time_label(primary.get("resets_at")), "blue"),
            quota_risk_row("周限额", secondary_used, reset_time_label(secondary.get("resets_at")), "teal"),
            {"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"},
        ],
    }


def local_day_start(dt: datetime) -> datetime:
    local = dt.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def format_range_label(start: int, end: int, key: str) -> str:
    if key == "24h":
        return "最近24小时"
    if key == "today":
        return "今天"
    if key == "7":
        return "7天内"
    if key == "30":
        return "30天内"
    if key == "history":
        return "历史总览"
    return f"{from_unix_ms(start).astimezone():%Y-%m-%d} 至 {from_unix_ms(end - 1).astimezone():%Y-%m-%d}"


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

    if loaded["events"]:
        available_start = loaded["events"][0]["ts"]
        available_end = loaded["events"][-1]["ts"]
    else:
        current = unix_ms(now)
        available_start = current
        available_end = current

    now_ms = unix_ms(now)
    today_start = unix_ms(local_day_start(now))
    view_ranges = [
        ("24h", now_ms - DAY_MS, now_ms),
        ("today", today_start, now_ms),
        ("7", now_ms - 7 * DAY_MS, now_ms),
        ("30", now_ms - 30 * DAY_MS, now_ms),
        ("history", available_start, max(available_end, now_ms)),
    ]
    views = {key: build_view(key, format_range_label(start, end, key), start, end, loaded, session_catalog) for key, start, end in view_ranges}
    history = views["history"]

    model_catalog = sorted({row.get("model") or "unknown" for row in loaded["sessions"]} | {row.get("model") or "unknown" for row in loaded["events"]})
    session_catalog_rows = [[sid, data["name"], data["model"]] for sid, data in sorted(session_catalog.items())]
    records = [[event["ts"], event.get("sid") or "unknown", event.get("model") or "unknown", int(event["usage"].get("input_tokens") or 0), int(event["usage"].get("cached_input_tokens") or 0), int(event["usage"].get("output_tokens") or 0), int(event["usage"].get("reasoning_output_tokens") or 0), int(event["usage"].get("total_tokens") or 0)] for event in loaded["events"]]
    ttfb_records = [[event["ts"], event.get("sid") or "unknown", event.get("model") or "unknown", event.get("ttfb_ms") or 0] for event in loaded["ttfb_events"]]
    failure_records = [[event["ts"], event.get("sid") or "unknown", event.get("model") or "unknown"] for event in loaded["failure_events"]]
    limits = loaded.get("limits") or {}
    payload = {
        "schemaVersion": 2,
        "source": "codex",
        "generatedAt": now.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "windowDays": days,
        "root": str(root),
        "pricingRules": PRICING_RULES,
        "availableRange": {"start": available_start, "end": available_end},
        "catalog": {"sessions": session_catalog_rows, "models": model_catalog},
        "sessionsCatalog": session_catalog,
        "records": records,
        "ttfbRecords": ttfb_records,
        "failureRecords": failure_records,
        "views": views,
        "summary": history["summary"],
        "cost": history["cost"],
        "trend": history["trend"],
        "distribution": history["distribution"],
        "sessions": history["sessions"],
        "models": history["models"],
        "costModels": history["costModels"],
        "risk": history["risk"],
        "limits": {
            "raw": limits,
            "planType": limits.get("plan_type"),
            "rateLimitReachedType": limits.get("rate_limit_reached_type"),
        },
        "coverage": [
            {"metric": "Token 消耗", "source": "Codex token_count", "status": "ok" if records else "empty"},
            {"metric": "真实额度", "source": "Codex rate_limits", "status": "ok" if limits else "empty"},
            {"metric": "会话排行", "source": "Codex session metadata", "status": "ok" if session_catalog_rows else "empty"},
            {"metric": "模型排行", "source": "Codex turn_context", "status": "ok" if model_catalog else "empty"},
        ],
    }
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
