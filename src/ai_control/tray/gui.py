from __future__ import annotations

import argparse
import ctypes
import json
import locale
import os
import sys
import time
import traceback
from ctypes import wintypes
from pathlib import Path
from subprocess import DEVNULL, Popen
from typing import Any

from ai_control.host_agent.interactive_task import completed_process_report, run_schtasks
from ai_control.runtime_platform import is_windows
from ai_control.tray.actions import allow_ai_control, clear_emergency, emergency_stop, pause_ai_control, stop_ai_control
from ai_control.tray.hotkey import EmergencyHotkeyMonitor, build_hotkey_description
from ai_control.tray.state import (
    apply_recording_policy_settings,
    boundary_facts,
    default_agent_dir,
    default_tray_config_file,
    load_tray_status,
    recording_policy_flag_enabled,
    write_default_tray_config_if_missing,
)
from ai_control.tray.timeline_viewer import launch_timeline_viewer_process


TRAY_GUI_SCHEMA = "ai_control_tray_gui_v1"
TRAY_RESIDENT_SCHEMA = "ai_control_tray_resident_v1"
TRAY_WINDOW_CLASS_NAME = "AIControlTrayWindow"
RECORDING_SETTINGS_WINDOW_CLASS_NAME = "AIControlRecordingSettingsWindow"
TRAY_ONLOGON_TASK_NAME = "AIControlTrayGuiOnLogon"
TRAY_MESSAGE_ID = 0x0400 + 81
TIMER_ID_AUTOCLOSE = 1
TIMER_ID_REFRESH_ICON = 2
TRAY_ICON_REFRESH_INTERVAL_MS = 650
TRAY_ICON_LETTERMARK = "AS"
TRAY_ICON_CONSIDERED_SIZES = (16, 20, 24, 32)
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
NIN_SELECT = 0x0400
NIN_KEYSELECT = 0x0401

IDM_STATUS = 1001
IDM_EMERGENCY_STOP = 1002
IDM_CLEAR_EMERGENCY = 1003
IDM_PAUSE_AI_CONTROL = 1006
IDM_ALLOW_AI_CONTROL = 1007
IDM_STOP_AI_CONTROL = 1008
IDM_STATE_LABEL = 1010
IDM_LANGUAGE_FOLLOW_SYSTEM = 1011
IDM_LANGUAGE_ZH = 1012
IDM_LANGUAGE_EN = 1013
IDM_OPEN_RECORDING_SETTINGS = 1014
IDM_OPEN_TIMELINE = 1015
IDM_RECORDING_IDLE_CAPTURE = 2101
IDM_RECORDING_OPERATION_CAPTURE = 2102
IDM_RECORDING_PRE_ACTION_FRAME = 2103
IDM_RECORDING_POST_ACTION_FRAMES = 2104
IDM_RECORDING_IDLE_FPS = 2105
IDM_RECORDING_POST_ACTION_FPS = 2106
IDM_RECORDING_POST_ACTION_DURATION_MS = 2107
IDM_RECORDING_MAX_POST_ACTION_FRAMES = 2108
IDM_RECORDING_RETENTION_DAYS = 2109
IDM_RECORDING_DAILY_BOUNDARY = 2110
IDM_RECORDING_SEGMENT_BUCKET_GRANULARITY = 2111
IDM_RECORDING_MAX_STORAGE_MB = 2112
IDM_RECORDING_MIN_FREE_DISK_MB = 2113
IDM_RECORDING_SEGMENT_IMAGE_ENCODING = 2114
IDM_RECORDING_SEGMENT_IMAGE_QUALITY = 2115
IDM_RECORDING_SEGMENT_IMAGE_LOSSLESS = 2116


TRAY_MENU_ITEMS: tuple[dict[str, Any], ...] = (
    {"id": IDM_STATE_LABEL, "key": "state_label", "label": "State", "readonly": True},
    {"id": IDM_STATUS, "key": "status", "label": "Status"},
    {"id": IDM_PAUSE_AI_CONTROL, "key": "pause_ai_control", "label": "Pause AI Control"},
    {"id": IDM_ALLOW_AI_CONTROL, "key": "allow_ai_control", "label": "Allow AI Control"},
    {"id": IDM_EMERGENCY_STOP, "key": "emergency_stop", "label": "Emergency Stop"},
    {"id": IDM_CLEAR_EMERGENCY, "key": "clear_emergency_stop", "label": "Clear Emergency Stop"},
    {"id": IDM_OPEN_RECORDING_SETTINGS, "key": "open_recording_settings", "label": "Capture & Retention Settings"},
    {"id": IDM_OPEN_TIMELINE, "key": "open_timeline", "label": "Open Timeline"},
    {"id": 0, "key": "language", "label": "Language"},
    {"id": IDM_LANGUAGE_FOLLOW_SYSTEM, "key": "language_follow_system", "label": "Follow System"},
    {"id": IDM_LANGUAGE_ZH, "key": "language_zh", "label": "中文"},
    {"id": IDM_LANGUAGE_EN, "key": "language_en", "label": "English"},
    {"id": IDM_STOP_AI_CONTROL, "key": "stop_ai_control", "label": "Stop AI-Control"},
)

TRAY_ICON_STATES: dict[str, dict[str, Any]] = {
    "ready": {
        "label": "Ready",
        "glyph_rgb": (28, 145, 255),
        "animated": True,
        "frame_count": 4,
        "animation": "subtle_breathing",
        "brightness_frames": (0.92, 1.0, 1.08, 1.0),
    },
    "blocked": {
        "label": "Blocked",
        "glyph_rgb": (224, 52, 52),
        "animated": False,
        "frame_count": 1,
        "animation": "static",
    },
    "paused": {
        "label": "Paused",
        "glyph_rgb": (245, 166, 35),
        "animated": True,
        "frame_count": 2,
        "animation": "very_subtle_breathing",
        "brightness_frames": (0.95, 1.04),
    },
    "emergency": {
        "label": "Emergency stop",
        "glyph_rgb": (198, 40, 40),
        "animated": False,
        "frame_count": 1,
        "animation": "static",
    },
    "discovery_missing": {
        "label": "Discovery missing",
        "glyph_rgb": (118, 128, 138),
        "animated": False,
        "frame_count": 1,
        "animation": "static",
    },
    "unknown": {
        "label": "Unknown",
        "glyph_rgb": (132, 140, 148),
        "animated": False,
        "frame_count": 1,
        "animation": "static",
    },
}

MF_STRING = 0x0000
MF_GRAYED = 0x0001
MF_DISABLED = 0x0002
MF_CHECKED = 0x0008
MF_POPUP = 0x0010
MF_SEPARATOR = 0x0800
MF_ENABLED = 0x0000
WS_VISIBLE = 0x10000000
WS_CHILD = 0x40000000
WS_CAPTION = 0x00C00000
WS_SYSMENU = 0x00080000
WS_POPUP = 0x80000000
WS_BORDER = 0x00800000
WS_EX_DLGMODALFRAME = 0x00000001
BS_AUTOCHECKBOX = 0x00000003
BS_PUSHBUTTON = 0x00000000
ES_AUTOHSCROLL = 0x0080
ES_NUMBER = 0x2000
SS_LEFT = 0x00000000
BM_SETCHECK = 0x00F1
WM_GETTEXT = 0x000D
WM_SETTEXT = 0x000C
BST_CHECKED = 1
IDOK = 1
IDCANCEL = 2
NIF_MESSAGE = 0x0001
NIF_ICON = 0x0002
NIF_TIP = 0x0004
NIF_SHOWTIP = 0x0080
TRANSPARENT = 1
FW_SEMIBOLD = 600
DT_CENTER = 0x0001
DT_VCENTER = 0x0004
DT_SINGLELINE = 0x0020
DT_NOPREFIX = 0x0800
PATCOPY = 0x00F00021
BLACKNESS = 0x00000042
WHITENESS = 0x00FF0062
SM_CXSMICON = 49
SM_CYSMICON = 50

TRAY_LANGUAGE_OPTIONS = ("system", "zh", "en")
TRAY_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "status": "Status",
        "state": "State",
        "ready": "Ready",
        "blocked": "Blocked",
        "paused": "Paused",
        "emergency": "Emergency stop",
        "discovery_missing": "Discovery missing",
        "unknown": "Unknown",
        "pause_ai_control": "Pause AI Control",
        "allow_ai_control": "Allow AI Control",
        "emergency_stop": "Emergency Stop",
        "clear_emergency_stop": "Clear Emergency Stop",
        "open_recording_settings": "Capture & Retention Settings",
        "open_timeline": "Open Timeline",
        "recording_settings_title": "AgentSight Capture & Retention Settings",
        "recording_settings_note": "Capture cadence, action frames, and retention. These settings write to tray-config.jsonc only.",
        "setting_idle_capture": "Idle low-frequency capture",
        "setting_idle_fps": "Idle FPS",
        "setting_operation_capture": "Operation capture",
        "setting_pre_action_frame": "Capture pre-action frame",
        "setting_post_action_frames": "Capture post-action frames",
        "setting_post_action_fps": "Post-action FPS",
        "setting_post_action_duration_ms": "Post-action duration",
        "setting_max_post_action_frames": "Max post-action frames",
        "setting_retention_days": "Delete after days",
        "setting_max_storage_mb": "Max storage",
        "setting_min_free_disk_mb": "Keep free disk",
        "settings_group_idle": "Idle capture",
        "settings_group_action": "Action capture",
        "settings_group_retention": "Retention",
        "save": "Save",
        "cancel": "Cancel",
        "language": "Language",
        "language_follow_system": "Follow System",
        "language_zh": "中文",
        "language_en": "English",
        "stop_ai_control": "Stop AgentSight",
        "tray_title": "AI-Control",
        "tray_status": "Tray status",
        "can_attempt_real_control": "Can attempt real control",
        "control_blockers": "Control blockers",
        "none": "none",
        "service_status": "Service status",
        "host_agent_pid": "Host Agent PID",
        "emergency_stop_active": "Emergency stop active",
        "operator_control_status": "Operator control status",
        "ai_real_control_enabled": "AI real control enabled",
        "action_capture_enabled": "Action capture enabled",
        "continuous_recording_enabled": "Idle low-frequency capture enabled",
        "recording_settings_file": "Recording settings",
        "boundary_summary": "Boundary: no OCR, clipboard, DOM, accessibility tree, window semantics, or business-success judgment.",
        "paused_message": "AI real control paused.",
        "allowed_message": "AI real control allowed.",
        "emergency_message": "Emergency stop active.",
        "clear_emergency_message": "Emergency stop cleared",
        "stop_requested_message": "Stop AgentSight requested.",
        "host_agent_status": "Host Agent status",
        "language_changed": "Tray language updated.",
    },
    "zh": {
        "status": "状态",
        "state": "状态",
        "ready": "可用",
        "blocked": "已阻断",
        "paused": "已暂停",
        "emergency": "紧急停止",
        "discovery_missing": "未发现后台",
        "unknown": "未知",
        "pause_ai_control": "暂停 AI 控制",
        "allow_ai_control": "允许 AI 控制",
        "emergency_stop": "紧急停止",
        "clear_emergency_stop": "清除紧急停止",
        "open_recording_settings": "采集与保留设置",
        "open_timeline": "打开时间线",
        "recording_settings_title": "AgentSight 采集与保留设置",
        "recording_settings_note": "设置平时采集、动作前后帧和保留策略；只写入 tray-config.jsonc。",
        "setting_idle_capture": "平时低频记录",
        "setting_idle_fps": "平时 FPS",
        "setting_operation_capture": "操作捕获",
        "setting_pre_action_frame": "动作前帧",
        "setting_post_action_frames": "动作后帧",
        "setting_post_action_fps": "动作后 FPS",
        "setting_post_action_duration_ms": "动作后时长",
        "setting_max_post_action_frames": "动作后最大帧数",
        "setting_retention_days": "多少天后删除",
        "setting_max_storage_mb": "最大保留空间",
        "setting_min_free_disk_mb": "至少保留磁盘空间",
        "settings_group_idle": "平时采集",
        "settings_group_action": "动作采集",
        "settings_group_retention": "保留策略",
        "save": "保存",
        "cancel": "取消",
        "language": "语言",
        "language_follow_system": "跟随系统",
        "language_zh": "中文",
        "language_en": "English",
        "stop_ai_control": "停止 AgentSight",
        "tray_title": "AI-Control",
        "tray_status": "托盘状态",
        "can_attempt_real_control": "可尝试真实控制",
        "control_blockers": "控制阻断原因",
        "none": "无",
        "service_status": "服务状态",
        "host_agent_pid": "Host Agent PID",
        "emergency_stop_active": "紧急停止已启用",
        "operator_control_status": "操作者控制状态",
        "ai_real_control_enabled": "AI 真实控制已允许",
        "action_capture_enabled": "操作捕获已启用",
        "continuous_recording_enabled": "平时低频记录已启用",
        "recording_settings_file": "录制设置",
        "boundary_summary": "边界：不做 OCR、剪贴板、DOM、accessibility tree、窗口语义或业务成功判断。",
        "paused_message": "AI 真实控制已暂停。",
        "allowed_message": "AI 真实控制已允许。",
        "emergency_message": "紧急停止已启用。",
        "clear_emergency_message": "紧急停止已清除",
        "stop_requested_message": "已请求停止 AgentSight。",
        "host_agent_status": "Host Agent 状态",
        "language_changed": "托盘语言已更新。",
    },
}

