from __future__ import annotations

from typing import Any

from agentsight.visual_memory.frame_buffer import VisualFrameBuffer


VISUAL_MEMORY_QUERY_SCHEMA = "agentsight_p0j_visual_memory_query_v1"
VISUAL_MEMORY_READING_SUMMARY_SCHEMA = "agentsight_p0j3_visual_memory_query_reading_summary_v1"
VISUAL_MEMORY_QUERY_TYPES = {
    "status",
    "recent_frames",
    "frames_near_time",
    "sequence_change_index",
    "change_event",
    "event_window",
    "retention_status",
    "prune_unreferenced_buffer",
    "retention_class_projection",
    "storage_attention_summary",
}


def query_visual_memory(
    *,
    query_type: str,
    observations: dict[str, dict[str, Any]],
    frame_buffer: VisualFrameBuffer,
    sequence_id: str | None = None,
    change_event_id: str | None = None,
    max_entries: int = 8,
    before_count: int = 1,
    after_count: int = 1,
    requested_time: Any = None,
) -> dict[str, Any]:
    base = _base_query(query_type=query_type)
    limit = max(1, min(int(max_entries), 32))
    before_limit = max(0, min(int(before_count), 5))
    after_limit = max(0, min(int(after_count), 5))

    if query_type == "status":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "frame_buffer": frame_buffer.status(),
            "available_query_types": sorted(VISUAL_MEMORY_QUERY_TYPES),
        })

    if query_type == "recent_frames":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "max_entries": limit,
            "entries": frame_buffer.recent_entries(max_entries=limit),
            "entry_count": min(limit, len(frame_buffer.entries)),
            "frame_buffer_index_path_abs": str(frame_buffer.index_path.resolve()),
        })

    if query_type == "frames_near_time":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "frames_near_time": frame_buffer.frames_near_time(requested_time),
            "frame_buffer_index_path_abs": str(frame_buffer.index_path.resolve()),
        })

    if query_type == "retention_status":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "retention_status": frame_buffer.retention_status(),
        })

    if query_type == "prune_unreferenced_buffer":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "prune_plan": frame_buffer.plan_prune(),
            "prune_applied": False,
            "dry_run_only": True,
        })

    if query_type == "retention_class_projection":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "retention_class_projection": frame_buffer.retention_class_projection(max_entries=limit),
        })

    if query_type == "storage_attention_summary":
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "storage_attention_summary": frame_buffer.storage_attention_summary(max_entries=limit),
        })

    sequence = _sequence(observations, sequence_id)
    if sequence is None:
        return _with_reading_summary({
            **base,
            "query_status": "not_found",
            "not_found_reason": "sequence_id_missing_or_unknown",
            "sequence_id": sequence_id,
        })

    if query_type == "sequence_change_index":
        change_index = sequence.get("region_change_index")
        if not isinstance(change_index, dict):
            return _with_reading_summary({
                **base,
                "query_status": "not_found",
                "not_found_reason": "sequence_change_index_missing",
                "sequence_id": sequence.get("sequence_id"),
            })
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "sequence_id": sequence.get("sequence_id"),
            "region_change_index": change_index,
        })

    if query_type == "change_event":
        event = _change_event(sequence, change_event_id)
        if event is None:
            return _with_reading_summary({
                **base,
                "query_status": "not_found",
                "not_found_reason": "change_event_id_missing_or_unknown",
                "sequence_id": sequence.get("sequence_id"),
                "change_event_id": change_event_id,
            })
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "sequence_id": sequence.get("sequence_id"),
            "change_event_id": event.get("change_event_id"),
            "change_event": event,
        })

    if query_type == "event_window":
        event = _change_event(sequence, change_event_id)
        if event is None:
            return _with_reading_summary({
                **base,
                "query_status": "not_found",
                "not_found_reason": "change_event_id_missing_or_unknown",
                "sequence_id": sequence.get("sequence_id"),
                "change_event_id": change_event_id,
            })
        return _with_reading_summary({
            **base,
            "query_status": "generated",
            "sequence_id": sequence.get("sequence_id"),
            "change_event_id": event.get("change_event_id"),
            "event_window": _event_window(
                sequence=sequence,
                event=event,
                before_count=before_limit,
                after_count=after_limit,
            ),
        })

    return _with_reading_summary({
        **base,
        "query_status": "not_generated",
        "not_generated_reason": "unsupported_query_type",
    })


