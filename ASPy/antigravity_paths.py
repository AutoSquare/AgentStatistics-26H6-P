# -*- coding: utf-8 -*-
"""Antigravity CLI / IDE local data directory helpers."""
from __future__ import annotations

from pathlib import Path


def gemini_root() -> Path:
    return Path.home() / ".gemini"


def antigravity_cli_root() -> Path:
    return gemini_root() / "antigravity-cli"


def antigravity_data_roots() -> list[Path]:
    """Preferred order: CLI data dir first, then legacy IDE installs."""
    gemini = gemini_root()
    ordered = (
        "antigravity-cli",
        "antigravity",
        "antigravity-ide",
        "antigravity-backup",
    )
    roots: list[Path] = []
    for name in ordered:
        root = gemini / name
        if root not in roots:
            roots.append(root)
    return roots


def cli_log_paths() -> list[Path]:
    root = antigravity_cli_root()
    paths: list[Path] = []
    primary = root / "cli.log"
    if primary.is_file():
        paths.append(primary)
    log_dir = root / "log"
    if log_dir.is_dir():
        for path in sorted(log_dir.glob("cli-*.log"), reverse=True):
            if path.is_file() and path not in paths:
                paths.append(path)
    return paths


def transcript_log_globs() -> list[str]:
    return ("transcript_full.jsonl", "transcript.jsonl")
