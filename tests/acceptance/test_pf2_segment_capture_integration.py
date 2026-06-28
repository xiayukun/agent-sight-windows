from __future__ import annotations

import importlib.util
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ai_control.adapters.mcp import MCPStdioAdapter
from ai_control.evidence.store import EvidenceReplayService
from ai_control.segments import BinarySegmentReader, SegmentFrameRecorder
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3AStablePostObserveLookPngChannel


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for Segment capture integration tests")
class PF2SegmentCaptureIntegrationTest(unittest.TestCase):
    def test_binary_segment_recorder_appends_to_stable_daily_bucket_across_sessions(self) -> None:
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

            self.assertEqual(first_record["segment_path_abs"], second_record["segment_path_abs"])
            self.assertEqual(Path(second_record["segment_path_abs"]).name, "agentsight-20260620.agseg")
            self.assertEqual(first_record["frame_id"], "f000000")
            self.assertEqual(second_record["frame_id"], "f000001")
            self.assertEqual(second_record["frame_kind"], "pframe_delta")

            reader = BinarySegmentReader(second_record["segment_path_abs"])
            restored, report = reader.restore_frame(second_record["frame_id"])
            manifest = reader.manifest
            self.assertEqual(restored.size, (32, 20))
            self.assertEqual(manifest["frames"][0]["screen_region"], {"x": 100, "y": 50, "w": 32, "h": 20})
            self.assertEqual(manifest["frames"][0]["coordinate_system"], "virtual_screen_pixels")
            self.assertEqual(manifest["frames"][1]["screen_region"], {"x": 100, "y": 50, "w": 32, "h": 20})
            self.assertTrue(report["hash_ok"], report)
            self.assertFalse(report["tool_asserts_business_success"])

    def test_binary_segment_recorder_serializes_concurrent_writes_to_same_bucket(self) -> None:
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
            reader = BinarySegmentReader(records[0]["segment_path_abs"])
            self.assertEqual(reader.manifest["frame_count"], len(frame_paths))
            for record in records:
                _restored, report = reader.restore_frame(str(record["frame_id"]))
                self.assertTrue(report["hash_ok"], report)
                self.assertFalse(report["tool_asserts_business_success"])

    def test_visual_session_binary_segments_share_runs_root_bucket_file(self) -> None:
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

            self.assertEqual(Path(record["segment_path_abs"]).parent, root / "segments")
            self.assertEqual(Path(record["segment_path_abs"]).name, "agentsight-20260620.agseg")
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

            self.assertTrue(look["ok"])
            self.assertTrue(action["ok"])
            self.assertEqual(segment_status["storage_format"], "binary_agseg")
            self.assertTrue(Path(segment_status["segment_path_abs"]).exists())
            self.assertEqual(Path(segment_status["segment_path_abs"]).suffix, ".agseg")
            self.assertEqual(segment_status["status"], "active")
            self.assertEqual(segment_manifest["schema"], "agentsight_segment_v1")
            self.assertEqual(segment_manifest["container_model"], "single_file_agseg_v1")
            self.assertGreaterEqual(segment_manifest["frame_count"], 4)
            self.assertGreaterEqual(segment_manifest["pframe_delta_count"] + segment_manifest["pframe_no_change_count"], 1)
            self.assertEqual({entry["segment_frame"]["source"] for entry in do_entries}, {"post_do"})
            self.assertEqual({entry["segment_frame"]["event_id"] for entry in do_entries}, {"pf2-do"})
            self.assertEqual(
                {frame["segment_frame"]["source"] for frame in action["data"]["post_observe"]["sampled_frames"]},
                {"post_do"},
            )
            self.assertFalse(segment_status["boundary"]["business_success_judged"])

            restore_ref = do_entries[-1]["segment_frame"]["restore_ref"]
            self.assertEqual(restore_ref["storage_format"], "binary_agseg")
            reader = BinarySegmentReader(restore_ref["segment_path"])
            restored, report = reader.restore_frame(restore_ref["frame_id"])
            self.assertEqual(restored.size, (60, 40))
            self.assertTrue(report["hash_ok"], report)
            self.assertFalse(report["tool_asserts_business_success"])
            adapter.session.gateway.segment_recorder.close()


if __name__ == "__main__":
    unittest.main()