def _sequence(observations: dict[str, dict[str, Any]], sequence_id: str | None) -> dict[str, Any] | None:
    if not isinstance(sequence_id, str) or not sequence_id:
        return None
    sequence = observations.get(sequence_id)
    if isinstance(sequence, dict) and sequence.get("object_type") == "ObservationFrameSequence":
        return sequence
    return None


def _change_event(sequence: dict[str, Any], change_event_id: str | None) -> dict[str, Any] | None:
    if not isinstance(change_event_id, str) or not change_event_id:
        return None
    change_index = sequence.get("region_change_index")
    if not isinstance(change_index, dict):
        return None
    for event in change_index.get("change_events") or []:
        if isinstance(event, dict) and event.get("change_event_id") == change_event_id:
            return event
    return None


def _event_window(
    *,
    sequence: dict[str, Any],
    event: dict[str, Any],
    before_count: int,
    after_count: int,
) -> dict[str, Any]:
    frames = [frame for frame in sequence.get("frames", []) if isinstance(frame, dict)]
    after_index = int(event.get("after_frame_index") or 0)
    start_index = max(0, after_index - before_count)
    end_index = min(len(frames) - 1, after_index + after_count - 1)
    if not frames or end_index < start_index:
        selected: list[dict[str, Any]] = []
    else:
        selected = frames[start_index : end_index + 1]
    event_time = event.get("after_captured_at")
    return {
        "object_type": "VisualMemoryEventWindow",
        "schema": VISUAL_MEMORY_QUERY_SCHEMA,
        "source_sequence_id": sequence.get("sequence_id"),
        "source_change_event_id": event.get("change_event_id"),
        "event_after_frame_index": after_index,
        "event_after_captured_at": event_time,
        "before_count_requested": before_count,
        "after_count_requested": after_count,
        "window_start_frame_index": start_index if selected else None,
        "window_end_frame_index": end_index if selected else None,
        "frame_count": len(selected),
        "frames": [_event_window_frame(frame, event_time=event_time) for frame in selected],
        "returns_images": False,
        "raw_media_returned": False,
        "media_paths_returned": False,
        "metadata_only": True,
        "no_capture_performed": True,
        "tool_does_not_choose_frames_semantically": True,
    }


def _event_window_frame(frame: dict[str, Any], *, event_time: Any) -> dict[str, Any]:
    captured_at = frame.get("captured_at") or frame.get("timestamp")
    elapsed_ms = None
    if isinstance(captured_at, (int, float)) and isinstance(event_time, (int, float)):
        elapsed_ms = int((captured_at - event_time) * 1000)
    return {
        "frame_ref": frame.get("observation_id"),
        "sequence_id": frame.get("sequence_id"),
        "frame_index": frame.get("frame_index"),
        "captured_at": captured_at,
        "elapsed_from_event_ms": elapsed_ms,
        "media_ref": frame.get("media_ref"),
        "media_sha256": frame.get("media_sha256"),
        "media_size_bytes": frame.get("media_size_bytes"),
        "media_mime": frame.get("media_mime"),
        "width": frame.get("width"),
        "height": frame.get("height"),
        "screen_region": frame.get("screen_region"),
        "coordinate_system": frame.get("coordinate_system"),
        "raw_canonical": True,
        "media_access_returned": False,
        "image_bytes_returned": False,
        "derived_review_artifact": False,
    }


def _with_reading_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        **result,
        "ai_reading_summary": _reading_summary(result),
    }


