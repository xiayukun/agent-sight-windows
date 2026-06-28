from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ai_control.tray.state import boundary_facts, default_agent_dir, default_evidence_root, default_tray_config_file, normalize_recording_policy, read_jsonc_file
from ai_control.tray.viewers import default_operation_log_file


STORAGE_QUOTA_SCHEMA = "agentsight_storage_quota_v1"


def apply_storage_quota(
    *,
    root: Path | None = None,
    config: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    agent_dir = root or default_agent_dir()
    runs_root = agent_dir / "runs_host_agent"
    policy = config if isinstance(config, dict) else normalize_recording_policy(read_jsonc_file(default_tray_config_file()))
    max_storage_mb = int(policy.get("max_storage_mb") or 5120)
    min_free_disk_mb = int(policy.get("min_free_disk_mb") or 1024)
    before = _storage_snapshot(agent_dir)
    candidates = _quota_candidates(runs_root=runs_root, agent_dir=agent_dir)
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
    log_prune = _prune_operation_log_before(default_operation_log_file(), prune_logs_before_ms, dry_run=dry_run)
    after = _storage_snapshot(agent_dir)
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
        "operation_log_prune": log_prune,
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


def _quota_candidates(*, runs_root: Path, agent_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
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
            records.append(_candidate(segment, priority=20, reason="old_unpinned_segment"))
    records.sort(key=lambda item: (int(item["priority"]), float(item["mtime"]), str(item["path"])))
    return records


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
            index_path = path.with_suffix(".frames.jsonl")
            frames = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            manifest = {"frames": frames}
    except Exception:
        return None
    values = []
    for frame in manifest.get("frames") or []:
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


def _prune_operation_log_before(operation_log: Path, cutoff_ms: int | None, *, dry_run: bool) -> dict[str, Any]:
    if cutoff_ms is None or not operation_log.exists():
        return {"pruned": False, "reason": "no_cutoff_or_log_missing", "cutoff_ms": cutoff_ms}
    try:
        lines = operation_log.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {"pruned": False, "reason": "read_failed", "cutoff_ms": cutoff_ms}
    kept: list[str] = []
    removed = 0
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            kept.append(line)
            continue
        timestamp = _timestamp_ms_from_any(payload.get("timestamp_ms") or payload.get("created_at_ms") or payload.get("updated_at_ms"))
        if timestamp is not None and timestamp <= cutoff_ms:
            removed += 1
            continue
        kept.append(line)
    if not dry_run and removed:
        operation_log.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return {"pruned": bool(removed), "removed_count": removed, "kept_count": len(kept), "cutoff_ms": cutoff_ms, "dry_run": dry_run}


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
