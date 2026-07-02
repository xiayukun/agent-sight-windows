from __future__ import annotations

import base64
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from agentsight.segments.binary_container import BINARY_CONTAINER_MODEL, BinarySegmentReader
from agentsight.segments.global_index import build_global_segment_frame_index, query_segment_frames_near_time
from agentsight.segments.manifest import boundary_facts
from agentsight.segments.mkv_container import MKV_CONTAINER_MODEL, decode_mkv_frame_to_image


def decode_segment_frame_to_png(restore_ref: dict[str, Any], *, output_path: str | Path) -> dict[str, Any]:
    image, report = _decode_segment_frame_to_image(restore_ref)
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


def decode_segment_frame_to_image(restore_ref: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    return _decode_segment_frame_to_image(restore_ref)


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
    before, before_report = _decode_segment_frame_to_image(before_restore_ref)
    after, after_report = _decode_segment_frame_to_image(after_restore_ref)
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
            "storage_format": before_report.get("storage_format", "mkv_vfr"),
            "canonical_evidence_source": before_report.get("canonical_evidence_source", MKV_CONTAINER_MODEL),
            "before_restore_report": before_report,
            "after_restore_report": after_report,
            "review_media_path_abs": str(path.resolve()),
            "raw_or_derived": "derived_review_only",
            "artifact_is_canonical_evidence": False,
        }
    )


def query_segment_decoder_near_time(root: str | Path, requested_time: Any) -> dict[str, Any]:
    return query_segment_frames_near_time(build_global_segment_frame_index(root), requested_time)


