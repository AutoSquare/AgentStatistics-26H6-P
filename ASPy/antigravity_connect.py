# -*- coding: utf-8 -*-
"""Antigravity CLI / language_server discovery and Connect RPC helpers."""
from __future__ import annotations

import json
import re
import socket
import ssl
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from antigravity_paths import cli_log_paths

CONNECT_SERVICE = "/exa.language_server_pb.LanguageServerService"
MAX_RPC_BODY_BYTES = 16 * 1024 * 1024
VALID_CONNECT_STATUSES = {200, 401}
CLI_PROCESS_NAMES = ("agy.exe", "antigravity.exe")
CLI_LOG_PORT_PATTERN = re.compile(
    r"Language server listening on random port at (\d+) for (HTTPS \(gRPC\)|HTTP)",
    re.IGNORECASE,
)


@dataclass
class AntigravityConnection:
    """Running Antigravity language_server endpoint."""

    pid: int
    port: int
    csrf_token: str
    fingerprint: str


def is_language_server_command(command: str) -> bool:
    return "language_server" in command.lower()


def is_antigravity_command(command: str) -> bool:
    lower = command.lower()
    return (
        "antigravity" in lower
        or "agy.exe" in lower
        or ("--app_data_dir" in lower and "antigravity" in lower)
    )


def is_cli_runtime_process(command: str, executable_path: str) -> bool:
    lower_cmd = command.lower()
    lower_exe = executable_path.lower()
    if any(name in lower_exe for name in CLI_PROCESS_NAMES):
        return True
    if "antigravity-cli" in lower_cmd:
        return True
    if "agy.exe" in lower_cmd or "agy " in lower_cmd or lower_cmd.endswith("agy"):
        return True
    return False


def extract_flag(flag: str, command: str) -> str | None:
    escaped = re.escape(flag)
    match = re.search(rf"{escaped}[=\s]+(\S+)", command, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_csrf_token(command: str) -> str | None:
    token = extract_flag("--csrf_token", command)
    if not token or len(token) < 32:
        return None
    if not all(ch.isalnum() or ch == "-" for ch in token):
        return None
    return token


def executable_path_looks_antigravity(path: str) -> bool:
    lower = path.lower()
    return "antigravity" in lower or "language_server" in lower


def command_line_executable_looks_antigravity(command: str) -> bool:
    trimmed = command.strip()
    if trimmed.startswith('"'):
        first = trimmed[1:].split('"', 1)[0]
    else:
        first = trimmed.split(None, 1)[0] if trimmed else ""
    return executable_path_looks_antigravity(first)


def list_process_candidates_windows() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stderr=subprocess.DEVNULL,
            timeout=15,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(output) if output.strip() else []
        if isinstance(payload, dict):
            payload = [payload]
        for item in payload:
            if not isinstance(item, dict):
                continue
            pid = int(item.get("ProcessId") or 0)
            command = str(item.get("CommandLine") or "")
            if pid <= 0 or not command:
                continue
            lower = command.lower()
            if not is_language_server_command(lower) or not is_antigravity_command(lower):
                continue
            executable_path = str(item.get("ExecutablePath") or "").strip()
            if executable_path:
                if not executable_path_looks_antigravity(executable_path):
                    continue
            elif not command_line_executable_looks_antigravity(command):
                continue
            csrf = extract_csrf_token(command)
            if not csrf:
                continue
            declared_port = extract_flag("--extension_server_port", command)
            processes.append(
                {
                    "pid": pid,
                    "ppid": int(item.get("ParentProcessId") or 0),
                    "csrf_token": csrf,
                    "declared_port": int(declared_port) if declared_port and declared_port.isdigit() else None,
                    "command": command,
                }
            )
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError, OSError):
        return []
    processes.sort(key=lambda item: (-int(item["pid"]), -int(item.get("ppid") or 0)))
    deduped: list[dict[str, Any]] = []
    seen: set[int] = set()
    for process in processes:
        pid = int(process["pid"])
        if pid in seen:
            continue
        seen.add(pid)
        deduped.append(process)
    return deduped