LRESULT = ctypes.c_ssize_t
WPARAM = getattr(wintypes, "WPARAM", ctypes.c_size_t)
LPARAM = getattr(wintypes, "LPARAM", ctypes.c_ssize_t)
ATOM = getattr(wintypes, "ATOM", wintypes.WORD)
UINT_PTR = getattr(wintypes, "UINT_PTR", ctypes.c_size_t)
HFONT = getattr(wintypes, "HFONT", wintypes.HANDLE)


def _is_windows() -> bool:
    return is_windows()


def build_tray_gui_description() -> dict[str, Any]:
    windows = _is_windows()
    return {
        "object_type": "AIControlTrayGuiDescription",
        "schema": TRAY_GUI_SCHEMA,
        "tray_icon_gui_available": windows,
        "tray_icon_api": "Shell_NotifyIconW",
        "tray_icon_state_model": {
            "states": TRAY_ICON_STATES,
            "lettermark": TRAY_ICON_LETTERMARK,
            "icon_visual_style": "AS uppercase lettermark",
            "transparent_background": True,
            "colored_letter_glyphs": True,
            "no_background_shape": True,
            "background_shapes": False,
            "ready_animation_enabled": True,
            "considered_sizes": list(TRAY_ICON_CONSIDERED_SIZES),
            "runtime_generated_win32_gdi_icons": True,
            "runtime_generated_hicon_frames": True,
            "multi_frame_notifyicon_animation": True,
            "animation_interval_ms": TRAY_ICON_REFRESH_INTERVAL_MS,
            "animation_plans": {state: tray_icon_animation_plan_for_state(state) for state in TRAY_ICON_STATES},
            "fallback_system_icon": True,
            "tooltip_reflects_status": True,
            "status_change_refreshes_icon_immediately": True,
            "blocked_and_emergency_static": True,
            "notifyicon_v4_standard_tooltip": True,
        },
        "status_window": "MessageBoxW",
        "menu_items": list(TRAY_MENU_ITEMS),
        "menu_model": {
            "dynamic_from_tray_status": True,
            "readonly_state_label": True,
            "i18n_enabled": True,
            "settings_path": str(tray_settings_path()),
            "default_language": "follow_windows_system_language",
            "supported_languages": list(TRAY_LANGUAGE_OPTIONS),
            "language_menu_present": True,
            "uses_controls_enablement": True,
            "clipboard_action_present": False,
            "open_evidence_folder": False,
            "open_agent_data_folder": False,
            "exit_tray_only": False,
            "recording_settings_present": True,
            "recording_policy_toggles_present": False,
            "timeline_menu_present": True,
            "operation_log_menu_present": False,
            "operation_log_integrated_into_timeline": True,
            "refreshes_after_actions": True,
        },
        "controls": {
            "show_status": True,
            "pause_ai_control": True,
            "allow_ai_control": True,
            "emergency_stop": True,
            "physical_emergency_hotkey": True,
            "physical_emergency_hotkey_monitor_started_by_default_on_run": windows,
            "clear_emergency_stop": True,
            "open_evidence_folder": False,
            "open_agent_data_folder": False,
            "open_recording_settings": True,
            "open_timeline": True,
            "open_operation_log": False,
            "recording_settings_surface": "modern_scrollable_tkinter_dialog",
            "recording_settings_fallback_surface": "native_win32_dialog",
            "stop_ai_control_full_shutdown": True,
            "exit_tray_process": False,
            "exit_tray_process_only": False,
        },
        "stop_semantics": {
            "emergency_stop": "blocks real control and keeps human-visible tray status when possible",
            "operator_pause": "policy only; no process exit",
            "stop_ai_control": "full shutdown of Host Agent, Tray GUI, and Session Supervisor",
            "exit_tray_only": "removed from human menu because the Session Supervisor intentionally restarts the tray",
        },
        "recording_configuration": {
            "schema": "ai_control_tray_config_v1",
            "config_path": str(default_tray_config_file()),
            "human_editable_jsonc": True,
            "settings_viewer": "modern_scrollable_tkinter_dialog",
            "settings_fallback_viewer": "native_win32_dialog",
            "settings_dialog_model": "ai_control_recording_settings_dialog_v1",
            "idle_capture_default_fps": 1.0,
            "idle_capture_min_fps": 0.1,
            "action_capture_default_fps": 10,
            "action_capture_default_duration_ms": 10000,
            "action_capture_max_post_action_frames_required": True,
            "recording_policy_toggles_in_menu": False,
            "timeline_viewer": "pyside6_qt_native_window",
            "operation_log_viewer": "integrated_into_timeline_viewer",
            "html_viewer_removed": True,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        },
        "physical_hotkey_monitor": {
            "started_by_default_on_run": windows,
            "status_or_describe_starts_monitor": False,
            "can_disable_for_smoke_or_debug": "--no-hotkey",
            "ignored_input_source": "LLKHF_INJECTED|LLKHF_LOWER_IL_INJECTED",
        },
        "emergency_hotkey": build_hotkey_description(),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def tray_icon_state_for_status(status: dict[str, Any] | None = None) -> str:
    payload = status if isinstance(status, dict) else load_tray_status()
    tray_status = str(payload.get("tray_status") or "")
    if tray_status == "ready":
        return "ready"
    if tray_status == "operator_control_paused":
        return "paused"
    if tray_status == "emergency_stopped":
        return "emergency"
    if tray_status == "discovery_missing":
        return "discovery_missing"
    if tray_status == "blocked":
        return "blocked"
    return "unknown"


def tray_tooltip_for_status(status: dict[str, Any] | None = None, *, language: str | None = None) -> str:
    payload = status if isinstance(status, dict) else load_tray_status()
    icon_state = tray_icon_state_for_status(payload)
    active_language = language if language in {"zh", "en"} else resolve_tray_language()
    label = tray_text(icon_state, language=active_language)
    return f"AI-Control: {label}"[:127]


def tray_icon_animation_plan_for_state(state: str) -> dict[str, Any]:
    model = TRAY_ICON_STATES.get(state, TRAY_ICON_STATES["unknown"])
    return {
        "state": state if state in TRAY_ICON_STATES else "unknown",
        "lettermark": TRAY_ICON_LETTERMARK,
        "animated": bool(model.get("animated")),
        "animation": str(model.get("animation") or "static"),
        "frame_count": int(model.get("frame_count") or 1),
        "interval_ms": TRAY_ICON_REFRESH_INTERVAL_MS,
        "static_when_blocked": state in {"blocked", "emergency", "discovery_missing", "unknown"},
        "implementation": "runtime_win32_gdi_hicon_frames",
        "considered_sizes": list(TRAY_ICON_CONSIDERED_SIZES),
    }


def tray_icon_frame_specs_for_state(state: str) -> list[dict[str, Any]]:
    normalized = state if state in TRAY_ICON_STATES else "unknown"
    model = TRAY_ICON_STATES[normalized]
    frame_count = int(model.get("frame_count") or 1)
    brightness_frames = list(model.get("brightness_frames") or [1.0] * frame_count)
    if len(brightness_frames) < frame_count:
        brightness_frames.extend([1.0] * (frame_count - len(brightness_frames)))
    return [
        {
            "state": normalized,
            "frame_index": index,
            "lettermark": TRAY_ICON_LETTERMARK,
            "glyph_rgb": _scale_rgb(tuple(model["glyph_rgb"]), float(brightness_frames[index])),
            "transparent_background": True,
            "colored_letter_glyphs": True,
            "no_background_shape": True,
            "animated": bool(model.get("animated")),
            "animation": str(model.get("animation") or "static"),
            "size_policy": "generate_at_system_small_icon_size; design_considers_16_20_24_32",
        }
        for index in range(frame_count)
    ]


def tray_icon_state_is_animated(state: str) -> bool:
    return bool(TRAY_ICON_STATES.get(state, TRAY_ICON_STATES["unknown"]).get("animated"))


def _scale_rgb(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(component * factor)))) for component in rgb)


def _rgb_to_colorref(rgb: tuple[int, int, int]) -> int:
    return int(rgb[0]) | (int(rgb[1]) << 8) | (int(rgb[2]) << 16)


def tray_settings_path() -> Path:
    return default_agent_dir() / "tray-settings.json"


def load_tray_settings() -> dict[str, Any]:
    path = tray_settings_path()
    if not path.exists():
        return {"schema": "ai_control_tray_settings_v1", "language": "system"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "ai_control_tray_settings_v1", "language": "system"}
    language = str(data.get("language") or "system")
    if language not in TRAY_LANGUAGE_OPTIONS:
        language = "system"
    return {"schema": "ai_control_tray_settings_v1", "language": language}


