from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ai_control.adapters.mcp import MCPStdioAdapter
from ai_control.channels.key_text import key_text_summary, validate_key_text_stream
from ai_control.channels.keyboard_events import is_keyboard_action, keyboard_action_summary
from ai_control.channels.windows_software import WindowsSoftwareInputChannel
from ai_control.channels.windows_software.sendinput_backend import Win32InputBackend


DEFAULT_INPUT_CHANNEL_REF = "windows_software_input"
DEFAULT_OBSERVATION_CHANNEL_REF = "windows_software_observation"


def build_manual_windows_input_adapter(
    *,
    runs_dir: str | Path,
    arming_ref: str,
    operator_consent_ref: str,
    backend: Any | None = None,
    observation_channels: list[Any] | None = None,
    default_observation_channel_ref: str | None = None,
) -> MCPStdioAdapter:
    input_channel = WindowsSoftwareInputChannel(
        enabled=True,
        backend=backend or Win32InputBackend(),
        name=DEFAULT_INPUT_CHANNEL_REF,
        active_arming_ref=arming_ref,
        operator_consent_ref=operator_consent_ref,
    )
    kwargs: dict[str, Any] = {}
    if observation_channels is not None:
        kwargs["observation_channels"] = observation_channels
    if default_observation_channel_ref is not None:
        kwargs["default_observation_channel_ref"] = default_observation_channel_ref
    return MCPStdioAdapter(
        runs_dir=runs_dir,
        input_channels=[input_channel],
        default_input_channel_ref=input_channel.name,
        **kwargs,
    )


