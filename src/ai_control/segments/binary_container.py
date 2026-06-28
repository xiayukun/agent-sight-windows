from __future__ import annotations

import hashlib
import io
import json
import struct
from pathlib import Path
from typing import Any

from ai_control.segments.manifest import (
    CODEC_MODEL,
    FRAME_RATE_MODEL,
    SEGMENT_SCHEMA,
    boundary_facts,
    build_empty_segment_index,
    build_empty_segment_manifest,
    build_frame_record,
    sha256_image_rgba,
    validate_segment_manifest,
)


AGSEG_MAGIC = b"AGSEGv1\0"
AGSEG_FOOTER_MAGIC = b"AGSEGFTR"
BINARY_CONTAINER_MODEL = "single_file_agseg_v1"
BINARY_HEADER_SCHEMA = "agentsight_binary_segment_header_v1"


class BinarySegmentWriter:
    def __init__(
        self,
        segment_path: Path,
        *,
        segment_id: str,
        keyframe_interval: int = 120,
        keyframe_threshold_ratio: float = 0.35,
        image_encoding: str = "webp",
        image_quality: int = 70,
        image_lossless: bool = True,
        started_at_iso: str | None = None,
    ) -> None:
        self.segment_path = Path(segment_path)
        self.segment_id = segment_id
        self.keyframe_interval = max(1, int(keyframe_interval))
        self.keyframe_threshold_ratio = max(0.0, min(1.0, float(keyframe_threshold_ratio)))
        self.image_encoding = _normalize_image_encoding(image_encoding)
        self.image_quality = max(1, min(100, int(image_quality)))
        self.image_lossless = bool(image_lossless)
        self.manifest = build_empty_segment_manifest(segment_id=segment_id, started_at_iso=started_at_iso)
        self.manifest["container_model"] = BINARY_CONTAINER_MODEL
        self.manifest["binary_container"] = {
            "schema": "agentsight_binary_segment_container_v1",
            "path_suffix": ".agseg",
            "blob_encoding": self.image_encoding,
            "blob_compression": {
                "encoding": self.image_encoding,
                "quality": self.image_quality,
                "lossless": self.image_lossless,
            },
            "footer_magic": AGSEG_FOOTER_MAGIC.decode("ascii"),
            "blobs": [],
        }
        self.index = build_empty_segment_index(segment_id=segment_id)
        self._previous_image: Any | None = None
        self._previous_compare_image: Any | None = None
        self._previous_frame_id: str | None = None
        self._nearest_keyframe_id: str | None = None
        self._last_keyframe_index = -1
        self._closed = False
        self._file = None
        self._footer_start: int | None = None

    @classmethod
    def create(
        cls,
        segment_path: str | Path,
        *,
        segment_id: str,
        keyframe_interval: int = 120,
        keyframe_threshold_ratio: float = 0.35,
        image_encoding: str = "webp",
        image_quality: int = 70,
        image_lossless: bool = True,
        started_at_iso: str | None = None,
    ) -> "BinarySegmentWriter":
        writer = cls(
            Path(segment_path),
            segment_id=segment_id,
            keyframe_interval=keyframe_interval,
            keyframe_threshold_ratio=keyframe_threshold_ratio,
            image_encoding=image_encoding,
            image_quality=image_quality,
            image_lossless=image_lossless,
            started_at_iso=started_at_iso,
        )
        writer.segment_path.parent.mkdir(parents=True, exist_ok=True)
        writer._file = writer.segment_path.open("w+b")
        writer._write_header(started_at_iso=started_at_iso)
        writer.flush(validate=True, close_file=True)
        return writer

    @classmethod
    def open_or_create(
        cls,
        segment_path: str | Path,
        *,
        segment_id: str,
        keyframe_interval: int = 120,
        keyframe_threshold_ratio: float = 0.35,
        image_encoding: str = "webp",
        image_quality: int = 70,
        image_lossless: bool = True,
        started_at_iso: str | None = None,
    ) -> "BinarySegmentWriter":
        path = Path(segment_path)
        if not path.exists() or path.stat().st_size == 0:
            return cls.create(
                path,
                segment_id=segment_id,
                keyframe_interval=keyframe_interval,
                keyframe_threshold_ratio=keyframe_threshold_ratio,
                image_encoding=image_encoding,
                image_quality=image_quality,
                image_lossless=image_lossless,
                started_at_iso=started_at_iso,
            )

        reader = BinarySegmentReader(path)
        manifest = reader.manifest
        writer = cls(
            path,
            segment_id=str(manifest.get("segment_id") or segment_id),
            keyframe_interval=keyframe_interval,
            keyframe_threshold_ratio=keyframe_threshold_ratio,
            image_encoding=image_encoding,
            image_quality=image_quality,
            image_lossless=image_lossless,
            started_at_iso=str(manifest.get("started_at_iso") or started_at_iso or ""),
        )
        writer.manifest = manifest
        writer.index = manifest.get("index") or build_empty_segment_index(segment_id=writer.segment_id)
        writer._file = path.open("r+b")
        writer._footer_start = _footer_start(path)

        frames = list(manifest.get("frames") or [])
        if frames:
            last = frames[-1]
            writer._previous_frame_id = str(last["frame_id"])
            writer._nearest_keyframe_id = str(last.get("nearest_keyframe_id") or last["frame_id"])
            keyframes = [int(frame["frame_index"]) for frame in frames if frame.get("frame_kind") == "keyframe"]
            writer._last_keyframe_index = max(keyframes) if keyframes else -1
            previous_image, previous_report = reader.restore_frame(writer._previous_frame_id)
            if not previous_report.get("hash_ok"):
                raise ValueError("cannot append to .agseg with invalid last-frame hash")
            writer._previous_image = previous_image.copy()
            writer._previous_compare_image = previous_image.copy()
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
        if self._closed:
            raise ValueError("binary segment writer is closed")

        from PIL import ImageChops

        rgba = image.convert("RGBA")
        width, height = rgba.size
        frame_index = len(self.manifest["frames"])
        frame_id = f"f{frame_index:06d}"
        write_keyframe = self._should_write_keyframe(rgba, frame_index)
        delta_bbox = None
        keyframe_blob_ref = None
        delta_blob_ref = None
        frame_kind = "keyframe"
        canonical_rgba = rgba

        compare_previous = self._previous_compare_image or self._previous_image
        if not write_keyframe and self._previous_image is not None and compare_previous is not None:
            bbox = ImageChops.difference(compare_previous.convert("RGB"), rgba.convert("RGB")).getbbox()
            if bbox is None:
                frame_kind = "pframe_no_change"
                canonical_rgba = self._previous_image.copy()
            else:
                x0, y0, x1, y1 = bbox
                changed_ratio = ((x1 - x0) * (y1 - y0)) / float(width * height)
                if changed_ratio > self.keyframe_threshold_ratio:
                    write_keyframe = True
                else:
                    frame_kind = "pframe_delta"
                    delta_bbox = {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}
                    delta_blob_ref = self._write_image_blob(
                        rgba.crop(bbox),
                        role="delta_crop",
                        frame_id=frame_id,
                    )
                    canonical_rgba = self._previous_image.copy()
                    delta_image = self._canonical_blob_image(delta_blob_ref, fallback=rgba.crop(bbox))
                    canonical_rgba.paste(delta_image, (x0, y0))

        if write_keyframe:
            frame_kind = "keyframe"
            keyframe_blob_ref = self._write_image_blob(rgba, role="keyframe", frame_id=frame_id)
            canonical_rgba = self._canonical_blob_image(keyframe_blob_ref, fallback=rgba)
            self._nearest_keyframe_id = frame_id
            self._last_keyframe_index = frame_index

        full_frame_sha256 = sha256_image_rgba(canonical_rgba)

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
        self._previous_image = canonical_rgba.copy()
        self._previous_compare_image = rgba.copy()
        self._previous_frame_id = frame_id
        return record

    def close(self, *, ended_at_iso: str | None = None) -> dict[str, Any]:
        if self._closed:
            return self.manifest
        self.manifest["ended_at_iso"] = ended_at_iso or (
            self.manifest["frames"][-1]["timestamp_iso"] if self.manifest["frames"] else None
        )
        manifest = self.flush(validate=True)
        if self._file is not None:
            self._file.close()
            self._file = None
        self._closed = True
        return manifest

    def flush(self, *, validate: bool = True, close_file: bool = False) -> dict[str, Any]:
        if self._closed:
            return self.manifest
        self._ensure_file()
        self.manifest["index"] = self.index
        report = validate_segment_manifest(self.manifest) if validate else {"valid": True, "errors": []}
        if validate and not report["valid"]:
            raise ValueError(f"invalid binary segment manifest: {report['errors']}")
        manifest_bytes = json.dumps(self.manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if self._footer_start is not None:
            self._file.seek(self._footer_start)
            self._file.truncate()
        self._footer_start = self._file.tell()
        self._file.write(manifest_bytes)
        self._file.write(struct.pack("<Q", len(manifest_bytes)))
        self._file.write(AGSEG_FOOTER_MAGIC)
        self._file.flush()
        if close_file:
            self._file.close()
            self._file = None
        return self.manifest

    def _write_header(self, *, started_at_iso: str | None) -> None:
        self._ensure_file()
        header = {
            "schema": BINARY_HEADER_SCHEMA,
            "segment_schema": SEGMENT_SCHEMA,
            "segment_id": self.segment_id,
            "container_model": BINARY_CONTAINER_MODEL,
            "codec_model": CODEC_MODEL,
            "frame_rate_model": FRAME_RATE_MODEL,
            "created_at_iso": started_at_iso or self.manifest.get("started_at_iso"),
        }
        payload = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._file.write(AGSEG_MAGIC)
        self._file.write(struct.pack("<I", len(payload)))
        self._file.write(payload)

    def _write_image_blob(self, image: Any, *, role: str, frame_id: str) -> str:
        self._ensure_file()
        if self._footer_start is not None:
            self._file.seek(self._footer_start)
            self._file.truncate()
            self._footer_start = None
        buffer = io.BytesIO()
        if self.image_encoding == "png":
            image.save(buffer, format="PNG", optimize=True)
        else:
            image.save(buffer, format="WEBP", quality=self.image_quality, lossless=self.image_lossless, method=4)
        payload = buffer.getvalue()
        offset = self._file.tell()
        self._file.write(payload)
        blob_id = f"b{len(self.manifest['binary_container']['blobs']):06d}"
        self.manifest["binary_container"]["blobs"].append(
            {
                "blob_id": blob_id,
                "role": role,
                "frame_id": frame_id,
                "offset": int(offset),
                "length": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "encoding": self.image_encoding,
                "quality": self.image_quality,
                "lossless": self.image_lossless,
            }
        )
        return f"agseg:{blob_id}"

    def _canonical_blob_image(self, blob_ref: str, *, fallback: Any) -> Any:
        if self.image_encoding != "webp" or self.image_lossless:
            return fallback.convert("RGBA")
        try:
            from PIL import Image

            payload = self._read_written_blob_bytes(blob_ref)
            return Image.open(io.BytesIO(payload)).convert("RGBA")
        except Exception:
            return fallback.convert("RGBA")

    def _read_written_blob_bytes(self, blob_ref: str) -> bytes:
        self._ensure_file()
        blob_id = _blob_id_from_ref(blob_ref)
        blob = next((item for item in self.manifest["binary_container"]["blobs"] if item.get("blob_id") == blob_id), None)
        if not isinstance(blob, dict):
            raise KeyError(f"blob not found: {blob_ref}")
        self._file.flush()
        self._file.seek(int(blob["offset"]))
        return self._file.read(int(blob["length"]))

    def _ensure_file(self) -> None:
        if self._closed:
            raise ValueError("binary segment writer is closed")
        if self._file is not None:
            return
        mode = "r+b" if self.segment_path.exists() else "w+b"
        self._file = self.segment_path.open(mode)

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


class BinarySegmentReader:
    def __init__(self, segment_path: str | Path) -> None:
        self.segment_path = Path(segment_path)
        self.header, self.manifest = self._read_container_metadata()
        self._frames: list[dict[str, Any]] = list(self.manifest.get("frames") or [])
        self._by_id = {frame["frame_id"]: frame for frame in self._frames}
        self._blobs = {
            blob["blob_id"]: blob for blob in self.manifest.get("binary_container", {}).get("blobs", [])
        }

    def restore_frame(self, frame_id: str):
        from PIL import Image

        if frame_id not in self._by_id:
            raise KeyError(f"frame not found: {frame_id}")
        target = self._by_id[frame_id]
        keyframe_id = target["frame_id"] if target["frame_kind"] == "keyframe" else target.get("nearest_keyframe_id")
        if not keyframe_id or keyframe_id not in self._by_id:
            raise ValueError(f"nearest keyframe not found for {frame_id}")

        keyframe = self._by_id[keyframe_id]
        image = Image.open(io.BytesIO(self._read_blob_bytes(str(keyframe["keyframe_blob_ref"])))).convert("RGBA")
        restored_ids = [keyframe_id]
        if keyframe_id != frame_id:
            start = int(keyframe["frame_index"]) + 1
            stop = int(target["frame_index"]) + 1
            for record in self._frames[start:stop]:
                kind = record["frame_kind"]
                if kind == "keyframe":
                    image = Image.open(io.BytesIO(self._read_blob_bytes(str(record["keyframe_blob_ref"])))).convert("RGBA")
                elif kind == "pframe_delta":
                    bbox = record["delta_bbox"]
                    crop = Image.open(io.BytesIO(self._read_blob_bytes(str(record["delta_blob_ref"])))).convert("RGBA")
                    image.paste(crop, (int(bbox["x"]), int(bbox["y"])))
                elif kind == "pframe_no_change":
                    image = image.copy()
                else:
                    raise ValueError(f"unsupported frame kind: {kind}")
                restored_ids.append(record["frame_id"])

        actual_hash = sha256_image_rgba(image)
        report = {
            "object_type": "AgentSightBinarySegmentRestoreReport",
            "schema": self.manifest.get("schema"),
            "container_model": self.manifest.get("container_model"),
            "canonical_evidence_source": BINARY_CONTAINER_MODEL,
            "segment_id": self.manifest.get("segment_id"),
            "segment_path_abs": str(self.segment_path.resolve()),
            "frame_id": frame_id,
            "frame_kind": target.get("frame_kind"),
            "nearest_keyframe_id": keyframe_id,
            "restored_frame_ids": restored_ids,
            "expected_full_frame_sha256": target.get("full_frame_sha256"),
            "actual_full_frame_sha256": actual_hash,
            "hash_ok": actual_hash == target.get("full_frame_sha256"),
            "raw_or_derived": "raw",
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        return image, report

    def restore_frame_to_png(self, frame_id: str, output_path: str | Path) -> dict[str, Any]:
        image, report = self.restore_frame(frame_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        report["restored_media_path_abs"] = str(path.resolve())
        return report

    def restore_region(
        self,
        frame_id: str,
        region: dict[str, int],
        *,
        scale_down: int | float = 1,
        blur_radius: int | float = 0,
    ):
        from PIL import ImageFilter

        image, restore_report = self.restore_frame(frame_id)
        x = max(0, int(region.get("x", 0)))
        y = max(0, int(region.get("y", 0)))
        w = max(1, int(region.get("w", image.size[0] - x)))
        h = max(1, int(region.get("h", image.size[1] - y)))
        crop = image.crop((x, y, min(image.size[0], x + w), min(image.size[1], y + h)))
        scale = float(scale_down or 1)
        if scale > 1:
            next_size = (max(1, int(crop.size[0] / scale)), max(1, int(crop.size[1] / scale)))
            crop = crop.resize(next_size)
        blur = float(blur_radius or 0)
        if blur > 0:
            crop = crop.filter(ImageFilter.GaussianBlur(radius=blur))
        report = {
            "object_type": "AgentSightBinarySegmentRegionReviewReport",
            "schema": self.manifest.get("schema"),
            "canonical_evidence_source": BINARY_CONTAINER_MODEL,
            "segment_id": self.manifest.get("segment_id"),
            "segment_path_abs": str(self.segment_path.resolve()),
            "source_frame_id": frame_id,
            "source_frame_hash_ok": restore_report["hash_ok"],
            "region": {"x": x, "y": y, "w": w, "h": h},
            "scale_down": int(scale) if scale.is_integer() else scale,
            "blur_radius": int(blur) if blur.is_integer() else blur,
            "raw_or_derived": "derived_review_only",
            "artifact_is_canonical_evidence": False,
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        return crop, report

    def _read_container_metadata(self) -> tuple[dict[str, Any], dict[str, Any]]:
        with self.segment_path.open("rb") as handle:
            magic = handle.read(len(AGSEG_MAGIC))
            if magic != AGSEG_MAGIC:
                raise ValueError("not an AgentSight .agseg file")
            header_len = struct.unpack("<I", handle.read(4))[0]
            header = json.loads(handle.read(header_len).decode("utf-8"))
            handle.seek(0, 2)
            file_size = handle.tell()
            footer_size = 8 + len(AGSEG_FOOTER_MAGIC)
            if file_size < len(AGSEG_MAGIC) + 4 + header_len + footer_size:
                raise ValueError("truncated AgentSight .agseg file")
            handle.seek(file_size - len(AGSEG_FOOTER_MAGIC))
            if handle.read(len(AGSEG_FOOTER_MAGIC)) != AGSEG_FOOTER_MAGIC:
                raise ValueError("AgentSight .agseg footer missing")
            handle.seek(file_size - footer_size)
            manifest_len = struct.unpack("<Q", handle.read(8))[0]
            manifest_start = file_size - footer_size - manifest_len
            if manifest_start < len(AGSEG_MAGIC) + 4 + header_len:
                raise ValueError("invalid AgentSight .agseg manifest length")
            handle.seek(manifest_start)
            manifest = json.loads(handle.read(manifest_len).decode("utf-8"))
        return header, manifest

    def _read_blob_bytes(self, blob_ref: str) -> bytes:
        blob_id = _blob_id_from_ref(blob_ref)
        if blob_id not in self._blobs:
            raise KeyError(f"blob not found: {blob_ref}")
        blob = self._blobs[blob_id]
        with self.segment_path.open("rb") as handle:
            handle.seek(int(blob["offset"]))
            payload = handle.read(int(blob["length"]))
        digest = hashlib.sha256(payload).hexdigest()
        if digest != blob.get("sha256"):
            raise ValueError(f"blob hash mismatch: {blob_id}")
        return payload


def _blob_id_from_ref(blob_ref: str) -> str:
    if not blob_ref.startswith("agseg:"):
        raise ValueError(f"unsupported binary blob ref: {blob_ref}")
    return blob_ref.split(":", 1)[1]


def _footer_start(segment_path: Path) -> int:
    with segment_path.open("rb") as handle:
        magic = handle.read(len(AGSEG_MAGIC))
        if magic != AGSEG_MAGIC:
            raise ValueError("not an AgentSight .agseg file")
        header_len = struct.unpack("<I", handle.read(4))[0]
        handle.seek(0, 2)
        file_size = handle.tell()
        footer_size = 8 + len(AGSEG_FOOTER_MAGIC)
        if file_size < len(AGSEG_MAGIC) + 4 + header_len + footer_size:
            raise ValueError("truncated AgentSight .agseg file")
        handle.seek(file_size - len(AGSEG_FOOTER_MAGIC))
        if handle.read(len(AGSEG_FOOTER_MAGIC)) != AGSEG_FOOTER_MAGIC:
            raise ValueError("AgentSight .agseg footer missing")
        handle.seek(file_size - footer_size)
        manifest_len = struct.unpack("<Q", handle.read(8))[0]
        manifest_start = file_size - footer_size - manifest_len
        if manifest_start < len(AGSEG_MAGIC) + 4 + header_len:
            raise ValueError("invalid AgentSight .agseg manifest length")
        return int(manifest_start)


def _normalize_image_encoding(value: str) -> str:
    normalized = str(value or "webp").strip().lower()
    if normalized in {"png", "lossless_png"}:
        return "png"
    return "webp"
