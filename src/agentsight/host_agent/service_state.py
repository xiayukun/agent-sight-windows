from __future__ import annotations

import time
from typing import Any, Final


SERVICE_STATE_SCHEMA: Final = "agentsight_service_state_v1"
SERVICE_HEALTH_SCHEMA: Final = "agentsight_service_health_v1"

OK_ACTIVE_DEFAULT_DESKTOP: Final = "ok_active_default_desktop"
HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION: Final = "host_agent_not_active_console_session"

SERVICE_CONTROL_STATES: Final[tuple[str, ...]] = (
    OK_ACTIVE_DEFAULT_DESKTOP,
    HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION,
    "no_active_console_session",
    "session_disconnected",
    "locked_desktop",
    "secure_desktop_active",
    "uac_secure_desktop_active",
    "child_not_running",
    "child_start_failed",
    "child_unhealthy",
    "discovery_stale",
    "kill_switch_active",
    "operator_control_paused",
)

BLOCKING_SERVICE_STATES: Final[tuple[str, ...]] = tuple(
    state for state in SERVICE_CONTROL_STATES if state != OK_ACTIVE_DEFAULT_DESKTOP
)

BLOCKER_REASONS: Final[dict[str, str]] = {
    HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION: "The Host Agent process is running in a non-current Windows session; it cannot see or control the active visible desktop.",
    "no_active_console_session": "No active console session is available for a visible user-session agent.",
    "session_disconnected": "The target user session is disconnected and is not an active visible desktop.",
    "locked_desktop": "The user desktop is locked; real control must wait for the human-visible desktop.",
    "secure_desktop_active": "A secure desktop is active; P0-E1 only reports this blocker and does not bypass it.",
    "uac_secure_desktop_active": "A UAC secure desktop is active; P0-E1 treats it as out of scope for control.",
    "child_not_running": "The visible-session Host Agent child process is not running.",
    "child_start_failed": "The service supervisor recorded a child launch failure.",
    "child_unhealthy": "The visible-session Host Agent child is running but failed health checks.",
    "discovery_stale": "The discovery file points to stale or unverifiable child-agent state.",
    "kill_switch_active": "The emergency stop is active; restart and real control are disabled.",
    "operator_control_paused": "The local operator paused AI real-control requests from the tray-visible policy surface.",
}


class ServiceStateError(ValueError):
    pass


def service_state_schema() -> dict[str, Any]:
    return {
        "object_type": "AgentSightServiceStateSchema",
        "schema": "agentsight_service_state_schema_v1",
        "service_state_schema": SERVICE_STATE_SCHEMA,
        "allowed_service_statuses": list(SERVICE_CONTROL_STATES),
        "blocking_service_statuses": list(BLOCKING_SERVICE_STATES),
        "required_fields": [
            "object_type",
            "schema",
            "service_status",
            "can_attempt_real_control",
            "control_blockers",
            "host_input_sent",
            "host_sent_event_count",
        ],
    }


def service_health_schema() -> dict[str, Any]:
    return {
        "object_type": "AgentSightServiceHealthSchema",
        "schema": "agentsight_service_health_schema_v1",
        "service_health_schema": SERVICE_HEALTH_SCHEMA,
        "allowed_service_statuses": list(SERVICE_CONTROL_STATES),
        "blocking_service_statuses": list(BLOCKING_SERVICE_STATES),
        "required_fields": [
            "object_type",
            "schema",
            "service_status",
            "can_attempt_real_control",
            "control_blockers",
            "control_blocker_details",
            "host_input_sent",
            "host_sent_event_count",
            "boundary",
        ],
    }


