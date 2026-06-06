# -*- coding: utf-8 -*-
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import antigravity_quota
from antigravity_connect import AntigravityConnection
from antigravity_quota import (
    build_antigravity_risk_rows,
    collapse_model_pools,
    load_quota_cache,
    models_from_configs,
    parse_user_status,
    probe_antigravity_quota,
    save_quota_cache,
)


class AntigravityQuotaTests(unittest.TestCase):
    def test_models_from_configs_filters_blacklist(self) -> None:
        configs = [
            {
                "label": "Placeholder",
                "modelOrAlias": {"model": "MODEL_PLACEHOLDER_M9"},
                "quotaInfo": {"remainingFraction": 0.2},
            },
            {
                "label": "Gemini Flash",
                "modelOrAlias": {"model": "gemini-3-flash"},
                "quotaInfo": {"remainingFraction": 0.4, "resetTime": "2026-06-05T12:00:00Z"},
            },
        ]
        models = models_from_configs(configs)
        self.assertEqual(len(models), 1)
        self.assertEqual(models[0]["label"], "Gemini Flash")

    def test_collapse_model_pools_uses_tightest_remaining(self) -> None:
        models = [
            {"label": "Flash A", "modelId": "gemini-3-flash-a", "remainingPercentage": 90.0, "resetTime": "2026-06-05T01:00:00Z"},
            {"label": "Flash B", "modelId": "gemini-3-flash-b", "remainingPercentage": 40.0, "resetTime": "2026-06-05T02:00:00Z"},
            {"label": "Pro", "modelId": "gemini-3-pro", "remainingPercentage": 50.0, "resetTime": "2026-06-05T03:00:00Z"},
        ]
        pools = collapse_model_pools(models)
        self.assertEqual([pool["label"] for pool in pools], ["Gemini Pro", "Gemini Flash"])
        self.assertEqual(pools[0]["usedPercentage"], 50.0)
        self.assertEqual(pools[1]["usedPercentage"], 60.0)

    def test_parse_user_status_models(self) -> None:
        payload = {
            "userStatus": {
                "email": "user@example.com",
                "planStatus": {
                    "availablePromptCredits": 80,
                    "planInfo": {"monthlyPromptCredits": 100},
                },
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {
                            "label": "Gemini Flash",
                            "modelOrAlias": {"model": "gemini-3-flash"},
                            "quotaInfo": {"remainingFraction": 0.4, "resetTime": "2026-06-05T12:00:00Z"},
                        }
                    ]
                },
            }
        }
        parsed = parse_user_status(payload)
        self.assertEqual(parsed["email"], "user@example.com")
        self.assertEqual(len(parsed["models"]), 1)
        self.assertEqual(parsed["models"][0]["usedPercentage"], 60.0)
        self.assertEqual(parsed["pools"][0]["label"], "Gemini Flash")

    def test_build_risk_rows_offline(self) -> None:
        rows = build_antigravity_risk_rows({"ok": False, "error": "Antigravity CLI 未运行"}, 12.5)
        self.assertEqual(rows[0]["label"], "等待数据")
        self.assertEqual(rows[-1]["name"], "缓存")

    def test_build_risk_rows_cached(self) -> None:
        rows = build_antigravity_risk_rows(
            {
                "ok": True,
                "cached": True,
                "cachedAt": "2026-06-06T08:00:00Z",
                "probeError": "Antigravity CLI 未运行",
                "quota": {
                    "pools": [
                        {"label": "Gemini Pro", "usedPercentage": 40.0, "resetTime": "2026-06-06T12:00:00Z"},
                    ]
                },
            },
            12.5,
        )
        self.assertEqual(rows[0]["name"], "Gemini Pro")
        self.assertEqual(rows[0]["percentLabel"], "40%")

    def test_build_risk_rows_uses_pools(self) -> None:
        rows = build_antigravity_risk_rows(
            {
                "ok": True,
                "quota": {
                    "pools": [
                        {"label": "Gemini Pro", "usedPercentage": 50.0, "resetTime": "2026-06-05T12:00:00Z"},
                        {"label": "Gemini Flash", "usedPercentage": 10.0, "resetTime": "2026-06-05T11:00:00Z"},
                    ]
                },
            },
            12.5,
        )
        self.assertEqual(rows[0]["name"], "Gemini Pro")
        self.assertEqual(rows[1]["name"], "Gemini Flash")
        self.assertEqual(rows[-1]["name"], "缓存")

    def test_probe_quota_uses_detected_connections(self) -> None:
        connection = AntigravityConnection(pid=1, port=51210, csrf_token="", fingerprint="pid:1:port:51210", scheme="https")
        payload = {
            "userStatus": {
                "email": "user@example.com",
                "cascadeModelConfigData": {
                    "clientModelConfigs": [
                        {
                            "label": "Claude",
                            "modelOrAlias": {"model": "claude-sonnet-4-5"},
                            "quotaInfo": {"remainingFraction": 0.7},
                        }
                    ]
                },
            }
        }
        with patch("antigravity_quota.rpc_request", return_value=payload) as request:
            result = probe_antigravity_quota([connection])

        self.assertTrue(result["ok"])
        self.assertEqual(result["quota"]["email"], "user@example.com")
        self.assertEqual(result["quota"]["pools"][0]["label"], "Claude")
        self.assertEqual(result["baseUrl"], "https://127.0.0.1:51210")
        self.assertEqual(request.call_args.args[1], "GetUserStatus")

    def test_probe_quota_falls_back_to_command_model_configs(self) -> None:
        connection = AntigravityConnection(pid=1, port=51210, csrf_token="", fingerprint="pid:1:port:51210", scheme="https")
        fallback = {
            "clientModelConfigs": [
                {
                    "label": "Gemini Pro",
                    "modelOrAlias": {"model": "gemini-3-pro"},
                    "quotaInfo": {"remainingFraction": 0.5},
                }
            ]
        }
        with patch("antigravity_quota.rpc_request", side_effect=[None, fallback]) as request:
            result = probe_antigravity_quota([connection])

        self.assertTrue(result["ok"])
        self.assertEqual(result["quota"]["pools"][0]["label"], "Gemini Pro")
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[1], "GetCommandModelConfigs")

    def test_probe_quota_falls_back_to_local_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "quota_cache.json"
            save_quota_cache(
                cache_path,
                {
                    "ok": True,
                    "baseUrl": "https://127.0.0.1:51210",
                    "quota": {
                        "pools": [
                            {"label": "Gemini Flash", "usedPercentage": 20.0, "resetTime": "2026-06-06T12:00:00Z"},
                        ]
                    },
                },
            )
            with patch("antigravity_quota._probe_live", return_value={"ok": False, "error": "Antigravity CLI 未运行", "quota": None}):
                result = probe_antigravity_quota([], cache_path)

        self.assertTrue(result["ok"])
        self.assertTrue(result["cached"])
        self.assertEqual(result["quota"]["pools"][0]["label"], "Gemini Flash")
        self.assertEqual(result["probeError"], "Antigravity CLI 未运行")

    def test_save_and_load_quota_cache_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "quota_cache.json"
            save_quota_cache(
                cache_path,
                {
                    "ok": True,
                    "baseUrl": "https://127.0.0.1:51210",
                    "quota": {"pools": [{"label": "Claude", "usedPercentage": 55.0}]},
                },
            )
            loaded = load_quota_cache(cache_path)

        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["quota"]["pools"][0]["label"], "Claude")
        self.assertIsNotNone(loaded.get("cachedAt"))


if __name__ == "__main__":
    unittest.main()
