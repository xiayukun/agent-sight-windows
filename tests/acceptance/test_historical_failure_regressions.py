from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentsight.adapters.mcp import MCPStdioAdapter
from agentsight.host_agent.server import _host_agent_apply_recording_policy_defaults
from agentsight.protocol.schemas import MAX_POST_OBSERVE_FRAME_COUNT, SchemaError, validate_post_observe, validate_request
from agentsight.tray.state import apply_recording_policy_settings, write_default_tray_config_if_missing
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3AMemoryLookChannel


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for historical regression tests")
class AgentSightHistoricalFailureRegressionsTest(unittest.TestCase):
    def test_recording_policy_post_observe_uses_seconds_to_frames_and_clamps_to_bounded_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings(
                    {
                        "action_capture_enabled": True,
                        "capture_post_action_frames": True,
                        "post_action_fps": 10,
                        "post_action_duration_seconds": 60,
                    }
                )
                request = {"v": "V1", "id": "policy-60s", "basis": {"view_id": "v1"}, "seq": [{"t": "wait", "ms": 1}]}
                applied = _host_agent_apply_recording_policy_defaults(request)

                explicit = _host_agent_apply_recording_policy_defaults(
                    {
                        **request,
                        "id": "explicit-post-observe",
                        "post_observe": {"delay_ms": 0, "frame_count": 2, "interval_ms": 25},
                    }
                )

                apply_recording_policy_settings({"post_action_fps": 999, "post_action_duration_seconds": 999})
                clamped = _host_agent_apply_recording_policy_defaults({**request, "id": "policy-clamped"})

        self.assertEqual(applied["post_observe"]["frame_count"], 600)
        self.assertEqual(applied["post_observe"]["interval_ms"], 100)
        self.assertEqual(applied["post_observe"]["delay_ms"], 0)
        self.assertFalse(applied["post_observe"]["stop_when_stable"])
        self.assertEqual(explicit["post_observe"]["frame_count"], 2)
        self.assertEqual(explicit["post_observe"]["interval_ms"], 25)
        self.assertEqual(clamped["post_observe"]["frame_count"], MAX_POST_OBSERVE_FRAME_COUNT)
        self.assertEqual(clamped["post_observe"]["interval_ms"], 17)

    def test_default_recording_policy_post_observe_is_one_fps_for_ten_seconds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                write_default_tray_config_if_missing()
                applied = _host_agent_apply_recording_policy_defaults(
                    {"v": "V1", "id": "policy-default", "basis": {"view_id": "v1"}, "seq": [{"t": "wait", "ms": 1}]}
                )

        self.assertEqual(applied["post_observe"]["frame_count"], 10)
        self.assertEqual(applied["post_observe"]["interval_ms"], 1000)
        self.assertEqual(applied["post_observe"]["delay_ms"], 0)

    def test_explicit_post_observe_and_scale_down_boundaries_fail_honestly(self) -> None:
        invalid_post_observe_values = [
            {"frame_count": 0},
            {"frame_count": -1},
            {"frame_count": MAX_POST_OBSERVE_FRAME_COUNT + 1},
            {"delay_ms": -1},
            {"interval_ms": -1},
            {"interval_ms": 2001},
        ]
        for value in invalid_post_observe_values:
            with self.subTest(value=value):
                with self.assertRaises(SchemaError):
                    validate_post_observe(value)

        for scale_down in (0, -1, 33):
            with self.subTest(scale_down=scale_down):
                with self.assertRaises(SchemaError):
                    validate_request(
                        {
                            "command": "look",
                            "payload": {
                                "v": "V1",
                                "id": f"bad-scale-{scale_down}",
                                "q": "frame",
                                "src": {"type": "screen", "t": "latest"},
                                "r": {"x": 0, "y": 0, "w": 20, "h": 10},
                                "scale_down": scale_down,
                            },
                        }
                    )

    def test_repeated_actions_get_independent_bounded_sixty_second_observe_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings(
                    {
                        "action_capture_enabled": True,
                        "capture_post_action_frames": True,
                        "post_action_fps": 10,
                        "post_action_duration_seconds": 60,
                    }
                )
                first = _host_agent_apply_recording_policy_defaults(
                    {"v": "V1", "id": "first-action", "basis": {"view_id": "v1"}, "seq": [{"t": "wait", "ms": 1}]}
                )
                second = _host_agent_apply_recording_policy_defaults(
                    {"v": "V1", "id": "second-action", "basis": {"view_id": "v1"}, "seq": [{"t": "wait", "ms": 1}]}
                )

        self.assertIsNot(first, second)
        self.assertEqual(first["post_observe"], second["post_observe"])
        self.assertEqual(first["post_observe"]["frame_count"], 600)
        self.assertLessEqual(first["post_observe"]["frame_count"], MAX_POST_OBSERVE_FRAME_COUNT)
        self.assertEqual(first["post_observe"]["interval_ms"], 100)

    def test_public_screen_look_do_do_not_create_legacy_session_media_directories_by_default(self) -> None:
        observation = P3AMemoryLookChannel()
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[observation],
                default_observation_channel_ref=observation.name,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            screen = adapter.call_tool("screen", {"v": "V1", "id": "screen-no-legacy"})
            look = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-no-legacy",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 10},
                    "scale_down": 1,
                },
            )
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-no-legacy",
                    "basis": {"view_id": look["data"]["view"]["id"]},
                    "seq": [{"t": "wait", "ms": 1}],
                },
            )
            segment_status = adapter.session.gateway.segment_recorder.status()
            adapter.session.gateway.segment_recorder.close()
            root = Path(temp_dir)
            legacy_session_dirs = [path for path in root.rglob("session-*") if path.is_dir()]
            legacy_media_files = [path for path in root.rglob("*") if path.suffix.lower() in {".png", ".bmp", ".gif"}]
            mkv_segments = list(root.rglob("segments/*.mkv"))
            mkv_sidecars = list(root.rglob("segments/*.frames.jsonl")) + list(root.rglob("segments/*.manifest.json"))

        self.assertTrue(screen["ok"], json.dumps(screen, ensure_ascii=False))
        self.assertTrue(look["ok"], json.dumps(look, ensure_ascii=False))
        self.assertTrue(action["ok"], json.dumps(action, ensure_ascii=False))
        self.assertEqual(legacy_session_dirs, [])
        self.assertEqual(legacy_media_files, [])
        self.assertEqual(segment_status["storage_format"], "mkv_vfr")
        self.assertTrue(mkv_segments)
        self.assertTrue(mkv_sidecars)
        self.assertFalse(action["data"]["tool_asserts_business_success"])


if __name__ == "__main__":
    unittest.main()