def save_tray_language(language: str) -> dict[str, Any]:
    normalized = language if language in TRAY_LANGUAGE_OPTIONS else "system"
    path = tray_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema": "ai_control_tray_settings_v1",
        "language": normalized,
        "updated_at_ms": int(time.time() * 1000),
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def windows_system_language() -> str:
    if _is_windows():
        try:
            langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            if langid & 0x03FF == 0x0004:
                return "zh"
        except Exception:
            pass
    try:
        locale_name = locale.getlocale()[0] or locale.getdefaultlocale()[0] or ""
    except Exception:
        locale_name = ""
    return "zh" if locale_name.lower().startswith("zh") else "en"


def resolve_tray_language(settings: dict[str, Any] | None = None) -> str:
    payload = settings if isinstance(settings, dict) else load_tray_settings()
    language = str(payload.get("language") or "system")
    if language == "system":
        return windows_system_language()
    return language if language in {"zh", "en"} else "en"


def tray_text(key: str, *, language: str | None = None) -> str:
    lang = language if language in {"zh", "en"} else resolve_tray_language()
    return TRAY_TEXT.get(lang, TRAY_TEXT["en"]).get(key, TRAY_TEXT["en"].get(key, key))


def tray_status_display(status: dict[str, Any] | None = None, *, language: str | None = None) -> str:
    icon_state = tray_icon_state_for_status(status)
    return tray_text(icon_state, language=language)