def query_segment_change_index(
    root: str | Path,
    *,
    region: dict[str, Any] | None = None,
    region_coordinate_system: str = "virtual_screen_pixels",
    max_pairs: int = 128,
    min_changed_pixel_ratio: float = 0.0,
    start_time: Any = None,
    end_time: Any = None,
) -> dict[str, Any]:
    frames = _query_frames(root, start_time=start_time, end_time=end_time)
    changes: list[dict[str, Any]] = []
    decode_errors: list[dict[str, Any]] = []
    skipped_pairs: list[dict[str, Any]] = []
    duplicate_intervals: list[dict[str, Any]] = []
    max_pairs = max(0, int(max_pairs or 0))
    min_ratio = max(0.0, float(min_changed_pixel_ratio or 0.0))
    adjacent_pairs = list(zip(frames, frames[1:]))
    adjacent_pairs_considered = 0
    for before, after in adjacent_pairs:
        if adjacent_pairs_considered >= max_pairs:
            break
        adjacent_pairs_considered += 1
        mapping_error = _common_mapping_error(before, after, region=region, region_coordinate_system=region_coordinate_system)
        if mapping_error is not None:
            decode_errors.append(mapping_error)
            continue
        mapped = _common_mapped_region(before, after, region=region, region_coordinate_system=region_coordinate_system)
        if mapped is None:
            decode_errors.append(
                _with_common_fields(
                    {
                        "status": "decode_skipped_region_unavailable",
                        "before_frame": _frame_ref(before),
                        "after_frame": _frame_ref(after),
                        "requested_region": dict(region) if isinstance(region, dict) else None,
                        "region_coordinate_system": region_coordinate_system,
                    }
                )
            )
            continue
        try:
            before_image, before_report = _decode_segment_frame_to_image(before["segment_restore_ref"])
            after_image, after_report = _decode_segment_frame_to_image(after["segment_restore_ref"])
            bx, by, bw, bh = _region_tuple(mapped["before_region"], before_image.width, before_image.height)
            ax, ay, aw, ah = _region_tuple(mapped["after_region"], after_image.width, after_image.height)
            w = max(1, min(bw, aw))
            h = max(1, min(bh, ah))
            before_crop = before_image.crop((bx, by, bx + w, by + h))
            after_crop = after_image.crop((ax, ay, ax + w, ay + h))
            diff_stats = _diff_stats(before_crop, after_crop)
        except Exception as exc:
            decode_errors.append(
                _with_common_fields(
                    {
                        "object_type": "AgentSightSegmentDecodeError",
                        "schema": "agentsight_segment_decode_error_v1",
                        "status": "diff_failed",
                        "error_type": type(exc).__name__,
                        "detail": str(exc),
                        "before_frame": _frame_ref(before),
                        "after_frame": _frame_ref(after),
                        "requested_region": mapped["requested_region"],
                        "region": mapped["region"],
                    }
                )
            )
            continue
        duplicate_summary = _duplicate_interval_summary(before, after, mapped=mapped, diff_stats=diff_stats, threshold=min_ratio)
        if duplicate_summary is not None:
            duplicate_intervals.append(duplicate_summary)
        if diff_stats["changed_bbox"] is None:
            skipped_pairs.append(
                _skipped_pair_summary(
                    before,
                    after,
                    mapped=mapped,
                    diff_stats=diff_stats,
                    threshold=min_ratio,
                    skip_reason="exact_duplicate_logical_frame" if duplicate_summary is not None else "no_pixel_change",
                )
            )
            continue
        if diff_stats["changed_pixel_ratio"] < min_ratio:
            skipped_pairs.append(
                _skipped_pair_summary(
                    before,
                    after,
                    mapped=mapped,
                    diff_stats=diff_stats,
                    threshold=min_ratio,
                    skip_reason="below_min_changed_pixel_ratio",
                )
            )
            continue
        changes.append(
            _with_common_fields(
                {
                    "object_type": "AgentSightSegmentChange",
                    "schema": "agentsight_segment_change_v1",
                    "status": "computed",
                    "before_frame": _frame_ref(before),
                    "after_frame": _frame_ref(after),
                    "before_restore_report": _safe_restore_report(before_report),
                    "after_restore_report": _safe_restore_report(after_report),
                    "requested_region": mapped["requested_region"],
                    "region": {"x": int(mapped["region"]["x"]), "y": int(mapped["region"]["y"]), "w": w, "h": h},
                    "region_coordinate_system": region_coordinate_system,
                    "changed_bbox": diff_stats["changed_bbox"],
                    "changed_pixel_count": diff_stats["changed_pixel_count"],
                    "total_pixel_count": diff_stats["total_pixel_count"],
                    "changed_pixel_ratio": diff_stats["changed_pixel_ratio"],
                    "tool_asserts_semantic_change": False,
                }
            )
        )
    change_runs = _build_change_runs(changes)
    return _with_common_fields(
        {
            "object_type": "AgentSightSegmentChangeIndex",
            "schema": "agentsight_segment_change_index_v1",
            "query_kind": "changes",
            "query_status": "generated_with_decode_errors" if decode_errors else "generated",
            "storage_format": "mkv_vfr",
            "region_coordinate_system": region_coordinate_system,
            "requested_region": dict(region) if isinstance(region, dict) else None,
            "query_window": {
                "start_time": start_time,
                "end_time": end_time,
                "pair_time_basis": "after_frame_timestamp",
            },
            "frames_considered": len(frames),
            "adjacent_pair_count": len(adjacent_pairs),
            "adjacent_pairs_considered": adjacent_pairs_considered,
            "max_pairs": max_pairs,
            "changes": changes,
            "change_count": len(changes),
            "change_runs": change_runs,
            "change_run_count": len(change_runs),
            "skipped_pairs": skipped_pairs,
            "skipped_pair_count": len(skipped_pairs),
            "duplicate_intervals": duplicate_intervals,
            "duplicate_interval_count": len(duplicate_intervals),
            "pixel_diff_threshold": min_ratio,
            "pixel_diff_threshold_enabled": min_ratio > 0.0,
            "decode_errors": decode_errors,
            "decode_error_count": len(decode_errors),
            "errors": decode_errors,
            "artifacts": [],
            "artifact_count": 0,
            "no_capture_performed": True,
            "no_media_exported": True,
        }
    )


