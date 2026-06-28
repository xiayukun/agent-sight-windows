from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


FramePathResolver = Callable[[dict[str, Any]], Path | None]


def build_post_action_observation_window(
    *,
    baseline_frame: dict[str, Any] | None,
    frames: list[dict[str, Any]],
    screen_region: dict[str, int],
    coordinate_system: str | None,
    request: dict[str, Any],
    frame_path: FramePathResolver,
) -> dict[str, Any]:
    threshold = float(request.get("stable_threshold", 0.001))
    stable_frame_count = int(request.get("stable_frame_count", 2))
    requested_frame_count = int(request.get("frame_count", len(frames)))
    stop_when_stable = bool(request.get("stop_when_stable", False))
    comparisons: list[dict[str, Any]] = []

    if baseline_frame and frames:
        comparisons.append(
            _comparison_record(
                before=baseline_frame,
                after=frames[0],
                before_index=-1,
                after_index=0,
                comparison_kind="baseline_to_first_post_frame",
                frame_path=frame_path,
            )
        )

    for index in range(1, len(frames)):
        comparisons.append(
            _comparison_record(
                before=frames[index - 1],
                after=frames[index],
                before_index=index - 1,
                after_index=index,
                comparison_kind="post_frame_to_post_frame",
                frame_path=frame_path,
            )
        )

    computable = [item for item in comparisons if item["status"] == "computed"]
    post_computable = [item for item in computable if item["comparison_kind"] == "post_frame_to_post_frame"]
    changed = [item for item in computable if item["changed"]]
    recent = post_computable[-stable_frame_count:] if stable_frame_count > 0 else []
    stable = bool(recent) and len(recent) == stable_frame_count and all(item["changed_pixel_ratio"] <= threshold for item in recent)
    if not frames:
        stability_status = "not_sampled"
    elif len(frames) < 2:
        stability_status = "insufficient_post_frames"
    elif not post_computable:
        stability_status = "not_computable"
    elif stable:
        stability_status = "stable_by_recent_frame_threshold"
    else:
        stability_status = "still_changing_at_window_end"
    not_stable = stability_status == "still_changing_at_window_end"
    sampling_stop_reason = _sampling_stop_reason(
        frames=frames,
        requested_frame_count=requested_frame_count,
        stop_when_stable=stop_when_stable,
        stable=stable,
    )
    stopped_early = stop_when_stable and stable and len(frames) < requested_frame_count

    return {
        "object_type": "PostActionObservationWindow",
        "schema": "ai_control_post_action_observation_window_v1",
        "status": "generated" if frames else "not_generated",
        "mode": "post_action_observation_window",
        "request": _safe_request(request),
        "screen_region": dict(screen_region),
        "coordinate_system": coordinate_system,
        "baseline_frame_ref": baseline_frame.get("observation_id") if isinstance(baseline_frame, dict) else None,
        "sampled_frame_count": len(frames),
        "sampled_frames": [_frame_ref(frame, index) for index, frame in enumerate(frames)],
        "comparison_count": len(comparisons),
        "comparisons": comparisons,
        "summary": {
            "sampled_frame_count": len(frames),
            "comparison_count": len(comparisons),
            "computed_comparison_count": len(computable),
            "changed_comparison_count": len(changed),
            "changed": bool(changed),
            "changed_frame_indexes": sorted({item["after_frame_index"] for item in changed if item["after_frame_index"] >= 0}),
            "max_changed_pixel_ratio": max((item["changed_pixel_ratio"] for item in computable), default=0.0),
            "largest_changed_bbox": _largest_bbox(changed),
            "stable_threshold": threshold,
            "stable_frame_count_required": stable_frame_count,
            "requested_frame_count": requested_frame_count,
            "max_frame_count": requested_frame_count,
            "stop_when_stable": stop_when_stable,
            "stopped_early": stopped_early,
            "sampling_stop_reason": sampling_stop_reason,
            "stability_status": stability_status,
            "stable": stable,
            "not_stable": not_stable,
            "still_changing": not_stable,
            "still_changing_at_window_end": not_stable,
            "tool_asserts_semantic_change": False,
            "tool_asserts_business_success": False,
        },
        "stop_when_stable": stop_when_stable,
        "stopped_early": stopped_early,
        "sampling_stop_reason": sampling_stop_reason,
        "returns_images": False,
        "raw_media_returned": False,
        "derived_review_artifact_returned": False,
        "derived_metadata": True,
        "raw_frames_are_canonical_evidence": True,
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "boundary": {
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        },
    }


def should_stop_post_observe_sampling(
    *,
    baseline_frame: dict[str, Any] | None,
    frames: list[dict[str, Any]],
    screen_region: dict[str, int],
    coordinate_system: str | None,
    request: dict[str, Any],
    frame_path: FramePathResolver,
) -> bool:
    if not bool(request.get("stop_when_stable", False)):
        return False
    stable_frame_count = int(request.get("stable_frame_count", 2))
    if len(frames) < stable_frame_count + 1:
        return False
    window = build_post_action_observation_window(
        baseline_frame=baseline_frame,
        frames=frames,
        screen_region=screen_region,
        coordinate_system=coordinate_system,
        request=request,
        frame_path=frame_path,
    )
    return bool(window.get("summary", {}).get("stable"))


