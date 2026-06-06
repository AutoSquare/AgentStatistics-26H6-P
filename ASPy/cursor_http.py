# -*- coding: utf-8 -*-
"""HTTP helpers for Cursor web API with Vercel checkpoint detection."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import quote

MIN_INTERVAL_SEC = 0.9
_last_request_at = 0.0

MINIMAL_JSON_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

MINIMAL_CSV_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://cursor.com/cn/dashboard/usage",
    "User-Agent": MINIMAL_JSON_HEADERS["User-Agent"],
}

MINIMAL_JSON_HEADERS_WITH_REFERER = {
    **MINIMAL_JSON_HEADERS,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://cursor.com/dashboard/spending",
}

VERCEL_MARKERS = ("Vercel Security Checkpoint", "vercel security checkpoint")


def _throttle() -> None:
    global _last_request_at
    now = time.monotonic()
    wait = MIN_INTERVAL_SEC - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    _last_request_at = time.monotonic()


def _cookie_header(session_token: str) -> dict[str, str]:
    return {"Cookie": f"WorkosCursorSessionToken={session_token}"}


def classify_http_failure(status: int | None, body: str) -> dict[str, str]:
    text = body or ""
    if status in (401, 403) and any(marker in text for marker in VERCEL_MARKERS):
        return {
            "kind": "vercel_checkpoint",
            "message": "云端同步暂时受阻",
        }
    if status in (401, 403):
        return {"kind": "unauthorized", "message": f"Cursor API 未授权（HTTP {status}），请在本机 Cursor 重新登录。"}
    if status is not None:
        return {"kind": "network", "message": f"Cursor API 请求失败（HTTP {status}）。"}
    return {"kind": "network", "message": "Cursor API 网络请求失败。"}


def _parse_json_body(body: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _request_text_with_urllib(
    url: str,
    session_token: str,
    timeout: int,
    headers: dict[str, str],
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> dict[str, Any]:
    _throttle()
    request = urllib.request.Request(
        url,
        data=body,
        headers={**headers, **_cookie_header(session_token)},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8-sig", errors="replace")
            return {"ok": True, "text": body}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        failure = classify_http_failure(exc.code, body)
        return {"ok": False, **failure}
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return {"ok": False, "kind": "timeout", "message": f"Cursor API 请求超时（>{timeout}s）。"}
        return {"ok": False, "kind": "network", "message": f"Cursor API 网络错误：{reason}"}


def _request_text_with_curl_cffi(
    url: str,
    session_token: str,
    timeout: int,
    headers: dict[str, str],
    *,
    method: str = "GET",
    body: bytes | None = None,
) -> dict[str, Any] | None:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return None
    _throttle()
    try:
        response = curl_requests.request(
            method,
            url,
            data=body,
            headers={**headers, **_cookie_header(session_token)},
            impersonate="chrome120",
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as exc:
        return {"ok": False, **classify_http_failure(None, str(exc)), "detail": str(exc)}
    body = response.text or ""
    if response.status_code < 200 or response.status_code >= 300:
        failure = classify_http_failure(response.status_code, body)
        return {"ok": False, **failure}
    return {"ok": True, "text": body}


def request_text(
    url: str,
    session_token: str,
    timeout: int = 15,
    *,
    csv_export: bool = False,
    method: str = "GET",
    body: bytes | None = None,
) -> dict[str, Any]:
    headers = dict(MINIMAL_CSV_HEADERS if csv_export else MINIMAL_JSON_HEADERS_WITH_REFERER)
    if method.upper() == "POST":
        headers["Content-Type"] = "application/json"
        headers["Referer"] = "https://cursor.com/cn/dashboard/usage"
        headers["Origin"] = "https://cursor.com"
    curl_result = _request_text_with_curl_cffi(
        url,
        session_token,
        timeout,
        headers,
        method=method,
        body=body,
    )
    if curl_result is not None and curl_result.get("ok"):
        return curl_result
    if curl_result is not None and curl_result.get("kind") != "vercel_checkpoint":
        return curl_result
    return _request_text_with_urllib(
        url,
        session_token,
        timeout,
        headers,
        method=method,
        body=body,
    )


def request_json_post(url: str, session_token: str, body: dict[str, Any], timeout: int = 30) -> dict[str, Any]:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    text_result = request_text(url, session_token, timeout=timeout, method="POST", body=payload)
    if not text_result.get("ok"):
        return text_result
    parsed = _parse_json_body(str(text_result.get("text") or ""))
    if parsed is None:
        return {"ok": False, "kind": "parse", "message": "Cursor API 响应不是有效 JSON。"}
    return {"ok": True, "json": parsed}


def request_json(url: str, session_token: str, timeout: int = 15) -> dict[str, Any]:
    text_result = request_text(url, session_token, timeout=timeout)
    if not text_result.get("ok"):
        return text_result
    body = str(text_result.get("text") or "")
    payload = _parse_json_body(body)
    if payload is None:
        return {"ok": False, "kind": "parse", "message": "Cursor API 响应不是有效 JSON。"}
    return {"ok": True, "json": payload}


def request_usage_for_user(base_url: str, session_token: str, user_sub: str, timeout: int = 15) -> dict[str, Any]:
    query = quote(user_sub, safe="")
    return request_json(f"{base_url}?user={query}", session_token, timeout)