def query_segment_review_clip(
    root: str | Path,
    *,
    region: dict[str, Any] | None = None,
    region_coordinate_system: str = "virtual_screen_pixels",
    start_time: Any = None,
    end_time: Any = None,
    max_frames: int = 32,
    scale_down: int = 1,
    max_artifacts: int = 0,
    output_dir: str | Path | None = None,
    request_id: str = "look-clip",
) -> dict[str, Any]:
    frames = _query_frames(root, start_time=start_time, end_time=end_time)[: max(0, int(max_frames or 0))]
    selected: list[dict[str, Any]] = []
    decode_errors: list[dict[str, Any]] = []
    clip_images: list[Any] = []
    for frame in frames:
        mapping_error = _frame_mapping_error(frame, region=region, region_coordinate_system=region_coordinate_system)
        if mapping_error is not None:
            decode_errors.append(mapping_error)
            continue
        mapped = _mapped_region(frame, region=region, region_coordinate_system=region_coordinate_system)
        if mapped is None:
            decode_errors.append(
                _with_common_fields(
                    {
                        "status": "decode_skipped_region_unavailable",
                        "frame": _frame_ref(frame),
                        "requested_region": dict(region) if isinstance(region, dict) else None,
                        "region_coordinate_system": region_coordinate_system,
                    }
                )
            )
            continue
        entry = _with_common_fields(
            {
                "object_type": "AgentSightSegmentClipFrame",
                "schema": "agentsight_segment_review_clip_frame_v1",
                **_frame_ref(frame),
                "requested_region": mapped["requested_region"],
                "region": mapped["region"],
                "region_coordinate_system": region_coordinate_system,
            }
        )
        selected.append(entry)
        if int(max_artifacts or 0) > 0:
            try:
                image, _report = _restore_region(
                    frame["segment_restore_ref"],
                    region=mapped["region"],
                    scale_down=scale_down,
                    blur_radius=0,
                )
                clip_images.append(image.convert("RGB"))
            except Exception:
                pass
    artifacts: list[dict[str, Any]] = []
    if int(max_artifacts or 0) > 0 and clip_images:
        output_root = Path(output_dir) if output_dir else Path(root) / "derived"
        output_root.mkdir(parents=True, exist_ok=True)
        path = output_root / f"{_safe_file_token(request_id)}-review-clip.gif"
        clip_images[0].save(path, save_all=True, append_images=clip_images[1:], duration=250, loop=0)
        artifacts.append(
            _with_common_fields(
                {
                    "object_type": "AgentSightSegmentReviewArtifact",
                    "schema": "agentsight_segment_review_artifact_v1",
                    "artifact_type": "review_clip_gif",
                    "artifact_role": "derived_review_animation",
                    "media_path_abs": str(path.resolve()),
                    "path": str(path.resolve()),
                    "raw_or_derived": "derived_review_only",
                    "artifact_is_canonical_evidence": False,
                    "canonical": False,
                    "visualization_only": True,
                    "selected_frame_count": len(selected),
                }
            )
        )
    return _with_common_fields(
        {
            "object_type": "AgentSightSegmentReviewClip",
            "schema": "agentsight_segment_review_clip_v1",
            "query_kind": "clip",
            "query_status": "generated_with_decode_errors" if decode_errors else "generated",
            "storage_format": "mkv_vfr",
            "region_coordinate_system": region_coordinate_system,
            "requested_region": dict(region) if isinstance(region, dict) else None,
            "selected_frame_count": len(selected),
            "frames": selected,
            "decode_errors": decode_errors,
            "decode_error_count": len(decode_errors),
            "errors": decode_errors,
            "artifacts": artifacts,
            "artifact_count": len(artifacts),
            "no_capture_performed": True,
            "no_media_exported": not bool(artifacts),
        }
    )


