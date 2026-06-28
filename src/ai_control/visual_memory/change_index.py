from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ai_control.evidence.store import EvidenceReplayService


@dataclass(frozen=True)
class PixelChange:
    changed_pixel_count: int
    total_pixel_count: int
    bbox_frame: dict[str, int] | None

    @property
    def changed(self) -> bool:
        return self.changed_pixel_count > 0

    @property
    def ratio(self) -> float:
        if self.total_pixel_count <= 0:
            return 0.0
        return self.changed_pixel_count / self.total_pixel_count


def build_region_change_index(
    *,
    sequence: dict[str, Any],
    frames: list[dict[str, Any]],
    evidence: EvidenceReplayService,
    enabled: bool,
) -> dict[str, Any]:
    base = _base_index(sequence=sequence, frames=frames, enabled=enabled)
    if not enabled:
        return {**base, "status": "disabled", "not_generated_reason": "change_detection_disabled"}
    if len(frames) < 2:
        return {**base, "status": "not_generated", "not_generated_reason": "insufficient_frames"}
    if any(frame.get("media_mime") != "image/png" for frame in frames):
        return {**base, "status": "not_generated", "not_generated_reason": "non_png_frame_media"}

    try:
        images = [_load_rgba_pixels(frame, evidence) for frame in frames]
    except ImportError as exc:  # pragma: no cover - dependency dependent
        return {
            **base,
            "status": "not_generated",
            "not_generated_reason": "dependency_missing:Pillow",
            "failure_detail": str(exc),
        }
    except Exception as exc:
        return {
            **base,
            "status": "not_generated",
            "not_generated_reason": "frame_decode_failed",
            "failure_detail": str(exc),
        }

    dimensions = {(width, height) for width, height, _pixels in images}
    if len(dimensions) != 1:
        return {**base, "status": "not_generated", "not_generated_reason": "inconsistent_frame_dimensions"}
    width, height = next(iter(dimensions))
    total_pixels = width * height
    if total_pixels <= 0:
        return {**base, "status": "not_generated", "not_generated_reason": "invalid_frame_dimensions"}

    comparisons: list[dict[str, Any]] = []
    change_events: list[dict[str, Any]] = []
    for index in range(1, len(frames)):
        change = _pixel_change(images[index - 1][2], images[index][2], width=width, height=height)
        comparison = _comparison_record(frames, index, change)
        comparisons.append(comparison)
        if change.changed:
            change_events.append(_change_event(sequence, frames, index, change, len(change_events)))

    _attach_stability(change_events, comparisons, frames)
    changed_ratios = [event["changed_pixel_ratio"] for event in change_events]
    return {
        **base,
        "status": "generated",
        "method": "exact_rgba_pixel_diff",
        "frame_width": width,
        "frame_height": height,
        "total_pixel_count": total_pixels,
        "comparison_count": len(comparisons),
        "change_event_count": len(change_events),
        "changed": bool(change_events),
        "changed_frame_indexes": [event["after_frame_index"] for event in change_events],
        "max_changed_pixel_ratio": max(changed_ratios) if changed_ratios else 0.0,
        "comparisons": comparisons,
        "change_events": change_events,
    }