def _reading_summary(result: dict[str, Any]) -> dict[str, Any]:
    query_type = str(result.get("query_type") or "")
    query_status = str(result.get("query_status") or "")
    return {
        "object_type": "VisualMemoryQueryReadingSummary",
        "schema": VISUAL_MEMORY_READING_SUMMARY_SCHEMA,
        "summary_kind": "deterministic_metadata_only",
        "freeform_language_generated": False,
        "query_type": query_type,
        "query_status": query_status,
        "facts": _reading_summary_facts(result),
        "recommended_query_types": _recommended_query_types(result),
        "boundary_codes": [
            "metadata_only",
            "query_only",
            "no_capture",
            "no_input",
            "no_images",
            "no_media_paths",
            "no_ui_semantics",
            "no_action_completion_claim",
            "external_interpretation_required",
        ],
        "metadata_only": True,
        "no_capture_performed": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "returns_images": False,
        "raw_media_returned": False,
        "media_paths_returned": False,
        "semantic_interpretation_in_tool": False,
        "action_completion_judged": False,
        "derived_from": "query_response_metadata",
    }


def _reading_summary_facts(result: dict[str, Any]) -> dict[str, Any]:
    query_type = result.get("query_type")
    if result.get("query_status") != "generated":
        return {
            "not_found_reason": result.get("not_found_reason"),
            "not_generated_reason": result.get("not_generated_reason"),
            "sequence_id": result.get("sequence_id"),
            "change_event_id": result.get("change_event_id"),
        }
    if query_type == "status":
        status = result.get("frame_buffer") if isinstance(result.get("frame_buffer"), dict) else {}
        return {
            "frame_buffer_enabled": status.get("enabled"),
            "frame_buffer_bounded": status.get("bounded"),
            "frame_buffer_entry_count": status.get("entry_count"),
            "available_query_type_count": len(result.get("available_query_types") or []),
        }
    if query_type == "recent_frames":
        entries = [entry for entry in result.get("entries") or [] if isinstance(entry, dict)]
        return {
            "max_entries": result.get("max_entries"),
            "entry_count": result.get("entry_count"),
            "entry_refs": [entry.get("buffer_entry_id") for entry in entries],
            "observation_refs": [entry.get("observation_ref") for entry in entries],
        }
    if query_type == "sequence_change_index":
        index = result.get("region_change_index") if isinstance(result.get("region_change_index"), dict) else {}
        return {
            "sequence_id": result.get("sequence_id"),
            "change_event_count": index.get("change_event_count"),
            "changed_frame_indexes": index.get("changed_frame_indexes"),
            "sampled_frame_count": index.get("sampled_frame_count"),
        }
    if query_type == "change_event":
        event = result.get("change_event") if isinstance(result.get("change_event"), dict) else {}
        return {
            "sequence_id": result.get("sequence_id"),
            "change_event_id": result.get("change_event_id"),
            "before_frame_index": event.get("before_frame_index"),
            "after_frame_index": event.get("after_frame_index"),
            "changed_pixel_ratio": event.get("changed_pixel_ratio"),
            "changed_bbox": event.get("changed_bbox"),
            "possible_noise": event.get("possible_noise"),
        }
    if query_type == "event_window":
        window = result.get("event_window") if isinstance(result.get("event_window"), dict) else {}
        frames = [frame for frame in window.get("frames") or [] if isinstance(frame, dict)]
        return {
            "sequence_id": result.get("sequence_id"),
            "change_event_id": result.get("change_event_id"),
            "frame_count": window.get("frame_count"),
            "window_start_frame_index": window.get("window_start_frame_index"),
            "window_end_frame_index": window.get("window_end_frame_index"),
            "frame_refs": [frame.get("frame_ref") for frame in frames],
            "frame_indexes": [frame.get("frame_index") for frame in frames],
        }
    if query_type == "retention_status":
        status = result.get("retention_status") if isinstance(result.get("retention_status"), dict) else {}
        return {
            "entry_count": status.get("entry_count"),
            "indexed_media_bytes": status.get("indexed_media_bytes"),
            "expired_entry_count": status.get("expired_entry_count"),
            "would_prune_entry_count": status.get("would_prune_entry_count"),
            "would_keep_entry_count": status.get("would_keep_entry_count"),
            "reason_counts": status.get("reason_counts"),
            "prune_query_is_dry_run_only": status.get("prune_query_is_dry_run_only"),
            "prune_deletes_raw_media": status.get("prune_deletes_raw_media"),
        }
    if query_type == "prune_unreferenced_buffer":
        plan = result.get("prune_plan") if isinstance(result.get("prune_plan"), dict) else {}
        return {
            "entry_count_before": plan.get("entry_count_before"),
            "would_prune_entry_count": plan.get("would_prune_entry_count"),
            "would_keep_entry_count": plan.get("would_keep_entry_count"),
            "would_keep_entry_refs": plan.get("would_keep_entry_refs"),
            "reason_counts": plan.get("reason_counts"),
            "dry_run_only": plan.get("dry_run_only"),
            "prune_applied": plan.get("prune_applied"),
            "canonical_evidence_deleted": plan.get("canonical_evidence_deleted"),
            "prune_deletes_raw_media": plan.get("prune_deletes_raw_media"),
        }
    if query_type == "retention_class_projection":
        projection = result.get("retention_class_projection") if isinstance(result.get("retention_class_projection"), dict) else {}
        entries = [entry for entry in projection.get("entries") or [] if isinstance(entry, dict)]
        return {
            "entry_count": projection.get("entry_count"),
            "total_frame_buffer_entry_count": projection.get("total_frame_buffer_entry_count"),
            "retention_class_counts": projection.get("retention_class_counts"),
            "entry_refs": [entry.get("buffer_entry_id") for entry in entries],
            "retention_classes": [entry.get("retention_class") for entry in entries],
            "expired_entry_count": len([entry for entry in entries if entry.get("expired_by_ttl")]),
            "pin_state_written": projection.get("classification_limitations", {}).get("pin_state_written"),
            "semantic_importance_judged": projection.get("classification_limitations", {}).get("semantic_importance_judged"),
        }
    if query_type == "storage_attention_summary":
        summary = result.get("storage_attention_summary") if isinstance(result.get("storage_attention_summary"), dict) else {}
        return {
            "entry_count": summary.get("entry_count"),
            "indexed_media_bytes": summary.get("indexed_media_bytes"),
            "expired_entry_count": summary.get("expired_entry_count"),
            "retention_class_counts": summary.get("retention_class_counts"),
            "would_prune_entry_count": summary.get("would_prune_entry_count"),
            "would_keep_entry_count": summary.get("would_keep_entry_count"),
            "prune_reason_counts": summary.get("prune_reason_counts"),
            "sample_entry_count": summary.get("sample_entry_count"),
            "tool_recommends_delete": summary.get("decision_fields", {}).get("tool_recommends_delete"),
            "tool_recommends_keep": summary.get("decision_fields", {}).get("tool_recommends_keep"),
            "semantic_importance_judged": summary.get("decision_fields", {}).get("semantic_importance_judged"),
        }
    return {}


