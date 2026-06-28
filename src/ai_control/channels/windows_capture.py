from __future__ import annotations

import importlib.metadata
import importlib.util
import struct
import time
import uuid
import zlib
from typing import Any

from ai_control.channels.base import ChannelFailure
from ai_control.evidence.store import EvidenceReplayService


class WindowsCaptureObservationChannel:
    name = "windows_capture_wgc_observation"
    channel_type = "observation"

    def __init__(self, *, monitor_index: int = 1, capture_timeout_seconds: float = 3.0) -> None:
        self.monitor_index = monitor_index
        self.capture_timeout_seconds = capture_timeout_seconds

    def describe(self) -> dict[str, Any]:
        available = self._dependency_available()
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available" if available else "unavailable",
            "implementation": "windows-capture.WindowsCapture",
            "source_kind": "software_screen_capture",
            "target_kind": "monitor",
            "monitor_index": self.monitor_index,
            "window_targeting_enabled": False,
            "modes": ["fullscreen", "region", "after_action", "sequence"],
            "supports_sequence": True,
            "supports_change_observation": True,
            "max_frames": 5,
            "max_sequence_duration_ms": 1000,
            "media_mime_types": ["image/png"],
            "dependencies": [
                {
                    "name": "windows-capture",
                    "module": "windows_capture",
                    "installed": available,
                    "version": self._version("windows-capture") if available else None,
                    "optional_extra": "windows-capture",
                    "install_required": not available,
                }
            ],
            "install_hint": 'py -m pip install windows-capture',
            "unavailable_reason": None if available else "dependency_missing:windows-capture",
        }

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        if not self._dependency_available():
            raise ChannelFailure(
                "OBSERVATION_DEPENDENCY_MISSING",
                stage="WindowsCaptureObservationChannel",
                detail="dependency_missing:windows-capture",
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="windows-capture.WindowsCapture",
                requested_mode=payload.get("mode", "fullscreen"),
                requested_region=payload.get("region"),
            )

        mode = payload.get("mode", "fullscreen")
        frame = self._capture_full_frame()
        full_width = int(frame["width"])
        full_height = int(frame["height"])
        region = payload.get("region") if mode == "region" else {"x": 0, "y": 0, "width": full_width, "height": full_height}
        bgra_bytes = frame["bgra_bytes"]
        if mode == "region":
            bgra_bytes = self._crop_bgra_bytes(bgra_bytes, full_width=full_width, full_height=full_height, region=region)
            width = region["width"]
            height = region["height"]
        else:
            width = full_width
            height = full_height

        observation_id = f"obs-{uuid.uuid4().hex[:10]}"
        png_bytes = self._png_from_bgra_bytes(bgra_bytes, width=width, height=height)
        media = evidence.media_bytes_record(png_bytes)
        captured_at = time.time()
        return {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": mode,
            "timestamp": captured_at,
            "captured_at": captured_at,
            "channel_ref": self.name,
            "implementation": "windows-capture.WindowsCapture",
            "source_kind": "software_screen_capture",
            "target_kind": "monitor",
            "monitor_index": self.monitor_index,
            "media_mime": "image/png",
            "media_format": "png",
            "width": width,
            "height": height,
            "screen_region": region,
            "coordinate_system": "monitor_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            "canonical_storage_target": ".mkv",
            "default_media_file_written": False,
            "_bgra_bytes": bgra_bytes,
            **media,
        }

    def _dependency_available(self) -> bool:
        return importlib.util.find_spec("windows_capture") is not None

    def _version(self, distribution: str) -> str:
        try:
            return importlib.metadata.version(distribution)
        except Exception:
            return "unknown"

    def _capture_full_frame(self) -> dict[str, Any]:
        try:
            from windows_capture import WindowsCapture
        except Exception as exc:  # pragma: no cover - depends on optional dependency state
            raise ChannelFailure(
                "OBSERVATION_DEPENDENCY_MISSING",
                stage="WindowsCaptureObservationChannel.import",
                detail=str(exc),
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="windows-capture.WindowsCapture",
            ) from exc

        captured: dict[str, Any] = {}
        try:
            capture = WindowsCapture(cursor_capture=False, draw_border=False, monitor_index=self.monitor_index)

            @capture.event
            def on_frame_arrived(frame: Any, control: Any) -> None:
                captured["width"] = int(frame.width)
                captured["height"] = int(frame.height)
                captured["bgra_bytes"] = frame.frame_buffer.copy().tobytes()
                control.stop()

            @capture.event
            def on_closed() -> None:
                captured["closed"] = True

            control = capture.start_free_threaded()
            deadline = time.time() + self.capture_timeout_seconds
            while time.time() < deadline and "bgra_bytes" not in captured and not control.is_finished():
                time.sleep(0.02)
            if "bgra_bytes" not in captured:
                try:
                    control.stop()
                finally:
                    control.wait()
                raise ChannelFailure(
                    "SCREENSHOT_CAPTURE_TIMEOUT",
                    stage="WindowsCaptureObservationChannel.capture",
                    detail=f"no frame arrived within {self.capture_timeout_seconds:.1f}s",
                    channel_ref=self.name,
                    channel_type=self.channel_type,
                    implementation="windows-capture.WindowsCapture",
                )
            control.wait()
        except ChannelFailure:
            raise
        except Exception as exc:  # pragma: no cover - depends on host graphics state
            raise ChannelFailure(
                "SCREENSHOT_CAPTURE_FAILED",
                stage="WindowsCaptureObservationChannel.capture",
                detail=str(exc),
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="windows-capture.WindowsCapture",
            ) from exc
        return captured

    def _crop_bgra_bytes(
        self,
        data: bytes,
        *,
        full_width: int,
        full_height: int,
        region: dict[str, int],
    ) -> bytes:
        x = region["x"]
        y = region["y"]
        width = region["width"]
        height = region["height"]
        if x + width > full_width or y + height > full_height:
            raise ChannelFailure(
                "SCREENSHOT_CAPTURE_FAILED",
                stage="WindowsCaptureObservationChannel.region",
                detail=f"region outside captured monitor bounds: {region} > {full_width}x{full_height}",
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="windows-capture.WindowsCapture",
                requested_mode="region",
                requested_region=region,
            )
        row_size = full_width * 4
        cropped = bytearray()
        for row in range(y, y + height):
            start = row * row_size + x * 4
            cropped.extend(data[start : start + width * 4])
        return bytes(cropped)

    def _png_from_bgra_bytes(self, data: bytes, *, width: int, height: int) -> bytes:
        def chunk(kind: bytes, payload: bytes) -> bytes:
            return (
                struct.pack(">I", len(payload))
                + kind
                + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
            )

        expected = width * height * 4
        if len(data) != expected:
            raise ChannelFailure(
                "SCREENSHOT_CAPTURE_FAILED",
                stage="WindowsCaptureObservationChannel.encode_png",
                detail=f"unexpected BGRA byte size: {len(data)} != {expected}",
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation="windows-capture.WindowsCapture",
            )

        rows = bytearray()
        row_size = width * 4
        for y in range(height):
            rows.append(0)
            row = data[y * row_size : (y + 1) * row_size]
            for index in range(0, len(row), 4):
                b, g, r, _a = row[index : index + 4]
                rows.extend((r, g, b))
        header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")
