from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from ai_control.segments.global_index import build_global_segment_frame_index, query_segment_frames_near_time
from ai_control.segments.manifest import boundary_facts
from ai_control.segments.mkv_container import MKV_CONTAINER_MODEL, decode_mkv_frame_to_image


def decode_segment_frame_to_png(restore_ref: dict[str, Any], *, output_path: str | Path) -> dict[str, Any]:
    image, report = decode_mkv_frame_to_image(restore_ref)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return _with_common_fields(
        {
            **report,
            "review_media_path_abs": str(path.resolve()),
            "raw_or_derived": "derived_review_only",
            "artifact_is_canonical_evidence": False,
        }
    )


def decode_segment_region_to_png(
    restore_ref: dict[str, Any],
    *,
    region: dict[str, Any],
    output_path: str | Path,
    scale_down: int | float = 1,
    blur_radius: int | float = 0,
) -> dict[str, Any]:
    image, report = _restore_region(restore_ref, region=region, scale_down=scale_down, blur_radius=blur_radius)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return _with_common_fields(
        {
            **report,
            "review_media_path_abs": str(path.resolve()),
            "raw_or_derived": "derived_review_only",
            "artifact_is_canonical_evidence": False,
        }
    )


def decode_segment_region_to_image_content(
    restore_ref: dict[str, Any],
    *,
    region: dict[str, Any],
    scale_down: int | float = 1,
    blur_radius: int | float = 0,
) -> dict[str, Any]:
    image, report = _restore_region(restore_ref, region=region, scale_down=scale_down, blur_radius=blur_radius)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return _with_common_fields(
        {
            **report,
            "raw_or_derived": "derived_review_only",
            "artifact_is_canonical_evidence": False,
            "derived_review_file_written": False,
            "image_content_type": "mcp_image_content",
            "mcp_content": [
                {
                    "type": "image",
                    "mimeType": "image/png",
                    "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                    "raw_or_derived": "derived_review_only",
                    "canonical": False,
                }
            ],
        }
    )


def decode_segment_diff_to_png(
    before_restore_ref: dict[str, Any],
    after_restore_ref: dict[str, Any],
    *,
    output_path: str | Path,
    region: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before, before_report = decode_mkv_frame_to_image(before_restore_ref)
    after, after_report = decode_mkv_frame_to_image(after_restore_ref)
    if region:
        x, y, w, h = _region_tuple(region, before.width, before.height)
        before = before.crop((x, y, min(before.width, x + w), min(before.height, y + h)))
        after = after.crop((x, y, min(after.width, x + w), min(after.height, y + h)))
    diff = _simple_diff_heatmap(before, after)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    diff.save(path)
    return _with_common_fields(
        {
            "object_type": "AgentSightSegmentDiffReviewReport",
            "schema": "agentsight_mkv_segment_review_v1",
            "storage_format": "mkv_vfr",
            "canonical_evidence_source": MKV_CONTAINER_MODEL,
            "before_restore_report": before_report,
            "after_restore_report": after_report,
            "review_media_path_abs": str(path.resolve()),
            "raw_or_derived": "derived_review_only",
            "artifact_is_canonical_evidence": False,
        }
    )


def query_segment_decoder_near_time(root: str | Path, requested_time: Any) -> dict[str, Any]:
    return query_segment_frames_near_time(build_global_segment_frame_index(root), requested_time)


def query_segment_change_index(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _metadata_only_unimplemented("changes")


def query_segment_review_clip(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _metadata_only_unimplemented("clip")


def query_segment_timeline_diff(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _metadata_only_unimplemented("diff")


def _restore_region(
    restore_ref: dict[str, Any],
    *,
    region: dict[str, Any],
    scale_down: int | float,
    blur_radius: int | float,
) -> tuple[Any, dict[str, Any]]:
    image, restore_report = decode_mkv_frame_to_image(restore_ref)
    x, y, w, h = _region_tuple(region, image.width, image.height)
    crop = image.crop((x, y, min(image.width, x + w), min(image.height, y + h)))
    scale = float(scale_down or 1)
    if scale > 1:
        crop = crop.resize((max(1, int(crop.size[0] / scale)), max(1, int(crop.size[1] / scale))))
    blur = float(blur_radius or 0)
    if blur > 0:
        from PIL import ImageFilter

        crop = crop.filter(ImageFilter.GaussianBlur(radius=blur))
    return crop, {
        "object_type": "AgentSightSegmentRegionReviewReport",
        "schema": "agentsight_mkv_segment_review_v1",
        "source_frame_id": restore_ref.get("frame_id"),
        "source_frame_hash_ok": restore_report.get("hash_ok", True),
        "region": {"x": x, "y": y, "w": w, "h": h},
        "scale_down": int(scale) if scale.is_integer() else scale,
        "blur_radius": int(blur) if blur.is_integer() else blur,
        "storage_format": "mkv_vfr",
        "canonical_evidence_source": MKV_CONTAINER_MODEL,
        "restore_report": restore_report,
    }


def _simple_diff_heatmap(before: Any, after: Any) -> Any:
    from PIL import Image, ImageChops

    before_rgba = before.convert("RGBA")
    after_rgba = after.convert("RGBA")
    if before_rgba.size != after_rgba.size:
        after_rgba = after_rgba.resize(before_rgba.size)
    diff = ImageChops.difference(before_rgba, after_rgba).convert("L")
    heatmap = Image.new("RGBA", before_rgba.size, (0, 0, 0, 0))
    heatmap.putalpha(diff)
    return heatmap


def _metadata_only_unimplemented(kind: str) -> dict[str, Any]:
    return _with_common_fields(
        {
            "object_type": "AgentSightMkvMetadataQuery",
            "schema": "agentsight_mkv_segment_query_v1",
            "query_kind": kind,
            "query_status": "not_implemented_for_mkv_yet",
            "changes": [],
            "change_count": 0,
            "frames": [],
            "artifacts": [],
            "no_capture_performed": True,
            "no_media_exported": True,
            "storage_format": "mkv_vfr",
        }
    )


def _region_tuple(region: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    x = max(0, min(width, int(region.get("x", 0))))
    y = max(0, min(height, int(region.get("y", 0))))
    w = max(1, int(region.get("w", region.get("width", width - x))))
    h = max(1, int(region.get("h", region.get("height", height - y))))
    return x, y, min(w, width - x), min(h, height - y)


def _with_common_fields(payload: dict[str, Any]) -> dict[str, Any]:
    payload.setdefault("storage_format", "mkv_vfr")
    payload.setdefault("canonical_evidence_source", MKV_CONTAINER_MODEL)
    payload.setdefault("tool_asserts_business_success", False)
    payload.setdefault("tool_asserts_causality", False)
    payload.setdefault("tool_asserts_target_hit", False)
    payload.setdefault("host_input_sent", False)
    payload.setdefault("host_sent_event_count", 0)
    payload.setdefault("boundary", boundary_facts())
    return payload
