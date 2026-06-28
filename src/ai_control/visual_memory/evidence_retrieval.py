from __future__ import annotations

from io import BytesIO
from typing import Any

from ai_control.evidence.store import EvidenceReplayService


SUPPORTED_VISUAL_EVIDENCE_ARTIFACT_TYPES = {
    "raw_frame",
    "raw_crop",
    "before_after",
    "diff_heatmap",
}


def build_visual_evidence_artifacts(
    *,
    sequence: dict[str, Any],
    frames: list[dict[str, Any]],
    evidence: EvidenceReplayService,
    evidence_request: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if evidence_request is None:
        return None

    artifact_types = list(evidence_request.get("artifact_types") or [])
    max_artifacts = int(evidence_request.get("max_artifacts") or 1)
    source = evidence_request.get("source", "change_events")
    base = {
        "object_type": "VisualEvidenceArtifacts",
        "schema": "ai_control_p0h_visual_evidence_artifacts_v1",
        "source_sequence_id": sequence.get("sequence_id"),
        "source": source,
        "requested_artifact_types": artifact_types,
        "max_artifacts": max_artifacts,
        "artifact_count": 0,
        "artifacts": [],
        "raw_frames_are_integrity_truth_source": True,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
    if not artifact_types:
        return {**base, "status": "not_generated", "not_generated_reason": "no_artifact_types_requested"}
    if source != "change_events":
        return {**base, "status": "not_generated", "not_generated_reason": "unsupported_source"}

    change_index = sequence.get("region_change_index") or {}
    change_events = list(change_index.get("change_events") or [])
    if not change_events:
        return {**base, "status": "not_generated", "not_generated_reason": "no_change_events"}

    frame_by_ref = {frame.get("observation_id"): frame for frame in frames}
    artifacts: list[dict[str, Any]] = []
    selected_events = change_events[:max_artifacts]
    for event in selected_events:
        before = frame_by_ref.get(event.get("before_frame_ref"))
        after = frame_by_ref.get(event.get("after_frame_ref"))
        if not before or not after:
            continue
        if "raw_frame" in artifact_types:
            artifacts.extend(_raw_frame_artifacts(event, before, after))
        if "before_after" in artifact_types:
            artifacts.append(_before_after_artifact(event, before, after))
        if "raw_crop" in artifact_types:
            crop = _raw_crop_artifact(event=event, frame=after, evidence=evidence, sequence=sequence)
            if crop:
                artifacts.append(crop)
        if "diff_heatmap" in artifact_types:
            heatmap = _diff_heatmap_artifact(event=event, before=before, after=after, evidence=evidence, sequence=sequence)
            if heatmap:
                artifacts.append(heatmap)

    status = "generated" if artifacts else "not_generated"
    result = {**base, "status": status, "artifact_count": len(artifacts), "artifacts": artifacts}
    if not artifacts:
        result["not_generated_reason"] = "artifact_generation_failed"
    return result


def _raw_frame_artifacts(event: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _raw_frame_artifact(event, before, role="raw_before_frame"),
        _raw_frame_artifact(event, after, role="raw_after_frame"),
    ]


def _raw_frame_artifact(event: dict[str, Any], frame: dict[str, Any], *, role: str) -> dict[str, Any]:
    return {
        "object_type": "VisualEvidenceArtifact",
        "schema": "ai_control_p0h_visual_evidence_artifact_v1",
        "artifact_type": "raw_frame",
        "artifact_role": role,
        "source_change_event_id": event.get("change_event_id"),
        "frame_ref": frame.get("observation_id"),
        "media_ref": frame.get("media_ref"),
        "media_sha256": frame.get("media_sha256"),
        "media_size_bytes": frame.get("media_size_bytes"),
        "media_mime": frame.get("media_mime"),
        "screen_region": frame.get("screen_region"),
        "coordinate_system": frame.get("coordinate_system"),
        "canonical": True,
        "raw_media": True,
        "lossless_extract": False,
        "integrity_truth_source": True,
        "derived_review_artifact": False,
    }


def _before_after_artifact(event: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_type": "VisualEvidenceArtifact",
        "schema": "ai_control_p0h_visual_evidence_artifact_v1",
        "artifact_type": "before_after",
        "artifact_role": "raw_before_after_pair",
        "source_change_event_id": event.get("change_event_id"),
        "before_frame_ref": before.get("observation_id"),
        "after_frame_ref": after.get("observation_id"),
        "before_media_ref": before.get("media_ref"),
        "before_media_sha256": before.get("media_sha256"),
        "after_media_ref": after.get("media_ref"),
        "after_media_sha256": after.get("media_sha256"),
        "before_media": {
            "media_ref": before.get("media_ref"),
            "media_sha256": before.get("media_sha256"),
            "media_size_bytes": before.get("media_size_bytes"),
            "media_mime": before.get("media_mime"),
            "screen_region": before.get("screen_region"),
            "coordinate_system": before.get("coordinate_system"),
        },
        "after_media": {
            "media_ref": after.get("media_ref"),
            "media_sha256": after.get("media_sha256"),
            "media_size_bytes": after.get("media_size_bytes"),
            "media_mime": after.get("media_mime"),
            "screen_region": after.get("screen_region"),
            "coordinate_system": after.get("coordinate_system"),
        },
        "screen_region": after.get("screen_region"),
        "coordinate_system": after.get("coordinate_system"),
        "canonical": True,
        "raw_media": True,
        "lossless_extract": False,
        "integrity_truth_source": True,
        "derived_review_artifact": False,
    }


def _raw_crop_artifact(
    *,
    event: dict[str, Any],
    frame: dict[str, Any],
    evidence: EvidenceReplayService,
    sequence: dict[str, Any],
) -> dict[str, Any] | None:
    bbox = event.get("changed_bbox_frame")
    if not bbox:
        return None
    try:
        crop_bytes = _crop_png(frame, bbox, evidence)
    except Exception as exc:
        return _artifact_failure("raw_crop", event, "crop_generation_failed", str(exc))
    media = evidence.write_media_bytes(f"{event.get('change_event_id')}-raw-crop.png", crop_bytes)
    return {
        "object_type": "VisualEvidenceArtifact",
        "schema": "ai_control_p0h_visual_evidence_artifact_v1",
        "artifact_type": "raw_crop",
        "artifact_role": "raw_after_frame_changed_region_crop",
        "source_change_event_id": event.get("change_event_id"),
        "source_frame_ref": frame.get("observation_id"),
        "derived_from_raw_frame_ref": frame.get("observation_id"),
        "derived_from_raw_media_ref": frame.get("media_ref"),
        "derived_from_raw_sha256": frame.get("media_sha256"),
        "crop_region_frame": bbox,
        "crop_region": event.get("changed_bbox"),
        "screen_region": event.get("changed_bbox"),
        "coordinate_system": sequence.get("coordinate_system"),
        "media_mime": "image/png",
        "media_format": "png",
        **media,
        "canonical": True,
        "raw_media": True,
        "lossless_extract": True,
        "integrity_truth_source": True,
        "derived_review_artifact": False,
    }


def _diff_heatmap_artifact(
    *,
    event: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    evidence: EvidenceReplayService,
    sequence: dict[str, Any],
) -> dict[str, Any] | None:
    bbox = event.get("changed_bbox_frame")
    if not bbox:
        return None
    try:
        heatmap_bytes = _diff_heatmap_png(before, after, bbox, evidence)
    except Exception as exc:
        return _artifact_failure("diff_heatmap", event, "diff_heatmap_generation_failed", str(exc))
    media = evidence.write_media_bytes(f"{event.get('change_event_id')}-diff-heatmap.png", heatmap_bytes)
    return {
        "object_type": "VisualEvidenceArtifact",
        "schema": "ai_control_p0h_visual_evidence_artifact_v1",
        "artifact_type": "diff_heatmap",
        "artifact_role": "derived_diff_heatmap",
        "source_change_event_id": event.get("change_event_id"),
        "before_frame_ref": before.get("observation_id"),
        "after_frame_ref": after.get("observation_id"),
        "derived_from_raw_before_frame_ref": before.get("observation_id"),
        "derived_from_raw_before_media_ref": before.get("media_ref"),
        "derived_from_raw_before_sha256": before.get("media_sha256"),
        "derived_from_raw_after_frame_ref": after.get("observation_id"),
        "derived_from_raw_after_media_ref": after.get("media_ref"),
        "derived_from_raw_after_sha256": after.get("media_sha256"),
        "crop_region_frame": bbox,
        "crop_region": event.get("changed_bbox"),
        "screen_region": event.get("changed_bbox"),
        "coordinate_system": sequence.get("coordinate_system"),
        "media_mime": "image/png",
        "media_format": "png",
        **media,
        "canonical": False,
        "raw_media": False,
        "visualization_only": True,
        "excluded_from_integrity_truth_source": True,
        "integrity_truth_source": False,
        "derived_review_artifact": True,
    }


def _artifact_failure(artifact_type: str, event: dict[str, Any], reason: str, detail: str) -> dict[str, Any]:
    return {
        "object_type": "VisualEvidenceArtifact",
        "schema": "ai_control_p0h_visual_evidence_artifact_v1",
        "artifact_type": artifact_type,
        "source_change_event_id": event.get("change_event_id"),
        "status": "not_generated",
        "not_generated_reason": reason,
        "failure_detail": detail,
        "canonical": False,
        "integrity_truth_source": False,
    }


def _crop_png(frame: dict[str, Any], bbox: dict[str, int], evidence: EvidenceReplayService) -> bytes:
    from PIL import Image

    with Image.open(evidence.root / str(frame["media_ref"])) as image:
        crop = image.crop(_box(bbox))
        buffer = BytesIO()
        crop.save(buffer, format="PNG")
        return buffer.getvalue()


def _diff_heatmap_png(before: dict[str, Any], after: dict[str, Any], bbox: dict[str, int], evidence: EvidenceReplayService) -> bytes:
    from PIL import Image

    with Image.open(evidence.root / str(before["media_ref"])) as before_image:
        before_crop = before_image.convert("RGBA").crop(_box(bbox))
    with Image.open(evidence.root / str(after["media_ref"])) as after_image:
        after_crop = after_image.convert("RGBA").crop(_box(bbox))
    heatmap = Image.new("RGBA", before_crop.size, (0, 0, 0, 0))
    before_pixels = before_crop.load()
    after_pixels = after_crop.load()
    heatmap_pixels = heatmap.load()
    for y in range(before_crop.height):
        for x in range(before_crop.width):
            if before_pixels[x, y] != after_pixels[x, y]:
                heatmap_pixels[x, y] = (255, 0, 0, 220)
    buffer = BytesIO()
    heatmap.save(buffer, format="PNG")
    return buffer.getvalue()


def _box(bbox: dict[str, int]) -> tuple[int, int, int, int]:
    left = int(bbox["x"])
    top = int(bbox["y"])
    return (left, top, left + int(bbox["width"]), top + int(bbox["height"]))
