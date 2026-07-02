from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from agentsight.adapters.mcp import MCPStdioAdapter
from agentsight.segments import BinarySegmentReader, SegmentReader
from agentsight.segments.decoder import decode_segment_frame_to_image
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3AStablePostObserveLookPngChannel


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for Segment-aware frame index tests")
class PF3SegmentAwareFrameIndexTest(unittest.TestCase):
    def test_time_near_returns_segment_restore_ref_without_recapturing(self) -> None:
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
                    "id": "pf3-look",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 60, "h": 40},
                    "scale_down": 1,
                },
            )
            view_id = look["data"]["view"]["id"]
            before_capture_count = len(observation.captures)
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "pf3-do",
                    "basis": {"view_id": view_id},
                    "seq": [{"t": "wait", "ms": 1}],
                    "post_observe": {"delay_ms": 0, "frame_count": 2, "interval_ms": 0},
                },
            )
            entries = adapter.session.gateway.frame_buffer.entries
            after_entries = [entry for entry in entries if entry.get("source") == "do_after_frame"]
            requested_time = after_entries[-1]["captured_at"]
            lookup = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "pf3-near",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 60, "h": 40},
                    "scale_down": 1,
                    "time": {"near": str(requested_time)},
                },
            )
            after_lookup_capture_count = len(observation.captures)
            nearest = lookup["data"]["frames_near_time"]["nearest_frame"]
            restore_ref = nearest["segment_restore_ref"]

            if restore_ref.get("storage_format") == "binary_agseg":
                restored, report = BinarySegmentReader(Path(restore_ref["segment_path"])).restore_frame(restore_ref["frame_id"])
            elif restore_ref.get("storage_format") == "mkv_vfr" or str(restore_ref.get("segment_path", "")).lower().endswith(".mkv"):
                adapter.session.gateway.segment_recorder.close()
                restored, report = decode_segment_frame_to_image(restore_ref)
            else:
                restored, report = SegmentReader(Path(restore_ref["segment_path"])).restore_frame(restore_ref["frame_id"])
            adapter.session.gateway.segment_recorder.close()

        self.assertTrue(action["ok"])
        self.assertTrue(lookup["ok"])
        self.assertEqual(before_capture_count + 2, after_lookup_capture_count)
        self.assertTrue(lookup["data"]["no_capture_performed"])
        self.assertEqual(nearest["segment_frame_id"], restore_ref["frame_id"])
        self.assertEqual(nearest["segment_source"], "post_do")
        self.assertEqual(restored.size, (60, 40))
        self.assertTrue(report["hash_ok"], report)
        self.assertFalse(nearest["business_success_judged"])


if __name__ == "__main__":
    unittest.main()
