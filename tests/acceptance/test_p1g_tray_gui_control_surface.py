from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentsight.operator_notifications import (
    claim_next_notification,
    enqueue_notification,
    prepare_notification_delivery_draft,
)
from agentsight.storage_quota import apply_storage_quota
from agentsight.tray.ai_status import ai_status_report
from agentsight.tray.cli import main as tray_cli_main
from agentsight.tray.cli import ai_help_report
import agentsight.tray.gui as tray_gui_module
import agentsight.tray.timeline_viewer as timeline_viewer_module
from agentsight.tray.gui import (
    IDM_CLEAR_EMERGENCY,
    IDM_EMERGENCY_STOP,
    IDM_ALLOW_AGENTSIGHT,
    IDM_LANGUAGE_EN,
    IDM_LANGUAGE_FOLLOW_SYSTEM,
    IDM_LANGUAGE_ZH,
    IDM_OPEN_RECORDING_SETTINGS,
    IDM_OPEN_TIMELINE,
    IDM_PAUSE_AGENTSIGHT,
    IDM_STATE_LABEL,
    IDM_STOP_AGENTSIGHT,
    IDM_STATUS,
    NIN_KEYSELECT,
    NIN_SELECT,
    TRAY_ICON_CONSIDERED_SIZES,
    TRAY_ICON_LETTERMARK,
    TRAY_ICON_REFRESH_INTERVAL_MS,
    TRAY_ICON_STATES,
    TRAY_MENU_ITEMS,
    WM_CONTEXTMENU,
    WM_LBUTTONDOWN,
    WM_LBUTTONDBLCLK,
    WM_LBUTTONUP,
    WM_RBUTTONDOWN,
    WM_RBUTTONUP,
    build_tray_menu_model,
    build_recording_settings_dialog_model,
    Win32TrayApp,
    build_tray_gui_already_running_report,
    build_tray_gui_run_report,
    build_tray_gui_description,
    install_tray_gui_resident,
    main as tray_gui_main,
    run_tray_gui_watchdog,
    start_installed_tray_gui_resident,
    status_summary_text,
    load_tray_settings,
    save_tray_language,
    tray_icon_state_for_status,
    tray_tooltip_for_status,
    tray_gui_resident_status,
    tray_settings_path,
    tray_callback_event_code,
    tray_callback_opens_menu,
    tray_callback_opens_status,
    tray_icon_animation_plan_for_state,
    tray_icon_frame_specs_for_state,
    tray_icon_state_is_animated,
    uninstall_tray_gui_resident,
)
from agentsight.tray.state import (
    apply_recording_policy_settings,
    default_recording_policy,
    default_tray_config_file,
    load_tray_status,
    read_jsonc_file,
    set_recording_policy_flag,
    write_default_tray_config_if_missing,
)
from agentsight.tray.viewers import (
    append_operation_log,
    build_timeline_model,
    materialize_look_preview_cache,
    materialize_look_preview_cache_from_operation_log,
    public_operation_log_entry,
    read_operation_log,
)
from agentsight.tray.timeline_viewer import decode_frame_to_qimage, launch_timeline_viewer_process


