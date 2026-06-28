from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai_control.tray.state import (
    boundary_facts,
    clear_emergency_stop,
    default_discovery_file,
    load_operator_control_policy,
    load_tray_status,
    read_json_file,
    write_operator_control_policy,
    write_emergency_stop,
)


def request_host_agent_shutdown(discovery: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(discovery, dict) or not discovery.get("shutdown_url") or not discovery.get("token"):
        return {
            "shutdown_attempted": False,
            "shutdown_status": "discovery_missing_or_unauthenticated",
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    request = urllib.request.Request(
        str(discovery["shutdown_url"]),
        data=b"{}",
        headers={"Authorization": f"Bearer {discovery['token']}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return {
                "shutdown_attempted": True,
                "shutdown_status": "requested",
                "http_status": response.status,
                "response": parsed,
                "host_input_sent": False,
                "host_sent_event_count": 0,
            }
    except urllib.error.HTTPError as exc:
        return {
            "shutdown_attempted": True,
            "shutdown_status": "http_error",
            "http_status": exc.code,
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    except Exception as exc:
        return {
            "shutdown_attempted": True,
            "shutdown_status": "request_failed",
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }


def emergency_stop(reason: str, *, discovery_file: Path | None = None) -> dict[str, Any]:
    discovery_path = discovery_file or default_discovery_file()
    discovery = read_json_file(discovery_path)
    write_report = write_emergency_stop(reason=reason)
    shutdown = request_host_agent_shutdown(discovery)
    status = load_tray_status(discovery_file=discovery_path)
    return {
        "object_type": "AIControlTrayEmergencyStopReport",
        "schema": "ai_control_tray_emergency_stop_report_v1",
        "emergency_stop_status": "active",
        "write_report": write_report,
        "shutdown": shutdown,
        "tray_status": status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def clear_emergency() -> dict[str, Any]:
    clear_report = clear_emergency_stop()
    status = load_tray_status()
    return {
        "object_type": "AIControlTrayClearEmergencyStopReport",
        "schema": "ai_control_tray_clear_emergency_stop_report_v1",
        "clear_report": clear_report,
        "tray_status": status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def pause_ai_control(reason: str = "operator_paused_ai_control") -> dict[str, Any]:
    write_report = write_operator_control_policy(
        real_control_enabled=False,
        reason=reason,
        updated_by="operator",
    )
    status = load_tray_status()
    return {
        "object_type": "AIControlTrayPauseAIControlReport",
        "schema": "ai_control_tray_pause_ai_control_report_v1",
        "operator_control_status": "operator_control_paused",
        "write_report": write_report,
        "tray_status": status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def allow_ai_control(reason: str = "operator_allowed_ai_control") -> dict[str, Any]:
    write_report = write_operator_control_policy(
        real_control_enabled=True,
        reason=reason,
        updated_by="operator",
    )
    status = load_tray_status()
    return {
        "object_type": "AIControlTrayAllowAIControlReport",
        "schema": "ai_control_tray_allow_ai_control_report_v1",
        "operator_control_status": "real_control_allowed",
        "write_report": write_report,
        "policy": load_operator_control_policy(),
        "tray_status": status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def stop_ai_control(reason: str = "operator_requested_stop_ai_control_from_tray") -> dict[str, Any]:
    # Lazy import keeps the tray action surface from pulling lifecycle code into
    # import-time paths and avoids a circular import with session_supervisor.
    from ai_control.session_supervisor import stop_session_supervisor

    report = stop_session_supervisor(reason=reason, wait_seconds=2.0, force_after_timeout=True)
    return {
        "object_type": "AIControlTrayStopAIControlReport",
        "schema": "ai_control_tray_stop_ai_control_report_v1",
        "control_action": "stop_ai_control",
        "stop_semantics": {
            "emergency_stop": False,
            "operator_pause": False,
            "full_shutdown": True,
            "request_host_agent_shutdown": True,
            "request_tray_gui_close": True,
            "request_supervisor_exit": True,
        },
        "supervisor_stop": report,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
