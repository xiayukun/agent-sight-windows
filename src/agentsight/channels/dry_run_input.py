from __future__ import annotations

import time
from typing import Any

from agentsight.channels.base import ChannelFailure
from agentsight.channels.key_text import key_text_summary, validate_key_text_stream
from agentsight.channels.keyboard_events import is_keyboard_action, keyboard_action_summary
from agentsight.channels.pointer_events import is_mouse_action, mouse_action_summary


P1_MOUSE_INPUT_TYPES = [
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_button_down",
    "mouse_button_up",
    "mouse_drag",
    "mouse_scroll",
]


class DryRunInputChannel:
    name = "dry_run_input"
    channel_type = "input"
    implementation = "no_host_input_dry_run"

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": "available",
            "implementation": self.implementation,
            "source_kind": "test_only",
            "execution_mode": "dry_run",
            "real_input": False,
            "input_types": [
                "wait",
                *P1_MOUSE_INPUT_TYPES,
                "key_text_stream",
                "key_press",
                "key_chord",
                "key_down",
                "key_up",
            ],
            "input_executed": False,
            "host_input_executed": False,
        }

    def execute(self, input_event: dict[str, Any]) -> dict[str, Any]:
        self._validate(input_event)
        result = {
            "channel_ref": self.name,
            "input_channel_ref": self.name,
            "implementation": self.implementation,
            "result": "dry_run",
            "input_executed": False,
            "host_input_executed": False,
            "host_input_sent": False,
            "sent_event_count": 0,
            "host_sent_event_count": 0,
            "duration_ms": input_event.get("payload", {}).get("duration_ms", 0),
            "executed_at": time.time(),
            "stopped_input": False,
            "released_inputs": True,
            "release_result": "not_required",
        }
        if input_event.get("input_type") == "key_text_stream":
            text = validate_key_text_stream(input_event.get("payload", {}).get("text"))
            result.update(key_text_summary(text))
            result["requested_event_count"] = len(text) * 2
        if is_keyboard_action(input_event.get("input_type")):
            summary = keyboard_action_summary(input_event.get("input_type"), input_event.get("payload", {}))
            result.update(summary)
            if summary.get("intentional_hold"):
                result["released_inputs"] = False
                result["release_result"] = "intentional_key_hold"
                result["held_keys_after_action"] = [summary["key"]]
                result["pressed_keys_unreleased"] = [summary["key"]]
        if is_mouse_action(input_event.get("input_type")):
            result.update(mouse_action_summary(input_event.get("input_type"), input_event.get("payload", {})))
        return result

    def _validate(self, input_event: dict[str, Any]) -> None:
        payload = input_event.get("payload", {})
        input_type = input_event.get("input_type")
        if is_mouse_action(input_type):
            try:
                mouse_action_summary(input_type, payload)
            except ValueError as exc:
                failure_code = "INPUT_COORDINATE_OUT_OF_SCOPE" if "coordinate" in str(exc) else "INPUT_EVENT_UNSUPPORTED"
                raise ChannelFailure(
                    failure_code,
                    stage="DryRunInputChannel.validate",
                    detail=str(exc),
                    retryable=False,
                    channel_ref=self.name,
                    channel_type=self.channel_type,
                    implementation=self.implementation,
                ) from exc
        if input_type == "key_text_stream":
            try:
                validate_key_text_stream(payload.get("text"))
            except ValueError as exc:
                raise ChannelFailure(
                    "INPUT_EVENT_UNSUPPORTED",
                    stage="DryRunInputChannel.validate",
                    detail=str(exc),
                    retryable=False,
                    channel_ref=self.name,
                    channel_type=self.channel_type,
                    implementation=self.implementation,
                ) from exc
        if is_keyboard_action(input_type):
            try:
                keyboard_action_summary(input_type, payload)
            except ValueError as exc:
                raise ChannelFailure(
                    "INPUT_EVENT_UNSUPPORTED",
                    stage="DryRunInputChannel.validate",
                    detail=str(exc),
                    retryable=False,
                    channel_ref=self.name,
                    channel_type=self.channel_type,
                    implementation=self.implementation,
                ) from exc
