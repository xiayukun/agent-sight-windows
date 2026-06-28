from __future__ import annotations

import os
import time
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from ai_control.evidence.store import EvidenceReplayService
from ai_control.segments.mkv_container import decode_mkv_frame_to_image, iter_mkv_frames
from ai_control.segments.recorder import SegmentFrameRecorder
from ai_control.tray.viewers import build_timeline_model


class MkvSegmentStorageTest(unittest.TestCase):
    def test_recorder_writes_mkv_index_and_timeline_decodes_selected_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["LOCALAPPDATA"] = temp_dir
            root = Path(temp_dir) / "ai-control" / "runs_host_agent" / "visual-default"
            recorder = SegmentFrameRecorder(EvidenceReplayService(root))
            refs = []
            for index, color in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
                image = Image.new("RGBA", (64, 48), (*color, 255))
                frame = {
                    "observation_id": f"obs-{index}",
                    "captured_at": time.time(),
                    "width": 64,
                    "height": 48,
                    "_bgra_bytes": image.tobytes("raw", "BGRA"),
                    "screen_region": {"x": 0, "y": 0, "w": 64, "h": 48},
                    "coordinate_system": "virtual_screen_pixels",
                }
                refs.append(recorder.record_frame(frame, source="test", event_id=None))
                time.sleep(0.02)
            recorder.close()

            runs_root = Path(temp_dir) / "ai-control" / "runs_host_agent"
            self.assertEqual([ref["storage_format"] for ref in refs], ["mkv_vfr", "mkv_vfr", "mkv_vfr"])
            self.assertEqual(len(list((runs_root / "segments").glob("*.mkv"))), 1)
            self.assertEqual(len(list((runs_root / "segments").glob("*.agseg"))), 0)
            self.assertEqual(len(iter_mkv_frames(runs_root)), 3)
            image, report = decode_mkv_frame_to_image(refs[-1]["restore_ref"])
            self.assertEqual(image.size, (64, 48))
            self.assertEqual(report["storage_format"], "mkv_vfr")

            model = build_timeline_model(max_frames=10, max_logs=10)
            self.assertEqual(model["frame_count"], 3)
            self.assertEqual(model["frames"][-1]["storage_format"], "mkv_vfr")
            self.assertFalse(model["boundary"]["ocr_used"])
            self.assertFalse(model["boundary"]["clipboard_used"])


if __name__ == "__main__":
    unittest.main()
