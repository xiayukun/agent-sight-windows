from __future__ import annotations

from io import BytesIO
from typing import Any

from agentsight.evidence.store import EvidenceReplayService


MAX_SEQUENCE_GIF_PIXELS = 2_000_000


def build_sequence_gif_artifact(
    *,
    sequence_id: str,
    frames: list[dict[str, Any]],
    interval_ms: int,
    evidence: EvidenceReplayService,
    max_pixels: int = MAX_SEQUENCE_GIF_PIXELS,
) -> dict[str, Any]:
    artifact = _base_artifact(sequence_id=sequence_id, frames=frames, interval_ms=interval_ms)
    if not frames:
        return {**artifact, "status": "not_generated", "not_generated_reason": "no_frames"}
    if any(frame.get("media_mime") != "image/png" for frame in frames):
        return {**artifact, "status": "not_generated", "not_generated_reason": "non_png_frame_media"}

    dimensions = {(frame.get("width"), frame.get("height")) for frame in frames}
    if len(dimensions) != 1:
        return {**artifact, "status": "not_generated", "not_generated_reason": "inconsistent_frame_dimensions"}
    width, height = next(iter(dimensions))
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return {**artifact, "status": "not_generated", "not_generated_reason": "invalid_frame_dimensions"}
    if width * height * len(frames) > max_pixels:
        return {
            **artifact,
            "status": "not_generated",
            "not_generated_reason": "pixel_budget_exceeded",
            "pixel_budget": max_pixels,
        }

    try:
        from PIL import Image
    except Exception as exc:  # pragma: no cover - dependency dependent
        return {
            **artifact,
            "status": "not_generated",
            "not_generated_reason": "dependency_missing:Pillow",
            "failure_detail": str(exc),
            "install_hint": 'py -m pip install -e ".[windows-capture]"',
        }

    images = []
    try:
        for frame in frames:
            media_ref = frame.get("media_ref")
            if not isinstance(media_ref, str):
                return {**artifact, "status": "not_generated", "not_generated_reason": "frame_media_ref_missing"}
            media_path = evidence.root / media_ref
            with Image.open(media_path) as image:
                images.append(image.convert("P", palette=Image.ADAPTIVE))
        buffer = BytesIO()
        images[0].save(
            buffer,
            format="GIF",
            save_all=True,
            append_images=images[1:],
            duration=interval_ms,
            loop=0,
        )
    except Exception as exc:
        return {
            **artifact,
            "status": "generation_failed",
            "failure_code": "SEQUENCE_MEDIA_RENDER_FAILED",
            "failure_detail": str(exc),
            "frame_sequence_preserved": True,
        }

    media = evidence.write_media_bytes(f"{sequence_id}.gif", buffer.getvalue())
    return {
        **artifact,
        "status": "generated",
        "media_ref": media["media_ref"],
        "media_sha256": media["media_sha256"],
        "media_size_bytes": media["media_size_bytes"],
    }


def _base_artifact(*, sequence_id: str, frames: list[dict[str, Any]], interval_ms: int) -> dict[str, Any]:
    return {
        "object_type": "SequenceMedia",
        "role": "animation_preview",
        "media_role": "sequence_visualization",
        "media_mime": "image/gif",
        "media_format": "gif",
        "source_sequence_id": sequence_id,
        "generated_from_frame_refs": [frame.get("observation_id") for frame in frames],
        "generated_from_media_refs": [
            {
                "frame_ref": frame.get("observation_id"),
                "media_ref": frame.get("media_ref"),
                "media_sha256": frame.get("media_sha256"),
            }
            for frame in frames
            if frame.get("media_ref")
        ],
        "frame_count": len(frames),
        "interval_ms": interval_ms,
        "coordinate_system": frames[0].get("coordinate_system") if frames else None,
        "screen_region": (frames[0].get("screen_region") or frames[0].get("region")) if frames else None,
        "bounded": True,
        "derived_from_frames": True,
        "canonical": True,
        "semantic_extraction": False,
        "ocr_used": False,
        "window_semantics_used": False,
    }