def run_manual_input_smoke(
    adapter: MCPStdioAdapter,
    *,
    runs_dir: str | Path,
    arming_ref: str,
    operator_consent_ref: str,
    action: dict[str, Any] | None = None,
    observation_channel_ref: str = DEFAULT_OBSERVATION_CHANNEL_REF,
    observation_request: dict[str, Any] | None = None,
    post_action_capture_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    transcript: list[dict[str, Any]] = []
    requested_action = action or {"input_type": "wait", "duration_ms": 1}
    action_payload = requested_action

    capabilities = adapter.call_tool("get_capabilities", {"probe_mode": "passive"})
    transcript.append(_compact_response("get_capabilities_passive_probe", capabilities))

    before_payload = dict(observation_request or {"mode": "fullscreen"})
    before_payload["channel_ref"] = observation_channel_ref
    before = adapter.call_tool("observe", before_payload)
    transcript.append(_compact_response("observe_before", before))
    if not before.get("ok"):
        return _report(
            "manual_input_observation_not_ready",
            3,
            adapter=adapter,
            runs_dir=runs_dir,
            started_at=started_at,
            observation_channel_ref=observation_channel_ref,
            action=action_payload,
            requested_action=requested_action,
            capabilities=capabilities,
            execute=None,
            package=None,
            replay=None,
            integrity=None,
            transcript=transcript,
            recommended_next="run_capture_smoke_or_select_available_real_observation_channel",
        )

    candidate_selection: dict[str, Any] | None = None
    if requested_action.get("input_type") == "candidate_click":
        action_payload, candidate_selection = _candidate_click_action(adapter, before, requested_action, transcript)
        if not action_payload:
            package, replay, integrity = _read_evidence(adapter, transcript)
            return _report(
                "manual_input_candidate_failed",
                4,
                adapter=adapter,
                runs_dir=runs_dir,
                started_at=started_at,
                observation_channel_ref=observation_channel_ref,
                action={"input_type": "candidate_click"},
                requested_action=requested_action,
                capabilities=capabilities,
                before=before,
                execute=None,
                package=package,
                replay=replay,
                integrity=integrity,
                transcript=transcript,
                recommended_next="inspect_candidate_generation_or_choose_explicit_coordinates",
                candidate_selection=candidate_selection,
            )

    lease = adapter.call_tool(
        "create_lease",
        {
            "duration_ms": 10_000,
            "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
            "arming_ref": arming_ref,
            "operator_consent_ref": operator_consent_ref,
            "before_observation_ref": before["data"]["observation_id"],
            "after_observation_channel_ref": observation_channel_ref,
            "budget": {"max_input_events": 1},
        },
    )
    transcript.append(_compact_response("create_lease", lease))
    if not lease.get("ok"):
        package, replay, integrity = _read_evidence(adapter, transcript)
        return _report(
            "manual_input_lease_failed",
            4,
            adapter=adapter,
            runs_dir=runs_dir,
            started_at=started_at,
            observation_channel_ref=observation_channel_ref,
            action=action_payload,
            requested_action=requested_action,
            capabilities=capabilities,
            before=before,
            execute=None,
            package=package,
            replay=replay,
            integrity=integrity,
            transcript=transcript,
            recommended_next="check_arming_consent_and_real_observation_refs",
            candidate_selection=candidate_selection,
        )

    execute_payload = {"lease_id": lease["data"]["lease_id"], **action_payload}
    execute = adapter.call_tool("execute_input", execute_payload)
    transcript.append(_compact_response("execute_input", execute))
    post_action_sequence: dict[str, Any] | None = None
    post_action_sequence_skipped_reason: str | None = None
    if execute.get("ok") and _should_capture_post_action_sequence(requested_action, execute, post_action_capture_policy):
        post_action_sequence = _post_action_sequence(
            adapter,
            before,
            observation_channel_ref,
            transcript,
            policy=post_action_capture_policy,
        )
    elif execute.get("ok") and _post_sequence_candidate_action(requested_action):
        if post_action_capture_policy and post_action_capture_policy.get("enabled") is False:
            post_action_sequence_skipped_reason = "post_action_capture_disabled"
        else:
            post_action_sequence_skipped_reason = "inputs_not_confirmed_released"
    package, replay, integrity = _read_evidence(adapter, transcript)
    integrity_ok = bool(integrity.get("ok") and integrity.get("data", {}).get("ok"))
    if execute.get("ok") and integrity_ok:
        smoke_status = "manual_input_succeeded"
        exit_code = 0
        recommended_next = "manual_real_input_path_is_ready_for_authorized_operator_smoke"
    elif execute.get("ok"):
        smoke_status = "manual_input_integrity_failed"
        exit_code = 6
        recommended_next = "inspect_evidence_store"
    else:
        smoke_status = "manual_input_failed"
        exit_code = 5
        recommended_next = "inspect_failure_and_backend_status"

    return _report(
        smoke_status,
        exit_code,
        adapter=adapter,
        runs_dir=runs_dir,
        started_at=started_at,
        observation_channel_ref=observation_channel_ref,
        action=action_payload,
        requested_action=requested_action,
        capabilities=capabilities,
        before=before,
        execute=execute,
        post_action_sequence=post_action_sequence,
        post_action_sequence_skipped_reason=post_action_sequence_skipped_reason,
        post_action_capture_policy=post_action_capture_policy,
        package=package,
        replay=replay,
        integrity=integrity,
        transcript=transcript,
        recommended_next=recommended_next,
        candidate_selection=candidate_selection,
    )


def _candidate_click_action(
    adapter: MCPStdioAdapter,
    before: dict[str, Any],
    requested_action: dict[str, Any],
    transcript: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    observation_ref = before.get("data", {}).get("observation_id")
    candidates = adapter.call_tool("derive_candidates", {"observation_ref": observation_ref})
    transcript.append(_compact_response("derive_candidates", candidates))
    if not candidates.get("ok"):
        return None, {
            "selection_status": "candidate_generation_failed",
            "failure_code": candidates.get("failure", {}).get("failure_code"),
            "failure_detail": candidates.get("failure", {}).get("detail"),
            "source_observation_ref": observation_ref,
        }
    candidate_list = candidates.get("data", {}).get("candidates", [])
    selected = next((item for item in candidate_list if item.get("candidate_kind") == "geometry_center"), None)
    if not selected or not isinstance(selected.get("click_point"), dict):
        return None, {
            "selection_status": "no_geometry_center_candidate",
            "candidate_method": candidates.get("data", {}).get("candidate_method"),
            "candidate_count": len(candidate_list),
            "source_observation_ref": observation_ref,
        }
    point = selected["click_point"]
    region = selected.get("screen_region")
    if selected.get("source_observation_ref") != observation_ref:
        return None, {
            "selection_status": "candidate_source_mismatch",
            "candidate_method": candidates.get("data", {}).get("candidate_method"),
            "candidate_id": selected.get("candidate_id"),
            "source_observation_ref": observation_ref,
            "candidate_source_observation_ref": selected.get("source_observation_ref"),
        }
    if not _point_inside_region(point, region):
        return None, {
            "selection_status": "candidate_click_point_out_of_region",
            "candidate_method": candidates.get("data", {}).get("candidate_method"),
            "candidate_id": selected.get("candidate_id"),
            "source_observation_ref": observation_ref,
            "click_point": point,
            "screen_region": region,
        }
    button = requested_action.get("button", "left")
    action_payload = {"input_type": "mouse_click", "x": point["x"], "y": point["y"], "button": button}
    selection = {
        "selection_status": "selected",
        "candidate_method": candidates.get("data", {}).get("candidate_method"),
        "candidate_count": len(candidate_list),
        "candidate_id": selected.get("candidate_id"),
        "candidate_kind": selected.get("candidate_kind"),
        "source_observation_ref": selected.get("source_observation_ref"),
        "source_channel_ref": selected.get("source_channel_ref"),
        "source_real_capture": selected.get("source_real_capture"),
        "source_media_ref": selected.get("source_media_ref"),
        "source_media_sha256": selected.get("source_media_sha256"),
        "click_point": point,
        "candidate_geometry_only": True,
        "semantics_used": selected.get("semantics_used"),
        "ocr_used": selected.get("ocr_used"),
        "window_semantics_used": selected.get("window_semantics_used"),
    }
    return action_payload, selection


def _point_inside_region(point: dict[str, Any], region: Any) -> bool:
    if not isinstance(region, dict):
        return False
    try:
        x = int(point["x"])
        y = int(point["y"])
        left = int(region["x"])
        top = int(region["y"])
        width = int(region["width"])
        height = int(region["height"])
    except (KeyError, TypeError, ValueError):
        return False
    return width > 0 and height > 0 and left <= x < left + width and top <= y < top + height


def _post_action_sequence(
    adapter: MCPStdioAdapter,
    before: dict[str, Any],
    observation_channel_ref: str,
    transcript: list[dict[str, Any]],
    *,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    before_data = before.get("data", {})
    active_policy = _effective_post_action_capture_policy(before_data, policy)
    delay_ms = int(active_policy.get("delay_ms", 0) or 0)
    if delay_ms > 0:
        time.sleep(delay_ms / 1000)
    sequence_payload: dict[str, Any] = {
        "mode": "sequence",
        "channel_ref": observation_channel_ref,
        "frame_count": active_policy["frame_count"],
        "interval_ms": active_policy["interval_ms"],
        "baseline_observation_ref": before_data.get("observation_id"),
    }
    region = active_policy.get("region")
    if isinstance(region, dict):
        sequence_payload["region"] = region
    sequence = adapter.call_tool("observe", sequence_payload)
    transcript.append(_compact_response("post_action_sequence", sequence))
    return sequence


def _effective_post_action_capture_policy(before_data: dict[str, Any], policy: dict[str, Any] | None) -> dict[str, Any]:
    active = dict(policy or {})
    active.setdefault("enabled", True)
    active.setdefault("frame_count", 2)
    active.setdefault("interval_ms", 100)
    active.setdefault("delay_ms", 0)
    active.setdefault("duration_ms", 0)
    active.setdefault("effective_duration_ms", (int(active["frame_count"]) - 1) * int(active["interval_ms"]))
    active.setdefault("duration_derivation", "default_frame_count")
    active.setdefault("media_kind", "gif")
    active.setdefault("video_requested", False)
    active.setdefault("video_supported", False)
    active.setdefault("min_frame_count", 2)
    active.setdefault("max_frame_count", 5)
    active.setdefault("min_interval_ms", 50)
    active.setdefault("max_interval_ms", 250)
    active.setdefault("max_total_capture_duration_ms", 1000)
    active.setdefault("max_delay_ms", 5000)
    active.setdefault("continuous_recording", False)
    active.setdefault("visualization_only", True)
    active.setdefault("application_effect_unverified", True)
    active.setdefault("schema", "manual_post_action_capture_policy_v1")
    active.setdefault("requested", {"enabled": active.get("enabled", True), "mode": active.get("mode", "sequence")})
    if "region" not in active:
        region = before_data.get("screen_region") or before_data.get("region")
        if isinstance(region, dict):
            active["region"] = region
    return active


def _post_action_capture_policy_requested(policy: dict[str, Any]) -> dict[str, Any]:
    requested = policy.get("requested")
    if isinstance(requested, dict):
        return dict(requested)
    return {"enabled": policy.get("enabled", True), "mode": policy.get("mode", "sequence")}


def _post_action_capture_policy_effective(policy: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in policy.items() if key != "requested"}


def _sequence_frame_media_refs(sequence_data: dict[str, Any]) -> list[dict[str, Any]]:
    media_refs = sequence_data.get("media_refs")
    if isinstance(media_refs, list):
        return [item for item in media_refs if isinstance(item, dict) and item.get("media_ref")]

    frames = sequence_data.get("frames")
    refs: list[dict[str, Any]] = []
    if isinstance(frames, list):
        for frame in frames:
            if not isinstance(frame, dict) or not frame.get("media_ref"):
                continue
            refs.append(
                {
                    "frame_ref": frame.get("observation_id"),
                    "media_ref": frame.get("media_ref"),
                    "media_sha256": frame.get("media_sha256"),
                    "media_size_bytes": frame.get("media_size_bytes"),
                }
            )
    return refs


def _post_sequence_candidate_action(requested_action: dict[str, Any]) -> bool:
    return requested_action.get("input_type") in {
        "candidate_click",
        "key_text_stream",
        "key_press",
        "key_chord",
        "key_down",
        "key_up",
    }


def _should_capture_post_action_sequence(
    requested_action: dict[str, Any],
    execute: dict[str, Any],
    policy: dict[str, Any] | None = None,
) -> bool:
    if not _post_sequence_candidate_action(requested_action):
        return False
    if policy and policy.get("enabled") is False:
        return False
    data = execute.get("data", {}) if execute.get("ok") else {}
    return data.get("released_inputs") is not False


def not_armed_report(*, runs_dir: str | Path, env_keys_present: list[str]) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = {
        "object_type": "ManualInputSmokeReport",
        "smoke_status": "manual_input_not_armed",
        "exit_code": 2,
        "result_attribution": "host_not_armed",
        "recommended_next": "provide_host_arming_environment_and_rerun",
        "suggested_next": ["provide_host_arming_environment_and_rerun"],
        "started_at": now,
        "ended_at": now,
        "session_id": None,
        "runs_dir": str(runs_dir),
        "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
        "observation_channel_ref": DEFAULT_OBSERVATION_CHANNEL_REF,
        "manual_arming_required": True,
        "env_keys_present": env_keys_present,
        "input_executed": False,
        "host_input_executed": False,
        "install_executed": False,
        "background_action_executed": False,
        "evidence_package_ok": False,
        "replay_read_only": False,
        "integrity_ok": False,
        "transcript": [],
    }
    return apply_manual_report_profile(report)


def invalid_action_report(*, runs_dir: str | Path, detail: str, failure_code: str | None = None) -> dict[str, Any]:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = {
        "object_type": "ManualInputSmokeReport",
        "smoke_status": "manual_input_action_invalid",
        "exit_code": 2,
        "result_attribution": "host_action_invalid",
        "recommended_next": "set_supported_manual_input_action_or_post_action_capture_policy",
        "suggested_next": ["set_supported_manual_input_action_or_post_action_capture_policy"],
        "failure_code": failure_code or "MANUAL_INPUT_ACTION_INVALID",
        "detail": detail,
        "started_at": now,
        "ended_at": now,
        "session_id": None,
        "runs_dir": str(runs_dir),
        "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
        "manual_arming_required": True,
        "input_executed": False,
        "host_input_executed": False,
        "install_executed": False,
        "background_action_executed": False,
        "evidence_package_ok": False,
        "replay_read_only": False,
        "integrity_ok": False,
        "transcript": [],
    }
    return apply_manual_report_profile(report)


def apply_manual_report_profile(report: dict[str, Any], profile: str = "full") -> dict[str, Any]:
    requested = profile if profile in {"full", "compact"} else "full"
    full_report = dict(report)
    full_report["report_profile_requested"] = requested
    full_report["report_profile_effective"] = requested
    full_report["omitted_sections"] = [] if requested == "full" else ["full_manual_smoke_report"]
    full_report["how_to_expand"] = (
        "already_full"
        if requested == "full"
        else "rerun with AI_CONTROL_REPORT_PROFILE=full for the full manual smoke report"
    )
    full_report["compact_receipt"] = _build_compact_receipt(full_report)
    if requested == "compact":
        return _compact_report_from_receipt(full_report)
    return full_report


def _compact_report_from_receipt(report: dict[str, Any]) -> dict[str, Any]:
    receipt = report["compact_receipt"]
    return {
        "object_type": report.get("object_type", "ManualInputSmokeReport"),
        "report_profile_requested": "compact",
        "report_profile_effective": "compact",
        "compact_receipt": receipt,
        "smoke_status": report.get("smoke_status"),
        "smoke_status_detail": report.get("smoke_status_detail"),
        "exit_code": report.get("exit_code"),
        "result_attribution": report.get("result_attribution"),
        "failure_code": report.get("failure_code"),
        "failure_detail": report.get("failure_detail") or report.get("detail"),
        "suggested_next": report.get("suggested_next"),
        "recommended_next": report.get("recommended_next"),
        "omitted_sections": [
            "transcript",
            "capabilities_diagnostics",
            "candidate_selection_detail",
            "frame_media_refs",
            "absolute_media_paths",
            "media_hash_detail",
            "backend_implementation_detail",
            "full_post_action_policy_detail",
        ],
        "how_to_expand": "rerun with AI_CONTROL_REPORT_PROFILE=full for the full manual smoke report",
    }


def _build_compact_receipt(report: dict[str, Any]) -> dict[str, Any]:
    input_facts = _receipt_input_facts(report)
    post_capture = _receipt_post_capture_summary(report)
    evidence = _receipt_evidence_refs(report)
    boundary = _receipt_boundary_facts(report, input_facts=input_facts, post_capture=post_capture)
    status = {
        "smoke_status": report.get("smoke_status"),
        "smoke_status_detail": report.get("smoke_status_detail"),
        "exit_code": report.get("exit_code"),
        "result_attribution": report.get("result_attribution"),
        "failure_code": report.get("failure_code"),
        "failure_detail": report.get("failure_detail") or report.get("detail"),
        "suggested_next": report.get("suggested_next"),
        "recommended_next": report.get("recommended_next"),
    }
    return {
        "object_type": "ManualInputSmokeCompactReceipt",
        "schema": "manual_input_compact_receipt_v1",
        "status": status,
        "safe_summary": _receipt_safe_summary(
            status=status,
            input_facts=input_facts,
            post_capture=post_capture,
            boundary=boundary,
        ),
        "input_facts": input_facts,
        "post_capture_summary": post_capture,
        "boundary_facts": boundary,
        "evidence_refs": evidence,
        "report_profile": {
            "requested": report.get("report_profile_requested", "full"),
            "effective": report.get("report_profile_effective", "full"),
            "how_to_expand": report.get("how_to_expand", "already_full"),
        },
    }


def _receipt_input_facts(report: dict[str, Any]) -> dict[str, Any]:
    existing = report.get("input_facts") if isinstance(report.get("input_facts"), dict) else {}
    action = report.get("action") if isinstance(report.get("action"), dict) else {}
    return {
        "input_type": existing.get("input_type") or action.get("input_type"),
        "protocol_input_recorded": bool(existing.get("protocol_input_recorded", report.get("input_executed", False))),
        "input_executed": bool(existing.get("input_executed", report.get("input_executed", False))),
        "host_input_sent": bool(existing.get("host_input_sent", report.get("host_input_executed", False))),
        "host_input_executed": bool(existing.get("host_input_executed", report.get("host_input_executed", False))),
        "host_sent_event_count": existing.get("host_sent_event_count", report.get("host_sent_event_count")),
        "input_backend_kind": existing.get("input_backend_kind", report.get("input_backend_kind")),
        "normalized_chord": existing.get("normalized_chord", report.get("normalized_chord")),
        "text_length": existing.get("text_length", report.get("text_length")),
        "text_redacted": existing.get("text_redacted", report.get("text_redacted")),
        "released_inputs": existing.get("released_inputs", report.get("released_inputs")),
        "release_result": existing.get("release_result", report.get("release_result")),
        "clipboard_api_used": bool(existing.get("clipboard_api_used", report.get("clipboard_api_used", False))),
        "paste_api_used": bool(existing.get("paste_api_used", report.get("paste_api_used", False))),
        "file_source_used": bool(existing.get("file_source_used", report.get("file_source_used", False))),
        "command_source_used": bool(existing.get("command_source_used", report.get("command_source_used", False))),
        "application_effect_unverified": existing.get(
            "application_effect_unverified",
            report.get("application_effect_unverified", True),
        ),
    }


def _receipt_post_capture_summary(report: dict[str, Any]) -> dict[str, Any]:
    existing = report.get("post_capture_summary") if isinstance(report.get("post_capture_summary"), dict) else {}
    policy_effective = existing.get("policy_effective")
    if not isinstance(policy_effective, dict):
        policy_effective = report.get("post_action_capture_policy_effective")
    if not isinstance(policy_effective, dict):
        policy_effective = {}
    policy_requested = existing.get("policy_requested")
    if not isinstance(policy_requested, dict):
        policy_requested = report.get("post_action_capture_policy_requested")
    if not isinstance(policy_requested, dict):
        policy_requested = {}
    capture_ok = bool(existing.get("capture_ok", report.get("post_action_sequence_ok", False)))
    return {
        "requested": bool(existing.get("requested", report.get("post_action_sequence_requested", False))),
        "attempted": bool(existing.get("attempted", report.get("post_action_sequence_attempted", False))),
        "capture_ok": capture_ok,
        "post_action_capture_ok": capture_ok,
        "visual_sequence_captured": bool(existing.get("visual_sequence_captured", report.get("visual_sequence_captured", False))),
        "skipped_reason": existing.get("skipped_reason", report.get("post_action_sequence_skipped_reason")),
        "failure_code": existing.get("failure_code", report.get("post_action_sequence_failure_code")),
        "failure_detail": existing.get("failure_detail", report.get("post_action_sequence_failure_detail")),
        "frame_count": existing.get("frame_count", report.get("post_action_sequence_frame_count")),
        "interval_ms": existing.get("interval_ms", report.get("post_action_sequence_interval_ms")),
        "media_kind": policy_effective.get("media_kind", report.get("post_action_capture_media_kind")),
        "sequence_ref": existing.get("sequence_ref", report.get("post_action_sequence_ref")),
        "gif_ref": report.get("post_action_sequence_media_ref"),
        "visualization_only": bool(existing.get("visualization_only", report.get("post_action_sequence_visualization_only", False))),
        "application_effect_unverified": bool(
            existing.get("application_effect_unverified", report.get("post_action_sequence_application_effect_unverified", False))
        ),
        "ocr_used": False if capture_ok else existing.get("ocr_used", report.get("post_action_sequence_ocr_used")),
        "window_semantics_used": False
        if capture_ok
        else existing.get("window_semantics_used", report.get("post_action_sequence_window_semantics_used")),
        "business_result_evaluated": False
        if capture_ok
        else existing.get("business_result_evaluated", report.get("post_action_sequence_business_result_evaluated")),
        "policy_requested": {
            "enabled": policy_requested.get("enabled"),
            "mode": policy_requested.get("mode"),
            "media_kind": policy_requested.get("media_kind"),
            "frame_count": policy_requested.get("frame_count"),
            "interval_ms": policy_requested.get("interval_ms"),
            "delay_ms": policy_requested.get("delay_ms"),
            "duration_ms": policy_requested.get("duration_ms"),
            "region": policy_requested.get("region"),
        },
        "policy_effective": {
            "enabled": policy_effective.get("enabled"),
            "frame_count": policy_effective.get("frame_count"),
            "interval_ms": policy_effective.get("interval_ms"),
            "delay_ms": policy_effective.get("delay_ms"),
            "effective_duration_ms": policy_effective.get("effective_duration_ms"),
            "duration_derivation": policy_effective.get("duration_derivation"),
            "media_kind": policy_effective.get("media_kind"),
            "region": policy_effective.get("region"),
            "video_supported": policy_effective.get("video_supported"),
            "continuous_recording": policy_effective.get("continuous_recording"),
            "visualization_only": policy_effective.get("visualization_only"),
            "application_effect_unverified": policy_effective.get("application_effect_unverified"),
        },
    }


def _receipt_evidence_refs(report: dict[str, Any]) -> dict[str, Any]:
    existing = report.get("evidence_refs") if isinstance(report.get("evidence_refs"), dict) else {}
    return {
        "before_observation_ref": existing.get("before_observation_ref", report.get("before_observation_ref")),
        "before_media_ref": existing.get("before_media_ref", report.get("before_media_ref")),
        "after_observation_ref": existing.get("after_observation_ref", report.get("after_observation_ref")),
        "after_media_ref": existing.get("after_media_ref", report.get("after_media_ref")),
        "post_action_sequence_ref": existing.get("post_action_sequence_ref", report.get("post_action_sequence_ref")),
        "post_action_gif_ref": existing.get("post_action_gif_ref", report.get("post_action_sequence_media_ref")),
        "evidence_package_ok": bool(existing.get("evidence_package_ok", report.get("evidence_package_ok", False))),
        "replay_read_only": bool(existing.get("replay_read_only", report.get("replay_read_only", False))),
        "integrity_ok": bool(existing.get("integrity_ok", report.get("integrity_ok", False))),
    }


def _receipt_boundary_facts(
    report: dict[str, Any],
    *,
    input_facts: dict[str, Any],
    post_capture: dict[str, Any],
) -> dict[str, Any]:
    return {
        "application_effect_unverified": bool(input_facts.get("application_effect_unverified", True))
        or bool(post_capture.get("application_effect_unverified", False)),
        "business_result_evaluated": False,
        "clipboard_api_used": bool(input_facts.get("clipboard_api_used", False)),
        "paste_api_used": bool(input_facts.get("paste_api_used", False)),
        "clipboard_content_observed": False,
        "file_source_used": bool(input_facts.get("file_source_used", False)),
        "command_source_used": bool(input_facts.get("command_source_used", False)),
        "ocr_used": False,
        "window_semantics_used": False,
        "background_action_executed": bool(report.get("background_action_executed", False)),
        "install_executed": bool(report.get("install_executed", False)),
        "visual_evidence_only": True,
    }


def _receipt_safe_summary(
    *,
    status: dict[str, Any],
    input_facts: dict[str, Any],
    post_capture: dict[str, Any],
    boundary: dict[str, Any],
) -> str:
    if input_facts.get("host_input_sent"):
        input_part = "host_input_sent=true; host input was sent by the reported input backend"
    elif input_facts.get("protocol_input_recorded"):
        input_part = "protocol_input_recorded=true; host_input_sent=false; the input path was recorded without sending host input"
    else:
        input_part = "protocol_input_recorded=false; host_input_sent=false; no input was executed"

    if post_capture.get("capture_ok"):
        capture_part = "visual_sequence_captured=true; post-action visual evidence was captured only"
    elif post_capture.get("attempted"):
        capture_part = "post_action_capture_ok=false; post-action visual capture was attempted but did not succeed"
    elif post_capture.get("requested") is False:
        capture_part = "post_action_capture_requested=false; post-action visual capture was not requested"
    else:
        capture_part = "post-action visual capture did not produce a captured sequence"

    boundary_part = "clipboard_api_used=false; paste_api_used=false; business_result_evaluated=false"
    if status.get("failure_code"):
        return f"{input_part}; {capture_part}; {boundary_part}; status={status.get('smoke_status')}; failure_code={status.get('failure_code')}."
    if boundary.get("clipboard_api_used") or boundary.get("paste_api_used"):
        boundary_part = "clipboard_or_paste_api_boundary_touched=true; inspect the full report"
    return f"{input_part}; {capture_part}; {boundary_part}; status={status.get('smoke_status')}."


def _read_evidence(adapter: MCPStdioAdapter, transcript: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    package = adapter.call_tool("get_evidence_package")
    replay = adapter.call_tool("read_replay")
    integrity = adapter.call_tool("verify_integrity")
    transcript.append(_compact_response("get_evidence_package", package))
    transcript.append(_compact_response("read_replay", replay))
    transcript.append(_compact_response("verify_integrity", integrity))
    return package, replay, integrity


def _report(
    smoke_status: str,
    exit_code: int,
    *,
    adapter: MCPStdioAdapter,
    runs_dir: str | Path,
    started_at: str,
    observation_channel_ref: str,
    action: dict[str, Any],
    requested_action: dict[str, Any] | None = None,
    capabilities: dict[str, Any],
    before: dict[str, Any] | None = None,
    execute: dict[str, Any] | None,
    post_action_sequence: dict[str, Any] | None = None,
    package: dict[str, Any] | None,
    replay: dict[str, Any] | None,
    integrity: dict[str, Any] | None,
    transcript: list[dict[str, Any]],
    recommended_next: str,
    candidate_selection: dict[str, Any] | None = None,
    post_action_sequence_skipped_reason: str | None = None,
    post_action_capture_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = execute.get("data", {}) if execute and execute.get("ok") else {}
    failure = execute.get("failure", {}) if execute and not execute.get("ok") else {}
    diagnostics = capabilities.get("data", {}).get("input_diagnostics", {})
    before_data = before.get("data", {}) if before and before.get("ok") else {}
    after_data = execute.get("after_observation", {}) if execute and execute.get("ok") else {}
    post_sequence_data = post_action_sequence.get("data", {}) if post_action_sequence and post_action_sequence.get("ok") else {}
    post_sequence_failure = post_action_sequence.get("failure", {}) if post_action_sequence and not post_action_sequence.get("ok") else {}
    if post_sequence_data and not post_sequence_data.get("media_refs"):
        derived_media_refs = _sequence_frame_media_refs(post_sequence_data)
        if derived_media_refs:
            post_sequence_data = {**post_sequence_data, "media_refs": derived_media_refs}
    sequence_media = post_sequence_data.get("sequence_media") if isinstance(post_sequence_data.get("sequence_media"), dict) else {}
    sequence_media_access = (
        post_sequence_data.get("sequence_media_access") if isinstance(post_sequence_data.get("sequence_media_access"), dict) else {}
    )
    input_backend_kind = _input_backend_kind(data, failure)
    host_sent_event_count = data.get("host_sent_event_count", data.get("sent_event_count"))
    smoke_status_detail = _smoke_status_detail(
        smoke_status,
        requested_action or action,
        input_backend_kind=input_backend_kind,
        host_input_executed=bool(data.get("host_input_executed", False)),
        post_action_sequence=post_action_sequence,
    )
    public_requested_action = _public_action(requested_action or action)
    public_action = _public_action(action)
    text_summary = _report_text_summary(data=data, action=action)
    keyboard_summary = _report_keyboard_summary(data=data, action=action)
    post_capture_policy_active = _effective_post_action_capture_policy(before_data, post_action_capture_policy)
    post_capture_policy_effective = _post_action_capture_policy_effective(post_capture_policy_active)
    post_capture_policy_requested = _post_action_capture_policy_requested(post_capture_policy_active)
    visual_sequence_captured = bool(post_action_sequence and post_action_sequence.get("ok"))
    input_facts = _input_facts(
        public_action=public_action,
        data=data,
        input_backend_kind=input_backend_kind,
        host_sent_event_count=host_sent_event_count,
        text_summary=text_summary,
        keyboard_summary=keyboard_summary,
    )
    post_capture_summary = _post_capture_summary(
        requested=post_capture_policy_requested,
        effective=post_capture_policy_effective,
        post_action_sequence=post_action_sequence,
        post_action_sequence_skipped_reason=post_action_sequence_skipped_reason,
        post_sequence_data=post_sequence_data,
        post_sequence_failure=post_sequence_failure,
    )
    evidence_refs = _evidence_refs(
        before_data=before_data,
        after_data=after_data,
        post_sequence_data=post_sequence_data,
        sequence_media=sequence_media,
        package=package,
        replay=replay,
        integrity=integrity,
    )
    truth_summary = _truth_summary(
        before_data=before_data,
        candidate_selection=candidate_selection,
        input_backend_kind=input_backend_kind,
        data=data,
    )
    report = {
        "object_type": "ManualInputSmokeReport",
        "smoke_status": smoke_status,
        "smoke_status_detail": smoke_status_detail,
        "exit_code": exit_code,
        "result_attribution": _result_attribution(smoke_status),
        "recommended_next": recommended_next,
        "suggested_next": [recommended_next],
        "started_at": started_at,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": adapter.session_id,
        "runs_dir": str(runs_dir),
        "input_channel_ref": data.get("input_channel_ref") or failure.get("channel_ref") or DEFAULT_INPUT_CHANNEL_REF,
        "observation_channel_ref": observation_channel_ref,
        "backend_implementation": data.get("implementation") or failure.get("implementation"),
        "input_backend_kind": input_backend_kind,
        "host_api": data.get("host_api"),
        "requested_event_count": data.get("requested_event_count"),
        "host_sent_event_count": host_sent_event_count,
        "requested_action": public_requested_action,
        "action": public_action,
        **text_summary,
        **keyboard_summary,
        "candidate_selection": candidate_selection,
        "candidate_geometry_only": bool(candidate_selection and candidate_selection.get("candidate_geometry_only") is True),
        "candidate_source_real_capture": (candidate_selection or {}).get("source_real_capture"),
        "before_observation_ref": before_data.get("observation_id"),
        "before_observation_channel_ref": before_data.get("channel_ref"),
        "before_real_capture": before_data.get("real_capture"),
        "before_source_kind": before_data.get("source_kind"),
        "before_media_ref": before_data.get("media_ref"),
        "before_media_sha256": before_data.get("media_sha256"),
        "manual_arming_required": True,
        "real_input_available": diagnostics.get("real_input_available", False),
        "available_real_input_channels": diagnostics.get("available_real_input_channels", []),
        "input_executed": bool(data.get("input_executed", failure.get("input_executed", False))),
        "host_input_executed": bool(data.get("host_input_executed", False)),
        "sent_event_count": data.get("sent_event_count"),
        "released_inputs": data.get("released_inputs"),
        "release_result": data.get("release_result"),
        "real_input_gate_state": data.get("real_input_gate", {}).get("state"),
        "after_observation_ref": after_data.get("observation_id"),
        "after_observation_channel_ref": (execute or {}).get("after_observation", {}).get("channel_ref"),
        "after_real_capture": after_data.get("real_capture"),
        "after_media_ref": after_data.get("media_ref"),
        "after_media_sha256": after_data.get("media_sha256"),
        "post_action_sequence_ok": bool(post_action_sequence and post_action_sequence.get("ok")),
        "visual_sequence_captured": visual_sequence_captured,
        "post_action_sequence_attempted": post_action_sequence is not None,
        "post_action_sequence_requested": bool(post_capture_policy_effective.get("enabled"))
        and (post_action_sequence is not None or bool(post_action_sequence_skipped_reason)),
        "post_action_capture_policy": post_capture_policy_effective,
        "post_action_capture_policy_requested": post_capture_policy_requested,
        "post_action_capture_policy_effective": post_capture_policy_effective,
        "post_action_capture_media_kind": post_capture_policy_effective.get("media_kind"),
        "post_action_capture_delay_ms": post_capture_policy_effective.get("delay_ms"),
        "post_action_capture_requested_frame_count": post_capture_policy_effective.get("frame_count"),
        "post_action_capture_requested_interval_ms": post_capture_policy_effective.get("interval_ms"),
        "post_action_capture_requested_region": post_capture_policy_effective.get("region"),
        "post_action_capture_requested_duration_ms": post_capture_policy_requested.get("duration_ms"),
        "post_action_capture_effective_duration_ms": post_capture_policy_effective.get("effective_duration_ms"),
        "post_action_capture_duration_derivation": post_capture_policy_effective.get("duration_derivation"),
        "post_action_capture_video_requested": post_capture_policy_effective.get("video_requested"),
        "post_action_capture_video_supported": post_capture_policy_effective.get("video_supported"),
        "post_action_sequence_skipped_reason": post_action_sequence_skipped_reason,
        "post_action_sequence_failure_code": post_sequence_failure.get("failure_code"),
        "post_action_sequence_failure_detail": post_sequence_failure.get("detail"),
        "post_action_sequence_ref": post_sequence_data.get("sequence_id"),
        "post_action_sequence_channel_ref": post_sequence_data.get("channel_ref"),
        "post_action_sequence_frame_count": post_sequence_data.get("actual_frame_count"),
        "post_action_sequence_interval_ms": post_sequence_data.get("interval_ms"),
        "post_action_sequence_region": post_sequence_data.get("screen_region"),
        "post_action_sequence_media_refs": post_sequence_data.get("media_refs"),
        "post_action_sequence_change_summary": post_sequence_data.get("change_summary"),
        "post_action_sequence_visualization_only": bool(post_action_sequence and post_action_sequence.get("ok")),
        "post_action_sequence_application_effect_unverified": bool(post_action_sequence and post_action_sequence.get("ok")),
        "post_action_sequence_ocr_used": False if post_action_sequence and post_action_sequence.get("ok") else None,
        "post_action_sequence_window_semantics_used": False if post_action_sequence and post_action_sequence.get("ok") else None,
        "post_action_sequence_business_result_evaluated": False if post_action_sequence and post_action_sequence.get("ok") else None,
        "post_action_sequence_media_status": sequence_media.get("status") or post_sequence_data.get("sequence_media_status"),
        "post_action_sequence_media_ref": sequence_media.get("media_ref"),
        "post_action_sequence_media_sha256": sequence_media.get("media_sha256"),
        "post_action_sequence_media_mime": sequence_media.get("media_mime"),
        "post_action_sequence_media_size_bytes": sequence_media.get("media_size_bytes"),
        "post_action_sequence_generated_from_frame_refs": sequence_media.get("generated_from_frame_refs"),
        "post_action_sequence_media_path_abs": sequence_media_access.get("media_path_abs"),
        "post_action_sequence_media_access_response_only": bool(sequence_media_access),
        "truth_summary": truth_summary,
        "input_facts": input_facts,
        "post_capture_summary": post_capture_summary,
        "evidence_refs": evidence_refs,
        "failure_code": failure.get("failure_code"),
        "failure_detail": failure.get("detail"),
        "install_executed": False,
        "background_action_executed": False,
        "evidence_package_ok": bool(package and package.get("ok")),
        "replay_read_only": bool(replay and replay.get("ok") and replay.get("data", {}).get("read_only")),
        "integrity_ok": bool(integrity and integrity.get("ok") and integrity.get("data", {}).get("ok")),
        "transcript": transcript,
    }
    return apply_manual_report_profile(report)


def _result_attribution(smoke_status: str) -> str:
    if smoke_status == "manual_input_succeeded":
        return "armed_input_path_verified"
    if smoke_status == "manual_input_observation_not_ready":
        return "real_observation_unavailable"
    if smoke_status == "manual_input_lease_failed":
        return "arming_or_lease_failed"
    if smoke_status == "manual_input_candidate_failed":
        return "candidate_selection_failed"
    if smoke_status == "manual_input_integrity_failed":
        return "evidence_integrity_failed"
    return "input_execution_failed"


def _input_backend_kind(data: dict[str, Any], failure: dict[str, Any]) -> str | None:
    input_channel_ref = data.get("input_channel_ref") or failure.get("channel_ref")
    implementation = data.get("implementation") or failure.get("implementation") or ""
    lowered = str(implementation).lower()
    if input_channel_ref == "dry_run_input" or "dry_run" in lowered:
        return "dry_run"
    if "fake" in lowered or "no_host" in lowered:
        return "fake_no_host_calls"
    if str(data.get("host_api", "")).lower() == "sendinput" or implementation == "ctypes_win32_sendinput":
        return "win32_sendinput"
    if data.get("input_executed") is True and data.get("host_input_executed") is False:
        return "fake_no_host_calls"
    return "unknown" if implementation or input_channel_ref else None


def _smoke_status_detail(
    smoke_status: str,
    requested_action: dict[str, Any],
    *,
    input_backend_kind: str | None,
    host_input_executed: bool,
    post_action_sequence: dict[str, Any] | None,
) -> str:
    if requested_action.get("input_type") == "key_text_stream":
        if smoke_status != "manual_input_succeeded":
            return f"key_text_{smoke_status.removeprefix('manual_input_')}"
        if post_action_sequence is not None and not post_action_sequence.get("ok"):
            return "key_text_post_action_sequence_failed"
        if host_input_executed:
            return "key_text_host_input_succeeded"
        if input_backend_kind == "fake_no_host_calls":
            return "key_text_fake_backend_succeeded"
        if input_backend_kind == "dry_run":
            return "key_text_dry_run_succeeded"
        return "key_text_no_host_input_succeeded"
    if is_keyboard_action(requested_action.get("input_type")):
        prefix = requested_action.get("input_type")
        if smoke_status != "manual_input_succeeded":
            return f"{prefix}_{smoke_status.removeprefix('manual_input_')}"
        if post_action_sequence is not None and not post_action_sequence.get("ok"):
            return f"{prefix}_post_action_sequence_failed"
        if host_input_executed:
            return f"{prefix}_host_input_succeeded"
        if input_backend_kind == "fake_no_host_calls":
            return f"{prefix}_fake_backend_succeeded"
        if input_backend_kind == "dry_run":
            return f"{prefix}_dry_run_succeeded"
        return f"{prefix}_no_host_input_succeeded"
    if requested_action.get("input_type") != "candidate_click":
        return smoke_status
    if smoke_status != "manual_input_succeeded":
        return f"candidate_{smoke_status.removeprefix('manual_input_')}"
    if post_action_sequence is not None and not post_action_sequence.get("ok"):
        return "candidate_post_action_sequence_failed"
    if host_input_executed:
        return "candidate_host_input_succeeded"
    if input_backend_kind == "fake_no_host_calls":
        return "candidate_fake_backend_succeeded"
    if input_backend_kind == "dry_run":
        return "candidate_dry_run_succeeded"
    return "candidate_no_host_input_succeeded"


def _public_action(action: dict[str, Any]) -> dict[str, Any]:
    if is_keyboard_action(action.get("input_type")):
        return keyboard_action_summary(action.get("input_type"), action)
    if action.get("input_type") != "key_text_stream":
        return action
    text = validate_key_text_stream(action.get("text"))
    return {"input_type": "key_text_stream", **key_text_summary(text)}


def _report_text_summary(*, data: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    if action.get("input_type") != "key_text_stream":
        return {
            "text_length": data.get("text_length"),
            "text_sha256": data.get("text_sha256"),
            "text_encoding": data.get("text_encoding"),
            "text_redacted": data.get("text_redacted"),
            "text_recording_policy": data.get("text_recording_policy"),
            "human_input_equivalent": data.get("human_input_equivalent"),
            "keyboard_event_policy": data.get("keyboard_event_policy"),
            "text_source_policy": data.get("text_source_policy"),
            "clipboard_used": data.get("clipboard_used"),
            "file_source_used": data.get("file_source_used"),
            "command_source_used": data.get("command_source_used"),
        }
    summary = key_text_summary(validate_key_text_stream(action.get("text")))
    if not data.get("text_sha256"):
        return summary
    return {key: data.get(key, value) for key, value in summary.items()}


def _report_keyboard_summary(*, data: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    if not is_keyboard_action(action.get("input_type")):
        return {}
    summary = keyboard_action_summary(action.get("input_type"), action)
    return {key: data.get(key, value) for key, value in summary.items()}


def _truth_summary(
    *,
    before_data: dict[str, Any],
    candidate_selection: dict[str, Any] | None,
    input_backend_kind: str | None,
    data: dict[str, Any],
) -> dict[str, str]:
    before_real = before_data.get("source_kind") == "software_screen_capture" and before_data.get("real_capture") is True
    candidate_real = bool(candidate_selection and candidate_selection.get("source_real_capture") is True)
    host_executed = data.get("host_input_executed")
    input_executed = data.get("input_executed")
    if before_real:
        observation_truth = "real_capture"
    elif before_data:
        observation_truth = "test_capture"
    else:
        observation_truth = "unknown"
    if candidate_selection is None:
        candidate_truth = "candidate_unavailable"
    elif candidate_real:
        candidate_truth = "geometry_from_real_capture"
    else:
        candidate_truth = "geometry_from_test_capture"
    if input_backend_kind == "win32_sendinput":
        input_channel_truth = "real_input_channel"
    elif input_backend_kind == "dry_run":
        input_channel_truth = "dry_run"
    elif input_backend_kind == "fake_no_host_calls":
        input_channel_truth = "mock_or_fake"
    else:
        input_channel_truth = "unknown"
    if input_backend_kind == "win32_sendinput":
        backend_truth = "host_sendinput"
    elif input_backend_kind == "fake_no_host_calls":
        backend_truth = "fake_backend_no_host_calls"
    elif input_backend_kind == "dry_run":
        backend_truth = "dry_run_no_host_calls"
    else:
        backend_truth = "unknown"
    if host_executed is True:
        host_input_truth = "host_input_executed"
    elif host_executed is False or input_executed is not None:
        host_input_truth = "no_host_input_executed"
    else:
        host_input_truth = "unknown_after_failure"
    return {
        "observation_truth": observation_truth,
        "candidate_truth": candidate_truth,
        "input_channel_truth": input_channel_truth,
        "backend_truth": backend_truth,
        "host_input_truth": host_input_truth,
    }


def _input_facts(
    *,
    public_action: dict[str, Any],
    data: dict[str, Any],
    input_backend_kind: str | None,
    host_sent_event_count: Any,
    text_summary: dict[str, Any],
    keyboard_summary: dict[str, Any],
) -> dict[str, Any]:
    input_type = public_action.get("input_type")
    application_effect_unverified = public_action.get("application_effect_unverified")
    if application_effect_unverified is None:
        application_effect_unverified = keyboard_summary.get("application_effect_unverified")
    if application_effect_unverified is None and input_type in {"key_text_stream", "key_press", "key_chord"}:
        application_effect_unverified = True
    return {
        "input_type": input_type,
        "protocol_input_recorded": bool(data.get("input_executed", False)),
        "input_executed": bool(data.get("input_executed", False)),
        "host_input_sent": bool(data.get("host_input_executed", False)),
        "host_input_executed": bool(data.get("host_input_executed", False)),
        "host_sent_event_count": host_sent_event_count,
        "input_backend_kind": input_backend_kind,
        "normalized_chord": keyboard_summary.get("normalized_chord"),
        "text_length": text_summary.get("text_length"),
        "text_redacted": text_summary.get("text_redacted"),
        "released_inputs": data.get("released_inputs"),
        "release_result": data.get("release_result"),
        "clipboard_api_used": public_action.get("clipboard_api_used", data.get("clipboard_api_used", False)),
        "paste_api_used": public_action.get("paste_api_used", data.get("paste_api_used", False)),
        "application_effect_unverified": application_effect_unverified,
    }


def _post_capture_summary(
    *,
    requested: dict[str, Any],
    effective: dict[str, Any],
    post_action_sequence: dict[str, Any] | None,
    post_action_sequence_skipped_reason: str | None,
    post_sequence_data: dict[str, Any],
    post_sequence_failure: dict[str, Any],
) -> dict[str, Any]:
    capture_ok = bool(post_action_sequence and post_action_sequence.get("ok"))
    return {
        "requested": bool(effective.get("enabled"))
        and (post_action_sequence is not None or bool(post_action_sequence_skipped_reason)),
        "attempted": post_action_sequence is not None,
        "capture_ok": capture_ok,
        "visual_sequence_captured": capture_ok,
        "skipped_reason": post_action_sequence_skipped_reason,
        "failure_code": post_sequence_failure.get("failure_code"),
        "failure_detail": post_sequence_failure.get("detail"),
        "policy_requested": requested,
        "policy_effective": effective,
        "frame_count": post_sequence_data.get("actual_frame_count"),
        "interval_ms": post_sequence_data.get("interval_ms"),
        "region": post_sequence_data.get("screen_region"),
        "sequence_ref": post_sequence_data.get("sequence_id"),
        "media_refs": post_sequence_data.get("media_refs"),
        "visualization_only": capture_ok,
        "application_effect_unverified": capture_ok,
        "ocr_used": False if capture_ok else None,
        "window_semantics_used": False if capture_ok else None,
        "business_result_evaluated": False if capture_ok else None,
    }


def _evidence_refs(
    *,
    before_data: dict[str, Any],
    after_data: dict[str, Any],
    post_sequence_data: dict[str, Any],
    sequence_media: dict[str, Any],
    package: dict[str, Any] | None,
    replay: dict[str, Any] | None,
    integrity: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "before_observation_ref": before_data.get("observation_id"),
        "before_media_ref": before_data.get("media_ref"),
        "after_observation_ref": after_data.get("observation_id"),
        "after_media_ref": after_data.get("media_ref"),
        "post_action_sequence_ref": post_sequence_data.get("sequence_id"),
        "post_action_media_refs": post_sequence_data.get("media_refs"),
        "post_action_gif_ref": sequence_media.get("media_ref"),
        "post_action_gif_sha256": sequence_media.get("media_sha256"),
        "evidence_package_ok": bool(package and package.get("ok")),
        "replay_read_only": bool(replay and replay.get("ok") and replay.get("data", {}).get("read_only")),
        "integrity_ok": bool(integrity and integrity.get("ok") and integrity.get("data", {}).get("ok")),
    }


def _compact_response(step: str, response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok"):
        data = response.get("data", {})
        return {
            "step": step,
            "ok": True,
            "object_type": data.get("object_type"),
            "channel_ref": data.get("channel_ref") or data.get("input_channel_ref"),
            "input_executed": data.get("input_executed"),
            "host_input_executed": data.get("host_input_executed"),
            "evidence_ref": response.get("evidence_ref"),
        }
    failure = response.get("failure", {})
    return {
        "step": step,
        "ok": False,
        "failure_code": failure.get("failure_code"),
        "failure_detail": failure.get("detail"),
        "channel_ref": failure.get("channel_ref"),
        "input_executed": failure.get("input_executed"),
        "evidence_ref": failure.get("evidence_ref"),
    }
