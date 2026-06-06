# -*- coding: utf-8 -*-
"""Cursor IDE backend API (api2.cursor.sh) using Bearer accessToken from state.vscdb."""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Any

IDE_API_BASE = "https://api2.cursor.sh"
IDE_USAGE_PATH = "/auth/usage"
IDE_PROFILE_PATH = "/auth/full_stripe_profile"
IDE_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}


def request_ide_json(path: str, access_token: str, timeout: int = 15) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{IDE_API_BASE}{path}",
        headers={**IDE_HEADERS, "Authorization": f"Bearer {access_token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict):
                return {"ok": True, "json": payload}
            return {"ok": False, "kind": "parse", "message": "Cursor IDE API 响应不是 JSON 对象。"}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in (401, 403):
            return {"ok": False, "kind": "unauthorized", "message": f"Cursor IDE API 未授权（HTTP {exc.code}）。"}
        return {"ok": False, "kind": "network", "message": f"Cursor IDE API 请求失败（HTTP {exc.code}）：{body}"}
    except (urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return {"ok": False, "kind": "timeout", "message": f"Cursor IDE API 请求超时（>{timeout}s）。"}
        return {"ok": False, "kind": "network", "message": f"Cursor IDE API 网络错误：{reason}"}


def number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_ide_limits(usage_payload: dict[str, Any], profile_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    gpt4 = usage_payload.get("gpt-4") if isinstance(usage_payload.get("gpt-4"), dict) else {}
    requests_used = number_or_none(gpt4.get("numRequestsTotal")) or number_or_none(gpt4.get("numRequests"))
    requests_limit = number_or_none(gpt4.get("maxRequestUsage"))
    plan_percent = None
    if requests_used is not None and requests_limit is not None and requests_limit > 0:
        plan_percent = min(100.0, max(0.0, requests_used / requests_limit * 100))

    profile = profile_payload if isinstance(profile_payload, dict) else {}
    membership = profile.get("individualMembershipType") or profile.get("membershipType")
    if not isinstance(membership, str):
        membership = None

    billing_cycle_end = usage_payload.get("startOfMonth") if isinstance(usage_payload.get("startOfMonth"), str) else None
    return {
        "planPercent": plan_percent,
        "autoPercent": None,
        "apiPercent": None,
        "requestsUsed": requests_used,
        "requestsLimit": requests_limit,
        "billingCycleEnd": billing_cycle_end,
        "membershipType": membership,
        "source": "ide-api",
    }


def probe_ide_limits(access_token: str) -> dict[str, Any]:
    token = access_token.strip()
    if not token:
        return {"ok": False, "usage": None, "error": "未检测到 Cursor IDE accessToken"}
    usage_result = request_ide_json(IDE_USAGE_PATH, token)
    if not usage_result.get("ok"):
        return {"ok": False, "usage": None, "error": usage_result.get("message"), "errorKind": usage_result.get("kind")}
    profile_result = request_ide_json(IDE_PROFILE_PATH, token)
    profile_payload = profile_result.get("json") if profile_result.get("ok") else None
    usage = parse_ide_limits(usage_result["json"], profile_payload if isinstance(profile_payload, dict) else None)
    return {"ok": True, "usage": usage, "ideApi": True}
