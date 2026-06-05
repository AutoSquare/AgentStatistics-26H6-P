# -*- coding: utf-8 -*-
"""Antigravity local quota probe via Connect RPC."""
from __future__ import annotations

from typing import Any

from antigravity_connect import CONNECT_SERVICE, connect_request, discover_connect_base, list_process_candidates_windows
from usage_common import quota_risk_row, reset_time_label

STATUS_PATH = f"{CONNECT_SERVICE}/GetUserStatus"


def parse_user_status(payload: dict[str, Any]) -> dict[str, Any]:
    user_status = payload.get("userStatus") if isinstance(payload.get("userStatus"), dict) else payload
    result: dict[str, Any] = {
        "email": user_status.get("email") if isinstance(user_status.get("email"), str) else None,
        "models": [],
        "promptCredits": None,
    }
    plan_status = user_status.get("planStatus") if isinstance(user_status.get("planStatus"), dict) else {}
    available = plan_status.get("availablePromptCredits")
    monthly = None
    plan_info = plan_status.get("planInfo") if isinstance(plan_status.get("planInfo"), dict) else {}
    monthly = plan_info.get("monthlyPromptCredits")
    if isinstance(available, (int, float)) and isinstance(monthly, (int, float)) and monthly > 0:
        used = max(0, monthly - available)
        result["promptCredits"] = {
            "available": available,
            "monthly": monthly,
            "usedPercentage": used / monthly * 100,
            "remainingPercentage": available / monthly * 100,
        }
    cascade = user_status.get("cascadeModelConfigData") if isinstance(user_status.get("cascadeModelConfigData"), dict) else {}
    configs = cascade.get("clientModelConfigs") if isinstance(cascade.get("clientModelConfigs"), list) else []
    for item in configs:
        if not isinstance(item, dict):
            continue
        model = item.get("modelOrAlias") if isinstance(item.get("modelOrAlias"), dict) else {}
        model_id = model.get("model") if isinstance(model.get("model"), str) else item.get("modelId")
        quota = item.get("quotaInfo") if isinstance(item.get("quotaInfo"), dict) else {}
        remaining_fraction = quota.get("remainingFraction")
        remaining_percent = None
        used_percent = None
        if isinstance(remaining_fraction, (int, float)):
            remaining_percent = max(0.0, min(1.0, float(remaining_fraction))) * 100
            used_percent = 100.0 - remaining_percent
        result["models"].append(
            {
                "label": item.get("label") if isinstance(item.get("label"), str) else str(model_id or "unknown"),
                "modelId": str(model_id or "unknown"),
                "remainingPercentage": remaining_percent,
                "usedPercentage": used_percent,
                "resetTime": quota.get("resetTime") if isinstance(quota.get("resetTime"), str) else None,
            }
        )
    return result


def probe_antigravity_quota() -> dict[str, Any]:
    processes = list_process_candidates_windows()
    if not processes:
        return {"ok": False, "error": "Antigravity CLI 未运行", "quota": None}
    for process in processes:
        base = discover_connect_base(process)
        if not base:
            continue
        payload = connect_request(
            base,
            STATUS_PATH,
            str(process["csrf_token"]),
            {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}},
        )
        if payload:
            return {"ok": True, "quota": parse_user_status(payload), "baseUrl": base}
    return {"ok": False, "error": "无法连接 Antigravity CLI 本机服务", "quota": None}


def build_antigravity_risk_rows(quota_data: dict[str, Any] | None, cache_hit: float) -> list[dict[str, Any]]:
    if not quota_data or not quota_data.get("ok"):
        note = (quota_data or {}).get("error") or "请运行 Antigravity CLI（agy）"
        return [
            quota_risk_row("提示额度", None, None, "blue", note),
            quota_risk_row("模型配额", None, None, "teal", note),
            {"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"},
        ]
    quota = quota_data.get("quota") or {}
    rows: list[dict[str, Any]] = []
    prompt = quota.get("promptCredits")
    if isinstance(prompt, dict):
        rows.append(
            quota_risk_row(
                "提示额度",
                prompt.get("usedPercentage"),
                None,
                "blue",
                f"剩余 {prompt.get('remainingPercentage', 0):.0f}%",
            )
        )
    models = quota.get("models") if isinstance(quota.get("models"), list) else []
    top_models = sorted(
        [m for m in models if isinstance(m, dict)],
        key=lambda item: float(item.get("usedPercentage") or 0),
        reverse=True,
    )[:2]
    if top_models:
        for model in top_models:
            rows.append(
                quota_risk_row(
                    str(model.get("label") or model.get("modelId") or "模型"),
                    model.get("usedPercentage"),
                    reset_time_label(model.get("resetTime")),
                    "teal",
                    "Antigravity Connect RPC",
                )
            )
    else:
        rows.append(quota_risk_row("模型配额", None, None, "teal", "Connect RPC 未返回模型配额"))
    rows.append({"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"})
    return rows
