# -*- coding: utf-8 -*-
"""Shared usage aggregation utilities for AgentStatistics adapters."""
from __future__ import annotations

import bisect
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DAY_MS = 24 * 60 * 60 * 1000
HOUR_MS = 60 * 60 * 1000
CACHE_VERSION = 6
MIN_READABLE_CACHE_VERSION = 4


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


def tail(value: str, length: int) -> str:
    return value[-length:] if len(value) > length else value


def project_name(cwd: str, fallback: str) -> str:
    if not cwd:
        return fallback
    name = Path(cwd).name
    return name or fallback


def pricing_for_model(model: str, pricing_rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    key = (model or "").lower()
    for rule in pricing_rules:
        if any(pattern in key for pattern in rule["patterns"]):
            return rule
    return None


def price_usage(
    model: str,
    usage: dict[str, Any],
    pricing_rules: list[dict[str, Any]],
    separate_cache: bool = False,
) -> dict[str, float | int]:
    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    cached_raw = max(0, int(usage.get("cached_input_tokens") or 0))
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    reasoning_raw = max(0, int(usage.get("reasoning_output_tokens") or 0))
    if separate_cache:
        cached_tokens = cached_raw
        billable_input = input_tokens
    else:
        cached_tokens = min(cached_raw, input_tokens) if input_tokens > 0 else cached_raw
        billable_input = max(0, input_tokens - cached_tokens)
    visible_output = max(0, output_tokens)
    billed_reasoning = max(0, reasoning_raw)
    priced_tokens = billable_input + cached_tokens + visible_output + billed_reasoning
    rule = pricing_for_model(model, pricing_rules)
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


def price_event(
    event: dict[str, Any],
    pricing_rules: list[dict[str, Any]],
    separate_cache: bool = False,
) -> dict[str, float | int]:
    """Use official per-event cost when present, scaled into local cost parts."""
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    estimated = price_usage(event.get("model") or "unknown", usage, pricing_rules, separate_cache)
    raw_cost = event.get("cost")
    try:
        official_total = float(raw_cost)
    except (TypeError, ValueError):
        official_total = 0.0
    if official_total <= 0:
        return estimated

    estimated_total = float(estimated["total"])
    if estimated_total <= 0:
        token_parts = token_cost_part_weights(usage, separate_cache)
        token_total = sum(token_parts.values())
        if token_total > 0:
            return {
                "input": official_total * token_parts["input"] / token_total,
                "cached": official_total * token_parts["cached"] / token_total,
                "output": official_total * token_parts["output"] / token_total,
                "reasoning": official_total * token_parts["reasoning"] / token_total,
                "total": official_total,
                "pricedTokens": int(token_total),
                "unpricedTokens": 0,
            }
        return {
            "input": official_total,
            "cached": 0.0,
            "output": 0.0,
            "reasoning": 0.0,
            "total": official_total,
            "pricedTokens": int(usage.get("total_tokens") or 0),
            "unpricedTokens": 0,
        }

    scale = official_total / estimated_total
    return {
        "input": float(estimated["input"]) * scale,
        "cached": float(estimated["cached"]) * scale,
        "output": float(estimated["output"]) * scale,
        "reasoning": float(estimated["reasoning"]) * scale,
        "total": official_total,
        "pricedTokens": int(estimated["pricedTokens"]),
        "unpricedTokens": 0,
    }


def token_cost_part_weights(usage: dict[str, Any], separate_cache: bool = False) -> dict[str, int]:
    input_tokens = max(0, int(usage.get("input_tokens") or 0))
    cached_raw = max(0, int(usage.get("cached_input_tokens") or 0))
    output_tokens = max(0, int(usage.get("output_tokens") or 0))
    reasoning_tokens = max(0, int(usage.get("reasoning_output_tokens") or 0))
    cached_tokens = cached_raw if separate_cache else min(cached_raw, input_tokens) if input_tokens > 0 else cached_raw
    return {
        "input": input_tokens,
        "cached": cached_tokens,
        "output": output_tokens,
        "reasoning": reasoning_tokens,
    }


def choose_nice_step(duration_ms: int, target_buckets: int) -> int:
    steps_minutes = [1, 2, 5, 10, 15, 30, 60, 120, 240, 360, 720, 1440, 2880, 10080]
    for minutes in steps_minutes:
        step = minutes * 60 * 1000
        if math.ceil(duration_ms / step) <= target_buckets:
            return step
    return steps_minutes[-1] * 60 * 1000


def build_buckets(
    events: list[dict[str, Any]],
    start: int,
    end: int,
    step: int,
    pricing_rules: list[dict[str, Any]],
    separate_cache: bool = False,
) -> tuple[list[list[Any]], list[list[Any]]]:
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
        bucket["cost"] += float(price_event(event, pricing_rules, separate_cache)["total"])
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


def build_calendar_buckets(
    events: list[dict[str, Any]],
    granularity: str,
    pricing_rules: list[dict[str, Any]],
    separate_cache: bool = False,
) -> tuple[list[list[Any]], list[list[Any]]]:
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
        bucket["cost"] += float(price_event(event, pricing_rules, separate_cache)["total"])
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


def quota_risk_row(name: str, used: float | None, reset: str | None, tone: str, empty_note: str = "本地日志暂无 rate_limits") -> dict[str, Any]:
    if used is None:
        return {"name": name, "value": 0, "label": "等待数据", "percentLabel": "--", "note": empty_note, "tone": tone}
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


def build_codex_risk_rows(limits: dict[str, Any], cache_hit: float) -> list[dict[str, Any]]:
    primary = map_value(limits, "primary")
    secondary = map_value(limits, "secondary")
    primary_used = safe_percent(primary.get("used_percent"))
    secondary_used = safe_percent(secondary.get("used_percent"))
    return [
        quota_risk_row("5h 窗口", primary_used, reset_time_label(primary.get("resets_at")), "blue"),
        quota_risk_row("周限额", secondary_used, reset_time_label(secondary.get("resets_at")), "teal"),
        {"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"},
    ]


def build_view(
    key: str,
    label: str,
    start: int,
    end: int,
    loaded: dict[str, Any],
    session_catalog: dict[str, dict[str, str]],
    pricing_rules: list[dict[str, Any]],
    risk_rows: list[dict[str, Any]] | None = None,
    cache_hit_mode: str = "input",
) -> dict[str, Any]:
    separate_cache = cache_hit_mode == "input_plus_cached"
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
        cost = price_event(event, pricing_rules, separate_cache)
        for field in ("input", "cached", "output", "reasoning", "total"):
            costs[field] += float(cost[field])
        costs["unpricedTokens"] += int(cost["unpricedTokens"])
        catalog = session_catalog.get(event.get("sid") or "", {})
        session = by_session.setdefault(
            event.get("sid") or "unknown",
            {"name": catalog.get("name") or f"会话 {tail(event.get('sid') or 'unknown', 6)}", "model": model, "tokens": 0, "requests": 0, "status": "ok"},
        )
        session["tokens"] += int(usage.get("total_tokens") or 0)
        session["requests"] += 1
        row = by_model.setdefault(
            model,
            {"name": model, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}, "tokens": 0, "requests": 0, "cost": 0.0, "latencyTotal": 0, "latencyCount": 0},
        )
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
        row = by_model.setdefault(
            model,
            {"name": model, "usage": {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "reasoning_output_tokens": 0}, "tokens": 0, "requests": 0, "cost": 0.0, "latencyTotal": 0, "latencyCount": 0},
        )
        row["latencyTotal"] += int(event.get("ttfb_ms") or 0)
        row["latencyCount"] += 1
    calls = len(events)
    cache_denominator = totals["input_tokens"] + totals["cached_input_tokens"] if cache_hit_mode == "input_plus_cached" else totals["input_tokens"]
    cache_hit = (totals["cached_input_tokens"] / cache_denominator * 100) if cache_denominator else 0
    success_rate, failure_rate = success_failure_rates(calls, len(failures))
    duration = max(1, end - start)
    if key == "history":
        axis_granularity = choose_history_granularity(start, end, 120)
        trend, distribution = build_calendar_buckets(events, axis_granularity, pricing_rules, separate_cache)
        trend_step_minutes = None
    else:
        axis_granularity = choose_axis_granularity(duration)
        trend_step = choose_nice_step(duration, 180)
        trend, distribution = build_buckets(events, start, end, trend_step, pricing_rules, separate_cache)
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
    resolved_risk = risk_rows if risk_rows is not None else build_codex_risk_rows(limits, cache_hit)
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
        "risk": resolved_risk,
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