def list_listening_ports(pid: int) -> list[int]:
    ports: list[int] = []
    try:
        output = subprocess.check_output(
            ["netstat", "-ano", "-p", "TCP"],
            stderr=subprocess.DEVNULL,
            timeout=12,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.SubprocessError, OSError):
        return ports
    pid_text = str(pid)
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        if not parts[0].upper().startswith("TCP"):
            continue
        if parts[-1] != pid_text or parts[3].upper() != "LISTENING":
            continue
        local = parts[1]
        _, _, port_text = local.rpartition(":")
        host = local[: -len(port_text) - 1] if port_text else local
        if host not in {"127.0.0.1", "[::1]", "::1", "0.0.0.0", "[::]", "*"}:
            continue
        try:
            port = int(port_text)
        except ValueError:
            continue
        if 1 <= port < 65536:
            ports.append(port)
    return sorted(set(ports))


def connect_request(
    base_url: str,
    path: str,
    csrf_token: str | None,
    body: dict[str, Any] | None = None,
    timeout: int = 12,
) -> dict[str, Any] | None:
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(body or {}).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
    }
    if csrf_token:
        headers["X-Codeium-Csrf-Token"] = csrf_token
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    context = ssl._create_unverified_context() if base_url.startswith("https://") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            raw = response.read(MAX_RPC_BODY_BYTES + 1)
            if len(raw) > MAX_RPC_BODY_BYTES:
                return None
            payload = json.loads(raw.decode("utf-8"))
            return payload if isinstance(payload, dict) else None
    except urllib.error.HTTPError as exc:
        if exc.code not in VALID_CONNECT_STATUSES:
            return None
        try:
            raw = exc.read(MAX_RPC_BODY_BYTES + 1)
            if not raw:
                return {}
            payload = json.loads(raw.decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, ValueError, OSError):
            return {}
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, socket.timeout, ValueError):
        return None


def probe_connect_scheme(port: int, csrf_token: str | None = None, timeout: int = 4) -> str | None:
    body = {"wrapper_data": {}}
    path = f"{CONNECT_SERVICE}/GetUnleashData"
    for scheme in ("https", "http"):
        payload = connect_request(f"{scheme}://127.0.0.1:{port}", path, csrf_token, body, timeout=timeout)
        if payload is not None:
            return scheme
    return None


def probe_heartbeat(port: int, csrf_token: str | None) -> bool:
    body = {"uuid": "00000000-0000-0000-0000-000000000000"}
    for scheme in ("http", "https"):
        payload = connect_request(f"{scheme}://127.0.0.1:{port}", f"{CONNECT_SERVICE}/Heartbeat", csrf_token, body, timeout=4)
        if payload is not None:
            return True
    return False


def candidate_probe_ports(process: dict[str, Any], ports: list[int]) -> list[int]:
    candidates = list(ports)
    declared = process.get("declared_port")
    if isinstance(declared, int) and declared not in candidates:
        candidates.append(declared)
    return sorted(set(candidates))


def parse_cli_log_ports(max_files: int = 3) -> list[int]:
    ports: list[int] = []
    for path in cli_log_paths()[:max_files]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in CLI_LOG_PORT_PATTERN.finditer(text):
            try:
                port = int(match.group(1))
            except ValueError:
                continue
            if 1 <= port < 65536 and port not in ports:
                ports.append(port)
    return ports


