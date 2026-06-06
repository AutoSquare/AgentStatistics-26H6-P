# -*- coding: utf-8 -*-
"""Invoke tokscale CLI for Cursor CSV sync, aligned with token-monitor."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from cursor_sync import tokscale_credentials_path


def default_tokscale_cache_dir() -> Path:
    return Path.home() / ".config" / "tokscale" / "cursor-cache"


def is_default_tokscale_cache(cache_dir: Path) -> bool:
    try:
        return cache_dir.resolve() == default_tokscale_cache_dir().resolve()
    except OSError:
        return False


def _app_roots() -> list[Path]:
    roots: list[Path] = []
    seen: set[str] = set()

    def add_root(raw: str | Path) -> None:
        key = str(raw)
        if key in seen:
            return
        seen.add(key)
        roots.append(Path(raw))

    env_root = os.environ.get("AGENTSTATISTICS_ROOT", "").strip()
    if env_root:
        add_root(env_root)
    here = Path(__file__).resolve().parent
    current = here
    for _ in range(8):
        add_root(current)
        if (current / "ThirdParty" / "tokscale").is_dir():
            roots.insert(0, current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return roots


def _platform_bundle_subdir() -> str:
    if os.name == "nt":
        return "win-x64"
    if sys.platform == "darwin":
        return "darwin-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "darwin-x64"
    if platform.machine().lower() in {"arm64", "aarch64"}:
        return "linux-arm64"
    return "linux-x64"


def _app_bundled_tokscale_binary() -> Path | None:
    binary_name = "tokscale.exe" if os.name == "nt" else "tokscale"
    subdir = _platform_bundle_subdir()
    for root in _app_roots():
        candidate = root / "ThirdParty" / "tokscale" / subdir / binary_name
        if candidate.is_file():
            return candidate
    return None


def _token_monitor_roots() -> list[Path]:
    here = Path(__file__).resolve().parent
    roots = [
        here.parent.parent / "token-monitor",
        here.parent / "token-monitor",
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _bundled_tokscale_binary() -> Path | None:
    binary_name = "tokscale.exe" if os.name == "nt" else "tokscale"
    packages = ["@tokscale/cli-win32-x64-msvc"]
    if sys.platform == "darwin" and sys.maxsize > 2**32:
        packages.insert(0, "@tokscale/cli-darwin-arm64")
    elif sys.platform == "darwin":
        packages.insert(0, "@tokscale/cli-darwin-x64")
    elif sys.platform.startswith("linux") and platform.machine().lower() in {"aarch64", "arm64"}:
        packages.extend(["@tokscale/cli-linux-arm64-gnu", "@tokscale/cli-linux-arm64-musl"])
    elif sys.platform.startswith("linux"):
        packages.extend(["@tokscale/cli-linux-x64-gnu", "@tokscale/cli-linux-x64-musl"])

    for monitor_root in _token_monitor_roots():
        node_modules = monitor_root / "node_modules"
        if not node_modules.is_dir():
            continue
        for package in packages:
            bin_path = node_modules / package / "bin" / binary_name
            if bin_path.is_file():
                return bin_path
        shim = node_modules / "tokscale" / "bin.js"
        if shim.is_file():
            return shim
    return None


def _nodejs_search_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[str] = set()

    def add_dir(raw: str | Path | None) -> None:
        if not raw:
            return
        try:
            path = Path(raw)
        except (TypeError, ValueError):
            return
        if not path.is_dir():
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        dirs.append(path)

    for part in os.environ.get("PATH", "").split(os.pathsep):
        add_dir(part.strip().strip('"'))
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData", "AppData"):
        base = os.environ.get(env_name)
        if base:
            add_dir(Path(base) / "nodejs")
            add_dir(Path(base) / "npm")
    add_dir(r"C:\Program Files\nodejs")
    add_dir(os.path.expandvars(r"%ProgramFiles%\nodejs"))
    add_dir(os.path.expandvars(r"%AppData%\npm"))
    return dirs


def _resolve_launcher(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    suffix = ".cmd" if os.name == "nt" else ""
    for directory in _nodejs_search_dirs():
        for candidate_name in (name, f"{name}{suffix}", f"{name}.exe"):
            candidate = directory / candidate_name
            if candidate.is_file():
                return str(candidate)
    return None


def resolve_tokscale_argv() -> list[str] | None:
    for resolver in (_app_bundled_tokscale_binary, _bundled_tokscale_binary):
        bundled = resolver()
        if bundled is None:
            continue
        if bundled.suffix.lower() == ".js":
            node = _resolve_launcher("node")
            if node:
                return [node, str(bundled)]
        else:
            return [str(bundled)]
    npx = _resolve_launcher("npx")
    if npx:
        return [npx, "-y", "tokscale@latest"]
    bunx = _resolve_launcher("bunx")
    if bunx:
        return [bunx, "tokscale@latest"]
    return None


def _needs_windows_shell(argv: list[str]) -> bool:
    if os.name != "nt" or not argv:
        return False
    executable = Path(argv[0])
    return executable.suffix.lower() in {".cmd", ".bat"}


def _parse_sync_json(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        raise ValueError("tokscale cursor sync 无输出")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        starts = [index for index in (text.find("{"), text.find("[")) if index >= 0]
        if not starts:
            raise ValueError(f"无法解析 tokscale JSON：{text[:200]}")
        payload = json.loads(text[min(starts) :])
    if not isinstance(payload, dict):
        raise ValueError("tokscale cursor sync 返回非对象 JSON")
    return payload


def sync_via_tokscale_cli(timeout_sec: int = 90) -> dict[str, Any]:
    argv = resolve_tokscale_argv()
    if not argv:
        return {
            "synced": False,
            "rows": 0,
            "error": "未找到 tokscale CLI（需要 npx 或 token-monitor 内置二进制）。",
            "errorKind": "tokscale_missing",
        }
    if not tokscale_credentials_path().exists():
        return {
            "synced": False,
            "rows": 0,
            "error": "未找到 tokscale 凭证文件，无法执行 tokscale cursor sync。",
            "errorKind": "not_authenticated",
        }

    command = [*argv, "cursor", "sync", "--json"]
    run_kwargs: dict[str, Any] = {
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout_sec,
        "check": False,
    }
    if _needs_windows_shell(command):
        run_kwargs["shell"] = True
        command = subprocess.list2cmdline(command)
    elif os.name == "nt":
        run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        completed = subprocess.run(command, **run_kwargs)
    except subprocess.TimeoutExpired:
        return {
            "synced": False,
            "rows": 0,
            "error": f"tokscale cursor sync 超时（>{timeout_sec}s）。",
            "errorKind": "timeout",
        }
    except OSError as exc:
        return {
            "synced": False,
            "rows": 0,
            "error": f"tokscale cursor sync 启动失败：{exc}",
            "errorKind": "tokscale_missing",
        }

    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        return {
            "synced": False,
            "rows": 0,
            "error": detail or f"tokscale cursor sync 退出码 {completed.returncode}",
            "errorKind": "tokscale_failed",
        }

    try:
        payload = _parse_sync_json(completed.stdout)
    except ValueError as exc:
        return {
            "synced": False,
            "rows": 0,
            "error": str(exc),
            "errorKind": "tokscale_failed",
        }

    synced = bool(payload.get("synced"))
    rows = int(payload.get("rows") or 0)
    error = payload.get("error")
    result: dict[str, Any] = {
        "synced": synced,
        "rows": rows,
        "path": str(default_tokscale_cache_dir() / "usage.csv"),
        "engine": "tokscale-cli",
    }
    if error:
        result["error"] = str(error)
    if not synced:
        result["errorKind"] = "tokscale_failed"
        if "error" not in result:
            result["error"] = "tokscale cursor sync 未成功"
    return result