def query_segment_timeline_diff(
    root: str | Path,
    *,
    region: dict[str, Any] | None = None,
    region_coordinate_system: str = "virtual_screen_pixels",
    start_time: Any = None,
    end_time: Any = None,
    max_artifacts: int = 0,
    output_dir: str | Path | None = None,
    request_id: str = "look-diff-timeline",
) -> dict[str, Any]:
    index = query_segment_change_index(
        root,
        region=region,
        region_coordinate_system=region_coordinate_system,
        start_time=start_time,
        end_time=end_time,
        max_pairs=128,
        min_changed_pixel_ratio=0.0,
    )
    artifacts: list[dict[str, Any]] = []
    if int(max_artifacts or 0) > 0:
        output_root = Path(output_dir) if output_dir else Path(root) / "derived"
        output_root.mkdir(parents=True, exist_ok=True)
        for artifact_index, change in enumerate(index.get("changes") or []):
            if artifact_index >= int(max_artifacts):
                break
            try:
                before = change["before_frame"]["segment_restore_ref"]
                after = change["after_frame"]["segment_restore_ref"]
                path = output_root / f"{_safe_file_token(request_id)}-diff-{artifact_index:06d}.png"
                report = decode_segment_diff_to_png(before, after, output_path=path, region=change.get("region"))
                artifacts.append(
                    _with_common_fields(
                        {
                            "object_type": "AgentSightSegmentReviewArtifact",
                            "schema": "agentsight_segment_review_artifact_v1",
                            "artifact_type": "diff_heatmap",
                            "artifact_role": "derived_review_image",
                            "media_path_abs": report["review_media_path_abs"],
                            "path": report["review_media_path_abs"],
                            "raw_or_derived": "derived_review_only",
                            "artifact_is_canonical_evidence": False,
                            "canonical": False,
                            "visualization_only": True,
                            "change_index": artifact_index,
                        }
                    )
                )
            except Exception:
                continue
    index["query_kind"] = "diff"
    index["schema"] = "agentsight_segment_timeline_diff_v1"
    index["artifacts"] = artifacts
    index["artifact_count"] = len(artifacts)
    index["no_media_exported"] = not bool(artifacts)
    return index


def _decode_segment_frame_to_image(restore_ref: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    segment_path = Path(str(restore_ref.get("segment_path") or ""))
    storage_format = str(restore_ref.get("storage_format") or "")
    if storage_format == "binary_agseg" or segment_path.suffix.lower() == ".agseg":
        reader = BinarySegmentReader(segment_path)
        image, report = reader.restore_frame(str(restore_ref.get("frame_id") or ""))
        return image.convert("RGBA"), _with_common_fields(
            {
                **report,
                "status": "decoded",
                "storage_format": "binary_agseg",
                "canonical_evidence_source": BINARY_CONTAINER_MODEL,
                "segment_path": str(segment_path.resolve()),
                "file_written": False,
            }
        )
    image, report = decode_mkv_frame_to_image(restore_ref)
    return image.convert("RGBA"), report


def _restore_region(
    restore_ref: dict[str, Any],
    *,
    region: dict[str, Any],
    scale_down: int | float,
    blur_radius: int | float,
) -> tuple[Any, dict[str, Any]]:
    image, restore_report = _decode_segment_frame_to_image(restore_ref)
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
        "storage_format": restore_report.get("storage_format", "mkv_vfr"),
        "canonical_evidence_source": restore_report.get("canonical_evidence_source", MKV_CONTAINER_MODEL),
        "restore_report": restore_report,
    }


def _query_frames(root: str | Path, *, start_time: Any = None, end_time: Any = None) -> list[dict[str, Any]]:
    index = build_global_segment_frame_index(root)
    start_ms = _time_ms(start_time)
    end_ms = _time_ms(end_time)
    frames = [frame for frame in index.get("frames") or [] if isinstance(frame, dict)]
    if start_ms is not None:
        frames = [frame for frame in frames if int(frame.get("timestamp_epoch_ms") or 0) >= start_ms]
    if end_ms is not None:
        frames = [frame for frame in frames if int(frame.get("timestamp_epoch_ms") or 0) <= end_ms]
    return sorted(frames, key=lambda frame: int(frame.get("timestamp_epoch_ms") or 0))


