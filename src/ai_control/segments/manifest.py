from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any


SEGMENT_SCHEMA = "agentsight_segment_v1"
SEGMENT_INDEX_SCHEMA = "agentsight_segment_index_v1"
CONTAINER_MODEL = "proto_directory_now_single_file_later"
BINARY_CONTAINER_MODEL = "single_file_agseg_v1"
CODEC_MODEL = "keyframe_plus_pframe_delta_crop"
FRAME_RATE_MODEL = "variable_timestamped_frames"
ALLOWED_FRAME_KINDS = {"keyframe", "pframe_delta", "pframe_no_change"}
ALLOWED_FRAME_SOURCES = {"idle", "pre_do", "post_do", "screen", "look", "manual_import", "diagnostic"}


def boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_image_rgba(image: Any) -> str:
    rgba = image.convert("RGBA")
    width, height = rgba.size
    digest = hashlib.sha256()
    digest.update(b"agentsight-rgba-v1")
    digest.update(str(width).encode("ascii"))
    digest.update(b"x")
    digest.update(str(height).encode("ascii"))
    digest.update(b"\0")
    digest.update(rgba.tobytes())
    return digest.hexdigest()


def build_empty_segment_manifest(
    *,
    segment_id: str,
    started_at_iso: str | None = None,
    ended_at_iso: str | None = None,
) -> dict[str, Any]:
    return {
        "object_type": "AgentSightSegmentManifest",
        "schema": SEGMENT_SCHEMA,
        "segment_id": segment_id,
        "container_model": CONTAINER_MODEL,
        "codec_model": CODEC_MODEL,
        "frame_rate_model": FRAME_RATE_MODEL,
        "raw_frames_are_canonical_evidence": True,
        "derived_review_video_is_canonical": False,
        "b_frames_used": False,
        "h264_used": False,
        "started_at_iso": started_at_iso or _now_iso(),
        "ended_at_iso": ended_at_iso,
        "frame_count": 0,
        "keyframe_count": 0,
        "pframe_delta_count": 0,
        "pframe_no_change_count": 0,
        "frames": [],
        "integrity": {
            "manifest_hash_algorithm": "sha256",
            "frame_hash_algorithm": "sha256",
            "hash_scope": "restored_full_frame_rgba_pixels_with_size_v1",
        },
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": boundary_facts(),
    }


def build_empty_segment_index(*, segment_id: str) -> dict[str, Any]:
    return {
        "object_type": "AgentSightSegmentIndex",
        "schema": SEGMENT_INDEX_SCHEMA,
        "segment_id": segment_id,
        "frame_count": 0,
        "time_index": [],
        "keyframes": [],
        "events": [],
    }


def build_frame_record(
    *,
    frame_id: str,
    timestamp_iso: str,
    timestamp_monotonic_ms: int,
    frame_index: int,
    frame_kind: str,
    source: str,
    width: int,
    height: int,
    full_frame_sha256: str,
    event_id: str | None = None,
    nearest_keyframe_id: str | None = None,
    previous_frame_id: str | None = None,
    delta_bbox: dict[str, int] | None = None,
    keyframe_blob_ref: str | None = None,
    delta_blob_ref: str | None = None,
    raw_or_derived: str = "raw",
    cursor_mode: str = "none",
    capture_content_degenerate: bool = False,
    screen_region: dict[str, int] | None = None,
    coordinate_system: str | None = None,
) -> dict[str, Any]:
    record = {
        "frame_id": frame_id,
        "timestamp_iso": timestamp_iso,
        "timestamp_monotonic_ms": int(timestamp_monotonic_ms),
        "frame_index": int(frame_index),
        "frame_kind": frame_kind,
        "source": source,
        "event_id": event_id,
        "nearest_keyframe_id": nearest_keyframe_id,
        "previous_frame_id": previous_frame_id,
        "delta_bbox": delta_bbox,
        "keyframe_blob_ref": keyframe_blob_ref,
        "delta_blob_ref": delta_blob_ref,
        "full_frame_sha256": full_frame_sha256,
        "raw_or_derived": raw_or_derived,
        "cursor_mode": cursor_mode,
        "capture_content_degenerate": bool(capture_content_degenerate),
        "width": int(width),
        "height": int(height),
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
    }
    if screen_region is not None:
        record["screen_region"] = {
            "x": int(screen_region.get("x", 0)),
            "y": int(screen_region.get("y", 0)),
            "w": int(screen_region.get("w", screen_region.get("width", width))),
            "h": int(screen_region.get("h", screen_region.get("height", height))),
        }
    if coordinate_system:
        record["coordinate_system"] = str(coordinate_system)
    return record