def build_recording_settings_dialog_model(
    policy: dict[str, Any] | None = None,
    *,
    language: str | None = None,
) -> dict[str, Any]:
    active_language = language if language in {"zh", "en"} else resolve_tray_language()
    payload = policy if isinstance(policy, dict) else write_default_tray_config_if_missing()
    recording = payload.get("recording") if isinstance(payload.get("recording"), dict) else {}
    idle = recording.get("idle_capture") if isinstance(recording.get("idle_capture"), dict) else {}
    action = recording.get("action_capture") if isinstance(recording.get("action_capture"), dict) else {}
    return {
        "object_type": "AIControlRecordingSettingsDialogModel",
        "schema": "ai_control_recording_settings_dialog_v1",
        "ui_surface": "native_windows_dialog",
        "preferred_ui_surface": "modern_scrollable_tkinter_dialog",
        "fallback_ui_surface": "native_win32_dialog",
        "scrollable": True,
        "style_model": {
            "font_family": "Segoe UI",
            "grouped_cards": True,
            "sticky_footer_buttons": True,
            "mousewheel_scroll": True,
            "inspired_by": ["CustomTkinter scrollable settings frames", "ttkbootstrap flat ttk styling"],
        },
        "language": active_language,
        "title": tray_text("recording_settings_title", language=active_language),
        "note": tray_text("recording_settings_note", language=active_language),
        "config_file": str(payload.get("config_file") or default_tray_config_file()),
        "controls": [
            {"kind": "section", "key": "idle_group", "label": tray_text("settings_group_idle", language=active_language)},
            {
                "id": IDM_RECORDING_IDLE_CAPTURE,
                "key": "continuous_recording_enabled",
                "field": "continuous_recording_enabled",
                "control_type": "checkbox",
                "label": tray_text("setting_idle_capture", language=active_language),
                "checked": recording_policy_flag_enabled(payload, "continuous_recording_enabled"),
            },
            {
                "id": IDM_RECORDING_IDLE_FPS,
                "key": "idle_fps",
                "field": "idle_fps",
                "control_type": "float",
                "label": tray_text("setting_idle_fps", language=active_language),
                "value": float(idle.get("fps") or 1.0),
                "min": 0.1,
                "max": 60,
                "unit": "fps",
            },
            {"kind": "section", "key": "action_group", "label": tray_text("settings_group_action", language=active_language)},
            {
                "id": IDM_RECORDING_OPERATION_CAPTURE,
                "key": "action_capture_enabled",
                "field": "action_capture_enabled",
                "control_type": "checkbox",
                "label": tray_text("setting_operation_capture", language=active_language),
                "checked": recording_policy_flag_enabled(payload, "action_capture_enabled"),
            },
            {
                "id": IDM_RECORDING_PRE_ACTION_FRAME,
                "key": "capture_pre_action_frame",
                "field": "capture_pre_action_frame",
                "control_type": "checkbox",
                "label": tray_text("setting_pre_action_frame", language=active_language),
                "checked": recording_policy_flag_enabled(payload, "capture_pre_action_frame"),
            },
            {
                "id": IDM_RECORDING_POST_ACTION_FRAMES,
                "key": "capture_post_action_frames",
                "field": "capture_post_action_frames",
                "control_type": "checkbox",
                "label": tray_text("setting_post_action_frames", language=active_language),
                "checked": recording_policy_flag_enabled(payload, "capture_post_action_frames"),
            },
            {
                "id": IDM_RECORDING_POST_ACTION_FPS,
                "key": "post_action_fps",
                "field": "post_action_fps",
                "control_type": "integer",
                "label": tray_text("setting_post_action_fps", language=active_language),
                "value": int(action.get("post_action_fps") or 10),
                "min": 1,
                "max": 60,
                "unit": "fps",
            },
            {
                "id": IDM_RECORDING_POST_ACTION_DURATION_MS,
                "key": "post_action_duration_ms",
                "field": "post_action_duration_ms",
                "control_type": "integer",
                "label": tray_text("setting_post_action_duration_ms", language=active_language),
                "value": int(action.get("post_action_duration_ms") or 10000),
                "min": 1,
                "max": 60000,
                "unit": "ms",
            },
            {
                "id": IDM_RECORDING_MAX_POST_ACTION_FRAMES,
                "key": "max_post_action_frames",
                "field": "max_post_action_frames",
                "control_type": "integer",
                "label": tray_text("setting_max_post_action_frames", language=active_language),
                "value": int(action.get("max_post_action_frames") or 100),
                "min": 1,
                "max": 1000,
                "unit": "frames",
                "required": True,
            },
            {"kind": "section", "key": "retention_group", "label": tray_text("settings_group_retention", language=active_language)},
            {
                "id": IDM_RECORDING_RETENTION_DAYS,
                "key": "retention_days",
                "field": "retention_days",
                "control_type": "integer",
                "label": tray_text("setting_retention_days", language=active_language),
                "value": int(payload.get("retention_days") or 30),
                "min": 1,
                "max": 3650,
                "unit": "days",
            },
            {
                "id": IDM_RECORDING_MAX_STORAGE_MB,
                "key": "max_storage_mb",
                "field": "max_storage_mb",
                "control_type": "integer",
                "label": tray_text("setting_max_storage_mb", language=active_language),
                "value": int(payload.get("max_storage_mb") or 5120),
                "min": 256,
                "max": 1048576,
                "unit": "MB",
            },
            {
                "id": IDM_RECORDING_MIN_FREE_DISK_MB,
                "key": "min_free_disk_mb",
                "field": "min_free_disk_mb",
                "control_type": "integer",
                "label": tray_text("setting_min_free_disk_mb", language=active_language),
                "value": int(payload.get("min_free_disk_mb") or 1024),
                "min": 256,
                "max": 1048576,
                "unit": "MB",
            },
        ],
        "buttons": [
            {"id": IDOK, "key": "save", "label": tray_text("save", language=active_language)},
            {"id": IDCANCEL, "key": "cancel", "label": tray_text("cancel", language=active_language)},
        ],
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def recording_settings_dialog_text(model: dict[str, Any]) -> str:
    lines = [str(model.get("note") or ""), "", f"Config: {model.get('config_file')}"]
    for control in model.get("controls", []):
        if control.get("kind") == "section":
            lines.append("")
            lines.append(f"[{control.get('label')}]")
            continue
        mark = "[x]" if control.get("checked") else "[ ]"
        if control.get("control_type") == "checkbox":
            lines.append(f"{mark} {control.get('label')}")
        else:
            lines.append(f"{control.get('label')}: {control.get('value')} {control.get('unit') or ''}".rstrip())
    lines.append("")
    lines.append("Boundary: no OCR, clipboard, DOM, accessibility tree, window semantics, input, target-hit, causality, or business-success judgment.")
    return "\n".join(lines)


def show_modern_recording_settings_dialog(model: dict[str, Any]) -> dict[str, Any] | None:
    import tkinter as tk
    from tkinter import ttk

    result: dict[str, Any] | None = None
    language = str(model.get("language") or "en")
    root = tk.Tk()
    root.title(str(model.get("title") or "AgentSight"))
    root.geometry("660x620")
    root.minsize(560, 440)
    root.configure(bg="#f4f7fb")
    try:
        root.call("tk", "scaling", 1.15)
    except tk.TclError:
        pass

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    base_font = ("Segoe UI", 10)
    title_font = ("Segoe UI Semibold", 15)
    section_font = ("Segoe UI Semibold", 10)
    small_font = ("Segoe UI", 9)
    style.configure(".", font=base_font)
    style.configure("Settings.TFrame", background="#f4f7fb")
    style.configure("Card.TFrame", background="#ffffff", relief="flat")
    style.configure("Header.TFrame", background="#0f766e")
    style.configure("HeaderTitle.TLabel", background="#0f766e", foreground="#ffffff", font=title_font)
    style.configure("HeaderNote.TLabel", background="#0f766e", foreground="#d7fffb", font=small_font)
    style.configure("Section.TLabel", background="#ffffff", foreground="#0f172a", font=section_font)
    style.configure("Body.TLabel", background="#ffffff", foreground="#1f2937", font=base_font)
    style.configure("Hint.TLabel", background="#ffffff", foreground="#64748b", font=small_font)
    style.configure("Footer.TFrame", background="#f4f7fb")
    style.configure("Primary.TButton", font=("Segoe UI Semibold", 10))

    root.columnconfigure(0, weight=1)
    root.rowconfigure(1, weight=1)

    header = ttk.Frame(root, style="Header.TFrame", padding=(18, 14, 18, 14))
    header.grid(row=0, column=0, sticky="ew")
    header.columnconfigure(1, weight=1)
    mark = ttk.Label(header, text="AS", style="HeaderTitle.TLabel")
    mark.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14))
    title = ttk.Label(header, text=str(model.get("title") or ""), style="HeaderTitle.TLabel")
    title.grid(row=0, column=1, sticky="ew")
    note = ttk.Label(header, text=str(model.get("note") or ""), style="HeaderNote.TLabel", wraplength=560)
    note.grid(row=1, column=1, sticky="ew", pady=(4, 0))

    outer = ttk.Frame(root, style="Settings.TFrame")
    outer.grid(row=1, column=0, sticky="nsew")
    outer.columnconfigure(0, weight=1)
    outer.rowconfigure(0, weight=1)

    canvas = tk.Canvas(outer, bg="#f4f7fb", highlightthickness=0, borderwidth=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")

    content = ttk.Frame(canvas, style="Settings.TFrame", padding=(16, 16, 16, 16))
    window_id = canvas.create_window((0, 0), window=content, anchor="nw")
    content.columnconfigure(0, weight=1)

    variables: dict[str, tk.Variable] = {}
    active_card: ttk.Frame | None = None
    card_row = 0

    def on_configure(_event: object = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfigure(window_id, width=canvas.winfo_width())

    def on_mousewheel(event: tk.Event) -> None:
        delta = int(-1 * (event.delta / 120))
        canvas.yview_scroll(delta, "units")

    content.bind("<Configure>", on_configure)
    canvas.bind("<Configure>", on_configure)
    canvas.bind_all("<MouseWheel>", on_mousewheel)

    def ensure_card(section_label: str) -> ttk.Frame:
        nonlocal active_card, card_row
        card = ttk.Frame(content, style="Card.TFrame", padding=(14, 12, 14, 12))
        card.grid(row=card_row, column=0, sticky="ew", pady=(0, 12))
        card.columnconfigure(1, weight=1)
        label = ttk.Label(card, text=section_label, style="Section.TLabel")
        label.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        active_card = card
        card_row += 1
        return card

    row_by_card: dict[int, int] = {}
    for control in model.get("controls", []):
        if control.get("kind") == "section":
            ensure_card(str(control.get("label") or "Settings"))
            continue
        if active_card is None:
            ensure_card(str(model.get("title") or "Settings"))
        assert active_card is not None
        card_key = id(active_card)
        row = row_by_card.get(card_key, 1)
        control_type = str(control.get("control_type") or "checkbox")
        key = str(control.get("key") or "")
        label_text = str(control.get("label") or key)
        if control_type == "checkbox":
            var = tk.BooleanVar(value=bool(control.get("checked")))
            variables[key] = var
            checkbox = ttk.Checkbutton(active_card, text=label_text, variable=var)
            checkbox.grid(row=row, column=0, columnspan=3, sticky="w", pady=5)
        else:
            ttk.Label(active_card, text=label_text, style="Body.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
            var = tk.StringVar(value=str(control.get("value") or ""))
            variables[key] = var
            entry = ttk.Entry(active_card, textvariable=var, width=16)
            entry.grid(row=row, column=1, sticky="ew", pady=5)
            hint_parts = []
            unit = str(control.get("unit") or "")
            if unit:
                hint_parts.append(unit)
            if control.get("min") is not None and control.get("max") is not None:
                hint_parts.append(f"{control.get('min')}-{control.get('max')}")
            ttk.Label(active_card, text="  ".join(hint_parts), style="Hint.TLabel").grid(row=row, column=2, sticky="w", pady=5, padx=(10, 0))
        row_by_card[card_key] = row + 1

    footer = ttk.Frame(root, style="Footer.TFrame", padding=(16, 10, 16, 14))
    footer.grid(row=2, column=0, sticky="ew")
    footer.columnconfigure(0, weight=1)
    path_label = ttk.Label(footer, text=str(model.get("config_file") or ""), foreground="#64748b", background="#f4f7fb", font=small_font)
    path_label.grid(row=0, column=0, sticky="w")

    def collect_and_close() -> None:
        nonlocal result
        result = {key: var.get() for key, var in variables.items()}
        root.destroy()

    def cancel() -> None:
        root.destroy()

    ttk.Button(footer, text=tray_text("save", language=language), style="Primary.TButton", command=collect_and_close).grid(row=0, column=1, sticky="e", padx=(10, 8))
    ttk.Button(footer, text=tray_text("cancel", language=language), command=cancel).grid(row=0, column=2, sticky="e")
    root.protocol("WM_DELETE_WINDOW", cancel)
    root.mainloop()
    return result


def tray_callback_event_code(lparam: int) -> int:
    return int(lparam) & 0xFFFF


def tray_callback_opens_status(lparam: int) -> bool:
    event = tray_callback_event_code(lparam)
    return event in {WM_LBUTTONUP, NIN_KEYSELECT}


def tray_callback_opens_menu(lparam: int) -> bool:
    event = tray_callback_event_code(lparam)
    return event in {WM_RBUTTONUP, WM_CONTEXTMENU}


def build_tray_menu_model(status: dict[str, Any] | None = None, *, language: str | None = None) -> list[dict[str, Any]]:
    payload = status if isinstance(status, dict) else load_tray_status()
    controls = payload.get("controls") if isinstance(payload.get("controls"), dict) else {}
    settings = load_tray_settings()
    active_language = language if language in {"zh", "en"} else resolve_tray_language(settings)
    configured_language = str(settings.get("language") or "system")
    can_pause = bool(controls.get("can_pause_ai_real_control"))
    can_allow = bool(controls.get("can_allow_ai_real_control"))
    can_clear_emergency = bool(controls.get("can_clear_emergency_stop"))
    can_open_recording_settings = bool(controls.get("can_open_recording_settings", True))
    can_open_timeline = bool(controls.get("can_open_timeline", True))
    return [
        {
            "kind": "item",
            "id": IDM_STATE_LABEL,
            "key": "state_label",
            "label": f"{tray_text('state', language=active_language)}: {tray_status_display(payload, language=active_language)}",
            "enabled": False,
            "readonly": True,
        },
        {"kind": "separator"},
        {"kind": "item", "id": IDM_STATUS, "key": "status", "label": tray_text("status", language=active_language), "enabled": True},
        {"kind": "item", "id": IDM_PAUSE_AI_CONTROL, "key": "pause_ai_control", "label": tray_text("pause_ai_control", language=active_language), "enabled": can_pause},
        {"kind": "item", "id": IDM_ALLOW_AI_CONTROL, "key": "allow_ai_control", "label": tray_text("allow_ai_control", language=active_language), "enabled": can_allow},
        {"kind": "separator"},
        {"kind": "item", "id": IDM_EMERGENCY_STOP, "key": "emergency_stop", "label": tray_text("emergency_stop", language=active_language), "enabled": True},
        {"kind": "item", "id": IDM_CLEAR_EMERGENCY, "key": "clear_emergency_stop", "label": tray_text("clear_emergency_stop", language=active_language), "enabled": can_clear_emergency},
        {"kind": "separator"},
        {"kind": "item", "id": IDM_OPEN_RECORDING_SETTINGS, "key": "open_recording_settings", "label": tray_text("open_recording_settings", language=active_language), "enabled": can_open_recording_settings},
        {"kind": "item", "id": IDM_OPEN_TIMELINE, "key": "open_timeline", "label": tray_text("open_timeline", language=active_language), "enabled": can_open_timeline},
        {
            "kind": "submenu",
            "key": "language",
            "label": tray_text("language", language=active_language),
            "items": [
                {
                    "kind": "item",
                    "id": IDM_LANGUAGE_FOLLOW_SYSTEM,
                    "key": "language_follow_system",
                    "label": tray_text("language_follow_system", language=active_language),
                    "enabled": True,
                    "checked": configured_language == "system",
                },
                {
                    "kind": "item",
                    "id": IDM_LANGUAGE_ZH,
                    "key": "language_zh",
                    "label": tray_text("language_zh", language=active_language),
                    "enabled": True,
                    "checked": configured_language == "zh",
                },
                {
                    "kind": "item",
                    "id": IDM_LANGUAGE_EN,
                    "key": "language_en",
                    "label": tray_text("language_en", language=active_language),
                    "enabled": True,
                    "checked": configured_language == "en",
                },
            ],
        },
        {"kind": "separator"},
        {"kind": "item", "id": IDM_STOP_AI_CONTROL, "key": "stop_ai_control", "label": tray_text("stop_ai_control", language=active_language), "enabled": True},
    ]


def status_summary_text(status: dict[str, Any] | None = None, *, language: str | None = None) -> str:
    payload = status if isinstance(status, dict) else load_tray_status()
    active_language = language if language in {"zh", "en"} else resolve_tray_language()
    service = payload.get("service") if isinstance(payload.get("service"), dict) else {}
    emergency = payload.get("emergency_stop") if isinstance(payload.get("emergency_stop"), dict) else {}
    policy = payload.get("recording_policy") if isinstance(payload.get("recording_policy"), dict) else {}
    recording = policy.get("recording") if isinstance(policy.get("recording"), dict) else {}
    action_capture = recording.get("action_capture") if isinstance(recording.get("action_capture"), dict) else {}
    operator_policy = payload.get("operator_control_policy") if isinstance(payload.get("operator_control_policy"), dict) else {}
    host_agent = payload.get("host_agent") if isinstance(payload.get("host_agent"), dict) else {}
    lines = [
        tray_text("tray_title", language=active_language),
        f"{tray_text('tray_status', language=active_language)}: {tray_status_display(payload, language=active_language)}",
        f"{tray_text('can_attempt_real_control', language=active_language)}: {payload.get('can_attempt_real_control')}",
        f"{tray_text('control_blockers', language=active_language)}: {', '.join(payload.get('control_blockers') or []) or tray_text('none', language=active_language)}",
        f"{tray_text('service_status', language=active_language)}: {service.get('service_status')}",
        f"{tray_text('host_agent_pid', language=active_language)}: {host_agent.get('pid')}",
        f"{tray_text('emergency_stop_active', language=active_language)}: {emergency.get('active')}",
        f"{tray_text('operator_control_status', language=active_language)}: {operator_policy.get('policy_status')}",
        f"{tray_text('ai_real_control_enabled', language=active_language)}: {operator_policy.get('real_control_enabled')}",
        f"{tray_text('action_capture_enabled', language=active_language)}: {action_capture.get('enabled')}",
        f"{tray_text('continuous_recording_enabled', language=active_language)}: {policy.get('continuous_recording_enabled')}",
        f"{tray_text('recording_settings_file', language=active_language)}: {payload.get('paths', {}).get('tray_config_file')}",
        "",
        tray_text("boundary_summary", language=active_language),
    ]
    return "\n".join(lines)


def write_json_report(report: dict[str, Any], output: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def build_tray_gui_run_report(
    *,
    tray_icon_gui_started: bool,
    tray_icon_added: bool,
    run_seconds_requested: int | None,
    started_at_ms: int,
    ended_at_ms: int,
    exit_code: int,
    physical_hotkey_monitor_enabled: bool,
    hotkey_start_report: dict[str, Any] | None = None,
    hotkey_trigger_report: dict[str, Any] | None = None,
    hotkey_stop_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlTrayGuiRunReport",
        "schema": TRAY_GUI_SCHEMA,
        "tray_icon_gui_started": tray_icon_gui_started,
        "tray_icon_added": tray_icon_added,
        "run_seconds_requested": run_seconds_requested,
        "physical_hotkey_monitor_enabled": physical_hotkey_monitor_enabled,
        "physical_hotkey_monitor_start": hotkey_start_report,
        "physical_hotkey_monitor_trigger": hotkey_trigger_report,
        "physical_hotkey_monitor_stop": hotkey_stop_report,
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "exit_code": exit_code,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def build_tray_gui_already_running_report(
    *,
    run_seconds_requested: int | None,
    started_at_ms: int,
    ended_at_ms: int,
    physical_hotkey_monitor_requested: bool,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlTrayGuiAlreadyRunningReport",
        "schema": TRAY_GUI_SCHEMA,
        "run_status": "already_running",
        "single_instance_guard": True,
        "tray_window_class": TRAY_WINDOW_CLASS_NAME,
        "tray_window_present": True,
        "new_tray_window_started": False,
        "tray_icon_added": False,
        "run_seconds_requested": run_seconds_requested,
        "physical_hotkey_monitor_requested": physical_hotkey_monitor_requested,
        "physical_hotkey_monitor_started": False,
        "started_at_ms": started_at_ms,
        "ended_at_ms": ended_at_ms,
        "exit_code": 0,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, WPARAM, LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HCURSOR),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class Win32TrayApp:
    def __init__(self, *, run_seconds: int | None = None, enable_hotkey_monitor: bool = True) -> None:
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.kernel32 = ctypes.windll.kernel32
        self.gdi32 = ctypes.windll.gdi32
        self._configure_winapi()
        self.run_seconds = run_seconds
        self.enable_hotkey_monitor = enable_hotkey_monitor
        self.class_name = TRAY_WINDOW_CLASS_NAME
        self.hwnd: int | None = None
        self.fallback_hicon = self.user32.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), wintypes.LPCWSTR))
        self.hicon = self.fallback_hicon
        self.generated_hicons: list[int] = []
        self._icon_cache: dict[tuple[str, int, int], int] = {}
        self._animation_frame_index = 0
        self._wndproc = WNDPROC(self._window_proc)
        self._recording_settings_wndproc = WNDPROC(self._recording_settings_window_proc)
        self.recording_settings_hwnd: int | None = None
        self.recording_settings_class_registered = False
        self.tray_icon_added = False
        self._last_icon_state: str | None = None
        self._last_tooltip: str | None = None
        self.hotkey_monitor: EmergencyHotkeyMonitor | None = None
        self.hotkey_start_report: dict[str, Any] | None = None
        self.hotkey_stop_report: dict[str, Any] | None = None

    def _configure_winapi(self) -> None:
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
        self.user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        self.user32.LoadIconW.restype = wintypes.HICON
        self.user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self.user32.GetSystemMetrics.restype = ctypes.c_int
        self.user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
        self.user32.RegisterClassW.restype = ATOM
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self.user32.CreateWindowExW.restype = wintypes.HWND
        self.user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.SetTimer.argtypes = [wintypes.HWND, UINT_PTR, wintypes.UINT, wintypes.LPVOID]
        self.user32.KillTimer.argtypes = [wintypes.HWND, UINT_PTR]
        self.user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
        self.user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.user32.CreatePopupMenu.restype = wintypes.HMENU
        self.user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, UINT_PTR, wintypes.LPCWSTR]
        self.user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPVOID,
        ]
        self.user32.DestroyMenu.argtypes = [wintypes.HMENU]
        self.user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user32.DestroyWindow.argtypes = [wintypes.HWND]
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, WPARAM, LPARAM]
        self.user32.SendMessageW.restype = LRESULT
        self.user32.IsDlgButtonChecked.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.IsDlgButtonChecked.restype = wintypes.UINT
        self.user32.DrawTextW.argtypes = [
            wintypes.HDC,
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.RECT),
            wintypes.UINT,
        ]
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
        self.user32.CreateIconIndirect.restype = wintypes.HICON
        self.user32.DestroyIcon.argtypes = [wintypes.HICON]
        self.gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self.gdi32.CreateBitmap.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
            wintypes.LPVOID,
        ]
        self.gdi32.CreateBitmap.restype = wintypes.HBITMAP
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self.gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self.gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
        self.gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
        self.gdi32.CreateFontW.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPCWSTR,
        ]
        self.gdi32.CreateFontW.restype = HFONT
        self.gdi32.SetBkMode.argtypes = [wintypes.HDC, ctypes.c_int]
        self.gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
        self.gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.PatBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        self.shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    def run(self) -> int:
        if not _is_windows():
            return 4
        self._register_window_class()
        self._create_window()
        self._add_icon()
        self.user32.SetTimer(self.hwnd, TIMER_ID_REFRESH_ICON, TRAY_ICON_REFRESH_INTERVAL_MS, None)
        self._start_hotkey_monitor()
        if self.run_seconds and self.run_seconds > 0:
            self.user32.SetTimer(self.hwnd, TIMER_ID_AUTOCLOSE, int(self.run_seconds * 1000), None)
        try:
            self._message_loop()
        finally:
            if self.hwnd:
                self.user32.KillTimer(self.hwnd, TIMER_ID_REFRESH_ICON)
                self.user32.KillTimer(self.hwnd, TIMER_ID_AUTOCLOSE)
            self._stop_hotkey_monitor("tray_gui_exiting")
            self._delete_icon()
            self._destroy_generated_icons()
        return 0

    def _start_hotkey_monitor(self) -> None:
        if not self.enable_hotkey_monitor or not _is_windows():
            self.hotkey_start_report = {
                "object_type": "AIControlTrayGuiHotkeyMonitorStartReport",
                "schema": TRAY_GUI_SCHEMA,
                "hotkey_status": "disabled",
                "physical_hotkey_monitor_enabled": False,
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }
            return
        try:
            self.hotkey_monitor = EmergencyHotkeyMonitor(stop_callback=self._emergency_stop_from_physical_hotkey)
            self.hotkey_start_report = self.hotkey_monitor.start()
        except Exception as exc:
            self.hotkey_start_report = {
                "object_type": "AIControlTrayGuiHotkeyMonitorStartReport",
                "schema": TRAY_GUI_SCHEMA,
                "hotkey_status": "failed",
                "physical_hotkey_monitor_enabled": True,
                "error": str(exc),
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }

    def _stop_hotkey_monitor(self, reason: str) -> None:
        if self.hotkey_monitor:
            self.hotkey_stop_report = self.hotkey_monitor.stop(reason)

    def _emergency_stop_from_physical_hotkey(self, reason: str) -> dict[str, Any]:
        report = emergency_stop(reason)
        return report

    def _register_window_class(self) -> None:
        hinstance = self.kernel32.GetModuleHandleW(None)
        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self._wndproc
        wndclass.hInstance = hinstance
        wndclass.hIcon = self.hicon
        wndclass.lpszClassName = self.class_name
        self.user32.RegisterClassW(ctypes.byref(wndclass))

    def _create_window(self) -> None:
        hwnd = self.user32.CreateWindowExW(
            0,
            self.class_name,
            "AI-Control Tray",
            0,
            0,
            0,
            0,
            0,
            None,
            None,
            self.kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise ctypes.WinError()
        self.hwnd = hwnd

    def _notify_data(self, status: dict[str, Any] | None = None, *, advance_animation: bool = False) -> NOTIFYICONDATAW:
        status = status if isinstance(status, dict) else load_tray_status()
        state = tray_icon_state_for_status(status)
        tooltip = tray_tooltip_for_status(status)
        if state != self._last_icon_state:
            self._animation_frame_index = 0
        elif advance_animation and tray_icon_state_is_animated(state):
            frame_count = max(1, len(tray_icon_frame_specs_for_state(state)))
            self._animation_frame_index = (self._animation_frame_index + 1) % frame_count
        self.hicon = self._icon_for_state_frame(state, self._animation_frame_index)
        self._last_icon_state = state
        self._last_tooltip = tooltip
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self.hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        data.uCallbackMessage = TRAY_MESSAGE_ID
        data.hIcon = self.hicon
        data.szTip = tooltip
        return data

    def _add_icon(self) -> None:
        data = self._notify_data()
        if not self.shell32.Shell_NotifyIconW(0x0, ctypes.byref(data)):
            raise ctypes.WinError()
        self.tray_icon_added = True
        data.uTimeoutOrVersion = 4
        self.shell32.Shell_NotifyIconW(0x4, ctypes.byref(data))

    def _delete_icon(self) -> None:
        if self.hwnd:
            data = self._notify_data()
            self.shell32.Shell_NotifyIconW(0x2, ctypes.byref(data))

    def _refresh_icon(self) -> None:
        if not self.tray_icon_added or not self.hwnd:
            return
        status = load_tray_status()
        state = tray_icon_state_for_status(status)
        tooltip = tray_tooltip_for_status(status)
        animated = tray_icon_state_is_animated(state)
        data = self._notify_data(status, advance_animation=animated and state == self._last_icon_state)
        self.shell32.Shell_NotifyIconW(0x1, ctypes.byref(data))

    def _icon_for_status(self, status: dict[str, Any]) -> int:
        return self._icon_for_state(tray_icon_state_for_status(status))

    def _icon_for_state(self, state: str) -> int:
        return self._icon_for_state_frame(state, 0)

    def _icon_for_state_frame(self, state: str, frame_index: int) -> int:
        try:
            normalized = state if state in TRAY_ICON_STATES else "unknown"
            size = self._system_tray_icon_size()
            frame_count = max(1, len(tray_icon_frame_specs_for_state(normalized)))
            index = int(frame_index) % frame_count
            cache_key = (normalized, index, size)
            cached = self._icon_cache.get(cache_key)
            if cached:
                return cached
            icon = self._create_ai_icon(tray_icon_frame_specs_for_state(normalized)[index], size=size)
            self._icon_cache[cache_key] = icon
            return icon
        except Exception:
            return self.fallback_hicon

    def _system_tray_icon_size(self) -> int:
        width = int(self.user32.GetSystemMetrics(SM_CXSMICON) or 16)
        height = int(self.user32.GetSystemMetrics(SM_CYSMICON) or width)
        return max(16, min(max(width, height), 32))

    def _create_ai_icon(self, spec: dict[str, Any], *, size: int) -> int:
        hdc = self.user32.GetDC(None)
        if not hdc:
            return self.fallback_hicon
        color_bitmap = None
        mask_bitmap = None
        mem_dc = None
        font = None
        try:
            glyph_rgb = tuple(spec["glyph_rgb"])
            color_bitmap = self.gdi32.CreateCompatibleBitmap(hdc, size, size)
            mask_bitmap = self.gdi32.CreateBitmap(size, size, 1, 1, None)
            mem_dc = self.gdi32.CreateCompatibleDC(hdc)
            if not color_bitmap or not mask_bitmap or not mem_dc:
                return self.fallback_hicon
            old_obj = self.gdi32.SelectObject(mem_dc, color_bitmap)
            self.gdi32.PatBlt(mem_dc, 0, 0, size, size, BLACKNESS)
            font_height = -max(10, int(size * 0.72))
            font = self.gdi32.CreateFontW(
                font_height,
                0,
                0,
                0,
                FW_SEMIBOLD,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                "Segoe UI",
            )
            if font:
                self.gdi32.SelectObject(mem_dc, font)
            self.gdi32.SetBkMode(mem_dc, TRANSPARENT)
            self.gdi32.SetTextColor(mem_dc, _rgb_to_colorref(glyph_rgb))
            rect = wintypes.RECT(0, -1 if size <= 20 else 0, size, size)
            self.user32.DrawTextW(
                mem_dc,
                str(spec.get("lettermark") or TRAY_ICON_LETTERMARK),
                -1,
                ctypes.byref(rect),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            self.gdi32.SelectObject(mem_dc, mask_bitmap)
            self.gdi32.PatBlt(mem_dc, 0, 0, size, size, WHITENESS)
            if font:
                self.gdi32.SelectObject(mem_dc, font)
            self.gdi32.SetBkMode(mem_dc, TRANSPARENT)
            self.gdi32.SetTextColor(mem_dc, 0)
            mask_rect = wintypes.RECT(0, -1 if size <= 20 else 0, size, size)
            self.user32.DrawTextW(
                mem_dc,
                str(spec.get("lettermark") or TRAY_ICON_LETTERMARK),
                -1,
                ctypes.byref(mask_rect),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            if old_obj:
                self.gdi32.SelectObject(mem_dc, old_obj)
            icon_info = ICONINFO()
            icon_info.fIcon = True
            icon_info.xHotspot = 0
            icon_info.yHotspot = 0
            icon_info.hbmMask = mask_bitmap
            icon_info.hbmColor = color_bitmap
            icon = self.user32.CreateIconIndirect(ctypes.byref(icon_info))
            if icon:
                self.generated_hicons.append(icon)
                return icon
        finally:
            if font:
                self.gdi32.DeleteObject(font)
            if mem_dc:
                self.gdi32.DeleteDC(mem_dc)
            if color_bitmap:
                self.gdi32.DeleteObject(color_bitmap)
            if mask_bitmap:
                self.gdi32.DeleteObject(mask_bitmap)
            self.user32.ReleaseDC(None, hdc)
        return self.fallback_hicon

    def _destroy_generated_icons(self) -> None:
        for icon in self.generated_hicons:
            if icon and icon != self.fallback_hicon:
                self.user32.DestroyIcon(icon)
        self.generated_hicons.clear()
        self._icon_cache.clear()

    def _message_loop(self) -> None:
        msg = MSG()
        msg_ptr = ctypes.pointer(msg)
        while self.user32.GetMessageW(msg_ptr, None, 0, 0) != 0:
            self.user32.TranslateMessage(msg_ptr)
            self.user32.DispatchMessageW(msg_ptr)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == TRAY_MESSAGE_ID:
            if tray_callback_opens_status(lparam):
                self._show_status()
                return 0
            if tray_callback_opens_menu(lparam):
                self._show_menu()
                return 0
        if message == 0x0111:
            self._handle_command(wparam & 0xFFFF)
            return 0
        if message == 0x0113:
            if wparam == TIMER_ID_AUTOCLOSE:
                self.user32.KillTimer(hwnd, TIMER_ID_AUTOCLOSE)
                self.user32.PostQuitMessage(0)
                return 0
            if wparam == TIMER_ID_REFRESH_ICON:
                self._refresh_icon()
                return 0
            return 0
        if message == 0x0002:
            self.user32.PostQuitMessage(0)
            return 0
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _show_menu(self) -> None:
        menu = self.user32.CreatePopupMenu()
        for item in build_tray_menu_model():
            if item.get("kind") == "separator":
                self.user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                continue
            if item.get("kind") == "submenu":
                submenu = self.user32.CreatePopupMenu()
                for child in item.get("items", []):
                    flags = MF_STRING if child.get("enabled", True) else (MF_STRING | MF_GRAYED | MF_DISABLED)
                    if child.get("checked"):
                        flags |= MF_CHECKED
                    self.user32.AppendMenuW(submenu, flags, int(child["id"]), str(child["label"]))
                self.user32.AppendMenuW(menu, MF_STRING | MF_POPUP, int(submenu), str(item["label"]))
                continue
            flags = MF_STRING if item.get("enabled", True) else (MF_STRING | MF_GRAYED | MF_DISABLED)
            if item.get("checked"):
                flags |= MF_CHECKED
            self.user32.AppendMenuW(menu, flags, int(item["id"]), str(item["label"]))
        point = POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        self.user32.SetForegroundWindow(self.hwnd)
        self.user32.TrackPopupMenu(menu, 0x0002, point.x, point.y, 0, self.hwnd, None)
        self.user32.DestroyMenu(menu)

    def _show_recording_settings_dialog(self, config: dict[str, Any]) -> None:
        model = build_recording_settings_dialog_model(config)
        try:
            settings = show_modern_recording_settings_dialog(model)
            if settings is not None:
                apply_recording_policy_settings(settings)
                self._refresh_icon()
            return
        except Exception as exc:
            try:
                _write_json_path(
                    default_agent_dir() / "tray-modern-recording-settings-error.json",
                    {
                        "object_type": "AgentSightTrayModernRecordingSettingsError",
                        "schema": "agentsight_tray_modern_recording_settings_error_v1",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback_tail": traceback.format_exc()[-4000:],
                        "fallback": "win32_recording_settings_dialog",
                        "host_input_sent": False,
                        "host_sent_event_count": 0,
                        "boundary": boundary_facts(),
                    },
                )
            except Exception:
                pass
        if not _is_windows() or not self.hwnd:
            self._message_box(recording_settings_dialog_text(model), title=str(model["title"]))
            return
        if self.recording_settings_hwnd:
            self.user32.SetForegroundWindow(self.recording_settings_hwnd)
            return
        try:
            self._register_recording_settings_class()
            hinstance = self.kernel32.GetModuleHandleW(None)
            hwnd = self.user32.CreateWindowExW(
                WS_EX_DLGMODALFRAME,
                RECORDING_SETTINGS_WINDOW_CLASS_NAME,
                str(model["title"]),
                WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_VISIBLE,
                520,
                300,
                560,
                520,
                self.hwnd,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                raise ctypes.WinError()
            self.recording_settings_hwnd = hwnd
            self._populate_recording_settings_window(hwnd, model)
            self.user32.SetForegroundWindow(hwnd)
        except Exception:
            self._message_box(recording_settings_dialog_text(model), title=str(model["title"]))

    def _register_recording_settings_class(self) -> None:
        if self.recording_settings_class_registered:
            return
        hinstance = self.kernel32.GetModuleHandleW(None)
        wndclass = WNDCLASSW()
        wndclass.lpfnWndProc = self._recording_settings_wndproc
        wndclass.hInstance = hinstance
        wndclass.hIcon = self.hicon
        wndclass.lpszClassName = RECORDING_SETTINGS_WINDOW_CLASS_NAME
        self.user32.RegisterClassW(ctypes.byref(wndclass))
        self.recording_settings_class_registered = True

    def _populate_recording_settings_window(self, hwnd: int, model: dict[str, Any]) -> None:
        hinstance = self.kernel32.GetModuleHandleW(None)
        self._create_child_window(hwnd, "STATIC", str(model.get("note") or ""), 16, 14, 510, 34, 0, SS_LEFT)
        y = 56
        for control in model.get("controls", []):
            if control.get("kind") == "section":
                self._create_child_window(hwnd, "STATIC", str(control.get("label") or ""), 18, y, 500, 22, 0, SS_LEFT)
                y += 26
                continue
            control_type = str(control.get("control_type") or "checkbox")
            label = str(control.get("label") or control.get("key") or "")
            if control_type == "checkbox":
                child = self._create_child_window(hwnd, "BUTTON", label, 28, y, 460, 24, int(control["id"]), BS_AUTOCHECKBOX)
                if control.get("checked"):
                    self.user32.SendMessageW(child, BM_SETCHECK, BST_CHECKED, 0)
                y += 30
                continue
            self._create_child_window(hwnd, "STATIC", label, 28, y + 4, 210, 22, 0, SS_LEFT)
            edit_style = WS_BORDER | ES_AUTOHSCROLL
            if control_type == "integer":
                edit_style |= ES_NUMBER
            self._create_child_window(hwnd, "EDIT", str(control.get("value") or ""), 250, y, 110, 24, int(control["id"]), edit_style)
            hint = str(control.get("unit") or "")
            if control.get("min") is not None and control.get("max") is not None:
                hint = f"{hint} ({control.get('min')}-{control.get('max')})".strip()
            self._create_child_window(hwnd, "STATIC", hint, 370, y + 4, 145, 22, 0, SS_LEFT)
            y += 30
        self._create_child_window(hwnd, "BUTTON", tray_text("save", language=model.get("language")), 342, 450, 86, 28, IDOK, BS_PUSHBUTTON)
        self._create_child_window(hwnd, "BUTTON", tray_text("cancel", language=model.get("language")), 442, 450, 86, 28, IDCANCEL, BS_PUSHBUTTON)

    def _create_child_window(
        self,
        parent: int,
        class_name: str,
        text: str,
        x: int,
        y: int,
        width: int,
        height: int,
        control_id: int,
        style_extra: int,
    ) -> int:
        hwnd = self.user32.CreateWindowExW(
            0,
            class_name,
            text,
            WS_CHILD | WS_VISIBLE | style_extra,
            x,
            y,
            width,
            height,
            parent,
            wintypes.HMENU(control_id),
            self.kernel32.GetModuleHandleW(None),
            None,
        )
        if not hwnd:
            raise ctypes.WinError()
        return int(hwnd)

    def _recording_settings_window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        if message == 0x0111:
            command_id = int(wparam) & 0xFFFF
            if command_id == IDOK:
                self._apply_recording_settings_window(hwnd)
                self.user32.DestroyWindow(hwnd)
                return 0
            if command_id == IDCANCEL:
                self.user32.DestroyWindow(hwnd)
                return 0
        if message == 0x0010:
            self.user32.DestroyWindow(hwnd)
            return 0
        if message == 0x0002:
            if self.recording_settings_hwnd == hwnd:
                self.recording_settings_hwnd = None
            return 0
        return self.user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _apply_recording_settings_window(self, hwnd: int) -> None:
        settings = {
            "continuous_recording_enabled": self.user32.IsDlgButtonChecked(hwnd, IDM_RECORDING_IDLE_CAPTURE) == BST_CHECKED,
            "idle_fps": self._dialog_text(hwnd, IDM_RECORDING_IDLE_FPS),
            "action_capture_enabled": self.user32.IsDlgButtonChecked(hwnd, IDM_RECORDING_OPERATION_CAPTURE) == BST_CHECKED,
            "capture_pre_action_frame": self.user32.IsDlgButtonChecked(hwnd, IDM_RECORDING_PRE_ACTION_FRAME) == BST_CHECKED,
            "capture_post_action_frames": self.user32.IsDlgButtonChecked(hwnd, IDM_RECORDING_POST_ACTION_FRAMES) == BST_CHECKED,
            "post_action_fps": self._dialog_text(hwnd, IDM_RECORDING_POST_ACTION_FPS),
            "post_action_duration_ms": self._dialog_text(hwnd, IDM_RECORDING_POST_ACTION_DURATION_MS),
            "max_post_action_frames": self._dialog_text(hwnd, IDM_RECORDING_MAX_POST_ACTION_FRAMES),
            "retention_days": self._dialog_text(hwnd, IDM_RECORDING_RETENTION_DAYS),
            "max_storage_mb": self._dialog_text(hwnd, IDM_RECORDING_MAX_STORAGE_MB),
            "min_free_disk_mb": self._dialog_text(hwnd, IDM_RECORDING_MIN_FREE_DISK_MB),
        }
        apply_recording_policy_settings(settings)
        self._refresh_icon()

    def _dialog_text(self, hwnd: int, control_id: int) -> str:
        buffer = ctypes.create_unicode_buffer(128)
        self.user32.GetDlgItemTextW(hwnd, int(control_id), buffer, 128)
        return str(buffer.value)

    def _handle_command(self, command_id: int) -> None:
        if command_id == IDM_STATUS:
            self._show_status()
        elif command_id == IDM_PAUSE_AI_CONTROL:
            pause_ai_control("operator_requested_from_tray_gui")
            self._refresh_icon()
        elif command_id == IDM_ALLOW_AI_CONTROL:
            allow_ai_control("operator_requested_from_tray_gui")
            self._refresh_icon()
        elif command_id == IDM_EMERGENCY_STOP:
            emergency_stop("operator_requested_from_tray_gui")
            self._refresh_icon()
        elif command_id == IDM_CLEAR_EMERGENCY:
            clear_emergency()
            self._refresh_icon()
        elif command_id == IDM_OPEN_RECORDING_SETTINGS:
            config = write_default_tray_config_if_missing()
            self._show_recording_settings_dialog(config)
        elif command_id == IDM_OPEN_TIMELINE:
            launch_timeline_viewer_process(mode="timeline")
        elif command_id == IDM_STOP_AI_CONTROL:
            report = stop_ai_control("operator_requested_stop_ai_control_from_tray_gui")
            supervisor_stop = report.get("supervisor_stop", {}) if isinstance(report, dict) else {}
            host_status = (supervisor_stop.get("host_agent_process") or {}).get("status") if isinstance(supervisor_stop, dict) else None
            self._message_box(f"{tray_text('stop_requested_message')}\n\n{tray_text('host_agent_status')}: {host_status or tray_text('unknown')}")
            self.user32.PostQuitMessage(0)
        elif command_id == IDM_LANGUAGE_FOLLOW_SYSTEM:
            save_tray_language("system")
            self._refresh_icon()
            self._message_box(tray_text("language_changed"))
        elif command_id == IDM_LANGUAGE_ZH:
            save_tray_language("zh")
            self._refresh_icon()
            self._message_box(tray_text("language_changed", language="zh"))
        elif command_id == IDM_LANGUAGE_EN:
            save_tray_language("en")
            self._refresh_icon()
            self._message_box(tray_text("language_changed", language="en"))

    def _show_status(self) -> None:
        self._message_box(status_summary_text())

    def _message_box(self, text: str, *, title: str = "AI-Control") -> None:
        self.user32.MessageBoxW(self.hwnd, text, title, 0x40)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI-Control Windows tray GUI.")
    subcommands = parser.add_subparsers(dest="command")
    run = subcommands.add_parser("run", description="Run the Windows notification-area tray icon.")
    run.add_argument("--seconds", type=int, default=None, help="Auto-exit after N seconds; intended for smoke tests.")
    run.add_argument("--output", default=None, help="Optional JSON run report path.")
    run.add_argument(
        "--no-hotkey",
        action="store_true",
        help="Do not start the physical emergency hotkey monitor with the tray; intended for smoke/debug only.",
    )
    describe = subcommands.add_parser("describe", description="Write or print a tray GUI capability description.")
    describe.add_argument("--output", default=None)
    watchdog = subcommands.add_parser("watchdog", description="Keep the Windows tray icon visible in the current user session.")
    watchdog.add_argument("--interval-seconds", type=float, default=5.0)
    watchdog.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    watchdog.add_argument("--output", default=None)
    install = subcommands.add_parser("install-resident", description="Install current-user startup/watchdog for the tray icon.")
    install.add_argument("--repo-root", default=str(_default_repo_root()))
    install.add_argument("--python", default=_default_python_command())
    install.add_argument("--tray-gui-exe", default=_default_tray_gui_exe_arg())
    install.add_argument("--start-now", action="store_true")
    install.add_argument("--start-method", choices=["auto", "onlogon_task", "startup_vbs"], default="auto")
    install.add_argument("--wait-seconds", type=float, default=10.0)
    start = subcommands.add_parser("start-resident", description="Start the installed tray watchdog once.")
    start.add_argument("--start-method", choices=["auto", "onlogon_task", "startup_vbs"], default="auto")
    start.add_argument("--wait-seconds", type=float, default=10.0)
    status = subcommands.add_parser("status-resident", description="Report tray resident/watchdog installation status.")
    status.add_argument("--output", default=None)
    uninstall = subcommands.add_parser("uninstall-resident", description="Stop and remove tray startup/watchdog files.")
    uninstall.add_argument("--keep-tray-running", action="store_true")
    args = parser.parse_args(argv)
    command = args.command or "run"
    if command == "describe":
        write_json_report(build_tray_gui_description(), args.output)
        return 0
    if command == "watchdog":
        return run_tray_gui_watchdog(
            interval_seconds=float(args.interval_seconds),
            once=bool(args.once),
            output=args.output,
        )
    if command == "install-resident":
        report = install_tray_gui_resident(
            repo_root=Path(args.repo_root),
            python_command=args.python,
            tray_gui_exe=Path(args.tray_gui_exe) if args.tray_gui_exe else None,
            start_now=bool(args.start_now),
            start_method=args.start_method,
            wait_seconds=float(args.wait_seconds),
        )
        write_json_report(report, None)
        return int(report.get("exit_code", 0))
    if command == "start-resident":
        report = start_installed_tray_gui_resident(
            start_method=args.start_method,
            wait_seconds=float(args.wait_seconds),
        )
        write_json_report(report, None)
        return int(report.get("exit_code", 0))
    if command == "status-resident":
        report = tray_gui_resident_status()
        write_json_report(report, args.output)
        return int(report.get("exit_code", 0))
    if command == "uninstall-resident":
        report = uninstall_tray_gui_resident(stop_running=not bool(args.keep_tray_running))
        write_json_report(report, None)
        return int(report.get("exit_code", 0))
    started_at = int(time.time() * 1000)
    try:
        run_seconds = getattr(args, "seconds", None)
        output = getattr(args, "output", None)
        hotkey_enabled = not bool(getattr(args, "no_hotkey", False))
        if _tray_window_present():
            write_json_report(
                build_tray_gui_already_running_report(
                    run_seconds_requested=run_seconds,
                    started_at_ms=started_at,
                    ended_at_ms=int(time.time() * 1000),
                    physical_hotkey_monitor_requested=hotkey_enabled,
                ),
                output,
            )
            return 0
        app = Win32TrayApp(run_seconds=run_seconds, enable_hotkey_monitor=hotkey_enabled)
        exit_code = app.run()
        ended_at = int(time.time() * 1000)
        if output:
            write_json_report(
                build_tray_gui_run_report(
                    tray_icon_gui_started=app.tray_icon_added,
                    tray_icon_added=app.tray_icon_added,
                    run_seconds_requested=run_seconds,
                    started_at_ms=started_at,
                    ended_at_ms=ended_at,
                    exit_code=exit_code,
                    physical_hotkey_monitor_enabled=hotkey_enabled,
                    hotkey_start_report=app.hotkey_start_report,
                    hotkey_trigger_report=app.hotkey_monitor.trigger_report if app.hotkey_monitor else None,
                    hotkey_stop_report=app.hotkey_stop_report,
                ),
                output,
            )
        return exit_code
    except Exception as exc:
        report = {
            "object_type": "AIControlTrayGuiRunFailure",
            "schema": TRAY_GUI_SCHEMA,
            "tray_icon_gui_started": False,
            "started_at_ms": started_at,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "exit_code": 4,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        write_json_report(report, getattr(args, "output", None))
        return 4


def default_tray_watchdog_report_file() -> Path:
    return default_agent_dir() / "last-tray-watchdog-report.json"


def default_tray_watchdog_stop_file() -> Path:
    return default_agent_dir() / "tray-watchdog.stop"


def default_tray_resident_command_file() -> Path:
    return default_agent_dir() / "AIControlTrayGuiWatchdog.cmd"


def default_tray_resident_vbs_file() -> Path:
    startup = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "AIControlTrayGui.vbs"


def tray_gui_resident_status() -> dict[str, Any]:
    command_file = default_tray_resident_command_file()
    vbs_file = default_tray_resident_vbs_file()
    stop_file = default_tray_watchdog_stop_file()
    report_file = default_tray_watchdog_report_file()
    tray_window_present = _tray_window_present()
    return {
        "object_type": "AIControlTrayGuiResidentStatus",
        "schema": TRAY_RESIDENT_SCHEMA,
        "resident_role": "human_visible_tray_presence",
        "tray_window_present": tray_window_present,
        "tray_icon_expected_visible": tray_window_present,
        "installed": command_file.exists() and vbs_file.exists(),
        "startup_launcher": str(vbs_file),
        "watchdog_command": str(command_file),
        "watchdog_stop_file": str(stop_file),
        "watchdog_stop_requested": stop_file.exists(),
        "watchdog_report_file": str(report_file),
        "last_watchdog_report": _read_json_file(report_file),
        "onlogon_task": {
            "task_name": TRAY_ONLOGON_TASK_NAME,
            "schedule": "ONLOGON",
            "run_level": "LIMITED",
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }


def install_tray_gui_resident(
    *,
    repo_root: Path,
    python_command: str,
    tray_gui_exe: Path | None,
    start_now: bool,
    wait_seconds: float,
    start_method: str = "auto",
) -> dict[str, Any]:
    default_agent_dir().mkdir(parents=True, exist_ok=True)
    default_tray_resident_vbs_file().parent.mkdir(parents=True, exist_ok=True)
    default_tray_watchdog_stop_file().unlink(missing_ok=True)
    command = _tray_watchdog_command(
        repo_root=repo_root,
        python_command=python_command,
        tray_gui_exe=tray_gui_exe if tray_gui_exe and tray_gui_exe.exists() else None,
    )
    command_file = default_tray_resident_command_file()
    vbs_file = default_tray_resident_vbs_file()
    command_file.write_text(command, encoding="utf-8")
    vbs_file.write_text(_hidden_vbs(command_file), encoding="ascii")
    onlogon_task = _install_tray_onlogon_task(command_file)
    report: dict[str, Any] = {
        "object_type": "AIControlTrayGuiResidentInstallReport",
        "schema": TRAY_RESIDENT_SCHEMA,
        "install_status": "installed",
        "startup_launcher": str(vbs_file),
        "watchdog_command": str(command_file),
        "watchdog_report_file": str(default_tray_watchdog_report_file()),
        "watchdog_stop_file": str(default_tray_watchdog_stop_file()),
        "repo_root": str(repo_root),
        "python_command": python_command,
        "tray_gui_exe": str(tray_gui_exe) if tray_gui_exe else None,
        "install_mode": "tray_gui_exe" if tray_gui_exe and tray_gui_exe.exists() else "source_python",
        "start_now": start_now,
        "start_method": start_method,
        "onlogon_task": onlogon_task,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }
    if start_now:
        report["start"] = start_installed_tray_gui_resident(
            start_method=start_method,
            wait_seconds=wait_seconds,
        )
        if report["start"].get("exit_code") not in {0, None}:
            report["exit_code"] = report["start"].get("exit_code", 5)
    _write_tray_resident_report(report, "last-tray-installer-report.json")
    return report


def start_installed_tray_gui_resident(*, start_method: str = "auto", wait_seconds: float = 10.0) -> dict[str, Any]:
    vbs_file = default_tray_resident_vbs_file()
    command_file = default_tray_resident_command_file()
    if not vbs_file.exists() or not command_file.exists():
        return {
            "object_type": "AIControlTrayGuiResidentStartReport",
            "schema": TRAY_RESIDENT_SCHEMA,
            "start_status": "not_installed",
            "startup_launcher": str(vbs_file),
            "watchdog_command": str(command_file),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
            "exit_code": 4,
        }
    default_tray_watchdog_stop_file().unlink(missing_ok=True)
    launcher = _start_tray_hidden(vbs_file, command_path=command_file, start_method=start_method)
    visible = _wait_for_tray_window(wait_seconds=wait_seconds)
    report = {
        "object_type": "AIControlTrayGuiResidentStartReport",
        "schema": TRAY_RESIDENT_SCHEMA,
        "start_status": "started" if launcher.get("started") else "start_attempted",
        "startup_launcher": str(vbs_file),
        "watchdog_command": str(command_file),
        "start_method": start_method,
        "launcher": launcher,
        "tray_visible_after_wait": visible,
        "watchdog_report_file": str(default_tray_watchdog_report_file()),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0 if launcher.get("started") else 5,
    }
    _write_tray_resident_report(report, "last-tray-start-report.json")
    return report


def uninstall_tray_gui_resident(*, stop_running: bool = True) -> dict[str, Any]:
    stop_file = default_tray_watchdog_stop_file()
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text("AI-Control tray watchdog stop requested.\n", encoding="utf-8")
    command_file = default_tray_resident_command_file()
    vbs_file = default_tray_resident_vbs_file()
    command_existed = command_file.exists()
    vbs_existed = vbs_file.exists()
    command_file.unlink(missing_ok=True)
    vbs_file.unlink(missing_ok=True)
    task_delete = _delete_tray_onlogon_task()
    close_report = _request_tray_window_close() if stop_running else {"close_requested": False, "reason": "keep_tray_running"}
    report = {
        "object_type": "AIControlTrayGuiResidentUninstallReport",
        "schema": TRAY_RESIDENT_SCHEMA,
        "uninstall_status": "removed",
        "watchdog_stop_file": str(stop_file),
        "watchdog_stop_requested": True,
        "watchdog_command_removed": command_existed,
        "startup_launcher_removed": vbs_existed,
        "onlogon_task": task_delete,
        "tray_close": close_report,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }
    _write_tray_resident_report(report, "last-tray-uninstall-report.json")
    return report


def run_tray_gui_watchdog(*, interval_seconds: float, once: bool = False, output: str | None = None) -> int:
    started_any = False
    cycle = 0
    report: dict[str, Any] = {}
    while True:
        cycle += 1
        if _unified_session_supervisor_enabled_file().exists():
            report = _tray_watchdog_report(
                watchdog_status="stopped_by_unified_session_supervisor",
                cycle=cycle,
                tray_window_present=_tray_window_present(),
                started_child=False,
                child_start={
                    "action": "none",
                    "reason": "unified_session_supervisor_enabled",
                    "unified_supervisor_enabled_file": str(_unified_session_supervisor_enabled_file()),
                },
            )
            _write_json_path(default_tray_watchdog_report_file(), report)
            if output:
                _write_json_path(Path(output), report)
            return 0
        if default_tray_watchdog_stop_file().exists():
            report = _tray_watchdog_report(
                watchdog_status="stopped_by_stop_file",
                cycle=cycle,
                tray_window_present=_tray_window_present(),
                started_child=False,
            )
            _write_json_path(default_tray_watchdog_report_file(), report)
            if output:
                _write_json_path(Path(output), report)
            return 0
        tray_window_present = _tray_window_present()
        child: dict[str, Any] | None = None
        if not tray_window_present:
            child = _start_tray_gui_child()
            started_any = started_any or bool(child.get("started"))
            time.sleep(0.5)
            tray_window_present = _tray_window_present()
        report = _tray_watchdog_report(
            watchdog_status="running",
            cycle=cycle,
            tray_window_present=tray_window_present,
            started_child=bool(child and child.get("started")),
            child_start=child,
            started_any=started_any,
        )
        _write_json_path(default_tray_watchdog_report_file(), report)
        if output:
            _write_json_path(Path(output), report)
        if once:
            return 0
        time.sleep(max(0.5, interval_seconds))


def _tray_watchdog_report(
    *,
    watchdog_status: str,
    cycle: int,
    tray_window_present: bool,
    started_child: bool,
    child_start: dict[str, Any] | None = None,
    started_any: bool = False,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlTrayGuiWatchdogReport",
        "schema": TRAY_RESIDENT_SCHEMA,
        "watchdog_status": watchdog_status,
        "watchdog_pid": os.getpid(),
        "watchdog_cycle": cycle,
        "tray_window_class": TRAY_WINDOW_CLASS_NAME,
        "tray_window_present": tray_window_present,
        "tray_icon_expected_visible": tray_window_present,
        "started_child": started_child,
        "started_any": started_any,
        "child_start": child_start,
        "watchdog_stop_file": str(default_tray_watchdog_stop_file()),
        "watchdog_report_file": str(default_tray_watchdog_report_file()),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _tray_window_present() -> bool:
    if not _is_windows():
        return False
    try:
        hwnd = ctypes.windll.user32.FindWindowW(TRAY_WINDOW_CLASS_NAME, None)
    except Exception:
        return False
    return bool(hwnd)


def _request_tray_window_close() -> dict[str, Any]:
    if not _is_windows():
        return {"close_requested": False, "reason": "requires_windows"}
    try:
        hwnd = ctypes.windll.user32.FindWindowW(TRAY_WINDOW_CLASS_NAME, None)
        if not hwnd:
            return {"close_requested": False, "reason": "tray_window_not_found"}
        posted = bool(ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0))
        return {"close_requested": posted, "hwnd_present": True}
    except Exception as exc:
        return {"close_requested": False, "error": str(exc)}


def _unified_session_supervisor_enabled_file() -> Path:
    return default_agent_dir() / "unified-session-supervisor.enabled"


def _start_tray_gui_child() -> dict[str, Any]:
    command = _tray_gui_child_command()
    try:
        process = Popen(  # noqa: S603 - command is self/module path only.
            command,
            cwd=str(_default_repo_root()),
            stdout=DEVNULL,
            stderr=DEVNULL,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )
        return {"started": True, "pid": process.pid, "command": _redact_command(command)}
    except Exception as exc:
        return {"started": False, "command": _redact_command(command), "error": str(exc)}


def _tray_gui_child_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "run"]
    return [sys.executable, "-m", "ai_control.tray.gui", "run"]


def _tray_watchdog_command(*, repo_root: Path, python_command: str, tray_gui_exe: Path | None) -> str:
    if tray_gui_exe:
        launch_line = f'"{tray_gui_exe}" watchdog'
        return "\r\n".join(["@echo off", "chcp 65001 >nul", launch_line, ""])
    return "\r\n".join(
        [
            "@echo off",
            "chcp 65001 >nul",
            f'cd /d "{repo_root}"',
            "set PYTHONPATH=src",
            f'"{python_command}" -m ai_control.tray.gui watchdog',
            "",
        ]
    )


def _hidden_vbs(cmd_path: Path) -> str:
    return "\n".join(
        [
            'Set shell = CreateObject("WScript.Shell")',
            f'shell.Run """" & "{cmd_path}" & """", 0, False',
            "",
        ]
    )


def _start_tray_hidden(vbs_path: Path, *, command_path: Path, start_method: str = "auto") -> dict[str, Any]:
    if start_method in {"auto", "onlogon_task"}:
        task_report = _run_tray_onlogon_task()
        if task_report.get("started") or start_method == "onlogon_task":
            return task_report
    if start_method in {"auto", "startup_vbs"}:
        return _start_via_startup_vbs(vbs_path)
    return _start_via_startup_vbs(vbs_path)


def _start_via_startup_vbs(vbs_path: Path) -> dict[str, Any]:
    try:
        os.startfile(str(vbs_path))  # type: ignore[attr-defined]
        return {"start_method_used": "startup_vbs", "started": True, "startup_launcher": str(vbs_path)}
    except Exception as exc:
        return {"start_method_used": "startup_vbs", "started": False, "startup_launcher": str(vbs_path), "error": str(exc)}


def _install_tray_onlogon_task(command_path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"task_name": TRAY_ONLOGON_TASK_NAME, "install_status": "skipped_non_windows", "task_launcher": str(command_path)}
    create = run_schtasks(
        [
            "/Create",
            "/TN",
            TRAY_ONLOGON_TASK_NAME,
            "/TR",
            str(command_path),
            "/SC",
            "ONLOGON",
            "/RL",
            "LIMITED",
            "/F",
        ]
    )
    return {
        "task_name": TRAY_ONLOGON_TASK_NAME,
        "install_status": "installed" if create.returncode == 0 else "install_failed",
        "task_launcher": str(command_path),
        "schedule": "ONLOGON",
        "run_level": "LIMITED",
        "create": completed_process_report(create),
    }


def _run_tray_onlogon_task() -> dict[str, Any]:
    if os.name != "nt":
        return {"start_method_used": "onlogon_task", "started": False, "task_name": TRAY_ONLOGON_TASK_NAME, "error": "requires_windows"}
    run = run_schtasks(["/Run", "/TN", TRAY_ONLOGON_TASK_NAME])
    return {
        "start_method_used": "onlogon_task",
        "started": run.returncode == 0,
        "task_name": TRAY_ONLOGON_TASK_NAME,
        "run": completed_process_report(run),
    }


def _delete_tray_onlogon_task() -> dict[str, Any]:
    if os.name != "nt":
        return {"task_name": TRAY_ONLOGON_TASK_NAME, "delete_status": "skipped_non_windows"}
    delete = run_schtasks(["/Delete", "/TN", TRAY_ONLOGON_TASK_NAME, "/F"])
    return {
        "task_name": TRAY_ONLOGON_TASK_NAME,
        "delete_status": "deleted" if delete.returncode == 0 else "delete_failed_or_missing",
        "delete": completed_process_report(delete),
    }


def _wait_for_tray_window(*, wait_seconds: float) -> bool:
    deadline = time.time() + max(0.0, wait_seconds)
    while time.time() <= deadline:
        if _tray_window_present():
            return True
        time.sleep(0.25)
    return _tray_window_present()


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_python_command() -> str:
    return Path(sys.executable).name if Path(sys.executable).name else "py"


def _default_tray_gui_exe_arg() -> str:
    candidate = _default_adjacent_tray_gui_exe()
    return str(candidate) if candidate else ""


def _default_adjacent_tray_gui_exe() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    executable = Path(sys.executable)
    candidate = executable.with_name("AIControlTrayGui.exe")
    return candidate if candidate.exists() else executable


def _write_tray_resident_report(report: dict[str, Any], filename: str) -> None:
    _write_json_path(default_agent_dir() / filename, report)


def _write_json_path(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _redact_command(command: list[str]) -> list[str]:
    return [str(part) for part in command]


if __name__ == "__main__":
    raise SystemExit(main())