def cutoff_for_days(days: int, now: datetime) -> datetime:
    from datetime import timedelta

    if days <= 0:
        return datetime.min.replace(tzinfo=timezone.utc)
    return now - timedelta(days=days)


def cache_covers_days(cached_days: int, requested_days: int) -> bool:
    if requested_days <= 0:
        return cached_days <= 0
    if cached_days <= 0:
        return True
    return cached_days >= requested_days


def load_generic_cache(cache_path: Path, days: int) -> dict[str, Any]:
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


def write_generic_cache(cache_path: Path, days: int, files: dict[str, Any]) -> None:
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


def build_standard_payload(
    source: str,
    root: Path,
    days: int,
    loaded: dict[str, Any],
    session_catalog: dict[str, dict[str, str]],
    pricing_rules: list[dict[str, Any]],
    coverage: list[dict[str, str]],
    limits_meta: dict[str, Any] | None = None,
    risk_builder: Callable[[dict[str, Any], float], list[dict[str, Any]]] | None = None,
    cache_hit_mode: str = "input",
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
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

    def view_risk(loaded_data: dict[str, Any], cache_hit: float) -> list[dict[str, Any]]:
        if risk_builder is not None:
            return risk_builder(loaded_data, cache_hit)
        return build_codex_risk_rows(loaded_data.get("limits") or {}, cache_hit)

    views: dict[str, Any] = {}
    for key, start, end in view_ranges:
        events = in_range(loaded["events"], start, end)
        cache_hit = 0.0
        input_total = sum(int(e["usage"].get("input_tokens") or 0) for e in events)
        cached_total = sum(int(e["usage"].get("cached_input_tokens") or 0) for e in events)
        cache_denominator = input_total + cached_total if cache_hit_mode == "input_plus_cached" else input_total
        if cache_denominator:
            cache_hit = cached_total / cache_denominator * 100
        views[key] = build_view(
            key,
            format_range_label(start, end, key),
            start,
            end,
            loaded,
            session_catalog,
            pricing_rules,
            risk_rows=view_risk(loaded, cache_hit),
            cache_hit_mode=cache_hit_mode,
        )
    history = views["history"]

    model_catalog = sorted({row.get("model") or "unknown" for row in loaded["sessions"]} | {row.get("model") or "unknown" for row in loaded["events"]})
    session_catalog_rows = [[sid, data["name"], data["model"]] for sid, data in sorted(session_catalog.items())]
    records = [
        [
            event["ts"],
            event.get("sid") or "unknown",
            event.get("model") or "unknown",
            int(event["usage"].get("input_tokens") or 0),
            int(event["usage"].get("cached_input_tokens") or 0),
            int(event["usage"].get("output_tokens") or 0),
            int(event["usage"].get("reasoning_output_tokens") or 0),
            int(event["usage"].get("total_tokens") or 0),
            round(float(event.get("cost") or 0.0), 6),
        ]
        for event in loaded["events"]
    ]
    ttfb_records = [[event["ts"], event.get("sid") or "unknown", event.get("model") or "unknown", event.get("ttfb_ms") or 0] for event in loaded["ttfb_events"]]
    failure_records = [[event["ts"], event.get("sid") or "unknown", event.get("model") or "unknown"] for event in loaded["failure_events"]]
    limits = loaded.get("limits") or {}
    meta = limits_meta or {}
    return {
        "schemaVersion": 2,
        "source": source,
        "generatedAt": now.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "windowDays": days,
        "root": str(root),
        "pricingRules": pricing_rules,
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
        "limits": meta if meta else {"raw": limits},
        "coverage": coverage,
    }
