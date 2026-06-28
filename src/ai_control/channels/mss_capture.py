from __future__ import annotations

import importlib.util
import time
import uuid
from typing import Any

from ai_control.channels.base import ChannelFailure
from ai_control.evidence.store import EvidenceReplayService


class MssObservationChannel:
    name = "mss_observation"
    channel_type = "observation"

    def describe(self) -> dict[str, Any]:
        available = importlib.util.find_spec("mss") is not None
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available" if available else "unavailable",
            "implementation": "mss",
            "source_kind": "software_screen_capture",
            "modes": ["fullscreen", "region", "after_action", "sequence"],
            "supports_sequence": True,
            "supports_change_observation": True,
            "max_frames": 5,
            "max_sequence_duration_ms": 1000,
            "media_mime_types": ["image/png"],
            "dependencies": [
                {
                    "name": "mss",
                    "module": "mss",
                    "installed": available,
                    "version": self._version("mss") if available else None,
                    "optional_extra": "windows-capture",
                    "install_required": not available,
                }
            ],
            "install_hint": 'py -m pip install -e ".[windows-capture]"',
            "unavailable_reason": None if available else "dependency_missing:mss",
        }

    def _version(self, distribution: str) -> str:
        try:
            import importlib.metadata

            return importlib.metadata.version(distribution)
        except Exception:
            return "unknown"

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        if importlib.util.find_spec("mss") is None:
            raise ChannelFailure(
                "OBSERVATION_DEPENDENCY_MISSING",
                stage="MssObservationChannel",
                detail="dependency_missing:mss",
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="mss",
                requested_mode=payload.get("mode", "fullscreen"),
                requested_region=payload.get("region"),
            )
        try:
            import mss
            import mss.tools
        except Exception as exc:  # pragma: no cover - depends on optional dependency state
            raise ChannelFailure(
                "OBSERVATION_DEPENDENCY_MISSING",
                stage="MssObservationChannel.import",
                detail=str(exc),
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="mss",
                requested_mode=payload.get("mode", "fullscreen"),
                requested_region=payload.get("region"),
            ) from exc

        mode = payload.get("mode", "fullscreen")
        observation_id = f"obs-{uuid.uuid4().hex[:10]}"
        try:
            mss_factory = getattr(mss, "MSS", mss.mss)
            with mss_factory() as sct:
                monitor = dict(sct.monitors[0])
                if mode == "region":
                    region = payload["region"]
                    monitor = {
                        "left": region["x"],
                        "top": region["y"],
                        "width": region["width"],
                        "height": region["height"],
                    }
                shot = sct.grab(monitor)
                png_bytes = mss.tools.to_png(shot.rgb, shot.size)
                bgra_bytes = bytes(shot.bgra)
        except Exception as exc:  # pragma: no cover - depends on host graphics state
            raise ChannelFailure(
                "SCREENSHOT_CAPTURE_FAILED",
                stage="MssObservationChannel.capture",
                detail=str(exc),
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="mss",
                requested_mode=mode,
                requested_region=payload.get("region"),
            ) from exc

        media = evidence.media_bytes_record(png_bytes)
        return {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": mode,
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "media_mime": "image/png",
            "media_format": "png",
            "width": shot.size.width,
            "height": shot.size.height,
            "screen_region": payload.get("region", {"x": monitor["left"], "y": monitor["top"], "width": shot.size.width, "height": shot.size.height}),
            "coordinate_system": "virtual_screen_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            "canonical_storage_target": ".mkv",
            "default_media_file_written": False,
            "_bgra_bytes": bgra_bytes,
            **media,
        }
