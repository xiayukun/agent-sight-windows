from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest import mock

from ai_control.adapters.mcp import MCPStdioAdapter, MCP_TOOL_NAMES
from ai_control.adapters.mcp.server import tool_schema
from ai_control.channels.windows_software.input import WindowsSoftwareInputChannel
from ai_control.evidence.store import EvidenceReplayService
from ai_control.host_agent.server import (
    _host_agent_apply_recording_policy_defaults,
    _host_agent_protocol_do,
    _host_agent_protocol_look,
    _host_agent_visual_observe,
    _host_historical_decode_region,
    _host_agent_public_blocked_response,
    _host_agent_public_readiness,
    write_discovery_file,
)
from ai_control.protocol.schemas import SchemaError, validate_request
from ai_control.segments import BinarySegmentWriter
from ai_control.tray.state import apply_recording_policy_settings, write_default_tray_config_if_missing


PIL_AVAILABLE = importlib.util.find_spec("PIL") is not None


class P3ALookPngChannel:
    name = "p3a_png_screen"
    channel_type = "observation"

    def __init__(self) -> None:
        self.captures: list[dict[str, Any]] = []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available",
            "implementation": "p3a_png_screen",
            "source_kind": "software_screen_capture",
            "modes": ["fullscreen", "region", "after_action", "sequence"],
            "supports_sequence": True,
            "media_mime_types": ["image/png"],
        }

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        region = payload.get("region") or {"x": -20, "y": 30, "width": 160, "height": 100}
        self.captures.append(dict(region))
        width = int(region["width"])
        height = int(region["height"])
        observation_id = f"obs-p3a-{len(self.captures):03d}"
        media = evidence.write_media_bytes(f"{observation_id}.png", _png_bytes(width, height, len(self.captures)))
        return {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": payload.get("mode", "fullscreen"),
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "implementation": "p3a_png_screen",
            "source_kind": "software_screen_capture",
            "real_capture": True,
            "media_mime": "image/png",
            "media_format": "png",
            "width": width,
            "height": height,
            "screen_region": region,
            "coordinate_system": "virtual_screen_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            **media,
        }


class P3AStablePostObserveLookPngChannel(P3ALookPngChannel):
    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        region = payload.get("region") or {"x": -20, "y": 30, "width": 160, "height": 100}
        self.captures.append(dict(region))
        width = int(region["width"])
        height = int(region["height"])
        observation_id = f"obs-p3a-stable-{len(self.captures):03d}"
        color_index = 1 if len(self.captures) == 1 else 9
        media = evidence.write_media_bytes(f"{observation_id}.png", _png_bytes(width, height, color_index))
        return {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": payload.get("mode", "fullscreen"),
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "implementation": "p3a_stable_post_observe_screen",
            "source_kind": "software_screen_capture",
            "real_capture": True,
            "media_mime": "image/png",
            "media_format": "png",
            "width": width,
            "height": height,
            "screen_region": region,
            "coordinate_system": "virtual_screen_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            **media,
        }


class P3AMemoryLookChannel(P3ALookPngChannel):
    name = "p3a_memory_screen"

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        region = payload.get("region") or {"x": -20, "y": 30, "width": 160, "height": 100}
        self.captures.append(dict(region))
        width = int(region["width"])
        height = int(region["height"])
        observation_id = f"obs-p3a-memory-{len(self.captures):03d}"
        media = evidence.media_bytes_record(_png_bytes(width, height, len(self.captures)))
        return {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": payload.get("mode", "fullscreen"),
            "timestamp": time.time(),
            "captured_at": time.time(),
            "channel_ref": self.name,
            "implementation": "p3a_memory_screen",
            "source_kind": "software_screen_capture",
            "real_capture": True,
            "media_mime": "image/png",
            "media_format": "png",
            "width": width,
            "height": height,
            "screen_region": region,
            "coordinate_system": "virtual_screen_pixels",
            "capture_status": "captured",
            "media_integrity_checked": True,
            "default_media_file_written": False,
            "canonical_storage_target": ".agseg",
            **media,
        }


class P3AInputChannel:
    name = "p3a_input"
    channel_type = "input"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available",
            "implementation": "p3a_input_spy",
            "source_kind": "test_only",
            "execution_mode": "dry_run",
            "real_input": False,
            "input_types": [
                "mouse_move",
                "mouse_click",
                "mouse_double_click",
                "mouse_button_down",
                "mouse_button_up",
                "mouse_scroll",
                "key_text_stream",
                "key_press",
                "key_chord",
                "key_down",
                "key_up",
            ],
            "input_executed": True,
        }

    def execute(self, input_event: dict[str, Any]) -> dict[str, Any]:
        self.events.append(input_event)
        return {
            "channel_ref": self.name,
            "input_channel_ref": self.name,
            "implementation": "p3a_input_spy",
            "result": "recorded_without_host_input",
            "input_executed": True,
            "host_input_executed": False,
            "host_input_sent": False,
            "sent_event_count": 0,
            "host_sent_event_count": 0,
            "stopped_input": False,
            "released_inputs": True,
            "release_result": "not_required",
        }


class WindowsSoftwareInputChannelPlatformProbeTest(unittest.TestCase):
    def test_describe_avoids_wmi_backed_platform_system_probe(self) -> None:
        channel = WindowsSoftwareInputChannel(enabled=True)
        with mock.patch("platform.system", side_effect=AssertionError("platform.system must not be called")):
            with mock.patch("ai_control.channels.windows_software.input._is_windows", return_value=True):
                description = channel.describe()

        self.assertEqual(description["status"], "available")
        self.assertTrue(description["platform_supported"])
        self.assertTrue(description["host_input_possible"])
        self.assertFalse(description["input_executed"])


