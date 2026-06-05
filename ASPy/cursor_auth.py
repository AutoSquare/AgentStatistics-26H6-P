# -*- coding: utf-8 -*-
"""Cursor Session Token normalization for AgentStatistics."""
from __future__ import annotations

import re

COOKIE_NAME = "WorkosCursorSessionToken"


def normalize_session_token(value: str | None) -> str | None:
    """Normalize pasted Cursor session token or cookie header text."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower().startswith("cookie:"):
        text = text[7:].strip()
    match = re.search(rf"{re.escape(COOKIE_NAME)}=([^;\s]+)", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()
    if text.lower() == COOKIE_NAME.lower():
        return None
    if re.search(r"\s", text):
        return None
    if len(text) < 8:
        return None
    return text
