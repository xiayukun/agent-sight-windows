from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from ai_control.adapters.mcp import MCPStdioAdapter
from ai_control.evidence.store import EvidenceReplayService
from ai_control.host_agent.server import _host_agent_idle_capture_tick, _host_agent_idle_capture_wait_seconds
from ai_control.segments import BinarySegmentReader, SegmentFrameRecorder, SegmentReader
from ai_control.tray.state import apply_recording_policy_settings, write_default_tray_config_if_missing
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3ALookPngChannel


class PF2IdleCaptureAndRotationTest(unittest.TestCase):
    def test_idle_capture_degenerate_black_frame_is_not_recorded_to_segment(self) -> None:
        observation = _BlackFrameObservationChannel()
        input_channel = P3AInputChannel()
        policy = {
            "continuous_recording_enabled": True,
            "recording": {"idle_capture": {"enabled": True, "fps": 1.0}},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[observation],
                default_observation_channel_ref=observation.name,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            report = adapter.session.gateway.capture_idle_frame(policy=policy, now_ms=1000)
            manifest = adapter.session.gateway.segment_recorder.manifest()
            adapter.session.gateway.segment_recorder.close()

        self.assertTrue(report["captured"])
        self.assertEqual(report["segment_frame"]["status"], "not_recorded")
        self.assertEqual(report["segment_frame"]["not_recorded_reason"], "capture_content_degenerate")
        self.assertEqual(manifest["frame_count"], 0)

    def test_host_agent_idle_tick_skips_when_readiness_blocked(self) -> None:
        class FakeGateway:
            def __init__(self) -> None:
                self.calls = 0

            def capture_idle_frame(self, *, policy: dict[str, Any], now_ms: int | None = None) -> dict[str, Any]:
                self.calls += 1
                return {"captured": True}

        class FakeAdapter:
            def __init__(self) -> None:
                self.session = type("Session", (), {"gateway": FakeGateway()})()

        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            import os
            from unittest import mock

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings({"continuous_recording_enabled": True, "idle_fps": 1.0})
                adapter = FakeAdapter()
                with mock.patch(
                    "ai_control.host_agent.server.build_host_agent_health_report",
                    return_value={
                        "service_status": "session_disconnected",
                        "can_attempt_real_control": False,
                        "control_blockers": ["screen_capture_unavailable"],
                    },
                ):
                    report = _host_agent_idle_capture_tick(adapter=adapter, now_ms=1234)

        self.assertFalse(report["captured"])
        self.assertEqual(report["skip_reason"], "readiness_blocked")
        self.assertEqual(report["readiness"]["code"], "NOT_IN_ACTIVE_SESSION")
        self.assertEqual(adapter.session.gateway.calls, 0)

    def test_idle_capture_tick_respects_policy_fps_and_records_segment_frame(self) -> None:
        observation = P3ALookPngChannel()
        input_channel = P3AInputChannel()
        policy = {
            "continuous_recording_enabled": True,
            "recording": {"idle_capture": {"enabled": True, "fps": 1.0}},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            import os
            from unittest import mock

            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings({"segment_image_lossless": True})
                adapter = MCPStdioAdapter(
                    runs_dir=temp_dir,
                    observation_channels=[observation],
                    default_observation_channel_ref=observation.name,
                    input_channels=[input_channel],
                    default_input_channel_ref=input_channel.name,
                )
            first = adapter.session.gateway.capture_idle_frame(policy=policy, now_ms=1000)
            skipped = adapter.session.gateway.capture_idle_frame(policy=policy, now_ms=1500)
            second = adapter.session.gateway.capture_idle_frame(policy=policy, now_ms=2000)
            manifest = adapter.session.gateway.segment_recorder.manifest()
            status = adapter.session.gateway.segment_recorder.status()

            self.assertTrue(first["captured"])
            self.assertEqual(first["segment_frame"]["source"], "idle")
            self.assertEqual(first["segment_frame"]["storage_format"], "binary_agseg")
            self.assertTrue(Path(status["segment_path_abs"]).exists())
            self.assertEqual(Path(status["segment_path_abs"]).suffix, ".agseg")
            self.assertFalse(skipped["captured"])
            self.assertEqual(skipped["skip_reason"], "idle_interval_not_elapsed")
            self.assertTrue(second["captured"])
            self.assertEqual(len(observation.captures), 2)
            self.assertEqual(manifest["frame_count"], 2)
            restored, report = BinarySegmentReader(status["segment_path_abs"]).restore_frame(second["segment_frame"]["frame_id"])
            adapter.session.gateway.segment_recorder.close()
            self.assertEqual(restored.size, (160, 100))
            self.assertTrue(report["hash_ok"], report)
            self.assertFalse(first["boundary"]["business_success_judged"])
            self.assertTrue(adapter.session.gateway.observations)
            self.assertFalse(
                any(_contains_key(entry, "_media_bytes") for entry in adapter.session.gateway.observations.values()),
                "runtime observations must not retain raw image bytes after Segment recording",
            )
            self.assertLessEqual(len(adapter.session.gateway.observations), 96)

            disabled = adapter.session.gateway.capture_idle_frame(
                policy={"continuous_recording_enabled": False, "recording": {"idle_capture": {"enabled": False, "fps": 1.0}}},
                now_ms=3000,
            )
            self.assertFalse(disabled["captured"])
            self.assertEqual(disabled["skip_reason"], "idle_capture_disabled")
            self.assertEqual(disabled["runtime_memory_compaction"]["reason"], "idle_capture_disabled")

    def test_segment_recorder_rotates_on_daily_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = EvidenceReplayService(temp_dir, session_id="session-rotation")
            recorder = SegmentFrameRecorder(evidence, daily_segment_boundary_local_time="00:00")
            first_frame = _fake_media_frame(evidence, "first.png", captured_at=_epoch("2026-06-21T23:59:59+08:00"))
            second_frame = _fake_media_frame(evidence, "second.png", captured_at=_epoch("2026-06-22T00:00:01+08:00"))
            first = recorder.record_frame(first_frame, source="idle", event_id="idle-before")
            second = recorder.record_frame(second_frame, source="idle", event_id="idle-after")

            self.assertNotEqual(first["segment_id"], second["segment_id"])
            self.assertIn("20260621", first["segment_id"])
            self.assertIn("20260622", second["segment_id"])
            self.assertEqual(first["source"], "idle")
            self.assertEqual(second["source"], "idle")
            self.assertEqual(first["storage_format"], "binary_agseg")
            self.assertTrue(Path(first["segment_path_abs"]).exists())
            self.assertTrue(Path(second["segment_path_abs"]).exists())
            recorder.close()

    def test_segment_recorder_can_keep_legacy_directory_storage_for_migration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            evidence = EvidenceReplayService(temp_dir, session_id="session-legacy")
            recorder = SegmentFrameRecorder(evidence, storage_format="proto_directory")
            frame = _fake_media_frame(evidence, "legacy.png", captured_at=_epoch("2026-06-21T10:00:00+08:00"))
            recorded = recorder.record_frame(frame, source="idle", event_id="idle-legacy")

            self.assertEqual(recorded["storage_format"], "proto_directory")
            self.assertTrue(Path(recorded["manifest_path_abs"]).exists())
            restored, report = SegmentReader(Path(recorded["segment_path_abs"])).restore_frame(recorded["frame_id"])
            self.assertEqual(restored.size, (8, 8))
            self.assertTrue(report["hash_ok"], report)
            recorder.close()

    def test_host_agent_idle_tick_reads_tray_policy_without_host_input(self) -> None:
        class FakeGateway:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def capture_idle_frame(self, *, policy: dict[str, Any], now_ms: int | None = None) -> dict[str, Any]:
                self.calls.append({"policy": policy, "now_ms": now_ms})
                return {
                    "object_type": "AgentSightIdleCaptureTickReport",
                    "schema": "agentsight_idle_capture_tick_v1",
                    "captured": bool(policy["recording"]["idle_capture"]["enabled"]),
                    "host_input_sent": False,
                    "host_sent_event_count": 0,
                    "boundary": {
                        "ocr_used": False,
                        "clipboard_used": False,
                        "accessibility_tree_used": False,
                        "dom_used": False,
                        "window_semantics_used": False,
                        "business_success_judged": False,
                    },
                }

        class FakeAdapter:
            def __init__(self) -> None:
                self.session = type("Session", (), {"gateway": FakeGateway()})()

        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            import os
            from unittest import mock

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings({"continuous_recording_enabled": True, "idle_fps": 0.5})
                adapter = FakeAdapter()
                with mock.patch(
                    "ai_control.host_agent.server.build_host_agent_health_report",
                    return_value={
                        "service_status": "ok_active_default_desktop",
                        "can_attempt_real_control": True,
                        "control_blockers": [],
                    },
                ):
                    report = _host_agent_idle_capture_tick(adapter=adapter, now_ms=1234)

        self.assertTrue(report["captured"])
        self.assertEqual(adapter.session.gateway.calls[0]["policy"]["recording"]["idle_capture"]["fps"], 0.5)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_idle_capture_wait_seconds_tracks_configured_fps(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            import os
            from unittest import mock

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings({"continuous_recording_enabled": True, "idle_fps": 10.0})
                self.assertAlmostEqual(_host_agent_idle_capture_wait_seconds(default_interval_seconds=0.5), 0.1, places=3)

                apply_recording_policy_settings({"continuous_recording_enabled": True, "idle_fps": 0.1})
                self.assertEqual(_host_agent_idle_capture_wait_seconds(default_interval_seconds=0.5), 0.5)

    def test_gateway_segment_recorder_reads_tray_segment_bucket_granularity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            import os
            from unittest import mock

            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings({"segment_bucket_granularity": "hourly"})
                adapter = MCPStdioAdapter(runs_dir=temp_dir)
                status = adapter.session.gateway.segment_recorder.status()
                adapter.session.gateway.segment_recorder.close()

        self.assertEqual(status["storage_format"], "binary_agseg")
        self.assertEqual(status["segment_bucket_granularity"], "hourly")


def _fake_media_frame(evidence: EvidenceReplayService, name: str, *, captured_at: float) -> dict[str, Any]:
    from PIL import Image
    from io import BytesIO

    buf = BytesIO()
    Image.new("RGBA", (8, 8), (20, 80, 140, 255)).save(buf, format="PNG")
    media = evidence.write_media_bytes(name, buf.getvalue())
    return {
        "observation_id": name.removesuffix(".png"),
        "captured_at": captured_at,
        "timestamp": captured_at,
        "captured_at_monotonic_ms": int(captured_at * 1000),
        "media_mime": "image/png",
        "width": 8,
        "height": 8,
        **media,
    }


class _BlackFrameObservationChannel(P3ALookPngChannel):
    name = "black_frame_screen"

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        from PIL import Image
        from io import BytesIO

        region = payload.get("region") or {"x": 0, "y": 0, "width": 160, "height": 100}
        self.captures.append(dict(region))
        output = BytesIO()
        Image.new("RGB", (int(region["width"]), int(region["height"])), (0, 0, 0)).save(output, format="PNG")
        media = evidence.media_bytes_record(output.getvalue())
        return {
            "object_type": "ObservationFrame",
            "observation_id": f"obs-black-{len(self.captures):03d}",
            "mode": payload.get("mode", "fullscreen"),
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "implementation": "black_frame_screen",
            "source_kind": "software_screen_capture",
            "real_capture": True,
            "media_mime": "image/png",
            "media_format": "png",
            "width": int(region["width"]),
            "height": int(region["height"]),
            "screen_region": region,
            "coordinate_system": "virtual_screen_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            **media,
        }


def _epoch(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()


def _contains_key(value: Any, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(_contains_key(child, key) for child in value.values())
    if isinstance(value, list):
        return any(_contains_key(child, key) for child in value)
    return False


if __name__ == "__main__":
    unittest.main()
