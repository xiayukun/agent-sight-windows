from __future__ import annotations

import importlib.util
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agentsight.adapters.mcp import MCPStdioAdapter
from agentsight.evidence.store import EvidenceReplayService
from agentsight.segments import SegmentFrameRecorder, decode_mkv_frame_to_image
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3AStablePostObserveLookPngChannel


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for Segment capture integration tests")
class PF2SegmentCaptureIntegrationTest(unittest.TestCase):
    def test_mkv_segment_recorder_uses_stable_daily_bucket_without_overwriting_existing_session(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first_media = root / "first.png"
            second_media = root / "second.png"
            base = Image.new("RGBA", (32, 20), (20, 30, 40, 255))
            changed = base.copy()
            for x in range(3, 9):
                for y in range(2, 8):
                    changed.putpixel((x, y), (220, 30, 40, 255))
            base.save(first_media)
            changed.save(second_media)

            first_evidence = EvidenceReplayService(root, session_id="session-first")
            first = SegmentFrameRecorder(first_evidence, segment_bucket_granularity="daily")
            first_record = first.record_frame(
                {
                    "media_path_abs": str(first_media),
                    "captured_at": "2026-06-20T10:00:00+08:00",
                    "screen_region": {"x": 100, "y": 50, "width": 32, "height": 20},
                    "coordinate_system": "virtual_screen_pixels",
                },
                source="look",
                event_id=None,
            )
            first.close()

            second_evidence = EvidenceReplayService(root, session_id="session-second")
            second = SegmentFrameRecorder(second_evidence, segment_bucket_granularity="daily")
            second_record = second.record_frame(
                {
                    "media_path_abs": str(second_media),
                    "captured_at": "2026-06-20T10:00:01+08:00",
                    "screen_region": {"x": 100, "y": 50, "width": 32, "height": 20},
                    "coordinate_system": "virtual_screen_pixels",
                },
                source="look",
                event_id=None,
            )
            second.close()

            self.assertEqual(Path(first_record["segment_path_abs"]).parent, Path(second_record["segment_path_abs"]).parent)
            self.assertEqual(Path(first_record["segment_path_abs"]).name, "agentsight-20260620-001.mkv")
            self.assertEqual(Path(second_record["segment_path_abs"]).name, "agentsight-20260620-002.mkv")
            self.assertEqual(first_record["frame_id"], "f000000")
            self.assertEqual(second_record["frame_id"], "f000000")
            self.assertEqual(second_record["frame_kind"], "vfr_frame")
            self.assertEqual(second_record["storage_format"], "mkv_vfr")

            restored, report = decode_mkv_frame_to_image(second_record["restore_ref"])
            self.assertEqual(restored.size, (32, 20))
            self.assertEqual(report["status"], "decoded")
            self.assertFalse(report["tool_asserts_business_success"])

    def test_mkv_segment_recorder_serializes_concurrent_writes_to_same_bucket(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            frames_dir = root / "frames"
            frames_dir.mkdir()
            frame_paths: list[Path] = []
            for index in range(12):
                media = frames_dir / f"frame-{index:02d}.png"
                image = Image.new("RGBA", (48, 28), (20, 30, 40, 255))
                for x in range(index % 9, min(48, index % 9 + 6)):
                    for y in range((index * 2) % 11, min(28, (index * 2) % 11 + 5)):
                        image.putpixel((x, y), (60 + index, 120, 180, 255))
                image.save(media)
                frame_paths.append(media)

            evidence = EvidenceReplayService(root, session_id="session-concurrent")
            recorder = SegmentFrameRecorder(evidence, segment_bucket_granularity="daily")

            def record(index: int) -> dict[str, object]:
                return recorder.record_frame(
                    {
                        "media_path_abs": str(frame_paths[index]),
                        "captured_at": f"2026-06-20T10:00:{index:02d}+08:00",
                        "screen_region": {"x": 0, "y": 0, "width": 48, "height": 28},
                        "coordinate_system": "virtual_screen_pixels",
                    },
                    source="look",
                    event_id=f"concurrent-{index:02d}",
                )

            with ThreadPoolExecutor(max_workers=6) as pool:
                records = list(pool.map(record, range(len(frame_paths))))
            recorder.close()

            self.assertEqual({record["status"] for record in records}, {"recorded"})
            self.assertEqual(len({record["frame_id"] for record in records}), len(frame_paths))
            self.assertEqual(len({record["segment_path_abs"] for record in records}), 1)
            self.assertEqual(Path(records[0]["segment_path_abs"]).suffix, ".mkv")
            self.assertEqual(len(Path(records[0]["index_path_abs"]).read_text(encoding="utf-8").splitlines()), len(frame_paths))
            for record in records:
                _restored, report = decode_mkv_frame_to_image(record["restore_ref"])
                self.assertEqual(report["status"], "decoded")
                self.assertFalse(report["tool_asserts_business_success"])

    def test_visual_session_mkv_segments_share_runs_root_bucket_file(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            media = root / "frame.png"
            Image.new("RGBA", (24, 16), (40, 50, 60, 255)).save(media)

            evidence = EvidenceReplayService(root / "visual-default", session_id="session-visual")
            recorder = SegmentFrameRecorder(evidence, segment_bucket_granularity="daily")
            record = recorder.record_frame(
                {"media_path_abs": str(media), "captured_at": "2026-06-20T10:00:00+08:00"},
                source="look",
                event_id=None,
            )
            recorder.close()

            self.assertEqual(Path(record["segment_path_abs"]).parent.resolve(), (root / "segments").resolve())
            self.assertEqual(Path(record["segment_path_abs"]).name, "agentsight-20260620-001.mkv")
            self.assertTrue(Path(record["segment_path_abs"]).exists())

    def test_public_look_and_do_post_observe_frames_are_written_to_canonical_segment(self) -> None:
        observation = P3AStablePostObserveLookPngChannel()
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[observation],
                default_observation_channel_ref=observation.name,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            look = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "pf2-look",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 60, "h": 40},
                    "scale_down": 1,
                },
            )
            view_id = look["data"]["view"]["id"]
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "pf2-do",
                    "basis": {"view_id": view_id},
                    "seq": [{"t": "wait", "ms": 1}],
                    "post_observe": {"delay_ms": 0, "frame_count": 3, "interval_ms": 0},
                },
            )

            segment_status = adapter.session.gateway.segment_recorder.status()
            segment_manifest = adapter.session.gateway.segment_recorder.manifest()
            do_entries = [entry for entry in adapter.session.gateway.frame_buffer.entries if entry.get("source") == "do_after_frame"]
            adapter.session.gateway.segment_recorder.close()

            self.assertTrue(look["ok"])
            self.assertTrue(action["ok"])
            self.assertEqual(segment_status["storage_format"], "mkv_vfr")
            self.assertTrue(Path(segment_status["segment_path_abs"]).exists())
            self.assertEqual(Path(segment_status["segment_path_abs"]).suffix, ".mkv")
            self.assertTrue(Path(segment_status["index_path_abs"]).exists())
            self.assertTrue(Path(segment_status["manifest_path_abs"]).exists())
            self.assertEqual(segment_status["status"], "active")
            self.assertEqual(segment_manifest["storage_format"], "mkv_vfr")
            self.assertEqual(segment_manifest["container_model"], "mkv_vfr_ffmpeg_v1")
            self.assertGreaterEqual(segment_manifest["frame_count"], 4)
            self.assertGreaterEqual(
                sum(1 for frame in segment_manifest["frames"] if frame.get("frame_kind") == "vfr_frame"),
                4,
            )
            self.assertEqual({entry["segment_frame"]["source"] for entry in do_entries}, {"post_do"})
            self.assertEqual({entry["segment_frame"]["event_id"] for entry in do_entries}, {"pf2-do"})
            self.assertEqual(
                {frame["segment_frame"]["source"] for frame in action["data"]["post_observe"]["sampled_frames"]},
                {"post_do"},
            )
            self.assertFalse(segment_status["boundary"]["business_success_judged"])

            restore_ref = do_entries[-1]["segment_frame"]["restore_ref"]
            self.assertEqual(restore_ref["storage_format"], "mkv_vfr")
            restored, report = decode_mkv_frame_to_image(restore_ref)
            self.assertEqual(restored.size, (60, 40))
            self.assertEqual(report["status"], "decoded")
            self.assertFalse(report["tool_asserts_business_success"])


if __name__ == "__main__":
    unittest.main()
