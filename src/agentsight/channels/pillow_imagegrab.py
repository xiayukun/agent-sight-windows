from __future__ import annotations

import importlib.util
import time
import uuid
from io import BytesIO
from typing import Any

from agentsight.channels.base import ChannelFailure
from agentsight.evidence.store import EvidenceReplayService


class PillowImageGrabObservationChannel:
    name = "pillow_imagegrab_observation"
    channel_type = "observation"

    def describe(self) -> dict[str, Any]:
        available = importlib.util.find_spec("PIL") is not None
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available" if available else "unavailable",
            "implementation": "Pillow.ImageGrab",
            "source_kind": "software_screen_capture",
            "modes": ["fullscreen", "region", "after_action", "sequence"],
            "supports_sequence": True,
            "supports_change_observation": True,
            "max_frames": 5,
            "max_sequence_duration_ms": 1000,
            "media_mime_types": ["image/png"],
            "dependencies": [
                {
                    "name": "Pillow",
                    "module": "PIL",
                    "installed": available,
                    "version": self._version("Pillow") if available else None,
                    "optional_extra": "windows-capture",
                    "install_required": not available,
                }
            ],
            "install_hint": 'py -m pip install -e ".[windows-capture]"',
            "unavailable_reason": None if available else "dependency_missing:Pillow",
        }

    def _version(self, distribution: str) -> str:
        try:
            import importlib.metadata

            return importlib.metadata.version(distribution)
        except Exception:
            return "unknown"

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        if importlib.util.find_spec("PIL") is None:
            raise ChannelFailure(
                "OBSERVATION_DEPENDENCY_MISSING",
                stage="PillowImageGrabObservationChannel",
                detail="dependency_missing:Pillow",
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="Pillow.ImageGrab",
                requested_mode=payload.get("mode", "fullscreen"),
                requested_region=payload.get("region"),
            )
        try:
            from PIL import ImageGrab
        except Exception as exc:  # pragma: no cover - depends on optional dependency state
            raise ChannelFailure(
                "OBSERVATION_DEPENDENCY_MISSING",
                stage="PillowImageGrabObservationChannel.import",
                detail=str(exc),
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="Pillow.ImageGrab",
                requested_mode=payload.get("mode", "fullscreen"),
                requested_region=payload.get("region"),
            ) from exc

        mode = payload.get("mode", "fullscreen")
        bbox = None
        if mode == "region":
            region = payload["region"]
            bbox = (region["x"], region["y"], region["x"] + region["width"], region["y"] + region["height"])
        observation_id = f"obs-{uuid.uuid4().hex[:10]}"
        try:
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            output = BytesIO()
            image.save(output, format="PNG")
            png_bytes = output.getvalue()
            bgra_bytes = image.convert("RGBA").tobytes("raw", "BGRA")
        except Exception as exc:  # pragma: no cover - depends on host graphics state
            raise ChannelFailure(
                "SCREENSHOT_CAPTURE_FAILED",
                stage="PillowImageGrabObservationChannel.capture",
                detail=str(exc),
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="Pillow.ImageGrab",
                requested_mode=mode,
                requested_region=payload.get("region"),
            ) from exc

        media = evidence.media_bytes_record(png_bytes)
        width, height = image.size
        screen_region = payload.get("region", {"x": 0, "y": 0, "width": width, "height": height})
        return {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": mode,
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "media_mime": "image/png",
            "media_format": "png",
            "width": width,
            "height": height,
            "screen_region": screen_region,
            "coordinate_system": "virtual_screen_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            "canonical_storage_target": ".mkv",
            "default_media_file_written": False,
            "_bgra_bytes": bgra_bytes,
            **media,
        }
