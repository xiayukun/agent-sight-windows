from __future__ import annotations

import ctypes
import hashlib
import os
import platform
import struct
import sys
import time
import uuid
import zlib
from ctypes import wintypes
from pathlib import Path
from subprocess import Popen
from typing import Any, Mapping

from ai_control.diagnostics.input_smoke import (
    DEFAULT_OBSERVATION_CHANNEL_REF,
    build_manual_windows_input_adapter,
    run_manual_input_smoke,
)
from ai_control.runtime_platform import is_windows, platform_system_label


P0_ARMING_FLAG = "AI_CONTROL_P0_REAL_INPUT_SMOKE"
ARMING_REF = "AI_CONTROL_REAL_INPUT_ARMING_REF"
CONSENT_REF = "AI_CONTROL_REAL_INPUT_CONSENT_REF"
DEFAULT_TEXT = "AI-CONTROL-TEST"


def _is_windows() -> bool:
    return is_windows()


def _platform_system_label() -> str:
    return platform_system_label()


def run_p0_real_input_closed_loop_smoke(
    *,
    runs_dir: str | Path = "runs_p0_real_input_closed_loop",
    env: Mapping[str, str] | None = None,
    launch_apps: bool = True,
    include_notepad: bool = True,
    include_calculator: bool = True,
) -> dict[str, Any]:
    active_env = env if env is not None else os.environ
    root = Path(runs_dir)
    host = WindowsHostProbe()
    started_at = _utc_now()
    dpi_awareness = host.ensure_dpi_awareness()
    input_desktop = host.ensure_input_desktop()
    environment = host.environment_report(dpi_awareness=dpi_awareness, input_desktop=input_desktop)
    arming = _arming_status(active_env)
    if not arming["armed"]:
        return {
            "object_type": "P0RealInputClosedLoopSmokeReport",
            "schema": "p0_real_input_closed_loop_v1",
            "p0_status": "not_armed_no_host_input",
            "exit_code": 2,
            "started_at": started_at,
            "ended_at": _utc_now(),
            "armed": False,
            "environment": environment,
            "tasks": [],
            "input_executed": False,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "external_review_required": True,
            "suggested_next": [
                "set_AI_CONTROL_P0_REAL_INPUT_SMOKE_armed_only_when_the_human_host_intends_real_input",
                "open_or_allow_the_smoke_to_launch_notepad_and_calculator",
            ],
            "safe_report_lines": [
                "P0 real-input smoke did not run because host arming was absent.",
                "No install, command product control, clipboard, OCR, window semantics, DOM, accessibility, or host input was executed by this report.",
                "Set AI_CONTROL_P0_REAL_INPUT_SMOKE=armed plus arming and consent refs only for an explicit host-side P0 smoke.",
            ],
        }

    tasks: list[dict[str, Any]] = []
    if include_notepad:
        tasks.append(_run_notepad_task(root / "notepad", host=host, arming=arming, launch_app=launch_apps))
    if include_calculator:
        tasks.append(_run_calculator_task(root / "calculator", host=host, arming=arming, launch_app=launch_apps))
    complete = bool(tasks) and all(task.get("task_status") == "input_and_evidence_recorded" for task in tasks)
    host_sent_event_count = sum(int(task.get("host_sent_event_count") or 0) for task in tasks)
    observation_content = _p0_observation_content(tasks)
    blockers = [
        blocker
        for blocker in [
            _observation_blocking_diagnosis(tasks),
            _foreground_blocking_diagnosis(tasks, environment),
            _input_blocking_diagnosis(tasks, environment),
        ]
        if blocker
    ]
    p0_status = (
        "host_input_events_and_visual_evidence_recorded"
        if complete and host_sent_event_count > 0 and not observation_content["capture_content_degenerate"]
        else "p0_smoke_incomplete"
    )
    exit_code = 0 if p0_status == "host_input_events_and_visual_evidence_recorded" else 1
    if blockers:
        p0_status = "p0_smoke_blocked_multiple_conditions" if len(blockers) > 1 else blockers[0]["blocking_condition"]
        exit_code = 5
    return {
        "object_type": "P0RealInputClosedLoopSmokeReport",
        "schema": "p0_real_input_closed_loop_v1",
        "p0_status": p0_status,
        "exit_code": exit_code,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "armed": True,
        "environment": environment,
        "tasks": tasks,
        "host_input_sent": host_sent_event_count > 0,
        "host_sent_event_count": host_sent_event_count,
        "observation_content": observation_content,
        "capture_content_degenerate": observation_content["capture_content_degenerate"],
        "blocking_diagnosis": blockers[0] if blockers else None,
        "blocking_diagnoses": blockers,
        "external_review_required": True,
        "red_line": {
            "tool_asserts_business_success": False,
            "input_visual_relationship_judgment": "external_review_only",
            "visual_review_owner": "human_or_external_visual_ai_reviews_saved_before_after_images",
        },
        "safe_report_lines": _p0_safe_report_lines(tasks, host_sent_event_count),
    }


def _run_notepad_task(root: Path, *, host: "WindowsHostProbe", arming: dict[str, Any], launch_app: bool) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    target_file = root / f"AIControlP0Notepad-{uuid.uuid4().hex[:8]}.txt"
    target_file.write_text("", encoding="utf-8")
    setup = host.launch_and_focus(
        "notepad.exe",
        title_contains=[target_file.name],
        launch_app=launch_app,
        launch_args=[str(target_file)],
    )
    region = _region_from_window_rect(setup.get("window_rect"))
    task_env = {
        "task_name": "notepad_key_text_stream",
        "target_app": "notepad",
        "target_test_file": str(target_file),
        "input_goal_for_external_review": f"after image should visibly contain {DEFAULT_TEXT!r}",
        "setup": setup,
        "coordinate_audit": {
            "input_kind": "keyboard",
            "dpi_awareness": host.dpi_awareness_snapshot(),
            "target_window_foreground_before_input": setup.get("foreground_match"),
            "observation_region": region,
        },
    }
    reports: list[dict[str, Any]] = []
    if region and not setup.get("foreground_match"):
        focus_click = _focus_click_action(region)
        focus_report = _run_manual_action(
            root,
            arming=arming,
            action=focus_click,
            observation_region=region,
            action_label="focus_target_window_click",
            setup=setup,
            allow_non_foreground_input=True,
        )
        reports.append(focus_report)
        time.sleep(0.25)
        foreground_after_click = host.foreground_window_info()
        foreground_match_after_click = bool(
            setup.get("window", {}).get("hwnd")
            and foreground_after_click.get("hwnd") == setup.get("window", {}).get("hwnd")
        )
        setup["focus_click_action_recorded"] = True
        setup["focus_click_point"] = {key: focus_click[key] for key in ("x", "y")}
        setup["foreground_window_after_focus_click"] = foreground_after_click
        setup["foreground_match_after_focus_click"] = foreground_match_after_click
        setup["foreground_match"] = foreground_match_after_click
        setup["setup_status"] = "focused_after_recorded_focus_click" if foreground_match_after_click else setup.get("setup_status")
        task_env["coordinate_audit"]["target_window_foreground_before_input"] = foreground_match_after_click

    reports.append(_run_manual_action(
        root,
        arming=arming,
        action={"input_type": "key_text_stream", "text": DEFAULT_TEXT},
        observation_region=region,
        setup=setup,
    ))
    return _task_report(task_env, reports)


