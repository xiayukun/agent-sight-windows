from __future__ import annotations

import argparse
import ctypes
import json
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

from ai_control.runtime_platform import is_windows, platform_system_label
from ai_control.tray.actions import emergency_stop
from ai_control.tray.state import boundary_facts


EMERGENCY_HOTKEY_SCHEMA = "ai_control_emergency_hotkey_v1"
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

LLKHF_EXTENDED = 0x01
LLKHF_LOWER_IL_INJECTED = 0x02
LLKHF_INJECTED = 0x10
LLKHF_ALTDOWN = 0x20
LLKHF_UP = 0x80
INJECTED_FLAG_MASK = LLKHF_INJECTED | LLKHF_LOWER_IL_INJECTED

VK_ESCAPE = 0x1B
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LSHIFT = 0xA0
VK_RSHIFT = 0xA1
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5

CTRL_KEYS = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
SHIFT_KEYS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}
ALT_KEYS = {VK_MENU, VK_LMENU, VK_RMENU}
TRIGGER_KEY = VK_ESCAPE


def _is_windows() -> bool:
    return is_windows()


def default_hotkey_policy() -> dict[str, Any]:
    return {
        "object_type": "AIControlEmergencyHotkeyPolicy",
        "schema": EMERGENCY_HOTKEY_SCHEMA,
        "policy_role": "human_physical_emergency_stop_hotkey",
        "enabled_when_monitor_running": True,
        "default_chord": "Ctrl+Alt+Shift+Esc",
        "trigger_key": "Esc",
        "required_modifiers": ["Ctrl", "Alt", "Shift"],
        "low_level_keyboard_hook": "WH_KEYBOARD_LL",
        "ignore_injected_events": True,
        "injected_event_flags": {
            "LLKHF_INJECTED": LLKHF_INJECTED,
            "LLKHF_LOWER_IL_INJECTED": LLKHF_LOWER_IL_INJECTED,
            "ignored_mask": INJECTED_FLAG_MASK,
        },
        "effect": {
            "write_emergency_stop_marker": True,
            "write_watchdog_stop_marker": True,
            "request_host_agent_shutdown": True,
            "uninstall_host_agent": False,
        },
        "not_started_by_status_or_describe": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def build_hotkey_description() -> dict[str, Any]:
    windows = _is_windows()
    return {
        "object_type": "AIControlEmergencyHotkeyDescription",
        "schema": EMERGENCY_HOTKEY_SCHEMA,
        "platform": "Windows" if windows else platform_system_label(),
        "available": windows,
        "policy": default_hotkey_policy(),
        "run_command": "ai-control-tray emergency-hotkey run",
        "describe_command": "ai-control-tray emergency-hotkey describe",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def low_level_key_event_is_injected(flags: int) -> bool:
    return bool(int(flags) & INJECTED_FLAG_MASK)


def key_event_action(message: int) -> str:
    if message in {WM_KEYDOWN, WM_SYSKEYDOWN}:
        return "down"
    if message in {WM_KEYUP, WM_SYSKEYUP}:
        return "up"
    return "other"


def update_pressed_keys(pressed_keys: set[int], *, vk_code: int, message: int, flags: int) -> dict[str, Any]:
    injected = low_level_key_event_is_injected(flags)
    action = key_event_action(message)
    if injected:
        return {
            "action": action,
            "injected": True,
            "pressed_keys_updated": False,
            "triggered": False,
            "reason": "injected_keyboard_event_ignored",
        }
    if action == "down":
        pressed_keys.add(int(vk_code))
    elif action == "up":
        pressed_keys.discard(int(vk_code))
    triggered = action == "down" and emergency_chord_active(pressed_keys, vk_code=int(vk_code))
    return {
        "action": action,
        "injected": False,
        "pressed_keys_updated": action in {"down", "up"},
        "triggered": triggered,
        "reason": "physical_hotkey_detected" if triggered else "no_trigger",
    }


def emergency_chord_active(pressed_keys: set[int], *, vk_code: int) -> bool:
    keys = set(pressed_keys)
    keys.add(int(vk_code))
    return (
        int(vk_code) == TRIGGER_KEY
        and bool(keys & CTRL_KEYS)
        and bool(keys & ALT_KEYS)
        and bool(keys & SHIFT_KEYS)
    )


def build_hotkey_trigger_report(
    *,
    trigger_event: dict[str, Any],
    emergency_report: dict[str, Any],
    started_at_ms: int,
    ended_at_ms: int | None = None,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlEmergencyHotkeyTriggerReport",
        "schema": EMERGENCY_HOTKEY_SCHEMA,
        "hotkey_status": "triggered",
        "trigger_source": "physical_keyboard_low_level_hook",
        "default_chord": default_hotkey_policy()["default_chord"],
        "trigger_event": trigger_event,
        "emergency_stop": emergency_report,
        "started_at_ms": started_at_ms,
        "ended_at_ms": int(ended_at_ms if ended_at_ms is not None else time.time() * 1000),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", wintypes.DWORD),
        ("pt_x", wintypes.LONG),
        ("pt_y", wintypes.LONG),
    ]


HOOKPROC_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
HOOKPROC = HOOKPROC_FACTORY(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)


class EmergencyHotkeyMonitor:
    def __init__(self, *, stop_callback: Callable[[str], dict[str, Any]] = emergency_stop) -> None:
        if not _is_windows():
            raise RuntimeError("Emergency hotkey monitor is only available on Windows")
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        self._configure_winapi()
        self.stop_callback = stop_callback
        self.pressed_keys: set[int] = set()
        self.started_at_ms = int(time.time() * 1000)
        self.trigger_report: dict[str, Any] | None = None
        self._hook_proc = HOOKPROC(self._handle_keyboard_event)
        self._hook_handle: int | None = None

    def _configure_winapi(self) -> None:
        self.user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
        self.user32.SetWindowsHookExW.restype = wintypes.HHOOK
        self.user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user32.CallNextHookEx.restype = ctypes.c_ssize_t
        self.user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
        self.user32.PeekMessageW.argtypes = [
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        # Do not bind TranslateMessage/DispatchMessage to this module's MSG
        # type. The tray GUI has its own Win32 MSG structure; ctypes function
        # argtypes live on the shared user32 function object and would otherwise
        # make the GUI message loop reject its own pointer type.
        self.user32.TranslateMessage.argtypes = None
        self.user32.DispatchMessageW.argtypes = None
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE

    def start(self) -> dict[str, Any]:
        if self._hook_handle:
            return {
                "object_type": "AIControlEmergencyHotkeyStartReport",
                "schema": EMERGENCY_HOTKEY_SCHEMA,
                "hotkey_status": "monitoring",
                "hook_installed": True,
                "already_running": True,
                "started_at_ms": self.started_at_ms,
                "policy": default_hotkey_policy(),
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }
        self._hook_handle = self.user32.SetWindowsHookExW(
            WH_KEYBOARD_LL,
            self._hook_proc,
            self.kernel32.GetModuleHandleW(None),
            0,
        )
        if not self._hook_handle:
            raise ctypes.WinError()
        return {
            "object_type": "AIControlEmergencyHotkeyStartReport",
            "schema": EMERGENCY_HOTKEY_SCHEMA,
            "hotkey_status": "monitoring",
            "hook_installed": True,
            "already_running": False,
            "started_at_ms": self.started_at_ms,
            "policy": default_hotkey_policy(),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }

    def stop(self, reason: str = "stopped") -> dict[str, Any]:
        hook_was_installed = bool(self._hook_handle)
        unhooked = False
        if self._hook_handle:
            unhooked = bool(self.user32.UnhookWindowsHookEx(self._hook_handle))
            self._hook_handle = None
        report = self._stopped_report(reason)
        report["hook_was_installed"] = hook_was_installed
        report["hook_unhooked"] = unhooked
        return report

    def run(self, *, seconds: int | None = None) -> dict[str, Any]:
        self.start()
        deadline = time.monotonic() + seconds if seconds and seconds > 0 else None
        msg = MSG()
        msg_ptr = ctypes.pointer(msg)
        try:
            while self.trigger_report is None:
                while self.user32.PeekMessageW(msg_ptr, None, 0, 0, 0x0001):
                    if msg.message == WM_QUIT:
                        return self.stop("quit_message")
                    self.user32.TranslateMessage(msg_ptr)
                    self.user32.DispatchMessageW(msg_ptr)
                if deadline is not None and time.monotonic() >= deadline:
                    return self.stop("timeout")
                time.sleep(0.01)
            return self.trigger_report
        finally:
            if self._hook_handle:
                self.stop("cleanup_after_trigger" if self.trigger_report else "cleanup")

    def _handle_keyboard_event(self, n_code: int, w_param: int, l_param: int) -> int:
        if n_code < 0:
            return int(self.user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param))
        event = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        update = update_pressed_keys(
            self.pressed_keys,
            vk_code=int(event.vkCode),
            message=int(w_param),
            flags=int(event.flags),
        )
        if update.get("triggered"):
            trigger_event = {
                "vk_code": int(event.vkCode),
                "scan_code": int(event.scanCode),
                "flags": int(event.flags),
                "message": int(w_param),
                **update,
                "llkhf_injected": False,
                "llkhf_lower_il_injected": False,
            }
            emergency = self.stop_callback("physical_hotkey_ctrl_alt_shift_escape")
            self.trigger_report = build_hotkey_trigger_report(
                trigger_event=trigger_event,
                emergency_report=emergency,
                started_at_ms=self.started_at_ms,
            )
            return 1
        return int(self.user32.CallNextHookEx(self._hook_handle, n_code, w_param, l_param))

    def _stopped_report(self, reason: str) -> dict[str, Any]:
        return {
            "object_type": "AIControlEmergencyHotkeyRunReport",
            "schema": EMERGENCY_HOTKEY_SCHEMA,
            "hotkey_status": "stopped_without_trigger",
            "stop_reason": reason,
            "started_at_ms": self.started_at_ms,
            "ended_at_ms": int(time.time() * 1000),
            "policy": default_hotkey_policy(),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }


def write_json_report(report: dict[str, Any], output: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI-Control physical emergency hotkey monitor.")
    subcommands = parser.add_subparsers(dest="command")
    describe = subcommands.add_parser("describe", description="Describe the emergency hotkey capability.")
    describe.add_argument("--output")
    run = subcommands.add_parser("run", description="Run the physical emergency hotkey monitor.")
    run.add_argument("--seconds", type=int)
    run.add_argument("--output")
    args = parser.parse_args(argv)
    command = args.command or "describe"
    if command == "describe":
        write_json_report(build_hotkey_description(), getattr(args, "output", None))
        return 0
    started_at_ms = int(time.time() * 1000)
    try:
        monitor = EmergencyHotkeyMonitor()
        report = monitor.run(seconds=getattr(args, "seconds", None))
        write_json_report(report, getattr(args, "output", None))
        return 0
    except Exception as exc:
        report = {
            "object_type": "AIControlEmergencyHotkeyRunFailure",
            "schema": EMERGENCY_HOTKEY_SCHEMA,
            "hotkey_status": "failed",
            "started_at_ms": started_at_ms,
            "ended_at_ms": int(time.time() * 1000),
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        write_json_report(report, getattr(args, "output", None))
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
