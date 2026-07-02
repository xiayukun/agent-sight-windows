from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from agentsight.evidence.store import EvidenceReplayService
from agentsight.segments.binary_container import BinarySegmentWriter
from agentsight.segments.manifest import boundary_facts
from agentsight.segments.mkv_container import MkvSegmentWriter, timestamp_iso
from agentsight.segments.writer import SegmentWriter


class SegmentFrameRecorder:
    """Canonical MKV VFR recorder over already captured raw frames.

    Explicit legacy modes are retained for migration tests/tools, but default
    runtime storage is always ``mkv_vfr``.
    """

    def __init__(
        self,
        evidence: EvidenceReplayService,
        *,
        enabled: bool = True,
        daily_segment_boundary_local_time: str = "00:00",
        storage_format: str = "mkv_vfr",
        segment_bucket_granularity: str = "hourly",
        image_encoding: str = "ffv1",
        image_quality: int = 70,
        image_lossless: bool = True,
        max_segment_size_mb: int | float | None = 1024,
    ) -> None:
        self.evidence = evidence
        self.enabled = bool(enabled)
        self.daily_segment_boundary_local_time = daily_segment_boundary_local_time
        self.storage_format = _normalize_storage_format(storage_format)
        self.segment_bucket_granularity = _normalize_bucket_granularity(segment_bucket_granularity)
        self.image_encoding = image_encoding or "webp"
        self.image_quality = image_quality
        self.image_lossless = bool(image_lossless)
        self.max_segment_size_mb = _normalize_max_segment_size_mb(max_segment_size_mb)
        self.max_segment_size_bytes = int(self.max_segment_size_mb * 1024 * 1024) if self.max_segment_size_mb > 0 else 0
        self.segment_id = ""
        self.segment_dir = _stable_segment_dir(evidence.root)
        self.segment_path = self.segment_dir / f"segment-uninitialized{_segment_suffix(self.storage_format)}"
        self.writer: MkvSegmentWriter | BinarySegmentWriter | SegmentWriter | None = None
        self._active_bucket: str | None = None
        self._segment_sequence = 0
        self._lock = threading.RLock()
        self._last_quota_check_at = 0.0
        self._segment_started_at_ms = 0
        self._max_segment_duration_ms = 60 * 60 * 1000

    def record_frame(
        self,
        frame: dict[str, Any],
        *,
        source: str | None,
        event_id: str | None,
        view_id: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._record_frame_locked(frame, source=source, event_id=event_id, view_id=view_id)

    def _record_frame_locked(
        self,
        frame: dict[str, Any],
        *,
        source: str | None,
        event_id: str | None,
        view_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            return self._not_recorded("segment_recorder_disabled", frame=frame, source=source, event_id=event_id, view_id=view_id)
        if frame.get("capture_content_degenerate"):
            return self._not_recorded("capture_content_degenerate", frame=frame, source=source, event_id=event_id, view_id=view_id)
        image = self._image_from_frame(frame)
        if image is None:
            return self._not_recorded("frame_payload_missing", frame=frame, source=source, event_id=event_id, view_id=view_id)
        width, height = image.size
        if width <= 0 or height <= 0:
            return self._not_recorded("frame_geometry_missing", frame=frame, source=source, event_id=event_id, view_id=view_id)
        captured_at_ms = _timestamp_ms(frame.get("captured_at") or frame.get("timestamp"))
        bucket = _bucket_from_time(captured_at_ms / 1000.0, self.daily_segment_boundary_local_time, self.segment_bucket_granularity)
        writer_width = getattr(self.writer, "width", width) if self.writer is not None else width
        writer_height = getattr(self.writer, "height", height) if self.writer is not None else height
        segment_too_old = self._segment_started_at_ms and captured_at_ms - self._segment_started_at_ms >= self._max_segment_duration_ms
        segment_too_large = self._segment_size_limit_reached()
        if self.writer is None or bucket != self._active_bucket or width != writer_width or height != writer_height or segment_too_old or segment_too_large:
            self._open_segment(bucket=bucket, width=width, height=height)
            if segment_too_large:
                self._apply_storage_quota_best_effort_throttled(force=True)
        try:
            record = self._add_frame_to_writer(frame=frame, image=image, captured_at_ms=captured_at_ms, source=source, event_id=event_id)
            if self._segment_size_limit_reached():
                self.rotate_segment(reason="max_segment_size_reached")
                self._apply_storage_quota_best_effort_throttled(force=True)
            else:
                self._apply_storage_quota_best_effort_throttled()
        except Exception as exc:
            return self._not_recorded(f"segment_record_failed:{type(exc).__name__}", frame=frame, source=source, event_id=event_id, view_id=view_id)
        return self._recorded_ref(record, view_id=view_id)

    def _recorded_ref(self, record: dict[str, Any], *, view_id: str | None) -> dict[str, Any]:
        restore_ref = self._restore_ref(record)
        return {
            "object_type": "AgentSightSegmentFrameRef",
            "schema": "agentsight_segment_v1",
            "status": "recorded",
            "segment_id": self.segment_id,
            "storage_format": self.storage_format,
            "segment_path_abs": str(self.segment_path.resolve()),
            "manifest_path_abs": self._manifest_path_abs(),
            "index_path_abs": self._index_path_abs(),
            "manifest_embedded": self.storage_format == "binary_agseg",
            "frame_id": record["frame_id"],
            "logical_frame_id": record.get("logical_frame_id", record["frame_id"]),
            "logical_frame_index": record.get("logical_frame_index", record.get("frame_index")),
            "physical_frame_id": record.get("physical_frame_id", record["frame_id"]),
            "physical_frame_index": record.get("physical_frame_index", record.get("frame_index")),
            "duplicate_of_frame_id": record.get("duplicate_of_frame_id"),
            "logical_duplicate": bool(record.get("logical_duplicate")),
            "frame_kind": record["frame_kind"],
            "frame_index": record["frame_index"],
            "pts_ms": record.get("pts_ms", record.get("timestamp_monotonic_ms", 0)),
            "playback_pts_ms": record.get("playback_pts_ms"),
            "playback_time_basis": record.get("playback_time_basis"),
            "source": record["source"],
            "event_id": record.get("event_id"),
            "view_id": view_id,
            "restore_ref": restore_ref,
            "raw_or_derived": "raw",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "object_type": "AgentSightSegmentRecorderStatus",
                "schema": "agentsight_segment_recorder_v1",
                "status": "active" if self.enabled else "disabled",
                "enabled": self.enabled,
                "storage_format": self.storage_format,
                "segment_bucket_granularity": self.segment_bucket_granularity,
                "image_encoding": self.image_encoding,
                "image_quality": self.image_quality,
                "image_lossless": self.image_lossless,
                "max_segment_size_mb": self.max_segment_size_mb,
                "max_segment_size_bytes": self.max_segment_size_bytes,
                "current_segment_size_bytes": self._current_segment_size_bytes(),
                "segment_id": self.segment_id,
                "segment_path_abs": str(self.segment_path.resolve()),
                "manifest_path_abs": self._manifest_path_abs(),
                "index_path_abs": self._index_path_abs(),
                "manifest_embedded": self.storage_format == "binary_agseg",
                "frame_count": self._frame_count(),
                "raw_frames_are_canonical_evidence": self.storage_format == "mkv_vfr",
                "derived_review_video_is_canonical": False,
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }

    def manifest(self) -> dict[str, Any]:
        with self._lock:
            return self.writer.manifest if self.writer else self._empty_manifest()

    def close(self) -> None:
        with self._lock:
            if self.writer is not None:
                self.writer.close()
                self.writer = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def rotate_segment(self, *, reason: str, bucket: str | None = None) -> dict[str, Any]:
        with self._lock:
            if self.writer is not None:
                self.writer.close()
                self.writer = None
            self._active_bucket = None
            return {
                "object_type": "AgentSightSegmentRotationReport",
                "schema": "agentsight_segment_recorder_v1",
                "rotated": True,
                "reason": reason,
                "previous_segment_id": self.segment_id,
                "segment_id": self.segment_id,
                "storage_format": self.storage_format,
                "segment_path_abs": str(self.segment_path.resolve()),
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }

    def _open_segment(self, *, bucket: str, width: int, height: int) -> None:
        if self.writer is not None:
            self.writer.close()
        self._segment_sequence += 1
        self._active_bucket = bucket
        self.segment_dir = _stable_segment_dir(self.evidence.root)
        self.segment_id, self.segment_path = self._next_segment_identity(bucket=bucket)
        if self.storage_format == "mkv_vfr":
            self.writer = MkvSegmentWriter(self.segment_path, segment_id=self.segment_id, width=width, height=height)
            self._segment_started_at_ms = self.writer.started_at_ms
        elif self.storage_format == "binary_agseg":
            self.writer = BinarySegmentWriter.open_or_create(
                self.segment_path,
                segment_id=self.segment_id,
                image_encoding=self.image_encoding,
                image_quality=self.image_quality,
                image_lossless=self.image_lossless,
                started_at_iso=timestamp_iso(int(time.time() * 1000)),
            )
            frames = list(self.writer.manifest.get("frames") or [])
            self._segment_started_at_ms = _timestamp_ms(frames[0]["timestamp_iso"]) if frames else int(time.time() * 1000)
        else:
            self.writer = SegmentWriter.create(
                self.segment_path,
                segment_id=self.segment_id,
                started_at_iso=timestamp_iso(int(time.time() * 1000)),
            )
            self._segment_started_at_ms = int(time.time() * 1000)

    def _next_segment_identity(self, *, bucket: str) -> tuple[str, Path]:
        """Use stable bucket names; MKV bumps suffix only to avoid overwriting."""
        if self.storage_format == "binary_agseg":
            segment_id = f"agentsight-{bucket}"
            return segment_id, self.segment_dir / f"{segment_id}.agseg"
        sequence = max(1, self._segment_sequence)
        while True:
            segment_id = f"agentsight-{bucket}-{sequence:03d}"
            segment_path = self.segment_dir / f"{segment_id}{_segment_suffix(self.storage_format)}"
            paths = [segment_path, _index_path(segment_path, self.storage_format), _manifest_path(segment_path, self.storage_format)]
            if not any(path.exists() for path in paths):
                self._segment_sequence = sequence
                return segment_id, segment_path
            sequence += 1

    def _add_frame_to_writer(
        self,
        *,
        frame: dict[str, Any],
        image: Image.Image,
        captured_at_ms: int,
        source: str | None,
        event_id: str | None,
    ) -> dict[str, Any]:
        assert self.writer is not None
        common = {
            "timestamp_iso": timestamp_iso(captured_at_ms),
            "source": self._segment_source(source),
            "event_id": event_id,
            "cursor_mode": str(frame.get("cursor_mode") or "none"),
            "capture_content_degenerate": bool(frame.get("capture_content_degenerate")),
            "screen_region": _normalize_screen_region(frame.get("screen_region") or frame.get("region")),
            "coordinate_system": str(frame.get("coordinate_system") or "") or None,
        }
        if self.storage_format == "mkv_vfr":
            assert isinstance(self.writer, MkvSegmentWriter)
            return self.writer.add_frame(
                image.convert("RGBA").tobytes("raw", "BGRA"),
                captured_at_ms=captured_at_ms,
                **common,
            )
        assert isinstance(self.writer, (BinarySegmentWriter, SegmentWriter))
        return self.writer.add_frame(
            image,
            timestamp_monotonic_ms=int(frame.get("captured_at_monotonic_ms") or captured_at_ms),
            **common,
        )

    def _restore_ref(self, record: dict[str, Any]) -> dict[str, Any]:
        restore = {
            "storage_format": self.storage_format,
            "segment_path": str(self.segment_path.resolve()),
            "frame_id": record["frame_id"],
        }
        if self.storage_format == "mkv_vfr":
            restore.update(
                {
                    "index_path": self._index_path_abs(),
                    "pts_ms": record.get("pts_ms"),
                    "playback_pts_ms": record.get("playback_pts_ms"),
                    "playback_time_basis": record.get("playback_time_basis"),
                    "logical_frame_id": record.get("logical_frame_id", record["frame_id"]),
                    "logical_frame_index": record.get("logical_frame_index", record.get("frame_index")),
                    "physical_frame_id": record.get("physical_frame_id", record["frame_id"]),
                    "physical_frame_index": record.get("physical_frame_index", record.get("frame_index")),
                    "duplicate_of_frame_id": record.get("duplicate_of_frame_id"),
                    "logical_duplicate": bool(record.get("logical_duplicate")),
                    "timestamp_ms": record.get("timestamp_ms"),
                }
            )
        return restore

    def _manifest_path_abs(self) -> str:
        return str(_manifest_path(self.segment_path, self.storage_format).resolve())

    def _index_path_abs(self) -> str:
        return str(_index_path(self.segment_path, self.storage_format).resolve())

    def _frame_count(self) -> int:
        if self.writer is None:
            return 0
        if isinstance(self.writer, MkvSegmentWriter):
            return len(self.writer.frames)
        return int(self.writer.manifest.get("frame_count") or len(self.writer.manifest.get("frames") or []))

    def _current_segment_size_bytes(self) -> int:
        try:
            return int(self.segment_path.stat().st_size) if self.segment_path.exists() else 0
        except OSError:
            return 0

    def _segment_size_limit_reached(self) -> bool:
        return self.max_segment_size_bytes > 0 and self.writer is not None and self._current_segment_size_bytes() >= self.max_segment_size_bytes

    def _empty_manifest(self) -> dict[str, Any]:
        return {"storage_format": self.storage_format, "frame_count": 0, "frames": [], "boundary": boundary_facts()}

    def _image_from_frame(self, frame: dict[str, Any]) -> Image.Image | None:
        direct = frame.get("_bgra_bytes")
        if isinstance(direct, (bytes, bytearray, memoryview)):
            width, height = _frame_dimensions(frame)
            if width > 0 and height > 0:
                return Image.frombytes("RGBA", (width, height), bytes(direct), "raw", "BGRA")
        media_bytes = frame.get("_media_bytes")
        if isinstance(media_bytes, (bytes, bytearray, memoryview)):
            with Image.open(BytesIO(bytes(media_bytes))) as image:
                return image.convert("RGBA")
        for field in ("raw_media_path_abs", "media_path_abs"):
            media_path = frame.get(field)
            if isinstance(media_path, (str, Path)) and str(media_path):
                path = Path(str(media_path))
                if path.exists() and path.is_file():
                    with Image.open(path) as image:
                        return image.convert("RGBA")
        media_ref = frame.get("media_ref")
        if isinstance(media_ref, str) and media_ref:
            path = self.evidence.root / media_ref
            if path.exists() and path.is_file():
                with Image.open(path) as image:
                    return image.convert("RGBA")
        return None

    def _segment_source(self, source: str | None) -> str:
        return {
            "do_after_frame": "post_do",
            "observe": "manual_import",
            "review_clip": "manual_import",
            None: "manual_import",
        }.get(source, source)  # type: ignore[return-value]

    def _apply_storage_quota_best_effort_throttled(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_quota_check_at < 10.0:
            return
        self._last_quota_check_at = now
        try:
            from agentsight.storage_quota import apply_storage_quota

            apply_storage_quota(root=self.evidence.root.parent)
        except Exception:
            pass

    def _not_recorded(
        self,
        reason: str,
        *,
        frame: dict[str, Any],
        source: str | None,
        event_id: str | None,
        view_id: str | None,
    ) -> dict[str, Any]:
        return {
            "object_type": "AgentSightSegmentFrameRef",
            "schema": "agentsight_segment_v1",
            "status": "not_recorded",
            "not_recorded_reason": reason,
            "segment_id": self.segment_id,
            "storage_format": self.storage_format,
            "observation_ref": frame.get("observation_id"),
            "source": self._segment_source(source),
            "event_id": event_id,
            "view_id": view_id,
            "raw_or_derived": "raw",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }


def _stable_segment_dir(evidence_root: Path) -> Path:
    parent = evidence_root.parent
    if parent.name.startswith("visual-") and parent.parent != parent:
        return parent.parent / "segments"
    return parent / "segments"


def _normalize_storage_format(value: str | None) -> str:
    normalized = str(value or "mkv_vfr").strip().lower()
    if normalized in {"binary_agseg", "agseg"}:
        return "binary_agseg"
    # Legacy directory requests are intentionally normalized to current canonical
    # MKV storage; proto_directory remains available through SegmentWriter/Reader
    # compatibility tests, not the runtime recorder.
    return "mkv_vfr"


def _normalize_max_segment_size_mb(value: int | float | str | None) -> float:
    try:
        if value is None or isinstance(value, bool):
            parsed = 1024.0
        else:
            parsed = float(value)
    except (TypeError, ValueError):
        parsed = 1024.0
    if parsed <= 0:
        return 0.0
    return min(1024.0 * 1024.0, parsed)


def _segment_suffix(storage_format: str) -> str:
    if storage_format == "binary_agseg":
        return ".agseg"
    if storage_format == "proto_directory":
        return ""
    return ".mkv"


def _manifest_path(segment_path: Path, storage_format: str) -> Path:
    if storage_format == "mkv_vfr":
        return segment_path.with_suffix(".manifest.json")
    if storage_format == "proto_directory":
        return segment_path / "manifest.json"
    return segment_path


def _index_path(segment_path: Path, storage_format: str) -> Path:
    if storage_format == "mkv_vfr":
        return segment_path.with_suffix(".frames.jsonl")
    if storage_format == "proto_directory":
        return segment_path / "index.json"
    return segment_path


def _normalize_bucket_granularity(value: str) -> str:
    normalized = str(value or "hourly").strip().lower()
    if normalized in {"day", "daily"}:
        return "daily"
    return "hourly"


def _normalize_screen_region(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    try:
        return {
            "x": int(value.get("x", 0)),
            "y": int(value.get("y", 0)),
            "w": int(value.get("w", value.get("width", 0))),
            "h": int(value.get("h", value.get("height", 0))),
        }
    except (TypeError, ValueError):
        return None


def _frame_dimensions(frame: dict[str, Any]) -> tuple[int, int]:
    try:
        width = int(frame.get("width") or 0)
        height = int(frame.get("height") or 0)
    except (TypeError, ValueError):
        width, height = 0, 0
    if width > 0 and height > 0:
        return width, height
    region = _normalize_screen_region(frame.get("screen_region") or frame.get("region"))
    if region:
        return int(region["w"]), int(region["h"])
    return 0, 0


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        # Small numbers are epoch seconds; larger values are already ms.
        parsed = float(value)
        return int(parsed if parsed > 10_000_000_000 else parsed * 1000)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return _timestamp_ms(float(text))
        except ValueError:
            pass
        try:
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.astimezone()
            return int(dt.timestamp() * 1000)
        except ValueError:
            return int(time.time() * 1000)
    return int(time.time() * 1000)


def _bucket_from_time(value: Any, boundary_hhmm: str, granularity: str = "hourly") -> str:
    dt = _datetime_from_value(value)
    if _normalize_bucket_granularity(granularity) == "hourly":
        return dt.strftime("%Y%m%d-%H")
    hour, minute = _parse_hhmm(boundary_hhmm)
    boundary = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if dt < boundary:
        boundary = boundary - timedelta(days=1)
    return boundary.strftime("%Y%m%d")


def _datetime_from_value(value: Any) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value)).astimezone()
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromtimestamp(_timestamp_ms(value) / 1000.0).astimezone()
        except Exception:
            pass
    return datetime.now().astimezone()


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return max(0, min(23, int(hour_text))), max(0, min(59, int(minute_text)))
    except Exception:
        return 0, 0
