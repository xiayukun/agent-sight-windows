from __future__ import annotations

import time
from typing import Any

from ai_control.channels.base import ChannelFailure
from ai_control.channels.key_text import validate_key_text_stream
from ai_control.channels.keyboard_events import is_keyboard_action, keyboard_action_summary
from ai_control.channels.pointer_events import is_mouse_action, mouse_action_summary
from ai_control.runtime_platform import is_windows
from ai_control.channels.windows_software.sendinput_backend import Win32InputBackend


P1_MOUSE_INPUT_TYPES = [
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_button_down",
    "mouse_button_up",
    "mouse_drag",
    "mouse_scroll",
]


def _is_windows() -> bool:
    return is_windows()


class WindowsSoftwareInputChannel:
    channel_type = "input"

    def __init__(
        self,
        *,
        enabled: bool = False,
        backend: Any | None = None,
        name: str | None = None,
        active_arming_ref: str | None = None,
        operator_consent_ref: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.backend = backend
        self.name = name or ("windows_software_input" if enabled else "windows_software_input_disabled")
        self.active_arming_ref = active_arming_ref
        self.operator_consent_ref = operator_consent_ref
        self.implementation = "ctypes_win32_sendinput" if enabled else "ctypes_win32_sendinput_disabled"

    def describe(self) -> dict[str, Any]:
        is_windows = _is_windows()
        status = "available" if self.enabled and is_windows else "disabled"
        return {
            "name": self.name,
            "type": self.channel_type,
            "status": status,
            "implementation": self.implementation,
            "source_kind": "software_input",
            "execution_mode": "real" if status == "available" else "disabled",
            "real_input": True,
            "platform_supported": is_windows,
            "requires_explicit_enable": not self.enabled,
            "requires_arming": status == "available",
            "arming_state": "armed" if self.active_arming_ref else "not_armed",
            "active_arming_ref": self.active_arming_ref,
            "operator_consent_ref": self.operator_consent_ref,
            "input_types": [
                "wait",
                *P1_MOUSE_INPUT_TYPES,
                "key_text_stream",
                "key_press",
                "key_chord",
                "key_down",
                "key_up",
            ],
            "mouse_coordinate_system": "virtual_screen_pixels",
            "supported_mouse_coordinate_systems": ["virtual_screen_pixels", "monitor_pixels"],
            "supports_negative_virtual_coordinates": True,
            "sendinput_absolute_mapping": "MOUSEEVENTF_ABSOLUTE|MOUSEEVENTF_VIRTUALDESK over GetSystemMetrics virtual screen",
            "input_executed": False,
            "host_input_possible": status == "available",
            "unavailable_reason": None if status == "available" else "real_input_requires_explicit_enablement",
        }

    def execute(self, input_event: dict[str, Any]) -> dict[str, Any]:
        descriptor = self.describe()
        if descriptor["status"] != "available":
            raise ChannelFailure(
                "INPUT_CHANNEL_DISABLED",
                stage="WindowsSoftwareInputChannel.execute",
                detail="windows software input is declared but disabled until explicit user enablement",
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation=self.implementation,
            )
        self._validate(input_event)
        backend = self.backend or Win32InputBackend()
        started_at = time.time()
        try:
            result = backend.execute(input_event)
        except ChannelFailure:
            raise
        except Exception as exc:  # pragma: no cover - host backend dependent
            raise ChannelFailure(
                "INPUT_EXECUTION_FAILED",
                stage="WindowsSoftwareInputChannel.execute",
                detail=str(exc),
                retryable=False,
                channel_ref=self.name,
                channel_type=self.channel_type,
                implementation=self.implementation,
            ) from exc
        result.setdefault("channel_ref", self.name)
        result.setdefault("input_channel_ref", self.name)
        result.setdefault("implementation", self.implementation)
        result.setdefault("input_executed", True)
        result.setdefault("host_input_executed", bool(result.get("sent_event_count", 0)))
        result.setdefault("duration_ms", int((time.time() - started_at) * 1000))
        result.setdefault("stopped_input", False)
        result.setdefault("released_inputs", True)
        result.setdefault("release_result", "released")
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
                    stage="WindowsSoftwareInputChannel.validate",
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
                    stage="WindowsSoftwareInputChannel.validate",
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
                    stage="WindowsSoftwareInputChannel.validate",
                    detail=str(exc),
                    retryable=False,
                    channel_ref=self.name,
                    channel_type=self.channel_type,
                    implementation=self.implementation,
                ) from exc
