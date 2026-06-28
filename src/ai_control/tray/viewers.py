from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ai_control.tray.state import boundary_facts, default_agent_dir, default_evidence_root, default_tray_config_file


OPERATION_LOG_SCHEMA = "ai_control_operation_log_v1"
TIMELINE_VIEWER_SCHEMA = "ai_control_timeline_viewer_v1"


def default_operation_log_file() -> Path:
    return default_agent_dir() / "operation-log.jsonl"


def default_look_preview_cache_dir() -> Path:
    return default_agent_dir() / "agent-sight-look-preview-cache"


def append_operation_log(entry: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    log_path = path or default_operation_log_file()
    payload = {
        "object_type": "AIControlOperationLogEntry",
        "schema": OPERATION_LOG_SCHEMA,
        "timestamp_ms": int(time.time() * 1000),
        "entry": entry,
        "host_input_sent": bool(entry.get("host_input_sent", False)),
        "host_sent_event_count": int(entry.get("host_sent_event_count") or 0),
        "tool_asserts_business_success": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
        "boundary": boundary_facts(),
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "object_type": "AIControlOperationLogAppendReport",
        "schema": OPERATION_LOG_SCHEMA,
        "operation_log_file": str(log_path),
        "written": True,
        "boundary": boundary_facts(),
    }


def public_operation_log_entry(
    *,
    route: str,
    request: dict[str, Any] | None,
    response: dict[str, Any] | None,
    http_status: int,
    caller_hint: str | None = None,
) -> dict[str, Any]:
    response_payload = response if isinstance(response, dict) else {}
    request_payload = request if isinstance(request, dict) else {}
    transient_image_content_present = _contains_mcp_image_content(response_payload)
    response_for_log = _drop_transient_image_content(response_payload)
    response_input = response_payload.get("input") if isinstance(response_payload.get("input"), dict) else {}
    public_op = request_payload.get("op") or route.lstrip("/")
    is_public_do = public_op == "do" or response_payload.get("schema") == "ai_control_do_v1" or response_payload.get("object_type") == "DoResult"
    host_input_sent = bool(
        response_input.get("sent", False)
        if is_public_do
        else response_payload.get(
            "host_input_sent",
            response_payload.get("input_executed", response_input.get("sent", False)),
        )
    )
    host_sent_event_count = int(
        response_input.get("host_event_count", 0)
        if is_public_do
        else response_payload.get(
            "host_sent_event_count",
            response_input.get("host_event_count", 0),
        )
        or 0
    )
    frame_refs = _frame_refs_from_public_response(response_payload)
    look_preview_refs = _look_preview_refs_from_public_response(response_payload)
    return {
        "route": route,
        "op": public_op,
        "request_id": request_payload.get("id"),
        "caller_hint": caller_hint,
        "http_status": http_status,
        "ok": response_payload.get("ok"),
        "status": response_payload.get("status"),
        "code": response_payload.get("code"),
        "service_status": response_payload.get("service_status"),
        "can_attempt_real_control": response_payload.get("can_attempt_real_control"),
        "control_blockers": response_payload.get("control_blockers"),
        "host_input_sent": host_input_sent,
        "host_sent_event_count": host_sent_event_count,
        "request_json": _redact_sensitive(request_payload),
        "response_json": _redact_sensitive(response_for_log),
        "transient_image_content_omitted": transient_image_content_present,
        "media_paths": _media_paths(response_payload),
        "frame_refs": frame_refs,
        "segment_frame_refs": frame_refs.get("all", []),
        "look_preview_refs": look_preview_refs,
        "tool_asserts_business_success": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
    }


def read_operation_log(*, path: Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    log_path = path or default_operation_log_file()
    if not log_path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            entries.append(payload)
    return entries[-max(1, limit) :]


def build_timeline_model(*, max_frames: int = 500, max_logs: int = 200) -> dict[str, Any]:
    opened_at_ms = int(time.time() * 1000)
    frames = [
        frame
        for frame in _scan_segment_frames(default_evidence_root(), max_frames=max_frames)
        if int(frame.get("timestamp_ms") or 0) <= opened_at_ms
    ]
    for index, frame in enumerate(frames):
        frame["index"] = index
    logs = read_operation_log(limit=max_logs)
    attachments = _attach_operation_logs_to_frames(frames, logs)
    return {
        "object_type": "AIControlTimelineModel",
        "schema": TIMELINE_VIEWER_SCHEMA,
        "generated_at_ms": opened_at_ms,
        "opened_at_ms": opened_at_ms,
        "right_wall_ms": opened_at_ms,
        "frames": frames,
        "operation_logs": logs,
        "operation_log_attachments": attachments,
        "frame_count": len(frames),
        "operation_log_count": len(logs),
        "timeline_config": {
            "time_axis_px_per_second": 10,
            "initial_window_seconds": 3600,
            "open_strategy": "latest_first_metadata_only",
            "right_wall_policy": "fixed_at_viewer_open_time",
            "thumbnail_batch_size": len(frames),
            "source": str(default_tray_config_file()),
        },
        "raw_or_derived_note": "Timeline opens metadata-first from MKV frame indexes. It decodes the selected frame in memory and does not judge target hit, causality, or business success.",
        "boundary": boundary_facts(),
    }


def _scan_segment_frames(root: Path, *, max_frames: int) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    frames: list[dict[str, Any]] = []
    try:
        from ai_control.segments.mkv_container import MKV_CONTAINER_MODEL, iter_mkv_frames

        records = iter_mkv_frames(root, max_frames=max_frames)
    except Exception:
        records = []
    for record in records:
        frame_id = str(record.get("frame_id") or "")
        segment_path = Path(str(record.get("segment_path") or ""))
        index_path = Path(str(record.get("index_path") or segment_path.with_suffix(".frames.jsonl")))
        if not frame_id or not segment_path:
            continue
        timestamp_ms = int(record.get("timestamp_ms") or _timestamp_ms_from_frame_record(record, fallback=index_path.stat().st_mtime if index_path.exists() else time.time()))
        frames.append(
            {
                "index": 0,
                "path": None,
                "uri": None,
                "name": f"{record.get('segment_id')}-{frame_id}",
                "timestamp_ms": timestamp_ms,
                "size_bytes": int(segment_path.stat().st_size) if segment_path.exists() else 0,
                "raw_or_derived": "raw_mkv_index_metadata",
                "preview_policy": "on_demand_only",
                "canonical_source": MKV_CONTAINER_MODEL,
                "storage_format": "mkv_vfr",
                "segment_id": record.get("segment_id"),
                "segment_frame_id": frame_id,
                "segment_frame_kind": record.get("frame_kind"),
                "segment_source": record.get("source"),
                "segment_path_abs": str(segment_path.resolve()),
                "segment_restore_ref": {
                    "storage_format": "mkv_vfr",
                    "segment_path": str(segment_path.resolve()),
                    "index_path": str(index_path.resolve()),
                    "frame_id": frame_id,
                    "pts_ms": record.get("pts_ms"),
                    "playback_pts_ms": record.get("playback_pts_ms"),
                    "playback_time_basis": record.get("playback_time_basis"),
                },
                "operation_log_indexes": [],
            }
        )
    frames.sort(key=lambda frame: int(frame.get("timestamp_ms") or 0))
    selected = frames[-max(1, max_frames) :]
    for index, frame in enumerate(selected):
        frame["index"] = index
    return selected


def _timestamp_ms_from_frame_record(record: dict[str, Any], *, fallback: float) -> int:
    value = record.get("timestamp_iso")
    if isinstance(value, str):
        try:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            return int(datetime.fromisoformat(normalized).timestamp() * 1000)
        except ValueError:
            pass
    return int(float(fallback) * 1000)


def _attach_operation_logs_to_frames(frames: list[dict[str, Any]], logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    if not frames:
        return attachments
    by_path = {str(frame.get("path")): frame for frame in frames if frame.get("path")}
    by_segment_frame_id = {
        str(frame.get("segment_frame_id")): frame
        for frame in frames
        if frame.get("segment_frame_id")
    }
    for log_index, payload in enumerate(logs):
        entry = payload.get("entry") if isinstance(payload.get("entry"), dict) else {}
        media_paths = [str(item) for item in entry.get("media_paths") or []]
        segment_frame_refs = [item for item in entry.get("segment_frame_refs") or [] if isinstance(item, dict)]
        segment_frame = next(
            (
                by_segment_frame_id.get(str(ref.get("segment_frame_id") or ref.get("frame_id")))
                for ref in segment_frame_refs
                if by_segment_frame_id.get(str(ref.get("segment_frame_id") or ref.get("frame_id")))
            ),
            None,
        )
        exact_frame = next((by_path.get(path) for path in media_paths if by_path.get(path)), None)
        timestamp_ms = int(payload.get("timestamp_ms") or 0)
        nearest_frame = segment_frame or exact_frame or min(
            frames,
            key=lambda frame: abs(int(frame.get("timestamp_ms") or 0) - timestamp_ms),
        )
        nearest_index = int(nearest_frame.get("index") or 0)
        delta_ms = int(nearest_frame.get("timestamp_ms") or 0) - timestamp_ms
        nearest_frame.setdefault("operation_log_indexes", []).append(log_index)
        attachment_basis = "segment_frame_id" if segment_frame else ("exact_media_path" if exact_frame else "nearest_timestamp")
        attachment = {
            "log_index": log_index,
            "frame_index": nearest_index,
            "attachment_basis": attachment_basis,
            "delta_ms": delta_ms,
            "route": entry.get("route"),
            "op": entry.get("op"),
            "http_status": entry.get("http_status"),
            "status": entry.get("status"),
            "code": entry.get("code"),
            "host_sent_event_count": entry.get("host_sent_event_count"),
            "segment_frame_id": nearest_frame.get("segment_frame_id"),
        }
        payload["timeline_attachment"] = attachment
        attachments.append(attachment)
    return attachments


def _media_paths(value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for key in ("path", "media_path_abs", "raw_media_path_abs"):
            item = value.get(key)
            if isinstance(item, str) and item not in paths:
                paths.append(item)
        for child in value.values():
            paths.extend(path for path in _media_paths(child) if path not in paths)
    elif isinstance(value, list):
        for child in value:
            paths.extend(path for path in _media_paths(child) if path not in paths)
    return paths


def _drop_transient_image_content(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return value
    return _drop_transient_image_content_recursive(value)


def _drop_transient_image_content_recursive(value: Any) -> Any:
    if isinstance(value, dict):
        stripped: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"content", "mcp_content", "_mcp_content"} and _is_mcp_image_content_list(item):
                continue
            stripped[key] = _drop_transient_image_content_recursive(item)
        return stripped
    if isinstance(value, list):
        return [_drop_transient_image_content_recursive(item) for item in value]
    return value


def _is_mcp_image_content_list(value: Any) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, dict) and item.get("type") == "image"
        for item in value
    )


def _contains_mcp_image_content(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (key in {"content", "mcp_content", "_mcp_content"} and _is_mcp_image_content_list(item))
            or _contains_mcp_image_content(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_mcp_image_content(item) for item in value)
    return False


def _look_preview_refs_from_public_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(response, dict):
        return []
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for record in _iter_view_records(response):
        view_id = str(record.get("view_id") or "")
        restore_ref = record.get("segment_restore_ref") if isinstance(record.get("segment_restore_ref"), dict) else None
        actual_region = record.get("actual_decoded_region") if isinstance(record.get("actual_decoded_region"), dict) else None
        requested_region = record.get("requested_screen_region") if isinstance(record.get("requested_screen_region"), dict) else None
        if not view_id:
            continue
        materialize_blocker = None
        if restore_ref is None:
            materialize_blocker = "missing_segment_restore_ref"
        elif actual_region is None:
            materialize_blocker = "actual_decoded_region_required"
        key = json.dumps(
            {
                "view_id": view_id,
                "restore_ref": restore_ref,
                "actual_decoded_region": actual_region,
                "requested_screen_region": requested_region,
                "scale_down": record.get("scale_down"),
                "blur_radius": record.get("blur_radius"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(
            {
                "schema": "agentsight_look_preview_descriptor_v1",
                "preview_kind": "on_demand_derived_review",
                "source": "view_record",
                "view_id": view_id,
                "source_frame_id": record.get("source_frame_id"),
                "segment_id": record.get("segment_id"),
                "segment_restore_ref": restore_ref,
                "region": dict(actual_region) if actual_region is not None else None,
                "region_basis": "actual_decoded_region" if actual_region is not None else None,
                "requested_screen_region": requested_region,
                "actual_decoded_region": actual_region,
                "output_image_size": record.get("output_image_size"),
                "scale_down": record.get("scale_down", 1),
                "blur_radius": record.get("blur_radius", 0),
                "cursor_mode": record.get("cursor_mode", "none"),
                "transform": record.get("transform"),
                "raw_or_derived": "derived_review_only",
                "canonical": False,
                "default_loaded": False,
                "requires_user_action": True,
                "cache_policy": "regenerable_derived_review_cache",
                "cache_file_written": False,
                "can_materialize_from_segment": materialize_blocker is None,
                "materialize_blocker": materialize_blocker,
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
                "tool_asserts_causality": False,
            }
        )
    return records


def _iter_view_records(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        candidate = value.get("view_record")
        if isinstance(candidate, dict):
            found.append(candidate)
        if value.get("schema") == "ai_control_view_index_v1" and isinstance(value.get("public_record"), dict):
            found.append(value["public_record"])
        for child in value.values():
            found.extend(_iter_view_records(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_iter_view_records(child))
    return found


def materialize_look_preview_cache(preview_ref: dict[str, Any], *, output_dir: Path | None = None) -> dict[str, Any]:
    output_root = output_dir or default_look_preview_cache_dir()
    if not isinstance(preview_ref, dict):
        return _look_preview_cache_blocked("invalid_preview_ref", output_root=output_root)
    restore_ref = preview_ref.get("segment_restore_ref")
    region = preview_ref.get("actual_decoded_region") if isinstance(preview_ref.get("actual_decoded_region"), dict) else None
    if not isinstance(restore_ref, dict):
        return _look_preview_cache_blocked("missing_segment_restore_ref", output_root=output_root, preview_ref=preview_ref)
    if not isinstance(region, dict):
        return _look_preview_cache_blocked("actual_decoded_region_required", output_root=output_root, preview_ref=preview_ref)
    output_root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(json.dumps(preview_ref, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    view_id = str(preview_ref.get("view_id") or "view").replace("/", "_").replace("\\", "_").replace(":", "_")
    output_path = output_root / f"{view_id}-{digest}.png"
    try:
        from ai_control.segments.decoder import decode_segment_region_to_png

        report = decode_segment_region_to_png(
            restore_ref,
            region=region,
            output_path=output_path,
            scale_down=preview_ref.get("scale_down", 1),
            blur_radius=preview_ref.get("blur_radius", 0),
        )
    except Exception as exc:
        return _look_preview_cache_blocked(str(exc), output_root=output_root, preview_ref=preview_ref)
    return {
        "object_type": "AgentSightLookPreviewCacheReport",
        "schema": "agentsight_look_preview_cache_v1",
        "status": "written" if output_path.exists() else "failed",
        "preview_kind": "on_demand_derived_review",
        "view_id": preview_ref.get("view_id"),
        "preview_cache_path": str(output_path),
        "preview_cache_uri": output_path.resolve().as_uri() if output_path.exists() else None,
        "raw_or_derived": "derived_review_only",
        "canonical": False,
        "cache_policy": "temporary_regenerable_derived_review_cache",
        "cache_can_be_pruned": True,
        "canonical_storage_remains": [".mkv", ".frames.jsonl", "operation-log.jsonl"],
        "enters_long_term_evidence_chain": False,
        "artifact_is_canonical_evidence": False,
        "cache_file_written": output_path.exists(),
        "exit_code": 0 if output_path.exists() else 2,
        "decode_report": report,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_asserts_business_success": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
        "boundary": boundary_facts(),
    }


def materialize_look_preview_cache_from_operation_log(
    *,
    log_index: int,
    preview_index: int = 0,
    path: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    logs = read_operation_log(path=path, limit=max(1, log_index + 1))
    if log_index < 0 or log_index >= len(logs):
        return _look_preview_cache_blocked(
            "operation_log_index_not_found",
            output_root=output_dir or default_look_preview_cache_dir(),
        )
    entry = logs[log_index].get("entry") if isinstance(logs[log_index].get("entry"), dict) else {}
    refs = [ref for ref in entry.get("look_preview_refs") or [] if isinstance(ref, dict)]
    if preview_index < 0 or preview_index >= len(refs):
        return _look_preview_cache_blocked(
            "look_preview_ref_index_not_found",
            output_root=output_dir or default_look_preview_cache_dir(),
        )
    report = materialize_look_preview_cache(refs[preview_index], output_dir=output_dir)
    report["operation_log_index"] = log_index
    report["look_preview_ref_index"] = preview_index
    report["operation_log_file"] = str(path or default_operation_log_file())
    return report


def _look_preview_cache_blocked(reason: str, *, output_root: Path, preview_ref: dict[str, Any] | None = None) -> dict[str, Any]:
    requested_screen_region = preview_ref.get("requested_screen_region") if isinstance(preview_ref, dict) else None
    actual_decoded_region = preview_ref.get("actual_decoded_region") if isinstance(preview_ref, dict) else None
    return {
        "object_type": "AgentSightLookPreviewCacheReport",
        "schema": "agentsight_look_preview_cache_v1",
        "status": "blocked",
        "reason": reason,
        "materialize_blocker": reason,
        "preview_kind": "on_demand_derived_review",
        "view_id": preview_ref.get("view_id") if isinstance(preview_ref, dict) else None,
        "requested_screen_region": requested_screen_region if isinstance(requested_screen_region, dict) else None,
        "actual_decoded_region": actual_decoded_region if isinstance(actual_decoded_region, dict) else None,
        "preview_cache_dir": str(output_root),
        "raw_or_derived": "derived_review_only",
        "canonical": False,
        "cache_policy": "regenerable_derived_review_cache",
        "cache_file_written": False,
        "exit_code": 2,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_asserts_business_success": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
        "boundary": boundary_facts(),
    }


def _frame_refs_from_public_response(response: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    refs = {"pre_action": [], "post_action": [], "looked_frames": [], "all": []}
    if not isinstance(response, dict):
        return refs
    seen: set[tuple[str, str, str]] = set()

    def append_ref(bucket: str, frame: dict[str, Any], *, relation: str) -> None:
        ref = _operation_frame_ref(frame, relation=relation)
        if not ref:
            return
        key = (
            str(ref.get("segment_id") or ""),
            str(ref.get("segment_frame_id") or ""),
            json.dumps(ref.get("restore_ref") or {}, ensure_ascii=False, sort_keys=True),
        )
        if key in seen:
            return
        seen.add(key)
        refs[bucket].append(ref)
        refs["all"].append(ref)

    post = response.get("post_observe") if isinstance(response.get("post_observe"), dict) else {}
    for frame in post.get("sampled_frames") or []:
        if not isinstance(frame, dict):
            continue
        append_ref("post_action", frame, relation="post_action")
    for key in ("screen_frame_index", "frames_near_time", "decoded_review", "clip", "diffs", "artifacts", "view_record"):
        value = response.get(key)
        for frame in _iter_frame_like_dicts(value):
            append_ref("looked_frames", frame, relation="looked_frame")
    return refs


def _operation_frame_ref(frame: dict[str, Any], *, relation: str) -> dict[str, Any] | None:
    segment_frame = frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else frame
    restore_ref = segment_frame.get("restore_ref") or segment_frame.get("segment_restore_ref")
    segment_frame_id = segment_frame.get("segment_frame_id") or segment_frame.get("frame_id") or segment_frame.get("source_frame_id")
    segment_id = segment_frame.get("segment_id")
    if not (segment_frame_id or segment_id or restore_ref):
        return None
    return {
        "relation": relation,
        "observation_ref": frame.get("observation_ref") or frame.get("observation_id"),
        "frame_index": frame.get("frame_index"),
        "segment_id": segment_id,
        "segment_frame_id": segment_frame_id,
        "segment_frame_status": segment_frame.get("status") or segment_frame.get("segment_frame_status"),
        "segment_source": segment_frame.get("source") or segment_frame.get("segment_source"),
        "frame_kind": segment_frame.get("frame_kind"),
        "restore_ref": restore_ref,
        "raw_or_derived": segment_frame.get("raw_or_derived", "raw"),
        "tool_asserts_business_success": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
    }


def _iter_frame_like_dicts(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if any(key in value for key in ("segment_frame", "segment_restore_ref", "segment_frame_id")):
            found.append(value)
        for child in value.values():
            found.extend(_iter_frame_like_dicts(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_iter_frame_like_dicts(child))
    return found


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in ("token", "authorization", "password", "secret", "api_key", "apikey")):
                redacted[key] = "<redacted>"
            else:
                redacted[key] = _redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value




