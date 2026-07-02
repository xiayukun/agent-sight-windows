from __future__ import annotations

from io import BytesIO
from typing import Any


def analyze_capture_image_bytes(data: bytes | bytearray | memoryview | None) -> dict[str, Any]:
    """Return pixel-quality facts for a captured frame.

    This is evidence quality checking only. It does not OCR, inspect UI
    semantics, or judge business success.
    """

    if not isinstance(data, (bytes, bytearray, memoryview)) or not data:
        return {
            "capture_content_degenerate": False,
            "degenerate_reasons": [],
            "capture_quality_checked": False,
            "capture_quality_unavailable_reason": "media_bytes_missing",
        }
    try:
        from PIL import Image

        with Image.open(BytesIO(bytes(data))) as image:
            sample = image.convert("RGB")
            sample.thumbnail((96, 96))
            data_source = getattr(sample, "get_flattened_data", sample.getdata)
            pixels = list(data_source())
    except Exception as exc:
        return {
            "capture_content_degenerate": True,
            "degenerate_reasons": [f"capture_quality_decode_failed:{type(exc).__name__}"],
            "capture_quality_checked": True,
        }

    if not pixels:
        return {
            "capture_content_degenerate": True,
            "degenerate_reasons": ["empty_pixel_sample"],
            "capture_quality_checked": True,
        }

    reasons: list[str] = []
    channels = list(zip(*pixels))
    ranges = [max(channel) - min(channel) for channel in channels]
    near_single_color = max(ranges) <= 1
    if max(ranges) <= 1:
        pass
    blackish = sum(1 for red, green, blue in pixels if red <= 8 and green <= 8 and blue <= 8)
    if blackish / len(pixels) >= 0.995:
        reasons.append("near_all_black_frame")

    return {
        "capture_content_degenerate": bool(reasons),
        "degenerate_reasons": reasons,
        "capture_quality_checked": True,
        "capture_quality_sample_pixels": len(pixels),
        "capture_quality_near_single_color": near_single_color,
    }
