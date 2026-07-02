from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentsight.segments.manifest import boundary_facts, sha256_image_rgba


class SegmentReader:
    def __init__(self, segment_dir: str | Path) -> None:
        self.segment_dir = Path(segment_dir)
        self.manifest = json.loads((self.segment_dir / "manifest.json").read_text(encoding="utf-8"))
        self._frames: list[dict[str, Any]] = list(self.manifest.get("frames") or [])
        self._by_id = {frame["frame_id"]: frame for frame in self._frames}

    def restore_frame(self, frame_id: str):
        from PIL import Image

        if frame_id not in self._by_id:
            raise KeyError(f"frame not found: {frame_id}")
        target = self._by_id[frame_id]
        keyframe_id = target["frame_id"] if target["frame_kind"] == "keyframe" else target.get("nearest_keyframe_id")
        if not keyframe_id or keyframe_id not in self._by_id:
            raise ValueError(f"nearest keyframe not found for {frame_id}")

        keyframe = self._by_id[keyframe_id]
        image = Image.open(self.segment_dir / keyframe["keyframe_blob_ref"]).convert("RGBA")
        restored_ids = [keyframe_id]
        if keyframe_id != frame_id:
            start = int(keyframe["frame_index"]) + 1
            stop = int(target["frame_index"]) + 1
            for record in self._frames[start:stop]:
                kind = record["frame_kind"]
                if kind == "keyframe":
                    image = Image.open(self.segment_dir / record["keyframe_blob_ref"]).convert("RGBA")
                elif kind == "pframe_delta":
                    bbox = record["delta_bbox"]
                    crop = Image.open(self.segment_dir / record["delta_blob_ref"]).convert("RGBA")
                    image.paste(crop, (int(bbox["x"]), int(bbox["y"])))
                elif kind == "pframe_no_change":
                    image = image.copy()
                else:
                    raise ValueError(f"unsupported frame kind: {kind}")
                restored_ids.append(record["frame_id"])

        actual_hash = sha256_image_rgba(image)
        report = {
            "object_type": "AgentSightSegmentRestoreReport",
            "schema": self.manifest.get("schema"),
            "segment_id": self.manifest.get("segment_id"),
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

    def restore_frame_to_path(self, frame_id: str, output_path: str | Path) -> dict[str, Any]:
        image, report = self.restore_frame(frame_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
        report["restored_media_path_abs"] = str(path.resolve())
        return report
