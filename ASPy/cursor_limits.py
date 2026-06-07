# -*- coding: utf-8 -*-
"""Cursor billing limits probe for risk panels."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cursor_auth import normalize_session_token
from cursor_cli_auth import read_cli_auth_bundle
from cursor_http import request_json, request_usage_for_user
from cursor_cli_api import probe_cli_limits
from usage_common import quota_risk_row, reset_time_label

LIMITS_CACHE_VERSION = 1
USAGE_SUMMARY_URL = "https://cursor.com/api/usage-summary"
AUTH_ME_URL = "https://cursor.com/api/auth/me"
REQUEST_USAGE_URL = "https://cursor.com/api/usage"


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


def has_any_number(*values: Any) -> bool:
    return any(isinstance(value, (int, float)) and not isinstance(value, bool) for value in values)


def parse_request_usage(request_usage: dict[str, Any] | None) -> dict[str, float | None]:
    usage = request_usage if isinstance(request_usage, dict) else {}
    gpt4 = usage.get("gpt-4") if isinstance(usage.get("gpt-4"), dict) else usage.get("gpt4")
    gpt4 = gpt4 if isinstance(gpt4, dict) else {}
    requests_used = number_or_none(gpt4.get("numRequestsTotal")) or number_or_none(gpt4.get("numRequests"))
    requests_limit = number_or_none(gpt4.get("maxRequestUsage"))
    return {"requestsUsed": requests_used, "requestsLimit": requests_limit}


def parse_usage_summary(summary: dict[str, Any], request_usage: dict[str, Any] | None = None) -> dict[str, Any]:
    individual = summary.get("individualUsage") if isinstance(summary.get("individualUsage"), dict) else {}
    plan = individual.get("plan") if isinstance(individual.get("plan"), dict) else {}
    on_demand = individual.get("onDemand") if isinstance(individual.get("onDemand"), dict) else {}
    overall = individual.get("overall") if isinstance(individual.get("overall"), dict) else {}
    team = summary.get("teamUsage") if isinstance(summary.get("teamUsage"), dict) else {}
    team_on_demand = team.get("onDemand") if isinstance(team.get("onDemand"), dict) else {}
    team_pooled = team.get("pooled") if isinstance(team.get("pooled"), dict) else {}

    plan_used = number_or_none(plan.get("used")) or 0
    plan_limit = number_or_none(plan.get("limit")) or 0
    overall_used = number_or_none(overall.get("used"))
    overall_limit = number_or_none(overall.get("limit"))
    overall_remaining = number_or_none(overall.get("remaining"))
    auto_percent = clamp_percent(plan.get("autoPercentUsed"))
    api_percent = clamp_percent(plan.get("apiPercentUsed"))
    on_demand_used = number_or_none(on_demand.get("used")) or 0
    on_demand_limit = number_or_none(on_demand.get("limit"))
    on_demand_remaining = number_or_none(on_demand.get("remaining"))
    team_on_demand_used = number_or_none(team_on_demand.get("used"))
    team_on_demand_limit = number_or_none(team_on_demand.get("limit"))
    team_on_demand_remaining = number_or_none(team_on_demand.get("remaining"))
    team_pooled_used = number_or_none(team_pooled.get("used"))
    team_pooled_limit = number_or_none(team_pooled.get("limit"))
    team_pooled_remaining = number_or_none(team_pooled.get("remaining"))

    plan_percent = clamp_percent(plan.get("totalPercentUsed"))
    if plan_percent is None:
        if auto_percent is not None and api_percent is not None:
            plan_percent = clamp_percent((auto_percent + api_percent) / 2)
        elif api_percent is not None:
            plan_percent = api_percent
        elif auto_percent is not None:
            plan_percent = auto_percent
        elif plan_limit > 0:
            plan_percent = percent_from_used_limit(plan_used, plan_limit)
        elif overall_limit is not None and overall_limit > 0:
            plan_percent = percent_from_used_limit(overall_used, overall_limit)
        elif team_pooled_limit is not None and team_pooled_limit > 0:
            plan_percent = percent_from_used_limit(team_pooled_used, team_pooled_limit)
        else:
            plan_percent = 0

    resolved_plan_used = plan_used
    resolved_plan_limit = plan_limit
    resolved_plan_remaining = number_or_none(plan.get("remaining"))
    if resolved_plan_limit <= 0 and resolved_plan_used <= 0:
        if overall_used is not None and overall_limit is not None:
            resolved_plan_used = overall_used
            resolved_plan_limit = overall_limit
            resolved_plan_remaining = overall_remaining
        elif team_pooled_used is not None and team_pooled_limit is not None:
            resolved_plan_used = team_pooled_used
            resolved_plan_limit = team_pooled_limit
            resolved_plan_remaining = team_pooled_remaining

    parsed_request_usage = parse_request_usage(request_usage)
    return {
        "planPercent": plan_percent,
        "autoPercent": auto_percent,
        "apiPercent": api_percent,
        "planUsedUsd": cents_to_usd(resolved_plan_used),
        "planLimitUsd": cents_to_usd(resolved_plan_limit),
        "planRemainingUsd": None if resolved_plan_remaining is None else cents_to_usd(resolved_plan_remaining),
        "onDemandPercent": percent_from_used_limit(on_demand_used, on_demand_limit),
        "onDemandUsedUsd": cents_to_usd(on_demand_used),
        "onDemandLimitUsd": None if on_demand_limit is None else cents_to_usd(on_demand_limit),
        "onDemandRemainingUsd": None if on_demand_remaining is None else cents_to_usd(on_demand_remaining),
        "teamOnDemandPercent": percent_from_used_limit(team_on_demand_used, team_on_demand_limit),
        "teamOnDemandUsedUsd": None if team_on_demand_used is None else cents_to_usd(team_on_demand_used),
        "teamOnDemandLimitUsd": None if team_on_demand_limit is None else cents_to_usd(team_on_demand_limit),
        "teamOnDemandRemainingUsd": None if team_on_demand_remaining is None else cents_to_usd(team_on_demand_remaining),
        "teamPooledPercent": percent_from_used_limit(team_pooled_used, team_pooled_limit),
        "teamPooledUsedUsd": None if team_pooled_used is None else cents_to_usd(team_pooled_used),
        "teamPooledLimitUsd": None if team_pooled_limit is None else cents_to_usd(team_pooled_limit),
        "teamPooledRemainingUsd": None if team_pooled_remaining is None else cents_to_usd(team_pooled_remaining),
        "billingCycleEnd": summary.get("billingCycleEnd") if isinstance(summary.get("billingCycleEnd"), str) else None,
        "membershipType": summary.get("membershipType") if isinstance(summary.get("membershipType"), str) else None,
        "limitType": summary.get("limitType") if isinstance(summary.get("limitType"), str) else None,
        "isUnlimited": bool(summary.get("isUnlimited")) if isinstance(summary.get("isUnlimited"), bool) else False,
        **parsed_request_usage,
    }


def default_limits_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "cursor_limits_cache.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_limits_cache(cache_path: Path | None) -> dict[str, Any] | None:
    path = cache_path or default_limits_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("version") or 0) != LIMITS_CACHE_VERSION:
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    return {
        "usage": usage,
        "cachedAt": payload.get("cachedAt") if isinstance(payload.get("cachedAt"), str) else None,
    }


def save_limits_cache(cache_path: Path | None, probe_result: dict[str, Any]) -> None:
    usage = probe_result.get("usage")
    if not isinstance(usage, dict):
        return
    path = cache_path or default_limits_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": LIMITS_CACHE_VERSION,
        "cachedAt": _iso_now(),
        "usage": usage,
    }
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _probe_live(session_token: str, *, include_request_usage: bool = False) -> dict[str, Any]:
    session_token = normalize_session_token(session_token) or session_token
    if not session_token:
        return {"ok": False, "usage": None, "error": "未检测到 Cursor 登录态"}
    summary_result = request_json(USAGE_SUMMARY_URL, session_token)
    if not summary_result.get("ok"):
        return {
            "ok": False,
            "usage": None,
            "error": summary_result.get("message") or "Cursor usage-summary 请求失败",
            "errorKind": summary_result.get("kind"),
        }
    summary = summary_result.get("json") if isinstance(summary_result.get("json"), dict) else {}
    request_usage = None
    user: dict[str, Any] = {}
    if include_request_usage:
        user_result = request_json(AUTH_ME_URL, session_token)
        user = user_result.get("json") if user_result.get("ok") and isinstance(user_result.get("json"), dict) else {}
        sub = user.get("sub") if isinstance(user.get("sub"), str) else None
        if sub:
            request_usage_result = request_usage_for_user(REQUEST_USAGE_URL, session_token, sub)
            if request_usage_result.get("ok") and isinstance(request_usage_result.get("json"), dict):
                request_usage = request_usage_result["json"]
    usage = parse_usage_summary(summary, request_usage)
    usage["email"] = user.get("email") if isinstance(user.get("email"), str) else None
    return {"ok": True, "usage": usage}


def _cache_age_seconds(cached_at: str | None) -> float | None:
    if not cached_at:
        return None
    try:
        normalized = cached_at.replace("Z", "+00:00")
        cached_time = datetime.fromisoformat(normalized)
        if cached_time.tzinfo is None:
            cached_time = cached_time.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - cached_time).total_seconds())
    except ValueError:
        return None


def _token_probe_order(session_token: str | None) -> list[str]:
    from cursor_discover import iter_session_token_candidates

    ordered: list[str] = []
    seen: set[str] = set()
    normalized = normalize_session_token(session_token) if session_token else None
    if normalized and normalized not in seen:
        seen.add(normalized)
        ordered.append(normalized)
    for candidate in iter_session_token_candidates():
        token = normalize_session_token(str(candidate.get("token") or ""))
        if token and token not in seen:
            seen.add(token)
            ordered.append(token)
    return ordered


def probe_cursor_limits(
    session_token: str | None = None,
    cache_path: Path | None = None,
    *,
    max_cache_age_sec: int = 0,
) -> dict[str, Any]:
    if max_cache_age_sec > 0:
        cached = load_limits_cache(cache_path)
        if cached:
            age = _cache_age_seconds(cached.get("cachedAt"))
            if age is not None and age <= max_cache_age_sec:
                usage = cached["usage"] if isinstance(cached.get("usage"), dict) else {}
                source_note = "Cursor CLI API（本地缓存）" if usage.get("source") == "cli-api" else "本地额度缓存"
                if usage.get("source") == "usage-summary":
                    source_note = "Cursor usage-summary（本地缓存）"
                return {
                    "ok": True,
                    "usage": usage,
                    "cached": True,
                    "cachedAt": cached.get("cachedAt"),
                    "cacheFresh": True,
                    "sourceNote": source_note,
                }

    tokens = _token_probe_order(session_token)
    probe_error = "未检测到 Cursor 登录态"
    probe_kind = None
    saw_vercel = False
    for token in tokens:
        live = _probe_live(token)
        if live.get("ok"):
            save_limits_cache(cache_path, live)
            return live
        probe_error = str(live.get("error") or probe_error)
        probe_kind = live.get("errorKind") or probe_kind
        if probe_kind == "vercel_checkpoint":
            saw_vercel = True

    cli_auth = read_cli_auth_bundle()
    access_token = str((cli_auth or {}).get("accessToken") or "").strip()
    if access_token:
        cli_live = probe_cli_limits(access_token)
        if cli_live.get("ok"):
            cli_live["usage"]["email"] = cli_auth.get("email")
            cli_live["usage"]["accountId"] = cli_auth.get("accountId")
            cli_live["usage"]["source"] = "cli-api"
            cli_live["cliApi"] = True
            save_limits_cache(cache_path, cli_live)
            if saw_vercel:
                cli_live["probeError"] = probe_error
                cli_live["probeErrorKind"] = "vercel_checkpoint"
            return cli_live
        probe_error = str(cli_live.get("error") or probe_error)
        probe_kind = cli_live.get("errorKind") or probe_kind

    cached = load_limits_cache(cache_path)
    if cached:
        return {
            "ok": True,
            "usage": cached["usage"],
            "cached": True,
            "cachedAt": cached.get("cachedAt"),
            "probeError": probe_error,
            "probeErrorKind": probe_kind,
        }
    return {"ok": False, "usage": None, "error": probe_error, "errorKind": probe_kind}


def _limits_source_note(limits_data: dict[str, Any] | None, fallback: str) -> str:
    if not limits_data or not limits_data.get("ok"):
        return fallback
    if isinstance(limits_data.get("sourceNote"), str) and limits_data["sourceNote"].strip():
        return limits_data["sourceNote"].strip()
    usage = limits_data.get("usage") if isinstance(limits_data.get("usage"), dict) else {}
    if limits_data.get("cacheFresh"):
        if usage.get("source") == "cli-api":
            return "Cursor CLI API（本地缓存）"
        return "本地额度缓存"
    if usage.get("source") == "cli-api" or limits_data.get("cliApi"):
        return "Cursor CLI API（api2.cursor.sh）"
    if limits_data.get("cached") and limits_data.get("probeError"):
        if usage.get("source") == "cli-api":
            return "Cursor CLI API（本地缓存）"
        error = str(limits_data.get("probeError") or "API 暂不可用")
        cached_at = limits_data.get("cachedAt")
        if cached_at:
            return f"缓存额度 · {error} · {cached_at}"
        return f"缓存额度 · {error}"
    if limits_data.get("cached"):
        if usage.get("source") == "cli-api":
            return "Cursor CLI API（本地缓存）"
        return "本地额度缓存"
    return fallback


def build_cursor_risk_rows(limits_data: dict[str, Any] | None, cache_hit: float) -> list[dict[str, Any]]:
    if not limits_data or not limits_data.get("ok"):
        note = (limits_data or {}).get("error") or (limits_data or {}).get("probeError") or "请配置 Cursor Session Token 或检查网络"
        return [
            quota_risk_row("套餐用量", None, None, "blue", note),
            quota_risk_row("Auto", None, None, "teal", "等待 Cursor 额度数据"),
            quota_risk_row("API", None, None, "amber", "等待 Cursor 额度数据"),
            {"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"},
        ]
    usage = limits_data.get("usage") or {}
    reset = reset_time_label(usage.get("billingCycleEnd"))
    source_note = _limits_source_note(limits_data, "Cursor usage-summary")
    rows = [
        quota_risk_row("套餐用量", usage.get("planPercent"), reset, "blue", source_note),
        quota_risk_row("Auto", usage.get("autoPercent"), reset, "teal", "Cursor autoPercentUsed"),
        quota_risk_row("API", usage.get("apiPercent"), reset, "amber", "Cursor apiPercentUsed"),
    ]
    if usage.get("onDemandPercent") is not None:
        rows.append(quota_risk_row("Credits", usage.get("onDemandPercent"), None, "purple", "Cursor on-demand"))
    rows.append({"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"})
    return rows
