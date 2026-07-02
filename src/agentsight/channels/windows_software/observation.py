from __future__ import annotations

import ctypes
import struct
import sys
import time
import uuid
from typing import Any

from agentsight.channels.base import ChannelFailure
from agentsight.evidence.store import EvidenceReplayService


SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


class WindowsSoftwareObservationChannel:
    name = "windows_software_observation"
    channel_type = "observation"

    def describe(self) -> dict[str, Any]:
        available, reason = self._probe_available()
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available" if available else "unavailable",
            "implementation": "ctypes_win32_gdi_bmp",
            "source_kind": "software_screen_capture",
            "modes": ["fullscreen", "region", "after_action", "sequence"],
            "supports_sequence": True,
            "supports_change_observation": True,
            "max_frames": 5,
            "max_sequence_duration_ms": 1000,
            "dependencies": [],
            "install_hint": None,
            "unavailable_reason": None if available else reason,
        }

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        available, reason = self._probe_available()
        if not available:
            raise ChannelFailure(
                "SCREEN_CAPTURE_UNAVAILABLE",
                stage="WindowsSoftwareObservationChannel",
                detail=reason,
                retryable=False,
            )

        mode = payload.get("mode", "fullscreen")
        screen_region = self._screen_region(payload)
        observation_id = f"obs-{uuid.uuid4().hex[:10]}"

        try:
            bmp_bytes = self._capture_bmp(**screen_region)
        except ChannelFailure:
            raise
        except OSError as exc:
            raise ChannelFailure(
                "SCREENSHOT_CAPTURE_FAILED",
                stage="WindowsSoftwareObservationChannel.capture",
                detail=str(exc),
            ) from exc

        media = evidence.media_bytes_record(bmp_bytes)
        frame: dict[str, Any] = {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": mode,
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "media_mime": "image/bmp",
            "media_format": "bmp",
            "width": screen_region["width"],
            "height": screen_region["height"],
            "screen_region": screen_region,
            "coordinate_system": "virtual_screen_pixels",
            "scale_factor": 1.0,
            "capture_status": "captured",
            "media_integrity_checked": True,
            "canonical_storage_target": ".mkv",
            "default_media_file_written": False,
            "_bgra_bytes": bmp_bytes[54:],
            **media,
        }
        if mode == "region":
            frame["region"] = payload["region"]
        if "after_action_ref" in payload:
            frame["after_action_ref"] = payload["after_action_ref"]
        return frame

    def _probe_available(self) -> tuple[bool, str | None]:
        if sys.platform != "win32":
            return False, "non_windows_platform"
        try:
            virtual = self._virtual_screen()
            self._capture_bmp(x=virtual["x"], y=virtual["y"], width=1, height=1)
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _screen_region(self, payload: dict[str, Any]) -> dict[str, int]:
        virtual = self._virtual_screen()
        mode = payload.get("mode", "fullscreen")
        if mode == "region":
            region = payload["region"]
            x = int(region["x"])
            y = int(region["y"])
            width = int(region["width"])
            height = int(region["height"])
            if x < virtual["x"] or y < virtual["y"]:
                raise ChannelFailure(
                    "REGION_CAPTURE_UNAVAILABLE",
                    stage="WindowsSoftwareObservationChannel.region",
                    detail="region starts outside virtual screen",
                )
            if x + width > virtual["x"] + virtual["width"] or y + height > virtual["y"] + virtual["height"]:
                raise ChannelFailure(
                    "REGION_CAPTURE_UNAVAILABLE",
                    stage="WindowsSoftwareObservationChannel.region",
                    detail="region extends outside virtual screen",
                )
            return {"x": x, "y": y, "width": width, "height": height}
        return virtual

    def _virtual_screen(self) -> dict[str, int]:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        if width <= 0 or height <= 0:
            x = 0
            y = 0
            width = int(user32.GetSystemMetrics(SM_CXSCREEN))
            height = int(user32.GetSystemMetrics(SM_CYSCREEN))
        if width <= 0 or height <= 0:
            raise ChannelFailure(
                "SCREEN_CAPTURE_UNAVAILABLE",
                stage="WindowsSoftwareObservationChannel.virtual_screen",
                detail="screen dimensions are unavailable",
                retryable=False,
            )
        return {"x": x, "y": y, "width": width, "height": height}

    def _capture_bmp(self, *, x: int, y: int, width: int, height: int) -> bytes:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

        user32.GetDC.restype = ctypes.c_void_p
        user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
        gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi32.SelectObject.restype = ctypes.c_void_p
        gdi32.BitBlt.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        gdi32.BitBlt.restype = ctypes.c_int
        gdi32.GetDIBits.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi32.DeleteDC.argtypes = [ctypes.c_void_p]

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            raise ChannelFailure(
                "SCREENSHOT_PERMISSION_DENIED",
                stage="WindowsSoftwareObservationChannel.GetDC",
                detail="GetDC returned null",
            )
        memory_dc = None
        bitmap = None
        old_object = None
        try:
            memory_dc = gdi32.CreateCompatibleDC(screen_dc)
            if not memory_dc:
                raise OSError("CreateCompatibleDC returned null")
            bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
            if not bitmap:
                raise OSError("CreateCompatibleBitmap returned null")
            old_object = gdi32.SelectObject(memory_dc, bitmap)
            if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, x, y, SRCCOPY | CAPTUREBLT):
                capture_error = ctypes.get_last_error()
                if not gdi32.BitBlt(memory_dc, 0, 0, width, height, screen_dc, x, y, SRCCOPY):
                    plain_error = ctypes.get_last_error()
                    raise OSError(f"BitBlt failed capture_error={capture_error} plain_error={plain_error}")

            pixel_size = width * height * 4
            pixels = ctypes.create_string_buffer(pixel_size)
            info = BITMAPINFO()
            info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            info.bmiHeader.biWidth = width
            info.bmiHeader.biHeight = -height
            info.bmiHeader.biPlanes = 1
            info.bmiHeader.biBitCount = 32
            info.bmiHeader.biCompression = 0
            info.bmiHeader.biSizeImage = pixel_size
            info.bmiHeader.biXPelsPerMeter = 2835
            info.bmiHeader.biYPelsPerMeter = 2835
            scan_lines = gdi32.GetDIBits(
                memory_dc,
                bitmap,
                0,
                height,
                pixels,
                ctypes.byref(info),
                DIB_RGB_COLORS,
            )
            if scan_lines != height:
                raise OSError(f"GetDIBits returned {scan_lines} scan lines")

            file_header_size = 14
            info_header_size = 40
            data = pixels.raw
            file_size = file_header_size + info_header_size + len(data)
            file_header = b"BM" + struct.pack("<IHHI", file_size, 0, 0, file_header_size + info_header_size)
            info_header = struct.pack(
                "<IiiHHIIiiII",
                info_header_size,
                width,
                -height,
                1,
                32,
                0,
                len(data),
                2835,
                2835,
                0,
                0,
            )
            return file_header + info_header + data
        finally:
            if old_object and memory_dc:
                gdi32.SelectObject(memory_dc, old_object)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if memory_dc:
                gdi32.DeleteDC(memory_dc)
            if screen_dc:
                user32.ReleaseDC(None, screen_dc)