def _common_mapping_error(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    region: dict[str, Any] | None,
    region_coordinate_system: str,
) -> dict[str, Any] | None:
    before_error = _frame_mapping_error(before, region=region, region_coordinate_system=region_coordinate_system)
    after_error = _frame_mapping_error(after, region=region, region_coordinate_system=region_coordinate_system)
    if before_error or after_error:
        return _with_common_fields(
            {
                "status": (before_error or after_error or {}).get("status", "decode_skipped_coordinate_mapping_failed"),
                "before_frame": _frame_ref(before),
                "after_frame": _frame_ref(after),
                "before_error": before_error,
                "after_error": after_error,
                "requested_region": dict(region) if isinstance(region, dict) else None,
                "region_coordinate_system": region_coordinate_system,
            }
        )
    if region is not None and region_coordinate_system == "virtual_screen_pixels":
        before_basis = _coordinate_mapping_basis(before)
        after_basis = _coordinate_mapping_basis(after)
        if before_basis and after_basis and before_basis != after_basis:
            return _with_common_fields(
                {
                    "status": "decode_skipped_inconsistent_coordinate_metadata",
                    "before_frame": _frame_ref(before),
                    "after_frame": _frame_ref(after),
                    "before_mapping_basis": before_basis,
                    "after_mapping_basis": after_basis,
                    "requested_region": dict(region) if isinstance(region, dict) else None,
                    "region_coordinate_system": region_coordinate_system,
                }
            )
    return None


def _frame_mapping_error(
    frame: dict[str, Any],
    *,
    region: dict[str, Any] | None,
    region_coordinate_system: str,
) -> dict[str, Any] | None:
    if region is None or region_coordinate_system != "virtual_screen_pixels":
        return None
    width = int(frame.get("width") or 0)
    height = int(frame.get("height") or 0)
    requested = _normalized_requested_region(region, width=width, height=height)
    stored_region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else None
    coordinate_system = frame.get("coordinate_system")
    if coordinate_system not in {"virtual_screen_pixels", "monitor_pixels"} or not stored_region:
        return _with_common_fields(
            {
                "status": "decode_skipped_missing_coordinate_metadata",
                "frame": _frame_ref(frame),
                "requested_region": requested,
                "region_coordinate_system": region_coordinate_system,
                "frame_coordinate_system": coordinate_system,
                "frame_screen_region": stored_region,
            }
        )
    x0 = int(stored_region.get("x", 0))
    y0 = int(stored_region.get("y", 0))
    w0 = int(stored_region.get("w", stored_region.get("width", width)))
    h0 = int(stored_region.get("h", stored_region.get("height", height)))
    left = max(int(requested["x"]), x0)
    top = max(int(requested["y"]), y0)
    right = min(int(requested["x"]) + int(requested["w"]), x0 + w0)
    bottom = min(int(requested["y"]) + int(requested["h"]), y0 + h0)
    if right <= left or bottom <= top:
        return _with_common_fields(
            {
                "status": "decode_skipped_no_overlap",
                "frame": _frame_ref(frame),
                "requested_region": requested,
                "region_coordinate_system": region_coordinate_system,
                "frame_coordinate_system": coordinate_system,
                "frame_screen_region": {"x": x0, "y": y0, "w": w0, "h": h0},
            }
        )
    return None


def _coordinate_mapping_basis(frame: dict[str, Any]) -> dict[str, Any] | None:
    stored_region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else None
    coordinate_system = frame.get("coordinate_system")
    if coordinate_system not in {"virtual_screen_pixels", "monitor_pixels"} or not stored_region:
        return None
    return {
        "coordinate_system": coordinate_system,
        "screen_region": {
            "x": int(stored_region.get("x", 0)),
            "y": int(stored_region.get("y", 0)),
            "w": int(stored_region.get("w", stored_region.get("width", frame.get("width") or 0))),
            "h": int(stored_region.get("h", stored_region.get("height", frame.get("height") or 0))),
        },
    }


