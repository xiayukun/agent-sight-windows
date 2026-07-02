from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agentsight.segments.manifest import boundary_facts


def plan_segment_prune(
    root: str | Path,
    *,
    retention_days: int,
    now_iso: str | None = None,
    operation_logs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root_path = Path(root)
    now = _parse_iso(now_iso) if now_iso else datetime.now().astimezone()
    cutoff = now - timedelta(days=max(1, int(retention_days)))
    pinned = _pinned_segment_ids(operation_logs or [])
    candidates = []
    pinned_segments = []
    kept = []
    for manifest_path in sorted(root_path.rglob("segments/segment-*/manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        segment_id = str(manifest.get("segment_id") or manifest_path.parent.name.removeprefix("segment-"))
        ended_at = _parse_iso(str(manifest.get("ended_at_iso") or manifest.get("started_at_iso") or ""))
        record = {
            "segment_id": segment_id,
            "segment_path_abs": str(manifest_path.parent.resolve()),
            "manifest_path_abs": str(manifest_path.resolve()),
            "ended_at_iso": manifest.get("ended_at_iso"),
            "frame_count": manifest.get("frame_count"),
        }
        if segment_id in pinned:
            pinned_segments.append({**record, "keep_reason": "referenced_by_operation_log"})
        elif ended_at < cutoff:
            candidates.append({**record, "delete_reason": "retention_days_expired"})
        else:
            kept.append({**record, "keep_reason": "within_retention_window"})
    for segment_path in sorted(root_path.rglob("segments/*.agseg")):
        try:
            from agentsight.segments.binary_container import BinarySegmentReader

            manifest = BinarySegmentReader(segment_path).manifest
        except Exception:
            continue
        segment_id = str(manifest.get("segment_id") or segment_path.stem)
        ended_at = _parse_iso(str(manifest.get("ended_at_iso") or manifest.get("started_at_iso") or ""))
        record = {
            "segment_id": segment_id,
            "storage_format": "binary_agseg",
            "delete_target_kind": "file",
            "segment_path_abs": str(segment_path.resolve()),
            "manifest_path_abs": None,
            "manifest_embedded": True,
            "ended_at_iso": manifest.get("ended_at_iso"),
            "frame_count": manifest.get("frame_count"),
        }
        if segment_id in pinned or segment_path.name in pinned or segment_path.stem in pinned:
            pinned_segments.append({**record, "keep_reason": "referenced_by_operation_log"})
        elif ended_at < cutoff:
            candidates.append({**record, "delete_reason": "retention_days_expired"})
        else:
            kept.append({**record, "keep_reason": "within_retention_window"})
    return {
        "object_type": "AgentSightSegmentPrunePlan",
        "schema": "agentsight_segment_retention_prune_v1",
        "root_path_abs": str(root_path.resolve()),
        "retention_days": max(1, int(retention_days)),
        "now_iso": now.isoformat(),
        "cutoff_iso": cutoff.isoformat(),
        "dry_run": True,
        "would_delete_count": len(candidates),
        "pinned_segment_count": len(pinned_segments),
        "kept_segment_count": len(kept),
        "would_delete": candidates,
        "pinned_segments": pinned_segments,
        "kept_segments": kept,
        "raw_media_deleted": False,
        "derived_review_artifacts_may_be_rebuilt": True,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": boundary_facts(),
    }


def apply_segment_prune_plan(plan: dict[str, Any], *, report_path: str | Path | None = None) -> dict[str, Any]:
    deleted = []
    skipped = []
    for item in plan.get("would_delete") or []:
        path_text = item.get("segment_path_abs") if isinstance(item, dict) else None
        if not isinstance(path_text, str):
            continue
        path = Path(path_text)
        if not path.exists():
            skipped.append({**item, "skip_reason": "already_missing"})
            continue
        if path.is_file():
            path.unlink()
        else:
            shutil.rmtree(path)
        deleted.append(item)
    root = Path(str(plan.get("root_path_abs") or "."))
    output = Path(report_path) if report_path else root / "segment-prune-report.json"
    report = {
        "object_type": "AgentSightSegmentPruneReport",
        "schema": "agentsight_segment_retention_prune_v1",
        "applied": True,
        "plan_schema": plan.get("schema"),
        "deleted_segment_count": len(deleted),
        "skipped_segment_count": len(skipped),
        "pinned_segment_count": plan.get("pinned_segment_count"),
        "deleted_segments": deleted,
        "skipped_segments": skipped,
        "pinned_segments": plan.get("pinned_segments") or [],
        "raw_media_deleted": bool(deleted),
        "canonical_evidence_deleted": bool(deleted),
        "delete_scope": "expired_unreferenced_segments",
        "created_at_ms": int(time.time() * 1000),
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": boundary_facts(),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    report["prune_report_path_abs"] = str(output.resolve())
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _pinned_segment_ids(logs: list[dict[str, Any]]) -> set[str]:
    pinned: set[str] = set()
    for payload in logs:
        entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else payload
        for ref in entry.get("segment_frame_refs") or [] if isinstance(entry, dict) else []:
            if isinstance(ref, dict) and ref.get("segment_id"):
                pinned.add(str(ref["segment_id"]))
            restore_ref = ref.get("restore_ref") if isinstance(ref, dict) else None
            if isinstance(restore_ref, dict) and isinstance(restore_ref.get("segment_path"), str):
                pinned.add(Path(restore_ref["segment_path"]).name.removeprefix("segment-"))
    return pinned


def _parse_iso(value: str) -> datetime:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        return parsed if parsed.tzinfo else parsed.astimezone()
    except Exception:
        return datetime.fromtimestamp(0).astimezone()
