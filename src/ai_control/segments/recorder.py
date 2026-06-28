from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image

from ai_control.evidence.store import EvidenceReplayService
from ai_control.segments.manifest import boundary_facts
from ai_control.segments.mkv_container import MkvSegmentWriter, timestamp_iso


class SegmentFrameRecorder:
    """Canonical MKV VFR recorder over already captured raw frames."""

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
    ) -> None:
        self.evidence = evidence
        self.enabled = bool(enabled)
        self.daily_segment_boundary_local_time = daily_segment_boundary_local_time
        self.storage_format = "mkv_vfr"
        self.segment_bucket_granularity = _normalize_bucket_granularity(segment_bucket_granularity)
        self.image_encoding = "ffv1"
        self.image_quality = image_quality
        self.image_lossless = True
        self.segment_id = ""
        self.segment_dir = _stable_segment_dir(evidence.root)
        self.segment_path = self.segment_dir / "segment-uninitialized.mkv"
        self.writer: MkvSegmentWriter | None = None
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
        bgra = self._bgra_bytes(frame)
        if bgra is None:
            return self._not_recorded("frame_payload_missing", frame=frame, source=source, event_id=event_id, view_id=view_id)
        captured_at_ms = _timestamp_ms(frame.get("captured_at") or frame.get("timestamp"))
        bucket = _bucket_from_time(captured_at_ms / 1000.0, self.daily_segment_boundary_local_time, self.segment_bucket_granularity)
        width = int(frame.get("width") or 0)
        height = int(frame.get("height") or 0)
        if width <= 0 or height <= 0:
            return self._not_recorded("frame_geometry_missing", frame=frame, source=source, event_id=event_id, view_id=view_id)
        segment_too_old = self._segment_started_at_ms and captured_at_ms - self._segment_started_at_ms >= self._max_segment_duration_ms
        if self.writer is None or bucket != self._active_bucket or width != self.writer.width or height != self.writer.height or segment_too_old:
            self._open_segment(bucket=bucket, width=width, height=height)
        try:
            assert self.writer is not None
            record = self.writer.add_frame(
                bgra,
                captured_at_ms=captured_at_ms,
                timestamp_iso=timestamp_iso(captured_at_ms),
                source=self._segment_source(source),
                event_id=event_id,
                cursor_mode=str(frame.get("cursor_mode") or "none"),
                capture_content_degenerate=bool(frame.get("capture_content_degenerate")),
                screen_region=_normalize_screen_region(frame.get("screen_region") or frame.get("region")),
                coordinate_system=str(frame.get("coordinate_system") or "") or None,
            )
            self._apply_storage_quota_best_effort_throttled()
        except Exception as exc:
            return self._not_recorded(f"segment_record_failed:{type(exc).__name__}", frame=frame, source=source, event_id=event_id, view_id=view_id)
        return {
            "object_type": "AgentSightSegmentFrameRef",
            "schema": "agentsight_segment_v1",
            "status": "recorded",
            "segment_id": self.segment_id,
            "storage_format": "mkv_vfr",
            "segment_path_abs": str(self.segment_path.resolve()),
            "manifest_path_abs": str(self.segment_path.with_suffix(".manifest.json").resolve()),
            "index_path_abs": str(self.segment_path.with_suffix(".frames.jsonl").resolve()),
            "manifest_embedded": False,
            "frame_id": record["frame_id"],
            "frame_kind": record["frame_kind"],
            "frame_index": record["frame_index"],
            "pts_ms": record["pts_ms"],
            "playback_pts_ms": record.get("playback_pts_ms"),
            "playback_time_basis": record.get("playback_time_basis"),
            "source": record["source"],
            "event_id": record.get("event_id"),
            "view_id": view_id,
            "restore_ref": {
                "storage_format": "mkv_vfr",
                "segment_path": str(self.segment_path.resolve()),
                "index_path": str(self.segment_path.with_suffix(".frames.jsonl").resolve()),
                "frame_id": record["frame_id"],
                "pts_ms": record["pts_ms"],
                "playback_pts_ms": record.get("playback_pts_ms"),
                "playback_time_basis": record.get("playback_time_basis"),
            },
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
                "storage_format": "mkv_vfr",
                "segment_bucket_granularity": self.segment_bucket_granularity,
                "image_encoding": "ffv1",
                "image_quality": self.image_quality,
                "image_lossless": True,
                "segment_id": self.segment_id,
                "segment_path_abs": str(self.segment_path.resolve()),
                "manifest_path_abs": str(self.segment_path.with_suffix(".manifest.json").resolve()),
                "index_path_abs": str(self.segment_path.with_suffix(".frames.jsonl").resolve()),
                "manifest_embedded": False,
                "frame_count": len(self.writer.frames) if self.writer else 0,
                "raw_frames_are_canonical_evidence": True,
                "derived_review_video_is_canonical": False,
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }

    def manifest(self) -> dict[str, Any]:
        with self._lock:
            return self.writer.manifest if self.writer else {}

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
                "storage_format": "mkv_vfr",
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
        self.writer = MkvSegmentWriter(self.segment_path, segment_id=self.segment_id, width=width, height=height)
        self._segment_started_at_ms = self.writer.started_at_ms

    def _next_segment_identity(self, *, bucket: str) -> tuple[str, Path]:
        """Use stable hourly bucket names; bump suffix only to avoid overwriting."""
        sequence = max(1, self._segment_sequence)
        while True:
            segment_id = f"agentsight-{bucket}-{sequence:03d}"
            segment_path = self.segment_dir / f"{segment_id}.mkv"
            index_path = segment_path.with_suffix(".frames.jsonl")
            manifest_path = segment_path.with_suffix(".manifest.json")
            if not segment_path.exists() and not index_path.exists() and not manifest_path.exists():
                self._segment_sequence = sequence
                return segment_id, segment_path
            sequence += 1

    def _bgra_bytes(self, frame: dict[str, Any]) -> bytes | None:
        direct = frame.get("_bgra_bytes")
        if isinstance(direct, (bytes, bytearray, memoryview)):
            return bytes(direct)
        media_bytes = frame.get("_media_bytes")
        if isinstance(media_bytes, (bytes, bytearray, memoryview)):
            with Image.open(BytesIO(bytes(media_bytes))) as image:
                return image.convert("RGBA").tobytes("raw", "BGRA")
        return None

    def _segment_source(self, source: str | None) -> str:
        return {
            "do_after_frame": "post_do",
            "observe": "manual_import",
            "review_clip": "manual_import",
            None: "manual_import",
        }.get(source, source)  # type: ignore[return-value]

    def _apply_storage_quota_best_effort_throttled(self) -> None:
        now = time.monotonic()
        if now - self._last_quota_check_at < 10.0:
            return
        self._last_quota_check_at = now
        try:
            from ai_control.storage_quota import apply_storage_quota

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
            "storage_format": "mkv_vfr",
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


def _timestamp_ms(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(float(value) * 1000)
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
    return datetime.now().astimezone()


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = str(value).split(":", 1)
        return max(0, min(23, int(hour_text))), max(0, min(59, int(minute_text)))
    except Exception:
        return 0, 0