def _run_calculator_task(root: Path, *, host: "WindowsHostProbe", arming: dict[str, Any], launch_app: bool) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    setup = host.launch_and_focus("calc.exe", title_contains=["Calculator", "计算器"], launch_app=launch_app)
    rect = setup.get("window_rect")
    region = _region_from_window_rect(rect)
    click_plan = _calculator_click_plan(rect)
    task_env = {
        "task_name": "calculator_mouse_click_2_plus_2_equals",
        "target_app": "calculator",
        "input_goal_for_external_review": "after final image should visibly show the calculator result 4",
        "setup": setup,
        "coordinate_audit": {
            "input_kind": "mouse",
            "dpi_awareness": host.dpi_awareness_snapshot(),
            "target_window_foreground_before_input": setup.get("foreground_match"),
            "observation_region": region,
            "click_plan": click_plan,
            "coordinate_space": "screen_pixels_expected_to_match_wgc_monitor_pixels_on_primary_monitor",
            "tool_asserts_click_hit_target": False,
        },
    }
    reports = []
    for item in click_plan:
        reports.append(
            _run_manual_action(
                root,
                arming=arming,
                action={"input_type": "mouse_click", "x": item["x"], "y": item["y"], "button": "left"},
                observation_region=region,
                action_label=item["label"],
                setup=setup,
            )
        )
        time.sleep(0.08)
    return _task_report(task_env, reports)


def _run_manual_action(
    root: Path,
    *,
    arming: dict[str, Any],
    action: dict[str, Any],
    observation_region: dict[str, int] | None,
    action_label: str | None = None,
    setup: dict[str, Any] | None = None,
    allow_non_foreground_input: bool = False,
) -> dict[str, Any]:
    if setup is not None and not setup.get("foreground_match") and not allow_non_foreground_input:
        return _run_preflight_observation_stop(
            root,
            arming=arming,
            observation_region=observation_region,
            action=action,
            action_label=action_label,
            setup=setup,
        )

    adapter = build_manual_windows_input_adapter(
        runs_dir=root,
        arming_ref=arming["arming_ref"],
        operator_consent_ref=arming["operator_consent_ref"],
        default_observation_channel_ref=DEFAULT_OBSERVATION_CHANNEL_REF,
    )
    policy = {
        "enabled": True,
        "schema": "manual_post_action_capture_policy_v1",
        "mode": "sequence",
        "frame_count": 3,
        "interval_ms": 100,
        "delay_ms": 100,
        "duration_ms": 0,
        "effective_duration_ms": 200,
        "duration_derivation": "explicit_frame_count",
        "media_kind": "gif",
        "video_requested": False,
        "video_supported": False,
        "min_frame_count": 2,
        "max_frame_count": 5,
        "min_interval_ms": 50,
        "max_interval_ms": 250,
        "max_total_capture_duration_ms": 1000,
        "max_delay_ms": 5000,
        "continuous_recording": False,
        "visualization_only": True,
        "application_effect_unverified": True,
        "requested": {
            "enabled": True,
            "mode": "sequence",
            "media_kind": "gif",
            "frame_count": 3,
            "interval_ms": 100,
            "delay_ms": 100,
            "duration_ms": None,
        },
    }
    if observation_region:
        policy["region"] = observation_region
        policy["requested"]["region"] = observation_region
    observation_request = {"mode": "region", "region": observation_region} if observation_region else {"mode": "fullscreen"}
    report = run_manual_input_smoke(
        adapter,
        runs_dir=root,
        arming_ref=arming["arming_ref"],
        operator_consent_ref=arming["operator_consent_ref"],
        action=action,
        observation_channel_ref=DEFAULT_OBSERVATION_CHANNEL_REF,
        observation_request=observation_request,
        post_action_capture_policy=policy,
    )
    return _manual_action_summary(report, action_label=action_label)


def _focus_click_action(region: dict[str, int]) -> dict[str, Any]:
    return {
        "input_type": "mouse_click",
        "x": int(region["x"]) + min(24, max(1, int(region["width"]) - 1)),
        "y": int(region["y"]) + min(96, max(1, int(region["height"]) - 1)),
        "button": "left",
    }


def _run_preflight_observation_stop(
    root: Path,
    *,
    arming: dict[str, Any],
    observation_region: dict[str, int] | None,
    action: dict[str, Any],
    action_label: str | None,
    setup: dict[str, Any],
) -> dict[str, Any]:
    report = _run_manual_action(
        root,
        arming=arming,
        action={"input_type": "wait", "duration_ms": 1},
        observation_region=observation_region,
        action_label=action_label or "preflight_observation_only",
        setup=None,
    )
    report.update(
        {
            "smoke_status": "manual_input_preflight_failed",
            "smoke_status_detail": "target_window_not_foreground",
            "requested_input_type_blocked_before_host_input": action.get("input_type"),
            "host_input_sent": False,
            "host_input_executed": False,
            "host_sent_event_count": 0,
            "failure_code": "TARGET_WINDOW_NOT_FOREGROUND",
            "failure_detail": (
                "Target window was not the foreground window after focus attempt; "
                "real keyboard/mouse input was not sent."
            ),
            "foreground_preflight": {
                "foreground_match": bool(setup.get("foreground_match")),
                "focus_result": setup.get("focus_result"),
                "foreground_window_after_focus": setup.get("foreground_window_after_focus"),
            },
        }
    )
    return report