def _common_mapped_region(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    region: dict[str, Any] | None,
    region_coordinate_system: str,
) -> dict[str, Any] | None:
    before_mapped = _mapped_region(before, region=region, region_coordinate_system=region_coordinate_system)
    after_mapped = _mapped_region(after, region=region, region_coordinate_system=region_coordinate_system)
    if before_mapped is None or after_mapped is None:
        return None
    w = min(int(before_mapped["region"]["w"]), int(after_mapped["region"]["w"]))
    h = min(int(before_mapped["region"]["h"]), int(after_mapped["region"]["h"]))
    if w <= 0 or h <= 0:
        return None
    return {
        "requested_region": before_mapped["requested_region"],
        "region": {"x": int(before_mapped["region"]["x"]), "y": int(before_mapped["region"]["y"]), "w": w, "h": h},
        "before_region": {**before_mapped["region"], "w": w, "h": h},
        "after_region": {**after_mapped["region"], "w": w, "h": h},
    }


def _mapped_region(
    frame: dict[str, Any],
    *,
    region: dict[str, Any] | None,
    region_coordinate_system: str,
) -> dict[str, Any] | None:
    width = int(frame.get("width") or 0)
    height = int(frame.get("height") or 0)
    requested = _normalized_requested_region(region, width=width, height=height)
    stored_region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else None
    coordinate_system = frame.get("coordinate_system")
    if region_coordinate_system == "virtual_screen_pixels" and coordinate_system in {"virtual_screen_pixels", "monitor_pixels"} and stored_region:
        x0 = int(stored_region.get("x", 0))
        y0 = int(stored_region.get("y", 0))
        w0 = int(stored_region.get("w", stored_region.get("width", width)))
        h0 = int(stored_region.get("h", stored_region.get("height", height)))
        left = max(int(requested["x"]), x0)
        top = max(int(requested["y"]), y0)
        right = min(int(requested["x"]) + int(requested["w"]), x0 + w0)
        bottom = min(int(requested["y"]) + int(requested["h"]), y0 + h0)
        if right <= left or bottom <= top:
            return None
        return {
            "requested_region": requested,
            "region": {"x": left - x0, "y": top - y0, "w": right - left, "h": bottom - top},
        }
    x, y, w, h = _region_tuple(requested, width, height)
    return {"requested_region": requested, "region": {"x": x, "y": y, "w": w, "h": h}}


def _normalized_requested_region(region: dict[str, Any] | None, *, width: int, height: int) -> dict[str, int]:
    if isinstance(region, dict):
        return {
            "x": int(region.get("x", 0)),
            "y": int(region.get("y", 0)),
            "w": int(region.get("w", region.get("width", width or 1))),
            "h": int(region.get("h", region.get("height", height or 1))),
        }
    return {"x": 0, "y": 0, "w": max(1, width), "h": max(1, height)}


def _diff_stats(before: Any, after: Any) -> dict[str, Any]:
    from PIL import ImageChops

    before_rgb = before.convert("RGB")
    after_rgb = after.convert("RGB")
    if before_rgb.size != after_rgb.size:
        after_rgb = after_rgb.resize(before_rgb.size)
    diff = ImageChops.difference(before_rgb, after_rgb)
    bbox = diff.getbbox()
    changed = 0
    if bbox is not None:
        pixels = diff.load()
        for y in range(diff.height):
            for x in range(diff.width):
                if max(pixels[x, y]):
                    changed += 1
    total = diff.width * diff.height
    return {
        "changed_pixel_count": changed,
        "total_pixel_count": total,
        "changed_pixel_ratio": round(changed / total, 8) if total else 0.0,
        "changed_bbox": None if bbox is None else {"x": bbox[0], "y": bbox[1], "w": bbox[2] - bbox[0], "h": bbox[3] - bbox[1]},
    }


