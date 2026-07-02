from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from agentsight.tray.state import boundary_facts, default_agent_dir, default_tray_config_file, normalize_recording_policy, read_jsonc_file


STORAGE_QUOTA_SCHEMA = "agentsight_storage_quota_v1"


def apply_storage_quota(
    *,
    root: Path | None = None,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    agent_dir, runs_root = _normalize_storage_quota_roots(root)
    policy = config if isinstance(config, dict) else normalize_recording_policy(read_jsonc_file(default_tray_config_file()))
    max_storage_mb = int(policy.get("max_storage_mb") or 5120)
    min_free_disk_mb = int(policy.get("min_free_disk_mb") or 5120)
    before = _storage_snapshot(agent_dir)
    operation_log_path = agent_dir / "operation-log.jsonl"
    operation_logs = _read_operation_log_entries(operation_log_path)
    pinned_segment_keys = _pinned_segment_keys(operation_logs)
    candidates, protected = _quota_candidates(runs_root=runs_root, agent_dir=agent_dir, pinned_segment_keys=pinned_segment_keys)
    deleted: list[dict[str, Any]] = []
    prune_logs_before_ms: int | None = None
    target_bytes = max(1, max_storage_mb) * 1024 * 1024
    min_free_bytes = max(1, min_free_disk_mb) * 1024 * 1024
    for candidate in candidates:
        current = _storage_snapshot(agent_dir)
        if current["total_bytes"] <= target_bytes and current["free_bytes"] >= min_free_bytes:
            break
        path = Path(str(candidate["path"]))
        record = dict(candidate)
        if dry_run:
            record["deleted"] = False
            record["dry_run"] = True
        else:
            try:
                segment_end_ms = _segment_end_ms(path) if path.suffix.lower() == ".mkv" else None
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=False)
                elif path.exists():
                    path.unlink()
                    _unlink_sidecars(path)
                record["deleted"] = True
                if segment_end_ms is not None:
                    prune_logs_before_ms = max(prune_logs_before_ms or 0, int(segment_end_ms))
            except Exception as exc:
                record["deleted"] = False
                record["error"] = str(exc)
        deleted.append(record)
    log_prune = _prune_operation_log_before(
        operation_log_path,
        prune_logs_before_ms,
        dry_run=dry_run,
        protected_segment_keys=_protected_segment_keys(protected),
    )
    after = _storage_snapshot(agent_dir)
    pruned_gap_report = {
        "object_type": "AgentSightStorageQuotaPrunedGapReport",
        "schema": STORAGE_QUOTA_SCHEMA,
        "cutoff_ms": prune_logs_before_ms,
        "deleted_segment_count": sum(1 for item in deleted if item.get("deleted") and item.get("reason") == "old_unpinned_segment"),
        "protected_segment_count": len(protected),
        "operation_log_pruned": bool(log_prune.get("pruned")),
        "protected_old_log_count": int(log_prune.get("protected_old_count") or 0),
        "sidecars_deleted_with_segments": all(
            not Path(str(item.get("path"))).with_suffix(suffix).exists()
            for item in deleted
            if item.get("deleted") and str(item.get("path", "")).lower().endswith(".mkv")
            for suffix in (".frames.jsonl", ".manifest.json")
        ),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    report = {
        "object_type": "AgentSightStorageQuotaReport",
        "schema": STORAGE_QUOTA_SCHEMA,
        "applied": not dry_run,
        "agent_dir": str(agent_dir),
        "runs_root": str(runs_root),
        "max_storage_mb": max_storage_mb,
        "min_free_disk_mb": min_free_disk_mb,
        "before": before,
        "after": after,
        "deleted": deleted,
        "protected": protected,
        "protected_count": len(protected),
        "operation_log_prune": log_prune,
        "pruned_gap_report": pruned_gap_report,
        "deleted_count": sum(1 for item in deleted if item.get("deleted")),
        "quota_ok": after["total_bytes"] <= target_bytes and after["free_bytes"] >= min_free_bytes,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    try:
        report_path = agent_dir / "storage-quota-last-report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report["report_path"] = str(report_path)
    except Exception:
        pass
    return report


def _normalize_storage_quota_roots(root: Path | None) -> tuple[Path, Path]:
    if root is None:
        agent_dir = default_agent_dir()
        return agent_dir, agent_dir / "runs_host_agent"
    requested = Path(root)
    requested_name = requested.name.lower()
    parent_name = requested.parent.name.lower()
    grandparent_name = requested.parent.parent.name.lower()
    if requested_name == "runs_host_agent":
        return requested.parent, requested
    if requested_name.startswith("visual-") and parent_name == "runs_host_agent":
        return requested.parent.parent, requested.parent
    if requested_name.startswith("session-") and parent_name == "runs_host_agent":
        return requested.parent.parent, requested.parent
    if parent_name.startswith("visual-") and grandparent_name == "runs_host_agent":
        return requested.parent.parent.parent, requested.parent.parent
    return requested, requested / "runs_host_agent"


def _quota_candidates(*, runs_root: Path, agent_dir: Path, pinned_segment_keys: set[str] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    protected: list[dict[str, Any]] = []
    pinned = pinned_segment_keys or set()
    derived_names = {
        "agent-sight-look-preview-cache",
    }
    for name in derived_names:
        path = agent_dir / name
        if path.exists():
            records.append(_candidate(path, priority=0, reason="derived_review_cache"))
    if runs_root.exists():
        for session in runs_root.glob("session-*"):
            if session.is_dir():
                records.append(_candidate(session, priority=10, reason="legacy_session_png_evidence"))
        for segment in runs_root.glob("**/*.mkv"):
            protection = _segment_protection(segment, pinned_segment_keys=pinned)
            if protection is not None:
                protected.append({**_candidate(segment, priority=20, reason=protection["reason"]), **protection})
            else:
                records.append(_candidate(segment, priority=20, reason="old_unpinned_segment"))
    records.sort(key=lambda item: (int(item["priority"]), float(item["mtime"]), str(item["path"])))
    protected.sort(key=lambda item: (int(item["priority"]), float(item["mtime"]), str(item["path"])))
    return records, protected


def _segment_protection(path: Path, *, pinned_segment_keys: set[str]) -> dict[str, Any] | None:
    manifest_path = path.with_suffix(".manifest.json")
    segment_id = path.stem
    manifest: dict[str, Any] = {}
    manifest_read_error: str | None = None
    if manifest_path.exists():
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
                segment_id = str(manifest.get("segment_id") or segment_id)
        except Exception as exc:
            manifest_read_error = f"{type(exc).__name__}:{exc}"
    elif path.with_suffix(".frames.jsonl").exists():
        return {
            "reason": "unfinalized_or_open_writer_segment",
            "keep_reason": "manifest_missing_treat_as_open_writer",
            "segment_id": segment_id,
            "manifest_path": str(manifest_path),
        }
    if _segment_keys(path, segment_id=segment_id) & pinned_segment_keys:
        return {
            "reason": "referenced_by_operation_log",
            "keep_reason": "pinned_segment",
            "segment_id": segment_id,
            "manifest_path": str(manifest_path),
        }
    if manifest_read_error is not None:
        return {
            "reason": "unfinalized_or_open_writer_segment",
            "keep_reason": "manifest_unreadable_treat_as_open_writer",
            "segment_id": segment_id,
            "manifest_path": str(manifest_path),
            "error": manifest_read_error,
        }
    if manifest.get("finalized") is False:
        return {
            "reason": "unfinalized_or_open_writer_segment",
            "keep_reason": "manifest_finalized_false",
            "segment_id": segment_id,
            "manifest_path": str(manifest_path),
        }
    return None


def _candidate(path: Path, *, priority: int, reason: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "priority": priority,
        "reason": reason,
        "size_bytes": _path_size(path),
        "mtime": path.stat().st_mtime if path.exists() else 0,
    }


def _storage_snapshot(path: Path) -> dict[str, Any]:
    total = _path_size(path)
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        free = int(usage.free)
    except Exception:
        free = -1
    return {"path": str(path), "total_bytes": total, "total_mb": round(total / 1024 / 1024, 2), "free_bytes": free, "free_mb": round(free / 1024 / 1024, 2) if free >= 0 else None}


def _path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += int(child.stat().st_size)
            except OSError:
                pass
    return total


def _segment_end_ms(path: Path) -> int | None:
    try:
        manifest_path = path.with_suffix(".manifest.json")
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        else:
            manifest = {}
        frames = manifest.get("frames") or []
        if not frames:
            index_path = path.with_suffix(".frames.jsonl")
            frames = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except Exception:
        return None
    values = []
    for frame in frames:
        if isinstance(frame, dict):
            parsed = _timestamp_ms_from_any(frame.get("timestamp_iso"))
            if parsed is not None:
                values.append(parsed)
            elif frame.get("timestamp_ms") is not None:
                values.append(int(frame.get("timestamp_ms") or 0))
            elif frame.get("timestamp_monotonic_ms") is not None:
                values.append(int(frame.get("timestamp_monotonic_ms") or 0))
    return max(values) if values else None


def _unlink_sidecars(path: Path) -> None:
    for suffix in (".frames.jsonl", ".manifest.json"):
        sidecar = path.with_suffix(suffix)
        try:
            sidecar.unlink(missing_ok=True)
        except Exception:
            pass


def _read_operation_log_entries(operation_log: Path) -> list[dict[str, Any]]:
    if not operation_log.exists():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = operation_log.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def _pinned_segment_keys(logs: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for payload in logs:
        keys.update(_segment_reference_keys(payload))
    return keys


def _protected_segment_keys(protected: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for item in protected:
        path_text = item.get("path")
        segment_id = item.get("segment_id")
        if isinstance(path_text, str) and path_text:
            keys.update(_segment_keys(Path(path_text), segment_id=str(segment_id) if segment_id else None))
        elif segment_id:
            keys.add(_key(str(segment_id)))
    return keys


def _segment_reference_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"segment_id", "source_segment_id"} and isinstance(child, str) and child:
                keys.add(_key(child))
            elif key in {"segment_path", "segment_path_abs", "source_segment_path", "path"} and isinstance(child, str) and child:
                keys.update(_segment_keys(Path(child)))
            else:
                keys.update(_segment_reference_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_segment_reference_keys(child))
    return keys


def _segment_keys(path: Path, *, segment_id: str | None = None) -> set[str]:
    keys = {_key(path.name), _key(path.stem), _key(str(path))}
    try:
        keys.add(_key(str(path.resolve())))
    except Exception:
        pass
    if segment_id:
        keys.add(_key(segment_id))
    return keys


def _entry_references_any_segment(payload: dict[str, Any], protected_segment_keys: set[str]) -> bool:
    return bool(_segment_reference_keys(payload) & protected_segment_keys)


def _key(value: str) -> str:
    return value.replace("\\", "/").lower()


def _prune_operation_log_before(operation_log: Path, cutoff_ms: int | None, *, dry_run: bool, protected_segment_keys: set[str] | None = None) -> dict[str, Any]:
    if cutoff_ms is None or not operation_log.exists():
        return {"pruned": False, "reason": "no_cutoff_or_log_missing", "cutoff_ms": cutoff_ms}
    protected_keys = protected_segment_keys or set()
    try:
        lines = operation_log.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"pruned": False, "reason": "read_failed", "cutoff_ms": cutoff_ms}
    kept: list[str] = []
    removed = 0
    protected_old = 0
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            kept.append(line)
            continue
        timestamp = _timestamp_ms_from_any(payload.get("timestamp_ms") or payload.get("created_at_ms") or payload.get("updated_at_ms"))
        if timestamp is not None and timestamp <= cutoff_ms:
            if protected_keys and _entry_references_any_segment(payload, protected_keys):
                protected_old += 1
                kept.append(line)
                continue
            removed += 1
            continue
        kept.append(line)
    if not dry_run and removed:
        operation_log.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {"pruned": bool(removed), "removed_count": removed, "kept_count": len(kept), "protected_old_count": protected_old, "cutoff_ms": cutoff_ms, "dry_run": dry_run}


def _timestamp_ms_from_any(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        raw = int(value)
        return raw if raw > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
        try:
            from datetime import datetime

            normalized = text.replace("Z", "+00:00")
            return int(datetime.fromisoformat(normalized).timestamp() * 1000)
        except Exception:
            return None
    return None


def _walk_values(value: Any):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value
