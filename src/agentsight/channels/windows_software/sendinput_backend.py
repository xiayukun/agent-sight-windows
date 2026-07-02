from __future__ import annotations

import ctypes
import time
from typing import Any
from ctypes import wintypes

from agentsight.channels.base import ChannelFailure
from agentsight.channels.key_text import key_text_summary, validate_key_text_stream
from agentsight.channels.keyboard_events import is_keyboard_action, keyboard_action_summary
from agentsight.channels.pointer_events import is_mouse_action, mouse_action_summary


INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else wintypes.DWORD
VK_BY_KEY = {
    **{chr(code): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(value): 0x30 + value for value in range(10)},
    "ENTER": 0x0D,
    "TAB": 0x09,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "SPACE": 0x20,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "UP": 0x26,
    "DOWN": 0x28,
    "HOME": 0x24,
    "END": 0x23,
    "PAGE_UP": 0x21,
    "PAGE_DOWN": 0x22,
    "ALT": VK_MENU,
    "CTRL": 0x11,
    "SHIFT": VK_SHIFT,
    "WIN": 0x5B,
    **{f"F{value}": 0x6F + value for value in range(1, 13)},
}


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class InputUnion(ctypes.Union):
    _fields_ = [("mi", MouseInput), ("ki", KeyboardInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]


class Win32InputBackend:
    implementation = "ctypes_win32_sendinput"

    def execute(self, input_event: dict[str, Any]) -> dict[str, Any]:
        input_type = input_event.get("input_type")
        started_at = time.time()
        if input_type == "wait":
            duration_ms = int(input_event.get("payload", {}).get("duration_ms", 0) or 0)
            if duration_ms > 0:
                time.sleep(duration_ms / 1000)
            return self._result(
                started_at,
                requested_event_count=0,
                sent_event_count=0,
                host_input_executed=False,
                result="waited",
                release_result="not_required",
            )
        if is_mouse_action(input_type):
            return self._mouse_action(input_event, started_at=started_at)
        if input_type == "key_text_stream":
            return self._key_text_stream(input_event, started_at=started_at)
        if is_keyboard_action(input_type):
            return self._keyboard_action(input_event, started_at=started_at)
        raise ChannelFailure(
            "INPUT_EVENT_UNSUPPORTED",
            stage="Win32InputBackend.execute",
            detail=f"unsupported input_type: {input_type!r}",
            retryable=False,
            implementation=self.implementation,
        )

    def _mouse_action(self, input_event: dict[str, Any], *, started_at: float) -> dict[str, Any]:
        try:
            summary = mouse_action_summary(input_event.get("input_type"), input_event.get("payload", {}))
        except ValueError as exc:
            failure_code = "INPUT_COORDINATE_OUT_OF_SCOPE" if "coordinate" in str(exc) else "INPUT_EVENT_UNSUPPORTED"
            raise ChannelFailure(
                failure_code,
                stage="Win32InputBackend.mouse_action",
                detail=str(exc),
                retryable=False,
                implementation=self.implementation,
            ) from exc
        plan = self._mouse_action_plan(summary)
        events = [item["event"] for item in plan]
        sent, result_status = self._send(events)
        release_plan = self._unreleased_mouse_buttons_after(plan, sent)
        release_sent = 0
        released_inputs = True
        release_result = "released"
        released_buttons: list[str] = []
        if result_status != "succeeded":
            if release_plan:
                released_buttons = release_plan
                released_inputs, release_result, release_sent = self._release_mouse_buttons(release_plan)
            else:
                release_result = "not_required"
        result = self._result(
            started_at,
            requested_event_count=len(events),
            sent_event_count=sent + release_sent,
            host_input_executed=(sent + release_sent) > 0,
            result="sent" if result_status == "succeeded" else "partial_sent",
            result_status=result_status,
            released_inputs=released_inputs,
            release_result=release_result,
        )
        result.update(summary)
        result["sendinput_event_kind"] = "INPUT_MOUSE"
        result["primary_sent_event_count"] = sent
        result["release_requested_event_count"] = len(release_plan)
        result["release_sent_event_count"] = release_sent
        result["released_buttons"] = released_buttons
        result["pressed_buttons_unreleased"] = [] if released_inputs else release_plan[release_sent:]
        result["coordinate_integrity"] = self._mouse_coordinate_integrity(plan)
        result["host_sent_event_count"] = sent + release_sent
        result["sent_event_count"] = sent + release_sent
        return result

    def _mouse_action_plan(self, summary: dict[str, Any]) -> list[dict[str, Any]]:
        input_type = summary["input_type"]
        if input_type == "mouse_move":
            return [self._mouse_move_plan_item(summary["requested_coordinates"])]
        if input_type == "mouse_click":
            return [
                self._mouse_move_plan_item(summary["requested_coordinates"]),
                self._mouse_button_plan_item(str(summary["button"]), "down"),
                self._mouse_button_plan_item(str(summary["button"]), "up"),
            ]
        if input_type == "mouse_double_click":
            return [
                self._mouse_move_plan_item(summary["requested_coordinates"]),
                self._mouse_button_plan_item(str(summary["button"]), "down"),
                self._mouse_button_plan_item(str(summary["button"]), "up"),
                self._mouse_button_plan_item(str(summary["button"]), "down"),
                self._mouse_button_plan_item(str(summary["button"]), "up"),
            ]
        if input_type == "mouse_button_down":
            return [
                self._mouse_move_plan_item(summary["requested_coordinates"]),
                self._mouse_button_plan_item(str(summary["button"]), "down"),
            ]
        if input_type == "mouse_button_up":
            return [
                self._mouse_move_plan_item(summary["requested_coordinates"]),
                self._mouse_button_plan_item(str(summary["button"]), "up"),
            ]
        if input_type == "mouse_drag":
            return [
                self._mouse_move_plan_item(summary["requested_coordinates"]),
                self._mouse_button_plan_item(str(summary["button"]), "down"),
                self._mouse_move_plan_item(summary["target_coordinates"]),
                self._mouse_button_plan_item(str(summary["button"]), "up"),
            ]
        if input_type == "mouse_scroll":
            plan = [self._mouse_move_plan_item(summary["requested_coordinates"])]
            if summary.get("wheel_delta"):
                plan.append(self._mouse_wheel_plan_item(MOUSEEVENTF_WHEEL, int(summary["wheel_delta"]), "vertical"))
            if summary.get("horizontal_wheel_delta"):
                plan.append(
                    self._mouse_wheel_plan_item(MOUSEEVENTF_HWHEEL, int(summary["horizontal_wheel_delta"]), "horizontal")
                )
            return plan
        raise ChannelFailure(
            "INPUT_EVENT_UNSUPPORTED",
            stage="Win32InputBackend.mouse_action_plan",
            detail=f"unsupported mouse input_type: {input_type!r}",
            retryable=False,
            implementation=self.implementation,
        )

    def _key_text_stream(self, input_event: dict[str, Any], *, started_at: float) -> dict[str, Any]:
        try:
            text = validate_key_text_stream(input_event.get("payload", {}).get("text"))
        except ValueError as exc:
            raise ChannelFailure(
                "INPUT_EVENT_UNSUPPORTED",
                stage="Win32InputBackend.key_text_stream",
                detail=str(exc),
                retryable=False,
                implementation=self.implementation,
            ) from exc
        events = []
        for char in text:
            code = ord(char)
            events.append(self._key_event(code, 0))
            events.append(self._key_event(code, KEYEVENTF_KEYUP))
        try:
            sent, result_status = self._send(events)
            input_path_used = "sendinput_unicode"
            primary_failure: ChannelFailure | None = None
        except ChannelFailure as exc:
            primary_failure = exc
            events = self._text_virtual_key_events(text)
            try:
                sent, result_status = self._send(events)
                input_path_used = "sendinput_virtual_key_fallback"
            except ChannelFailure as fallback_exc:
                raise ChannelFailure(
                    fallback_exc.failure_code,
                    stage=fallback_exc.stage,
                    detail=(
                        f"{fallback_exc.detail}; primary_unicode_failure_code={primary_failure.failure_code}; "
                        f"primary_unicode_detail={primary_failure.detail}"
                    ),
                    retryable=fallback_exc.retryable,
                    implementation=self.implementation,
                ) from fallback_exc
        released_inputs = True
        release_result = "released"
        if result_status != "succeeded":
            release_result = "not_required"
            if sent % 2 == 1 and sent < len(events):
                char_index = sent // 2
                released_inputs, release_result, release_sent = self._release_key(ord(text[char_index]))
                sent += release_sent
            else:
                released_inputs = True
        result = self._result(
            started_at,
            requested_event_count=len(events),
            sent_event_count=sent,
            host_input_executed=sent > 0,
            result="sent" if result_status == "succeeded" else "partial_sent",
            result_status=result_status,
            released_inputs=released_inputs,
            release_result=release_result,
        )
        result.update(key_text_summary(text))
        result["input_path_used"] = input_path_used
        result["key_event_encoding"] = "unicode_scan_code" if input_path_used == "sendinput_unicode" else "virtual_key"
        result["primary_input_path_failed"] = bool(primary_failure)
        if primary_failure:
            result["primary_failure_code"] = primary_failure.failure_code
            result["primary_failure_stage"] = primary_failure.stage
            result["primary_failure_detail"] = primary_failure.detail
        return result

    def _keyboard_action(self, input_event: dict[str, Any], *, started_at: float) -> dict[str, Any]:
        try:
            summary = keyboard_action_summary(input_event.get("input_type"), input_event.get("payload", {}))
        except ValueError as exc:
            raise ChannelFailure(
                "INPUT_EVENT_UNSUPPORTED",
                stage="Win32InputBackend.keyboard_action",
                detail=str(exc),
                retryable=False,
                implementation=self.implementation,
            ) from exc
        planned_events = summary["event_sequence"]
        events = [self._vk_key_event(str(item["key"]), str(item["action"])) for item in planned_events]
        sent, result_status = self._send(events)
        release_plan = self._unreleased_keys_after(planned_events, sent)
        release_sent = 0
        released_inputs = True
        release_result = "released"
        released_keys: list[str] = []
        if result_status != "succeeded":
            if release_plan:
                released_keys = release_plan
                released_inputs, release_result, release_sent = self._release_vk_keys(release_plan)
            else:
                release_result = "not_required"
        result = self._result(
            started_at,
            requested_event_count=len(events),
            sent_event_count=sent + release_sent,
            host_input_executed=(sent + release_sent) > 0,
            result="sent" if result_status == "succeeded" else "partial_sent",
            result_status=result_status,
            released_inputs=released_inputs,
            release_result=release_result,
        )
        result.update(summary)
        result["sendinput_event_kind"] = "INPUT_KEYBOARD"
        result["key_event_encoding"] = "virtual_key"
        result["primary_sent_event_count"] = sent
        result["release_requested_event_count"] = len(release_plan)
        result["release_sent_event_count"] = release_sent
        result["released_keys"] = released_keys
        if input_event.get("input_type") == "key_down" and result_status == "succeeded":
            held_key = str(summary["key"])
            result["released_inputs"] = False
            result["release_result"] = "intentional_key_hold"
            result["intentional_hold"] = True
            result["held_keys_after_action"] = [held_key]
            result["pressed_keys_unreleased"] = [held_key]
        else:
            result["pressed_keys_unreleased"] = [] if released_inputs else release_plan[release_sent:]
            result["held_keys_after_action"] = []
        result["host_sent_event_count"] = sent + release_sent
        result["sent_event_count"] = sent + release_sent
        return result

    def _send(self, events: list[Input]) -> tuple[int, str]:
        if not events:
            return 0, "succeeded"
        array_type = Input * len(events)
        user32 = self._user32()
        user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int)
        user32.SendInput.restype = wintypes.UINT
        event_array = array_type(*events)
        sent = user32.SendInput(len(events), event_array, ctypes.sizeof(Input))
        if sent == 0:
            last_error = ctypes.get_last_error()
            error_text = ctypes.FormatError(last_error).strip() if last_error else "no extended error from GetLastError"
            raise ChannelFailure(
                "INPUT_EXECUTION_FAILED",
                stage="Win32SendInputBackend.SendInput",
                detail=f"SendInput sent {sent}/{len(events)} events; last_error={last_error}; error_text={error_text}",
                retryable=False,
                implementation=self.implementation,
            )
        if sent != len(events):
            return int(sent), "partial_failed"
        return int(sent), "succeeded"

    def _virtual_screen_metrics(self) -> dict[str, Any]:
        user32 = self._user32()
        x = int(user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
        y = int(user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
        width = int(user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
        height = int(user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        source = "GetSystemMetrics(SM_X/Y/CXV/CYVIRTUALSCREEN)"
        if width <= 1 or height <= 1:
            x = 0
            y = 0
            width = int(user32.GetSystemMetrics(SM_CXSCREEN))
            height = int(user32.GetSystemMetrics(SM_CYSCREEN))
            source = "fallback:GetSystemMetrics(SM_CX/CYSCREEN)"
        if width <= 1 or height <= 1:
            raise ChannelFailure(
                "INPUT_COORDINATE_OUT_OF_SCOPE",
                stage="Win32InputBackend.coordinates",
                detail="screen metrics unavailable",
                retryable=False,
                implementation=self.implementation,
            )
        return {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "right_exclusive": x + width,
            "bottom_exclusive": y + height,
            "source": source,
        }

    def _absolute_coordinates_with_metrics(self, x: int, y: int) -> tuple[int, int, dict[str, Any]]:
        metrics = self._virtual_screen_metrics()
        if x < metrics["x"] or y < metrics["y"] or x >= metrics["right_exclusive"] or y >= metrics["bottom_exclusive"]:
            raise ChannelFailure(
                "INPUT_COORDINATE_OUT_OF_SCOPE",
                stage="Win32InputBackend.coordinates",
                detail=f"point ({x},{y}) is outside virtual screen metrics {metrics}",
                retryable=False,
                implementation=self.implementation,
            )
        absolute_x = int((x - metrics["x"]) * 65535 / (metrics["width"] - 1))
        absolute_y = int((y - metrics["y"]) * 65535 / (metrics["height"] - 1))
        return absolute_x, absolute_y, metrics

    def _absolute_coordinates(self, x: int, y: int) -> tuple[int, int]:
        absolute_x, absolute_y, _metrics = self._absolute_coordinates_with_metrics(x, y)
        return absolute_x, absolute_y

    def _mouse_event(self, x: int, y: int, flags: int, data: int = 0) -> Input:
        return Input(type=INPUT_MOUSE, union=InputUnion(mi=MouseInput(x, y, data & 0xFFFFFFFF, flags, 0, 0)))

    def _mouse_move_plan_item(self, point: dict[str, int]) -> dict[str, Any]:
        abs_x, abs_y, metrics = self._absolute_coordinates_with_metrics(int(point["x"]), int(point["y"]))
        return {
            "event": self._mouse_event(abs_x, abs_y, MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK | MOUSEEVENTF_MOVE),
            "kind": "move",
            "point": {"x": int(point["x"]), "y": int(point["y"])},
            "absolute_point": {"x": abs_x, "y": abs_y},
            "virtual_screen_metrics": metrics,
            "coordinate_space": "virtual_screen_pixels",
            "sendinput_absolute_space": "virtual_desktop_absolute_0_65535",
        }

    def _mouse_coordinate_integrity(self, plan: list[dict[str, Any]]) -> dict[str, Any]:
        move_items = [item for item in plan if item.get("kind") == "move"]
        return {
            "schema": "agentsight_p1c_coordinate_integrity_v1",
            "input_coordinate_space": "virtual_screen_pixels",
            "sendinput_absolute_coordinate_space": "virtual_desktop_absolute_0_65535",
            "sendinput_uses_virtual_desktop": True,
            "mouse_move_flags": ["MOUSEEVENTF_MOVE", "MOUSEEVENTF_ABSOLUTE", "MOUSEEVENTF_VIRTUALDESK"],
            "virtual_screen_metrics": move_items[0].get("virtual_screen_metrics") if move_items else None,
            "normalized_points": [
                {
                    "point": item.get("point"),
                    "absolute_point": item.get("absolute_point"),
                    "coordinate_space": item.get("coordinate_space"),
                }
                for item in move_items
            ],
            "tool_generated_coordinates": False,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def _mouse_button_plan_item(self, button: str, action: str) -> dict[str, Any]:
        flag = self._mouse_button_flag(button, action)
        return {
            "event": self._mouse_event(0, 0, flag),
            "kind": "button",
            "button": button,
            "action": action,
        }

    def _mouse_wheel_plan_item(self, flag: int, delta: int, axis: str) -> dict[str, Any]:
        return {
            "event": self._mouse_event(0, 0, flag, delta),
            "kind": "wheel",
            "axis": axis,
            "delta": delta,
        }

    def _mouse_button_flag(self, button: str, action: str) -> int:
        flags = {
            ("left", "down"): MOUSEEVENTF_LEFTDOWN,
            ("left", "up"): MOUSEEVENTF_LEFTUP,
            ("right", "down"): MOUSEEVENTF_RIGHTDOWN,
            ("right", "up"): MOUSEEVENTF_RIGHTUP,
            ("middle", "down"): MOUSEEVENTF_MIDDLEDOWN,
            ("middle", "up"): MOUSEEVENTF_MIDDLEUP,
        }
        try:
            return flags[(button, action)]
        except KeyError as exc:
            raise ChannelFailure(
                "INPUT_EVENT_UNSUPPORTED",
                stage="Win32InputBackend.mouse_button_flag",
                detail=f"unsupported mouse button/action: {button!r}/{action!r}",
                retryable=False,
                implementation=self.implementation,
            ) from exc

    def _key_event(self, scan_code: int, flags: int) -> Input:
        return Input(type=INPUT_KEYBOARD, union=InputUnion(ki=KeyboardInput(0, scan_code, KEYEVENTF_UNICODE | flags, 0, 0)))

    def _vk_key_event(self, key: str, action: str) -> Input:
        if key not in VK_BY_KEY:
            raise ChannelFailure(
                "INPUT_EVENT_UNSUPPORTED",
                stage="Win32InputBackend.vk_key_event",
                detail=f"unsupported virtual key: {key!r}",
                retryable=False,
                implementation=self.implementation,
            )
        flags = KEYEVENTF_KEYUP if action == "up" else 0
        return self._vk_key_code_event(VK_BY_KEY[key], flags)

    def _vk_key_code_event(self, vk_code: int, flags: int = 0) -> Input:
        return Input(type=INPUT_KEYBOARD, union=InputUnion(ki=KeyboardInput(vk_code, 0, flags, 0, 0)))

    def _text_virtual_key_events(self, text: str) -> list[Input]:
        user32 = self._user32()
        user32.VkKeyScanW.argtypes = (wintypes.WCHAR,)
        user32.VkKeyScanW.restype = ctypes.c_short
        events: list[Input] = []
        for char in text:
            mapped = int(user32.VkKeyScanW(char))
            if mapped == -1:
                raise ChannelFailure(
                    "INPUT_EVENT_UNSUPPORTED",
                    stage="Win32InputBackend.text_virtual_key_events",
                    detail=f"character cannot be mapped to a virtual key: U+{ord(char):04X}",
                    retryable=False,
                    implementation=self.implementation,
                )
            vk_code = mapped & 0xFF
            shift_state = (mapped >> 8) & 0xFF
            modifiers = self._vk_shift_modifiers(shift_state)
            events.extend(self._vk_key_code_event(modifier, 0) for modifier in modifiers)
            events.append(self._vk_key_code_event(vk_code, 0))
            events.append(self._vk_key_code_event(vk_code, KEYEVENTF_KEYUP))
            events.extend(self._vk_key_code_event(modifier, KEYEVENTF_KEYUP) for modifier in reversed(modifiers))
        return events

    def _vk_shift_modifiers(self, shift_state: int) -> list[int]:
        modifiers: list[int] = []
        if shift_state & 0x01:
            modifiers.append(VK_SHIFT)
        if shift_state & 0x02:
            modifiers.append(VK_CONTROL)
        if shift_state & 0x04:
            modifiers.append(VK_MENU)
        return modifiers

    def _release_mouse(self, up_flag: int) -> tuple[bool, str, int]:
        try:
            sent, status = self._send([self._mouse_event(0, 0, up_flag)])
        except ChannelFailure:
            return False, "failed", 0
        if sent == 1 and status == "succeeded":
            return True, "emergency_released", sent
        return False, "emergency_partial_failed", sent

    def _release_mouse_buttons(self, buttons: list[str]) -> tuple[bool, str, int]:
        try:
            sent, status = self._send([self._mouse_button_plan_item(button, "up")["event"] for button in buttons])
        except ChannelFailure:
            return False, "failed", 0
        if sent == len(buttons) and status == "succeeded":
            return True, "emergency_released", sent
        return False, "emergency_partial_failed", sent

    def _release_key(self, scan_code: int) -> tuple[bool, str, int]:
        try:
            sent, status = self._send([self._key_event(scan_code, KEYEVENTF_KEYUP)])
        except ChannelFailure:
            return False, "failed", 0
        if sent == 1 and status == "succeeded":
            return True, "emergency_released", sent
        return False, "emergency_partial_failed", sent

    def _release_vk_keys(self, keys: list[str]) -> tuple[bool, str, int]:
        try:
            sent, status = self._send([self._vk_key_event(key, "up") for key in keys])
        except ChannelFailure:
            return False, "failed", 0
        if sent == len(keys) and status == "succeeded":
            return True, "emergency_released", sent
        return False, "emergency_partial_failed", sent

    def _unreleased_keys_after(self, planned_events: list[dict[str, str]], sent: int) -> list[str]:
        pressed: list[str] = []
        for item in planned_events[:sent]:
            key = item["key"]
            action = item["action"]
            if action == "down":
                pressed.append(key)
            elif action == "up" and key in pressed:
                pressed.remove(key)
        return list(reversed(pressed))

    def _unreleased_mouse_buttons_after(self, plan: list[dict[str, Any]], sent: int) -> list[str]:
        pressed: list[str] = []
        for item in plan[:sent]:
            if item.get("kind") != "button":
                continue
            button = str(item.get("button"))
            action = str(item.get("action"))
            if action == "down":
                pressed.append(button)
            elif action == "up" and button in pressed:
                pressed.remove(button)
        return list(reversed(pressed))

    def _user32(self) -> Any:
        try:
            return ctypes.WinDLL("user32", use_last_error=True)
        except AttributeError as exc:  # pragma: no cover - non-Windows safeguard
            raise ChannelFailure(
                "INPUT_EXECUTION_UNAVAILABLE",
                stage="Win32InputBackend.user32",
                detail="Win32 user32.dll is not available on this platform",
                retryable=False,
                implementation=self.implementation,
            ) from exc

    def _result(
        self,
        started_at: float,
        *,
        requested_event_count: int,
        sent_event_count: int,
        host_input_executed: bool,
        result: str,
        result_status: str = "succeeded",
        released_inputs: bool = True,
        release_result: str = "released",
    ) -> dict[str, Any]:
        return {
            "implementation": self.implementation,
            "result": result,
            "result_status": result_status,
            "input_executed": True,
            "host_input_executed": host_input_executed,
            "host_api": "SendInput",
            "requested_event_count": requested_event_count,
            "host_sent_event_count": sent_event_count,
            "sent_event_count": sent_event_count,
            "duration_ms": int((time.time() - started_at) * 1000),
            "executed_at": time.time(),
            "stopped_input": False,
            "released_inputs": released_inputs,
            "release_result": release_result,
        }


Win32SendInputBackend = Win32InputBackend