class P3AHostFakeAdapter:
    def __init__(self, observe_dir: Path | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.observe_dir = observe_dir
        self.observe_count = 0

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = arguments or {}
        self.calls.append((name, payload))
        if name == "observe" and self.observe_dir:
            self.observe_count += 1
            media_path = self.observe_dir / f"host-post-observe-{self.observe_count}.png"
            region = payload.get("region") or {"x": 0, "y": 0, "width": 20, "height": 16}
            media_path.write_bytes(_png_bytes(int(region["width"]), int(region["height"]), self.observe_count + 10))
            return {
                "ok": True,
                "data": {
                    "object_type": "ObservationFrame",
                    "observation_id": f"obs-host-post-{self.observe_count}",
                    "captured_at": time.time(),
                    "media_mime": "image/png",
                    "media_path_abs": str(media_path),
                    "screen_region": region,
                    "coordinate_system": "virtual_screen_pixels",
                },
            }
        if name == "create_lease":
            return {"ok": True, "data": {"lease_id": "lease-host", "max_input_events": 5}}
        if name == "execute_input":
            return {
                "ok": True,
                "data": {
                    "object_type": "InputEvent",
                    "input_event_id": f"input-{len(self.calls)}",
                    "input_type": payload.get("input_type"),
                    "host_input_sent": False,
                    "host_input_executed": False,
                    "host_sent_event_count": 0,
                    "sent_event_count": 0,
                },
                "after_observation": {
                    "observation_id": f"obs-after-{len(self.calls)}",
                    "screen_region": {"x": -10, "y": 20, "width": 100, "height": 80},
                    "coordinate_system": "virtual_screen_pixels",
                },
            }
        if name == "get_evidence_package":
            return {"ok": True, "data": {"object_type": "EvidencePackage"}}
        if name == "read_replay":
            return {"ok": True, "data": {"object_type": "ReplayIndex", "read_only": True}}
        if name == "verify_integrity":
            return {"ok": True, "data": {"object_type": "IntegrityManifest", "ok": True}}
        return {"ok": False, "failure": {"failure_code": "unexpected_tool"}}


class P3AHostSegmentFakeAdapter(P3AHostFakeAdapter):
    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        result = super().call_tool(name, arguments)
        if name == "get_capabilities":
            return {"ok": True, "data": {"object_type": "Capabilities"}}
        if name == "observe" and result.get("ok"):
            result["data"]["segment_frame"] = {
                "schema": "agentsight_segment_frame_ref_v1",
                "segment_id": "seg-host",
                "frame_id": "f000001",
                "source": "look",
                "raw_or_derived": "raw",
                "restore_ref": {
                    "segment_path": str((self.observe_dir or Path(".")).parent / "segments" / "segment-seg-host"),
                    "frame_id": "f000001",
                },
            }
        return result


def _png_bytes(width: int, height: int, index: int) -> bytes:
    from PIL import Image

    image = Image.new("RGBA", (width, height), (20 * index % 255, 30, 60, 255))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(PIL_AVAILABLE, "Pillow is required for P3-A look/do protocol tests")
class P3AScreenLookDoProtocolTest(unittest.TestCase):
    def assert_public_ready(self, data: dict[str, Any]) -> None:
        self.assertEqual(data["code"], "READY")
        self.assertEqual(data["readiness"]["schema"], "ai_control_public_readiness_v1")
        self.assertTrue(data["readiness"]["ok"])
        self.assertEqual(data["readiness"]["code"], "READY")
        self.assertEqual(data["control_blockers"], [])

    def test_public_mcp_surface_is_screen_look_do_only(self) -> None:
        self.assertEqual(MCP_TOOL_NAMES, ("screen", "look", "do"))
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(runs_dir=temp_dir)
            tools = adapter.list_tools()["tools"]

        self.assertEqual([tool["name"] for tool in tools], ["screen", "look", "do"])
        for tool in tools:
            self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertEqual(tool_schema("look")["required"], ["q", "src", "r", "scale_down"])
        self.assertEqual(tool_schema("do")["required"], ["basis", "seq"])
        self.assertNotIn("physical_current_screen", tool_schema("do")["properties"]["basis"]["properties"])
        self.assertNotIn("view_path", tool_schema("do")["properties"]["basis"]["properties"])
        self.assertEqual(tool_schema("do")["properties"]["basis"]["required"], ["view_id"])
        self.assertNotIn("observe", MCP_TOOL_NAMES)
        self.assertNotIn("query_visual_memory", MCP_TOOL_NAMES)
        self.assertNotIn("run_limited_batch", MCP_TOOL_NAMES)

    def test_gateway_usage_guide_recommends_public_look_instead_of_legacy_visual_memory_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(runs_dir=temp_dir)
            guide = adapter.session.gateway.handle({"command": "get_capabilities", "payload": {}})["data"]["ai_usage_guide"]

        recommended = json.dumps(guide["recommended_ai_flow"], sort_keys=True)
        ordinary_steps = json.dumps(guide["visual_memory_workflow"]["ordinary_ai_steps"], sort_keys=True)
        legacy_steps = json.dumps(guide["visual_memory_workflow"]["legacy_internal_steps"], sort_keys=True)
        self.assertIn("look", guide["workflow"])
        self.assertIn("look time.near", ordinary_steps)
        self.assertNotIn("query_visual_memory", recommended)
        self.assertNotIn("query_visual_memory", ordinary_steps)
        self.assertNotIn("query_visual_memory", legacy_steps)
        self.assertEqual(guide["visual_memory_workflow"]["ordinary_public_facade"], "look")
        self.assertEqual(guide["visual_memory_workflow"]["legacy_internal_query_tool"], "query_visual_memory")

    def test_public_do_rejects_legacy_view_path_basis(self) -> None:
        with self.assertRaises(SchemaError):
            validate_request(
                {
                    "command": "do",
                    "payload": {
                        "v": "V1",
                        "id": "legacy-view-path",
                        "basis": {"view_path": "legacy.png"},
                        "seq": [{"t": "wait", "ms": 1}],
                    },
                }
            )

    def test_public_screen_look_do_embed_readiness_without_health_tool(self) -> None:
        observation = P3ALookPngChannel()
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[observation],
                default_observation_channel_ref=observation.name,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            screen = adapter.call_tool("screen", {"v": "V1", "id": "screen-ready"})
            look = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-ready",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 10},
                    "scale_down": 1,
                },
            )
            do = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-ready",
                    "basis": {"view_id": look["data"]["view"]["id"]},
                    "seq": [{"t": "wait", "ms": 1}],
                },
            )
            screen_frame_index = screen["data"]["screen_frame_index"]
            screen_frame_media_exists = Path(screen_frame_index["frame_buffer"]["raw_media_path_abs"]).exists()

        self.assert_public_ready(screen["data"])
        self.assert_public_ready(look["data"])
        self.assert_public_ready(do["data"])
        self.assertEqual(screen_frame_index["status"], "indexed")
        self.assertTrue(screen_frame_index["indexed"])
        self.assertEqual(screen_frame_index["source"], "screen")
        self.assertEqual(screen_frame_index["frame_buffer"]["source"], "screen")
        self.assertEqual(screen_frame_index["frame_buffer"]["event_id"], "screen-ready")
        self.assertEqual(screen_frame_index["frame_buffer"]["raw_or_derived"], "raw")
        self.assertEqual(screen_frame_index["frame_buffer"]["cursor_mode"], "none")
        self.assertFalse(screen_frame_index["tool_asserts_business_success"])
        self.assertTrue(screen_frame_media_exists)
        self.assertNotIn("health", MCP_TOOL_NAMES)

    def test_mcp_adapter_look_returns_transient_image_content_outside_json_data(self) -> None:
        observation = P3AMemoryLookChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[observation],
                default_observation_channel_ref=observation.name,
            )
            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-content-direct",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 10},
                    "scale_down": 1,
                },
            )
            session_dirs = list(Path(temp_dir).glob("session-*"))
            media_files = list(Path(temp_dir).glob("session-*/media/*"))

        self.assertTrue(result["ok"])
        self.assertIsNone(result["evidence_ref"])
        self.assertEqual(result["content"][0]["type"], "image")
        self.assertEqual(result["content"][0]["mimeType"], "image/png")
        self.assertTrue(result["content"][0]["data"])
        self.assertNotIn("content", result["data"])
        self.assertTrue(result["data"]["image_content_returned"])
        self.assertFalse(result["data"]["derived_review_file_written"])
        self.assertEqual(session_dirs, [])
        self.assertEqual(media_files, [])
        self.assertFalse(result["data"]["tool_asserts_business_success"])

    def test_host_visual_observe_and_look_expose_segment_frame_refs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observe_dir = Path(temp_dir) / "observe"
            observe_dir.mkdir()
            fake_adapter = P3AHostSegmentFakeAdapter(observe_dir=observe_dir)
            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter", return_value=fake_adapter):
                status, observe = _host_agent_visual_observe(
                    visual_sessions={},
                    runs_dir=temp_dir,
                    request={
                        "mode": "region",
                        "region": {"x": 0, "y": 0, "width": 24, "height": 18},
                    },
                )
                look_status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views={},
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-segment",
                        "q": "frame",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": 0, "y": 0, "w": 24, "h": 18},
                        "scale_down": 1,
                    },
                )

        self.assertEqual(status, 200)
        self.assertEqual(observe["segment_frame"]["segment_id"], "seg-host")
        self.assertEqual(observe["segment_frame"]["restore_ref"]["frame_id"], "f000001")
        self.assertEqual(look_status, 200)
        self.assertEqual(look["content"][0]["type"], "image")
        self.assertEqual(look["content"][0]["mimeType"], "image/png")
        self.assertTrue(look["content"][0]["data"])
        self.assertNotIn("path", look["view"])
        self.assertTrue(look["image_content_returned"])
        self.assertFalse(look["derived_review_file_written"])
        self.assertEqual(look["segment_frame"]["segment_id"], "seg-host")
        self.assertEqual(look["segment_frame"]["raw_or_derived"], "raw")
        self.assertEqual(look["view_record"]["segment_restore_ref"]["frame_id"], "f000001")
        self.assertEqual(look["view_record"]["transform"]["view_pixels_to_virtual_screen_pixels"]["scale_x"], 1)
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_look_view_record_separates_screen_region_from_decoded_region(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observe_dir = Path(temp_dir) / "observe"
            observe_dir.mkdir()
            fake_adapter = P3AHostSegmentFakeAdapter(observe_dir=observe_dir)
            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter", return_value=fake_adapter):
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views={},
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-region-offset",
                        "q": "frame",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": -10, "y": 20, "w": 24, "h": 18},
                        "scale_down": 2,
                    },
                )

        self.assertEqual(status, 200)
        self.assertEqual(look["view_record"]["requested_screen_region"], {"x": -10, "y": 20, "w": 24, "h": 18})
        self.assertEqual(look["view_record"]["actual_decoded_region"], {"x": 0, "y": 0, "w": 24, "h": 18})
        self.assertEqual(look["view_record"]["transform"]["view_pixels_to_virtual_screen_pixels"]["origin_x"], -10)
        self.assertEqual(look["view_record"]["transform"]["view_pixels_to_virtual_screen_pixels"]["origin_y"], 20)
        self.assertTrue(look["image_content_returned"])
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_default_visual_session_reuses_adapter_for_segment_append(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observe_dir = Path(temp_dir) / "observe"
            observe_dir.mkdir()
            visual_sessions: dict[str, dict[str, Any]] = {}
            protocol_views: dict[str, dict[str, Any]] = {}
            fake_adapter = P3AHostSegmentFakeAdapter(observe_dir=observe_dir)
            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter", return_value=fake_adapter) as build:
                first_status, first = _host_agent_protocol_look(
                    visual_sessions=visual_sessions,
                    protocol_views=protocol_views,
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-segment-first",
                        "q": "frame",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": 0, "y": 0, "w": 24, "h": 18},
                        "scale_down": 1,
                    },
                )
                second_status, second = _host_agent_protocol_look(
                    visual_sessions=visual_sessions,
                    protocol_views=protocol_views,
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-segment-second",
                        "q": "frame",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": 0, "y": 0, "w": 24, "h": 18},
                        "scale_down": 1,
                    },
                )

        self.assertEqual(first_status, 200)
        self.assertEqual(second_status, 200)
        self.assertEqual(protocol_views[first["view"]["id"]]["visual_session_id"], "visual-default")
        self.assertEqual(protocol_views[second["view"]["id"]]["visual_session_id"], "visual-default")
        self.assertEqual(list(visual_sessions.keys()), ["visual-default"])
        self.assertEqual(fake_adapter.observe_count, 2)
        self.assertEqual(build.call_count, 1)

    def test_host_look_time_near_decodes_agseg_region_without_capture(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00+08:00",
                timestamp_monotonic_ms=1000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            protocol_views: dict[str, dict[str, Any]] = {}
            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter") as build:
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views=protocol_views,
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-time-near-decode",
                        "q": "frame",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": 110, "y": 55, "w": 20, "h": 10},
                        "scale_down": 2,
                        "time": {"near": "2026-06-21T10:00:00+08:00"},
                    },
                )

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertEqual(look["type"], "time_near_frames")
        self.assertTrue(look["no_capture_performed"])
        self.assertTrue(look["decoded_review_returned"])
        self.assertEqual(look["r"]["unit"], "virtual_screen_px")
        self.assertFalse(look["view_is_current_action_basis"])
        self.assertEqual(look["decoded_review"]["raw_or_derived"], "derived_review_only")
        self.assertEqual(look["decoded_review"]["scale_down"], 2)
        self.assertEqual(look["decoded_review"]["decode_region_basis"]["region"], {"x": 10, "y": 5, "w": 20, "h": 10})
        self.assertEqual(look["decoded_review"]["requested_screen_region"], {"x": 110, "y": 55, "w": 20, "h": 10})
        self.assertTrue(look["image_content_returned"])
        self.assertEqual(look["content"][0]["type"], "image")
        self.assertNotIn("review_media_path_abs", look["decoded_review"])
        self.assertFalse(look["historical_view"]["view_is_current_action_basis"])
        self.assertEqual(look["view_record"]["view_id"], look["historical_view"]["id"])
        self.assertEqual(look["view_record"]["view_role"], "historical_segment_review")
        self.assertFalse(look["view_record"]["view_is_current_action_basis"])
        self.assertEqual(look["view_record"]["segment_restore_ref"]["frame_id"], "f000000")
        self.assertEqual(look["view_record"]["requested_screen_region"], {"x": 110, "y": 55, "w": 20, "h": 10})
        self.assertEqual(look["view_record"]["actual_decoded_region"], {"x": 10, "y": 5, "w": 20, "h": 10})
        self.assertEqual(look["view_record"]["raw_or_derived"], "derived_review_only")
        self.assertFalse(look["view_record"]["derived_review_file_written"])
        self.assertIn(look["historical_view"]["id"], protocol_views)
        self.assertFalse(protocol_views[look["historical_view"]["id"]]["view_is_current_action_basis"])
        do_status, do_report = _host_agent_protocol_do(
            visual_sessions={},
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-historical-time-near",
                "basis": {"view_id": look["historical_view"]["id"], "point": {"x": 1, "y": 1}},
                "seq": [{"t": "click", "b": "left"}],
            },
        )
        self.assertEqual(do_status, 409)
        self.assertEqual(do_report["failed_step"]["failure_code"], "VIEW_NOT_ACTION_BASIS")
        self.assertEqual(look["frames_near_time"]["frame_count"], 1)
        self.assertEqual(look["frames_near_time"]["nearest_frame"]["segment_frame_id"], "f000000")
        self.assertFalse(look["tool_asserts_business_success"])
        self.assertFalse(look["boundary"]["business_success_judged"])
        build.assert_not_called()

    def test_mcp_look_time_near_returns_historical_view_record_for_on_demand_preview(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00+08:00",
                timestamp_monotonic_ms=1000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()
            adapter = MCPStdioAdapter(runs_dir=temp_dir)
            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-time-near-view-record",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 110, "y": 55, "w": 20, "h": 10},
                    "scale_down": 2,
                    "time": {"near": "2026-06-21T10:00:00+08:00"},
                },
            )
            historical_view_id = result["data"]["historical_view"]["id"]
            do_result = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "mcp-do-historical-time-near",
                    "basis": {"view_id": historical_view_id, "point": {"x": 1, "y": 1}},
                    "seq": [{"t": "click", "b": "left"}],
                },
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["content"][0]["type"], "image")
        self.assertNotIn("content", result["data"])
        self.assertEqual(result["data"]["view_record"]["view_id"], result["data"]["historical_view"]["id"])
        self.assertEqual(result["data"]["view_record"]["view_role"], "historical_segment_review")
        self.assertFalse(result["data"]["view_record"]["view_is_current_action_basis"])
        self.assertEqual(result["data"]["view_record"]["segment_restore_ref"]["frame_id"], "f000000")
        self.assertEqual(result["data"]["view_record"]["actual_decoded_region"], {"x": 10, "y": 5, "w": 20, "h": 10})
        self.assertFalse(result["data"]["view_record"]["derived_review_file_written"])
        self.assertFalse(result["data"]["tool_asserts_business_success"])

    def test_host_look_changes_returns_segment_metadata_without_capture(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            for x in range(8, 12):
                for y in range(6, 9):
                    changed.putpixel((x, y), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 0, "y": 0, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 0, "y": 0, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter") as build:
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views={},
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-changes",
                        "q": "changes",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": 0, "y": 0, "w": 40, "h": 24},
                        "scale_down": 1,
                        "min_changed_pixel_ratio": 0.001,
                    },
                )

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertEqual(look["type"], "changes")
        self.assertTrue(look["no_capture_performed"])
        self.assertTrue(look["no_media_exported"])
        self.assertEqual(look["r"]["unit"], "virtual_screen_px")
        self.assertFalse(look["derived_review_artifact_returned"])
        self.assertEqual(look["changes"]["schema"], "agentsight_segment_change_index_v1")
        self.assertEqual(look["changes"]["change_count"], 1)
        self.assertEqual(look["changes"]["region_coordinate_system"], "virtual_screen_pixels")
        self.assertEqual(look["changes"]["changes"][0]["changed_bbox"], {"x": 8, "y": 6, "w": 4, "h": 3})
        self.assertFalse(look["tool_asserts_business_success"])
        self.assertFalse(look["tool_asserts_causality"])
        self.assertFalse(look["boundary"]["business_success_judged"])
        build.assert_not_called()

    def test_host_look_changes_maps_parent_view_region_to_virtual_screen(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            for x in range(10, 14):
                for y in range(7, 9):
                    changed.putpixel((x, y), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()
            protocol_views = {
                "v_parent": {
                    "view": {"id": "v_parent", "path": "parent.png", "w": 20, "h": 12, "scale_down": 2},
                    "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                    "transform": {
                        "view_pixels_to_virtual_screen_pixels": {
                            "origin_x": 100,
                            "origin_y": 50,
                            "scale_x": 2,
                            "scale_y": 2,
                        }
                    },
                    "visual_session_id": "visual-default",
                    "source_observation_ref": "obs-parent",
                }
            }

            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter") as build:
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views=protocol_views,
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-changes-view",
                        "q": "changes",
                        "src": {"type": "view", "view_id": "v_parent"},
                        "r": {"x": 5, "y": 3, "w": 5, "h": 4},
                        "scale_down": 1,
                    },
                )

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertEqual(look["r"]["unit"], "parent_view_px")
        self.assertEqual(look["screen_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        change = look["changes"]["changes"][0]
        self.assertEqual(change["requested_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        self.assertEqual(change["region"], {"x": 10, "y": 6, "w": 10, "h": 8})
        self.assertEqual(change["changed_bbox"], {"x": 0, "y": 1, "w": 4, "h": 2})
        self.assertTrue(look["no_capture_performed"])
        self.assertFalse(look["tool_asserts_target_hit"])
        build.assert_not_called()

    def test_host_look_view_source_uses_stored_transform_not_legacy_scale_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            observe_dir = Path(temp_dir) / "observe"
            observe_dir.mkdir()
            fake_adapter = P3AHostFakeAdapter(observe_dir=observe_dir)
            protocol_views = {
                "v_transform_parent": {
                    "view": {"id": "v_transform_parent", "w": 100, "h": 80, "scale_down": 9},
                    "visual_session_id": "visual-default",
                    "source_observation_ref": "obs-transform-parent",
                    "screen_rect": {"x": 0, "y": 0, "w": 900, "h": 720},
                    "transform": {
                        "view_pixels_to_virtual_screen_pixels": {
                            "origin_x": 100,
                            "origin_y": 50,
                            "scale_x": 2,
                            "scale_y": 2,
                        }
                    },
                }
            }
            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter", return_value=fake_adapter):
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views=protocol_views,
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-transform-child",
                        "q": "frame",
                        "src": {"type": "view", "view_id": "v_transform_parent"},
                        "r": {"x": 5, "y": 3, "w": 4, "h": 5},
                        "scale_down": 1,
                    },
                )
            observe_payloads = [payload for name, payload in fake_adapter.calls if name == "observe"]

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertEqual(observe_payloads[-1]["region"], {"x": 110, "y": 56, "width": 8, "height": 10})
        self.assertEqual(protocol_views[look["view"]["id"]]["screen_rect"], {"x": 110, "y": 56, "w": 8, "h": 10})
        self.assertEqual(look["view_record"]["requested_screen_region"], {"x": 110, "y": 56, "w": 8, "h": 10})
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_look_view_source_rejects_region_outside_parent_view_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status, look = _host_agent_protocol_look(
                visual_sessions={},
                protocol_views={
                    "v_parent": {
                        "view": {"id": "v_parent", "w": 20, "h": 12, "scale_down": 2},
                        "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                    }
                },
                runs_dir=temp_dir,
                request={
                    "v": "V1",
                    "id": "host-look-view-oob",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "v_parent"},
                    "r": {"x": 19, "y": 0, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertEqual(status, 409)
        self.assertEqual(look["failure_code"], "VIEW_REGION_OUT_OF_BOUNDS")
        self.assertFalse(look["host_input_sent"])
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_look_view_source_rejects_missing_parent_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status, look = _host_agent_protocol_look(
                visual_sessions={},
                protocol_views={
                    "v_parent": {
                        "view": {"id": "v_parent", "w": 20, "h": 12, "scale_down": 2},
                        "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                    }
                },
                runs_dir=temp_dir,
                request={
                    "v": "V1",
                    "id": "host-look-view-no-transform",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "v_parent"},
                    "r": {"x": 1, "y": 1, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertEqual(status, 409)
        self.assertEqual(look["failure_code"], "VIEW_TRANSFORM_UNAVAILABLE")
        self.assertFalse(look["host_input_sent"])
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_look_view_source_rejects_invalid_parent_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status, look = _host_agent_protocol_look(
                visual_sessions={},
                protocol_views={
                    "v_parent": {
                        "view": {"id": "v_parent", "w": 20, "h": 12, "scale_down": 2},
                        "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                        "transform": {
                            "view_pixels_to_virtual_screen_pixels": {
                                "origin_x": 100,
                                "origin_y": 50,
                                "scale_x": -1,
                                "scale_y": 2,
                            }
                        },
                    }
                },
                runs_dir=temp_dir,
                request={
                    "v": "V1",
                    "id": "host-look-view-bad-transform",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "v_parent"},
                    "r": {"x": 1, "y": 1, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertEqual(status, 409)
        self.assertEqual(look["failure_code"], "VIEW_TRANSFORM_UNAVAILABLE")
        self.assertFalse(look["host_input_sent"])
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_look_view_source_rejects_historical_review_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            status, look = _host_agent_protocol_look(
                visual_sessions={},
                protocol_views={
                    "sv_historical": {
                        "view": {"id": "sv_historical", "w": 20, "h": 12, "scale_down": 1},
                        "view_role": "historical_segment_review",
                        "view_is_current_action_basis": False,
                        "screen_rect": {"x": 100, "y": 50, "w": 20, "h": 12},
                        "transform": {
                            "view_pixels_to_virtual_screen_pixels": {
                                "origin_x": 100,
                                "origin_y": 50,
                                "scale_x": 1,
                                "scale_y": 1,
                            }
                        },
                    }
                },
                runs_dir=temp_dir,
                request={
                    "v": "V1",
                    "id": "host-look-historical-source",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "sv_historical"},
                    "r": {"x": 1, "y": 1, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertEqual(status, 409)
        self.assertEqual(look["failure_code"], "VIEW_NOT_CURRENT_SCREEN_BASIS")
        self.assertFalse(look["host_input_sent"])
        self.assertFalse(look["tool_asserts_business_success"])

    def test_host_look_diff_timeline_exports_derived_segment_artifact_without_capture(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            for x in range(12, 18):
                for y in range(8, 11):
                    changed.putpixel((x, y), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter") as build:
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views={},
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-diff-timeline",
                        "q": "diff",
                        "mode": "timeline_with_artifacts",
                        "src": {"type": "screen", "t": "latest"},
                        "r": {"x": 110, "y": 56, "w": 16, "h": 10},
                        "scale_down": 1,
                        "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:02Z"},
                        "max_artifacts": 1,
                    },
                )
                artifact_exists = Path(look["artifacts"][0]["media_path_abs"]).exists()

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertEqual(look["type"], "diff")
        self.assertEqual(look["mode"], "timeline_segment_diff")
        self.assertEqual(look["r"]["unit"], "virtual_screen_px")
        self.assertEqual(look["screen_region"], {"x": 110, "y": 56, "w": 16, "h": 10})
        self.assertTrue(look["no_capture_performed"])
        self.assertFalse(look["raw_media_returned"])
        self.assertTrue(look["derived_review_artifact_returned"])
        self.assertFalse(look["derived_artifacts_are_canonical"])
        self.assertEqual(look["diffs"]["change_count"], 1)
        self.assertEqual(look["diffs"]["changes"][0]["changed_bbox"], {"x": 2, "y": 2, "w": 6, "h": 3})
        self.assertEqual(look["artifacts"][0]["artifact_type"], "diff_heatmap")
        self.assertEqual(look["artifacts"][0]["raw_or_derived"], "derived_review_only")
        self.assertTrue(artifact_exists)
        self.assertFalse(look["tool_asserts_causality"])
        self.assertFalse(look["tool_asserts_target_hit"])
        self.assertFalse(look["tool_asserts_business_success"])
        self.assertFalse(look["boundary"]["business_success_judged"])
        build.assert_not_called()

    def test_host_look_clip_maps_parent_view_region_without_capture(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            segment_path = root / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            changed.putpixel((12, 8), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()
            protocol_views = {
                "v_parent": {
                    "view": {"id": "v_parent", "path": "parent.png", "w": 20, "h": 12, "scale_down": 2},
                    "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                    "transform": {
                        "view_pixels_to_virtual_screen_pixels": {
                            "origin_x": 100,
                            "origin_y": 50,
                            "scale_x": 2,
                            "scale_y": 2,
                        }
                    },
                    "visual_session_id": "visual-default",
                    "source_observation_ref": "obs-parent",
                }
            }

            with mock.patch("ai_control.host_agent.server.build_manual_windows_input_adapter") as build:
                status, look = _host_agent_protocol_look(
                    visual_sessions={},
                    protocol_views=protocol_views,
                    runs_dir=temp_dir,
                    request={
                        "v": "V1",
                        "id": "host-look-clip-view",
                        "q": "clip",
                        "src": {"type": "view", "view_id": "v_parent"},
                        "r": {"x": 5, "y": 3, "w": 5, "h": 4},
                        "scale_down": 1,
                        "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:02Z"},
                        "max_frames": 4,
                    },
                )

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertEqual(look["type"], "clip")
        self.assertEqual(look["mode"], "segment_review_clip")
        self.assertEqual(look["r"]["unit"], "parent_view_px")
        self.assertEqual(look["screen_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        self.assertEqual(look["source_rect_in_parent"], {"x": 5, "y": 3, "w": 5, "h": 4})
        self.assertTrue(look["no_capture_performed"])
        self.assertFalse(look["derived_review_artifact_returned"])
        self.assertEqual(look["clip"]["selected_frame_count"], 2)
        self.assertEqual(look["clip"]["frames"][0]["requested_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        self.assertEqual(look["clip"]["frames"][0]["region"], {"x": 10, "y": 6, "w": 10, "h": 8})
        self.assertFalse(look["tool_asserts_causality"])
        self.assertFalse(look["tool_asserts_target_hit"])
        self.assertFalse(look["tool_asserts_business_success"])
        build.assert_not_called()

    def test_mcp_look_changes_returns_existing_segment_metadata_without_observe(self) -> None:
        from PIL import Image

        channel = P3AMemoryLookChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            segment_path = Path(temp_dir) / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (20, 12), (10, 20, 30, 255))
            changed = base.copy()
            changed.putpixel((5, 4), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 0, "y": 0, "w": 20, "h": 12},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 0, "y": 0, "w": 20, "h": 12},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-changes",
                    "q": "changes",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 12},
                    "scale_down": 1,
                    "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:02Z"},
                    "max_pairs": 4,
                },
            )

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["type"], "changes")
        self.assertTrue(data["no_capture_performed"])
        self.assertFalse(data["raw_media_returned"])
        self.assertEqual(data["r"]["unit"], "virtual_screen_px")
        self.assertEqual(data["changes"]["change_count"], 1)
        self.assertEqual(data["changes"]["region_coordinate_system"], "virtual_screen_pixels")
        self.assertEqual(data["changes"]["query_window"]["pair_time_basis"], "after_frame_timestamp")
        self.assertEqual(len(channel.captures), 0)
        self.assertFalse(data["boundary"]["business_success_judged"])

    def test_mcp_look_time_near_decodes_agseg_region_without_observe(self) -> None:
        from PIL import Image

        channel = P3AMemoryLookChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            segment_path = Path(temp_dir) / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00+08:00",
                timestamp_monotonic_ms=1000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-time-near-decode",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 110, "y": 55, "w": 20, "h": 10},
                    "scale_down": 2,
                    "time": {"near": "2026-06-21T10:00:00+08:00"},
                },
            )
            historical_view_id = result["data"]["historical_view"]["id"]
            do_result = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "mcp-do-historical-time-near",
                    "basis": {"view_id": historical_view_id, "point": {"x": 1, "y": 1}},
                    "seq": [{"t": "click", "b": "left"}],
                },
            )

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["type"], "time_near_frames")
        self.assertEqual(data["mode"], "segment_decoder_nearest_indexed_frames")
        self.assertTrue(data["no_capture_performed"])
        self.assertTrue(data["decoded_review_returned"])
        self.assertFalse(data["view_is_current_action_basis"])
        self.assertEqual(data["r"]["unit"], "virtual_screen_px")
        self.assertEqual(data["decoded_review"]["raw_or_derived"], "derived_review_only")
        self.assertEqual(data["decoded_review"]["scale_down"], 2)
        self.assertEqual(data["decoded_review"]["decode_region_basis"]["region"], {"x": 10, "y": 5, "w": 20, "h": 10})
        self.assertEqual(data["decoded_review"]["requested_screen_region"], {"x": 110, "y": 55, "w": 20, "h": 10})
        self.assertTrue(data["image_content_returned"])
        self.assertEqual(result["content"][0]["type"], "image")
        self.assertNotIn("review_media_path_abs", data["decoded_review"])
        self.assertIn(historical_view_id, adapter.session.gateway.views)
        self.assertFalse(adapter.session.gateway.views[historical_view_id]["view_is_current_action_basis"])
        self.assertFalse(do_result["ok"])
        self.assertEqual(do_result["failure"]["failure_code"], "VIEW_NOT_ACTION_BASIS")
        self.assertEqual(data["frames_near_time"]["nearest_frame"]["segment_frame_id"], "f000000")
        self.assertEqual(len(channel.captures), 0)
        self.assertFalse(data["tool_asserts_causality"])
        self.assertFalse(data["tool_asserts_target_hit"])
        self.assertFalse(data["tool_asserts_business_success"])

    def test_mcp_look_changes_maps_parent_view_region_without_observe(self) -> None:
        from PIL import Image

        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            segment_path = Path(temp_dir) / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            for x in range(10, 14):
                for y in range(7, 9):
                    changed.putpixel((x, y), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()
            adapter.session.gateway.views["v_parent"] = {
                "view": {"id": "v_parent", "path": "parent.png", "w": 20, "h": 12, "scale_down": 2},
                "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 100,
                        "origin_y": 50,
                        "scale_x": 2,
                        "scale_y": 2,
                    }
                },
                "visual_session_id": "visual-default",
                "source_observation_ref": "obs-parent",
            }

            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-changes-view",
                    "q": "changes",
                    "src": {"type": "view", "view_id": "v_parent"},
                    "r": {"x": 5, "y": 3, "w": 5, "h": 4},
                    "scale_down": 1,
                },
            )

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["r"]["unit"], "parent_view_px")
        self.assertEqual(data["screen_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        self.assertEqual(data["changes"]["changes"][0]["changed_bbox"], {"x": 0, "y": 1, "w": 4, "h": 2})
        self.assertEqual(len(channel.captures), 0)
        self.assertTrue(data["no_capture_performed"])
        self.assertFalse(data["tool_asserts_business_success"])

    def test_mcp_look_diff_timeline_exports_derived_segment_artifact_without_observe(self) -> None:
        from PIL import Image

        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            segment_path = Path(temp_dir) / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            for x in range(12, 18):
                for y in range(8, 11):
                    changed.putpixel((x, y), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-diff-timeline",
                    "q": "diff",
                    "mode": "timeline_with_artifacts",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 110, "y": 56, "w": 16, "h": 10},
                    "scale_down": 1,
                    "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:02Z"},
                    "max_artifacts": 1,
                },
            )
            artifact_exists = Path(result["data"]["artifacts"][0]["media_path_abs"]).exists() if result.get("ok") else False

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["type"], "diff")
        self.assertEqual(data["mode"], "timeline_segment_diff")
        self.assertEqual(data["r"]["unit"], "virtual_screen_px")
        self.assertEqual(data["screen_region"], {"x": 110, "y": 56, "w": 16, "h": 10})
        self.assertTrue(data["no_capture_performed"])
        self.assertFalse(data["raw_media_returned"])
        self.assertTrue(data["derived_review_artifact_returned"])
        self.assertFalse(data["derived_artifacts_are_canonical"])
        self.assertEqual(len(channel.captures), 0)
        self.assertEqual(data["diffs"]["change_count"], 1)
        self.assertEqual(data["diffs"]["changes"][0]["requested_region"], {"x": 110, "y": 56, "w": 16, "h": 10})
        self.assertEqual(data["diffs"]["changes"][0]["region"], {"x": 10, "y": 6, "w": 16, "h": 10})
        self.assertEqual(data["artifacts"][0]["artifact_type"], "diff_heatmap")
        self.assertEqual(data["artifacts"][0]["raw_or_derived"], "derived_review_only")
        self.assertTrue(artifact_exists)
        self.assertFalse(data["tool_asserts_causality"])
        self.assertFalse(data["tool_asserts_target_hit"])
        self.assertFalse(data["tool_asserts_business_success"])

    def test_mcp_look_diff_timeline_maps_parent_view_region_without_observe(self) -> None:
        from PIL import Image

        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            segment_path = Path(temp_dir) / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (40, 24), (10, 20, 30, 255))
            changed = base.copy()
            for x in range(10, 14):
                for y in range(7, 9):
                    changed.putpixel((x, y), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 100, "y": 50, "w": 40, "h": 24},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()
            adapter.session.gateway.views["v_parent"] = {
                "view": {"id": "v_parent", "path": "parent.png", "w": 20, "h": 12, "scale_down": 2},
                "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 24},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 100,
                        "origin_y": 50,
                        "scale_x": 2,
                        "scale_y": 2,
                    }
                },
                "visual_session_id": "visual-default",
                "source_observation_ref": "obs-parent",
            }

            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-diff-timeline-view",
                    "q": "diff",
                    "mode": "timeline",
                    "src": {"type": "view", "view_id": "v_parent"},
                    "r": {"x": 5, "y": 3, "w": 5, "h": 4},
                    "scale_down": 1,
                    "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:02Z"},
                },
            )

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["requested_mode"], "timeline")
        self.assertEqual(data["r"]["unit"], "parent_view_px")
        self.assertEqual(data["screen_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        self.assertEqual(data["source_rect_in_parent"], {"x": 5, "y": 3, "w": 5, "h": 4})
        self.assertEqual(data["diffs"]["changes"][0]["requested_region"], {"x": 110, "y": 56, "w": 10, "h": 8})
        self.assertEqual(data["diffs"]["changes"][0]["region"], {"x": 10, "y": 6, "w": 10, "h": 8})
        self.assertEqual(data["diffs"]["changes"][0]["changed_bbox"], {"x": 0, "y": 1, "w": 4, "h": 2})
        self.assertFalse(data["derived_review_artifact_returned"])
        self.assertTrue(data["no_capture_performed"])
        self.assertEqual(len(channel.captures), 0)
        self.assertFalse(data["tool_asserts_target_hit"])

    def test_mcp_look_clip_exports_derived_gif_without_observe(self) -> None:
        from PIL import Image

        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            segment_path = Path(temp_dir) / "visual-default" / "segments" / "agentsight-20260621.agseg"
            base = Image.new("RGBA", (20, 12), (10, 20, 30, 255))
            changed = base.copy()
            changed.putpixel((5, 4), (220, 80, 40, 255))
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-20260621")
            writer.add_frame(
                base,
                timestamp_iso="2026-06-21T10:00:00Z",
                timestamp_monotonic_ms=1000,
                source="screen",
                screen_region={"x": 0, "y": 0, "w": 20, "h": 12},
                coordinate_system="virtual_screen_pixels",
            )
            writer.add_frame(
                changed,
                timestamp_iso="2026-06-21T10:00:01Z",
                timestamp_monotonic_ms=2000,
                source="look",
                screen_region={"x": 0, "y": 0, "w": 20, "h": 12},
                coordinate_system="virtual_screen_pixels",
            )
            writer.close()

            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "mcp-look-clip",
                    "q": "clip",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 12},
                    "scale_down": 1,
                    "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:02Z"},
                    "max_frames": 8,
                    "max_artifacts": 1,
                },
            )
            artifact_exists = Path(result["data"]["artifacts"][0]["media_path_abs"]).exists() if result.get("ok") else False

        self.assertTrue(result["ok"])
        data = result["data"]
        self.assertEqual(data["type"], "clip")
        self.assertEqual(data["mode"], "segment_review_clip")
        self.assertTrue(data["no_capture_performed"])
        self.assertFalse(data["raw_media_returned"])
        self.assertTrue(data["derived_review_artifact_returned"])
        self.assertFalse(data["derived_artifacts_are_canonical"])
        self.assertEqual(data["clip"]["selected_frame_count"], 2)
        self.assertEqual(data["clip"]["artifacts"][0]["artifact_type"], "review_clip_gif")
        self.assertEqual(data["artifacts"][0]["raw_or_derived"], "derived_review_only")
        self.assertFalse(data["artifacts"][0]["artifact_is_canonical_evidence"])
        self.assertTrue(artifact_exists)
        self.assertEqual(len(channel.captures), 0)
        self.assertFalse(data["tool_asserts_causality"])
        self.assertFalse(data["tool_asserts_target_hit"])
        self.assertFalse(data["tool_asserts_business_success"])

    def test_historical_decode_region_maps_monitor_pixel_frames_with_screen_region(self) -> None:
        mapped = _host_historical_decode_region(
            {"x": 110, "y": 55, "w": 20, "h": 10},
            {
                "screen_region": {"x": 100, "y": 50, "w": 40, "h": 24},
                "coordinate_system": "monitor_pixels",
            },
        )

        self.assertEqual(mapped["unit"], "virtual_screen_px")
        self.assertEqual(mapped["source_coordinate_system"], "monitor_pixels")
        self.assertEqual(mapped["region"], {"x": 10, "y": 5, "w": 20, "h": 10})
        self.assertEqual(mapped["stored_frame_region"], {"x": 100, "y": 50, "w": 40, "h": 24})

    def test_historical_decode_region_reports_no_overlap_for_mapped_screen_region(self) -> None:
        mapped = _host_historical_decode_region(
            {"x": 500, "y": 500, "w": 20, "h": 10},
            {
                "screen_region": {"x": 100, "y": 50, "w": 40, "h": 24},
                "coordinate_system": "monitor_pixels",
            },
        )

        self.assertEqual(mapped["unit"], "virtual_screen_px")
        self.assertEqual(mapped["status"], "no_overlap")
        self.assertEqual(mapped["source_coordinate_system"], "monitor_pixels")
        self.assertEqual(mapped["stored_frame_region"], {"x": 100, "y": 50, "w": 40, "h": 24})
        self.assertEqual(mapped["requested_screen_region"], {"x": 500, "y": 500, "w": 20, "h": 10})
        self.assertNotIn("region", mapped)

    def test_host_look_time_near_falls_back_to_next_decodable_segment_frame(self) -> None:
        bad = {
            "segment_id": "seg-runtime",
            "segment_path_abs": "C:\\tmp\\seg.agseg",
            "frame_id": "f-bad",
            "segment_frame_id": "f-bad",
            "relation": "nearest",
            "delta_ms": 0,
            "screen_region": {"x": 0, "y": 0, "w": 100, "h": 60},
            "coordinate_system": "monitor_pixels",
            "segment_restore_ref": {
                "storage_format": "binary_agseg",
                "segment_path": "C:\\tmp\\seg.agseg",
                "frame_id": "f-bad",
            },
        }
        good = {
            **bad,
            "frame_id": "f-good",
            "segment_frame_id": "f-good",
            "relation": "after",
            "delta_ms": 10,
            "segment_restore_ref": {
                "storage_format": "binary_agseg",
                "segment_path": "C:\\tmp\\seg.agseg",
                "frame_id": "f-good",
            },
        }
        near = {
            "query_status": "generated",
            "frames": [bad, good],
            "nearest_frame": bad,
            "before_frame": bad,
            "after_frame": good,
        }

        def decode_side_effect(restore_ref: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            if restore_ref["frame_id"] == "f-bad":
                raise ValueError("blob hash mismatch: b000001")
            return {
                "object_type": "AgentSightBinarySegmentRegionReviewReport",
                "schema": "agentsight_segment_v1",
                "source_frame_id": restore_ref["frame_id"],
                "source_frame_hash_ok": True,
                "region": kwargs["region"],
                "scale_down": kwargs["scale_down"],
                "raw_or_derived": "derived_review_only",
                "artifact_is_canonical_evidence": False,
                "derived_review_file_written": False,
                "image_content_type": "mcp_image_content",
                "mcp_content": [{"type": "image", "mimeType": "image/png", "data": "abc"}],
                "tool_asserts_business_success": False,
                "tool_asserts_causality": False,
                "tool_asserts_target_hit": False,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("ai_control.host_agent.server.query_segment_decoder_near_time", return_value=near):
                with mock.patch("ai_control.host_agent.server.decode_segment_region_to_image_content", side_effect=decode_side_effect) as decode:
                    status, look = _host_agent_protocol_look(
                        visual_sessions={},
                        protocol_views={},
                        runs_dir=temp_dir,
                        request={
                            "v": "V1",
                            "id": "host-look-time-near-fallback",
                            "q": "frame",
                            "src": {"type": "screen", "t": "latest"},
                            "r": {"x": 10, "y": 10, "w": 20, "h": 10},
                            "scale_down": 1,
                            "time": {"near": "2026-06-21T10:00:00+08:00"},
                        },
                    )

        self.assertEqual(status, 200)
        self.assertTrue(look["ok"])
        self.assertTrue(look["decoded_review_returned"])
        self.assertIsNone(look["decode_error"])
        self.assertEqual(len(look["decode_errors"]), 1)
        self.assertEqual(look["decode_errors"][0]["frame_id"], "f-bad")
        self.assertEqual(look["decoded_review"]["source_frame_id"], "f-good")
        self.assertEqual(look["decoded_review"]["selected_segment_frame"]["frame_id"], "f-good")
        self.assertEqual(look["decoded_review"]["decode_region_basis"]["unit"], "virtual_screen_px")
        self.assertEqual(look["r"]["unit"], "virtual_screen_px")
        self.assertIsNone(look["r"]["coordinate_caveat"])
        self.assertTrue(look["no_capture_performed"])
        self.assertFalse(look["tool_asserts_business_success"])
        self.assertEqual(decode.call_count, 2)

    def test_host_look_time_near_skips_mapped_candidate_when_requested_region_has_no_overlap(self) -> None:
        outside = {
            "segment_id": "seg-runtime",
            "segment_path_abs": "C:\\tmp\\seg.agseg",
            "frame_id": "f-outside",
            "segment_frame_id": "f-outside",
            "relation": "nearest",
            "delta_ms": 0,
            "screen_region": {"x": 100, "y": 100, "w": 20, "h": 20},
            "coordinate_system": "monitor_pixels",
            "segment_restore_ref": {
                "storage_format": "binary_agseg",
                "segment_path": "C:\\tmp\\seg.agseg",
                "frame_id": "f-outside",
            },
        }
        inside = {
            **outside,
            "frame_id": "f-inside",
            "segment_frame_id": "f-inside",
            "relation": "after",
            "delta_ms": 5,
            "screen_region": {"x": 500, "y": 500, "w": 80, "h": 60},
            "segment_restore_ref": {
                "storage_format": "binary_agseg",
                "segment_path": "C:\\tmp\\seg.agseg",
                "frame_id": "f-inside",
            },
        }
        near = {
            "query_status": "generated",
            "frames": [outside, inside],
            "nearest_frame": outside,
            "before_frame": outside,
            "after_frame": inside,
        }

        def decode_side_effect(restore_ref: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
            return {
                "object_type": "AgentSightBinarySegmentRegionReviewReport",
                "schema": "agentsight_segment_v1",
                "source_frame_id": restore_ref["frame_id"],
                "source_frame_hash_ok": True,
                "region": kwargs["region"],
                "scale_down": kwargs["scale_down"],
                "raw_or_derived": "derived_review_only",
                "artifact_is_canonical_evidence": False,
                "derived_review_file_written": False,
                "image_content_type": "mcp_image_content",
                "mcp_content": [{"type": "image", "mimeType": "image/png", "data": "abc"}],
                "tool_asserts_business_success": False,
                "tool_asserts_causality": False,
                "tool_asserts_target_hit": False,
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch("ai_control.host_agent.server.query_segment_decoder_near_time", return_value=near):
                with mock.patch("ai_control.host_agent.server.decode_segment_region_to_image_content", side_effect=decode_side_effect) as decode:
                    status, look = _host_agent_protocol_look(
                        visual_sessions={},
                        protocol_views={},
                        runs_dir=temp_dir,
                        request={
                            "v": "V1",
                            "id": "host-look-time-near-no-overlap",
                            "q": "frame",
                            "src": {"type": "screen", "t": "latest"},
                            "r": {"x": 510, "y": 510, "w": 20, "h": 10},
                            "scale_down": 1,
                            "time": {"near": "2026-06-21T10:00:00+08:00"},
                        },
                    )

        self.assertEqual(status, 200)
        self.assertTrue(look["decoded_review_returned"])
        self.assertEqual(len(look["decode_errors"]), 1)
        self.assertEqual(look["decode_errors"][0]["status"], "decode_skipped_no_overlap")
        self.assertEqual(look["decode_errors"][0]["frame_id"], "f-outside")
        self.assertEqual(look["decoded_review"]["source_frame_id"], "f-inside")
        self.assertEqual(look["decoded_review"]["decode_region_basis"]["region"], {"x": 10, "y": 10, "w": 20, "h": 10})
        self.assertEqual(decode.call_count, 1)

    def test_public_mcp_mode_rejects_non_public_tool_names_at_call_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(runs_dir=temp_dir, enforce_public_tool_allowlist=True)
            result = adapter.call_tool("query_visual_memory", {"query_type": "status"})

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "mcp_tool_not_public")
        self.assertEqual(result["allowed_tools"], ["screen", "look", "do"])
        self.assertFalse(result["host_input_sent"])
        self.assertEqual(result["host_sent_event_count"], 0)
        self.assertFalse(result["boundary"]["business_success_judged"])

    def test_look_creates_view_id_and_child_view_coordinate_mapping(self) -> None:
        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            root = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-root",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": -10, "y": 20, "w": 100, "h": 80},
                    "scale_down": 5,
                },
            )
            root_view_id = root["data"]["view"]["id"]
            child = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-child",
                    "q": "frame",
                    "src": {"type": "view", "view_id": root_view_id},
                    "r": {"x": 2, "y": 3, "w": 4, "h": 5},
                    "scale_down": 1,
                },
            )

            child_view_id = child["data"]["view"]["id"]
            child_record = adapter.session.gateway.views[child_view_id]

        self.assertTrue(root["ok"])
        self.assertEqual(root["content"][0]["type"], "image")
        self.assertEqual(root["content"][0]["mimeType"], "image/png")
        self.assertTrue(root["content"][0]["data"])
        self.assertEqual(root["data"]["view"]["w"], 20)
        self.assertEqual(root["data"]["view"]["h"], 16)
        self.assertNotIn("path", root["data"]["view"])
        self.assertTrue(root["data"]["image_content_returned"])
        self.assertFalse(root["data"]["derived_review_file_written"])
        self.assertEqual(root["data"]["view_record"]["requested_screen_region"], {"x": -10, "y": 20, "w": 100, "h": 80})
        self.assertEqual(root["data"]["view_record"]["actual_decoded_region"], {"x": 0, "y": 0, "w": 100, "h": 80})
        self.assertEqual(root["data"]["view_record"]["transform"]["view_pixels_to_virtual_screen_pixels"]["scale_x"], 5)
        self.assertTrue(child["ok"])
        self.assertEqual(child["data"]["view"]["w"], 20)
        self.assertEqual(child["data"]["view"]["h"], 25)
        self.assertEqual(child_record["parent_view_id"], root_view_id)
        self.assertEqual(child_record["source_rect_in_parent"], {"x": 2, "y": 3, "w": 4, "h": 5})
        self.assertEqual(child_record["screen_rect"], {"x": 0, "y": 35, "w": 20, "h": 25})
        self.assertEqual(child_record["public_record"]["requested_screen_region"], {"x": 0, "y": 35, "w": 20, "h": 25})
        self.assertEqual(child_record["public_record"]["actual_decoded_region"], {"x": 0, "y": 0, "w": 20, "h": 25})
        self.assertEqual(child_record["raw_or_derived"], "derived_review_only")
        self.assertEqual(child_record["coordinate_system"], "virtual_screen_pixels")
        self.assertIn("segment_restore_ref", child_record)

    def test_look_view_source_rejects_region_outside_parent_view_bounds(self) -> None:
        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            root = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-root-oob",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 20},
                    "scale_down": 2,
                },
            )
            child = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-child-oob",
                    "q": "frame",
                    "src": {"type": "view", "view_id": root["data"]["view"]["id"]},
                    "r": {"x": 9, "y": 0, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertFalse(child["ok"])
        self.assertEqual(child["failure"]["failure_code"], "VIEW_REGION_OUT_OF_BOUNDS")

    def test_look_view_source_rejects_missing_parent_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(runs_dir=temp_dir)
            adapter.session.gateway.views["v_no_transform"] = {
                "view": {"id": "v_no_transform", "w": 10, "h": 10, "scale_down": 2},
                "screen_rect": {"x": 0, "y": 0, "w": 20, "h": 20},
            }
            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-no-transform",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "v_no_transform"},
                    "r": {"x": 0, "y": 0, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["failure_code"], "VIEW_TRANSFORM_UNAVAILABLE")

    def test_look_view_source_rejects_invalid_parent_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(runs_dir=temp_dir)
            adapter.session.gateway.views["v_bad_transform"] = {
                "view": {"id": "v_bad_transform", "w": 10, "h": 10, "scale_down": 2},
                "screen_rect": {"x": 0, "y": 0, "w": 20, "h": 20},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 0,
                        "origin_y": 0,
                        "scale_x": 0,
                        "scale_y": 2,
                    }
                },
            }
            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-bad-transform",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "v_bad_transform"},
                    "r": {"x": 0, "y": 0, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["failure_code"], "VIEW_TRANSFORM_UNAVAILABLE")

    def test_look_view_source_rejects_historical_review_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(runs_dir=temp_dir)
            adapter.session.gateway.views["sv_historical"] = {
                "view": {"id": "sv_historical", "w": 10, "h": 10, "scale_down": 1},
                "view_role": "historical_segment_review",
                "view_is_current_action_basis": False,
                "screen_rect": {"x": 0, "y": 0, "w": 10, "h": 10},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 0,
                        "origin_y": 0,
                        "scale_x": 1,
                        "scale_y": 1,
                    }
                },
            }
            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-historical-source",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "sv_historical"},
                    "r": {"x": 0, "y": 0, "w": 2, "h": 2},
                    "scale_down": 1,
                },
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["failure"]["failure_code"], "VIEW_NOT_CURRENT_SCREEN_BASIS")

    def test_look_view_source_uses_stored_transform_not_legacy_scale_fields(self) -> None:
        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            adapter.session.gateway.views["v_transform_parent"] = {
                "view": {"id": "v_transform_parent", "w": 100, "h": 80, "scale_down": 9},
                "screen_rect": {"x": 0, "y": 0, "w": 900, "h": 720},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 100,
                        "origin_y": 50,
                        "scale_x": 2,
                        "scale_y": 2,
                    }
                },
                "source_observation_ref": "obs-transform-parent",
            }
            child = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-transform-child",
                    "q": "frame",
                    "src": {"type": "view", "view_id": "v_transform_parent"},
                    "r": {"x": 5, "y": 3, "w": 4, "h": 5},
                    "scale_down": 1,
                },
            )
            child_view_id = child["data"]["view"]["id"]
            child_record = adapter.session.gateway.views[child_view_id]

        self.assertTrue(child["ok"])
        self.assertEqual(channel.captures[-1], {"x": 110, "y": 56, "width": 8, "height": 10})
        self.assertEqual(child_record["screen_rect"], {"x": 110, "y": 56, "w": 8, "h": 10})
        self.assertEqual(
            child_record["transform"]["view_pixels_to_virtual_screen_pixels"],
            {
                "origin_x": 110,
                "origin_y": 56,
                "scale_x": 1,
                "scale_y": 1,
                "formula": "screen_x=origin_x+view_x*scale_x; screen_y=origin_y+view_y*scale_y",
            },
        )
        self.assertFalse(child["data"]["tool_asserts_business_success"])

    def test_look_mcp_image_content_is_not_written_to_evidence_event(self) -> None:
        channel = P3AMemoryLookChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            result = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-no-content-in-evidence",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 32, "h": 24},
                    "scale_down": 2,
                },
            )
            session_dirs = list(Path(temp_dir).glob("session-*"))
            segment_frames = list(Path(temp_dir).glob("segments/*.agseg"))

        self.assertTrue(result["content"][0]["data"])
        self.assertIsNone(result["evidence_ref"])
        self.assertEqual(session_dirs, [])
        self.assertTrue(segment_frames)
        self.assertIn("view_record", result["data"])
        self.assertFalse(result["data"]["derived_review_file_written"])

    def test_public_do_does_not_create_legacy_session_media_or_evidence_objects(self) -> None:
        channel = P3AMemoryLookChannel()
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            look = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-before-do-no-session",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 32, "h": 24},
                    "scale_down": 2,
                },
            )
            result = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-no-session",
                    "basis": {"view_id": look["data"]["view"]["id"]},
                    "seq": [{"t": "move", "coord": "view", "x": 5, "y": 5}, {"t": "click", "b": "left"}],
                },
            )
            session_dirs = list(Path(temp_dir).glob("session-*"))
            media_files = list(Path(temp_dir).glob("session-*/media/*"))
            object_files = list(Path(temp_dir).glob("session-*/objects/*"))
            segment_files = list(Path(temp_dir).glob("segments/*.agseg"))
            backend_events = list(input_channel.events)

        self.assertTrue(result["ok"])
        self.assertIsNone(result["evidence_ref"])
        self.assertEqual(session_dirs, [])
        self.assertEqual(media_files, [])
        self.assertEqual(object_files, [])
        self.assertTrue(segment_files)
        self.assertEqual(result["data"]["input"]["host_event_count"], 0)
        self.assertEqual(len(backend_events), 2)
        self.assertTrue(all(event["legacy_evidence_prewrite_skipped"] for event in backend_events))

    def test_look_diff_compares_view_baseline_against_latest_pixels(self) -> None:
        channel = P3ALookPngChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[channel],
                default_observation_channel_ref=channel.name,
            )
            root = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-diff-root",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 40, "y": 50, "w": 24, "h": 12},
                    "scale_down": 1,
                },
            )
            diff = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-diff-public",
                    "q": "diff",
                    "src": {"type": "view", "view_id": root["data"]["view"]["id"]},
                    "r": {"x": 4, "y": 3, "w": 10, "h": 6},
                    "scale_down": 1,
                    "max_artifacts": 1,
                },
            )
            heatmap_exists = Path(diff["data"]["artifacts"][0]["media_path_abs"]).exists() if diff.get("ok") else False

        self.assertTrue(diff["ok"])
        data = diff["data"]
        self.assertEqual(data["type"], "diff")
        self.assertEqual(data["mode"], "endpoint_latest_vs_view_baseline")
        self.assertEqual(data["screen_region"], {"x": 44, "y": 53, "w": 10, "h": 6})
        self.assertEqual(data["summary"]["status"], "computed")
        self.assertTrue(data["summary"]["changed"])
        self.assertEqual(data["summary"]["frame_pairs"], 1)
        self.assertEqual(data["summary"]["computed_comparison_count"], 1)
        self.assertEqual(data["summary"]["largest_changed_bbox"], {"x": 44, "y": 53, "width": 10, "height": 6})
        self.assertEqual(data["diffs"][0]["changed_bbox_frame"], {"x": 0, "y": 0, "width": 10, "height": 6})
        self.assertFalse(data["tool_asserts_semantic_change"])
        self.assertFalse(data["tool_asserts_target_hit"])
        self.assertFalse(data["tool_asserts_business_success"])
        self.assertFalse(data["raw_media_returned"])
        self.assertTrue(data["derived_review_artifact_returned"])
        self.assertFalse(data["derived_artifacts_are_canonical"])
        self.assertEqual(data["artifacts"][0]["artifact_type"], "diff_heatmap")
        self.assertTrue(heatmap_exists)

    def test_do_requires_move_before_click_and_translates_view_coordinates(self) -> None:
        observation = P3ALookPngChannel()
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
                    "id": "look-action",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": -10, "y": 20, "w": 100, "h": 80},
                    "scale_down": 5,
                },
            )
            view_id = look["data"]["view"]["id"]
            no_move = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-no-move",
                    "basis": {"view_id": view_id},
                    "seq": [{"t": "click", "b": "left"}],
                },
            )
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-action",
                    "basis": {"view_id": view_id},
                    "seq": [
                        {"t": "move", "x": 6, "y": 7, "coord": "view", "move": "instant"},
                        {"t": "click", "b": "left"},
                        10,
                        {"t": "text", "text": "AI-CONTROL-TEST"},
                        {"t": "key", "key": "ENTER"},
                    ],
                },
            )

        self.assertTrue(no_move["ok"])
        self.assertFalse(no_move["data"]["ok"])
        self.assertEqual(no_move["data"]["failed_step"]["failure_code"], "DO_REQUIRES_PRIOR_MOVE")
        self.assertTrue(action["ok"])
        self.assertTrue(action["data"]["ok"])
        self.assertEqual(action["data"]["status"], "done")
        self.assertEqual(action["data"]["input"]["host_event_count"], 0)
        self.assertEqual(action["data"]["steps"][0]["screen"], {"x": 20, "y": 55})
        self.assertEqual(input_channel.events[0]["payload"]["input_type"], "mouse_move")
        self.assertEqual(input_channel.events[0]["payload"]["x"], 20)
        self.assertEqual(input_channel.events[0]["payload"]["y"], 55)
        self.assertEqual(input_channel.events[1]["payload"]["input_type"], "mouse_click")
        self.assertEqual(input_channel.events[1]["payload"]["x"], 20)
        self.assertEqual(input_channel.events[1]["payload"]["y"], 55)
        self.assertEqual(input_channel.events[2]["payload"]["input_type"], "key_text_stream")
        self.assertNotIn("text", action["data"]["steps"][3]["req"])
        self.assertIn("text_summary", action["data"]["steps"][3]["req"])
        self.assertFalse(action["data"]["tool_asserts_business_success"])

    def test_do_can_translate_basis_point_from_view_coordinates(self) -> None:
        observation = P3ALookPngChannel()
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
                    "id": "look-basis-point",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": -10, "y": 20, "w": 100, "h": 80},
                    "scale_down": 5,
                },
            )
            view_id = look["data"]["view"]["id"]
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-basis-point",
                    "basis": {"view_id": view_id, "point": {"x": 6, "y": 7}},
                    "seq": [{"t": "click", "b": "left"}],
                },
            )

        self.assertTrue(action["ok"])
        self.assertTrue(action["data"]["ok"])
        self.assertEqual(action["data"]["basis"]["point"]["x"], 6)
        self.assertEqual(action["data"]["basis"]["point"]["y"], 7)
        self.assertEqual(action["data"]["basis"]["screen_point"], {"x": 20, "y": 55})
        self.assertEqual(action["data"]["steps"][0]["screen"], {"x": 20, "y": 55})
        self.assertEqual(input_channel.events[0]["payload"]["input_type"], "mouse_click")
        self.assertEqual(input_channel.events[0]["payload"]["x"], 20)
        self.assertEqual(input_channel.events[0]["payload"]["y"], 55)
        self.assertFalse(action["data"]["tool_asserts_target_hit"])
        self.assertFalse(action["data"]["tool_asserts_business_success"])

    def test_do_rejects_basis_point_outside_view_bounds(self) -> None:
        observation = P3ALookPngChannel()
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
                    "id": "look-basis-oob",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 20},
                    "scale_down": 2,
                },
            )
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-basis-oob",
                    "basis": {"view_id": look["data"]["view"]["id"], "point": {"x": 10, "y": 0}},
                    "seq": [{"t": "click", "b": "left"}],
                },
            )

        self.assertFalse(action["ok"])
        self.assertEqual(action["failure"]["failure_code"], "VIEW_POINT_OUT_OF_BOUNDS")
        self.assertFalse(input_channel.events)

    def test_do_rejects_move_point_outside_view_bounds(self) -> None:
        observation = P3ALookPngChannel()
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
                    "id": "look-move-oob",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 20, "h": 20},
                    "scale_down": 2,
                },
            )
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-move-oob",
                    "basis": {"view_id": look["data"]["view"]["id"]},
                    "seq": [{"t": "move", "x": 0, "y": 10, "coord": "view", "move": "instant"}],
                },
            )

        self.assertFalse(action["ok"])
        self.assertEqual(action["failure"]["failure_code"], "VIEW_POINT_OUT_OF_BOUNDS")
        self.assertFalse(input_channel.events)

    def test_do_fails_when_view_transform_is_missing(self) -> None:
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            adapter.session.gateway.views["legacy-no-transform"] = {
                "view": {"id": "legacy-no-transform", "w": 20, "h": 16, "scale_down": 5},
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
                "source_observation_ref": "obs-source",
            }
            adapter.session.gateway.observations["obs-source"] = {
                "observation_id": "obs-source",
                "screen_region": {"x": -10, "y": 20, "width": 100, "height": 80},
                "coordinate_system": "virtual_screen_pixels",
            }
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-missing-transform",
                    "basis": {"view_id": "legacy-no-transform"},
                    "seq": [{"t": "move", "x": 6, "y": 7, "coord": "view", "move": "instant"}],
                },
            )

        self.assertFalse(action["ok"])
        self.assertEqual(action["failure"]["failure_code"], "VIEW_TRANSFORM_UNAVAILABLE")
        self.assertFalse(input_channel.events)

    def test_do_rejects_historical_review_view_as_action_basis(self) -> None:
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            adapter.session.gateway.views["sv_historical"] = {
                "view": {
                    "id": "sv_historical",
                    "w": 20,
                    "h": 10,
                    "scale_down": 2,
                    "view_is_current_action_basis": False,
                },
                "view_role": "historical_segment_review",
                "view_is_current_action_basis": False,
                "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 20},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 100,
                        "origin_y": 50,
                        "scale_x": 2,
                        "scale_y": 2,
                    }
                },
                "source_observation_ref": "obs-historical",
            }
            adapter.session.gateway.observations["obs-historical"] = {
                "observation_id": "obs-historical",
                "screen_region": {"x": 100, "y": 50, "width": 40, "height": 20},
                "coordinate_system": "virtual_screen_pixels",
            }
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "do-historical-basis",
                    "basis": {"view_id": "sv_historical", "point": {"x": 3, "y": 4}},
                    "seq": [{"t": "click", "b": "left"}],
                },
            )

        self.assertFalse(action["ok"])
        self.assertEqual(action["failure"]["failure_code"], "VIEW_NOT_ACTION_BASIS")
        self.assertFalse(input_channel.events)

    def test_do_post_observe_returns_metadata_window_without_success_claims(self) -> None:
        observation = P3ALookPngChannel()
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
                    "id": "look-post-observe",
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
                    "id": "do-post-observe",
                    "basis": {"view_id": view_id},
                    "seq": [{"t": "move", "x": 4, "y": 5, "coord": "view", "move": "instant"}],
                    "post_observe": {
                        "delay_ms": 0,
                        "frame_count": 3,
                        "interval_ms": 0,
                        "stable_threshold": 0.0,
                        "stable_frame_count": 2,
                        "stop_when_stable": False,
                    },
                },
            )

        self.assertTrue(action["ok"])
        self.assertTrue(action["data"]["ok"])
        post = action["data"]["post_observe"]
        self.assertEqual(post["schema"], "ai_control_post_action_observation_window_v1")
        self.assertEqual(post["request"]["frame_count"], 3)
        self.assertEqual(post["sampled_frame_count"], 3)
        self.assertEqual(post["comparison_count"], 3)
        self.assertEqual(post["summary"]["computed_comparison_count"], 3)
        self.assertTrue(post["summary"]["changed"])
        self.assertEqual(post["summary"]["stability_status"], "still_changing_at_window_end")
        self.assertTrue(post["summary"]["not_stable"])
        self.assertTrue(post["summary"]["still_changing_at_window_end"])
        self.assertFalse(post["summary"]["stop_when_stable"])
        self.assertFalse(post["summary"]["stopped_early"])
        self.assertEqual(post["summary"]["sampling_stop_reason"], "max_frame_count_reached")
        self.assertFalse(post["summary"]["tool_asserts_business_success"])
        self.assertFalse(post["tool_asserts_business_success"])
        self.assertFalse(post["tool_asserts_semantic_change"])
        self.assertEqual(post["input_visual_relationship_judgment"], "external_review_only")
        self.assertFalse(post["raw_media_returned"])
        self.assertFalse(post["derived_review_artifact_returned"])
        self.assertTrue(post["derived_metadata"])
        self.assertTrue(post["raw_frames_are_canonical_evidence"])
        self.assertNotIn("media_path_abs", json.dumps(post, sort_keys=True))
        self.assertEqual(len(observation.captures), 5)

    def test_visual_frame_index_records_public_look_and_do_after_frames_for_time_near_lookup(self) -> None:
        observation = P3ALookPngChannel()
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
                    "id": "look-index-source",
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
                    "id": "do-index-source",
                    "basis": {"view_id": view_id},
                    "seq": [{"t": "wait", "ms": 1}],
                    "post_observe": {"delay_ms": 0, "frame_count": 2, "interval_ms": 0},
                },
            )
            entries = adapter.session.gateway.frame_buffer.entries
            look_entry = next(entry for entry in entries if entry.get("source") == "look")
            after_entries = [entry for entry in entries if entry.get("source") == "do_after_frame"]
            requested_time = (float(look_entry["captured_at"]) + float(after_entries[-1]["captured_at"])) / 2
            time_lookup = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "look-time-near",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 60, "h": 40},
                    "scale_down": 1,
                    "time": {"near": str(requested_time)},
                },
            )
            memory_lookup = adapter.call_tool(
                "query_visual_memory",
                {"query_type": "frames_near_time", "requested_time": str(requested_time), "max_entries": 4},
            )
            look_media_path_exists = Path(look_entry["raw_media_path_abs"]).exists()
            nearest_media_path_exists = Path(
                time_lookup["data"]["frames_near_time"]["nearest_frame"]["raw_media_path_abs"]
            ).exists()

        self.assertTrue(action["ok"])
        self.assertEqual(look_entry["frame_id"], look_entry["observation_ref"])
        self.assertEqual(look_entry["view_id"], view_id)
        self.assertEqual(look_entry["event_id"], "look-index-source")
        self.assertEqual(look_entry["raw_or_derived"], "raw")
        self.assertEqual(look_entry["cursor_mode"], "none")
        self.assertIsInstance(look_entry["captured_at_monotonic_ms"], int)
        self.assertIn("T", look_entry["captured_at_iso"])
        self.assertTrue(look_media_path_exists)
        self.assertEqual(len(after_entries), 2)
        self.assertEqual({entry["event_id"] for entry in after_entries}, {"do-index-source"})
        self.assertEqual({entry["view_id"] for entry in after_entries}, {view_id})
        self.assertTrue(time_lookup["ok"])
        data = time_lookup["data"]
        self.assertEqual(data["type"], "time_near_frames")
        self.assertTrue(data["no_capture_performed"])
        self.assertFalse(data["tool_asserts_business_success"])
        near = data["frames_near_time"]
        self.assertEqual(near["schema"], "ai_control_p0_time_near_frame_query_v1")
        self.assertEqual(near["query_status"], "generated")
        self.assertGreaterEqual(near["frame_count"], 1)
        self.assertIsNotNone(near["nearest_frame"])
        self.assertTrue(nearest_media_path_exists)
        self.assertIn(near["nearest_frame"]["before_after_nearest"], {"before", "after", "nearest"})
        self.assertTrue(memory_lookup["ok"])
        self.assertEqual(memory_lookup["data"]["frames_near_time"]["query_status"], "generated")

    def test_do_post_observe_stop_when_stable_stops_before_max_frame_count(self) -> None:
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
                    "id": "look-post-observe-stable",
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
                    "id": "do-post-observe-stable",
                    "basis": {"view_id": view_id},
                    "seq": [{"t": "move", "x": 4, "y": 5, "coord": "view", "move": "instant"}],
                    "post_observe": {
                        "delay_ms": 0,
                        "frame_count": 5,
                        "interval_ms": 0,
                        "stable_threshold": 0.0,
                        "stable_frame_count": 1,
                        "stop_when_stable": True,
                    },
                },
            )

        self.assertTrue(action["ok"])
        self.assertTrue(action["data"]["ok"])
        post = action["data"]["post_observe"]
        self.assertEqual(post["request"]["frame_count"], 5)
        self.assertTrue(post["summary"]["stop_when_stable"])
        self.assertTrue(post["summary"]["stable"])
        self.assertTrue(post["summary"]["stopped_early"])
        self.assertEqual(post["summary"]["sampling_stop_reason"], "stable_window_reached")
        self.assertEqual(post["sampled_frame_count"], 2)
        self.assertEqual(post["comparison_count"], 2)
        self.assertFalse(post["tool_asserts_business_success"])
        self.assertNotIn("media_path_abs", json.dumps(post, sort_keys=True))
        self.assertEqual(len(observation.captures), 4)

    def test_schema_rejects_click_coordinates_in_do_seq(self) -> None:
        with self.assertRaises(SchemaError):
            validate_request(
                {
                    "command": "do",
                    "payload": {
                        "basis": {"view_id": "v_test"},
                        "seq": [{"t": "click", "x": 1, "y": 2, "b": "left"}],
                    },
                }
            )

    def test_schema_rejects_physical_current_screen_public_do_basis(self) -> None:
        with self.assertRaises(SchemaError):
            validate_request(
                {
                    "command": "do",
                    "payload": {
                        "basis": {"physical_current_screen": True},
                        "seq": [{"t": "wait", "ms": 1}],
                    },
                }
            )

    def test_schema_validates_post_observe_bounds(self) -> None:
        valid_request = {
            "command": "do",
            "payload": {
                "basis": {"view_id": "v_test"},
                "seq": [{"t": "wait", "ms": 1}],
                "post_observe": {"delay_ms": 1, "frame_count": 1, "interval_ms": 1},
            },
        }
        self.assertEqual(validate_request(valid_request).payload["post_observe"]["frame_count"], 1)
        self.assertNotIn("stop_when_stable", validate_request(valid_request).payload["post_observe"])
        valid_stop = {
            "command": "do",
            "payload": {
                "basis": {"view_id": "v_test"},
                "seq": [{"t": "wait", "ms": 1}],
                "post_observe": {"frame_count": 5, "stop_when_stable": True},
            },
        }
        self.assertTrue(validate_request(valid_stop).payload["post_observe"]["stop_when_stable"])
        with self.assertRaises(SchemaError):
            validate_request(
                {
                    "command": "do",
                    "payload": {
                        "basis": {"view_id": "v_test"},
                        "seq": [{"t": "wait", "ms": 1}],
                        "post_observe": {"frame_count": 1001},
                    },
                }
            )
        with self.assertRaises(SchemaError):
            validate_request(
                {
                    "command": "do",
                    "payload": {
                        "basis": {"view_id": "v_test"},
                        "seq": [{"t": "wait", "ms": 1}],
                        "post_observe": {"stop_when_stable": "yes"},
                    },
                }
            )
        with self.assertRaises(SchemaError):
            validate_request(
                {
                    "command": "do",
                    "payload": {
                        "basis": {"view_id": "v_test"},
                        "seq": [{"t": "wait", "ms": 1}],
                        "post_observe": {"include_cursor": True},
                    },
                }
            )

    def test_mcp_do_schema_exposes_post_observe_without_new_tool(self) -> None:
        self.assertEqual(MCP_TOOL_NAMES, ("screen", "look", "do"))
        do_schema = tool_schema("do")
        post_schema = do_schema["properties"]["post_observe"]
        self.assertNotIn("post_observe", do_schema["required"])
        self.assertFalse(post_schema["additionalProperties"])
        self.assertEqual(post_schema["properties"]["frame_count"]["maximum"], 1000)
        self.assertEqual(post_schema["properties"]["interval_ms"]["maximum"], 2000)
        self.assertEqual(post_schema["properties"]["stop_when_stable"]["type"], "boolean")

    def test_host_agent_discovery_v2_exposes_screen_look_do_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            discovery = write_discovery_file(
                Path(temp_dir) / "host-agent.json",
                host="127.0.0.1",
                port=8765,
                token="token",
                runs_dir="runs",
                armed=True,
            )

        self.assertEqual(discovery["schema"], "discovery_v2")
        self.assertEqual(discovery["api"], {"screen": "/screen", "look": "/look", "do": "/do"})
        self.assertEqual(discovery["screen_url"], "http://127.0.0.1:8765/screen")
        self.assertEqual(discovery["look_url"], "http://127.0.0.1:8765/look")
        self.assertEqual(discovery["do_url"], "http://127.0.0.1:8765/do")
        self.assertEqual(discovery["mouse_url"], "http://127.0.0.1:8765/mouse")

    def test_host_agent_public_readiness_maps_locked_desktop_to_embedded_code(self) -> None:
        health = {
            "can_attempt_real_control": False,
            "service_status": "locked_desktop",
            "control_blockers": ["locked_desktop"],
        }

        readiness = _host_agent_public_readiness(health, armed=True, arm_required=True)
        blocked = _host_agent_public_blocked_response(
            operation="look",
            health=health,
            armed=True,
            arm_required=True,
            caller_lock={"status": "held_by_test"},
        )

        self.assertFalse(readiness["ok"])
        self.assertEqual(readiness["code"], "DESKTOP_LOCKED")
        self.assertEqual(blocked["schema"], "ai_control_public_readiness_blocked_v1")
        self.assertEqual(blocked["code"], "DESKTOP_LOCKED")
        self.assertEqual(blocked["readiness"]["code"], "DESKTOP_LOCKED")
        self.assertEqual(blocked["operation"], "look")
        self.assertFalse(blocked["host_input_sent"])
        self.assertEqual(blocked["host_sent_event_count"], 0)
        self.assertFalse(blocked["boundary"]["business_success_judged"])

    def test_host_agent_public_readiness_maps_not_armed_without_full_health_payload(self) -> None:
        health = {
            "can_attempt_real_control": True,
            "service_status": "ok_active_default_desktop",
            "control_blockers": [],
        }

        blocked = _host_agent_public_blocked_response(operation="do", health=health, armed=False, arm_required=True)

        self.assertEqual(blocked["code"], "HOST_AGENT_NOT_ARMED")
        self.assertFalse(blocked["readiness"]["ok"])
        self.assertFalse(blocked["readiness"]["real_input_armed"])
        self.assertNotIn("health", blocked)
        self.assertFalse(blocked["host_input_sent"])
        self.assertEqual(blocked["host_sent_event_count"], 0)

    def test_host_agent_do_routes_view_move_and_click_through_internal_mouse_chain(self) -> None:
        fake_adapter = P3AHostFakeAdapter()
        visual_sessions = {
            "visual-test": {
                "adapter": fake_adapter,
                "arming_ref": "arming",
                "operator_consent_ref": "consent",
                "observations": {
                    "obs-source": {
                        "observation_id": "obs-source",
                        "screen_region": {"x": -10, "y": 20, "width": 100, "height": 80},
                        "coordinate_system": "virtual_screen_pixels",
                    }
                },
                "observation_content": {"obs-source": {"capture_content_degenerate": False}},
                "observation_review_media": {},
                "observation_cursor_review": {},
            }
        }
        protocol_views = {
            "v_host": {
                "view": {"id": "v_host", "path": "view.png", "w": 20, "h": 16, "scale_down": 5},
                "visual_session_id": "visual-test",
                "source_observation_ref": "obs-source",
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": -10,
                        "origin_y": 20,
                        "scale_x": 5,
                        "scale_y": 5,
                    }
                },
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do",
                "basis": {"view_id": "v_host"},
                "seq": [
                    {"t": "move", "x": 6, "y": 7, "coord": "view", "move": "instant"},
                    {"t": "click", "b": "left"},
                ],
            },
        )

        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 200)
        self.assertTrue(report["ok"])
        self.assertEqual(report["steps"][0]["screen"], {"x": 20, "y": 55})
        self.assertEqual(execute_payloads[0]["input_type"], "mouse_move")
        self.assertEqual(execute_payloads[0]["x"], 20)
        self.assertEqual(execute_payloads[0]["y"], 55)
        self.assertEqual(execute_payloads[1]["input_type"], "mouse_click")
        self.assertEqual(execute_payloads[1]["x"], 20)
        self.assertEqual(execute_payloads[1]["y"], 55)
        self.assertNotIn("skip_after_observation", execute_payloads[0])
        self.assertNotIn("skip_after_observation", execute_payloads[1])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_host_agent_do_can_translate_basis_point_from_view_coordinates(self) -> None:
        fake_adapter = P3AHostFakeAdapter()
        visual_sessions = {
            "visual-test": {
                "adapter": fake_adapter,
                "arming_ref": "arming",
                "operator_consent_ref": "consent",
                "observations": {
                    "obs-source": {
                        "observation_id": "obs-source",
                        "screen_region": {"x": -10, "y": 20, "width": 100, "height": 80},
                        "coordinate_system": "virtual_screen_pixels",
                    }
                },
                "observation_content": {"obs-source": {"capture_content_degenerate": False}},
                "observation_review_media": {},
                "observation_cursor_review": {},
            }
        }
        protocol_views = {
            "v_host": {
                "view": {"id": "v_host", "w": 20, "h": 16, "scale_down": 5},
                "visual_session_id": "visual-test",
                "source_observation_ref": "obs-source",
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": -10,
                        "origin_y": 20,
                        "scale_x": 5,
                        "scale_y": 5,
                    }
                },
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-basis-point",
                "basis": {"view_id": "v_host", "point": {"x": 6, "y": 7}},
                "seq": [{"t": "click", "b": "left"}],
            },
        )

        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 200)
        self.assertTrue(report["ok"])
        self.assertEqual(report["basis"]["point"], {"x": 6, "y": 7})
        self.assertEqual(report["basis"]["screen_point"], {"x": 20, "y": 55})
        self.assertEqual(report["steps"][0]["screen"], {"x": 20, "y": 55})
        self.assertEqual(execute_payloads[0]["input_type"], "mouse_click")
        self.assertEqual(execute_payloads[0]["x"], 20)
        self.assertEqual(execute_payloads[0]["y"], 55)
        self.assertFalse(report["tool_asserts_target_hit"])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_host_agent_do_rejects_basis_point_outside_view_bounds(self) -> None:
        fake_adapter = P3AHostFakeAdapter()
        visual_sessions = {
            "visual-test": {
                "adapter": fake_adapter,
                "arming_ref": "arming",
                "operator_consent_ref": "consent",
                "observations": {"obs-source": {"observation_id": "obs-source"}},
                "observation_content": {"obs-source": {"capture_content_degenerate": False}},
                "observation_review_media": {},
                "observation_cursor_review": {},
            }
        }
        protocol_views = {
            "v_host": {
                "view": {"id": "v_host", "w": 20, "h": 16, "scale_down": 5},
                "visual_session_id": "visual-test",
                "source_observation_ref": "obs-source",
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": -10,
                        "origin_y": 20,
                        "scale_x": 5,
                        "scale_y": 5,
                    }
                },
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-basis-oob",
                "basis": {"view_id": "v_host", "point": {"x": 20, "y": 0}},
                "seq": [{"t": "click", "b": "left"}],
            },
        )

        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 409)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed_step"]["failure_code"], "VIEW_POINT_OUT_OF_BOUNDS")
        self.assertFalse(execute_payloads)
        self.assertFalse(report["tool_asserts_target_hit"])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_host_agent_do_rejects_move_point_outside_view_bounds(self) -> None:
        fake_adapter = P3AHostFakeAdapter()
        visual_sessions = {
            "visual-test": {
                "adapter": fake_adapter,
                "arming_ref": "arming",
                "operator_consent_ref": "consent",
                "observations": {"obs-source": {"observation_id": "obs-source"}},
                "observation_content": {"obs-source": {"capture_content_degenerate": False}},
                "observation_review_media": {},
                "observation_cursor_review": {},
            }
        }
        protocol_views = {
            "v_host": {
                "view": {"id": "v_host", "w": 20, "h": 16, "scale_down": 5},
                "visual_session_id": "visual-test",
                "source_observation_ref": "obs-source",
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": -10,
                        "origin_y": 20,
                        "scale_x": 5,
                        "scale_y": 5,
                    }
                },
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-move-oob",
                "basis": {"view_id": "v_host"},
                "seq": [{"t": "move", "x": -1, "y": 0, "coord": "view", "move": "instant"}],
            },
        )

        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 409)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed_step"]["failure_code"], "VIEW_POINT_OUT_OF_BOUNDS")
        self.assertFalse(execute_payloads)
        self.assertFalse(report["tool_asserts_target_hit"])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_host_agent_do_fails_when_view_transform_is_missing(self) -> None:
        fake_adapter = P3AHostFakeAdapter()
        visual_sessions = {
            "visual-test": {
                "adapter": fake_adapter,
                "arming_ref": "arming",
                "operator_consent_ref": "consent",
                "observations": {"obs-source": {"observation_id": "obs-source"}},
                "observation_content": {"obs-source": {"capture_content_degenerate": False}},
                "observation_review_media": {},
                "observation_cursor_review": {},
            }
        }
        protocol_views = {
            "v_host": {
                "view": {"id": "v_host", "w": 20, "h": 16, "scale_down": 5},
                "visual_session_id": "visual-test",
                "source_observation_ref": "obs-source",
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-missing-transform",
                "basis": {"view_id": "v_host"},
                "seq": [{"t": "move", "x": 6, "y": 7, "coord": "view", "move": "instant"}],
            },
        )

        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 409)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed_step"]["failure_code"], "VIEW_TRANSFORM_UNAVAILABLE")
        self.assertFalse(execute_payloads)
        self.assertFalse(report["tool_asserts_target_hit"])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_host_agent_do_rejects_historical_review_view_as_action_basis(self) -> None:
        fake_adapter = P3AHostFakeAdapter()
        visual_sessions = {
            "visual-test": {
                "adapter": fake_adapter,
                "arming_ref": "arming",
                "operator_consent_ref": "consent",
                "observations": {"obs-historical": {"observation_id": "obs-historical"}},
                "observation_content": {"obs-historical": {"capture_content_degenerate": False}},
                "observation_review_media": {},
                "observation_cursor_review": {},
            }
        }
        protocol_views = {
            "sv_historical": {
                "view": {
                    "id": "sv_historical",
                    "w": 20,
                    "h": 10,
                    "scale_down": 2,
                    "view_is_current_action_basis": False,
                },
                "view_role": "historical_segment_review",
                "view_is_current_action_basis": False,
                "visual_session_id": "visual-test",
                "source_observation_ref": "obs-historical",
                "screen_rect": {"x": 100, "y": 50, "w": 40, "h": 20},
                "transform": {
                    "view_pixels_to_virtual_screen_pixels": {
                        "origin_x": 100,
                        "origin_y": 50,
                        "scale_x": 2,
                        "scale_y": 2,
                    }
                },
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-historical-basis",
                "basis": {"view_id": "sv_historical", "point": {"x": 3, "y": 4}},
                "seq": [{"t": "click", "b": "left"}],
            },
        )

        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 409)
        self.assertFalse(report["ok"])
        self.assertEqual(report["failed_step"]["failure_code"], "VIEW_NOT_ACTION_BASIS")
        self.assertFalse(execute_payloads)
        self.assertFalse(report["tool_asserts_target_hit"])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_host_agent_do_post_observe_uses_existing_visual_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            media_dir = Path(temp_dir)
            baseline_path = media_dir / "host-baseline.png"
            baseline_path.write_bytes(_png_bytes(100, 80, 1))
            fake_adapter = P3AHostFakeAdapter(observe_dir=media_dir)
            visual_sessions = {
                "visual-test": {
                    "adapter": fake_adapter,
                    "arming_ref": "arming",
                    "operator_consent_ref": "consent",
                    "observations": {
                        "obs-source": {
                            "observation_id": "obs-source",
                            "media_mime": "image/png",
                            "media_path_abs": str(baseline_path),
                            "screen_region": {"x": -10, "y": 20, "width": 100, "height": 80},
                            "coordinate_system": "virtual_screen_pixels",
                        }
                    },
                    "observation_content": {"obs-source": {"capture_content_degenerate": False}},
                    "observation_review_media": {},
                    "observation_cursor_review": {},
                }
            }
            protocol_views = {
                "v_host": {
                    "view": {"id": "v_host", "path": "view.png", "w": 20, "h": 16, "scale_down": 5},
                    "visual_session_id": "visual-test",
                    "source_observation_ref": "obs-source",
                    "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
                    "transform": {
                        "view_pixels_to_virtual_screen_pixels": {
                            "origin_x": -10,
                            "origin_y": 20,
                            "scale_x": 5,
                            "scale_y": 5,
                        }
                    },
                }
            }

            status, report = _host_agent_protocol_do(
                visual_sessions=visual_sessions,
                protocol_views=protocol_views,
                request={
                    "v": "V1",
                    "id": "host-do-post-observe",
                    "basis": {"view_id": "v_host"},
                    "seq": [{"t": "move", "x": 1, "y": 1, "coord": "view", "move": "instant"}],
                    "post_observe": {"delay_ms": 0, "frame_count": 2, "interval_ms": 0},
                },
            )

        observe_payloads = [payload for name, payload in fake_adapter.calls if name == "observe"]
        execute_payloads = [payload for name, payload in fake_adapter.calls if name == "execute_input"]
        self.assertEqual(status, 200)
        self.assertTrue(report["ok"])
        self.assertEqual(execute_payloads[0]["skip_after_observation"], True)
        self.assertEqual(execute_payloads[0]["after_observation_skip_reason"], "post_observe_requested")
        self.assertTrue(report["steps"][0]["post_observe_fast_path"])
        self.assertTrue(report["steps"][0]["after_observation_skipped"])
        self.assertEqual(len(observe_payloads), 2)
        self.assertEqual(observe_payloads[0]["mode"], "region")
        self.assertEqual(observe_payloads[0]["region"], {"x": -10, "y": 20, "width": 100, "height": 80})
        post = report["post_observe"]
        self.assertEqual(post["sampled_frame_count"], 2)
        self.assertEqual(post["comparison_count"], 2)
        self.assertEqual(post["baseline_frame_ref"], "obs-source")
        self.assertFalse(post["tool_asserts_business_success"])
        self.assertFalse(post["boundary"]["window_semantics_used"])
        self.assertNotIn("media_path_abs", json.dumps(post, sort_keys=True))

    def test_host_agent_http_recording_policy_defaults_can_inject_bounded_post_observe(self) -> None:
        request = {
            "v": "V1",
            "id": "host-do-default-post",
            "basis": {"view_id": "v_host"},
            "seq": [{"t": "move", "x": 1, "y": 1, "coord": "view", "move": "instant"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                write_default_tray_config_if_missing()
                enabled = _host_agent_apply_recording_policy_defaults(request)
                apply_recording_policy_settings({"action_capture_enabled": False})
                disabled = _host_agent_apply_recording_policy_defaults(request)

        self.assertIsNot(enabled, request)
        self.assertIn("post_observe", enabled)
        self.assertEqual(enabled["post_observe"]["frame_count"], 100)
        self.assertEqual(enabled["post_observe"]["interval_ms"], 100)
        self.assertFalse(enabled["post_observe"]["stop_when_stable"])
        self.assertNotIn("post_observe", disabled)
        self.assertNotIn("recording_policy_applied", enabled)
        self.assertEqual(validate_request({"command": "do", "payload": enabled}).payload["post_observe"]["frame_count"], 100)

    def test_host_agent_recording_policy_defaults_tolerate_bad_human_config_types(self) -> None:
        request = {
            "v": "V1",
            "id": "host-do-bad-config",
            "basis": {"view_id": "v_host"},
            "seq": [{"t": "move", "x": 1, "y": 1, "coord": "view", "move": "instant"}],
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                config = write_default_tray_config_if_missing()
                config_path = Path(str(config["config_file"]))
                payload = json.loads(config_path.read_text(encoding="utf-8"))
                payload["recording"]["action_capture"]["post_action_fps"] = "bad"
                payload["recording"]["action_capture"]["post_action_duration_ms"] = {}
                payload["recording"]["action_capture"]["max_post_action_frames"] = []
                config_path.write_text(json.dumps(payload), encoding="utf-8")
                enabled = _host_agent_apply_recording_policy_defaults(request)

        post = enabled["post_observe"]
        self.assertEqual(post["delay_ms"], 0)
        self.assertEqual(post["frame_count"], 100)
        self.assertEqual(post["interval_ms"], 100)
        self.assertEqual(post["stable_threshold"], 0.001)
        self.assertEqual(post["stable_frame_count"], 2)
        self.assertFalse(post["stop_when_stable"])
        self.assertEqual(validate_request({"command": "do", "payload": enabled}).payload["post_observe"], post)

    def test_host_agent_do_post_observe_missing_session_returns_full_boundary_shape(self) -> None:
        protocol_views = {
            "v_missing": {
                "view": {"id": "v_missing", "path": "view.png", "w": 20, "h": 16, "scale_down": 5},
                "visual_session_id": "visual-missing",
                "source_observation_ref": "obs-source",
                "screen_rect": {"x": -10, "y": 20, "w": 100, "h": 80},
            }
        }

        status, report = _host_agent_protocol_do(
            visual_sessions={},
            protocol_views=protocol_views,
            request={
                "v": "V1",
                "id": "host-do-post-observe-missing-session",
                "basis": {"view_id": "v_missing"},
                "seq": [1],
                "post_observe": {"delay_ms": 0, "frame_count": 2, "interval_ms": 0},
            },
        )

        self.assertEqual(status, 200)
        self.assertTrue(report["ok"])
        post = report["post_observe"]
        self.assertEqual(post["status"], "not_generated")
        self.assertEqual(post["not_generated_reason"], "visual_session_missing")
        self.assertEqual(post["sampled_frame_count"], 0)
        self.assertFalse(post["raw_media_returned"])
        self.assertFalse(post["derived_review_artifact_returned"])
        self.assertTrue(post["raw_frames_are_canonical_evidence"])
        self.assertEqual(post["input_visual_relationship_judgment"], "external_review_only")
        self.assertFalse(post["tool_asserts_semantic_change"])
        self.assertFalse(post["tool_asserts_business_success"])
        self.assertFalse(post["summary"]["tool_asserts_semantic_change"])
        self.assertFalse(post["summary"]["tool_asserts_business_success"])
        self.assertFalse(post["boundary"]["window_semantics_used"])
        self.assertNotIn("media_path_abs", json.dumps(post, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
