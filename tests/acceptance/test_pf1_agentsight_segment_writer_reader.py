from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for Segment Writer/Reader tests")
class PF1AgentSightSegmentWriterReaderTest(unittest.TestCase):
    def _image(self, color: tuple[int, int, int, int] = (8, 12, 16, 255)):
        from PIL import Image

        return Image.new("RGBA", (16, 16), color)

    def _changed(self, base, box: tuple[int, int, int, int], color: tuple[int, int, int, int]):
        image = base.copy()
        for x in range(box[0], box[2]):
            for y in range(box[1], box[3]):
                image.putpixel((x, y), color)
        return image

    def test_static_frames_write_no_change_record_and_restore(self) -> None:
        from agentsight.segments import SegmentReader, SegmentWriter, validate_segment_manifest

        with tempfile.TemporaryDirectory() as tmp:
            writer = SegmentWriter.create(Path(tmp) / "segment-static", segment_id="seg-static")
            first = writer.add_frame(
                self._image(),
                timestamp_iso="2026-06-21T00:00:00Z",
                timestamp_monotonic_ms=1000,
                source="idle",
            )
            second = writer.add_frame(
                self._image(),
                timestamp_iso="2026-06-21T00:00:01Z",
                timestamp_monotonic_ms=2000,
                source="idle",
            )
            manifest = writer.close()

            self.assertEqual(first["frame_kind"], "keyframe")
            self.assertEqual(second["frame_kind"], "pframe_no_change")
            self.assertEqual(manifest["pframe_no_change_count"], 1)
            self.assertTrue(validate_segment_manifest(manifest)["valid"])

            restored, report = SegmentReader(writer.segment_dir).restore_frame(second["frame_id"])
            self.assertTrue(report["hash_ok"], report)
            self.assertEqual(restored.size, (16, 16))
            self.assertFalse(report["boundary"]["business_success_judged"])

    def test_small_region_change_writes_delta_crop_and_restores_exact_pixels(self) -> None:
        from agentsight.segments import SegmentReader, SegmentWriter, sha256_image_rgba

        with tempfile.TemporaryDirectory() as tmp:
            base = self._image()
            changed = self._changed(base, (2, 3, 5, 7), (220, 30, 20, 255))
            writer = SegmentWriter.create(
                Path(tmp) / "segment-small",
                segment_id="seg-small",
                keyframe_threshold_ratio=0.5,
            )
            writer.add_frame(base, timestamp_iso="2026-06-21T00:00:00Z", timestamp_monotonic_ms=1000, source="idle")
            record = writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T00:00:00.100Z",
                timestamp_monotonic_ms=1100,
                source="post_do",
                event_id="do-1",
            )
            writer.close()

            self.assertEqual(record["frame_kind"], "pframe_delta")
            self.assertEqual(record["delta_bbox"], {"x": 2, "y": 3, "w": 3, "h": 4})
            self.assertTrue((writer.segment_dir / record["delta_blob_ref"]).exists())

            restored, report = SegmentReader(writer.segment_dir).restore_frame(record["frame_id"])
            self.assertTrue(report["hash_ok"], report)
            self.assertEqual(sha256_image_rgba(restored), sha256_image_rgba(changed))

    def test_large_region_change_forces_new_keyframe(self) -> None:
        from agentsight.segments import SegmentWriter

        with tempfile.TemporaryDirectory() as tmp:
            writer = SegmentWriter.create(
                Path(tmp) / "segment-large",
                segment_id="seg-large",
                keyframe_threshold_ratio=0.25,
            )
            writer.add_frame(self._image(), timestamp_iso="2026-06-21T00:00:00Z", timestamp_monotonic_ms=1000, source="idle")
            record = writer.add_frame(
                self._image((200, 40, 70, 255)),
                timestamp_iso="2026-06-21T00:00:01Z",
                timestamp_monotonic_ms=2000,
                source="idle",
            )
            manifest = writer.close()

            self.assertEqual(record["frame_kind"], "keyframe")
            self.assertEqual(manifest["keyframe_count"], 2)

    def test_keyframe_interval_and_delta_chain_restore(self) -> None:
        from agentsight.segments import SegmentReader, SegmentWriter, sha256_image_rgba

        with tempfile.TemporaryDirectory() as tmp:
            base = self._image()
            frame_1 = self._changed(base, (1, 1, 3, 3), (255, 0, 0, 255))
            frame_2 = self._changed(frame_1, (5, 5, 7, 7), (0, 255, 0, 255))
            frame_3 = self._changed(frame_2, (9, 9, 11, 11), (0, 0, 255, 255))
            writer = SegmentWriter.create(
                Path(tmp) / "segment-chain",
                segment_id="seg-chain",
                keyframe_interval=3,
                keyframe_threshold_ratio=0.75,
            )
            records = [
                writer.add_frame(base, timestamp_iso="2026-06-21T00:00:00Z", timestamp_monotonic_ms=1000, source="idle"),
                writer.add_frame(frame_1, timestamp_iso="2026-06-21T00:00:01Z", timestamp_monotonic_ms=2000, source="post_do"),
                writer.add_frame(frame_2, timestamp_iso="2026-06-21T00:00:02Z", timestamp_monotonic_ms=3000, source="post_do"),
                writer.add_frame(frame_3, timestamp_iso="2026-06-21T00:00:03Z", timestamp_monotonic_ms=4000, source="post_do"),
            ]
            manifest = writer.close()

            self.assertEqual([record["frame_kind"] for record in records], ["keyframe", "pframe_delta", "pframe_delta", "keyframe"])
            self.assertEqual(manifest["frame_count"], 4)
            self.assertEqual(manifest["keyframe_count"], 2)
            self.assertEqual(manifest["pframe_delta_count"], 2)

            restored, report = SegmentReader(writer.segment_dir).restore_frame(records[2]["frame_id"])
            self.assertTrue(report["hash_ok"], report)
            self.assertEqual(sha256_image_rgba(restored), sha256_image_rgba(frame_2))


if __name__ == "__main__":
    unittest.main()
