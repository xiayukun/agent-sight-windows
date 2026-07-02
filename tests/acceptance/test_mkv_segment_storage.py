from __future__ import annotations

import json
import os
import time
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from agentsight.evidence.store import EvidenceReplayService
from agentsight.gateway import ProtocolGateway
from agentsight.segments.decoder import query_segment_change_index, query_segment_decoder_near_time
from agentsight.segments.global_index import build_global_segment_frame_index
from agentsight.segments.mkv_container import MkvSegmentWriter, decode_mkv_frame_to_image, iter_mkv_frames
from agentsight.segments.recorder import SegmentFrameRecorder
import agentsight.segments.recorder as recorder_module
from agentsight.tray.viewers import build_timeline_model
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3ALookPngChannel


class MkvSegmentStorageTest(unittest.TestCase):
    def test_recorder_writes_mkv_index_and_timeline_decodes_selected_frame(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": temp_dir}, clear=False):
                root = Path(temp_dir) / "AgentSight" / "runs_host_agent" / "visual-default"
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

                runs_root = Path(temp_dir) / "AgentSight" / "runs_host_agent"
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
                self.assertEqual(model["frames"][-1]["timeline_time_basis"], "timestamp_ms")
                self.assertFalse(model["boundary"]["ocr_used"])
                self.assertFalse(model["boundary"]["clipboard_used"])


    def test_recorder_rotates_mkv_segment_when_size_threshold_is_reached(self) -> None:
        class FakeMkvSegmentWriter:
            def __init__(self, path: Path, *, segment_id: str, width: int, height: int) -> None:
                self.path = path
                self.segment_id = segment_id
                self.width = width
                self.height = height
                self.frames: list[dict[str, object]] = []
                self.started_at_ms = 10_000_000_000_000
                self.closed = False
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(b"")
                self.index_path = self.path.with_suffix(".frames.jsonl")
                self.manifest_path = self.path.with_suffix(".manifest.json")

            def add_frame(self, bgra_bytes: bytes, *, captured_at_ms: int, **common: object) -> dict[str, object]:
                frame_index = len(self.frames)
                frame_id = f"f{frame_index:06d}"
                record = {
                    "frame_id": frame_id,
                    "frame_index": frame_index,
                    "logical_frame_id": frame_id,
                    "logical_frame_index": frame_index,
                    "physical_frame_id": f"p{frame_index:06d}",
                    "physical_frame_index": frame_index,
                    "logical_duplicate": False,
                    "frame_kind": "vfr_frame",
                    "timestamp_ms": captured_at_ms,
                    "timestamp_iso": common.get("timestamp_iso"),
                    "pts_ms": max(0, captured_at_ms - self.started_at_ms),
                    "playback_pts_ms": frame_index * 40,
                    "playback_time_basis": "test_physical_frame_index",
                    "source": common.get("source"),
                    "event_id": common.get("event_id"),
                }
                self.frames.append(record)
                with self.path.open("ab") as fh:
                    fh.write(b"x" * 2048)
                with self.index_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
                return record

            def close(self) -> None:
                self.closed = True
                self.manifest_path.write_text(
                    json.dumps({"segment_id": self.segment_id, "finalized": True, "frames": self.frames}),
                    encoding="utf-8",
                )

            @property
            def manifest(self) -> dict[str, object]:
                return {"frames": list(self.frames), "frame_count": len(self.frames), "storage_format": "mkv_vfr"}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "AgentSight" / "runs_host_agent" / "visual-default"
            recorder = SegmentFrameRecorder(
                EvidenceReplayService(root),
                segment_bucket_granularity="hourly",
                max_segment_size_mb=0.00005,
            )
            recorder._max_segment_duration_ms = 10**18
            with mock.patch.object(recorder_module, "MkvSegmentWriter", FakeMkvSegmentWriter):
                first = recorder.record_frame(
                    {
                        "captured_at": "2026-06-30T10:00:00+08:00",
                        "width": 4,
                        "height": 4,
                        "_bgra_bytes": bytes([1, 2, 3, 255]) * 16,
                    },
                    source="idle",
                    event_id="first",
                )
                second = recorder.record_frame(
                    {
                        "captured_at": "2026-06-30T10:00:01+08:00",
                        "width": 4,
                        "height": 4,
                        "_bgra_bytes": bytes([2, 3, 4, 255]) * 16,
                    },
                    source="idle",
                    event_id="second",
                )
                recorder.close()
                first_manifest_exists = Path(first["manifest_path_abs"]).exists()
                second_manifest_exists = Path(second["manifest_path_abs"]).exists()

            self.assertEqual(first["status"], "recorded")
            self.assertEqual(second["status"], "recorded")
            self.assertEqual(Path(first["segment_path_abs"]).name, "agentsight-20260630-10-001.mkv")
            self.assertEqual(Path(second["segment_path_abs"]).name, "agentsight-20260630-10-002.mkv")
            self.assertTrue(first_manifest_exists)
            self.assertTrue(second_manifest_exists)

    def test_recorder_keeps_mkv_segment_when_size_threshold_is_not_reached(self) -> None:
        class FakeMkvSegmentWriter:
            def __init__(self, path: Path, *, segment_id: str, width: int, height: int) -> None:
                self.path = path
                self.segment_id = segment_id
                self.width = width
                self.height = height
                self.frames: list[dict[str, object]] = []
                self.started_at_ms = 10_000_000_000_000
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(b"")

            def add_frame(self, bgra_bytes: bytes, *, captured_at_ms: int, **common: object) -> dict[str, object]:
                frame_index = len(self.frames)
                frame_id = f"f{frame_index:06d}"
                record = {
                    "frame_id": frame_id,
                    "frame_index": frame_index,
                    "logical_frame_id": frame_id,
                    "logical_frame_index": frame_index,
                    "physical_frame_id": f"p{frame_index:06d}",
                    "physical_frame_index": frame_index,
                    "logical_duplicate": False,
                    "frame_kind": "vfr_frame",
                    "timestamp_ms": captured_at_ms,
                    "pts_ms": max(0, captured_at_ms - self.started_at_ms),
                    "source": common.get("source"),
                }
                self.frames.append(record)
                with self.path.open("ab") as fh:
                    fh.write(b"x" * 2048)
                return record

            def close(self) -> None:
                self.path.with_suffix(".manifest.json").write_text(
                    json.dumps({"segment_id": self.segment_id, "finalized": True, "frames": self.frames}),
                    encoding="utf-8",
                )

            @property
            def manifest(self) -> dict[str, object]:
                return {"frames": list(self.frames), "frame_count": len(self.frames), "storage_format": "mkv_vfr"}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "AgentSight" / "runs_host_agent" / "visual-default"
            recorder = SegmentFrameRecorder(
                EvidenceReplayService(root),
                segment_bucket_granularity="hourly",
                max_segment_size_mb=10,
            )
            recorder._max_segment_duration_ms = 10**18
            with mock.patch.object(recorder_module, "MkvSegmentWriter", FakeMkvSegmentWriter):
                first = recorder.record_frame(
                    {
                        "captured_at": "2026-06-30T10:00:00+08:00",
                        "width": 4,
                        "height": 4,
                        "_bgra_bytes": bytes([1, 2, 3, 255]) * 16,
                    },
                    source="idle",
                    event_id="first",
                )
                second = recorder.record_frame(
                    {
                        "captured_at": "2026-06-30T10:00:01+08:00",
                        "width": 4,
                        "height": 4,
                        "_bgra_bytes": bytes([2, 3, 4, 255]) * 16,
                    },
                    source="idle",
                    event_id="second",
                )
                recorder.close()

            self.assertEqual(Path(first["segment_path_abs"]).name, "agentsight-20260630-10-001.mkv")
            self.assertEqual(Path(second["segment_path_abs"]).name, "agentsight-20260630-10-001.mkv")

    def test_recorder_forces_quota_check_after_size_rotation_even_when_throttled(self) -> None:
        class FakeMkvSegmentWriter:
            def __init__(self, path: Path, *, segment_id: str, width: int, height: int) -> None:
                self.path = path
                self.segment_id = segment_id
                self.width = width
                self.height = height
                self.frames: list[dict[str, object]] = []
                self.started_at_ms = 10_000_000_000_000
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_bytes(b"")

            def add_frame(self, bgra_bytes: bytes, *, captured_at_ms: int, **common: object) -> dict[str, object]:
                frame_index = len(self.frames)
                frame_id = f"f{frame_index:06d}"
                record = {
                    "frame_id": frame_id,
                    "frame_index": frame_index,
                    "logical_frame_id": frame_id,
                    "logical_frame_index": frame_index,
                    "physical_frame_id": f"p{frame_index:06d}",
                    "physical_frame_index": frame_index,
                    "logical_duplicate": False,
                    "frame_kind": "vfr_frame",
                    "timestamp_ms": captured_at_ms,
                    "pts_ms": max(0, captured_at_ms - self.started_at_ms),
                    "source": common.get("source"),
                }
                self.frames.append(record)
                with self.path.open("ab") as fh:
                    fh.write(b"x" * 2048)
                return record

            def close(self) -> None:
                self.path.with_suffix(".manifest.json").write_text(
                    json.dumps({"segment_id": self.segment_id, "finalized": True, "frames": self.frames}),
                    encoding="utf-8",
                )

            @property
            def manifest(self) -> dict[str, object]:
                return {"frames": list(self.frames), "frame_count": len(self.frames), "storage_format": "mkv_vfr"}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "AgentSight" / "runs_host_agent" / "visual-default"
            recorder = SegmentFrameRecorder(
                EvidenceReplayService(root),
                segment_bucket_granularity="hourly",
                max_segment_size_mb=0.00005,
            )
            recorder._max_segment_duration_ms = 10**18
            with mock.patch.object(recorder_module, "MkvSegmentWriter", FakeMkvSegmentWriter):
                with mock.patch("agentsight.storage_quota.apply_storage_quota") as apply_quota:
                    recorder.record_frame(
                        {
                            "captured_at": "2026-06-30T10:00:00+08:00",
                            "width": 4,
                            "height": 4,
                            "_bgra_bytes": bytes([1, 2, 3, 255]) * 16,
                        },
                        source="idle",
                        event_id="first",
                    )
                    apply_quota.reset_mock()
                    recorder._last_quota_check_at = time.monotonic()
                    recorder.record_frame(
                        {
                            "captured_at": "2026-06-30T10:00:01+08:00",
                            "width": 4,
                            "height": 4,
                            "_bgra_bytes": bytes([2, 3, 4, 255]) * 16,
                        },
                        source="idle",
                        event_id="second",
                    )
                    recorder.close()

            self.assertEqual(apply_quota.call_count, 1)

    def test_protocol_gateway_passes_tray_config_max_segment_size_to_recorder(self) -> None:
        observation = P3ALookPngChannel()
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                config_path = local / "AgentSight" / "tray-config.jsonc"
                config_path.parent.mkdir(parents=True, exist_ok=True)
                config_path.write_text(
                    json.dumps({"segment": {"max_segment_size_mb": 9}}),
                    encoding="utf-8",
                )
                gateway = ProtocolGateway(
                    runs_dir=temp_dir,
                    observation_channels=[observation],
                    default_observation_channel_ref=observation.name,
                    input_channels=[input_channel],
                    default_input_channel_ref=input_channel.name,
                )
                config_status = gateway.segment_recorder.status()
                gateway.close()
                override_gateway = ProtocolGateway(
                    runs_dir=temp_dir,
                    observation_channels=[observation],
                    default_observation_channel_ref=observation.name,
                    input_channels=[input_channel],
                    default_input_channel_ref=input_channel.name,
                    max_segment_size_mb=3,
                )
                override_status = override_gateway.segment_recorder.status()
                override_gateway.close()

            self.assertEqual(config_status["max_segment_size_mb"], 9)
            self.assertEqual(config_status["max_segment_size_bytes"], 9 * 1024 * 1024)
            self.assertEqual(override_status["max_segment_size_mb"], 3)

    def test_virtual_screen_region_query_without_coordinate_metadata_reports_decode_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-20260621", width=4, height=4)
            base = Image.new("RGBA", (4, 4), (10, 20, 30, 255))
            changed = base.copy()
            changed.putpixel((3, 3), (220, 80, 40, 255))
            writer.add_frame(
                base.tobytes("raw", "BGRA"),
                captured_at_ms=1_000,
                timestamp_iso="2026-06-21T10:00:00Z",
                source="screen",
                event_id=None,
                cursor_mode="none",
                capture_content_degenerate=False,
                screen_region=None,
                coordinate_system=None,
            )
            writer.add_frame(
                changed.tobytes("raw", "BGRA"),
                captured_at_ms=2_000,
                timestamp_iso="2026-06-21T10:00:01Z",
                source="look",
                event_id=None,
                cursor_mode="none",
                capture_content_degenerate=False,
                screen_region=None,
                coordinate_system=None,
            )
            writer.close()

            report = query_segment_change_index(
                root,
                region={"x": 100, "y": 100, "w": 2, "h": 2},
                region_coordinate_system="virtual_screen_pixels",
                max_pairs=4,
            )

        self.assertEqual(report["change_count"], 0)
        self.assertEqual(report["decode_error_count"], 1)
        self.assertEqual(report["decode_errors"][0]["status"], "decode_skipped_missing_coordinate_metadata")
        self.assertFalse(report["tool_asserts_business_success"])
        self.assertFalse(report["tool_asserts_target_hit"])

    def test_mkv_writer_streams_each_frame_once_without_retaining_raw_frames(self) -> None:
        class FakeStdin:
            def __init__(self) -> None:
                self.payloads: list[bytes] = []
                self.closed = False

            def write(self, payload: bytes) -> int:
                self.payloads.append(bytes(payload))
                return len(payload)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()
                self.returncode: int | None = None
                self.killed = False

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout: int | None = None) -> int:
                self.returncode = 0
                return 0

            def kill(self) -> None:
                self.killed = True
                self.returncode = -9

        with tempfile.TemporaryDirectory() as temp_dir:
            fake = FakeProcess()
            writer = MkvSegmentWriter(Path(temp_dir) / "streamed.mkv", segment_id="streamed", width=2, height=2)
            with mock.patch.object(writer, "_start_ffmpeg", return_value=fake) as start_ffmpeg:
                for index in range(3):
                    writer.add_frame(
                        bytes([index, index, index, 255]) * 4,
                        captured_at_ms=1_000 + index,
                        timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                        source="test",
                        event_id=None,
                        cursor_mode="none",
                        capture_content_degenerate=False,
                        screen_region={"x": 0, "y": 0, "w": 2, "h": 2},
                        coordinate_system="virtual_screen_pixels",
                    )
                writer.close()

        self.assertEqual(start_ffmpeg.call_count, 1)
        self.assertEqual(len(fake.stdin.payloads), 3)
        self.assertEqual([len(payload) for payload in fake.stdin.payloads], [16, 16, 16])
        self.assertTrue(fake.stdin.closed)
        self.assertNotIn("_raw_frames", writer.__dict__)

    def test_mkv_writer_handles_3600_small_frames_without_retaining_raw_payloads(self) -> None:
        class CountingStdin:
            def __init__(self) -> None:
                self.write_count = 0
                self.byte_count = 0
                self.closed = False

            def write(self, payload: bytes) -> int:
                self.write_count += 1
                self.byte_count += len(payload)
                return len(payload)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = CountingStdin()
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout: int | None = None) -> int:
                self.returncode = 0
                return 0

            def kill(self) -> None:
                self.returncode = -9

        with tempfile.TemporaryDirectory() as temp_dir:
            fake = FakeProcess()
            writer = MkvSegmentWriter(Path(temp_dir) / "many.mkv", segment_id="many", width=4, height=4)
            with mock.patch.object(writer, "_start_ffmpeg", return_value=fake):
                for index in range(3600):
                    frame_payload = bytes([index % 256, (index // 256) % 256, 3, 255]) * 16
                    writer.add_frame(
                        frame_payload,
                        captured_at_ms=10_000 + index,
                        timestamp_iso=f"2026-06-21T10:00:{index % 60:02d}Z",
                        source="test",
                        event_id=None,
                        cursor_mode="none",
                        capture_content_degenerate=False,
                        screen_region={"x": 0, "y": 0, "w": 4, "h": 4},
                        coordinate_system="virtual_screen_pixels",
                    )
                writer.close()

        self.assertEqual(fake.stdin.write_count, 3600)
        self.assertEqual(fake.stdin.byte_count, 3600 * 64)
        self.assertEqual(writer.manifest["frame_count"], 3600)
        self.assertNotIn("_raw_frames", writer.__dict__)
        retained_bytes = [value for value in writer.__dict__.values() if isinstance(value, (bytes, bytearray, memoryview))]
        self.assertEqual(retained_bytes, [])

    def test_unfinalized_mkv_segment_is_explicitly_marked_in_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-open.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-open", width=4, height=4)
            try:
                writer.add_frame(
                    Image.new("RGBA", (4, 4), (20, 30, 40, 255)).tobytes("raw", "BGRA"),
                    captured_at_ms=1_000,
                    timestamp_iso="2026-06-21T10:00:00Z",
                    source="idle",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 4, "h": 4},
                    coordinate_system="virtual_screen_pixels",
                )

                index = build_global_segment_frame_index(root)
            finally:
                writer.close()

        self.assertEqual(index["frame_count"], 1)
        self.assertEqual(index["unfinalized_segment_count"], 1)
        self.assertEqual(index["unfinalized_segments"][0]["status"], "unfinalized")
        self.assertFalse(index["frames"][0]["segment_finalized"])

    def test_exact_duplicate_static_sequence_keeps_logical_index_but_writes_one_physical_frame(self) -> None:
        class FakeStdin:
            def __init__(self) -> None:
                self.payloads: list[bytes] = []
                self.closed = False

            def write(self, payload: bytes) -> int:
                self.payloads.append(bytes(payload))
                return len(payload)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                self.closed = True

        class FakeProcess:
            def __init__(self) -> None:
                self.stdin = FakeStdin()
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout: int | None = None) -> int:
                self.returncode = 0
                return 0

            def kill(self) -> None:
                self.returncode = -9

        with tempfile.TemporaryDirectory() as temp_dir:
            fake = FakeProcess()
            writer = MkvSegmentWriter(Path(temp_dir) / "static.mkv", segment_id="static", width=2, height=2)
            payload = bytes([8, 16, 24, 255]) * 4
            with mock.patch.object(writer, "_start_ffmpeg", return_value=fake):
                for index in range(60):
                    writer.add_frame(
                        payload,
                        captured_at_ms=1_000 + index * 40,
                        timestamp_iso=f"2026-06-21T10:00:{index:02d}Z",
                        source="screen",
                        event_id=None,
                        cursor_mode="none",
                        capture_content_degenerate=False,
                        screen_region={"x": 0, "y": 0, "w": 2, "h": 2},
                        coordinate_system="virtual_screen_pixels",
                    )
                writer.close()

            records = [json.loads(line) for line in writer.index_path.read_text(encoding="utf-8").splitlines()]
            manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(len(fake.stdin.payloads), 1)
        self.assertEqual(len(records), 60)
        self.assertEqual(manifest["frame_count"], 60)
        self.assertEqual(manifest["physical_frame_count"], 1)
        self.assertEqual(records[0]["logical_frame_id"], "f000000")
        self.assertEqual(records[0]["physical_frame_id"], "p000000")
        self.assertFalse(records[0]["logical_duplicate"])
        self.assertTrue(all(record["physical_frame_id"] == "p000000" for record in records))
        self.assertTrue(all(record["logical_frame_id"] == record["frame_id"] for record in records))
        self.assertTrue(all(record["logical_duplicate"] for record in records[1:]))
        self.assertTrue(all(record["duplicate_of_frame_id"] == "f000000" for record in records[1:]))

    def test_time_near_returns_logical_duplicate_and_decodes_original_physical_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-20260621", width=3, height=2)
            image = Image.new("RGBA", (3, 2), (11, 22, 33, 255))
            for index in range(3):
                writer.add_frame(
                    image.tobytes("raw", "BGRA"),
                    captured_at_ms=1_000 + index * 1_000,
                    timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                    source="screen",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 3, "h": 2},
                    coordinate_system="virtual_screen_pixels",
                )
            writer.close()

            near = query_segment_decoder_near_time(root, 2.0)
            nearest = near["nearest_frame"]
            restored, report = decode_mkv_frame_to_image(nearest["segment_restore_ref"])

        self.assertEqual(nearest["logical_frame_id"], "f000001")
        self.assertEqual(nearest["frame_id"], "f000001")
        self.assertTrue(nearest["logical_duplicate"])
        self.assertEqual(nearest["duplicate_of_frame_id"], "f000000")
        self.assertEqual(nearest["physical_frame_id"], "p000000")
        self.assertTrue(report["logical_duplicate"])
        self.assertEqual(report["decoded_physical_frame_id"], "p000000")
        self.assertEqual(restored.size, (3, 2))
        self.assertEqual(restored.getpixel((1, 1)), (11, 22, 33, 255))

    def test_single_pixel_cursor_like_change_is_not_sparse_deduplicated_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = MkvSegmentWriter(Path(temp_dir) / "cursor.mkv", segment_id="cursor", width=4, height=4)
            base = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            cursor = base.copy()
            cursor.putpixel((3, 3), (255, 255, 255, 255))
            writer.add_frame(
                base.tobytes("raw", "BGRA"),
                captured_at_ms=1_000,
                timestamp_iso="2026-06-21T10:00:00Z",
                source="screen",
                event_id=None,
                cursor_mode="none",
                capture_content_degenerate=False,
                screen_region={"x": 0, "y": 0, "w": 4, "h": 4},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                cursor.tobytes("raw", "BGRA"),
                captured_at_ms=2_000,
                timestamp_iso="2026-06-21T10:00:01Z",
                source="screen",
                event_id=None,
                cursor_mode="none",
                capture_content_degenerate=False,
                screen_region={"x": 0, "y": 0, "w": 4, "h": 4},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()
            records = [json.loads(line) for line in writer.index_path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([record["logical_duplicate"] for record in records], [False, False])
        self.assertEqual([record["physical_frame_id"] for record in records], ["p000000", "p000001"])

    def test_change_index_reports_exact_duplicate_skipped_pairs_and_threshold_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-duplicates.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-duplicates", width=4, height=4)
            base = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            cursor = base.copy()
            cursor.putpixel((3, 3), (255, 255, 255, 255))
            for index, image in enumerate((base, base, cursor)):
                writer.add_frame(
                    image.tobytes("raw", "BGRA"),
                    captured_at_ms=1_000 + index * 1_000,
                    timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                    source="screen",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 4, "h": 4},
                    coordinate_system="virtual_screen_pixels",
                )
            writer.close()

            report = query_segment_change_index(root, max_pairs=8, min_changed_pixel_ratio=0.5)

        self.assertEqual(report["change_count"], 0)
        self.assertEqual(report["duplicate_interval_count"], 1)
        self.assertEqual(report["duplicate_intervals"][0]["skip_reason"], "exact_duplicate_logical_frame")
        self.assertEqual(report["duplicate_intervals"][0]["changed_pixel_ratio"], 0.0)
        self.assertEqual(report["skipped_pair_count"], 2)
        self.assertEqual([pair["skip_reason"] for pair in report["skipped_pairs"]], ["exact_duplicate_logical_frame", "below_min_changed_pixel_ratio"])
        self.assertEqual(report["skipped_pairs"][1]["changed_pixel_ratio"], 0.0625)
        self.assertEqual(report["skipped_pairs"][1]["threshold"], 0.5)
        self.assertTrue(report["pixel_diff_threshold_enabled"])


    def test_change_index_groups_thresholded_changes_into_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-runs.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-runs", width=4, height=4)
            base = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
            first = base.copy()
            first.putpixel((0, 0), (255, 255, 255, 255))
            second = base.copy()
            second.putpixel((1, 1), (255, 255, 255, 255))
            for index, image in enumerate((base, first, second, second)):
                writer.add_frame(
                    image.tobytes("raw", "BGRA"),
                    captured_at_ms=1_000 + index * 1_000,
                    timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                    source="screen",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 4, "h": 4},
                    coordinate_system="virtual_screen_pixels",
                )
            writer.close()

            report = query_segment_change_index(root, max_pairs=8, min_changed_pixel_ratio=0.05)

        self.assertEqual(report["change_count"], 2)
        self.assertEqual(report["change_run_count"], 1)
        run = report["change_runs"][0]
        self.assertEqual(run["start_time"], "2026-06-21T10:00:01Z")
        self.assertEqual(run["end_time"], "2026-06-21T10:00:02Z")
        self.assertEqual(run["duration_ms"], 1_000)
        self.assertEqual(run["pair_count"], 2)
        self.assertEqual(run["peak_changed_pixel_ratio"], 0.125)
        self.assertEqual(run["changed_bbox"], {"x": 0, "y": 0, "w": 2, "h": 2})
        self.assertFalse(run["tool_asserts_semantic_change"])

    def test_change_index_does_not_mark_aba_duplicate_of_earlier_as_exact_duplicate_interval(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-aba.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-aba", width=2, height=2)
            base = Image.new("RGBA", (2, 2), (0, 0, 0, 255))
            changed = Image.new("RGBA", (2, 2), (255, 255, 255, 255))
            for index, image in enumerate((base, changed, base)):
                writer.add_frame(
                    image.tobytes("raw", "BGRA"),
                    captured_at_ms=1_000 + index * 1_000,
                    timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                    source="screen",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 2, "h": 2},
                    coordinate_system="virtual_screen_pixels",
                )
            writer.close()

            report = query_segment_change_index(root, max_pairs=8, min_changed_pixel_ratio=0.5)

        self.assertEqual(report["duplicate_interval_count"], 0)
        self.assertEqual(report["change_count"], 2)
        self.assertEqual([change["changed_pixel_ratio"] for change in report["changes"]], [1.0, 1.0])
        self.assertTrue(report["changes"][1]["after_frame"]["logical_duplicate"])
        self.assertEqual(report["changes"][1]["after_frame"]["duplicate_of_frame_id"], "f000000")

    def test_change_index_max_pairs_bounds_considered_adjacent_pairs_including_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-max-pairs.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-max-pairs", width=2, height=2)
            image = Image.new("RGBA", (2, 2), (12, 34, 56, 255))
            for index in range(4):
                writer.add_frame(
                    image.tobytes("raw", "BGRA"),
                    captured_at_ms=1_000 + index * 1_000,
                    timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                    source="screen",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 2, "h": 2},
                    coordinate_system="virtual_screen_pixels",
                )
            writer.close()

            report = query_segment_change_index(root, max_pairs=2, min_changed_pixel_ratio=0.5)

        self.assertEqual(report["adjacent_pair_count"], 3)
        self.assertEqual(report["adjacent_pairs_considered"], 2)
        self.assertEqual(report["skipped_pair_count"], 2)
        self.assertEqual(report["duplicate_interval_count"], 2)
        self.assertEqual([pair["skip_reason"] for pair in report["skipped_pairs"]], ["exact_duplicate_logical_frame", "exact_duplicate_logical_frame"])

    def test_change_index_routes_decode_exceptions_to_decode_errors_not_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-decode-error.mkv"
            writer = MkvSegmentWriter(segment_path, segment_id="agentsight-decode-error", width=2, height=2)
            base = Image.new("RGBA", (2, 2), (0, 0, 0, 255))
            changed = Image.new("RGBA", (2, 2), (255, 255, 255, 255))
            for index, image in enumerate((base, changed)):
                writer.add_frame(
                    image.tobytes("raw", "BGRA"),
                    captured_at_ms=1_000 + index * 1_000,
                    timestamp_iso=f"2026-06-21T10:00:0{index}Z",
                    source="screen",
                    event_id=None,
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "w": 2, "h": 2},
                    coordinate_system="virtual_screen_pixels",
                )
            writer.close()

            with mock.patch("agentsight.segments.decoder._decode_segment_frame_to_image", side_effect=RuntimeError("bad mkv payload")):
                report = query_segment_change_index(root, max_pairs=4)

        self.assertEqual(report["adjacent_pair_count"], 1)
        self.assertEqual(report["adjacent_pairs_considered"], 1)
        self.assertEqual(report["change_count"], 0)
        self.assertEqual(report["changes"], [])
        self.assertEqual(report["decode_error_count"], 1)
        self.assertEqual(report["errors"], report["decode_errors"])
        self.assertEqual(report["decode_errors"][0]["status"], "diff_failed")
        self.assertEqual(report["decode_errors"][0]["error_type"], "RuntimeError")
        self.assertEqual(report["query_status"], "generated_with_decode_errors")


if __name__ == "__main__":
    unittest.main()
