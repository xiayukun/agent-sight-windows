from __future__ import annotations

import time
import uuid
from typing import Any

from agentsight.evidence.store import EvidenceReplayService


P1_MOUSE_INPUT_TYPES = [
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_button_down",
    "mouse_button_up",
    "mouse_drag",
    "mouse_scroll",
]


class MockObservationChannel:
    name = "mock_screen"
    channel_type = "observation"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available",
            "implementation": "mock_screen_text",
            "source_kind": "test_only",
            "modes": ["fullscreen", "region", "after_action", "sequence"],
            "supports_sequence": True,
            "supports_change_observation": True,
            "max_frames": 5,
            "max_sequence_duration_ms": 1000,
        }

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        observation_id = f"obs-{uuid.uuid4().hex[:10]}"
        mode = payload.get("mode", "fullscreen")
        media_ref = evidence.write_media_text(f"{observation_id}.txt", f"mock observation mode={mode}")
        frame: dict[str, Any] = {
            "object_type": "ObservationFrame",
            "observation_id": observation_id,
            "mode": mode,
            "timestamp": time.time(),
            "media_ref": media_ref,
            "channel_ref": self.name,
            "width": 20,
            "height": 10,
            "screen_region": payload.get("region", {"x": 0, "y": 0, "width": 20, "height": 10}),
            "coordinate_system": "mock_pixels",
        }
        if "region" in payload:
            frame["region"] = payload["region"]
        if "after_action_ref" in payload:
            frame["after_action_ref"] = payload["after_action_ref"]
        return frame


class MockInputChannel:
    name = "mock_input"
    channel_type = "input"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available",
            "implementation": "mock_input",
            "source_kind": "test_only",
            "execution_mode": "mock",
            "real_input": False,
            "input_types": ["wait", *P1_MOUSE_INPUT_TYPES, "key_text_stream", "key_press", "key_chord"],
            "input_executed": True,
        }

    def execute(self, input_event: dict[str, Any]) -> dict[str, Any]:
        return {
            "channel_ref": self.name,
            "input_channel_ref": self.name,
            "implementation": "mock_input",
            "result": "simulated",
            "input_executed": True,
            "sent_event_count": 0,
            "stopped_input": False,
            "released_inputs": True,
            "release_result": "not_required",
        }