def build_service_state(
    service_status: str,
    *,
    active_console_session_id: int | None = None,
    child_pid: int | None = None,
    child_session_id: int | None = None,
    child_health: dict[str, Any] | None = None,
    discovery_status: str | None = None,
    audit_log_ref: str | None = None,
    last_error: dict[str, Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    status = _validate_service_status(service_status)
    can_attempt = status == OK_ACTIVE_DEFAULT_DESKTOP
    return {
        "object_type": "AgentSightServiceState",
        "schema": SERVICE_STATE_SCHEMA,
        "service_role": "session_supervisor_state_model",
        "service_status": status,
        "service_state_family": "ready" if can_attempt else "blocked",
        "timestamp_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "active_console_session_id": active_console_session_id,
        "child_agent": {
            "pid": child_pid,
            "session_id": child_session_id,
            "health": child_health,
        },
        "discovery": {
            "status": discovery_status or ("fresh" if can_attempt else "unknown"),
        },
        "audit_log_ref": audit_log_ref,
        "last_error": last_error,
        "can_attempt_real_control": can_attempt,
        "control_blockers": [] if can_attempt else [status],
        "control_blocker_details": [] if can_attempt else [_blocker_detail(status)],
        "host_input_sent": False,
        "host_input_executed": False,
        "host_sent_event_count": 0,
        "service_installed": False,
        "privileged_operation_attempted": False,
        "boundary": _boundary_facts(),
    }


def build_service_health(state_or_status: dict[str, Any] | str) -> dict[str, Any]:
    state = build_service_state(state_or_status) if isinstance(state_or_status, str) else validate_service_state(state_or_status)
    status = str(state["service_status"])
    can_attempt = status == OK_ACTIVE_DEFAULT_DESKTOP
    return {
        "object_type": "AgentSightServiceHealth",
        "schema": SERVICE_HEALTH_SCHEMA,
        "service_role": "session_supervisor_health_model",
        "service_status": status,
        "state_schema": state.get("schema"),
        "state_timestamp_ms": state.get("timestamp_ms"),
        "active_console_session_id": state.get("active_console_session_id"),
        "child_agent": state.get("child_agent"),
        "discovery": state.get("discovery"),
        "can_attempt_real_control": can_attempt,
        "control_blockers": [] if can_attempt else [status],
        "control_blocker_details": [] if can_attempt else [_blocker_detail(status)],
        "host_input_sent": False,
        "host_input_executed": False,
        "host_sent_event_count": 0,
        "boundary": _boundary_facts(),
    }


def project_host_agent_health_to_service_state(
    *,
    session: dict[str, Any],
    station: dict[str, Any],
    input_desktop: dict[str, Any],
    foreground_window: dict[str, Any],
    cursor_probe: dict[str, Any],
    capture_probe: dict[str, Any],
    raw_can_attempt_real_control: bool,
    raw_control_blockers: list[str] | tuple[str, ...],
    now_ms: int | None = None,
) -> dict[str, Any]:
    classification = classify_host_agent_health(
        session=session,
        station=station,
        input_desktop=input_desktop,
        foreground_window=foreground_window,
        cursor_probe=cursor_probe,
        capture_probe=capture_probe,
        raw_can_attempt_real_control=raw_can_attempt_real_control,
        raw_control_blockers=raw_control_blockers,
    )
    state = build_service_state(
        classification["service_status"],
        active_console_session_id=_int_or_none(session.get("active_console_session_id")),
        child_pid=_int_or_none(session.get("process_id")),
        child_session_id=_int_or_none(session.get("process_session_id")),
        child_health={
            "raw_can_attempt_real_control": bool(raw_can_attempt_real_control),
            "raw_control_blockers": list(raw_control_blockers),
        },
        discovery_status="fresh",
        last_error={"classification": classification},
        now_ms=now_ms,
    )
    state["host_agent_health_projection"] = classification
    return state


def project_supervisor_child_health_to_service_state(
    *,
    discovery: dict[str, Any] | None,
    discovery_file_exists: bool,
    child_pid_running: bool | None,
    child_health: dict[str, Any] | None,
    child_health_request_ok: bool | None,
    child_start_error: dict[str, Any] | None = None,
    kill_switch_active: bool = False,
    now_ms: int | None = None,
) -> dict[str, Any]:
    classification = classify_supervisor_child_health(
        discovery=discovery,
        discovery_file_exists=discovery_file_exists,
        child_pid_running=child_pid_running,
        child_health=child_health,
        child_health_request_ok=child_health_request_ok,
        child_start_error=child_start_error,
        kill_switch_active=kill_switch_active,
    )
    child_pid = None
    child_session_id = None
    if isinstance(discovery, dict):
        child_pid = _int_or_none(discovery.get("pid"))
        child_session_id = _int_or_none(discovery.get("process_session_id"))
    state = build_service_state(
        classification["service_status"],
        active_console_session_id=_int_or_none(discovery.get("active_console_session_id")) if isinstance(discovery, dict) else None,
        child_pid=child_pid,
        child_session_id=child_session_id,
        child_health=child_health,
        discovery_status=classification["discovery_status"],
        last_error={"classification": classification, "child_start_error": child_start_error},
        now_ms=now_ms,
    )
    state["supervisor_child_health_projection"] = classification
    return state


def classify_supervisor_child_health(
    *,
    discovery: dict[str, Any] | None,
    discovery_file_exists: bool,
    child_pid_running: bool | None,
    child_health: dict[str, Any] | None,
    child_health_request_ok: bool | None,
    child_start_error: dict[str, Any] | None = None,
    kill_switch_active: bool = False,
) -> dict[str, Any]:
    signals: list[str] = []
    status = OK_ACTIVE_DEFAULT_DESKTOP
    discovery_status = "fresh"

    if kill_switch_active:
        status = "kill_switch_active"
        discovery_status = "blocked_by_kill_switch"
        signals.append("kill_switch_active")
    elif child_start_error:
        status = "child_start_failed"
        discovery_status = "start_failed"
        signals.append("child_start_error_present")
    elif not discovery_file_exists or not isinstance(discovery, dict):
        status = "child_not_running"
        discovery_status = "missing"
        signals.append("discovery_missing")
    elif child_pid_running is False:
        status = "discovery_stale"
        discovery_status = "stale_pid_not_running"
        signals.append("discovery_pid_not_running")
    elif child_health_request_ok is False:
        status = "child_unhealthy"
        discovery_status = "health_unreachable"
        signals.append("child_health_request_failed")
    elif isinstance(child_health, dict) and child_health.get("service_status") not in {None, OK_ACTIVE_DEFAULT_DESKTOP}:
        status = str(child_health["service_status"])
        discovery_status = "fresh_child_blocked"
        signals.append(f"child_service_status={status}")
    elif isinstance(child_health, dict) and child_health.get("can_attempt_real_control") is False:
        status = "child_unhealthy"
        discovery_status = "fresh_child_not_ready"
        signals.append("child_health_not_ready_without_service_status")
    else:
        signals.append("child_health_ready")

    return {
        "object_type": "SupervisorChildHealthClassification",
        "schema": "agentsight_supervisor_child_health_classification_v1",
        "service_status": status,
        "discovery_status": discovery_status,
        "can_attempt_real_control": status == OK_ACTIVE_DEFAULT_DESKTOP,
        "signals": signals,
        "discovery_file_exists": bool(discovery_file_exists),
        "child_pid_running": child_pid_running,
        "child_health_request_ok": child_health_request_ok,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _boundary_facts(),
    }


def classify_host_agent_health(
    *,
    session: dict[str, Any],
    station: dict[str, Any],
    input_desktop: dict[str, Any],
    foreground_window: dict[str, Any],
    cursor_probe: dict[str, Any],
    capture_probe: dict[str, Any],
    raw_can_attempt_real_control: bool,
    raw_control_blockers: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    signals: list[str] = []
    status = OK_ACTIVE_DEFAULT_DESKTOP
    active_console_session_id = _int_or_none(session.get("active_console_session_id"))
    process_session_id = _int_or_none(session.get("process_session_id"))
    connect_state = session.get("process_session_connect_state")
    connect_state_name = connect_state.get("state_name") if isinstance(connect_state, dict) else None
    connect_state_query_ok = connect_state.get("query_ok") if isinstance(connect_state, dict) else None
    process_session_is_wts_active = bool(session.get("process_session_is_wts_active"))
    process_is_active_visible_session = bool(
        session.get("process_is_active_visible_session")
        or session.get("process_is_active_console_session")
        or process_session_is_wts_active
    )
    foreground_title = str(foreground_window.get("title") or "")
    desktop_name = str(input_desktop.get("desktop_name") or "")
    input_desktop_error = str(input_desktop.get("error_text") or input_desktop.get("error") or "")
    raw_blocker_set = set(str(item) for item in raw_control_blockers)

    if "kill_switch_active" in raw_blocker_set:
        status = "kill_switch_active"
        signals.append("kill_switch_active")
    elif "operator_control_paused" in raw_blocker_set:
        status = "operator_control_paused"
        signals.append("operator_control_paused")
    elif active_console_session_id in {None, 0xFFFFFFFF}:
        status = "no_active_console_session"
        signals.append("active_console_session_unavailable")
    elif connect_state_name and connect_state_name != "WTSActive":
        status = "session_disconnected"
        signals.append(f"session_connect_state={connect_state_name}")
    elif process_session_id is not None and active_console_session_id is not None and process_session_id != active_console_session_id:
        signals.append("process_session_not_active_console_session")
        if process_is_active_visible_session:
            signals.append("process_session_is_active_visible_session")
            if process_session_is_wts_active:
                signals.append("process_session_is_wts_active")
        elif connect_state_query_ok is False:
            signals.append("active_visible_session_query_failed")
        else:
            signals.append("process_session_not_active_visible_session")
        if not process_is_active_visible_session and connect_state_query_ok is not False and not raw_can_attempt_real_control:
            status = HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION
            signals.append("raw_host_agent_health_not_ready_in_non_console_session")
        elif not raw_can_attempt_real_control:
            status = "child_unhealthy"
            signals.append("raw_host_agent_health_not_ready")
    elif _foreground_title_is_lock_screen(foreground_title):
        status = "locked_desktop"
        signals.append("foreground_title_lock_screen")
    elif desktop_name == "Winlogon":
        status = "uac_secure_desktop_active" if _foreground_title_is_uac(foreground_title) else "secure_desktop_active"
        signals.append("input_desktop_winlogon")
    elif _access_denied_secure_desktop(input_desktop=input_desktop, cursor_probe=cursor_probe, capture_probe=capture_probe):
        status = "uac_secure_desktop_active" if _foreground_title_is_uac(foreground_title) else "secure_desktop_active"
        signals.append("input_desktop_access_denied_with_cursor_or_capture_unavailable")
    elif "session_locked_or_secure_desktop" in raw_blocker_set:
        status = "secure_desktop_active"
        signals.append("raw_blocker_session_locked_or_secure_desktop")
    elif not raw_can_attempt_real_control:
        status = "child_unhealthy"
        signals.append("raw_host_agent_health_not_ready")
    else:
        signals.append("raw_host_agent_health_ready")

    return {
        "object_type": "HostAgentServiceStateClassification",
        "schema": "agentsight_host_agent_service_state_classification_v1",
        "service_status": status,
        "can_attempt_real_control": status == OK_ACTIVE_DEFAULT_DESKTOP,
        "signals": signals,
        "raw_can_attempt_real_control": bool(raw_can_attempt_real_control),
        "raw_control_blockers": list(raw_control_blockers),
        "foreground_title": foreground_title,
        "input_desktop_name": desktop_name or None,
        "input_desktop_error": input_desktop_error or None,
        "session_connect_state": connect_state_name,
        "process_is_active_visible_session": process_is_active_visible_session,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _boundary_facts(),
    }


def validate_service_state(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ServiceStateError("service state must be an object")
    if payload.get("object_type") != "AgentSightServiceState":
        raise ServiceStateError("service state object_type must be AgentSightServiceState")
    if payload.get("schema") != SERVICE_STATE_SCHEMA:
        raise ServiceStateError(f"service state schema must be {SERVICE_STATE_SCHEMA}")
    status = _validate_service_status(payload.get("service_status"))
    can_attempt = bool(payload.get("can_attempt_real_control"))
    expected_can_attempt = status == OK_ACTIVE_DEFAULT_DESKTOP
    if can_attempt != expected_can_attempt:
        raise ServiceStateError("can_attempt_real_control does not match service_status")
    _validate_input_accounting(payload)
    if not expected_can_attempt:
        _validate_blocker_details(payload, status)
    return dict(payload)


def validate_service_health(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ServiceStateError("service health must be an object")
    if payload.get("object_type") != "AgentSightServiceHealth":
        raise ServiceStateError("service health object_type must be AgentSightServiceHealth")
    if payload.get("schema") != SERVICE_HEALTH_SCHEMA:
        raise ServiceStateError(f"service health schema must be {SERVICE_HEALTH_SCHEMA}")
    status = _validate_service_status(payload.get("service_status"))
    can_attempt = bool(payload.get("can_attempt_real_control"))
    expected_can_attempt = status == OK_ACTIVE_DEFAULT_DESKTOP
    if can_attempt != expected_can_attempt:
        raise ServiceStateError("can_attempt_real_control does not match service_status")
    _validate_input_accounting(payload)
    if not expected_can_attempt:
        _validate_blocker_details(payload, status)
    return dict(payload)


def _validate_service_status(value: Any) -> str:
    if not isinstance(value, str) or value not in SERVICE_CONTROL_STATES:
        raise ServiceStateError(f"unknown service_status: {value!r}")
    return value


def _foreground_title_is_lock_screen(title: str) -> bool:
    normalized = title.strip().casefold()
    return normalized in {
        "windows 默认锁屏界面",
        "windows default lock screen",
        "default lock screen",
    }


def _foreground_title_is_uac(title: str) -> bool:
    normalized = title.strip().casefold()
    return normalized in {
        "用户账户控制",
        "用户帐户控制",
        "user account control",
    }


def _access_denied_secure_desktop(
    *,
    input_desktop: dict[str, Any],
    cursor_probe: dict[str, Any],
    capture_probe: dict[str, Any],
) -> bool:
    if input_desktop.get("opened"):
        return False
    text = str(input_desktop.get("error_text") or input_desktop.get("error") or "").casefold()
    access_denied = "access is denied" in text or "拒绝访问" in text
    cursor_unavailable = cursor_probe.get("ok") is False
    capture_unavailable = capture_probe.get("ok") is False
    return bool(access_denied and (cursor_unavailable or capture_unavailable))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _validate_input_accounting(payload: dict[str, Any]) -> None:
    if payload.get("host_input_sent") is not False:
        raise ServiceStateError("health/state models must not report sent host input")
    if payload.get("host_input_executed") is not False:
        raise ServiceStateError("health/state models must not report executed host input")
    if payload.get("host_sent_event_count") != 0:
        raise ServiceStateError("health/state models must report zero host input events")


def _validate_blocker_details(payload: dict[str, Any], status: str) -> None:
    blockers = payload.get("control_blockers")
    if blockers != [status]:
        raise ServiceStateError("blocked state must expose its service_status in control_blockers")
    details = payload.get("control_blocker_details")
    if not isinstance(details, list) or not details:
        raise ServiceStateError("blocked state must include explicit control_blocker_details")
    first = details[0]
    if not isinstance(first, dict) or first.get("code") != status or not first.get("reason"):
        raise ServiceStateError("blocked state must include a detail with code and reason")


def _blocker_detail(status: str) -> dict[str, Any]:
    return {
        "code": status,
        "reason": BLOCKER_REASONS[status],
        "can_attempt_real_control": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
    }


def _boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