class P1GTrayGuiControlSurfaceTest(unittest.TestCase):
    def _write_timeline_mkv_index(self, local: Path, segment_name: str, timestamps_ms: list[int], *, mtime: int | None = None) -> Path:
        segment_path = local / "AgentSight" / "runs_host_agent" / "segments" / f"{segment_name}.mkv"
        segment_path.parent.mkdir(parents=True, exist_ok=True)
        segment_path.write_bytes(b"")
        index_path = segment_path.with_suffix(".frames.jsonl")
        records = []
        for index, timestamp_ms in enumerate(timestamps_ms):
            records.append(
                {
                    "segment_id": segment_name,
                    "frame_id": f"{segment_name}-f{index:06d}",
                    "frame_index": index,
                    "timestamp_ms": timestamp_ms,
                    "pts_ms": index * 40,
                    "playback_pts_ms": index * 40,
                    "playback_time_basis": "test_index",
                    "source": "test",
                }
            )
        index_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
        if mtime is not None:
            os.utime(segment_path, (mtime, mtime))
            os.utime(index_path, (mtime, mtime))
        return index_path

    def test_tray_gui_description_declares_visible_controls_and_boundaries(self) -> None:
        report = build_tray_gui_description()

        self.assertEqual(report["schema"], "agentsight_tray_gui_v1")
        self.assertEqual(report["tray_icon_api"], "Shell_NotifyIconW")
        self.assertEqual(report["status_window"], "MessageBoxW")
        self.assertEqual(report["tray_icon_gui_available"], tray_gui_module._is_windows())
        self.assertTrue(report["controls"]["emergency_stop"])
        self.assertFalse(report["controls"]["open_agent_data_folder"])
        self.assertTrue(report["controls"]["physical_emergency_hotkey"])
        self.assertEqual(
            report["controls"]["physical_emergency_hotkey_monitor_started_by_default_on_run"],
            tray_gui_module._is_windows(),
        )
        self.assertTrue(report["tray_icon_state_model"]["runtime_generated_win32_gdi_icons"])
        self.assertTrue(report["tray_icon_state_model"]["runtime_generated_hicon_frames"])
        self.assertTrue(report["tray_icon_state_model"]["multi_frame_notifyicon_animation"])
        self.assertEqual(report["tray_icon_state_model"]["lettermark"], "AS")
        self.assertEqual(report["tray_icon_state_model"]["icon_visual_style"], "AS uppercase lettermark")
        self.assertTrue(report["tray_icon_state_model"]["transparent_background"])
        self.assertTrue(report["tray_icon_state_model"]["colored_letter_glyphs"])
        self.assertTrue(report["tray_icon_state_model"]["no_background_shape"])
        self.assertFalse(report["tray_icon_state_model"]["background_shapes"])
        self.assertTrue(report["tray_icon_state_model"]["ready_animation_enabled"])
        self.assertEqual(report["tray_icon_state_model"]["considered_sizes"], [16, 20, 24, 32])
        self.assertEqual(report["tray_icon_state_model"]["animation_interval_ms"], TRAY_ICON_REFRESH_INTERVAL_MS)
        self.assertTrue(report["tray_icon_state_model"]["blocked_and_emergency_static"])
        self.assertTrue(report["tray_icon_state_model"]["status_change_refreshes_icon_immediately"])
        self.assertTrue(report["tray_icon_state_model"]["tooltip_reflects_status"])
        self.assertTrue(report["menu_model"]["dynamic_from_tray_status"])
        self.assertTrue(report["menu_model"]["i18n_enabled"])
        self.assertTrue(report["menu_model"]["language_menu_present"])
        self.assertEqual(report["menu_model"]["default_language"], "follow_windows_system_language")
        self.assertEqual(report["menu_model"]["supported_languages"], ["system", "zh", "en"])
        self.assertIn("tray-settings.json", report["menu_model"]["settings_path"])
        self.assertFalse(report["menu_model"]["clipboard_action_present"])
        self.assertFalse(report["menu_model"]["open_evidence_folder"])
        self.assertFalse(report["menu_model"]["open_agent_data_folder"])
        self.assertFalse(report["menu_model"]["exit_tray_only"])
        self.assertTrue(report["menu_model"]["recording_settings_present"])
        self.assertFalse(report["menu_model"]["recording_policy_toggles_present"])
        self.assertTrue(report["menu_model"]["timeline_menu_present"])
        self.assertFalse(report["menu_model"]["operation_log_menu_present"])
        self.assertTrue(report["menu_model"]["operation_log_integrated_into_timeline"])
        self.assertFalse(report["physical_hotkey_monitor"]["status_or_describe_starts_monitor"])
        self.assertEqual(report["recording_configuration"]["schema"], "agentsight_tray_config_v1")
        self.assertIn("tray-config.jsonc", report["recording_configuration"]["config_path"])
        self.assertEqual(report["recording_configuration"]["idle_capture_default_fps"], 0.1)
        self.assertEqual(report["recording_configuration"]["idle_capture_min_fps"], 0.1)
        self.assertEqual(report["recording_configuration"]["action_capture_default_fps"], 1)
        self.assertFalse(report["recording_configuration"]["action_capture_max_post_action_frames_required"])
        self.assertFalse(report["recording_configuration"]["recording_policy_toggles_in_menu"])
        self.assertEqual(report["recording_configuration"]["settings_viewer"], "modern_scrollable_tkinter_dialog")
        self.assertEqual(report["recording_configuration"]["settings_fallback_viewer"], "native_win32_dialog")
        self.assertEqual(report["recording_configuration"]["settings_dialog_model"], "agentsight_recording_settings_dialog_v1")
        self.assertEqual(report["recording_configuration"]["timeline_viewer"], "pyside6_qt_native_window")
        self.assertEqual(report["recording_configuration"]["operation_log_viewer"], "integrated_into_timeline_viewer")
        self.assertTrue(report["recording_configuration"]["html_viewer_removed"])
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertFalse(report["boundary"]["business_success_judged"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_tray_menu_command_ids_are_stable_and_complete(self) -> None:
        ids = {item["id"] for item in TRAY_MENU_ITEMS}
        keys = {item["key"] for item in TRAY_MENU_ITEMS}

        self.assertEqual(
            ids,
            {
                IDM_STATUS,
                IDM_STATE_LABEL,
                IDM_PAUSE_AGENTSIGHT,
                IDM_ALLOW_AGENTSIGHT,
                IDM_EMERGENCY_STOP,
                IDM_CLEAR_EMERGENCY,
                IDM_OPEN_RECORDING_SETTINGS,
                IDM_OPEN_TIMELINE,
                0,
                IDM_LANGUAGE_FOLLOW_SYSTEM,
                IDM_LANGUAGE_ZH,
                IDM_LANGUAGE_EN,
                IDM_STOP_AGENTSIGHT,
            },
        )
        self.assertEqual(
            keys,
            {
                "status",
                "state_label",
                "pause_agentsight",
                "allow_agentsight",
                "emergency_stop",
                "clear_emergency_stop",
                "open_recording_settings",
                "open_timeline",
                "language",
                "language_follow_system",
                "language_zh",
                "language_en",
                "stop_agentsight",
            },
        )

    def test_tray_icon_state_and_tooltip_are_derived_from_status_without_semantics(self) -> None:
        cases = [
            ("ready", "ready", "AgentSight: Ready"),
            ("operator_control_paused", "paused", "AgentSight: Paused"),
            ("emergency_stopped", "emergency", "AgentSight: Emergency stop"),
            ("blocked", "blocked", "AgentSight: Blocked"),
            ("discovery_missing", "discovery_missing", "AgentSight: Discovery missing"),
            ("something_new", "unknown", "AgentSight: Unknown"),
        ]
        for tray_status, icon_state, tooltip in cases:
            status = {"tray_status": tray_status}
            self.assertEqual(tray_icon_state_for_status(status), icon_state)
            self.assertEqual(tray_tooltip_for_status(status, language="en"), tooltip)
        self.assertEqual(tray_tooltip_for_status({"tray_status": "ready"}, language="zh"), "AgentSight: 可用")

    def test_tray_icon_as_lettermark_animation_model_is_stateful_and_low_frequency(self) -> None:
        self.assertEqual(TRAY_ICON_LETTERMARK, "AS")
        self.assertEqual(TRAY_ICON_CONSIDERED_SIZES, (16, 20, 24, 32))
        self.assertGreaterEqual(TRAY_ICON_REFRESH_INTERVAL_MS, 300)
        self.assertLessEqual(TRAY_ICON_REFRESH_INTERVAL_MS, 800)

        ready_plan = tray_icon_animation_plan_for_state("ready")
        paused_plan = tray_icon_animation_plan_for_state("paused")
        blocked_plan = tray_icon_animation_plan_for_state("blocked")
        emergency_plan = tray_icon_animation_plan_for_state("emergency")
        missing_plan = tray_icon_animation_plan_for_state("discovery_missing")

        self.assertTrue(ready_plan["animated"])
        self.assertEqual(ready_plan["lettermark"], "AS")
        self.assertEqual(ready_plan["frame_count"], 4)
        self.assertEqual(ready_plan["implementation"], "runtime_win32_gdi_hicon_frames")
        self.assertTrue(paused_plan["animated"])
        self.assertEqual(paused_plan["frame_count"], 2)
        for plan in (blocked_plan, emergency_plan, missing_plan):
            self.assertFalse(plan["animated"])
            self.assertEqual(plan["frame_count"], 1)
            self.assertEqual(plan["animation"], "static")

        self.assertTrue(tray_icon_state_is_animated("ready"))
        self.assertTrue(tray_icon_state_is_animated("paused"))
        self.assertFalse(tray_icon_state_is_animated("blocked"))
        self.assertFalse(tray_icon_state_is_animated("emergency"))

    def test_tray_icon_frame_specs_use_as_text_and_expected_status_colors(self) -> None:
        ready_frames = tray_icon_frame_specs_for_state("ready")
        paused_frames = tray_icon_frame_specs_for_state("paused")
        blocked_frames = tray_icon_frame_specs_for_state("blocked")
        emergency_frames = tray_icon_frame_specs_for_state("emergency")
        unknown_frames = tray_icon_frame_specs_for_state("not_a_state")

        self.assertEqual(len(ready_frames), 4)
        self.assertEqual({frame["lettermark"] for frame in ready_frames}, {"AS"})
        self.assertTrue(all(frame["transparent_background"] for frame in ready_frames))
        self.assertTrue(all(frame["colored_letter_glyphs"] for frame in ready_frames))
        self.assertTrue(all(frame["no_background_shape"] for frame in ready_frames))
        self.assertGreater(len({frame["glyph_rgb"] for frame in ready_frames}), 1)
        self.assertEqual(len(paused_frames), 2)
        self.assertGreater(paused_frames[0]["glyph_rgb"][0], paused_frames[0]["glyph_rgb"][2])
        self.assertEqual(len(blocked_frames), 1)
        self.assertGreater(blocked_frames[0]["glyph_rgb"][0], blocked_frames[0]["glyph_rgb"][1])
        self.assertEqual(len(emergency_frames), 1)
        self.assertGreater(emergency_frames[0]["glyph_rgb"][0], emergency_frames[0]["glyph_rgb"][1])
        self.assertEqual(unknown_frames[0]["state"], "unknown")
        self.assertGreaterEqual(unknown_frames[0]["glyph_rgb"][0], 100)
        self.assertIn("16_20_24_32", unknown_frames[0]["size_policy"])
        self.assertFalse(TRAY_ICON_STATES["emergency"]["animated"])

    def test_dynamic_tray_menu_model_uses_status_control_enablement(self) -> None:
        status = {
            "tray_status": "operator_control_paused",
            "controls": {
                "can_pause_ai_real_control": False,
                "can_allow_ai_real_control": True,
                "can_clear_emergency_stop": False,
                "can_open_recording_settings": True,
                "can_open_timeline": True,
            },
        }
        menu = build_tray_menu_model(status, language="en")
        items = {item["key"]: item for item in menu if item.get("kind") == "item"}
        submenus = {item["key"]: item for item in menu if item.get("kind") == "submenu"}

        self.assertEqual(items["state_label"]["label"], "State: Paused")
        self.assertFalse(items["state_label"]["enabled"])
        self.assertFalse(items["pause_agentsight"]["enabled"])
        self.assertTrue(items["allow_agentsight"]["enabled"])
        self.assertFalse(items["clear_emergency_stop"]["enabled"])
        self.assertTrue(items["open_recording_settings"]["enabled"])
        self.assertNotIn("toggle_idle_capture", items)
        self.assertNotIn("toggle_operation_capture", items)
        self.assertNotIn("toggle_pre_action_frame", items)
        self.assertNotIn("toggle_post_action_frames", items)
        self.assertTrue(items["open_timeline"]["enabled"])
        self.assertNotIn("open_operation_log", items)
        self.assertNotIn("open_evidence_folder", items)
        self.assertNotIn("open_agent_data_folder", items)
        self.assertNotIn("exit_tray_only", items)
        self.assertIn("language", submenus)
        self.assertEqual(submenus["language"]["label"], "Language")
        self.assertEqual(
            {item["key"] for item in submenus["language"]["items"]},
            {"language_follow_system", "language_zh", "language_en"},
        )
        self.assertNotIn("copy_status_summary", items)

    def test_dynamic_tray_menu_model_can_render_chinese_labels(self) -> None:
        menu = build_tray_menu_model({"tray_status": "ready", "controls": {}}, language="zh")
        items = {item["key"]: item for item in menu if item.get("kind") == "item"}
        submenus = {item["key"]: item for item in menu if item.get("kind") == "submenu"}

        self.assertEqual(items["state_label"]["label"], "状态: 可用")
        self.assertEqual(items["pause_agentsight"]["label"], "暂停 AI 控制")
        self.assertEqual(items["allow_agentsight"]["label"], "允许 AI 控制")
        self.assertEqual(items["emergency_stop"]["label"], "紧急停止")
        self.assertEqual(items["open_recording_settings"]["label"], "采集与保留设置")
        self.assertNotIn("toggle_idle_capture", items)
        self.assertNotIn("toggle_operation_capture", items)
        self.assertNotIn("toggle_pre_action_frame", items)
        self.assertNotIn("toggle_post_action_frames", items)
        self.assertEqual(items["open_timeline"]["label"], "打开时间线")
        self.assertNotIn("open_operation_log", items)
        self.assertEqual(items["stop_agentsight"]["label"], "停止 AgentSight")
        self.assertEqual(submenus["language"]["label"], "语言")

    def test_timeline_launcher_prefers_adjacent_packaged_viewer_when_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = Path(temp_dir) / "dist"
            dist.mkdir()
            tray_exe = dist / "AgentSightTray.exe"
            timeline_exe = dist / "AgentSightTimelineViewer.exe"
            tray_exe.write_text("", encoding="utf-8")
            timeline_exe.write_text("", encoding="utf-8")
            process = mock.Mock(pid=4321)

            with mock.patch.object(timeline_viewer_module.sys, "frozen", True, create=True), mock.patch.object(
                timeline_viewer_module.sys, "executable", str(tray_exe)
            ), mock.patch.object(
                timeline_viewer_module, "_stop_existing_timeline_viewers", return_value={"attempted": False}
            ), mock.patch.object(
                timeline_viewer_module, "_write_timeline_launch_report"
            ), mock.patch.object(
                timeline_viewer_module.subprocess, "Popen", return_value=process
            ) as popen:
                report = launch_timeline_viewer_process(mode="timeline")

        self.assertEqual(report["launch_kind"], "packaged_qt_viewer")
        self.assertEqual(report["command"], [str(timeline_exe), "--mode", "timeline"])
        self.assertEqual(report["cwd"], str(dist))
        popen.assert_called_once()
        kwargs = popen.call_args.kwargs
        self.assertNotEqual(kwargs.get("creationflags"), getattr(timeline_viewer_module.subprocess, "CREATE_NO_WINDOW", object()))
        startupinfo = kwargs.get("startupinfo")
        if startupinfo is not None:
            self.assertNotEqual(getattr(startupinfo, "wShowWindow", None), 0)

    def test_recording_settings_dialog_model_follows_language_and_boundaries(self) -> None:
        policy = default_recording_policy()
        model = build_recording_settings_dialog_model(policy, language="zh")
        controls = {item["key"]: item for item in model["controls"] if item.get("control_type")}

        self.assertEqual(model["ui_surface"], "native_windows_dialog")
        self.assertEqual(model["preferred_ui_surface"], "modern_scrollable_tkinter_dialog")
        self.assertTrue(model["scrollable"])
        self.assertTrue(model["style_model"]["grouped_cards"])
        self.assertTrue(model["style_model"]["mousewheel_scroll"])
        self.assertEqual(model["title"], "AgentSight 采集与保留设置")
        self.assertIn("tray-config.jsonc", model["config_file"])
        self.assertEqual(
            set(controls),
            {
                "continuous_recording_enabled",
                "idle_fps",
                "action_capture_enabled",
                "capture_pre_action_frame",
                "capture_post_action_frames",
                "post_action_fps",
                "post_action_duration_seconds",
                "retention_days",
                "max_storage_gb",
                "min_free_disk_gb",
            },
        )
        self.assertEqual(controls["continuous_recording_enabled"]["label"], "平时低频记录")
        self.assertEqual(controls["idle_fps"]["label"], "平时 FPS")
        self.assertEqual(controls["continuous_recording_enabled"]["checked"], False)
        self.assertEqual(controls["idle_fps"]["value"], 0.1)
        self.assertEqual(controls["idle_fps"]["min"], 0.1)
        self.assertEqual(controls["post_action_fps"]["value"], 1)
        self.assertEqual(controls["post_action_duration_seconds"]["control_type"], "float")
        self.assertEqual(controls["post_action_duration_seconds"]["value"], 10)
        self.assertEqual(controls["post_action_duration_seconds"]["unit"], "seconds")
        self.assertEqual(controls["max_storage_gb"]["value"], 5)
        self.assertEqual(controls["max_storage_gb"]["unit"], "GB")
        self.assertEqual(controls["min_free_disk_gb"]["value"], 5)
        self.assertEqual(controls["min_free_disk_gb"]["unit"], "GB")
        self.assertNotIn("max_post_action_frames", controls)
        self.assertFalse(model["host_input_sent"])
        self.assertEqual(model["host_sent_event_count"], 0)
        self.assertFalse(model["boundary"]["clipboard_used"])
        self.assertFalse(model["boundary"]["business_success_judged"])

        en_model = build_recording_settings_dialog_model(policy, language="en")
        en_controls = {item["key"]: item for item in en_model["controls"] if item.get("control_type")}
        self.assertEqual(en_controls["continuous_recording_enabled"]["label"], "Idle low-frequency capture")
        summary = status_summary_text({"recording_policy": policy, "paths": {}}, language="en")
        self.assertIn("Idle low-frequency capture enabled", summary)
        self.assertNotIn("Continuous recording enabled", summary)
        zh_summary = status_summary_text({"recording_policy": policy, "paths": {}}, language="zh")
        self.assertIn("平时低频记录已启用", zh_summary)
        self.assertNotIn("连续记录已启用", zh_summary)

    def test_legacy_post_action_duration_ms_round_trips_as_float_seconds_without_precision_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            config_path = local / "AgentSight" / "tray-config.jsonc"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                write_default_tray_config_if_missing()
                payload = read_jsonc_file(config_path)
                payload["recording"]["action_capture"]["post_action_duration_ms"] = 1500
                payload["max_storage_mb"] = 1536
                payload["min_free_disk_mb"] = 768
                config_path.write_text(json.dumps(payload), encoding="utf-8")

                model = build_recording_settings_dialog_model(read_jsonc_file(config_path), language="en")
                controls = {item["key"]: item for item in model["controls"] if item.get("control_type")}
                apply_recording_policy_settings(
                    {
                        "post_action_duration_seconds": controls["post_action_duration_seconds"]["value"],
                        "max_storage_gb": controls["max_storage_gb"]["value"],
                        "min_free_disk_gb": controls["min_free_disk_gb"]["value"],
                    }
                )
                updated = read_jsonc_file(config_path)

        self.assertEqual(controls["post_action_duration_seconds"]["control_type"], "float")
        self.assertEqual(controls["post_action_duration_seconds"]["value"], 1.5)
        self.assertEqual(updated["recording"]["action_capture"]["post_action_duration_ms"], 1500)
        self.assertEqual(controls["max_storage_gb"]["value"], 1.5)
        self.assertEqual(updated["max_storage_mb"], 1536)
        self.assertEqual(controls["min_free_disk_gb"]["value"], 0.75)
        self.assertEqual(updated["min_free_disk_mb"], 768)

    def test_tray_recording_config_defaults_are_written_as_user_visible_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                report = write_default_tray_config_if_missing()
                config_path = default_tray_config_file()
                status = load_tray_status()

        self.assertTrue(report["created_now"])
        self.assertEqual(Path(report["config_file"]).name, "tray-config.jsonc")
        self.assertTrue(config_path.name.endswith("tray-config.jsonc"))
        self.assertEqual(status["paths"]["tray_config_file"], str(config_path))
        policy = status["recording_policy"]
        self.assertEqual(policy["schema"], "agentsight_tray_config_v1")
        self.assertFalse(policy["continuous_recording_enabled"])
        self.assertFalse(policy["recording"]["idle_capture"]["enabled"])
        self.assertEqual(policy["recording"]["idle_capture"]["fps"], 0.1)
        self.assertNotIn("interval_ms", policy["recording"]["idle_capture"])
        self.assertTrue(policy["recording"]["action_capture"]["capture_pre_action_frame"])
        self.assertTrue(policy["recording"]["action_capture"]["capture_post_action_frames"])
        self.assertEqual(policy["recording"]["action_capture"]["post_action_fps"], 1)
        self.assertEqual(policy["recording"]["action_capture"]["post_action_duration_ms"], 10000)
        self.assertNotIn("max_post_action_frames", policy["recording"]["action_capture"])
        self.assertEqual(policy["retention_days"], 30)
        self.assertEqual(policy["max_storage_mb"], 5120)
        self.assertEqual(policy["min_free_disk_mb"], 5120)
        self.assertNotIn("daily_segment_boundary_local_time", policy)
        self.assertNotIn("segment", policy["recording"])
        self.assertNotIn("operation_capture_enabled", policy)
        self.assertNotIn("recording_started_by_tray", policy)
        self.assertNotIn("prune_unreferenced_segments", policy)
        self.assertNotIn("pinned_evidence_never_pruned", policy)
        self.assertNotIn("recording_video_encoding_enabled", policy)
        self.assertNotIn("post_observe_defaults", policy["recording"])
        self.assertNotIn("timeline", policy)
        self.assertNotIn("operation_log", policy)
        self.assertNotIn("retention", policy)
        self.assertNotIn("boundary", policy)

    def test_recording_settings_update_user_config_without_host_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                write_default_tray_config_if_missing()
                report = apply_recording_policy_settings(
                    {
                        "continuous_recording_enabled": True,
                        "idle_fps": 0.5,
                        "action_capture_enabled": False,
                        "capture_pre_action_frame": False,
                        "capture_post_action_frames": True,
                        "post_action_fps": 12,
                        "post_action_duration_seconds": 5,
                        "retention_days": 14,
                        "max_storage_gb": 2,
                        "min_free_disk_gb": 1.5,
                        "daily_segment_boundary_local_time": "03:30",
                        "segment_bucket_granularity": "hourly",
                        "segment_image_encoding": "png",
                        "segment_image_quality": 80,
                        "segment_image_lossless": True,
                    }
                )
                policy = read_jsonc_file(default_tray_config_file())
                status = load_tray_status()
                menu = build_tray_menu_model(status, language="en")

        items = {item["key"]: item for item in menu if item.get("kind") == "item"}
        self.assertTrue(report["updated"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertTrue(policy["continuous_recording_enabled"])
        self.assertTrue(policy["recording"]["idle_capture"]["enabled"])
        self.assertEqual(policy["recording"]["idle_capture"]["fps"], 0.5)
        self.assertNotIn("interval_ms", policy["recording"]["idle_capture"])
        self.assertFalse(policy["recording"]["action_capture"]["enabled"])
        self.assertFalse(policy["recording"]["action_capture"]["capture_pre_action_frame"])
        self.assertEqual(policy["recording"]["action_capture"]["post_action_fps"], 12)
        self.assertEqual(policy["recording"]["action_capture"]["post_action_duration_ms"], 5000)
        self.assertNotIn("max_post_action_frames", policy["recording"]["action_capture"])
        self.assertNotIn("segment", policy["recording"])
        self.assertEqual(policy["retention_days"], 14)
        self.assertEqual(policy["max_storage_mb"], 2048)
        self.assertEqual(policy["min_free_disk_mb"], 1536)
        self.assertNotIn("daily_segment_boundary_local_time", policy)
        self.assertNotIn("operation_capture_enabled", policy)
        self.assertNotIn("prune_unreferenced_segments", policy)
        self.assertNotIn("pinned_evidence_never_pruned", policy)
        self.assertNotIn("recording_video_encoding_enabled", policy)
        self.assertNotIn("toggle_idle_capture", items)

    def test_legacy_operation_capture_enabled_no_longer_changes_action_capture_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            config_path = local / "AgentSight" / "tray-config.jsonc"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                write_default_tray_config_if_missing()
                apply_recording_policy_settings({"operation_capture_enabled": False})
                unknown = set_recording_policy_flag("operation_capture_enabled", False)
                policy = read_jsonc_file(config_path)

        self.assertTrue(policy["recording"]["action_capture"]["enabled"])
        self.assertFalse(unknown["updated"])
        self.assertEqual(unknown["reason"], "unknown_recording_policy_flag")
        self.assertNotIn("operation_capture_enabled", json.dumps(policy, sort_keys=True))

    def test_timeline_ignores_legacy_png_media_paths_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                media_dir = local / "AgentSight" / "runs_host_agent" / "session-test" / "media"
                media_dir.mkdir(parents=True)
                from PIL import Image

                target = media_dir / "frame-with-log.png"
                Image.new("RGB", (8, 8), (20, 120, 220)).save(target)
                append_operation_log(
                    {
                        "route": "/look",
                        "op": "look",
                        "request_id": "look-1",
                        "status": "ok",
                        "media_paths": [str(target)],
                        "host_input_sent": False,
                        "host_sent_event_count": 0,
                    }
                )
                model = build_timeline_model()

        self.assertEqual(model["frame_count"], 0)
        self.assertEqual(model["operation_log_count"], 1)
        self.assertEqual(model["operation_log_attachments"], [])
        self.assertEqual(model["frames"], [])

    def test_storage_quota_prunes_old_mkv_and_matching_operation_logs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                from agentsight.segments import MkvSegmentWriter

                agent_dir = local / "AgentSight"
                segment_path = agent_dir / "runs_host_agent" / "segments" / "agentsight-20260621-00.mkv"
                writer = MkvSegmentWriter(segment_path, segment_id="agentsight-20260621-00", width=8, height=8)
                writer.add_frame(
                    bytes([20, 20, 20, 255]) * 64,
                    captured_at_ms=1781971200000,
                    timestamp_iso="2026-06-21T00:00:00+00:00",
                    source="idle",
                    event_id="quota-old",
                    cursor_mode="none",
                    capture_content_degenerate=False,
                    screen_region={"x": 0, "y": 0, "width": 8, "height": 8},
                    coordinate_system="virtual_screen_pixels",
                )
                writer.close()
                protected_evidence = agent_dir / "runs_host_agent" / "evidence" / "keep.txt"
                protected_evidence.parent.mkdir(parents=True, exist_ok=True)
                protected_evidence.write_text("must not be quota-pruned by this isolated test", encoding="utf-8")
                log_path = agent_dir / "operation-log.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(
                    json.dumps({"timestamp_ms": 1781971200000, "entry": {"route": "/look"}}, ensure_ascii=False) + "\n"
                    + json.dumps({"timestamp_ms": 1782057600000, "entry": {"route": "/screen"}}, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                report = apply_storage_quota(root=agent_dir, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999})
                segment_exists_after = segment_path.exists()
                index_exists_after = segment_path.with_suffix(".frames.jsonl").exists()
                manifest_exists_after = segment_path.with_suffix(".manifest.json").exists()
                protected_evidence_exists_after = protected_evidence.exists()
                remaining = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

        self.assertTrue(report["deleted_count"] >= 1, report)
        self.assertFalse(segment_exists_after)
        self.assertFalse(index_exists_after)
        self.assertFalse(manifest_exists_after)
        self.assertTrue(protected_evidence_exists_after)
        self.assertTrue(report["operation_log_prune"]["pruned"])
        self.assertNotIn("/look", remaining)
        self.assertIn("/screen", remaining)
        self.assertFalse(report["boundary"]["clipboard_used"])

    def test_operation_log_preserves_blocked_failures_and_redacts_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True, exist_ok=True)
                (agent_dir / "tray-settings.json").write_text(
                    json.dumps({"schema": "agentsight_tray_settings_v1", "language": "en"}),
                    encoding="utf-8",
                )
                append_operation_log(
                    public_operation_log_entry(
                        route="/do",
                        request={
                            "op": "do",
                            "id": "blocked-1",
                            "token": "request-secret",
                            "nested": {"authorization": "Bearer request-secret"},
                        },
                        response={
                            "ok": False,
                            "status": "blocked",
                            "code": "OPERATOR_PAUSED",
                            "token": "response-secret",
                            "host_input_sent": False,
                            "host_sent_event_count": 0,
                            "control_blockers": ["OPERATOR_PAUSED"],
                        },
                        http_status=503,
                        caller_hint="test-caller",
                    )
                )
                logs = read_operation_log()

        self.assertEqual(len(logs), 1)
        entry = logs[0]["entry"]
        self.assertEqual(entry["route"], "/do")
        self.assertEqual(entry["http_status"], 503)
        self.assertEqual(entry["status"], "blocked")
        self.assertEqual(entry["code"], "OPERATOR_PAUSED")
        self.assertEqual(entry["control_blockers"], ["OPERATOR_PAUSED"])
        self.assertFalse(logs[0]["host_input_sent"])
        self.assertEqual(logs[0]["host_sent_event_count"], 0)
        self.assertEqual(entry["request_json"]["token"], "<redacted>")
        self.assertEqual(entry["request_json"]["nested"]["authorization"], "<redacted>")
        self.assertEqual(entry["response_json"]["token"], "<redacted>")
        self.assertNotIn("request-secret", json.dumps(logs, ensure_ascii=False))
        self.assertNotIn("response-secret", json.dumps(logs, ensure_ascii=False))
        self.assertFalse(logs[0]["tool_asserts_business_success"])
        self.assertFalse(logs[0]["tool_asserts_causality"])

    def test_operation_log_derives_do_input_event_count_from_public_do_result(self) -> None:
        entry = public_operation_log_entry(
            route="/do",
            request={"op": "do", "id": "act-1"},
            response={
                "object_type": "DoResult",
                "schema": "agentsight_do_v1",
                "ok": True,
                "status": "done",
                "input": {"sent": True, "host_event_count": 4, "step_count": 3},
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        self.assertTrue(entry["host_input_sent"])
        self.assertEqual(entry["host_sent_event_count"], 4)

    def test_operation_log_prefers_do_input_event_count_over_legacy_top_level_zero(self) -> None:
        entry = public_operation_log_entry(
            route="/do",
            request={"op": "do", "id": "act-conflict"},
            response={
                "object_type": "DoResult",
                "schema": "agentsight_do_v1",
                "ok": True,
                "status": "done",
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "input": {"sent": True, "host_event_count": 5, "step_count": 2},
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        self.assertTrue(entry["host_input_sent"])
        self.assertEqual(entry["host_sent_event_count"], 5)

    def test_operation_log_extracts_segment_frame_refs_from_public_response(self) -> None:
        entry = public_operation_log_entry(
            route="/do",
            request={"op": "do", "id": "act-segment"},
            response={
                "object_type": "DoResult",
                "schema": "agentsight_do_v1",
                "ok": True,
                "status": "done",
                "input": {"sent": True, "host_event_count": 3, "step_count": 1},
                "post_observe": {
                    "sampled_frames": [
                        {
                            "frame_index": 0,
                            "observation_ref": "obs-1",
                            "segment_frame": {
                                "status": "recorded",
                                "segment_id": "seg-1",
                                "frame_id": "f000001",
                                "frame_kind": "pframe_delta",
                                "source": "post_do",
                                "restore_ref": {"segment_path": "C:/tmp/segment-seg-1", "frame_id": "f000001"},
                            },
                        }
                    ]
                },
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        self.assertEqual(entry["frame_refs"]["post_action"][0]["segment_frame_id"], "f000001")
        self.assertEqual(entry["frame_refs"]["post_action"][0]["segment_source"], "post_do")
        self.assertEqual(entry["frame_refs"]["post_action"][0]["restore_ref"]["frame_id"], "f000001")
        self.assertEqual(entry["segment_frame_refs"][0]["segment_id"], "seg-1")
        self.assertFalse(entry["tool_asserts_business_success"])

    def test_operation_log_extracts_segment_frame_refs_from_public_clip_response(self) -> None:
        entry = public_operation_log_entry(
            route="/look",
            request={"op": "look", "id": "look-clip", "q": "clip"},
            response={
                "object_type": "LookResult",
                "ok": True,
                "status": "ok",
                "clip": {
                    "frames": [
                        {
                            "segment_id": "seg-clip",
                            "segment_frame_id": "clip-f1",
                            "frame_kind": "keyframe",
                            "restore_ref": {"segment_path": "C:/tmp/clip.agseg", "frame_id": "clip-f1"},
                        },
                        {
                            "segment_id": "seg-clip",
                            "segment_frame_id": "clip-f2",
                            "frame_kind": "pframe_delta",
                            "restore_ref": {"segment_path": "C:/tmp/clip.agseg", "frame_id": "clip-f2"},
                        },
                    ]
                },
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
                "tool_asserts_causality": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        self.assertEqual(
            [ref["segment_frame_id"] for ref in entry["frame_refs"]["looked_frames"]],
            ["clip-f1", "clip-f2"],
        )
        self.assertEqual(entry["segment_frame_refs"][0]["relation"], "looked_frame")
        self.assertFalse(entry["tool_asserts_business_success"])
        self.assertFalse(entry["tool_asserts_target_hit"])
        self.assertFalse(entry["tool_asserts_causality"])

    def test_operation_log_omits_transient_mcp_image_content(self) -> None:
        entry = public_operation_log_entry(
            route="/look",
            request={"op": "look", "id": "look-image-content", "q": "frame"},
            response={
                "object_type": "LookResult",
                "ok": True,
                "status": "ok",
                "image_content_returned": True,
                "content": [
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": "base64-image-payload",
                    }
                ],
                "view": {"id": "v1", "w": 10, "h": 10, "scale_down": 1},
                "view_record": {
                    "view_id": "v1",
                    "source_frame_id": "f000001",
                    "segment_id": "seg-view",
                    "segment_restore_ref": {
                        "storage_format": "binary_agseg",
                        "segment_path": "C:/tmp/visual-default/segments/agentsight-20260621.agseg",
                        "frame_id": "f000001",
                    },
                    "requested_screen_region": {"x": 10, "y": 20, "w": 100, "h": 80},
                    "actual_decoded_region": {"x": 10, "y": 20, "w": 100, "h": 80},
                    "output_image_size": {"w": 10, "h": 8},
                    "scale_down": 10,
                    "blur_radius": 2,
                    "cursor_mode": "none",
                    "raw_or_derived": "derived_review_only",
                    "transform": {
                        "schema": "agentsight_view_transform_v1",
                        "view_pixels_to_virtual_screen_pixels": {"origin_x": 10, "origin_y": 20, "scale_x": 10, "scale_y": 10},
                    },
                },
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
                "tool_asserts_causality": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        serialized = json.dumps(entry["response_json"], ensure_ascii=False, sort_keys=True)
        self.assertNotIn("base64-image-payload", serialized)
        self.assertNotIn('"content"', serialized)
        self.assertTrue(entry["transient_image_content_omitted"])
        self.assertTrue(entry["response_json"]["image_content_returned"])
        self.assertEqual(entry["look_preview_refs"][0]["schema"], "agentsight_look_preview_descriptor_v1")
        self.assertEqual(entry["look_preview_refs"][0]["view_id"], "v1")
        self.assertEqual(entry["look_preview_refs"][0]["segment_restore_ref"]["frame_id"], "f000001")
        self.assertEqual(entry["look_preview_refs"][0]["region"], {"x": 10, "y": 20, "w": 100, "h": 80})
        self.assertFalse(entry["look_preview_refs"][0]["default_loaded"])
        self.assertTrue(entry["look_preview_refs"][0]["requires_user_action"])
        self.assertFalse(entry["look_preview_refs"][0]["cache_file_written"])
        self.assertFalse(entry["tool_asserts_business_success"])

    def test_operation_log_extracts_time_near_historical_view_record_preview_ref(self) -> None:
        entry = public_operation_log_entry(
            route="/look",
            request={"op": "look", "id": "look-time-near", "q": "frame", "time": {"near": "10:00:00"}},
            response={
                "object_type": "LookResult",
                "ok": True,
                "type": "time_near_frames",
                "image_content_returned": True,
                "content": [{"type": "image", "mimeType": "image/png", "data": "historical-base64"}],
                "historical_view": {
                    "id": "sv_123",
                    "view_is_current_action_basis": False,
                    "view_role": "historical_segment_review",
                },
                "view_record": {
                    "view_id": "sv_123",
                    "view_role": "historical_segment_review",
                    "view_is_current_action_basis": False,
                    "source_frame_id": "f000000",
                    "segment_restore_ref": {
                        "storage_format": "binary_agseg",
                        "segment_path": "C:/tmp/visual-default/segments/agentsight-20260621.agseg",
                        "frame_id": "f000000",
                    },
                    "requested_screen_region": {"x": 110, "y": 55, "w": 20, "h": 10},
                    "actual_decoded_region": {"x": 10, "y": 5, "w": 20, "h": 10},
                    "output_image_size": {"w": 10, "h": 5},
                    "scale_down": 2,
                    "blur_radius": 0,
                    "cursor_mode": "none",
                    "raw_or_derived": "derived_review_only",
                    "derived_review_file_written": False,
                },
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
                "tool_asserts_causality": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        serialized = json.dumps(entry["response_json"], ensure_ascii=False, sort_keys=True)
        self.assertNotIn("historical-base64", serialized)
        self.assertEqual(entry["look_preview_refs"][0]["view_id"], "sv_123")
        self.assertEqual(entry["look_preview_refs"][0]["source"], "view_record")
        self.assertEqual(entry["look_preview_refs"][0]["segment_restore_ref"]["frame_id"], "f000000")
        self.assertEqual(entry["look_preview_refs"][0]["region"], {"x": 10, "y": 5, "w": 20, "h": 10})
        self.assertEqual(entry["look_preview_refs"][0]["raw_or_derived"], "derived_review_only")
        self.assertFalse(entry["look_preview_refs"][0]["default_loaded"])
        self.assertTrue(entry["look_preview_refs"][0]["requires_user_action"])
        self.assertFalse(entry["tool_asserts_business_success"])

    def test_tray_cli_materializes_look_preview_cache_by_log_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                from PIL import Image
                from agentsight.segments import BinarySegmentWriter

                segment_path = local / "AgentSight" / "runs_host_agent" / "segments" / "look-preview-cli.agseg"
                writer = BinarySegmentWriter.create(segment_path, segment_id="look-preview-cli")
                record = writer.add_frame(
                    Image.new("RGBA", (20, 16), (30, 100, 210, 255)),
                    timestamp_iso="2026-06-21T00:00:01Z",
                    timestamp_monotonic_ms=1000,
                    source="look",
                    event_id="look-preview-cli",
                )
                writer.close()
                append_operation_log(
                    public_operation_log_entry(
                        route="/look",
                        request={"op": "look", "id": "look-preview-cli", "q": "frame"},
                        response={
                            "object_type": "LookResult",
                            "ok": True,
                            "status": "ok",
                            "view_record": {
                                "view_id": "v_cli",
                                "source_frame_id": record["frame_id"],
                                "segment_restore_ref": {
                                    "storage_format": "binary_agseg",
                                    "segment_path": str(segment_path),
                                    "frame_id": record["frame_id"],
                                },
                                "actual_decoded_region": {"x": 1, "y": 2, "w": 12, "h": 8},
                                "scale_down": 2,
                                "blur_radius": 0,
                                "raw_or_derived": "derived_review_only",
                            },
                            "tool_asserts_business_success": False,
                            "tool_asserts_target_hit": False,
                            "tool_asserts_causality": False,
                        },
                        http_status=200,
                    )
                )
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = tray_cli_main(["look-preview", "materialize", "--log-index", "0", "--preview-index", "0"])
                report = json.loads(stdout.getvalue())
                self.assertTrue(Path(report["preview_cache_path"]).exists())

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["status"], "written")
        self.assertEqual(report["view_id"], "v_cli")
        self.assertFalse(report["canonical"])
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertFalse(report["tool_asserts_business_success"])

    def test_tray_cli_materialize_look_preview_returns_nonzero_exit_code_when_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                stdout = io.StringIO()
                with contextlib.redirect_stdout(stdout):
                    exit_code = tray_cli_main(["look-preview", "materialize", "--log-index", "0", "--preview-index", "0"])
                report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "operation_log_index_not_found")
        self.assertEqual(report["exit_code"], 2)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])

    def test_tray_cli_look_preview_without_subcommand_returns_json_blocked(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = tray_cli_main(["look-preview"])
        report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "look_preview_subcommand_required")
        self.assertEqual(report["exit_code"], 2)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])

    def test_tray_cli_look_preview_unknown_subcommand_returns_json_blocked(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = tray_cli_main(["look-preview", "bogus"])
        report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "unsupported_look_preview_command")
        self.assertEqual(report["requested_command"], "bogus")
        self.assertEqual(report["exit_code"], 2)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])

    def test_tray_cli_look_preview_unknown_subcommand_uses_json_when_argv_is_none(self) -> None:
        stdout = io.StringIO()
        with mock.patch("sys.argv", ["agentsight-tray", "look-preview", "bogus"]):
            with contextlib.redirect_stdout(stdout):
                exit_code = tray_cli_main()
        report = json.loads(stdout.getvalue())

        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["reason"], "unsupported_look_preview_command")
        self.assertEqual(report["requested_command"], "bogus")
        self.assertEqual(report["exit_code"], 2)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])

    def test_tray_ai_help_lists_look_preview_as_human_review_only(self) -> None:
        report = ai_help_report()
        flow = "\n".join(report["recommended_flow"])

        self.assertIn("look-preview materialize", report["commands"])
        self.assertIn("derived review cache", report["commands"]["look-preview materialize"])
        self.assertIn("not an ordinary AI public look/do step", report["commands"]["look-preview materialize"])
        self.assertIn("look-preview materialize", flow)
        self.assertIn("human review", flow)
        self.assertNotIn("visual-memory query entrypoints", json.dumps(report, sort_keys=True))
        self.assertNotIn("ordinary AI observe", json.dumps(report, sort_keys=True))
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_operation_log_extracts_segment_frame_refs_from_public_diff_response(self) -> None:
        entry = public_operation_log_entry(
            route="/look",
            request={"op": "look", "id": "look-diff", "q": "diff"},
            response={
                "object_type": "LookResult",
                "ok": True,
                "status": "ok",
                "diffs": {
                    "changes": [
                        {
                            "before_frame": {
                                "segment_id": "seg-diff",
                                "segment_frame_id": "diff-before",
                                "restore_ref": {"segment_path": "C:/tmp/diff.agseg", "frame_id": "diff-before"},
                            },
                            "after_frame": {
                                "segment_id": "seg-diff",
                                "segment_frame_id": "diff-after",
                                "restore_ref": {"segment_path": "C:/tmp/diff.agseg", "frame_id": "diff-after"},
                            },
                            "changed_pixel_ratio": 0.25,
                        }
                    ]
                },
                "tool_asserts_business_success": False,
                "tool_asserts_target_hit": False,
                "tool_asserts_causality": False,
            },
            http_status=200,
            caller_hint="test-caller",
        )

        self.assertEqual(
            [ref["segment_frame_id"] for ref in entry["frame_refs"]["looked_frames"]],
            ["diff-before", "diff-after"],
        )
        self.assertEqual(entry["segment_frame_refs"][0]["relation"], "looked_frame")
        self.assertFalse(entry["tool_asserts_business_success"])
        self.assertFalse(entry["tool_asserts_target_hit"])
        self.assertFalse(entry["tool_asserts_causality"])

    def test_timeline_attaches_operation_log_to_segment_frame_id_when_available(self) -> None:
        frames = [
            {
                "index": 0,
                "path": "C:/tmp/frame-1.png",
                "timestamp_ms": 1000,
                "raw_or_derived": "raw_existing_media",
                "operation_log_indexes": [],
                "segment_frame_id": "f000001",
            }
        ]
        logs = [
            {
                "timestamp_ms": 2000,
                "entry": {
                    "route": "/do",
                    "op": "do",
                    "request_id": "act-segment",
                    "segment_frame_refs": [{"segment_frame_id": "f000001"}],
                },
            }
        ]

        from agentsight.tray.viewers import _attach_operation_logs_to_frames

        attachments = _attach_operation_logs_to_frames(frames, logs)

        self.assertEqual(attachments[0]["attachment_basis"], "segment_frame_id")
        self.assertEqual(attachments[0]["frame_index"], 0)
        self.assertEqual(frames[0]["operation_log_indexes"], [0])

    def test_timeline_model_ignores_legacy_directory_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                from PIL import Image
                from agentsight.segments import SegmentWriter

                segment_dir = local / "AgentSight" / "runs_host_agent" / "session-test" / "segments" / "segment-seg-ui"
                writer = SegmentWriter.create(segment_dir, segment_id="seg-ui")
                record = writer.add_frame(
                    Image.new("RGBA", (12, 8), (40, 120, 220, 255)),
                    timestamp_iso="2026-06-21T00:00:00Z",
                    timestamp_monotonic_ms=1000,
                    source="post_do",
                    event_id="do-seg-ui",
                )
                writer.close()
                append_operation_log(
                    {
                        "route": "/do",
                        "op": "do",
                        "request_id": "do-seg-ui",
                        "status": "done",
                        "segment_frame_refs": [
                            {
                                "segment_id": "seg-ui",
                                "segment_frame_id": record["frame_id"],
                                "restore_ref": {"segment_path": str(segment_dir), "frame_id": record["frame_id"]},
                            }
                        ],
                        "host_input_sent": True,
                        "host_sent_event_count": 3,
                    }
                )
                model = build_timeline_model()

                self.assertEqual(model["frames"], [])
                self.assertEqual(model["operation_log_attachments"], [])

    def test_timeline_model_reads_binary_agseg_frames_as_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                from PIL import Image
                from agentsight.segments import BinarySegmentWriter

                segment_path = local / "AgentSight" / "runs_host_agent" / "session-test" / "segments" / "seg-ui-bin.agseg"
                writer = BinarySegmentWriter.create(segment_path, segment_id="seg-ui-bin")
                record = writer.add_frame(
                    Image.new("RGBA", (13, 9), (30, 160, 120, 255)),
                    timestamp_iso="2026-06-21T00:00:01Z",
                    timestamp_monotonic_ms=2000,
                    source="post_do",
                    event_id="do-seg-ui-bin",
                )
                writer.close()
                append_operation_log(
                    {
                        "route": "/do",
                        "op": "do",
                        "request_id": "do-seg-ui-bin",
                        "status": "done",
                        "segment_frame_refs": [
                            {
                                "segment_id": "seg-ui-bin",
                                "segment_frame_id": record["frame_id"],
                                "restore_ref": {
                                    "storage_format": "binary_agseg",
                                    "segment_path": str(segment_path),
                                    "frame_id": record["frame_id"],
                                },
                            }
                        ],
                        "host_input_sent": True,
                        "host_sent_event_count": 3,
                    }
                )
                model = build_timeline_model(duration_limit_ms=None)

                segment_frames = [frame for frame in model["frames"] if frame.get("segment_id") == "seg-ui-bin"]
                self.assertEqual(len(segment_frames), 1)
                self.assertEqual(segment_frames[0]["segment_frame_id"], record["frame_id"])
                self.assertEqual(segment_frames[0]["storage_format"], "binary_agseg")
                self.assertEqual(segment_frames[0]["canonical_source"], "single_file_agseg_v1")
                self.assertEqual(segment_frames[0]["raw_or_derived"], "raw_segment_index_metadata")
                self.assertEqual(segment_frames[0]["preview_policy"], "on_demand_only")
                self.assertIsNone(segment_frames[0]["path"])
                self.assertIsNone(segment_frames[0]["uri"])
                self.assertEqual(model["operation_log_attachments"][0]["attachment_basis"], "segment_frame_id")
                self.assertEqual(segment_frames[0]["operation_log_indexes"], [0])

    def test_timeline_initial_window_sparse_hour_does_not_backfill_to_500(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            right_wall_ms = 10_000_000
            old_timestamps = [right_wall_ms - 7_200_000 + index * 1_000 for index in range(300)]
            recent_timestamps = [right_wall_ms - 199_000 + index * 1_000 for index in range(200)]
            with mock.patch.dict("os.environ", env, clear=False):
                self._write_timeline_mkv_index(local, "agentsight-old", old_timestamps)
                self._write_timeline_mkv_index(local, "agentsight-recent", recent_timestamps)
                model = build_timeline_model(max_frames=500, max_logs=10, right_wall_ms=right_wall_ms)

        self.assertEqual(model["frame_count"], 200)
        self.assertEqual([frame["timestamp_ms"] for frame in model["frames"]], recent_timestamps)
        config = model["timeline_config"]
        self.assertEqual(config["loaded_frame_count"], 200)
        self.assertEqual(config["duration_limit_ms"], 3_600_000)
        self.assertEqual(config["frame_limit"], 500)

    def test_timeline_initial_window_high_frequency_caps_at_500(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            right_wall_ms = 10_000_000
            timestamps = [right_wall_ms - (699 - index) * 3_600 for index in range(700)]
            with mock.patch.dict("os.environ", env, clear=False):
                self._write_timeline_mkv_index(local, "agentsight-high-frequency", timestamps)
                model = build_timeline_model(max_frames=500, max_logs=10, right_wall_ms=right_wall_ms)

        selected_timestamps = [frame["timestamp_ms"] for frame in model["frames"]]
        self.assertEqual(model["frame_count"], 500)
        self.assertEqual(selected_timestamps, timestamps[-500:])
        self.assertGreater(selected_timestamps[0], right_wall_ms - 3_600_000)

    def test_timeline_initial_window_includes_boundaries_and_excludes_future(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            right_wall_ms = 10_000_000
            timestamps = [right_wall_ms - 3_600_001, right_wall_ms - 3_600_000, right_wall_ms, right_wall_ms + 1]
            with mock.patch.dict("os.environ", env, clear=False):
                self._write_timeline_mkv_index(local, "agentsight-boundaries", timestamps)
                model = build_timeline_model(max_frames=500, max_logs=10, right_wall_ms=right_wall_ms)

        self.assertEqual([frame["timestamp_ms"] for frame in model["frames"]], [right_wall_ms - 3_600_000, right_wall_ms])
        self.assertEqual(model["timeline_config"]["future_frame_count_excluded"], 1)

    def test_timeline_window_sorts_across_segments_by_timestamp_not_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            right_wall_ms = 10_000_000
            with mock.patch.dict("os.environ", env, clear=False):
                self._write_timeline_mkv_index(local, "agentsight-newer-mtime", [right_wall_ms - 20_000, right_wall_ms], mtime=300)
                self._write_timeline_mkv_index(local, "agentsight-older-mtime", [right_wall_ms - 10_000], mtime=100)
                model = build_timeline_model(max_frames=2, max_logs=10, right_wall_ms=right_wall_ms)

        self.assertEqual([frame["timestamp_ms"] for frame in model["frames"]], [right_wall_ms - 10_000, right_wall_ms])

    def test_timeline_manual_history_count_only_can_load_older_than_one_hour(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            right_wall_ms = 10_000_000
            timestamps = [right_wall_ms - 7_200_000 + index * 10_000 for index in range(700)]
            with mock.patch.dict("os.environ", env, clear=False):
                self._write_timeline_mkv_index(local, "agentsight-manual-history", timestamps)
                model = build_timeline_model(max_frames=600, max_logs=10, right_wall_ms=right_wall_ms, duration_limit_ms=None)

        selected_timestamps = [frame["timestamp_ms"] for frame in model["frames"]]
        self.assertEqual(model["frame_count"], 600)
        self.assertEqual(selected_timestamps, timestamps[-600:])
        self.assertLess(selected_timestamps[0], right_wall_ms - 3_600_000)
        self.assertIsNone(model["timeline_config"]["duration_limit_ms"])

    def test_timeline_open_does_not_generate_previews_from_derived_png_media(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                media_dir = local / "AgentSight" / "runs_host_agent" / "session-test" / "media"
                media_dir.mkdir(parents=True)
                from PIL import Image

                frame1 = media_dir / "frame-1.png"
                frame2 = media_dir / "frame-2.png"
                derived = media_dir / "frame-2-diff-heatmap.png"
                Image.new("RGBA", (8, 8), (10, 10, 10, 255)).save(frame1)
                second = Image.new("RGBA", (8, 8), (10, 10, 10, 255))
                second.putpixel((3, 4), (200, 20, 20, 255))
                second.save(frame2)
                Image.new("RGBA", (8, 8), (255, 0, 0, 120)).save(derived)
                model = build_timeline_model()

        self.assertEqual(model["frame_count"], 0)
        self.assertEqual(model["frames"], [])

    def test_operator_control_commands_refresh_icon_without_message_boxes(self) -> None:
        app = object.__new__(Win32TrayApp)
        app._refresh_icon = mock.Mock()
        app._message_box = mock.Mock()

        with mock.patch("agentsight.tray.gui.pause_agentsight") as pause:
            with mock.patch("agentsight.tray.gui.allow_agentsight") as allow:
                with mock.patch("agentsight.tray.gui.emergency_stop") as emergency:
                    with mock.patch("agentsight.tray.gui.clear_emergency") as clear:
                        app._handle_command(IDM_PAUSE_AGENTSIGHT)
                        app._handle_command(IDM_ALLOW_AGENTSIGHT)
                        app._handle_command(IDM_EMERGENCY_STOP)
                        app._handle_command(IDM_CLEAR_EMERGENCY)

        pause.assert_called_once_with("operator_requested_from_tray_gui")
        allow.assert_called_once_with("operator_requested_from_tray_gui")
        emergency.assert_called_once_with("operator_requested_from_tray_gui")
        clear.assert_called_once_with()
        self.assertEqual(app._refresh_icon.call_count, 4)
        app._message_box.assert_not_called()

    def test_open_recording_settings_uses_native_dialog_not_html_viewer(self) -> None:
        app = object.__new__(Win32TrayApp)
        app._show_recording_settings_dialog = mock.Mock()

        with mock.patch("agentsight.tray.gui.write_default_tray_config_if_missing", return_value={"config_file": "tray-config.jsonc"}) as write_config:
            with mock.patch("agentsight.tray.gui.os.startfile", create=True) as startfile:
                app._handle_command(IDM_OPEN_RECORDING_SETTINGS)

        write_config.assert_called_once()
        app._show_recording_settings_dialog.assert_called_once_with({"config_file": "tray-config.jsonc"})
        startfile.assert_not_called()

    def test_open_timeline_and_log_launch_native_qt_viewer_process(self) -> None:
        app = object.__new__(Win32TrayApp)
        with mock.patch("agentsight.tray.gui.launch_timeline_viewer_process") as launch:
            app._handle_command(IDM_OPEN_TIMELINE)

        self.assertEqual(launch.call_args_list[0].kwargs["mode"], "timeline")
        self.assertEqual(launch.call_count, 1)

    def test_native_timeline_viewer_launches_without_html_generation(self) -> None:
        with mock.patch(
            "agentsight.tray.timeline_viewer._stop_existing_timeline_viewers",
            return_value={"attempted": False, "reason": "test_mock"},
        ) as stop_existing:
            with mock.patch("agentsight.tray.timeline_viewer.subprocess.Popen") as popen:
                popen.return_value.pid = 4242
                report = launch_timeline_viewer_process(mode="timeline")

        stop_existing.assert_called_once()
        self.assertEqual(report["viewer"], "pyside6_qt_native_window")
        self.assertEqual(report["mode"], "timeline")
        self.assertEqual(report["stopped_existing_viewers"]["reason"], "test_mock")
        self.assertIn("-m", popen.call_args.args[0])
        self.assertIn("agentsight.tray.timeline_viewer", popen.call_args.args[0])
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertEqual(report["host_sent_event_count"], 0)
        kwargs = popen.call_args.kwargs
        self.assertNotEqual(kwargs.get("creationflags"), getattr(timeline_viewer_module.subprocess, "CREATE_NO_WINDOW", object()))
        startupinfo = kwargs.get("startupinfo")
        if startupinfo is not None:
            self.assertNotEqual(getattr(startupinfo, "wShowWindow", None), 0)

    def test_packaged_tray_prefers_adjacent_timeline_viewer_exe_over_source_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            packaged_viewer = Path(temp_dir) / "dist" / "AgentSightTimelineViewer.exe"
            packaged_viewer.parent.mkdir(parents=True)
            packaged_viewer.write_text("stub", encoding="utf-8")
            source_root = Path(temp_dir) / "source-root"
            source_root.mkdir()

            with mock.patch("agentsight.tray.timeline_viewer._packaged_timeline_viewer_exe", return_value=packaged_viewer):
                with mock.patch("agentsight.tray.timeline_viewer._source_repo_root_for_launcher", return_value=source_root):
                    with mock.patch(
                        "agentsight.tray.timeline_viewer._stop_existing_timeline_viewers",
                        return_value={"attempted": False, "reason": "test_mock"},
                    ):
                        with mock.patch("agentsight.tray.timeline_viewer.subprocess.Popen") as popen:
                            popen.return_value.pid = 4242
                            report = launch_timeline_viewer_process(mode="timeline")

        self.assertEqual(report["launch_kind"], "packaged_qt_viewer")
        self.assertEqual(popen.call_args.args[0][0], str(packaged_viewer))
        self.assertNotIn("-m", popen.call_args.args[0])
        kwargs = popen.call_args.kwargs
        self.assertNotEqual(kwargs.get("creationflags"), getattr(timeline_viewer_module.subprocess, "CREATE_NO_WINDOW", object()))
        startupinfo = kwargs.get("startupinfo")
        if startupinfo is not None:
            self.assertNotEqual(getattr(startupinfo, "wShowWindow", None), 0)

    def test_timeline_click_seek_policy_rejects_stale_playback_callbacks(self) -> None:
        self.assertFalse(
            timeline_viewer_module._timeline_playback_can_update_selection(
                playback_timer_active=False,
                syncing_to_player=False,
            )
        )
        self.assertFalse(
            timeline_viewer_module._timeline_playback_can_update_selection(
                playback_timer_active=True,
                syncing_to_player=True,
            )
        )
        self.assertTrue(
            timeline_viewer_module._timeline_playback_can_update_selection(
                playback_timer_active=True,
                syncing_to_player=False,
            )
        )
        self.assertFalse(
            timeline_viewer_module._timeline_segment_end_can_advance(
                playback_timer_active=False,
                pending_seek_ms=None,
            )
        )
        self.assertFalse(
            timeline_viewer_module._timeline_segment_end_can_advance(
                playback_timer_active=True,
                pending_seek_ms=120,
            )
        )
        self.assertTrue(
            timeline_viewer_module._timeline_segment_end_can_advance(
                playback_timer_active=True,
                pending_seek_ms=None,
            )
        )
        self.assertFalse(
            timeline_viewer_module._timeline_user_selection_should_emit(
                selected_index=4,
                nearest_index=4,
                force=False,
            )
        )
        self.assertTrue(
            timeline_viewer_module._timeline_user_selection_should_emit(
                selected_index=4,
                nearest_index=4,
                force=True,
            )
        )
        self.assertTrue(
            timeline_viewer_module._timeline_user_selection_should_emit(
                selected_index=4,
                nearest_index=5,
                force=False,
            )
        )

    def test_native_timeline_decodes_agseg_frame_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            from PIL import Image
            from agentsight.segments import BinarySegmentWriter

            segment_path = Path(temp_dir) / "segments" / "agentsight-test.agseg"
            writer = BinarySegmentWriter.create(segment_path, segment_id="agentsight-test")
            record = writer.add_frame(
                Image.new("RGB", (8, 8), (20, 120, 220)),
                timestamp_iso="2026-06-22T00:00:00+00:00",
                timestamp_monotonic_ms=1,
                source="idle",
            )
            writer.close()
            qimage, report = decode_frame_to_qimage(
                {
                    "segment_frame_id": record["frame_id"],
                    "segment_restore_ref": {
                        "storage_format": "binary_agseg",
                        "segment_path": str(segment_path),
                        "frame_id": record["frame_id"],
                    },
                }
            )

        self.assertIsNotNone(qimage)
        self.assertEqual(report["status"], "decoded")
        self.assertFalse(report["file_written"])
        self.assertEqual(report["raw_or_derived"], "derived_review_memory_only")

    def test_tray_language_setting_persists_in_local_appdata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                self.assertEqual(load_tray_settings()["language"], "system")
                saved = save_tray_language("zh")
                self.assertEqual(saved["language"], "zh")
                self.assertEqual(load_tray_settings()["language"], "zh")
                self.assertEqual(tray_settings_path(), local / "AgentSight" / "tray-settings.json")

    def test_dynamic_tray_menu_model_handles_emergency_and_discovery_missing_states(self) -> None:
        emergency = build_tray_menu_model(
            {
                "tray_status": "emergency_stopped",
                "controls": {
                    "can_pause_ai_real_control": True,
                    "can_allow_ai_real_control": False,
                    "can_clear_emergency_stop": True,
                },
            },
            language="en",
        )
        emergency_items = {item["key"]: item for item in emergency if item.get("kind") == "item"}
        self.assertEqual(emergency_items["state_label"]["label"], "State: Emergency stop")
        self.assertTrue(emergency_items["clear_emergency_stop"]["enabled"])

        missing = build_tray_menu_model({"tray_status": "discovery_missing", "controls": {}}, language="en")
        missing_items = {item["key"]: item for item in missing if item.get("kind") == "item"}
        self.assertEqual(missing_items["state_label"]["label"], "State: Discovery missing")
        self.assertFalse(missing_items["pause_agentsight"]["enabled"])
        self.assertFalse(missing_items["allow_agentsight"]["enabled"])
        self.assertTrue(missing_items["emergency_stop"]["enabled"])

    def test_tray_callback_events_support_legacy_and_notifyicon_v4_messages(self) -> None:
        self.assertFalse(tray_callback_opens_menu(WM_RBUTTONDOWN))
        self.assertTrue(tray_callback_opens_menu(WM_RBUTTONUP))
        self.assertTrue(tray_callback_opens_menu(WM_CONTEXTMENU))
        self.assertFalse(tray_callback_opens_menu(WM_LBUTTONUP))

        self.assertFalse(tray_callback_opens_status(WM_LBUTTONDOWN))
        self.assertTrue(tray_callback_opens_status(WM_LBUTTONUP))
        self.assertFalse(tray_callback_opens_status(WM_LBUTTONDBLCLK))
        self.assertFalse(tray_callback_opens_status(NIN_SELECT))
        self.assertTrue(tray_callback_opens_status(NIN_KEYSELECT))
        self.assertFalse(tray_callback_opens_status(WM_RBUTTONUP))

        self.assertEqual(tray_callback_event_code((17 << 16) | WM_CONTEXTMENU), WM_CONTEXTMENU)

    def test_status_summary_is_human_visible_without_leaking_token(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True)
                (agent_dir / "host-agent.json").write_text(
                    json.dumps({"pid": 5678, "token": "secret-token", "url": "http://127.0.0.1:8765"}),
                    encoding="utf-8",
                )
                (agent_dir / "service-state.json").write_text(
                    json.dumps(
                        {
                            "service_status": "ok_active_default_desktop",
                            "can_attempt_real_control": True,
                            "control_blockers": [],
                        }
                    ),
                    encoding="utf-8",
                )
                status = load_tray_status()
                text = status_summary_text(status, language="en")
                zh_text = status_summary_text(status, language="zh")

        self.assertIn("AgentSight", text)
        self.assertIn("Tray status: Ready", text)
        self.assertIn("Host Agent PID: 5678", text)
        self.assertIn("AI real control enabled: True", text)
        self.assertIn("Recording settings:", text)
        self.assertNotIn("secret-token", text)
        self.assertIn("no OCR, clipboard, DOM", text)
        self.assertIn("托盘状态: 可用", zh_text)
        self.assertIn("录制设置:", zh_text)
        self.assertIn("边界：不做 OCR", zh_text)

    def test_tray_status_falls_back_to_unified_supervisor_service_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True)
                (agent_dir / "host-agent.json").write_text(
                    json.dumps({"pid": 5678, "url": "http://127.0.0.1:8765"}),
                    encoding="utf-8",
                )
                (agent_dir / "session-supervisor-state.json").write_text(
                    json.dumps(
                        {
                            "host_agent": {
                                "probe": {
                                    "health": {
                                        "service_state": {
                                            "service_status": "ok_active_default_desktop",
                                            "can_attempt_real_control": True,
                                            "control_blockers": [],
                                        }
                                    }
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                status = load_tray_status()

        self.assertEqual(status["tray_status"], "ready")
        self.assertTrue(status["can_attempt_real_control"])
        self.assertTrue(status["service"]["state_present"])
        self.assertIn("session-supervisor-state.json", status["paths"]["service_state_file"])

    def test_tray_status_uses_host_health_blockers_before_embedded_service_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True)
                (agent_dir / "host-agent.json").write_text(
                    json.dumps({"pid": 5678, "url": "http://127.0.0.1:8765"}),
                    encoding="utf-8",
                )
                (agent_dir / "session-supervisor-state.json").write_text(
                    json.dumps(
                        {
                            "host_agent": {
                                "probe": {
                                    "health": {
                                        "service_status": "ok_active_default_desktop",
                                        "can_attempt_real_control": False,
                                        "control_blockers": ["screen_capture_unavailable"],
                                        "service_state": {
                                            "service_status": "ok_active_default_desktop",
                                            "can_attempt_real_control": True,
                                            "control_blockers": [],
                                        },
                                    }
                                }
                            }
                        }
                    ),
                    encoding="utf-8",
                )
                status = load_tray_status()

        self.assertEqual(status["tray_status"], "blocked")
        self.assertFalse(status["can_attempt_real_control"])
        self.assertEqual(status["control_blockers"], ["screen_capture_unavailable"])

    def test_status_reports_tray_gui_entrypoint_as_available_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                status = load_tray_status()

        self.assertEqual(status["controls"]["tray_icon_gui_entrypoint"], "agentsight-tray-gui")
        self.assertEqual(status["controls"]["tray_icon_gui_available"], tray_gui_module._is_windows())
        self.assertEqual(
            status["controls"]["physical_emergency_hotkey"]["tray_gui_starts_monitor_by_default"],
            tray_gui_module._is_windows(),
        )

    def test_gui_describe_command_can_write_json_for_windowed_packaging_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "describe.json"
            exit_code = tray_gui_main(["describe", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["object_type"], "AgentSightTrayDescription")
        self.assertFalse(report["boundary"]["window_semantics_used"])

    def test_console_tray_cli_can_describe_gui_for_packaged_runtime_diagnostics(self) -> None:
        with mock.patch("sys.stdout") as stdout:
            exit_code = tray_cli_main(["gui-describe"])
        report = json.loads("".join(call.args[0] for call in stdout.write.call_args_list))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["object_type"], "AgentSightTrayDescription")
        self.assertTrue(report["menu_model"]["dynamic_from_tray_status"])
        self.assertTrue(report["tray_icon_state_model"]["transparent_background"])
        self.assertFalse(report["menu_model"]["clipboard_action_present"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["window_semantics_used"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_gui_describe_avoids_wmi_backed_platform_system_probe(self) -> None:
        with mock.patch("platform.system", side_effect=AssertionError("platform.system must not run during describe")):
            report = build_tray_gui_description()

        self.assertEqual(report["object_type"], "AgentSightTrayDescription")
        self.assertIsInstance(report["tray_icon_gui_available"], bool)
        self.assertFalse(report["host_input_sent"])

    def test_gui_run_report_includes_hotkey_monitor_without_host_input_claims(self) -> None:
        report = build_tray_gui_run_report(
            tray_icon_gui_started=True,
            tray_icon_added=True,
            run_seconds_requested=1,
            started_at_ms=100,
            ended_at_ms=200,
            exit_code=0,
            physical_hotkey_monitor_enabled=True,
            hotkey_start_report={"hotkey_status": "monitoring", "host_input_sent": False},
            hotkey_trigger_report=None,
            hotkey_stop_report={"hotkey_status": "stopped_without_trigger", "stop_reason": "tray_gui_exiting"},
        )

        self.assertTrue(report["physical_hotkey_monitor_enabled"])
        self.assertEqual(report["physical_hotkey_monitor_start"]["hotkey_status"], "monitoring")
        self.assertIsNone(report["physical_hotkey_monitor_trigger"])
        self.assertEqual(report["physical_hotkey_monitor_stop"]["stop_reason"], "tray_gui_exiting")
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_gui_already_running_report_is_single_instance_no_host_input(self) -> None:
        report = build_tray_gui_already_running_report(
            run_seconds_requested=1,
            started_at_ms=100,
            ended_at_ms=101,
            physical_hotkey_monitor_requested=True,
        )

        self.assertEqual(report["object_type"], "AgentSightTrayAlreadyRunningReport")
        self.assertEqual(report["run_status"], "already_running")
        self.assertTrue(report["single_instance_guard"])
        self.assertTrue(report["tray_window_present"])
        self.assertFalse(report["new_tray_window_started"])
        self.assertFalse(report["physical_hotkey_monitor_started"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_gui_run_exits_without_second_window_when_tray_window_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "already-running.json"
            with mock.patch("agentsight.tray.gui._tray_window_present", return_value=True):
                with mock.patch("agentsight.tray.gui.Win32TrayApp") as app_cls:
                    exit_code = tray_gui_main(["run", "--seconds", "1", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        app_cls.assert_not_called()
        self.assertEqual(report["run_status"], "already_running")
        self.assertFalse(report["new_tray_window_started"])
        self.assertFalse(report["host_input_sent"])

    def test_tray_gui_starts_hotkey_monitor_with_existing_emergency_stop_path(self) -> None:
        app = object.__new__(Win32TrayApp)
        app.enable_hotkey_monitor = True
        app.hotkey_monitor = None
        app.hotkey_start_report = None
        app.user32 = mock.Mock()

        with mock.patch("agentsight.tray.gui._is_windows", return_value=True):
            with mock.patch("agentsight.tray.gui.EmergencyHotkeyMonitor") as monitor_cls:
                monitor = monitor_cls.return_value
                monitor.start.return_value = {
                    "hotkey_status": "monitoring",
                    "host_input_sent": False,
                    "host_sent_event_count": 0,
                }

                app._start_hotkey_monitor()

        self.assertIs(app.hotkey_monitor, monitor)
        self.assertEqual(app.hotkey_start_report["hotkey_status"], "monitoring")
        kwargs = monitor_cls.call_args.kwargs
        self.assertEqual(kwargs["stop_callback"], app._emergency_stop_from_physical_hotkey)

    def test_tray_gui_can_disable_hotkey_monitor_for_smoke_or_debug(self) -> None:
        app = object.__new__(Win32TrayApp)
        app.enable_hotkey_monitor = False
        app.hotkey_monitor = None
        app.hotkey_start_report = None

        app._start_hotkey_monitor()

        self.assertIsNone(app.hotkey_monitor)
        self.assertEqual(app.hotkey_start_report["hotkey_status"], "disabled")
        self.assertFalse(app.hotkey_start_report["host_input_sent"])

    def test_resident_status_reports_human_visible_watchdog_without_host_input(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.tray.gui._tray_window_present", return_value=True):
                    report = tray_gui_resident_status()

        self.assertEqual(report["schema"], "agentsight_tray_resident_v1")
        self.assertEqual(report["resident_role"], "human_visible_tray_presence")
        self.assertTrue(report["tray_window_present"])
        self.assertTrue(report["tray_icon_expected_visible"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertFalse(report["boundary"]["clipboard_used"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_install_resident_writes_startup_vbs_and_watchdog_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "repo"
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.tray.gui._install_tray_onlogon_task") as install_task:
                    install_task.return_value = {"task_name": "AgentSightTrayOnLogon", "install_status": "installed"}

                    report = install_tray_gui_resident(
                        repo_root=root,
                        python_command="py",
                        tray_gui_exe=None,
                        start_now=False,
                        wait_seconds=0.0,
                    )

                command_path = Path(report["watchdog_command"])
                vbs_path = Path(report["startup_launcher"])
                self.assertEqual(report["install_status"], "installed")
                self.assertEqual(report["install_mode"], "source_python")
                self.assertTrue(command_path.exists())
                self.assertTrue(vbs_path.exists())
                self.assertIn("-m agentsight.tray.gui watchdog", command_path.read_text(encoding="utf-8"))
                self.assertIn(str(command_path), vbs_path.read_text(encoding="ascii"))
                self.assertFalse(report["host_input_sent"])
                self.assertEqual(report["host_sent_event_count"], 0)
                self.assertFalse(report["boundary"]["accessibility_tree_used"])

    def test_source_mode_default_tray_gui_exe_arg_avoids_stale_dist_exe(self) -> None:
        with mock.patch.object(tray_gui_module.sys, "frozen", False, create=True):
            exe_arg = tray_gui_module._default_tray_gui_exe_arg()

        self.assertEqual(exe_arg, "")

    def test_start_resident_auto_falls_back_to_startup_vbs_when_onlogon_task_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.tray.gui._install_tray_onlogon_task") as install_task:
                    install_task.return_value = {"task_name": "AgentSightTrayOnLogon", "install_status": "install_failed"}
                    install_tray_gui_resident(
                        repo_root=Path(temp_dir) / "repo",
                        python_command="py",
                        tray_gui_exe=None,
                        start_now=False,
                        wait_seconds=0.0,
                    )
                with mock.patch("agentsight.tray.gui._run_tray_onlogon_task") as run_task:
                    run_task.return_value = {"start_method_used": "onlogon_task", "started": False}
                    with mock.patch("agentsight.tray.gui._start_via_startup_vbs") as start_vbs:
                        start_vbs.return_value = {"start_method_used": "startup_vbs", "started": True}
                        with mock.patch("agentsight.tray.gui._wait_for_tray_window", return_value=True):
                            report = start_installed_tray_gui_resident(start_method="auto", wait_seconds=0.0)

        self.assertEqual(report["start_status"], "started")
        self.assertEqual(report["launcher"]["start_method_used"], "startup_vbs")
        self.assertTrue(report["tray_visible_after_wait"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_watchdog_once_starts_child_when_tray_window_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "watchdog.json"
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.tray.gui._tray_window_present", side_effect=[False, True]):
                    with mock.patch("agentsight.tray.gui._start_tray_gui_child") as start_child:
                        start_child.return_value = {"started": True, "pid": 1234, "command": ["py", "-m", "agentsight.tray.gui", "run"]}

                        exit_code = run_tray_gui_watchdog(interval_seconds=0.5, once=True, output=str(output))

                report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["watchdog_status"], "running")
        self.assertTrue(report["started_child"])
        self.assertTrue(report["tray_window_present"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_uninstall_resident_sets_stop_marker_and_removes_launchers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.tray.gui._install_tray_onlogon_task") as install_task:
                    install_task.return_value = {"task_name": "AgentSightTrayOnLogon", "install_status": "installed"}
                    report = install_tray_gui_resident(
                        repo_root=Path(temp_dir) / "repo",
                        python_command="py",
                        tray_gui_exe=None,
                        start_now=False,
                        wait_seconds=0.0,
                    )
                command_path = Path(report["watchdog_command"])
                vbs_path = Path(report["startup_launcher"])
                with mock.patch("agentsight.tray.gui._delete_tray_onlogon_task") as delete_task:
                    delete_task.return_value = {"task_name": "AgentSightTrayOnLogon", "delete_status": "deleted"}

                    uninstall = uninstall_tray_gui_resident(stop_running=False)

                stop_path = Path(uninstall["watchdog_stop_file"])
                self.assertFalse(command_path.exists())
                self.assertFalse(vbs_path.exists())
                self.assertTrue(stop_path.exists())
                self.assertTrue(uninstall["watchdog_stop_requested"])
                self.assertEqual(uninstall["tray_close"]["reason"], "keep_tray_running")
                self.assertFalse(uninstall["host_input_sent"])
                self.assertEqual(uninstall["host_sent_event_count"], 0)

    def test_resident_status_command_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "resident-status.json"
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.tray.gui._tray_window_present", return_value=False):
                    exit_code = tray_gui_main(["status-resident", "--output", str(output)])
                report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["object_type"], "AgentSightTrayResidentStatus")
        self.assertFalse(report["tray_window_present"])
        self.assertFalse(report["host_input_sent"])

    def test_visible_notification_delivery_guidance_uses_public_screen_look_do_chain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            notification_file = Path(temp_dir) / "notifications.json"
            caller_id = "operator-notification-test-ai"
            enqueue_notification("短消息", path=notification_file, now_ms=1000)
            claim_next_notification(caller_id=caller_id, path=notification_file, now_ms=1100)

            report = prepare_notification_delivery_draft(caller_id=caller_id, path=notification_file, now_ms=1200)

        workflow = report["delivery_draft"]["host_agent_workflow_package"]
        workflow_json = json.dumps(workflow, ensure_ascii=False, sort_keys=True)
        self.assertEqual(workflow["required_public_tools"], ["Host Agent /screen", "Host Agent /look", "Host Agent /do"])
        self.assertIn("Host Agent /screen", workflow_json)
        self.assertIn("Host Agent /look", workflow_json)
        self.assertIn("Host Agent /do", workflow_json)
        self.assertNotIn("Host Agent /observe", workflow_json)
        self.assertNotIn("Host Agent /mouse", workflow_json)
        self.assertNotIn("Host Agent /input", workflow_json)
        self.assertFalse(report["delivery_draft"]["tool_asserts_delivery_success"])

    def test_ai_status_startup_guidance_does_not_recommend_legacy_visual_memory_tool(self) -> None:
        report = ai_status_report(
            caller_id="ai-status-test",
            tray_status_snapshot={
                "tray_status": "ready",
                "can_attempt_real_control": True,
                "control_blockers": [],
                "service": {
                    "service_status": "ok_active_default_desktop",
                    "can_attempt_real_control": True,
                    "control_blockers": [],
                },
                "host_agent": {"discovery_present": True, "pid": 1234},
                "operator_control_policy": {"policy_status": "allowed", "real_control_enabled": True},
            },
            release_summary_snapshot={"publication_blocked": False},
        )

        report_json = json.dumps(report, sort_keys=True)
        self.assertEqual(report["visual_memory_public_flow"]["public_tools"], ["screen", "look", "do"])
        self.assertIn("/look time.near", report_json)
        self.assertNotIn("query_visual_memory", report_json)
        self.assertFalse(report["non_actions"]["screen_capture_performed"])


if __name__ == "__main__":
    unittest.main()