def _manual_action_summary(report: dict[str, Any], *, action_label: str | None) -> dict[str, Any]:
    before_ref = report.get("before_media_ref")
    after_ref = report.get("after_media_ref")
    before_sha = report.get("before_media_sha256")
    after_sha = report.get("after_media_sha256")
    before_path = _media_path(report, before_ref)
    after_path = _media_path(report, after_ref)
    before_content = _media_content_diagnostics(before_path)
    after_content = _media_content_diagnostics(after_path)
    review_after = _external_review_after_media(
        report,
        fallback_media_ref=after_ref,
        fallback_media_sha256=after_sha,
    )
    review_after_content = _media_content_diagnostics(review_after.get("media_path_abs"))
    return {
        "action_label": action_label,
        "smoke_status": report.get("smoke_status"),
        "smoke_status_detail": report.get("smoke_status_detail"),
        "input_backend_kind": report.get("input_backend_kind"),
        "host_api": report.get("host_api"),
        "input_type": report.get("action", {}).get("input_type"),
        "host_input_sent": bool(report.get("host_input_executed")),
        "host_input_executed": bool(report.get("host_input_executed")),
        "host_sent_event_count": report.get("host_sent_event_count") or 0,
        "requested_event_count": report.get("requested_event_count"),
        "released_inputs": report.get("released_inputs"),
        "release_result": report.get("release_result"),
        "before_observation_ref": report.get("before_observation_ref"),
        "before_media_ref": before_ref,
        "before_media_path_abs": before_path,
        "before_media_sha256": before_sha,
        "before_media_content": before_content,
        "before_capture_content_degenerate": bool(before_content.get("capture_content_degenerate")),
        "after_observation_ref": report.get("after_observation_ref"),
        "after_media_ref": after_ref,
        "after_media_path_abs": after_path,
        "after_media_sha256": after_sha,
        "after_media_content": after_content,
        "after_capture_content_degenerate": bool(after_content.get("capture_content_degenerate")),
        "external_review_after_media_ref": review_after.get("media_ref"),
        "external_review_after_media_path_abs": review_after.get("media_path_abs"),
        "external_review_after_media_sha256": review_after.get("media_sha256"),
        "external_review_after_media_source": review_after.get("source"),
        "external_review_after_frame_ref": review_after.get("frame_ref"),
        "external_review_after_matches_immediate_after": review_after.get("media_path_abs") == after_path,
        "external_review_after_media_content": review_after_content,
        "external_review_after_capture_content_degenerate": bool(
            review_after_content.get("capture_content_degenerate")
        ),
        "before_after_media_hash_changed": bool(before_sha and after_sha and before_sha != after_sha),
        "post_action_sequence_ref": report.get("post_action_sequence_ref"),
        "post_action_sequence_media_refs": report.get("post_action_sequence_media_refs"),
        "post_action_sequence_media_path_abs": report.get("post_action_sequence_media_path_abs"),
        "post_action_sequence_change_summary": report.get("post_action_sequence_change_summary"),
        "evidence_package_ok": bool(report.get("evidence_package_ok")),
        "replay_read_only": bool(report.get("replay_read_only")),
        "integrity_ok": bool(report.get("integrity_ok")),
        "tool_asserts_business_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "failure_code": report.get("failure_code"),
        "failure_detail": report.get("failure_detail"),
    }


def _task_report(task_env: dict[str, Any], action_reports: list[dict[str, Any]]) -> dict[str, Any]:
    host_count = sum(int(item.get("host_sent_event_count") or 0) for item in action_reports)
    evidence_ok = all(item.get("evidence_package_ok") and item.get("replay_read_only") and item.get("integrity_ok") for item in action_reports)
    any_failed = any(item.get("smoke_status") != "manual_input_succeeded" for item in action_reports)
    content = _task_observation_content(action_reports)
    status = "recorded_with_failures" if any_failed else "input_and_evidence_recorded"
    return {
        **task_env,
        "task_status": status,
        "host_input_sent": host_count > 0,
        "host_sent_event_count": host_count,
        "evidence_chain_complete": bool(evidence_ok),
        "observation_content": content,
        "capture_content_degenerate": content["capture_content_degenerate"],
        "action_reports": action_reports,
        "external_review_required": True,
        "tool_asserts_task_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "safe_report_lines": [
            (
                f"Task {task_env['task_name']}: host_sent_event_count={host_count}; "
                f"evidence_chain_complete={bool(evidence_ok)}; "
                f"capture_content_degenerate={content['capture_content_degenerate']}."
            ),
            "Review before/after image paths externally; the tool does not assert the input caused the visible change.",
        ],
    }


def _media_path(report: dict[str, Any], media_ref: Any) -> str | None:
    if not isinstance(media_ref, str):
        return None
    path = Path(report["runs_dir"]) / report["session_id"] / media_ref
    return str(path.resolve())


def _external_review_after_media(
    report: dict[str, Any],
    *,
    fallback_media_ref: Any,
    fallback_media_sha256: Any,
) -> dict[str, Any]:
    sequence_frame = _last_post_action_sequence_frame_media(report)
    if sequence_frame:
        return sequence_frame
    return {
        "source": "execute_after_observation",
        "frame_ref": report.get("after_observation_ref"),
        "media_ref": fallback_media_ref,
        "media_path_abs": _media_path(report, fallback_media_ref),
        "media_sha256": fallback_media_sha256,
    }


def _last_post_action_sequence_frame_media(report: dict[str, Any]) -> dict[str, Any] | None:
    refs = report.get("post_action_sequence_media_refs")
    if isinstance(refs, list):
        for item in reversed(refs):
            if not isinstance(item, dict):
                continue
            media_ref = item.get("media_ref")
            media_path = _media_path(report, media_ref)
            if media_path and Path(media_path).exists():
                return {
                    "source": "post_action_sequence_last_frame",
                    "frame_ref": item.get("frame_ref"),
                    "media_ref": media_ref,
                    "media_path_abs": media_path,
                    "media_sha256": item.get("media_sha256") or _file_sha256(media_path),
                }

    for frame_ref in reversed(_post_action_sequence_frame_refs(report)):
        media_ref = _media_ref_for_frame_ref(report, frame_ref)
        media_path = _media_path(report, media_ref)
        if media_path and Path(media_path).exists():
            return {
                "source": "post_action_sequence_last_frame_derived_from_change_summary",
                "frame_ref": frame_ref,
                "media_ref": media_ref,
                "media_path_abs": media_path,
                "media_sha256": _file_sha256(media_path),
            }
    return None