def _build_change_runs(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    previous_after_id: str | None = None
    for change in changes:
        before_frame = change.get("before_frame") if isinstance(change.get("before_frame"), dict) else {}
        after_frame = change.get("after_frame") if isinstance(change.get("after_frame"), dict) else {}
        before_id = str(before_frame.get("frame_id") or before_frame.get("logical_frame_id") or "")
        after_id = str(after_frame.get("frame_id") or after_frame.get("logical_frame_id") or "")
        if change.get("status") != "computed" or not isinstance(change.get("changed_bbox"), dict):
            if current:
                runs.append(_change_run_summary(current))
                current = []
            previous_after_id = None
            continue
        if current and before_id != previous_after_id:
            runs.append(_change_run_summary(current))
            current = []
        current.append(change)
        previous_after_id = after_id
    if current:
        runs.append(_change_run_summary(current))
    return runs


def _change_run_summary(changes: list[dict[str, Any]]) -> dict[str, Any]:
    first = changes[0]
    last = changes[-1]
    first_after = first.get("after_frame") if isinstance(first.get("after_frame"), dict) else {}
    last_after = last.get("after_frame") if isinstance(last.get("after_frame"), dict) else {}
    start_ms = int(first_after.get("timestamp_epoch_ms") or 0)
    end_ms = int(last_after.get("timestamp_epoch_ms") or start_ms)
    peak = max(float(change.get("changed_pixel_ratio") or 0.0) for change in changes)
    bbox: dict[str, int] | None = None
    for change in changes:
        candidate = change.get("changed_bbox") if isinstance(change.get("changed_bbox"), dict) else None
        if candidate is not None:
            bbox = _bbox_union(bbox, candidate)
    return _with_common_fields(
        {
            "object_type": "AgentSightSegmentChangeRun",
            "schema": "agentsight_segment_change_run_v1",
            "status": "computed",
            "start_time": first_after.get("timestamp_iso"),
            "end_time": last_after.get("timestamp_iso"),
            "start_time_ms": start_ms,
            "end_time_ms": end_ms,
            "duration_ms": max(0, end_ms - start_ms),
            "pair_count": len(changes),
            "peak_changed_pixel_ratio": peak,
            "changed_bbox": bbox,
            "diff_coordinate_basis": "region_local_px",
            "requested_region": first.get("requested_region"),
            "region": first.get("region"),
            "region_coordinate_system": first.get("region_coordinate_system"),
            "before_frame": first.get("before_frame"),
            "after_frame": last.get("after_frame"),
            "tool_asserts_semantic_change": False,
        }
    )


def _bbox_union(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, int]:
    cx = int(candidate.get("x", 0))
    cy = int(candidate.get("y", 0))
    cw = int(candidate.get("w", 0))
    ch = int(candidate.get("h", 0))
    if current is None:
        return {"x": cx, "y": cy, "w": cw, "h": ch}
    left = min(int(current.get("x", 0)), cx)
    top = min(int(current.get("y", 0)), cy)
    right = max(int(current.get("x", 0)) + int(current.get("w", 0)), cx + cw)
    bottom = max(int(current.get("y", 0)) + int(current.get("h", 0)), cy + ch)
    return {"x": left, "y": top, "w": right - left, "h": bottom - top}


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


def _duplicate_interval_summary(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    mapped: dict[str, Any],
    diff_stats: dict[str, Any],
    threshold: float,
) -> dict[str, Any] | None:
    before_physical = before.get("physical_frame_id") or before.get("frame_id")
    after_physical = after.get("physical_frame_id") or after.get("frame_id")
    if before_physical != after_physical or diff_stats.get("changed_bbox") is not None:
        return None
    return _with_common_fields(
        {
            "object_type": "AgentSightSegmentDuplicateInterval",
            "schema": "agentsight_segment_duplicate_interval_v1",
            "status": "skipped_exact_duplicate_logical_frame",
            "skip_reason": "exact_duplicate_logical_frame",
            "before_frame": _frame_ref(before),
            "after_frame": _frame_ref(after),
            "physical_frame_id": after_physical,
            "requested_region": mapped["requested_region"],
            "region": mapped["region"],
            "changed_pixel_count": diff_stats["changed_pixel_count"],
            "total_pixel_count": diff_stats["total_pixel_count"],
            "changed_pixel_ratio": diff_stats["changed_pixel_ratio"],
            "threshold": threshold,
            "logical_duplicate": True,
            "tool_asserts_semantic_change": False,
        }
    )


def _skipped_pair_summary(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    mapped: dict[str, Any],
    diff_stats: dict[str, Any],
    threshold: float,
    skip_reason: str,
) -> dict[str, Any]:
    return _with_common_fields(
        {
            "object_type": "AgentSightSegmentSkippedChangePair",
            "schema": "agentsight_segment_skipped_change_pair_v1",
            "status": "skipped",
            "skip_reason": skip_reason,
            "before_frame": _frame_ref(before),
            "after_frame": _frame_ref(after),
            "requested_region": mapped["requested_region"],
            "region": mapped["region"],
            "changed_pixel_count": diff_stats["changed_pixel_count"],
            "total_pixel_count": diff_stats["total_pixel_count"],
            "changed_pixel_ratio": diff_stats["changed_pixel_ratio"],
            "threshold": threshold,
            "pixel_diff_threshold_enabled": threshold > 0.0,
            "tool_asserts_semantic_change": False,
        }
    )


def _frame_ref(frame: dict[str, Any]) -> dict[str, Any]:
    restore_ref = frame.get("segment_restore_ref") if isinstance(frame.get("segment_restore_ref"), dict) else {}
    return {
        "segment_id": frame.get("segment_id"),
        "segment_frame_id": frame.get("segment_frame_id") or frame.get("frame_id"),
        "frame_id": frame.get("frame_id"),
        "frame_index": frame.get("frame_index"),
        "logical_frame_id": frame.get("logical_frame_id", frame.get("frame_id")),
        "logical_frame_index": frame.get("logical_frame_index", frame.get("frame_index")),
        "physical_frame_id": frame.get("physical_frame_id", frame.get("frame_id")),
        "physical_frame_index": frame.get("physical_frame_index", frame.get("frame_index")),
        "duplicate_of_frame_id": frame.get("duplicate_of_frame_id"),
        "logical_duplicate": bool(frame.get("logical_duplicate")),
        "frame_kind": frame.get("frame_kind"),
        "source": frame.get("source"),
        "event_id": frame.get("event_id"),
        "timestamp_iso": frame.get("timestamp_iso"),
        "timestamp_epoch_ms": frame.get("timestamp_epoch_ms"),
        "storage_format": frame.get("storage_format"),
        "segment_path_abs": frame.get("segment_path_abs"),
        "segment_restore_ref": dict(restore_ref),
        "restore_ref": dict(restore_ref),
    }


def _safe_restore_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": report.get("status"),
        "segment_id": report.get("segment_id"),
        "storage_format": report.get("storage_format"),
        "frame_id": report.get("frame_id"),
        "frame_index": report.get("frame_index"),
        "logical_frame_id": report.get("logical_frame_id", report.get("frame_id")),
        "logical_frame_index": report.get("logical_frame_index", report.get("frame_index")),
        "physical_frame_id": report.get("physical_frame_id", report.get("frame_id")),
        "physical_frame_index": report.get("physical_frame_index", report.get("frame_index")),
        "duplicate_of_frame_id": report.get("duplicate_of_frame_id"),
        "logical_duplicate": bool(report.get("logical_duplicate")),
        "frame_kind": report.get("frame_kind"),
        "hash_ok": report.get("hash_ok"),
        "raw_or_derived": report.get("raw_or_derived"),
        "canonical_evidence_source": report.get("canonical_evidence_source"),
    }


def _region_tuple(region: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    width = max(1, int(width or 1))
    height = max(1, int(height or 1))
    x = max(0, min(width - 1, int(region.get("x", 0))))
    y = max(0, min(height - 1, int(region.get("y", 0))))
    w = max(1, int(region.get("w", region.get("width", width - x))))
    h = max(1, int(region.get("h", region.get("height", height - y))))
    return x, y, min(w, width - x), min(h, height - y)


def _time_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(float(value) * 1000 if float(value) < 10_000_000_000 else float(value))
    if isinstance(value, str):
        try:
            normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
            return int(datetime.fromisoformat(normalized).timestamp() * 1000)
        except Exception:
            return None
    return None


def _safe_file_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value))
    return cleaned.strip("-_")[:80] or "segment-review"


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
