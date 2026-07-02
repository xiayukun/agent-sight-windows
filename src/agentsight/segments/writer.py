from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentsight.segments.manifest import (
    build_empty_segment_index,
    build_empty_segment_manifest,
    build_frame_record,
    sha256_image_rgba,
    validate_segment_manifest,
)


class SegmentWriter:
    def __init__(
        self,
        segment_dir: Path,
        *,
        segment_id: str,
        keyframe_interval: int = 120,
        keyframe_threshold_ratio: float = 0.35,
        started_at_iso: str | None = None,
    ) -> None:
        self.segment_dir = Path(segment_dir)
        self.segment_id = segment_id
        self.keyframe_interval = max(1, int(keyframe_interval))
        self.keyframe_threshold_ratio = max(0.0, min(1.0, float(keyframe_threshold_ratio)))
        self.manifest = build_empty_segment_manifest(segment_id=segment_id, started_at_iso=started_at_iso)
        self.index = build_empty_segment_index(segment_id=segment_id)
        self._previous_image: Any | None = None
        self._previous_frame_id: str | None = None
        self._nearest_keyframe_id: str | None = None
        self._last_keyframe_index = -1

    @classmethod
    def create(
        cls,
        segment_dir: str | Path,
        *,
        segment_id: str,
        keyframe_interval: int = 120,
        keyframe_threshold_ratio: float = 0.35,
        started_at_iso: str | None = None,
    ) -> "SegmentWriter":
        writer = cls(
            Path(segment_dir),
            segment_id=segment_id,
            keyframe_interval=keyframe_interval,
            keyframe_threshold_ratio=keyframe_threshold_ratio,
            started_at_iso=started_at_iso,
        )
        writer.segment_dir.mkdir(parents=True, exist_ok=True)
        for child in ("keyframes", "deltas", "thumbnails", "derived"):
            (writer.segment_dir / child).mkdir(parents=True, exist_ok=True)
        return writer

    def add_frame(
        self,
        image: Any,
        *,
        timestamp_iso: str,
        timestamp_monotonic_ms: int,
        source: str,
        event_id: str | None = None,
        cursor_mode: str = "none",
        capture_content_degenerate: bool = False,
        screen_region: dict[str, int] | None = None,
        coordinate_system: str | None = None,
    ) -> dict[str, Any]:
        from PIL import ImageChops

        rgba = image.convert("RGBA")
        width, height = rgba.size
        frame_index = len(self.manifest["frames"])
        frame_id = f"f{frame_index:06d}"
        full_frame_sha256 = sha256_image_rgba(rgba)

        write_keyframe = self._should_write_keyframe(rgba, frame_index)
        delta_bbox = None
        keyframe_blob_ref = None
        delta_blob_ref = None
        frame_kind = "keyframe"

        if not write_keyframe and self._previous_image is not None:
            bbox = ImageChops.difference(self._previous_image.convert("RGB"), rgba.convert("RGB")).getbbox()
            if bbox is None:
                frame_kind = "pframe_no_change"
            else:
                x0, y0, x1, y1 = bbox
                changed_ratio = ((x1 - x0) * (y1 - y0)) / float(width * height)
                if changed_ratio > self.keyframe_threshold_ratio:
                    write_keyframe = True
                else:
                    frame_kind = "pframe_delta"
                    delta_bbox = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
                    delta_blob_ref = f"deltas/d{frame_index:06d}.png"
                    rgba.crop(bbox).save(self.segment_dir / delta_blob_ref)

        if write_keyframe:
            frame_kind = "keyframe"
            keyframe_blob_ref = f"keyframes/k{frame_index:06d}.png"
            rgba.save(self.segment_dir / keyframe_blob_ref)
            self._nearest_keyframe_id = frame_id
            self._last_keyframe_index = frame_index

        record = build_frame_record(
            frame_id=frame_id,
            timestamp_iso=timestamp_iso,
            timestamp_monotonic_ms=timestamp_monotonic_ms,
            frame_index=frame_index,
            frame_kind=frame_kind,
            source=source,
            width=width,
            height=height,
            full_frame_sha256=full_frame_sha256,
            event_id=event_id,
            nearest_keyframe_id=frame_id if frame_kind == "keyframe" else self._nearest_keyframe_id,
            previous_frame_id=None if frame_kind == "keyframe" else self._previous_frame_id,
            delta_bbox=delta_bbox,
            keyframe_blob_ref=keyframe_blob_ref,
            delta_blob_ref=delta_blob_ref,
            cursor_mode=cursor_mode,
            capture_content_degenerate=capture_content_degenerate,
            screen_region=screen_region,
            coordinate_system=coordinate_system,
        )
        self.manifest["frames"].append(record)
        self._refresh_counts()
        self._append_index(record)
        self._previous_image = rgba.copy()
        self._previous_frame_id = frame_id
        return record

    def close(self, *, ended_at_iso: str | None = None) -> dict[str, Any]:
        self.manifest["ended_at_iso"] = ended_at_iso or self.manifest["frames"][-1]["timestamp_iso"] if self.manifest["frames"] else ended_at_iso
        return self.flush(validate=True)

    def flush(self, *, validate: bool = True) -> dict[str, Any]:
        report = validate_segment_manifest(self.manifest)
        if validate and not report["valid"]:
            raise ValueError(f"invalid segment manifest: {report['errors']}")
        self._write_json("manifest.json", self.manifest)
        self._write_json("index.json", self.index)
        return self.manifest

    def _should_write_keyframe(self, rgba: Any, frame_index: int) -> bool:
        if frame_index == 0 or self._previous_image is None:
            return True
        if self._previous_image.size != rgba.size:
            return True
        return frame_index - self._last_keyframe_index >= self.keyframe_interval

    def _refresh_counts(self) -> None:
        frames = self.manifest["frames"]
        self.manifest["frame_count"] = len(frames)
        self.manifest["keyframe_count"] = sum(1 for frame in frames if frame["frame_kind"] == "keyframe")
        self.manifest["pframe_delta_count"] = sum(1 for frame in frames if frame["frame_kind"] == "pframe_delta")
        self.manifest["pframe_no_change_count"] = sum(1 for frame in frames if frame["frame_kind"] == "pframe_no_change")
        self.index["frame_count"] = len(frames)

    def _append_index(self, record: dict[str, Any]) -> None:
        self.index["time_index"].append(
            {
                "timestamp_iso": record["timestamp_iso"],
                "timestamp_monotonic_ms": record["timestamp_monotonic_ms"],
                "frame_id": record["frame_id"],
            }
        )
        if record["frame_kind"] == "keyframe":
            self.index["keyframes"].append(
                {
                    "frame_id": record["frame_id"],
                    "timestamp_monotonic_ms": record["timestamp_monotonic_ms"],
                }
            )
        event_id = record.get("event_id")
        if event_id:
            for event in self.index["events"]:
                if event.get("event_id") == event_id:
                    event.setdefault("frame_ids", []).append(record["frame_id"])
                    break
            else:
                self.index["events"].append({"event_id": event_id, "frame_ids": [record["frame_id"]]})

    def _write_json(self, name: str, data: dict[str, Any]) -> None:
        path = self.segment_dir / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