def _base_index(*, sequence: dict[str, Any], frames: list[dict[str, Any]], enabled: bool) -> dict[str, Any]:
    return {
        "object_type": "RegionChangeIndex",
        "schema": "ai_control_p0g_region_change_index_v1",
        "enabled": enabled,
        "source_sequence_id": sequence.get("sequence_id"),
        "source_frame_refs": [frame.get("observation_id") for frame in frames],
        "frame_count": len(frames),
        "screen_region": sequence.get("screen_region"),
        "coordinate_system": sequence.get("coordinate_system"),
        "returns_images": False,
        "raw_media_returned": False,
        "derived_review_artifact_returned": False,
        "derived_metadata": True,
        "canonical": False,
        "integrity_truth_source": False,
        "raw_frames_are_integrity_truth_source": True,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _load_rgba_pixels(frame: dict[str, Any], evidence: EvidenceReplayService) -> tuple[int, int, bytes]:
    from PIL import Image

    media_ref = frame.get("media_ref")
    if not isinstance(media_ref, str):
        raise ValueError("frame_media_ref_missing")
    with Image.open(evidence.root / media_ref) as image:
        rgba = image.convert("RGBA")
        return rgba.width, rgba.height, rgba.tobytes()


def _pixel_change(before_pixels: bytes, after_pixels: bytes, *, width: int, height: int) -> PixelChange:
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
    return PixelChange(changed_pixel_count=changed, total_pixel_count=width * height, bbox_frame=bbox)


def _comparison_record(frames: list[dict[str, Any]], index: int, change: PixelChange) -> dict[str, Any]:
    return {
        "before_frame_ref": frames[index - 1].get("observation_id"),
        "after_frame_ref": frames[index].get("observation_id"),
        "after_frame_index": index,
        "changed": change.changed,
        "changed_pixel_count": change.changed_pixel_count,
        "total_pixel_count": change.total_pixel_count,
        "changed_pixel_ratio": round(change.ratio, 8),
        "changed_bbox_frame": change.bbox_frame,
    }


def _change_event(
    sequence: dict[str, Any],
    frames: list[dict[str, Any]],
    index: int,
    change: PixelChange,
    event_index: int,
) -> dict[str, Any]:
    screen_region = sequence.get("screen_region") or {}
    bbox_screen = _screen_bbox(change.bbox_frame, screen_region)
    return {
        "change_event_id": f"{sequence.get('sequence_id')}-change-{event_index + 1:03d}",
        "before_frame_ref": frames[index - 1].get("observation_id"),
        "after_frame_ref": frames[index].get("observation_id"),
        "after_frame_index": index,
        "after_captured_at": frames[index].get("captured_at") or frames[index].get("timestamp"),
        "changed_pixel_count": change.changed_pixel_count,
        "total_pixel_count": change.total_pixel_count,
        "changed_pixel_ratio": round(change.ratio, 8),
        "changed_bbox_frame": change.bbox_frame,
        "changed_bbox": bbox_screen,
        "changed_bbox_coordinate_system": sequence.get("coordinate_system"),
        "noise_assessment": _noise_assessment(change),
        "stable_after_ms": None,
        "stable_after_basis": "computed_from_subsequent_sequence_frames",
    }


def _screen_bbox(bbox_frame: dict[str, int] | None, screen_region: dict[str, Any]) -> dict[str, int] | None:
    if not bbox_frame:
        return None
    return {
        "x": int(screen_region.get("x") or 0) + bbox_frame["x"],
        "y": int(screen_region.get("y") or 0) + bbox_frame["y"],
        "width": bbox_frame["width"],
        "height": bbox_frame["height"],
    }


def _noise_assessment(change: PixelChange) -> dict[str, Any]:
    bbox = change.bbox_frame or {"width": 0, "height": 0}
    thin = bbox["width"] <= 2 or bbox["height"] <= 2
    tiny_ratio = change.ratio <= 0.01
    return {
        "cursor_or_caret_noise_possible": bool(change.changed and (thin or tiny_ratio)),
        "basis": "thin_bbox_or_tiny_changed_ratio",
        "tool_does_not_classify_semantic_noise": True,
    }


def _attach_stability(
    change_events: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    frames: list[dict[str, Any]],
) -> None:
    changed_after_indexes = [comparison["after_frame_index"] for comparison in comparisons if comparison["changed"]]
    for event in change_events:
        after_index = event["after_frame_index"]
        later_changed = [index for index in changed_after_indexes if index > after_index]
        stable_until_index = (later_changed[0] - 1) if later_changed else len(frames) - 1
        event["stable_after_ms"] = _elapsed_ms(frames[after_index], frames[stable_until_index])


def _elapsed_ms(start_frame: dict[str, Any], end_frame: dict[str, Any]) -> int:
    start = start_frame.get("captured_at") or start_frame.get("timestamp")
    end = end_frame.get("captured_at") or end_frame.get("timestamp")
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        return 0
    return max(0, int((end - start) * 1000))