def validate_segment_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if manifest.get("schema") != SEGMENT_SCHEMA:
        errors.append("schema_mismatch")
    if manifest.get("container_model") not in {CONTAINER_MODEL, BINARY_CONTAINER_MODEL}:
        errors.append("container_model_mismatch")
    if manifest.get("codec_model") != CODEC_MODEL:
        errors.append("codec_model_mismatch")
    if manifest.get("frame_rate_model") != FRAME_RATE_MODEL:
        errors.append("frame_rate_model_mismatch")
    if manifest.get("raw_frames_are_canonical_evidence") is not True:
        errors.append("raw_frames_not_canonical")
    if manifest.get("derived_review_video_is_canonical") is not False:
        errors.append("derived_review_video_marked_canonical")
    if manifest.get("h264_used") is not False:
        errors.append("h264_must_not_be_canonical")
    if manifest.get("b_frames_used") is not False:
        errors.append("b_frames_not_allowed_v1")

    frames = manifest.get("frames")
    if not isinstance(frames, list):
        errors.append("frames_not_list")
        frames = []
    keyframes = 0
    pframe_delta = 0
    pframe_no_change = 0
    frame_ids: set[str] = set()
    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            errors.append(f"frame_{index}_not_object")
            continue
        frame_id = str(frame.get("frame_id") or "")
        if not frame_id:
            errors.append(f"frame_{index}_missing_frame_id")
        elif frame_id in frame_ids:
            errors.append(f"frame_{index}_duplicate_frame_id")
        frame_ids.add(frame_id)
        kind = str(frame.get("frame_kind") or "")
        if kind not in ALLOWED_FRAME_KINDS:
            errors.append(f"frame_{index}_invalid_kind")
        if frame.get("source") not in ALLOWED_FRAME_SOURCES:
            errors.append(f"frame_{index}_invalid_source")
        if frame.get("raw_or_derived") != "raw":
            errors.append(f"frame_{index}_not_raw")
        if frame.get("tool_asserts_business_success") is not False:
            errors.append(f"frame_{index}_asserts_business_success")
        if frame.get("tool_asserts_causality") is not False:
            errors.append(f"frame_{index}_asserts_causality")
        if frame.get("tool_asserts_target_hit") is not False:
            errors.append(f"frame_{index}_asserts_target_hit")
        if kind == "keyframe":
            keyframes += 1
            if not frame.get("keyframe_blob_ref"):
                errors.append(f"frame_{index}_keyframe_missing_blob")
        elif kind == "pframe_delta":
            pframe_delta += 1
            if not frame.get("nearest_keyframe_id"):
                errors.append(f"frame_{index}_pframe_missing_keyframe")
            if not frame.get("previous_frame_id"):
                errors.append(f"frame_{index}_pframe_missing_previous")
            if not isinstance(frame.get("delta_bbox"), dict):
                errors.append(f"frame_{index}_pframe_missing_delta_bbox")
            if not frame.get("delta_blob_ref"):
                errors.append(f"frame_{index}_pframe_missing_delta_blob")
        elif kind == "pframe_no_change":
            pframe_no_change += 1
            if not frame.get("previous_frame_id"):
                errors.append(f"frame_{index}_no_change_missing_previous")
    if manifest.get("frame_count") != len(frames):
        errors.append("frame_count_mismatch")
    if manifest.get("keyframe_count") != keyframes:
        errors.append("keyframe_count_mismatch")
    if manifest.get("pframe_delta_count") != pframe_delta:
        errors.append("pframe_delta_count_mismatch")
    if manifest.get("pframe_no_change_count") != pframe_no_change:
        errors.append("pframe_no_change_count_mismatch")

    return {
        "object_type": "AgentSightSegmentManifestValidationReport",
        "schema": SEGMENT_SCHEMA,
        "valid": not errors,
        "errors": errors,
        "frame_count": len(frames),
        "keyframe_count": keyframes,
        "pframe_delta_count": pframe_delta,
        "pframe_no_change_count": pframe_no_change,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
