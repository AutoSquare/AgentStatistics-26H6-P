# -*- coding: utf-8 -*-
"""Cursor billing limits probe for risk panels."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from cursor_auth import normalize_session_token
from usage_common import quota_risk_row, reset_time_label

USAGE_SUMMARY_URL = "https://cursor.com/api/usage-summary"
AUTH_ME_URL = "https://cursor.com/api/auth/me"
REQUEST_USAGE_URL = "https://cursor.com/api/usage"
DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.cursor.com/settings",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def clamp_percent(value: Any) -> float | None:
    try:
        raw = float(value)
    except (TypeError, ValueError):
        return None
    return min(100.0, max(0.0, raw))


def number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def cents_to_usd(value: float) -> float:
    return round(value) / 100


def percent_from_used_limit(used: float | None, limit: float | None) -> float | None:
    if used is None or limit is None or limit <= 0:
        return None
    return clamp_percent(used / limit * 100)


def request_json(url: str, session_token: str, timeout: int = 15) -> dict[str, Any] | None:
    request = urllib.request.Request(
        url,
        headers={**DEFAULT_HEADERS, "Cookie": f"WorkosCursorSessionToken={session_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, TimeoutError):
        return None


def parse_usage_summary(summary: dict[str, Any], request_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    individual = summary.get("individualUsage") if isinstance(summary.get("individualUsage"), dict) else {}
    plan = individual.get("plan") if isinstance(individual.get("plan"), dict) else {}
    on_demand = individual.get("onDemand") if isinstance(individual.get("onDemand"), dict) else {}
    auto_percent = clamp_percent(plan.get("autoPercentUsed"))
    api_percent = clamp_percent(plan.get("apiPercentUsed"))
    plan_percent = clamp_percent(plan.get("totalPercentUsed"))
    plan_used = number_or_none(plan.get("used")) or 0
    plan_limit = number_or_none(plan.get("limit")) or 0
    if plan_percent is None:
        if auto_percent is not None and api_percent is not None:
            plan_percent = clamp_percent((auto_percent + api_percent) / 2)
        elif api_percent is not None:
            plan_percent = api_percent
        elif auto_percent is not None:
            plan_percent = auto_percent
        elif plan_limit > 0:
            plan_percent = percent_from_used_limit(plan_used, plan_limit)
        else:
            plan_percent = 0

    requests_used = None
    requests_limit = None
    if isinstance(request_usage, dict):
        gpt4 = request_usage.get("gpt-4") if isinstance(request_usage.get("gpt-4"), dict) else request_usage.get("gpt4")
        if isinstance(gpt4, dict):
            requests_used = number_or_none(gpt4.get("numRequestsTotal")) or number_or_none(gpt4.get("numRequests"))
            requests_limit = number_or_none(gpt4.get("maxRequestUsage"))

    return {
        "planPercent": plan_percent,
        "autoPercent": auto_percent,
        "apiPercent": api_percent,
        "onDemandPercent": percent_from_used_limit(number_or_none(on_demand.get("used")), number_or_none(on_demand.get("limit"))),
        "requestsUsed": requests_used,
        "requestsLimit": requests_limit,
        "billingCycleEnd": summary.get("billingCycleEnd") if isinstance(summary.get("billingCycleEnd"), str) else None,
        "membershipType": summary.get("membershipType") if isinstance(summary.get("membershipType"), str) else None,
        "planUsedUsd": cents_to_usd(plan_used),
        "planLimitUsd": cents_to_usd(plan_limit),
    }


def probe_cursor_limits(session_token: str) -> dict[str, Any]:
    session_token = normalize_session_token(session_token) or session_token
    summary = request_json(USAGE_SUMMARY_URL, session_token)
    if not summary:
        return {"ok": False, "usage": None}
    user = request_json(AUTH_ME_URL, session_token) or {}
    request_usage = None
    sub = user.get("sub") if isinstance(user.get("sub"), str) else None
    if sub:
        request_usage = request_json(f"{REQUEST_USAGE_URL}?user={sub}", session_token)
    usage = parse_usage_summary(summary, request_usage)
    usage["email"] = user.get("email") if isinstance(user.get("email"), str) else None
    return {"ok": True, "usage": usage}


def build_cursor_risk_rows(limits_data: dict[str, Any] | None, cache_hit: float) -> list[dict[str, Any]]:
    if not limits_data or not limits_data.get("ok"):
        return [
            quota_risk_row("套餐用量", None, None, "blue", "请配置 Cursor Session Token 或检查网络"),
            quota_risk_row("Auto", None, None, "teal", "等待 Cursor 额度数据"),
            quota_risk_row("API", None, None, "amber", "等待 Cursor 额度数据"),
            {"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"},
        ]
    usage = limits_data.get("usage") or {}
    reset = reset_time_label(usage.get("billingCycleEnd"))
    rows = [
        quota_risk_row("套餐用量", usage.get("planPercent"), reset, "blue", "Cursor usage-summary"),
        quota_risk_row("Auto", usage.get("autoPercent"), reset, "teal", "Cursor autoPercentUsed"),
        quota_risk_row("API", usage.get("apiPercent"), reset, "amber", "Cursor apiPercentUsed"),
    ]
    if usage.get("onDemandPercent") is not None:
        rows.append(quota_risk_row("Credits", usage.get("onDemandPercent"), None, "purple", "Cursor on-demand"))
    rows.append({"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"})
    return rows
