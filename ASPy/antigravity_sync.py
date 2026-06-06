# -*- coding: utf-8 -*-
"""Sync Antigravity usage from running language_server into tokscale-style JSONL cache."""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from antigravity_connect import AntigravityConnection, detect_connections, rpc_request
from antigravity_paths import antigravity_data_roots

MANIFEST_VERSION = 1


@dataclass
class TrajectorySummary:
    session_id: str
    last_modified_ms: int | None
    step_count: int | None
    connection_fingerprint: str


@dataclass
class SessionCandidate:
    session_id: str
    last_modified_ms: int | None
    artifact_path: str | None = None


def sessions_dir(cache_dir: Path) -> Path:
    return cache_dir / "sessions"


def manifest_path(cache_dir: Path) -> Path:
    return cache_dir / "manifest.json"


def sanitize_session_id(session_id: str) -> str:
    sanitized = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in session_id.strip())
    trimmed = sanitized.strip("-")
    return trimmed or "session"


def session_artifact_file_stem(session_id: str) -> str:
    sanitized = sanitize_session_id(session_id)
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{sanitized}-{digest[:16]}"


def atomic_write_text(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".tmp-{path.name}-{os.getpid()}")
    temp.write_text(contents, encoding="utf-8")
    temp.replace(path)


def load_manifest(cache_dir: Path) -> dict[str, Any]:
    path = manifest_path(cache_dir)
    if not path.exists():
        return {"version": MANIFEST_VERSION, "syncedAt": None, "connections": [], "sessions": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": MANIFEST_VERSION, "syncedAt": None, "connections": [], "sessions": []}
    if not isinstance(payload, dict):
        return {"version": MANIFEST_VERSION, "syncedAt": None, "connections": [], "sessions": []}
    version = int(payload.get("version") or 0)
    if version > MANIFEST_VERSION:
        raise ValueError("manifest from newer tokscale version")
    if version < MANIFEST_VERSION:
        return {"version": MANIFEST_VERSION, "syncedAt": None, "connections": [], "sessions": []}
    payload.setdefault("connections", [])
    payload.setdefault("sessions", [])
    return payload


def save_manifest(cache_dir: Path, manifest: dict[str, Any]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(manifest_path(cache_dir), json.dumps(manifest, ensure_ascii=False, indent=2))


@contextmanager
def sync_lock(cache_dir: Path):
    cache_dir.mkdir(parents=True, exist_ok=True)
    lock_path = cache_dir / "sync.lock"
    acquired = False
    for _ in range(3):
        try:
            with lock_path.open("x", encoding="utf-8") as handle:
                handle.write(f"{os.getpid()} {int(datetime.now(timezone.utc).timestamp())}\n")
            acquired = True
            break
        except FileExistsError:
            lock_path.unlink(missing_ok=True)
    if not acquired:
        raise RuntimeError("无法获取 Antigravity 同步锁")
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def parse_timestamp_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        ts = int(value)
        return ts if ts > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            ts = int(text)
            return ts if ts > 0 else None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except ValueError:
            return None
    return None


def first_string(values: list[Any]) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def first_int(values: list[Any]) -> int | None:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def normalize_trajectory_summary(item: dict[str, Any], fingerprint: str) -> TrajectorySummary | None:
    session_id = first_string(
        [
            item.get("cascadeId"),
            item.get("trajectoryId"),
            item.get("id"),
            item.get("sessionId"),
        ]
    )
    if not session_id:
        return None
    return TrajectorySummary(
        session_id=session_id,
        last_modified_ms=parse_timestamp_value(
            first_string(
                [
                    item.get("lastModifiedTime"),
                    item.get("lastModified"),
                    item.get("updatedAt"),
                    item.get("modifiedAt"),
                ]
            )
        )
        or first_int([item.get("lastModifiedTime"), item.get("lastModified"), item.get("updatedAt"), item.get("modifiedAt")]),
        step_count=first_int([item.get("stepCount"), item.get("numSteps"), item.get("totalSteps")]),
        connection_fingerprint=fingerprint,
    )


def normalize_trajectory_summaries(response: dict[str, Any], fingerprint: str) -> list[TrajectorySummary]:
    items: list[Any] = []
    trajectory_summaries = response.get("trajectorySummaries")
    if isinstance(trajectory_summaries, list):
        items = trajectory_summaries
    elif isinstance(trajectory_summaries, dict):
        for key, value in trajectory_summaries.items():
            if isinstance(value, dict):
                entry = dict(value)
                entry.setdefault("cascadeId", key)
                items.append(entry)
    elif isinstance(response.get("cascadeTrajectories"), list):
        items = response["cascadeTrajectories"]
    summaries: list[TrajectorySummary] = []
    for item in items:
        if isinstance(item, dict):
            summary = normalize_trajectory_summary(item, fingerprint)
            if summary:
                summaries.append(summary)
    return summaries


def is_better_summary(next_summary: TrajectorySummary, current: TrajectorySummary) -> bool:
    next_modified = next_summary.last_modified_ms or 0
    current_modified = current.last_modified_ms or 0
    if next_modified != current_modified:
        return next_modified > current_modified
    return (next_summary.step_count or 0) > (current.step_count or 0)


def merge_summary(merged: dict[str, TrajectorySummary], summary: TrajectorySummary) -> None:
    existing = merged.get(summary.session_id)
    if existing is None or is_better_summary(summary, existing):
        merged[summary.session_id] = summary


def list_trajectory_summaries(connections: list[AntigravityConnection]) -> list[TrajectorySummary]:
    merged: dict[str, TrajectorySummary] = {}
    for connection in connections:
        response = rpc_request(connection, "GetAllCascadeTrajectories", {})
        if not response:
            continue
        for summary in normalize_trajectory_summaries(response, connection.fingerprint):
            merge_summary(merged, summary)
    values = list(merged.values())
    values.sort(
        key=lambda item: (
            -(item.last_modified_ms or 0),
            -(item.step_count or 0),
            item.session_id,
        )
    )
    return values


def file_modified_ms(path: Path) -> int | None:
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def latest_modified_in_dir(path: Path) -> int | None:
    latest = file_modified_ms(path)
    try:
        for entry in path.iterdir():
            modified = file_modified_ms(entry)
            if modified and (latest is None or modified > latest):
                latest = modified
    except OSError:
        return latest
    return latest


def scan_filesystem_session_candidates() -> list[SessionCandidate]:
    merged: dict[str, SessionCandidate] = {}
    for root in antigravity_data_roots():
        brain_dir = root / "brain"
        conversations_dir = root / "conversations"
        if brain_dir.is_dir():
            for entry in brain_dir.iterdir():
                if not entry.is_dir():
                    continue
                session_id = entry.name.strip()
                if not session_id:
                    continue
                candidate = SessionCandidate(session_id=session_id, last_modified_ms=latest_modified_in_dir(entry))
                merge_candidate(merged, candidate)
        if conversations_dir.is_dir():
            for entry in conversations_dir.glob("*.pb"):
                session_id = entry.stem.strip()
                if not session_id:
                    continue
                candidate = SessionCandidate(session_id=session_id, last_modified_ms=file_modified_ms(entry))
                merge_candidate(merged, candidate)
    values = list(merged.values())
    values.sort(key=lambda item: (-(item.last_modified_ms or 0), item.session_id))
    return values


def merge_candidate(target: dict[str, SessionCandidate], next_candidate: SessionCandidate) -> None:
    existing = target.get(next_candidate.session_id)
    if existing is None:
        target[next_candidate.session_id] = next_candidate
        return
    existing_modified = existing.last_modified_ms or 0
    next_modified = next_candidate.last_modified_ms or 0
    if next_modified > existing_modified:
        target[next_candidate.session_id] = next_candidate
    elif next_modified == existing_modified and existing.artifact_path and not next_candidate.artifact_path:
        return
    elif next_modified == existing_modified and next_candidate.artifact_path:
        target[next_candidate.session_id] = next_candidate


def merge_export_candidates(
    manifest: dict[str, Any],
    summaries: list[TrajectorySummary],
    filesystem: list[SessionCandidate],
) -> list[SessionCandidate]:
    merged: dict[str, SessionCandidate] = {}
    for summary in summaries:
        merge_candidate(
            merged,
            SessionCandidate(session_id=summary.session_id, last_modified_ms=summary.last_modified_ms),
        )
    for candidate in filesystem:
        merge_candidate(merged, candidate)
    for session in manifest.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        session_id = session.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            continue
        merge_candidate(
            merged,
            SessionCandidate(
                session_id=session_id,
                last_modified_ms=parse_timestamp_value(session.get("lastModifiedMs")),
                artifact_path=session.get("artifactPath") if isinstance(session.get("artifactPath"), str) else None,
            ),
        )
    values = list(merged.values())
    values.sort(key=lambda item: (-(item.last_modified_ms or 0), item.session_id))
    return values


def resolve_model_id(chat_model: dict[str, Any]) -> str:
    response_model = chat_model.get("responseModel")
    if isinstance(response_model, str) and response_model.strip():
        return response_model.strip()
    model = chat_model.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return "unknown"


def to_safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.strip():
        try:
            return max(0, int(float(value.strip())))
        except ValueError:
            return 0
    return 0


def normalize_session_metadata(session_id: str, metadata: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for meta in metadata:
        chat_model = meta.get("chatModel") if isinstance(meta.get("chatModel"), dict) else meta
        if not isinstance(chat_model, dict):
            continue
        model_id = resolve_model_id(chat_model)
        created_at = None
        start_meta = chat_model.get("chatStartMetadata")
        if isinstance(start_meta, dict):
            created_at = parse_timestamp_value(start_meta.get("createdAt"))
        lines.append(
            json.dumps(
                {
                    "type": "session_meta",
                    "sessionId": session_id,
                    "modelId": model_id,
                    "timestamp": created_at,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
        retry_infos = chat_model.get("retryInfos")
        if not isinstance(retry_infos, list):
            continue
        for retry in retry_infos:
            if not isinstance(retry, dict):
                continue
            usage = retry.get("usage") if isinstance(retry.get("usage"), dict) else retry
            if not isinstance(usage, dict):
                continue
            input_tokens = to_safe_int(usage.get("inputTokens"))
            output_tokens = to_safe_int(usage.get("outputTokens"))
            cache_read = to_safe_int(usage.get("cacheReadTokens"))
            reasoning = to_safe_int(usage.get("thinkingOutputTokens"))
            timestamp = parse_timestamp_value(usage.get("createdAt")) or parse_timestamp_value(usage.get("timestamp")) or created_at
            if input_tokens == 0 and output_tokens == 0 and cache_read == 0 and reasoning == 0:
                continue
            lines.append(
                json.dumps(
                    {
                        "type": "usage",
                        "sessionId": session_id,
                        "modelId": model_id,
                        "timestamp": timestamp,
                        "input": input_tokens,
                        "output": output_tokens,
                        "cacheRead": cache_read,
                        "cacheWrite": 0,
                        "reasoning": reasoning,
                        "responseId": usage.get("responseId") if isinstance(usage.get("responseId"), str) else None,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
    return lines


def try_fetch_session_artifact(summary: TrajectorySummary, connection: AntigravityConnection) -> dict[str, Any] | None:
    response = rpc_request(
        connection,
        "GetCascadeTrajectoryGeneratorMetadata",
        {"cascadeId": summary.session_id},
    )
    if not response:
        return None
    metadata = response.get("generatorMetadata")
    if not isinstance(metadata, list) or not metadata:
        return None
    meta_dicts = [item for item in metadata if isinstance(item, dict)]
    lines = normalize_session_metadata(summary.session_id, meta_dicts)
    if not lines:
        return None
    contents = "\n".join(lines) + "\n"
    artifact_hash = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    return {
        "contents": contents,
        "last_modified_ms": summary.last_modified_ms,
        "step_count": summary.step_count,
        "artifact_hash": f"sha256:{artifact_hash}",
    }


def fetch_session_artifact(summary: TrajectorySummary, connections: list[AntigravityConnection]) -> dict[str, Any] | None:
    ordered = list(connections)
    preferred = next((item for item in connections if item.fingerprint == summary.connection_fingerprint), None)
    if preferred:
        ordered = [preferred] + [item for item in connections if item is not preferred]
    for connection in ordered:
        artifact = try_fetch_session_artifact(summary, connection)
        if artifact:
            return artifact
    return None


def write_session_artifact(cache_dir: Path, session_id: str, contents: str) -> Path:
    target = sessions_dir(cache_dir) / f"{session_artifact_file_stem(session_id)}.jsonl"
    atomic_write_text(target, contents)
    return target


def relative_artifact_path(cache_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(cache_dir).as_posix()
    except ValueError:
        return path.as_posix()


def cleanup_stale_artifacts(cache_dir: Path, previous: dict[str, Any], next_manifest: dict[str, Any]) -> None:
    next_paths = {
        session.get("artifactPath")
        for session in next_manifest.get("sessions") or []
        if isinstance(session, dict) and isinstance(session.get("artifactPath"), str)
    }
    for session in previous.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        relative = session.get("artifactPath")
        if not isinstance(relative, str) or relative in next_paths:
            continue
        if not relative.startswith("sessions/") or not relative.endswith(".jsonl"):
            continue
        target = cache_dir / relative
        if target.is_file():
            target.unlink(missing_ok=True)


def sync_antigravity_cache(
    cache_dir: Path,
    connections: list[AntigravityConnection] | None = None,
) -> dict[str, Any]:
    cache_dir = Path(cache_dir)
    sessions_dir(cache_dir).mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "synced": False,
        "sessions": 0,
        "connections": 0,
        "summaries": 0,
        "filesystemCandidates": 0,
        "exportCandidates": 0,
        "error": None,
    }
    errors: list[str] = []
    try:
        with sync_lock(cache_dir):
            manifest = load_manifest(cache_dir)
            if connections is None:
                connections = detect_connections()
            result["connections"] = len(connections)
            if not connections:
                result["error"] = (
                    "Antigravity CLI 未运行，无法通过本机 Connect RPC 同步新数据；"
                    "将尝试读取本地 antigravity-cache 与 CLI transcript。"
                )
                if manifest.get("sessions"):
                    result["synced"] = True
                    result["sessions"] = len(manifest.get("sessions") or [])
                return result
            summaries = list_trajectory_summaries(connections)
            filesystem_candidates = scan_filesystem_session_candidates()
            export_candidates = merge_export_candidates(manifest, summaries, filesystem_candidates)
            result["summaries"] = len(summaries)
            result["filesystemCandidates"] = len(filesystem_candidates)
            result["exportCandidates"] = len(export_candidates)
            next_manifest: dict[str, Any] = {
                "version": MANIFEST_VERSION,
                "syncedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "connections": [
                    {"fingerprint": item.fingerprint, "pid": item.pid, "port": item.port}
                    for item in connections
                ],
                "sessions": [],
            }
            summary_map = {item.session_id: item for item in summaries}
            for candidate in export_candidates:
                entry = None
                summary = summary_map.get(candidate.session_id)
                if summary:
                    try:
                        artifact = fetch_session_artifact(summary, connections)
                    except Exception as exc:  # noqa: BLE001 - one flaky RPC must not drop other sessions
                        artifact = None
                        errors.append(f"{summary.session_id}: {exc}")
                    if artifact:
                        path = write_session_artifact(cache_dir, summary.session_id, artifact["contents"])
                        entry = {
                            "sessionId": summary.session_id,
                            "artifactPath": relative_artifact_path(cache_dir, path),
                            "lastModifiedMs": artifact.get("last_modified_ms"),
                            "stepCount": artifact.get("step_count"),
                            "connectionFingerprint": summary.connection_fingerprint,
                            "artifactHash": artifact.get("artifact_hash"),
                        }
                if entry is None and summary is None:
                    fallback = TrajectorySummary(
                        session_id=candidate.session_id,
                        last_modified_ms=candidate.last_modified_ms,
                        step_count=None,
                        connection_fingerprint=connections[0].fingerprint if connections else "",
                    )
                    try:
                        artifact = fetch_session_artifact(fallback, connections)
                    except Exception as exc:  # noqa: BLE001 - keep already exported artifacts usable
                        artifact = None
                        errors.append(f"{candidate.session_id}: {exc}")
                    if artifact:
                        path = write_session_artifact(cache_dir, candidate.session_id, artifact["contents"])
                        entry = {
                            "sessionId": candidate.session_id,
                            "artifactPath": relative_artifact_path(cache_dir, path),
                            "lastModifiedMs": artifact.get("last_modified_ms"),
                            "stepCount": artifact.get("step_count"),
                            "connectionFingerprint": fallback.connection_fingerprint,
                            "artifactHash": artifact.get("artifact_hash"),
                        }
                if entry is None:
                    for previous in manifest.get("sessions") or []:
                        if isinstance(previous, dict) and previous.get("sessionId") == candidate.session_id:
                            entry = previous
                            break
                if entry:
                    next_manifest["sessions"].append(entry)
            next_manifest["sessions"].sort(key=lambda item: str(item.get("sessionId") or ""))
            deduped: list[dict[str, Any]] = []
            seen_ids: set[str] = set()
            for session in next_manifest["sessions"]:
                session_id = str(session.get("sessionId") or "")
                if not session_id or session_id in seen_ids:
                    continue
                seen_ids.add(session_id)
                deduped.append(session)
            next_manifest["sessions"] = deduped
            save_manifest(cache_dir, next_manifest)
            cleanup_stale_artifacts(cache_dir, manifest, next_manifest)
            result["synced"] = True
            result["sessions"] = len(deduped)
            if errors:
                result["error"] = "; ".join(errors[:3])
            return result
    except Exception as exc:  # noqa: BLE001 - surface sync failure to UI
        result["error"] = str(exc)
        return result