def list_runtime_processes_windows() -> list[dict[str, Any]]:
    processes: list[dict[str, Any]] = []
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "Get-CimInstance Win32_Process | "
        "Where-Object { "
        "$_.ProcessName -in @('agy','antigravity') -or "
        "$_.CommandLine -match 'antigravity|agy\\.exe|antigravity-cli' "
        "} | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    try:
        output = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            stderr=subprocess.DEVNULL,
            timeout=15,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = json.loads(output) if output.strip() else []
        if isinstance(payload, dict):
            payload = [payload]
        for item in payload:
            if not isinstance(item, dict):
                continue
            pid = int(item.get("ProcessId") or 0)
            command = str(item.get("CommandLine") or "")
            executable_path = str(item.get("ExecutablePath") or "").strip()
            if pid <= 0:
                continue
            if not command and not executable_path:
                continue
            if not is_cli_runtime_process(command, executable_path):
                continue
            processes.append(
                {
                    "pid": pid,
                    "ppid": int(item.get("ParentProcessId") or 0),
                    "csrf_token": extract_csrf_token(command),
                    "declared_port": None,
                    "command": command,
                    "executable_path": executable_path,
                }
            )
    except (subprocess.SubprocessError, json.JSONDecodeError, ValueError, OSError):
        return []
    processes.sort(key=lambda item: (-int(item["pid"]), -int(item.get("ppid") or 0)))
    return processes


def append_connection(
    connections: list[AntigravityConnection],
    seen: set[tuple[int, int]],
    pid: int,
    port: int,
    csrf_token: str | None,
    probe_timeout: int = 2,
) -> None:
    key = (pid, port)
    if key in seen:
        return
    scheme = probe_connect_scheme(port, csrf_token, timeout=probe_timeout)
    if not scheme:
        return
    seen.add(key)
    connections.append(
        AntigravityConnection(
            pid=pid,
            port=port,
            csrf_token=csrf_token or "",
            fingerprint=f"pid:{pid}:port:{port}",
        )
    )


def ordered_probe_ports(process: dict[str, Any], preferred_ports: list[int] | None = None) -> list[int]:
    pid = int(process["pid"])
    ports: list[int] = []
    for port in preferred_ports or []:
        if port not in ports:
            ports.append(port)
    for port in candidate_probe_ports(process, list_listening_ports(pid)):
        if port not in ports:
            ports.append(port)
    return ports[:8]


def detect_connections() -> list[AntigravityConnection]:
    connections: list[AntigravityConnection] = []
    seen: set[tuple[int, int]] = set()
    language_server_processes = list_process_candidates_windows()
    runtime_processes = list_runtime_processes_windows()
    if not language_server_processes and not runtime_processes:
        return []
    for process in language_server_processes:
        pid = int(process["pid"])
        csrf = str(process["csrf_token"])
        for port in ordered_probe_ports(process):
            before = len(seen)
            append_connection(connections, seen, pid, port, csrf)
            if len(seen) > before:
                break
    for process in runtime_processes:
        pid = int(process["pid"])
        csrf = process.get("csrf_token")
        csrf_text = str(csrf) if isinstance(csrf, str) and csrf else None
        listening = set(list_listening_ports(pid))
        preferred_ports = [port for port in parse_cli_log_ports() if port in listening]
        for port in ordered_probe_ports(process, preferred_ports):
            before = len(seen)
            append_connection(connections, seen, pid, port, csrf_text)
            if len(seen) > before:
                break
    connections.sort(key=lambda item: (-max(item.pid, 0), item.port))
    deduped: list[AntigravityConnection] = []
    seen_ports: set[int] = set()
    for connection in connections:
        if connection.port in seen_ports:
            continue
        seen_ports.add(connection.port)
        deduped.append(connection)
    return deduped


def rpc_request(connection: AntigravityConnection, method: str, body: dict[str, Any] | None = None) -> dict[str, Any] | None:
    path = f"{CONNECT_SERVICE}/{method}"
    for scheme in ("http", "https"):
        payload = connect_request(
            f"{scheme}://127.0.0.1:{connection.port}",
            path,
            connection.csrf_token,
            body,
        )
        if payload is not None:
            return payload
    return None


def discover_connect_base(process: dict[str, Any]) -> str | None:
    pid = int(process["pid"])
    csrf = str(process["csrf_token"])
    candidates = candidate_probe_ports(process, list_listening_ports(pid))
    for port in candidates:
        for scheme in ("https", "http"):
            base = f"{scheme}://127.0.0.1:{port}"
            payload = connect_request(base, f"{CONNECT_SERVICE}/GetUnleashData", csrf, {"wrapper_data": {}})
            if payload is not None:
                return base
    return None