def _post_action_sequence_frame_refs(report: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    generated = report.get("post_action_sequence_generated_from_frame_refs")
    if isinstance(generated, list):
        refs.extend(item for item in generated if isinstance(item, str))
    summary = report.get("post_action_sequence_change_summary")
    comparisons = summary.get("comparisons") if isinstance(summary, dict) else None
    if isinstance(comparisons, list):
        for comparison in comparisons:
            if not isinstance(comparison, dict):
                continue
            after_ref = comparison.get("after_frame_ref")
            if isinstance(after_ref, str):
                refs.append(after_ref)
    return list(dict.fromkeys(refs))


def _media_ref_for_frame_ref(report: dict[str, Any], frame_ref: str) -> str | None:
    session_root = Path(report["runs_dir"]) / report["session_id"]
    media_dir = session_root / "media"
    for suffix in (".png", ".bmp", ".jpg", ".jpeg", ".webp"):
        candidate = media_dir / f"{frame_ref}{suffix}"
        if candidate.exists():
            return Path("media", candidate.name).as_posix()
    matches = sorted(media_dir.glob(f"{frame_ref}.*")) if media_dir.exists() else []
    if matches:
        return Path("media", matches[0].name).as_posix()
    return None


def _file_sha256(path_text: str | None) -> str | None:
    if not path_text:
        return None
    path = Path(path_text)
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _media_content_diagnostics(path_text: str | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "media_path_abs": path_text,
        "content_checked": False,
        "capture_content_valid": False,
        "capture_content_degenerate": False,
    }
    if not path_text:
        return {**base, "status": "media_absent"}
    path = Path(path_text)
    if not path.exists():
        return {**base, "status": "media_missing"}
    try:
        data = path.read_bytes()
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            parsed = _png_content_stats(data)
        elif data.startswith(b"BM"):
            parsed = _bmp_content_stats(data)
        else:
            return {
                **base,
                "content_checked": True,
                "status": "unsupported_media_format",
                "byte_size": len(data),
            }
    except Exception as exc:
        return {
            **base,
            "content_checked": True,
            "status": "content_check_failed",
            "error": str(exc),
        }
    degenerate = _content_stats_degenerate(parsed)
    return {
        **base,
        **parsed,
        "content_checked": True,
        "status": "checked",
        "capture_content_valid": not degenerate,
        "capture_content_degenerate": degenerate,
        "degenerate_reasons": _content_degenerate_reasons(parsed),
    }


def _png_content_stats(data: bytes) -> dict[str, Any]:
    offset = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    while offset + 8 <= len(data):
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _compression, _filter_method, interlace = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if not width or not height or bit_depth != 8 or color_type not in {0, 2, 4, 6} or interlace != 0:
        return {
            "media_format": "png",
            "byte_size": len(data),
            "width": width,
            "height": height,
            "parse_status": "unsupported_png_layout",
            "bit_depth": bit_depth,
            "color_type": color_type,
            "interlace": interlace,
        }
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    rows: list[bytes] = []
    source_offset = 0
    previous = bytes(stride)
    for _y in range(height):
        filter_type = raw[source_offset]
        source_offset += 1
        row = bytearray(raw[source_offset : source_offset + stride])
        source_offset += stride
        _unfilter_png_row(row, previous, filter_type, channels)
        rows.append(bytes(row))
        previous = bytes(row)
    stats = _pixel_stats_from_png_rows(rows, width=width, height=height, color_type=color_type, channels=channels)
    return {
        "media_format": "png",
        "byte_size": len(data),
        "parse_status": "parsed",
        "width": width,
        "height": height,
        "bit_depth": bit_depth,
        "color_type": color_type,
        **stats,
    }


def _unfilter_png_row(row: bytearray, previous: bytes, filter_type: int, bpp: int) -> None:
    for index in range(len(row)):
        left = row[index - bpp] if index >= bpp else 0
        up = previous[index] if previous else 0
        upper_left = previous[index - bpp] if previous and index >= bpp else 0
        if filter_type == 0:
            value = row[index]
        elif filter_type == 1:
            value = row[index] + left
        elif filter_type == 2:
            value = row[index] + up
        elif filter_type == 3:
            value = row[index] + ((left + up) // 2)
        elif filter_type == 4:
            value = row[index] + _paeth(left, up, upper_left)
        else:
            raise ValueError(f"unsupported png filter type {filter_type}")
        row[index] = value & 0xFF


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def _pixel_stats_from_png_rows(rows: list[bytes], *, width: int, height: int, color_type: int, channels: int) -> dict[str, Any]:
    def iter_pixels() -> Any:
        for row in rows:
            for offset in range(0, len(row), channels):
                if color_type == 0:
                    value = row[offset]
                    yield value, value, value, 255
                elif color_type == 2:
                    yield row[offset], row[offset + 1], row[offset + 2], 255
                elif color_type == 4:
                    value = row[offset]
                    yield value, value, value, row[offset + 1]
                else:
                    yield row[offset], row[offset + 1], row[offset + 2], row[offset + 3]

    return _pixel_stats(iter_pixels(), pixel_count=width * height)


def _bmp_content_stats(data: bytes) -> dict[str, Any]:
    if len(data) < 54:
        return {"media_format": "bmp", "byte_size": len(data), "parse_status": "too_short"}
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40:
        return {"media_format": "bmp", "byte_size": len(data), "parse_status": "unsupported_dib_header"}
    width = struct.unpack_from("<i", data, 18)[0]
    signed_height = struct.unpack_from("<i", data, 22)[0]
    planes = struct.unpack_from("<H", data, 26)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    height = abs(signed_height)
    if planes != 1 or compression != 0 or bpp not in {24, 32} or width <= 0 or height <= 0:
        return {
            "media_format": "bmp",
            "byte_size": len(data),
            "parse_status": "unsupported_bmp_layout",
            "width": width,
            "height": height,
            "bits_per_pixel": bpp,
            "compression": compression,
        }
    row_stride = ((width * bpp + 31) // 32) * 4

    def iter_pixels() -> Any:
        for y in range(height):
            source_y = y if signed_height < 0 else height - y - 1
            row_offset = pixel_offset + source_y * row_stride
            for x in range(width):
                offset = row_offset + x * (bpp // 8)
                blue = data[offset]
                green = data[offset + 1]
                red = data[offset + 2]
                alpha = data[offset + 3] if bpp == 32 else 255
                yield red, green, blue, alpha

    return {
        "media_format": "bmp",
        "byte_size": len(data),
        "parse_status": "parsed",
        "width": width,
        "height": height,
        "bits_per_pixel": bpp,
        **_pixel_stats(iter_pixels(), pixel_count=width * height),
    }


def _pixel_stats(pixels: Any, *, pixel_count: int) -> dict[str, Any]:
    if pixel_count <= 0:
        return {"pixel_count": pixel_count, "parse_status": "empty_pixels"}
    min_luma = 255
    max_luma = 0
    min_rgb = [255, 255, 255]
    max_rgb = [0, 0, 0]
    blackish = 0
    transparent = 0
    unique: set[tuple[int, int, int, int]] = set()
    unique_limit_reached = False
    for red, green, blue, alpha in pixels:
        if len(unique) <= 512:
            unique.add((red, green, blue, alpha))
        else:
            unique_limit_reached = True
        luma = int((red * 299 + green * 587 + blue * 114) / 1000)
        min_luma = min(min_luma, luma)
        max_luma = max(max_luma, luma)
        min_rgb[0] = min(min_rgb[0], red)
        min_rgb[1] = min(min_rgb[1], green)
        min_rgb[2] = min(min_rgb[2], blue)
        max_rgb[0] = max(max_rgb[0], red)
        max_rgb[1] = max(max_rgb[1], green)
        max_rgb[2] = max(max_rgb[2], blue)
        if luma <= 2:
            blackish += 1
        if alpha <= 2:
            transparent += 1
    return {
        "pixel_count": pixel_count,
        "unique_color_count_capped": len(unique),
        "unique_color_limit_reached": unique_limit_reached,
        "luma_min": min_luma,
        "luma_max": max_luma,
        "luma_range": max_luma - min_luma,
        "rgb_min": min_rgb,
        "rgb_max": max_rgb,
        "rgb_range_max": max(max_rgb[index] - min_rgb[index] for index in range(3)),
        "blackish_pixel_ratio": blackish / pixel_count,
        "transparent_pixel_ratio": transparent / pixel_count,
    }


def _content_stats_degenerate(stats: dict[str, Any]) -> bool:
    return bool(_content_degenerate_reasons(stats))


def _content_degenerate_reasons(stats: dict[str, Any]) -> list[str]:
    if stats.get("parse_status") != "parsed":
        return []
    reasons: list[str] = []
    if stats.get("unique_color_count_capped", 0) <= 1 and not stats.get("unique_color_limit_reached"):
        reasons.append("single_color_frame")
    if int(stats.get("luma_range") or 0) <= 2:
        reasons.append("near_zero_luma_range")
    if int(stats.get("rgb_range_max") or 0) <= 2:
        reasons.append("near_zero_rgb_range")
    if float(stats.get("blackish_pixel_ratio") or 0.0) >= 0.995:
        reasons.append("near_all_black")
    if float(stats.get("transparent_pixel_ratio") or 0.0) >= 0.995:
        reasons.append("near_all_transparent")
    return reasons


def _calculator_click_plan(rect: dict[str, int] | None) -> list[dict[str, Any]]:
    if not rect:
        return []
    left = rect["left"]
    top = rect["top"]
    width = rect["right"] - rect["left"]
    height = rect["bottom"] - rect["top"]
    keypad_left = left + 10
    keypad_width = max(1, min(width - 20, int(height * 0.58)))
    grid_top = top + int(height * 0.37)
    grid_height = max(1, height - int(height * 0.37) - 12)
    col_width = keypad_width / 4
    row_height = grid_height / 6

    def point(label: str, col: int, row: int) -> dict[str, Any]:
        return {
            "label": label,
            "x": int(keypad_left + (col + 0.5) * col_width),
            "y": int(grid_top + (row + 0.5) * row_height),
            "col": col,
            "row": row,
            "window_rect": rect,
            "keypad_area": {
                "left": keypad_left,
                "top": grid_top,
                "width": keypad_width,
                "height": grid_height,
            },
            "coordinate_source": "calculator_standard_keypad_area_ratio_estimate",
            "requires_external_review": True,
        }

    return [
        point("clear", 2, 0),
        point("digit_2_first", 1, 4),
        point("plus", 3, 4),
        point("digit_2_second", 1, 4),
        point("equals", 3, 5),
    ]


def _region_from_window_rect(rect: dict[str, int] | None) -> dict[str, int] | None:
    if not rect:
        return None
    width = rect["right"] - rect["left"]
    height = rect["bottom"] - rect["top"]
    if width <= 0 or height <= 0:
        return None
    return {"x": max(0, rect["left"]), "y": max(0, rect["top"]), "width": width, "height": height}


def _arming_status(env: Mapping[str, str]) -> dict[str, Any]:
    armed = env.get(P0_ARMING_FLAG) == "armed" and env.get(ARMING_REF) and env.get(CONSENT_REF)
    return {
        "armed": bool(armed),
        "arming_ref": env.get(ARMING_REF) or "p0-arming-missing",
        "operator_consent_ref": env.get(CONSENT_REF) or "p0-consent-missing",
        "required_env_keys": [P0_ARMING_FLAG, ARMING_REF, CONSENT_REF],
        "present_env_keys": [key for key in [P0_ARMING_FLAG, ARMING_REF, CONSENT_REF] if env.get(key)],
    }


def _p0_safe_report_lines(tasks: list[dict[str, Any]], host_sent_event_count: int) -> list[str]:
    content = _p0_observation_content(tasks)
    return [
        f"P0 real-input smoke recorded host_sent_event_count={host_sent_event_count}.",
        "The report proves only host input event reporting, saved before/after images, evidence package, replay, and integrity facts.",
        f"Observation content guard: capture_content_degenerate={content['capture_content_degenerate']}; valid_reference_count={content['valid_reference_count']}.",
        "The tool does not assert input caused the visible change; a human or external visual AI must review the saved images.",
        "No OCR, clipboard, window semantics, DOM, accessibility, shell/cmd product control, or business-result evaluation was used as product capability.",
        "Task summaries: " + "; ".join(f"{task.get('task_name')}={task.get('task_status')}" for task in tasks),
    ]


def _observation_blocking_diagnosis(tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    content = _p0_observation_content(tasks)
    if not content["capture_content_degenerate"]:
        return None
    return {
        "blocking_condition": "observation_content_degenerate",
        "failure_code": "CAPTURE_CONTENT_DEGENERATE",
        "failure_detail": "At least one captured reference frame is blank, near-monochrome, or otherwise too low-information to serve as visual evidence.",
        "observation_content": content,
        "suggested_next": [
            "run_the_smoke_from_a_real_interactive_window_station_or_host_agent",
            "do_not_treat_evidence_chain_complete_as_visual_content_success",
            "do_not_continue_to_input_success_claims_until_reference_frames_are_non_degenerate",
        ],
    }


def _foreground_blocking_diagnosis(tasks: list[dict[str, Any]], environment: dict[str, Any]) -> dict[str, Any] | None:
    action_reports = [report for task in tasks for report in task.get("action_reports", [])]
    foreground_reports = [report for report in action_reports if report.get("failure_code") == "TARGET_WINDOW_NOT_FOREGROUND"]
    if not foreground_reports:
        return None
    return {
        "blocking_condition": "target_window_not_foreground_no_host_input",
        "failure_code": "TARGET_WINDOW_NOT_FOREGROUND",
        "failure_detail": "The target window was not foreground after focus attempts, so the smoke did not send keyboard or mouse input.",
        "foreground_window": environment.get("foreground_window"),
        "input_desktop": environment.get("input_desktop"),
        "suggested_next": [
            "run_inside_a_visible_interactive_user_session_or_resident_host_agent",
            "inspect_focus_result_and_foreground_window_after_focus",
            "do_not_send_real_input_until_foreground_match_is_true",
        ],
    }


def _input_blocking_diagnosis(tasks: list[dict[str, Any]], environment: dict[str, Any]) -> dict[str, Any] | None:
    action_reports = [report for task in tasks for report in task.get("action_reports", [])]
    if not action_reports:
        return None
    access_denied_reports = [
        report
        for report in action_reports
        if report.get("failure_code") == "INPUT_EXECUTION_FAILED"
        and "last_error=5" in str(report.get("failure_detail", ""))
    ]
    if len(access_denied_reports) != len(action_reports):
        return None
    return {
        "blocking_condition": "host_input_injection_blocked_access_denied",
        "failure_code": "HOST_INPUT_ACCESS_DENIED",
        "failure_detail": "Every attempted real input action failed with host API last_error=5.",
        "foreground_window": environment.get("foreground_window"),
        "input_desktop": environment.get("input_desktop"),
        "window_station": environment.get("window_station"),
        "session": environment.get("session"),
        "current_process_integrity": environment.get("current_process_integrity"),
        "task_foreground_states": [
            {
                "task_name": task.get("task_name"),
                "foreground_match": task.get("setup", {}).get("foreground_match"),
                "focus_result": task.get("setup", {}).get("focus_result"),
            }
            for task in tasks
        ],
        "suggested_next": [
            "run_real_input_through_a_resident_ai_control_host_agent_in_the_visible_interactive_user_session",
            "treat_manual_interactive_powershell_only_as_a_temporary_diagnostic_substitute_not_the_product_path",
            "do_not_retry_by_guessing_coordinates_until_host_input_sent_is_positive",
        ],
    }


def _p0_observation_content(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    task_contents = [_task_observation_content(task.get("action_reports", [])) for task in tasks]
    return {
        "capture_content_checked": any(item["capture_content_checked"] for item in task_contents),
        "capture_content_degenerate": any(item["capture_content_degenerate"] for item in task_contents),
        "checked_reference_count": sum(item["checked_reference_count"] for item in task_contents),
        "valid_reference_count": sum(item["valid_reference_count"] for item in task_contents),
        "degenerate_reference_count": sum(item["degenerate_reference_count"] for item in task_contents),
        "unknown_reference_count": sum(item["unknown_reference_count"] for item in task_contents),
    }


def _task_observation_content(action_reports: list[dict[str, Any]]) -> dict[str, Any]:
    references = []
    seen_paths: set[str] = set()
    for report in action_reports:
        for key in ("before_media_content", "after_media_content", "external_review_after_media_content"):
            content = report.get(key)
            if isinstance(content, dict) and content.get("content_checked"):
                path = str(content.get("media_path_abs") or f"{id(content)}")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                references.append(content)
    degenerate = [item for item in references if item.get("capture_content_degenerate")]
    valid = [item for item in references if item.get("capture_content_valid")]
    unknown = [item for item in references if not item.get("capture_content_valid") and not item.get("capture_content_degenerate")]
    return {
        "capture_content_checked": bool(references),
        "capture_content_degenerate": bool(degenerate),
        "checked_reference_count": len(references),
        "valid_reference_count": len(valid),
        "degenerate_reference_count": len(degenerate),
        "unknown_reference_count": len(unknown),
    }


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WindowsHostProbe:
    def __init__(self) -> None:
        self._user32 = None

    def ensure_dpi_awareness(self) -> dict[str, Any]:
        if not _is_windows():
            return {"platform_supported": False, "attempted": False}
        attempts: list[dict[str, Any]] = []
        try:
            user32 = self.user32
            if hasattr(user32, "SetProcessDpiAwarenessContext"):
                ok = bool(user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)))
                attempts.append({"api": "SetProcessDpiAwarenessContext", "value": "PER_MONITOR_AWARE_V2", "ok": ok})
                if ok:
                    return {"platform_supported": True, "attempted": True, "attempts": attempts, "selected": attempts[-1]}
        except Exception as exc:
            attempts.append({"api": "SetProcessDpiAwarenessContext", "ok": False, "error": str(exc)})
        try:
            shcore = ctypes.WinDLL("shcore", use_last_error=True)
            ok = shcore.SetProcessDpiAwareness(2) == 0
            attempts.append({"api": "SetProcessDpiAwareness", "value": "PROCESS_PER_MONITOR_DPI_AWARE", "ok": ok})
            if ok:
                return {"platform_supported": True, "attempted": True, "attempts": attempts, "selected": attempts[-1]}
        except Exception as exc:
            attempts.append({"api": "SetProcessDpiAwareness", "ok": False, "error": str(exc)})
        try:
            ok = bool(self.user32.SetProcessDPIAware())
            attempts.append({"api": "SetProcessDPIAware", "ok": ok})
        except Exception as exc:
            attempts.append({"api": "SetProcessDPIAware", "ok": False, "error": str(exc)})
        return {"platform_supported": True, "attempted": True, "attempts": attempts, "selected": next((item for item in attempts if item.get("ok")), None)}

    def dpi_awareness_snapshot(self) -> dict[str, Any]:
        return {
            "system_metrics": self.system_metrics(),
            "thread_dpi_awareness_context": self._thread_dpi_awareness_context(),
            "system_dpi": self._system_dpi(),
        }

    def ensure_input_desktop(self) -> dict[str, Any]:
        if not _is_windows():
            return {"platform_supported": False, "attempted": False}
        access = 0x0001 | 0x0020 | 0x0040 | 0x0080 | 0x0100
        try:
            user32 = self.user32
            user32.OpenInputDesktop.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            user32.OpenInputDesktop.restype = wintypes.HANDLE
            desktop = user32.OpenInputDesktop(0, False, access)
            if not desktop:
                last_error = ctypes.get_last_error()
                return {
                    "platform_supported": True,
                    "attempted": True,
                    "opened": False,
                    "last_error": last_error,
                    "error_text": ctypes.FormatError(last_error).strip() if last_error else "no extended error",
                }
            name = self._user_object_name(desktop)
            user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
            user32.SetThreadDesktop.restype = wintypes.BOOL
            set_ok = bool(user32.SetThreadDesktop(desktop))
            last_error = ctypes.get_last_error()
            return {
                "platform_supported": True,
                "attempted": True,
                "opened": True,
                "desktop_name": name,
                "requested_access_mask": access,
                "requested_access": [
                    "DESKTOP_READOBJECTS",
                    "DESKTOP_JOURNALPLAYBACK",
                    "DESKTOP_ENUMERATE",
                    "DESKTOP_WRITEOBJECTS",
                    "DESKTOP_SWITCHDESKTOP",
                ],
                "set_thread_desktop_ok": set_ok,
                "last_error": last_error if not set_ok else 0,
                "error_text": ctypes.FormatError(last_error).strip() if (last_error and not set_ok) else None,
                "foreground_after_attach": self.foreground_window_info(),
            }
        except Exception as exc:
            return {"platform_supported": True, "attempted": True, "opened": False, "error": str(exc)}

    def environment_report(self, *, dpi_awareness: dict[str, Any], input_desktop: dict[str, Any]) -> dict[str, Any]:
        return {
            "platform": {
                "system": _platform_system_label(),
                "platform": sys.platform,
                "version": platform.version(),
                "release": platform.release(),
                "machine": platform.machine(),
            },
            "python": {
                "executable": sys.executable,
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
            },
            "current_process_integrity": self.process_integrity(),
            "session": self.session_report(),
            "window_station": self.window_station_report(),
            "dpi_awareness": dpi_awareness,
            "input_desktop": input_desktop,
            "virtual_screen_metrics": self.system_metrics(),
            "foreground_window": self.foreground_window_info(),
        }

    def launch_and_focus(
        self,
        executable: str,
        *,
        title_contains: list[str],
        launch_app: bool,
        launch_args: list[str] | None = None,
    ) -> dict[str, Any]:
        if not _is_windows():
            return {"setup_status": "unsupported_platform", "launch_executable": executable}
        process_id = None
        launch_error = None
        if launch_app:
            try:
                process = Popen([executable, *(launch_args or [])])
                process_id = process.pid
            except Exception as exc:
                launch_error = str(exc)
        time.sleep(1.2)
        window = self.find_window(process_id=process_id, title_contains=title_contains)
        focus_result = None
        if window.get("hwnd"):
            focus_result = self.focus_window(int(window["hwnd"]))
            time.sleep(0.3)
        foreground = self.foreground_window_info()
        foreground_match = bool(window.get("hwnd") and foreground.get("hwnd") == window.get("hwnd"))
        return {
            "setup_status": "focused" if foreground_match else "launched_or_found_not_foreground",
            "launch_executable": executable,
            "launch_args": launch_args or [],
            "launch_app": launch_app,
            "process_id": process_id,
            "launch_error": launch_error,
            "window": window,
            "window_rect": window.get("rect"),
            "focus_result": focus_result,
            "foreground_window_after_focus": foreground,
            "foreground_match": foreground_match,
            "tool_uses_window_info_for_smoke_setup_only": True,
        }

    def find_window(self, *, process_id: int | None, title_contains: list[str]) -> dict[str, Any]:
        process_matches: list[dict[str, Any]] = []
        title_matches: list[dict[str, Any]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def enum_proc(hwnd: int, _lparam: int) -> bool:
            if not self.user32.IsWindowVisible(hwnd):
                return True
            title = self._window_title(hwnd)
            if not title:
                return True
            pid = self._window_pid(hwnd)
            if process_id and pid == process_id:
                process_matches.append(self._window_info(hwnd))
            elif any(part.lower() in title.lower() for part in title_contains):
                title_matches.append(self._window_info(hwnd))
            return True

        self.user32.EnumWindows(enum_proc, 0)
        if process_matches:
            return process_matches[0]
        if title_matches:
            return title_matches[0]
        return {"hwnd": None, "title_contains": title_contains, "process_id": process_id, "matches": []}

    def focus_window(self, hwnd: int) -> dict[str, Any]:
        user32 = self.user32
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = (wintypes.HWND,)
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.SetFocus.argtypes = (wintypes.HWND,)
        user32.SetFocus.restype = wintypes.HWND
        user32.SetActiveWindow.argtypes = (wintypes.HWND,)
        user32.SetActiveWindow.restype = wintypes.HWND
        user32.SetWindowPos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL
        if hasattr(user32, "SwitchToThisWindow"):
            user32.SwitchToThisWindow.argtypes = (wintypes.HWND, wintypes.BOOL)
            user32.SwitchToThisWindow.restype = None
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        target_pid = wintypes.DWORD()
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, ctypes.byref(target_pid)))
        foreground_hwnd = user32.GetForegroundWindow()
        foreground_pid = wintypes.DWORD()
        foreground_thread = int(user32.GetWindowThreadProcessId(foreground_hwnd, ctypes.byref(foreground_pid))) if foreground_hwnd else 0
        current_thread = int(kernel32.GetCurrentThreadId())
        attached_target = False
        attached_foreground = False
        try:
            if target_thread and target_thread != current_thread:
                attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
            if foreground_thread and foreground_thread not in {current_thread, target_thread}:
                attached_foreground = bool(user32.AttachThreadInput(current_thread, foreground_thread, True))
            ok_show = bool(user32.ShowWindow(hwnd, 9))
            ok_bring = bool(user32.BringWindowToTop(hwnd))
            active_hwnd = user32.SetActiveWindow(hwnd)
            focus_hwnd = user32.SetFocus(hwnd)
            ok_foreground = bool(user32.SetForegroundWindow(hwnd))
            switch_attempted = hasattr(user32, "SwitchToThisWindow")
            if switch_attempted:
                user32.SwitchToThisWindow(hwnd, True)
                time.sleep(0.05)
            swp_no_size = 0x0001
            swp_no_move = 0x0002
            swp_show_window = 0x0040
            ok_topmost = bool(user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, swp_no_move | swp_no_size | swp_show_window))
            ok_not_topmost = bool(user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, swp_no_move | swp_no_size | swp_show_window))
            ok_foreground_after_switch = bool(user32.SetForegroundWindow(hwnd))
            final_foreground = self.foreground_window_info()
            return {
                "ShowWindow": ok_show,
                "BringWindowToTop": ok_bring,
                "SetActiveWindow": bool(active_hwnd),
                "SetFocus": bool(focus_hwnd),
                "SetForegroundWindow": ok_foreground,
                "SwitchToThisWindow": switch_attempted,
                "SetWindowPosTopmost": ok_topmost,
                "SetWindowPosNoTopmost": ok_not_topmost,
                "SetForegroundWindowAfterSwitch": ok_foreground_after_switch,
                "AttachThreadInput_target": attached_target,
                "AttachThreadInput_foreground": attached_foreground,
                "current_thread_id": current_thread,
                "target_thread_id": target_thread,
                "target_pid": int(target_pid.value),
                "foreground_thread_id_before": foreground_thread,
                "foreground_hwnd_before": int(foreground_hwnd) if foreground_hwnd else None,
                "foreground_window_after_attach_focus": final_foreground,
                "foreground_match_after_attach_focus": final_foreground.get("hwnd") == int(hwnd),
            }
        finally:
            if attached_foreground:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)

    def foreground_window_info(self) -> dict[str, Any]:
        if not _is_windows():
            return {"platform_supported": False}
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return {"hwnd": None}
        return self._window_info(hwnd)

    def system_metrics(self) -> dict[str, int] | dict[str, bool]:
        if not _is_windows():
            return {"platform_supported": False}
        metrics = {
            "screen_width": 0,
            "screen_height": 1,
            "virtual_screen_x": 76,
            "virtual_screen_y": 77,
            "virtual_screen_width": 78,
            "virtual_screen_height": 79,
        }
        return {key: int(self.user32.GetSystemMetrics(index)) for key, index in metrics.items()}

    def session_report(self) -> dict[str, Any]:
        if not _is_windows():
            return {"platform_supported": False}
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.ProcessIdToSessionId.argtypes = (wintypes.DWORD, ctypes.POINTER(wintypes.DWORD))
            kernel32.ProcessIdToSessionId.restype = wintypes.BOOL
            kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
            session_id = wintypes.DWORD()
            ok = bool(kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)))
            active_console = int(kernel32.WTSGetActiveConsoleSessionId())
            process_session_id = int(session_id.value) if ok else None
            connect_state = self._wts_connect_state(process_session_id)
            return {
                "platform_supported": True,
                "process_id": os.getpid(),
                "query_ok": ok,
                "process_session_id": process_session_id,
                "active_console_session_id": active_console,
                "process_is_active_console_session": bool(ok and process_session_id == active_console),
                "process_session_connect_state": connect_state,
                "process_session_is_wts_active": connect_state.get("state_name") == "WTSActive",
            }
        except Exception as exc:
            return {"platform_supported": True, "query_ok": False, "error": str(exc)}

    def _wts_connect_state(self, session_id: int | None) -> dict[str, Any]:
        if session_id is None:
            return {"query_ok": False, "error": "session_id_unavailable"}
        states = {
            0: "WTSActive",
            1: "WTSConnected",
            2: "WTSConnectQuery",
            3: "WTSShadow",
            4: "WTSDisconnected",
            5: "WTSIdle",
            6: "WTSListen",
            7: "WTSReset",
            8: "WTSDown",
            9: "WTSInit",
        }
        try:
            wtsapi32 = ctypes.WinDLL("wtsapi32", use_last_error=True)
            buffer = ctypes.c_void_p()
            bytes_returned = wintypes.DWORD()
            wtsapi32.WTSQuerySessionInformationW.argtypes = (
                wintypes.HANDLE,
                wintypes.DWORD,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.DWORD),
            )
            wtsapi32.WTSQuerySessionInformationW.restype = wintypes.BOOL
            wtsapi32.WTSFreeMemory.argtypes = (ctypes.c_void_p,)
            ok = bool(wtsapi32.WTSQuerySessionInformationW(None, session_id, 8, ctypes.byref(buffer), ctypes.byref(bytes_returned)))
            if not ok:
                last_error = ctypes.get_last_error()
                return {
                    "query_ok": False,
                    "last_error": last_error,
                    "error_text": ctypes.FormatError(last_error).strip() if last_error else "no extended error",
                }
            try:
                state_value = int(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_int)).contents.value)
                return {
                    "query_ok": True,
                    "state": state_value,
                    "state_name": states.get(state_value, f"unknown_{state_value}"),
                }
            finally:
                wtsapi32.WTSFreeMemory(buffer)
        except Exception as exc:
            return {"query_ok": False, "error": str(exc)}

    def window_station_report(self) -> dict[str, Any]:
        if not _is_windows():
            return {"platform_supported": False}
        try:
            self.user32.GetProcessWindowStation.restype = wintypes.HANDLE
            station = self.user32.GetProcessWindowStation()
            return {
                "platform_supported": True,
                "query_ok": bool(station),
                "window_station_name": self._user_object_name(station) if station else None,
            }
        except Exception as exc:
            return {"platform_supported": True, "query_ok": False, "error": str(exc)}

    @property
    def user32(self) -> Any:
        if self._user32 is None:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        return self._user32

    def _window_info(self, hwnd: int) -> dict[str, Any]:
        return {
            "hwnd": int(hwnd),
            "title": self._window_title(hwnd),
            "pid": self._window_pid(hwnd),
            "rect": self._window_rect(hwnd),
            "dpi_for_window": self._dpi_for_window(hwnd),
            "process_integrity": self.process_integrity(self._window_pid(hwnd)),
        }

    def _window_title(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _window_pid(self, hwnd: int) -> int:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def _window_rect(self, hwnd: int) -> dict[str, int] | None:
        rect = wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return {"left": int(rect.left), "top": int(rect.top), "right": int(rect.right), "bottom": int(rect.bottom)}

    def _dpi_for_window(self, hwnd: int) -> int | None:
        try:
            return int(self.user32.GetDpiForWindow(hwnd))
        except Exception:
            return None

    def _system_dpi(self) -> int | None:
        try:
            return int(self.user32.GetDpiForSystem())
        except Exception:
            return None

    def _thread_dpi_awareness_context(self) -> str | None:
        try:
            return str(self.user32.GetThreadDpiAwarenessContext())
        except Exception:
            return None

    def process_integrity(self, pid: int | None = None) -> dict[str, Any]:
        if not _is_windows():
            return {"platform_supported": False}
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        TOKEN_QUERY = 0x0008
        TOKEN_INTEGRITY_LEVEL = 25
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            query_pid = os.getpid() if pid is None else pid
            kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel32.OpenProcess.restype = wintypes.HANDLE
            process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, query_pid)
            close_process = True
            if not process:
                last_error = ctypes.get_last_error()
                return {
                    "pid": query_pid,
                    "query_ok": False,
                    "last_error": last_error,
                        "error_text": ctypes.FormatError(last_error).strip() if last_error else None,
                    }
            process_handle = ctypes.c_void_p(process if isinstance(process, int) else process.value)
            token = wintypes.HANDLE()
            advapi32.OpenProcessToken.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
            advapi32.OpenProcessToken.restype = wintypes.BOOL
            if not advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token)):
                last_error = ctypes.get_last_error()
                return {
                        "pid": query_pid,
                    "query_ok": False,
                    "last_error": last_error,
                        "error_text": ctypes.FormatError(last_error).strip() if last_error else None,
                    }
            token_handle = ctypes.c_void_p(token.value)
            needed = wintypes.DWORD()
            advapi32.GetTokenInformation.argtypes = (
                ctypes.c_void_p,
                wintypes.DWORD,
                wintypes.LPVOID,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            )
            advapi32.GetTokenInformation.restype = wintypes.BOOL
            advapi32.GetTokenInformation(token_handle, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(token_handle, TOKEN_INTEGRITY_LEVEL, buffer, needed.value, ctypes.byref(needed)):
                last_error = ctypes.get_last_error()
                return {
                    "pid": query_pid,
                    "query_ok": False,
                    "last_error": last_error,
                    "error_text": ctypes.FormatError(last_error).strip() if last_error else None,
                }
            label = ctypes.cast(buffer, ctypes.POINTER(SidAndAttributes)).contents
            sid = label.Sid
            advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
            advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
            count = advapi32.GetSidSubAuthorityCount(sid).contents.value
            advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
            advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
            rid = advapi32.GetSidSubAuthority(sid, count - 1).contents.value
            return {"pid": query_pid, "query_ok": True, "rid": int(rid), "level": _integrity_level_name(int(rid))}
        except Exception as exc:
            return {"pid": os.getpid() if pid is None else pid, "query_ok": False, "error": str(exc)}

    def _user_object_name(self, handle: int) -> str | None:
        try:
            user32 = self.user32
            needed = wintypes.DWORD()
            user32.GetUserObjectInformationW(handle, 2, None, 0, ctypes.byref(needed))
            if needed.value <= 0:
                return None
            buffer = ctypes.create_unicode_buffer(max(1, needed.value // ctypes.sizeof(ctypes.c_wchar)))
            ok = user32.GetUserObjectInformationW(handle, 2, buffer, needed.value, ctypes.byref(needed))
            return buffer.value if ok else None
        except Exception:
            return None


class SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


def _integrity_level_name(rid: int) -> str:
    if rid >= 0x4000:
        return "system"
    if rid >= 0x3000:
        return "high"
    if rid >= 0x2000:
        return "medium"
    if rid >= 0x1000:
        return "low"
    return "untrusted"
