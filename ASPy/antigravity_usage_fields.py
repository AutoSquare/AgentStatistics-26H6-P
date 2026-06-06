# -*- coding: utf-8 -*-
"""Normalize Antigravity raw usage fields to shared AgentStatistics schema."""
from __future__ import annotations


def build_antigravity_usage(
    raw_input: int,
    output_tokens_raw: int,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int = 0,
) -> dict[str, int]:
    """
    将 Antigravity 原始用量折叠为与 Cursor / Codex 一致的 prompt 口径。

    Antigravity JSONL 中 ``input`` 与 ``cacheRead`` 为并列字段；统一后
    ``input_tokens`` 表示 prompt 侧总量（新增输入 + 缓存读写），
    ``cached_input_tokens`` 为其中的缓存子集，便于 KPI 与趋势图「真实输入」对齐。
    """
    billable_input = max(0, int(raw_input or 0))
    cached_input_tokens = max(0, int(cache_read or 0)) + max(0, int(cache_write or 0))
    output_raw = max(0, int(output_tokens_raw or 0))
    reasoning_tokens = max(0, int(reasoning or 0))
    actual_visible_output = max(0, output_raw - reasoning_tokens) if output_raw >= reasoning_tokens else output_raw
    total_input_tokens = billable_input + cached_input_tokens
    return {
        "input_tokens": total_input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": actual_visible_output,
        "reasoning_output_tokens": reasoning_tokens,
        "total_tokens": total_input_tokens + output_raw,
    }
