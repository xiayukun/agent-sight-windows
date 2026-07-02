from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from agentsight.segments.binary_container import BINARY_CONTAINER_MODEL, BinarySegmentReader
from agentsight.segments.manifest import boundary_facts, sha256_image_rgba
from agentsight.segments.reader import SegmentReader


def export_segment_frame_crop(
    segment_dir: str | Path,
    *,
    frame_id: str,
    region: dict[str, Any],
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    source = _segment_review_source(segment_dir)
    reader = source["reader"]
    image, restore_report = reader.restore_frame(frame_id)
    x = max(0, int(region.get("x", 0)))
    y = max(0, int(region.get("y", 0)))
    w = max(1, int(region.get("w", region.get("width", 1))))
    h = max(1, int(region.get("h", region.get("height", 1))))
    box = (x, y, min(image.width, x + w), min(image.height, y + h))
    crop = image.crop(box)
    path = Path(output_path) if output_path else source["derived_dir"] / f"crop-{frame_id}-{x}-{y}-{w}x{h}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(path)
    return _artifact_report(
        segment_path=source["segment_path"],
        storage_format=source["storage_format"],
        canonical_evidence_source=source["canonical_evidence_source"],
        artifact_kind="region_crop",
        artifact_path=path,
        source_frame_ref={"frame_id": frame_id, "restore_report": _safe_restore_report(restore_report)},
        region={"x": x, "y": y, "w": box[2] - box[0], "h": box[3] - box[1]},
        extra={"artifact_sha256": sha256_image_rgba(crop)},
    )


def export_segment_frame_diff(
    segment_dir: str | Path,
    *,
    before_frame_id: str,
    after_frame_id: str,
    region: dict[str, Any] | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    from PIL import Image, ImageChops

    source = _segment_review_source(segment_dir)
    reader = source["reader"]
    before, before_report = reader.restore_frame(before_frame_id)
    after, after_report = reader.restore_frame(after_frame_id)
    if before.size != after.size:
        raise ValueError("segment_diff_frame_size_mismatch")
    before, after, normalized_region = _crop_diff_region(before, after, region)
    diff = ImageChops.difference(before.convert("RGB"), after.convert("RGB"))
    bbox = diff.getbbox()
    heatmap = Image.new("RGBA", before.size, (0, 0, 0, 0))
    changed_pixels = 0
    if bbox is not None:
        pixels = diff.load()
        heat = heatmap.load()
        for y in range(before.height):
            for x in range(before.width):
                r, g, b = pixels[x, y]
                intensity = max(r, g, b)
                if intensity:
                    changed_pixels += 1
                    heat[x, y] = (255, min(255, intensity), 0, 180)
    path = Path(output_path) if output_path else source["derived_dir"] / f"diff-{before_frame_id}-{after_frame_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    heatmap.save(path)
    total = before.width * before.height
    changed_bbox = None
    if bbox is not None:
        changed_bbox = {"x": bbox[0], "y": bbox[1], "w": bbox[2] - bbox[0], "h": bbox[3] - bbox[1]}
    return _artifact_report(
        segment_path=source["segment_path"],
        storage_format=source["storage_format"],
        canonical_evidence_source=source["canonical_evidence_source"],
        artifact_kind="diff_heatmap",
        artifact_path=path,
        source_frame_ref={
            "before_frame_id": before_frame_id,
            "after_frame_id": after_frame_id,
            "before_restore_report": _safe_restore_report(before_report),
            "after_restore_report": _safe_restore_report(after_report),
        },
        region=normalized_region,
        extra={
            "artifact_sha256": sha256_image_rgba(heatmap),
            "changed_pixel_count": changed_pixels,
            "total_pixel_count": total,
            "changed_pixel_ratio": round(changed_pixels / total, 8) if total else 0.0,
            "changed_bbox": changed_bbox,
            "diff_coordinate_basis": "region_local_px" if normalized_region else "frame_px",
        },
    )


def _artifact_report(
    *,
    segment_path: Path,
    storage_format: str,
    canonical_evidence_source: str,
    artifact_kind: str,
    artifact_path: Path,
    source_frame_ref: dict[str, Any],
    region: dict[str, int] | None,
    extra: dict[str, Any],
) -> dict[str, Any]:
    return {
        "object_type": "AgentSightSegmentReviewArtifact",
        "schema": "agentsight_segment_review_artifact_v1",
        "artifact_kind": artifact_kind,
        "artifact_path_abs": str(artifact_path.resolve()),
        "segment_path_abs": str(segment_path.resolve()),
        "storage_format": storage_format,
        "source_frame_ref": source_frame_ref,
        "region": region,
        "raw_or_derived": "derived_review_only",
        "artifact_is_canonical_evidence": False,
        "canonical_evidence_source": canonical_evidence_source,
        "created_at_ms": int(time.time() * 1000),
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": boundary_facts(),
        **extra,
    }


def _segment_review_source(segment_path: str | Path) -> dict[str, Any]:
    path = Path(segment_path)
    if path.suffix.lower() == ".agseg" or path.is_file():
        return {
            "reader": BinarySegmentReader(path),
            "segment_path": path,
            "storage_format": "binary_agseg",
            "canonical_evidence_source": BINARY_CONTAINER_MODEL,
            "derived_dir": path.parent / "derived" / path.stem,
        }
    return {
        "reader": SegmentReader(path),
        "segment_path": path,
        "storage_format": "proto_directory",
        "canonical_evidence_source": "agentsight_segment_v1",
        "derived_dir": path / "derived",
    }


def _crop_diff_region(before: Any, after: Any, region: dict[str, Any] | None) -> tuple[Any, Any, dict[str, int] | None]:
    if not isinstance(region, dict):
        return before, after, None
    x = max(0, int(region.get("x", 0)))
    y = max(0, int(region.get("y", 0)))
    w = max(1, int(region.get("w", region.get("width", before.width - x))))
    h = max(1, int(region.get("h", region.get("height", before.height - y))))
    x = min(before.width, x)
    y = min(before.height, y)
    box = (x, y, min(before.width, x + w), min(before.height, y + h))
    normalized = {"x": x, "y": y, "w": box[2] - box[0], "h": box[3] - box[1]}
    return before.crop(box), after.crop(box), normalized


def _safe_restore_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "segment_id": report.get("segment_id"),
        "storage_format": "binary_agseg"
        if report.get("canonical_evidence_source") == BINARY_CONTAINER_MODEL
        else "proto_directory",
        "frame_id": report.get("frame_id"),
        "frame_kind": report.get("frame_kind"),
        "hash_ok": report.get("hash_ok"),
        "raw_or_derived": report.get("raw_or_derived"),
        "canonical_evidence_source": report.get("canonical_evidence_source"),
    }
