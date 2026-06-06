# -*- coding: utf-8 -*-
"""Antigravity local quota probe via Connect RPC."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from antigravity_connect import AntigravityConnection, detect_connections, rpc_request
from usage_common import quota_risk_row, reset_time_label

QUOTA_CACHE_VERSION = 1

USER_STATUS_BODY = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}
MODEL_BLACKLIST = {
    "MODEL_CHAT_20706",
    "MODEL_CHAT_23310",
    "MODEL_GOOGLE_GEMINI_2_5_FLASH",
    "MODEL_GOOGLE_GEMINI_2_5_FLASH_THINKING",
    "MODEL_GOOGLE_GEMINI_2_5_FLASH_LITE",
    "MODEL_GOOGLE_GEMINI_2_5_PRO",
    "MODEL_PLACEHOLDER_M19",
    "MODEL_PLACEHOLDER_M9",
    "MODEL_PLACEHOLDER_M12",
}
POOL_ORDER = ("Gemini Pro", "Gemini Flash", "Claude")


def pool_for_model(label: str | None, model_id: str | None) -> str:
    text = f"{label or ''} {model_id or ''}".lower()
    if "gemini" in text and "pro" in text:
        return "Gemini Pro"
    if "gemini" in text and "flash" in text:
        return "Gemini Flash"
    return "Claude"


def parse_reset_time(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        millis = int(value if value > 20_000_000_000 else value * 1000)
        from datetime import datetime, timezone

        return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value).strip()
    return text or None


def models_from_configs(configs: list[Any]) -> list[dict[str, Any]]:
    models: list[dict[str, Any]] = []
    for item in configs:
        if not isinstance(item, dict):
            continue
        model = item.get("modelOrAlias") if isinstance(item.get("modelOrAlias"), dict) else {}
        model_id = model.get("model") if isinstance(model.get("model"), str) else item.get("modelId")
        if not isinstance(model_id, str) or not model_id or model_id in MODEL_BLACKLIST:
            continue
        quota = item.get("quotaInfo") if isinstance(item.get("quotaInfo"), dict) else {}
        remaining_fraction = quota.get("remainingFraction")
        if not isinstance(remaining_fraction, (int, float)):
            continue
        label = item.get("label") if isinstance(item.get("label"), str) and item.get("label").strip() else model_id
        remaining_percent = max(0.0, min(1.0, float(remaining_fraction))) * 100
        models.append(
            {
                "label": label,
                "modelId": model_id,
                "remainingPercentage": remaining_percent,
                "usedPercentage": 100.0 - remaining_percent,
                "resetTime": parse_reset_time(quota.get("resetTime")),
            }
        )
    return models


def collapse_model_pools(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pools: dict[str, dict[str, Any]] = {}
    for model in models:
        pool_name = pool_for_model(model.get("label"), model.get("modelId"))
        remaining = float(model.get("remainingPercentage") or 0)
        reset_time = model.get("resetTime")
        existing = pools.get(pool_name)
        if existing is None or remaining < float(existing.get("remainingPercentage") or 0):
            pools[pool_name] = {
                "label": pool_name,
                "modelId": pool_name,
                "remainingPercentage": remaining,
                "usedPercentage": 100.0 - remaining,
                "resetTime": reset_time,
            }
        elif remaining == float(existing.get("remainingPercentage") or 0) and reset_time and existing.get("resetTime"):
            if str(reset_time) < str(existing.get("resetTime")):
                pools[pool_name] = {
                    "label": pool_name,
                    "modelId": pool_name,
                    "remainingPercentage": remaining,
                    "usedPercentage": 100.0 - remaining,
                    "resetTime": reset_time,
                }
    return [pools[name] for name in POOL_ORDER if name in pools]


def parse_user_status(payload: dict[str, Any]) -> dict[str, Any]:
    user_status = payload.get("userStatus") if isinstance(payload.get("userStatus"), dict) else payload
    result: dict[str, Any] = {
        "email": user_status.get("email") if isinstance(user_status.get("email"), str) else None,
        "models": [],
        "pools": [],
        "promptCredits": None,
    }

    plan_status = user_status.get("planStatus") if isinstance(user_status.get("planStatus"), dict) else {}
    available = plan_status.get("availablePromptCredits")
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
    models = models_from_configs(configs)
    result["models"] = models
    result["pools"] = collapse_model_pools(models)
    return result


def _quota_from_connection(connection: AntigravityConnection) -> dict[str, Any] | None:
    payload = rpc_request(connection, "GetUserStatus", USER_STATUS_BODY)
    if payload:
        quota = parse_user_status(payload)
        if quota.get("pools"):
            return quota
    fallback = rpc_request(connection, "GetCommandModelConfigs", USER_STATUS_BODY)
    if not fallback:
        return None
    configs = fallback.get("clientModelConfigs") if isinstance(fallback.get("clientModelConfigs"), list) else []
    models = models_from_configs(configs)
    if not models:
        return None
    return {
        "email": None,
        "models": models,
        "pools": collapse_model_pools(models),
        "promptCredits": None,
    }


def default_quota_cache_path() -> Path:
    appdata = os.getenv("APPDATA")
    base = Path(appdata) if appdata else Path.home() / ".agentstatistics"
    return base / "AgentStatistics" / "antigravity_quota_cache.json"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def load_quota_cache(cache_path: Path | None) -> dict[str, Any] | None:
    path = cache_path or default_quota_cache_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or int(payload.get("version") or 0) != QUOTA_CACHE_VERSION:
        return None
    quota = payload.get("quota")
    if not isinstance(quota, dict) or not quota.get("pools"):
        return None
    return {
        "quota": quota,
        "baseUrl": payload.get("baseUrl") if isinstance(payload.get("baseUrl"), str) else None,
        "cachedAt": payload.get("cachedAt") if isinstance(payload.get("cachedAt"), str) else None,
    }


def save_quota_cache(cache_path: Path | None, probe_result: dict[str, Any]) -> None:
    quota = probe_result.get("quota")
    if not isinstance(quota, dict) or not quota.get("pools"):
        return
    path = cache_path or default_quota_cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": QUOTA_CACHE_VERSION,
        "cachedAt": _iso_now(),
        "baseUrl": probe_result.get("baseUrl"),
        "quota": quota,
    }
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _probe_live(connections: list[AntigravityConnection] | None) -> dict[str, Any]:
    resolved = connections if connections is not None else detect_connections()
    if not resolved:
        return {"ok": False, "error": "Antigravity CLI 未运行", "quota": None}
    for connection in resolved:
        quota = _quota_from_connection(connection)
        if quota:
            return {
                "ok": True,
                "quota": quota,
                "baseUrl": f"{connection.scheme}://127.0.0.1:{connection.port}",
            }
    return {"ok": False, "error": "无法连接 Antigravity CLI 本机服务", "quota": None}


def probe_antigravity_quota(
    connections: list[AntigravityConnection] | None = None,
    cache_path: Path | None = None,
) -> dict[str, Any]:
    live = _probe_live(connections)
    if live.get("ok") and live.get("quota"):
        save_quota_cache(cache_path, live)
        return live

    cached = load_quota_cache(cache_path)
    if cached:
        return {
            "ok": True,
            "quota": cached["quota"],
            "baseUrl": cached.get("baseUrl"),
            "cached": True,
            "cachedAt": cached.get("cachedAt"),
            "probeError": live.get("error"),
        }
    return live


def _risk_source_note(quota_data: dict[str, Any] | None) -> str:
    if quota_data and quota_data.get("cached"):
        error = quota_data.get("probeError") or "CLI 未运行"
        cached_at = quota_data.get("cachedAt")
        if cached_at:
            return f"缓存额度 · {error} · {cached_at}"
        return f"缓存额度 · {error}"
    return "Antigravity Connect RPC"


def build_antigravity_risk_rows(quota_data: dict[str, Any] | None, cache_hit: float) -> list[dict[str, Any]]:
    quota = (quota_data or {}).get("quota") or {}
    pools = quota.get("pools") if isinstance(quota.get("pools"), list) else []
    if pools:
        note = _risk_source_note(quota_data)
        rows: list[dict[str, Any]] = []
        for pool in pools[:3]:
            if not isinstance(pool, dict):
                continue
            rows.append(
                quota_risk_row(
                    str(pool.get("label") or pool.get("modelId") or "模型"),
                    pool.get("usedPercentage"),
                    reset_time_label(pool.get("resetTime")),
                    "blue" if str(pool.get("label")) == "Gemini Pro" else "teal",
                    note,
                )
            )
        rows.append({"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"})
        return rows

    note = (quota_data or {}).get("error") or (quota_data or {}).get("probeError") or "请运行 Antigravity CLI（agy）"
    return [
        quota_risk_row("Gemini Pro", None, None, "blue", note),
        quota_risk_row("Gemini Flash", None, None, "teal", note),
        {"name": "缓存", "value": cache_hit, "label": f"命中 {cache_hit:.0f}%", "note": "输入 token", "tone": "teal"},
    ]
