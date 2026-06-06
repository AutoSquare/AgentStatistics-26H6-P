# -*- coding: utf-8 -*-
"""Sync Cursor dashboard usage through an already-authenticated Edge DevTools tab."""
from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from cursor_usage_api import build_usage_json_document, extract_usage_events, write_usage_json

DASHBOARD_URL = "https://cursor.com/cn/dashboard/spending"
AUTH_ME_URL = "https://cursor.com/api/auth/me"
USAGE_SUMMARY_URL = "https://cursor.com/api/usage-summary"
USAGE_EVENTS_URL = "https://cursor.com/api/dashboard/get-filtered-usage-events"
DEFAULT_ENDPOINT = "http://127.0.0.1:9222"
DEFAULT_PAGE_SIZE = 500
MAX_PAGES = 200


class DevToolsError(RuntimeError):
    """Raised when the local Edge DevTools endpoint cannot serve the sync."""


class CdpWebSocket:
    def __init__(self, url: str, timeout: float = 30.0) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"ws", "wss"}:
            raise DevToolsError(f"unsupported websocket scheme: {parsed.scheme}")
        self._secure = parsed.scheme == "wss"
        self._host = parsed.hostname or "127.0.0.1"
        self._port = parsed.port or (443 if self._secure else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        self._path = path
        self._timeout = timeout
        self._next_id = 1
        self._sock = self._connect()

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        payload = {"id": message_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._send_text(json.dumps(payload, separators=(",", ":")))

        deadline = time.monotonic() + (timeout or self._timeout)
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            self._sock.settimeout(remaining)
            frame = self._read_frame()
            if frame is None:
                continue
            try:
                item = json.loads(frame)
            except json.JSONDecodeError:
                continue
            if item.get("id") != message_id:
                continue
            if "error" in item:
                raise DevToolsError(f"cdp {method} failed: {item['error']}")
            result = item.get("result")
            return result if isinstance(result, dict) else {}
        raise DevToolsError(f"cdp {method} timed out")

    def _connect(self) -> socket.socket:
        raw = socket.create_connection((self._host, self._port), timeout=self._timeout)
        sock: socket.socket = raw
        if self._secure:
            sock = ssl.create_default_context().wrap_socket(raw, server_hostname=self._host)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {self._path} HTTP/1.1\r\n"
            f"Host: {self._host}:{self._port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "Origin: http://127.0.0.1\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = self._recv_until(sock, b"\r\n\r\n")
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise DevToolsError("devtools websocket handshake failed")
        return sock

    @staticmethod
    def _recv_until(sock: socket.socket, marker: bytes) -> bytes:
        data = b""
        while marker not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        if len(payload) < 126:
            header.append(0x80 | len(payload))
        elif len(payload) <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", len(payload)))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", len(payload)))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self._sock.sendall(bytes(header) + masked)

    def _read_frame(self) -> str | None:
        header = self._recv_exact(2)
        if len(header) < 2:
            return None
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        masked = bool(header[1] & 0x80)
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        if opcode == 0x8:
            raise DevToolsError("devtools websocket closed")
        if opcode == 0x9:
            return None
        if opcode != 0x1:
            return None
        return payload.decode("utf-8", errors="replace")

    def _recv_exact(self, size: int) -> bytes:
        data = b""
        while len(data) < size:
            chunk = self._sock.recv(size - len(data))
            if not chunk:
                raise DevToolsError("devtools websocket disconnected")
            data += chunk
        return data


def _http_json(url: str, timeout: int = 5) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        raise DevToolsError(f"cannot read {url}: {exc}") from exc


def _find_or_create_dashboard_target(endpoint: str, timeout: int) -> str:
    tabs = _http_json(endpoint.rstrip("/") + "/json", timeout)
    if not isinstance(tabs, list):
        raise DevToolsError("devtools /json did not return a tab list")
    for tab in tabs:
        if not isinstance(tab, dict):
            continue
        url = str(tab.get("url") or "")
        ws_url = str(tab.get("webSocketDebuggerUrl") or "")
        if "cursor.com" in url and ws_url:
            return ws_url

    new_target = _http_json(
        endpoint.rstrip("/") + "/json/new?" + urllib.parse.quote(DASHBOARD_URL, safe=""),
        timeout,
    )
    if isinstance(new_target, dict) and new_target.get("webSocketDebuggerUrl"):
        return str(new_target["webSocketDebuggerUrl"])
    raise DevToolsError("cannot create cursor.com devtools target")


def _evaluate_json(ws: CdpWebSocket, expression: str, timeout: float = 45.0) -> dict[str, Any]:
    result = ws.call(
        "Runtime.evaluate",
        {
            "expression": expression,
            "awaitPromise": True,
            "returnByValue": True,
            "userGesture": True,
        },
        timeout=timeout,
    )
    exception = result.get("exceptionDetails")
    if exception:
        raise DevToolsError(f"browser evaluation failed: {exception}")
    remote = result.get("result")
    if not isinstance(remote, dict):
        return {}
    value = remote.get("value")
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _fetch_script(method: str, url: str, body: dict[str, Any] | None = None) -> str:
    body_json = "undefined" if body is None else json.dumps(json.dumps(body), ensure_ascii=False)
    return f"""
        (async () => {{
          try {{
            const options = {{
              method: {json.dumps(method)},
              credentials: 'include',
              headers: {{ 'Accept': 'application/json', 'Content-Type': 'application/json' }}
            }};
            const body = {body_json};
            if (body !== undefined) options.body = body;
            const response = await fetch({json.dumps(url)}, options);
            const text = await response.text();
            let payload = null;
            try {{ payload = text ? JSON.parse(text) : null; }} catch (_) {{}}
            return {{ ok: response.ok, status: response.status, json: payload, text: response.ok ? '' : text.slice(0, 400) }};
          }} catch (error) {{
            return {{ ok: false, status: 0, error: String(error) }};
          }}
        }})()
    """