def _recommended_query_types(result: dict[str, Any]) -> list[str]:
    query_type = result.get("query_type")
    if result.get("query_status") != "generated":
        return ["status"]
    if query_type == "status":
        return ["recent_frames", "sequence_change_index", "storage_attention_summary"]
    if query_type == "recent_frames":
        return ["sequence_change_index", "storage_attention_summary"]
    if query_type == "sequence_change_index":
        return ["change_event", "event_window"]
    if query_type == "change_event":
        return ["event_window"]
    if query_type == "retention_status":
        return ["retention_class_projection", "prune_unreferenced_buffer"]
    if query_type == "prune_unreferenced_buffer":
        return ["status", "recent_frames"]
    if query_type == "retention_class_projection":
        return ["storage_attention_summary", "retention_status", "prune_unreferenced_buffer"]
    if query_type == "storage_attention_summary":
        return ["retention_status", "retention_class_projection", "prune_unreferenced_buffer"]
    return []


def _base_query(*, query_type: str) -> dict[str, Any]:
    return {
        "object_type": "VisualMemoryQuery",
        "schema": VISUAL_MEMORY_QUERY_SCHEMA,
        "query_type": query_type,
        "query_api_public": False,
        "query_api_legacy_internal": True,
        "ordinary_public_facade": "look",
        "metadata_only": True,
        "no_capture_performed": True,
        "returns_images": False,
        "raw_media_returned": False,
        "derived_review_artifact_returned": False,
        "canonical": False,
        "integrity_truth_source": False,
        "raw_frames_are_integrity_truth_source": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
