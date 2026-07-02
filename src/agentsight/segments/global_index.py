from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from agentsight.segments.binary_container import BINARY_CONTAINER_MODEL, BinarySegmentReader
from agentsight.segments.manifest import boundary_facts
from agentsight.segments.mkv_container import MKV_CONTAINER_MODEL


GLOBAL_SEGMENT_FRAME_INDEX_SCHEMA = "agentsight_global_segment_frame_index_v1"


def build_global_segment_frame_index(root: str | Path) -> dict[str, Any]:
    root_path = Path(root)
    frames: list[dict[str, Any]] = []
    skipped_segments: list[dict[str, Any]] = []
    unfinalized_segments: list[dict[str, Any]] = []
    for index_path in sorted(root_path.rglob("segments/*.frames.jsonl")):
        try:
            records = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception as exc:
            skipped_segments.append(
                _skipped_segment(
                    path=index_path,
                    storage_format="mkv_vfr",
                    error=exc,
                )
            )
            continue
        segment_path = _segment_path_from_index(index_path)
        finalization = _segment_finalization_state(segment_path, index_path)
        if not finalization["segment_finalized"]:
            unfinalized_segments.append(finalization["report"])
        for record in records:
            if not isinstance(record, dict) or not record.get("frame_id"):
                continue
            timestamp_iso = str(record.get("timestamp_iso") or "")
            frames.append(
                {
                    "segment_id": str(record.get("segment_id") or segment_path.stem),
                    "storage_format": "mkv_vfr",
                    "canonical_source": MKV_CONTAINER_MODEL,
                    "segment_path_abs": str(segment_path.resolve()),
                    "manifest_path_abs": str(segment_path.with_suffix(".manifest.json").resolve()),
                    "index_path_abs": str(index_path.resolve()),
                    "manifest_embedded": False,
                    "segment_finalized": finalization["segment_finalized"],
                    "segment_finalization_status": finalization["status"],
                    "frame_id": record.get("frame_id"),
                    "frame_index": record.get("frame_index"),
                    "logical_frame_id": record.get("logical_frame_id", record.get("frame_id")),
                    "logical_frame_index": record.get("logical_frame_index", record.get("frame_index")),
                    "physical_frame_id": record.get("physical_frame_id", record.get("frame_id")),
                    "physical_frame_index": record.get("physical_frame_index", record.get("frame_index")),
                    "duplicate_of_frame_id": record.get("duplicate_of_frame_id"),
                    "logical_duplicate": bool(record.get("logical_duplicate")),
                    "frame_hash_sha256": record.get("frame_hash_sha256"),
                    "frame_kind": record.get("frame_kind"),
                    "source": record.get("source"),
                    "event_id": record.get("event_id"),
                    "timestamp_iso": timestamp_iso,
                    "timestamp_epoch_ms": int(record.get("timestamp_ms") or _parse_iso_ms(timestamp_iso)),
                    "segment_restore_ref": {
                        "storage_format": "mkv_vfr",
                        "segment_path": str(segment_path.resolve()),
                        "index_path": str(index_path.resolve()),
                        "frame_id": record.get("frame_id"),
                        "logical_frame_id": record.get("logical_frame_id", record.get("frame_id")),
                        "logical_frame_index": record.get("logical_frame_index", record.get("frame_index")),
                        "physical_frame_id": record.get("physical_frame_id", record.get("frame_id")),
                        "physical_frame_index": record.get("physical_frame_index", record.get("frame_index")),
                        "duplicate_of_frame_id": record.get("duplicate_of_frame_id"),
                        "logical_duplicate": bool(record.get("logical_duplicate")),
                        "timestamp_ms": int(record.get("timestamp_ms") or _parse_iso_ms(timestamp_iso)),
                        "pts_ms": record.get("pts_ms"),
                    },
                    "raw_or_derived": "raw",
                    "capture_content_degenerate": bool(record.get("capture_content_degenerate")),
                    "screen_region": record.get("screen_region"),
                    "coordinate_system": record.get("coordinate_system"),
                    "width": record.get("width"),
                    "height": record.get("height"),
                }
            )
    for segment_path in sorted(root_path.rglob("segments/*.agseg")):
        try:
            manifest = BinarySegmentReader(segment_path).manifest
            records = manifest.get("frames") or []
        except Exception as exc:
            skipped_segments.append(
                _skipped_segment(
                    path=segment_path,
                    storage_format="binary_agseg",
                    error=exc,
                )
            )
            continue
        for record in records:
            if not isinstance(record, dict) or not record.get("frame_id"):
                continue
            timestamp_iso = str(record.get("timestamp_iso") or "")
            timestamp_ms = record.get("timestamp_ms") or record.get("captured_at_epoch_ms") or _parse_iso_ms(timestamp_iso)
            frames.append(
                {
                    "segment_id": str(record.get("segment_id") or manifest.get("segment_id") or segment_path.stem),
                    "storage_format": "binary_agseg",
                    "canonical_source": BINARY_CONTAINER_MODEL,
                    "segment_path_abs": str(segment_path.resolve()),
                    "manifest_path_abs": str(segment_path.resolve()),
                    "index_path_abs": str(segment_path.resolve()),
                    "manifest_embedded": True,
                    "frame_id": record.get("frame_id"),
                    "frame_index": record.get("frame_index"),
                    "frame_kind": record.get("frame_kind"),
                    "source": record.get("source"),
                    "event_id": record.get("event_id"),
                    "timestamp_iso": timestamp_iso,
                    "timestamp_epoch_ms": int(timestamp_ms),
                    "segment_restore_ref": {
                        "storage_format": "binary_agseg",
                        "segment_path": str(segment_path.resolve()),
                        "frame_id": record.get("frame_id"),
                    },
                    "raw_or_derived": "raw",
                    "capture_content_degenerate": bool(record.get("capture_content_degenerate")),
                    "screen_region": record.get("screen_region"),
                    "coordinate_system": record.get("coordinate_system"),
                    "width": record.get("width"),
                    "height": record.get("height"),
                }
            )
    frames.sort(key=lambda frame: int(frame.get("timestamp_epoch_ms") or 0))
    return {
        "object_type": "AgentSightGlobalSegmentFrameIndex",
        "schema": GLOBAL_SEGMENT_FRAME_INDEX_SCHEMA,
        "root_path_abs": str(root_path.resolve()),
        "frame_count": len(frames),
        "frames": frames,
        "skipped_segment_count": len(skipped_segments),
        "skipped_segments": skipped_segments,
        "unfinalized_segment_count": len(unfinalized_segments),
        "unfinalized_segments": unfinalized_segments,
        "no_capture_performed": True,
        "index_canonical": False,
        "raw_segments_are_canonical_evidence": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _segment_finalization_state(segment_path: Path, index_path: Path) -> dict[str, Any]:
    manifest_path = segment_path.with_suffix(".manifest.json")
    finalized = True
    status = "finalized"
    detail: str | None = None
    if not manifest_path.exists():
        finalized = False
        status = "unfinalized"
        detail = "manifest_missing"
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            finalized = bool(manifest.get("finalized"))
            status = "finalized" if finalized else "unfinalized"
        except Exception as exc:
            finalized = False
            status = "unfinalized"
            detail = f"manifest_unreadable:{type(exc).__name__}:{exc}"
    return {
        "segment_finalized": finalized,
        "status": status,
        "report": {
            "object_type": "AgentSightUnfinalizedSegment",
            "schema": GLOBAL_SEGMENT_FRAME_INDEX_SCHEMA,
            "status": status,
            "path_abs": str(segment_path.resolve()),
            "index_path_abs": str(index_path.resolve()),
            "manifest_path_abs": str(manifest_path.resolve()),
            "storage_format": "mkv_vfr",
            "detail": detail,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            "boundary": boundary_facts(),
        },
    }


def _skipped_segment(*, path: Path, storage_format: str, error: Exception) -> dict[str, Any]:
    return {
        "object_type": "AgentSightSkippedSegment",
        "schema": "agentsight_global_segment_frame_index_v1",
        "status": "skipped",
        "path_abs": str(path.resolve()),
        "storage_format": storage_format,
        "error_type": type(error).__name__,
        "detail": str(error),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": boundary_facts(),
    }


def _segment_path_from_index(index_path: Path) -> Path:
    name = index_path.name
    if name.endswith(".frames.jsonl"):
        return index_path.with_name(name[: -len(".frames.jsonl")] + ".mkv")
    return index_path.with_suffix(".mkv")


def query_segment_frames_near_time(index: dict[str, Any], requested_time: Any) -> dict[str, Any]:
    requested_ms = _requested_time_ms(requested_time)
    skipped_segments = list(index.get("skipped_segments") or [])
    frames = [frame for frame in index.get("frames") or [] if isinstance(frame.get("timestamp_epoch_ms"), int)]
    if requested_ms is None:
        return _near_result(
            requested_time=requested_time,
            query_status="invalid_time",
            requested_ms=None,
            frames=[],
            skipped_segments=skipped_segments,
        )
    before = max((frame for frame in frames if frame["timestamp_epoch_ms"] <= requested_ms), key=lambda frame: frame["timestamp_epoch_ms"], default=None)
    after = min((frame for frame in frames if frame["timestamp_epoch_ms"] >= requested_ms), key=lambda frame: frame["timestamp_epoch_ms"], default=None)
    nearest = min(frames, key=lambda frame: abs(frame["timestamp_epoch_ms"] - requested_ms), default=None)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for frame in (before, after):
        if frame is None:
            continue
        key = (str(frame.get("segment_path_abs")), str(frame.get("frame_id")))
        if key in seen:
            continue
        selected.append(frame)
        seen.add(key)
    if not selected and nearest is not None:
        selected = [nearest]
    result = _near_result(
        requested_time=requested_time,
        query_status="generated" if selected else "not_found",
        requested_ms=requested_ms,
        frames=selected,
        skipped_segments=skipped_segments,
    )
    result["before_frame"] = _near_frame(before, requested_ms=requested_ms, relation="before") if before else None
    result["after_frame"] = _near_frame(after, requested_ms=requested_ms, relation="after") if after else None
    result["nearest_frame"] = _near_frame(nearest, requested_ms=requested_ms, relation="nearest") if nearest else None
    return result


def _near_result(
    *,
    requested_time: Any,
    query_status: str,
    requested_ms: int | None,
    frames: list[dict[str, Any]],
    skipped_segments: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "object_type": "AgentSightGlobalSegmentFramesNearTime",
        "schema": "agentsight_global_segment_frames_near_time_v1",
        "query_status": query_status,
        "requested_time": requested_time,
        "requested_time_epoch_ms": requested_ms,
        "frame_count": len(frames),
        "frames": [_near_frame(frame, requested_ms=requested_ms or 0, relation="selected") for frame in frames],
        "skipped_segment_count": len(skipped_segments),
        "skipped_segments": skipped_segments,
        "no_capture_performed": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _near_frame(frame: dict[str, Any], *, requested_ms: int, relation: str) -> dict[str, Any]:
    actual_ms = int(frame.get("timestamp_epoch_ms") or 0)
    return {
        **frame,
        "relation": relation,
        "delta_ms": actual_ms - requested_ms,
        "segment_frame_id": frame.get("frame_id"),
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
    }


def _requested_time_ms(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(float(value) * 1000 if float(value) < 10_000_000_000 else float(value))
    if isinstance(value, str):
        return _parse_iso_ms(value)
    return None


def _parse_iso_ms(value: str) -> int:
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        return int(datetime.fromisoformat(normalized).timestamp() * 1000)
    except Exception:
        return 0