def _date_range_ms(days: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=max(1, days))
    return str(int(start.timestamp() * 1000)), str(int((now + timedelta(minutes=1)).timestamp() * 1000))


def _fetch_all_events(ws: CdpWebSocket, days: int, page_size: int) -> tuple[list[dict[str, Any]], int | None]:
    start_ms, end_ms = _date_range_ms(days)
    events: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, MAX_PAGES + 1):
        payload = {
            "page": page,
            "pageSize": page_size,
            "startDate": start_ms,
            "endDate": end_ms,
        }
        result = _evaluate_json(ws, _fetch_script("POST", USAGE_EVENTS_URL, payload), timeout=60.0)
        if not result.get("ok"):
            if events:
                return events, total
            raise DevToolsError(f"usage events fetch failed: status={result.get('status')} error={result.get('error') or result.get('text')}")
        data = result.get("json")
        if not isinstance(data, dict):
            raise DevToolsError("usage events response is not an object")
        batch = extract_usage_events(data)
        raw_total = data.get("totalUsageEventsCount")
        if total is None:
            if isinstance(raw_total, int):
                total = raw_total
            elif isinstance(raw_total, str) and raw_total.isdigit():
                total = int(raw_total)
        events.extend(batch)
        if not batch:
            break
        if total is not None and len(events) >= total:
            break
        if len(batch) < page_size:
            break
    return events, total


def _write_text_atomically(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    _write_text_atomically(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _account_id(user: dict[str, Any]) -> str | None:
    value = user.get("sub") or user.get("id") or user.get("userId")
    return str(value) if value else None


def sync_from_edge_devtools(cache_dir: Path, app_data_dir: Path, endpoint: str, days: int, timeout: int) -> dict[str, Any]:
    ws_url = _find_or_create_dashboard_target(endpoint, timeout)
    ws = CdpWebSocket(ws_url, timeout=float(timeout))
    try:
        ws.call("Runtime.enable", timeout=10.0)
        ws.call("Page.enable", timeout=10.0)
        auth_result = _evaluate_json(ws, _fetch_script("GET", AUTH_ME_URL), timeout=30.0)
        if not auth_result.get("ok") or not isinstance(auth_result.get("json"), dict):
            raise DevToolsError(f"auth/me failed: status={auth_result.get('status')} error={auth_result.get('error') or auth_result.get('text')}")
        user = auth_result["json"]
        account_id = _account_id(user)
        if not account_id:
            raise DevToolsError("auth/me did not return an account id")
        email = str(user.get("email") or "") or None

        events, expected = _fetch_all_events(ws, days, DEFAULT_PAGE_SIZE)
        if not events:
            raise DevToolsError("usage events response is empty")
        if expected is not None and len(events) < expected:
            raise DevToolsError(f"usage events incomplete: actual={len(events)} expected={expected}")

        document = build_usage_json_document(events, source="edge-devtools-json")
        document["accountId"] = account_id
        if email:
            document["email"] = email
        write_usage_json(cache_dir, document)

        now = datetime.now(timezone.utc).isoformat()
        _write_json_atomically(
            cache_dir / "usage-account.json",
            {
                "version": 1,
                "accountId": account_id,
                "email": email,
                "isOnline": True,
                "updatedAt": now,
            },
        )

        summary_result = _evaluate_json(ws, _fetch_script("GET", USAGE_SUMMARY_URL), timeout=30.0)
        if summary_result.get("ok") and isinstance(summary_result.get("json"), dict):
            _write_json_atomically(
                app_data_dir / "cursor_web_usage_summary.json",
                {
                    "fetchedAt": now,
                    "summary": summary_result["json"],
                },
            )

        _write_json_atomically(
            app_data_dir / "cursor_usage_sync_status.json",
            {
                "version": 1,
                "accountId": account_id,
                "source": "edge-devtools-json",
                "status": "ok",
                "actualEvents": len(events),
                "expectedEvents": expected,
                "message": "Edge DevTools sync ok",
                "updatedAt": now,
            },
        )
        return {
            "ok": True,
            "accountId": account_id,
            "email": email,
            "actualEvents": len(events),
            "expectedEvents": expected,
            "source": "edge-devtools-json",
        }
    finally:
        ws.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--app-data-dir", required=True)
    parser.add_argument("--endpoint", default=os.getenv("AS_CURSOR_EDGE_DEVTOOLS_ENDPOINT") or DEFAULT_ENDPOINT)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    try:
        result = sync_from_edge_devtools(
            Path(args.cache_dir),
            Path(args.app_data_dir),
            args.endpoint,
            max(1, args.days),
            max(5, args.timeout),
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