def _comparison_record(
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    before_index: int,
    after_index: int,
    comparison_kind: str,
    frame_path: FramePathResolver,
) -> dict[str, Any]:
    base = {
        "comparison_kind": comparison_kind,
        "before_frame_ref": before.get("observation_id"),
        "after_frame_ref": after.get("observation_id"),
        "before_frame_index": before_index,
        "after_frame_index": after_index,
        "changed": False,
        "changed_pixel_count": 0,
        "total_pixel_count": 0,
        "changed_pixel_ratio": 0.0,
        "changed_bbox": None,
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
    }
    before_path = frame_path(before)
    after_path = frame_path(after)
    if not before_path or not after_path:
        return {**base, "status": "not_computed", "not_computed_reason": "frame_media_path_missing"}
    if before.get("media_mime") not in {None, "image/png"} or after.get("media_mime") not in {None, "image/png"}:
        return {**base, "status": "not_computed", "not_computed_reason": "non_png_frame_media"}
    try:
        width, height, before_pixels = _load_rgba_pixels(before_path)
        after_width, after_height, after_pixels = _load_rgba_pixels(after_path)
    except ImportError as exc:  # pragma: no cover - dependency dependent
        return {**base, "status": "not_computed", "not_computed_reason": "dependency_missing:Pillow", "failure_detail": str(exc)}
    except Exception as exc:
        return {**base, "status": "not_computed", "not_computed_reason": "frame_decode_failed", "failure_detail": str(exc)}
    if width != after_width or height != after_height:
        return {**base, "status": "not_computed", "not_computed_reason": "inconsistent_frame_dimensions"}
    change = _pixel_change(before_pixels, after_pixels, width=width, height=height)
    return {
        **base,
        "status": "computed",
        "frame_width": width,
        "frame_height": height,
        "changed": change["changed_pixel_count"] > 0,
        **change,
    }


def _load_rgba_pixels(path: Path) -> tuple[int, int, bytes]:
    from PIL import Image

    with Image.open(path) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, rgba.tobytes()


def _pixel_change(before_pixels: bytes, after_pixels: bytes, *, width: int, height: int) -> dict[str, Any]:
    if len(before_pixels) != len(after_pixels):
        raise ValueError("frame_pixel_buffer_size_mismatch")
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    changed = 0
    for offset in range(0, len(before_pixels), 4):
        if before_pixels[offset : offset + 4] == after_pixels[offset : offset + 4]:
            continue
        pixel_index = offset // 4
        y, x = divmod(pixel_index, width)
        changed += 1
        min_x = min(min_x, x)
        min_y = min(min_y, y)
        max_x = max(max_x, x)
        max_y = max(max_y, y)
    bbox = None
    if changed:
        bbox = {"x": min_x, "y": min_y, "width": max_x - min_x + 1, "height": max_y - min_y + 1}
    total = width * height
    return {
        "changed_pixel_count": changed,
        "total_pixel_count": total,
        "changed_pixel_ratio": round(changed / total, 8) if total else 0.0,
        "changed_bbox": bbox,
    }


def _frame_ref(frame: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "frame_index": index,
        "observation_ref": frame.get("observation_id"),
        "captured_at": frame.get("captured_at") or frame.get("timestamp"),
        "captured_time": frame.get("captured_time") or frame.get("source_time"),
        "media_ref": frame.get("media_ref"),
        "media_sha256": frame.get("media_sha256"),
        "segment_frame": frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else None,
        "segment_frame_id": (frame.get("segment_frame") or {}).get("frame_id") if isinstance(frame.get("segment_frame"), dict) else None,
        "segment_restore_ref": (frame.get("segment_frame") or {}).get("restore_ref") if isinstance(frame.get("segment_frame"), dict) else None,
        "raw_canonical": True,
        "returns_image_path": False,
    }


def _safe_request(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "delay_ms": int(request.get("delay_ms", 0)),
        "frame_count": int(request.get("frame_count", 3)),
        "interval_ms": int(request.get("interval_ms", 150)),
        "stable_threshold": float(request.get("stable_threshold", 0.001)),
        "stable_frame_count": int(request.get("stable_frame_count", 2)),
        "stop_when_stable": bool(request.get("stop_when_stable", False)),
    }


def _largest_bbox(changed: list[dict[str, Any]]) -> dict[str, int] | None:
    bboxes = [item.get("changed_bbox") for item in changed if isinstance(item.get("changed_bbox"), dict)]
    if not bboxes:
        return None
    return max(bboxes, key=lambda bbox: int(bbox["width"]) * int(bbox["height"]))


def _sampling_stop_reason(
    *,
    frames: list[dict[str, Any]],
    requested_frame_count: int,
    stop_when_stable: bool,
    stable: bool,
) -> str:
    if not frames:
        return "not_sampled"
    if stop_when_stable and stable:
        return "stable_window_reached"
    if len(frames) >= requested_frame_count:
        return "max_frame_count_reached"
    return "sampling_incomplete"
