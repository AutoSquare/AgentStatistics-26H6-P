# -*- coding: utf-8 -*-
"""Cursor dashboard usage JSON API and event normalization."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cursor_http import request_json_post
from usage_common import cutoff_for_days, parse_time, unix_ms

USAGE_EVENTS_URL = "https://cursor.com/api/dashboard/get-filtered-usage-events"
USAGE_JSON_VERSION = 1
USAGE_JSON_NAME = "usage.json"
DEFAULT_PAGE_SIZE = 500
MAX_PAGES = 200


def default_usage_json_path(cache_dir: Path) -> Path:
    return cache_dir / USAGE_JSON_NAME


def _epoch_seconds_from_numeric(raw: int) -> float | None:
    if raw <= 0:
        return None
    if raw >= 100_000_000_000:
        return raw / 1000.0
    if raw >= 1_000_000_000:
        return float(raw)
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = _epoch_seconds_from_numeric(int(value))
        if seconds is None:
            return None
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        seconds = _epoch_seconds_from_numeric(int(text))
        if seconds is not None:
            try:
                return datetime.fromtimestamp(seconds, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return None
    return parse_time(text)


def _parse_cost_usd(event: dict[str, Any]) -> float:
    charged = event.get("chargedCents")
    if isinstance(charged, (int, float)) and not isinstance(charged, bool):
        return max(0.0, float(charged) / 100.0)
    usage_cost = event.get("usageBasedCosts")
    if isinstance(usage_cost, (int, float)) and not isinstance(usage_cost, bool):
        return max(0.0, float(usage_cost))
    if isinstance(usage_cost, str):
        text = usage_cost.strip().replace("$", "").replace(",", "")
        try:
            return max(0.0, float(text))
        except ValueError:
            return 0.0
    token_usage = event.get("tokenUsage") if isinstance(event.get("tokenUsage"), dict) else {}
    total_cents = token_usage.get("totalCents")
    if isinstance(total_cents, (int, float)) and not isinstance(total_cents, bool):
        return max(0.0, float(total_cents) / 100.0)
    return 0.0


def _parse_int(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    text = str(value).strip().replace(",", "")
    if not text or text == "-":
        return 0
    try:
        return max(0, int(float(text)))
    except ValueError:
        return 0


def normalize_model_name(model: str) -> str:
    raw = str(model or "").strip()
    if not raw:
        return "unknown"
    if raw.lower() == "auto":
        return "cursor-auto"
    return raw


def normalize_usage_event(raw: dict[str, Any], *, client: str = "cursor") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    ts = _parse_timestamp(raw.get("timestamp") or raw.get("createdAt") or raw.get("date"))
    if ts is None:
        return None

    token_usage = raw.get("tokenUsage") if isinstance(raw.get("tokenUsage"), dict) else {}
    cache_read = _parse_int(token_usage.get("cacheReadTokens") or token_usage.get("cacheRead"))
    cache_write = _parse_int(token_usage.get("cacheWriteTokens") or token_usage.get("cacheWrite"))
    uncached_input_tokens = _parse_int(token_usage.get("inputTokens") or token_usage.get("input"))
    output_tokens = _parse_int(token_usage.get("outputTokens") or token_usage.get("output"))
    if uncached_input_tokens <= 0:
        uncached_input_tokens = _parse_int(raw.get("inputTokens"))
    if output_tokens <= 0:
        output_tokens = _parse_int(raw.get("outputTokens"))

    cached_input_tokens = cache_read + cache_write
    input_tokens = uncached_input_tokens + cached_input_tokens
    total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        total_tokens = _parse_int(raw.get("totalTokens"))

    model = normalize_model_name(str(raw.get("model") or "unknown"))
    sid_parts: list[str] = []
    for key in ("sessionId", "cloudAgentId", "automationId", "kind"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            sid_parts.append(value.strip())
    if sid_parts:
        sid = f"{client}:" + ":".join(sid_parts)
    else:
        digest = hashlib.sha256(f"{client}:{ts.isoformat()}:{model}:{total_tokens}".encode("utf-8")).hexdigest()[:10]
        sid = f"{client}:session-{digest}"

    return {
        "ts": unix_ms(ts),
        "sid": sid,
        "model": model,
        "usage": {
            "input_tokens": input_tokens,
            "cached_input_tokens": min(cached_input_tokens, input_tokens),
            "output_tokens": output_tokens,
            "reasoning_output_tokens": 0,
            "total_tokens": total_tokens,
        },
        "cost": _parse_cost_usd(raw),
    }


def extract_usage_events(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("usageEventsDisplay", "usageEvents", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _date_range_ms(days: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = cutoff_for_days(days, now)
    end = now + timedelta(minutes=1)
    return str(int(start.timestamp() * 1000)), str(int(end.timestamp() * 1000))


def fetch_usage_events_page(
    session_token: str,
    *,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    days: int = 30,
    timeout: int = 30,
) -> dict[str, Any]:
    start_ms, end_ms = _date_range_ms(days)
    body = {
        "page": page,
        "pageSize": page_size,
        "startDate": start_ms,
        "endDate": end_ms,
    }
    result = request_json_post(USAGE_EVENTS_URL, session_token, body, timeout=timeout)
    if not result.get("ok"):
        return result
    payload = result.get("json")
    if not isinstance(payload, dict):
        return {"ok": False, "kind": "parse", "message": "Cursor 用量 JSON 响应不是对象。"}
    return {"ok": True, "json": payload}


def fetch_all_usage_events(
    session_token: str,
    *,
    days: int = 30,
    page_size: int = DEFAULT_PAGE_SIZE,
    timeout: int = 30,
) -> dict[str, Any]:
    all_events: list[dict[str, Any]] = []
    total_count: int | None = None
    page = 1
    while page <= MAX_PAGES:
        page_result = fetch_usage_events_page(
            session_token,
            page=page,
            page_size=page_size,
            days=days,
            timeout=timeout,
        )
        if not page_result.get("ok"):
            if all_events:
                return {
                    "ok": True,
                    "events": all_events,
                    "totalUsageEventsCount": total_count or len(all_events),
                    "partial": True,
                    "warning": page_result.get("message"),
                }
            return page_result
        payload = page_result["json"]
        batch = extract_usage_events(payload)
        if total_count is None:
            raw_total = payload.get("totalUsageEventsCount")
            if isinstance(raw_total, (int, float)) and not isinstance(raw_total, bool):
                total_count = int(raw_total)
            elif isinstance(raw_total, str) and raw_total.isdigit():
                total_count = int(raw_total)
        all_events.extend(batch)
        if not batch:
            break
        if total_count is not None and len(all_events) >= total_count:
            break
        if len(batch) < page_size:
            break
        page += 1
    return {
        "ok": True,
        "events": all_events,
        "totalUsageEventsCount": total_count if total_count is not None else len(all_events),
        "partial": False,
    }


def build_usage_json_document(events: list[dict[str, Any]], *, source: str = "dashboard-json") -> dict[str, Any]:
    return {
        "version": USAGE_JSON_VERSION,
        "source": source,
        "syncedAt": datetime.now(timezone.utc).isoformat(),
        "totalEvents": len(events),
        "events": events,
    }


def write_usage_json(cache_dir: Path, document: dict[str, Any]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    target = default_usage_json_path(cache_dir)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)
    return target


def read_usage_json_document(cache_dir: Path) -> dict[str, Any] | None:
    path = default_usage_json_path(cache_dir)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_normalized_events_from_json(cache_dir: Path, days: int) -> list[dict[str, Any]]:
    document = read_usage_json_document(cache_dir)
    if not document:
        return []
    raw_events = document.get("events")
    if not isinstance(raw_events, list):
        return []
    cutoff = cutoff_for_days(days, datetime.now(timezone.utc))
    cutoff_ms = unix_ms(cutoff)
    normalized: list[dict[str, Any]] = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            continue
        event = normalize_usage_event(raw)
        if event is None or int(event.get("ts") or 0) < cutoff_ms:
            continue
        normalized.append(event)
    normalized.sort(key=lambda item: item["ts"])
    return normalized


def usage_json_is_fresh(cache_dir: Path, max_age_sec: int = 300) -> bool:
    path = default_usage_json_path(cache_dir)
    if not path.is_file():
        return False
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return False
    return age >= 0 and age < max_age_sec
