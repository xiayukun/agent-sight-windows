from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from ai_control.caller_lock import caller_lock_status, default_caller_lock_file
from ai_control.operator_notifications import default_operator_notification_file, notification_status
from ai_control.operator_workflow import workflow_status
from ai_control.prompt_inbox import default_prompt_inbox_file, prompt_inbox_status
from ai_control.runtime_platform import is_windows


TRAY_STATUS_SCHEMA = "ai_control_tray_status_v1"
TRAY_CONFIG_SCHEMA = "ai_control_tray_config_v1"
TRAY_POLICY_SCHEMA = "ai_control_tray_policy_v1"
OPERATOR_CONTROL_POLICY_SCHEMA = "ai_control_operator_control_policy_v1"
EMERGENCY_STOP_SCHEMA = "ai_control_emergency_stop_v1"


def default_agent_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control"
    return Path.home() / "AppData" / "Local" / "ai-control"


def default_discovery_file() -> Path:
    return default_agent_dir() / "host-agent.json"


def default_service_state_file() -> Path:
    return default_agent_dir() / "service-state.json"


def default_session_supervisor_state_file() -> Path:
    return default_agent_dir() / "session-supervisor-state.json"


def default_watchdog_stop_file() -> Path:
    return default_agent_dir() / "host-agent-watchdog.stop"


def default_emergency_stop_file() -> Path:
    return default_agent_dir() / "emergency-stop.json"


def default_tray_config_file() -> Path:
    return default_agent_dir() / "tray-config.jsonc"


def default_tray_policy_file() -> Path:
    return default_tray_config_file()


def default_operator_control_policy_file() -> Path:
    return default_agent_dir() / "operator-control-policy.json"


def default_evidence_root() -> Path:
    return default_agent_dir() / "runs_host_agent"


def _is_windows() -> bool:
    return is_windows()


def boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def default_recording_policy() -> dict[str, Any]:
    return {
        "object_type": "AIControlTrayConfig",
        "schema": TRAY_CONFIG_SCHEMA,
        "legacy_schema": TRAY_POLICY_SCHEMA,
        "config_role": "human_visible_recording_and_timeline_settings",
        "continuous_recording_enabled": False,
        "retention_days": 30,
        "max_storage_mb": 5120,
        "min_free_disk_mb": 1024,
        "recording": {
            "idle_capture": {
                "enabled": False,
                "fps": 1.0,
            },
            "action_capture": {
                "enabled": True,
                "capture_pre_action_frame": True,
                "capture_post_action_frames": True,
                "post_action_fps": 10,
                "post_action_duration_ms": 10000,
                "max_post_action_frames": 100,
            },
        },
    }


def _strip_jsonc_comments(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            result.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            result.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if ch == "/" and nxt == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def read_jsonc_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(_strip_jsonc_comments(path.read_text(encoding="utf-8")))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def normalize_recording_policy(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = default_recording_policy()
    if not isinstance(payload, dict):
        return policy
    for key in (
        "continuous_recording_enabled",
        "retention_days",
        "max_storage_mb",
        "min_free_disk_mb",
    ):
        if key in payload:
            policy[key] = payload[key]
    if "retention_days_default" in payload and "retention_days" not in payload:
        policy["retention_days"] = payload["retention_days_default"]
    for section in ("recording",):
        if isinstance(payload.get(section), dict):
            merged = dict(policy.get(section) or {})
            for subkey, value in payload[section].items():
                if subkey in {"post_observe_defaults", "segment"}:
                    continue
                if isinstance(value, dict) and isinstance(merged.get(subkey), dict):
                    child = dict(merged[subkey])
                    child.pop("notes", None)
                    child.update(value)
                    child.pop("notes", None)
                    merged[subkey] = child
                else:
                    merged[subkey] = value
            policy[section] = merged
    idle = policy.get("recording", {}).get("idle_capture", {})
    if isinstance(idle, dict):
        if "fps" not in idle and "interval_ms" in idle:
            try:
                interval = float(idle.get("interval_ms"))
            except (TypeError, ValueError):
                interval = 1000.0
            if interval > 0:
                idle["fps"] = round(1000.0 / interval, 3)
        idle.pop("interval_ms", None)
        idle["fps"] = _coerce_float(idle.get("fps"), default=1.0, minimum=0.1, maximum=60.0)
    recording = policy.get("recording")
    if isinstance(recording, dict):
        recording.pop("segment", None)
    policy.pop("daily_segment_boundary_local_time", None)
    return policy


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _coerce_time_hhmm(value: Any, default: str = "00:00") -> str:
    if isinstance(value, str):
        parts = value.strip().split(":")
        if len(parts) == 2:
            try:
                hour = int(parts[0])
                minute = int(parts[1])
            except ValueError:
                return default
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
    return default


def _coerce_segment_storage_format(value: Any) -> str:
    return "mkv_vfr"


def _coerce_segment_bucket_granularity(value: Any) -> str:
    normalized = str(value or "hourly").strip().lower()
    if normalized in {"hour", "hourly"}:
        return "hourly"
    return "daily"


def _coerce_image_encoding(value: Any) -> str:
    return "ffv1"


def apply_recording_policy_settings(settings: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_tray_config_file()
    payload = normalize_recording_policy(read_jsonc_file(config_path))
    recording = payload.setdefault("recording", {})
    idle = recording.setdefault("idle_capture", {})
    action = recording.setdefault("action_capture", {})
    recording.pop("segment", None)

    if "continuous_recording_enabled" in settings:
        enabled = _coerce_bool(settings["continuous_recording_enabled"], bool(payload.get("continuous_recording_enabled")))
        payload["continuous_recording_enabled"] = enabled
        idle["enabled"] = enabled
    if "idle_fps" in settings:
        idle["fps"] = _coerce_float(settings["idle_fps"], default=1.0, minimum=0.1, maximum=60.0)
    elif "idle_interval_ms" in settings:
        interval = _coerce_int(settings["idle_interval_ms"], default=1000, minimum=100, maximum=60000)
        idle["fps"] = round(1000.0 / float(interval), 3)
    if "action_capture_enabled" in settings:
        action["enabled"] = _coerce_bool(settings["action_capture_enabled"], bool(action.get("enabled", True)))
    if "capture_pre_action_frame" in settings:
        action["capture_pre_action_frame"] = _coerce_bool(settings["capture_pre_action_frame"], bool(action.get("capture_pre_action_frame", True)))
    if "capture_post_action_frames" in settings:
        action["capture_post_action_frames"] = _coerce_bool(settings["capture_post_action_frames"], bool(action.get("capture_post_action_frames", True)))
    if "post_action_fps" in settings:
        action["post_action_fps"] = _coerce_int(settings["post_action_fps"], default=10, minimum=1, maximum=60)
    if "post_action_duration_ms" in settings:
        action["post_action_duration_ms"] = _coerce_int(settings["post_action_duration_ms"], default=10000, minimum=1, maximum=60000)
    if "max_post_action_frames" in settings:
        action["max_post_action_frames"] = _coerce_int(settings["max_post_action_frames"], default=100, minimum=1, maximum=1000)
    if "retention_days" in settings:
        payload["retention_days"] = _coerce_int(settings["retention_days"], default=30, minimum=1, maximum=3650)
    if "max_storage_mb" in settings:
        payload["max_storage_mb"] = _coerce_int(settings["max_storage_mb"], default=5120, minimum=256, maximum=1048576)
    if "min_free_disk_mb" in settings:
        payload["min_free_disk_mb"] = _coerce_int(settings["min_free_disk_mb"], default=1024, minimum=256, maximum=1048576)
    payload.pop("daily_segment_boundary_local_time", None)
    payload["updated_at_ms"] = int(time.time() * 1000)
    payload["updated_by"] = "tray_gui_settings"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "object_type": "AIControlTrayConfigUpdateReport",
        "schema": TRAY_CONFIG_SCHEMA,
        "updated": True,
        "config_file": str(config_path),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def write_default_tray_config_if_missing(path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_tray_config_file()
    existed = config_path.exists()
    if existed:
        payload = normalize_recording_policy(read_jsonc_file(config_path))
    else:
        payload = default_recording_policy()
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload["config_file"] = str(config_path)
    payload["created_now"] = not existed
    return payload


def set_recording_policy_flag(flag: str, enabled: bool, *, path: Path | None = None) -> dict[str, Any]:
    config_path = path or default_tray_config_file()
    payload = normalize_recording_policy(read_jsonc_file(config_path))
    normalized_enabled = bool(enabled)
    if flag == "continuous_recording_enabled":
        payload["continuous_recording_enabled"] = normalized_enabled
        payload["recording"]["idle_capture"]["enabled"] = normalized_enabled
    elif flag == "action_capture_enabled":
        payload["recording"]["action_capture"]["enabled"] = normalized_enabled
    elif flag == "capture_pre_action_frame":
        payload["recording"]["action_capture"]["capture_pre_action_frame"] = normalized_enabled
    elif flag == "capture_post_action_frames":
        payload["recording"]["action_capture"]["capture_post_action_frames"] = normalized_enabled
    else:
        return {
            "object_type": "AIControlTrayConfigUpdateReport",
            "schema": TRAY_CONFIG_SCHEMA,
            "updated": False,
            "reason": "unknown_recording_policy_flag",
            "flag": flag,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    payload["updated_at_ms"] = int(time.time() * 1000)
    payload["updated_by"] = "tray_gui_menu"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "object_type": "AIControlTrayConfigUpdateReport",
        "schema": TRAY_CONFIG_SCHEMA,
        "updated": True,
        "flag": flag,
        "enabled": normalized_enabled,
        "config_file": str(config_path),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def toggle_recording_policy_flag(flag: str, *, path: Path | None = None) -> dict[str, Any]:
    payload = normalize_recording_policy(read_jsonc_file(path or default_tray_config_file()))
    current = recording_policy_flag_enabled(payload, flag)
    return set_recording_policy_flag(flag, not current, path=path)


def recording_policy_flag_enabled(payload: dict[str, Any] | None, flag: str) -> bool:
    policy = normalize_recording_policy(payload)
    if flag == "continuous_recording_enabled":
        return bool(policy.get("continuous_recording_enabled"))
    if flag == "action_capture_enabled":
        return bool(((policy.get("recording") or {}).get("action_capture") or {}).get("enabled"))
    if flag == "capture_pre_action_frame":
        return bool(((policy.get("recording") or {}).get("action_capture") or {}).get("capture_pre_action_frame"))
    if flag == "capture_post_action_frames":
        return bool(((policy.get("recording") or {}).get("action_capture") or {}).get("capture_post_action_frames"))
    return False


def build_operator_control_policy(
    *,
    real_control_enabled: bool = True,
    reason: str | None = None,
    updated_by: str = "default",
    now_ms: int | None = None,
) -> dict[str, Any]:
    enabled = bool(real_control_enabled)
    return {
        "object_type": "AIControlOperatorControlPolicy",
        "schema": OPERATOR_CONTROL_POLICY_SCHEMA,
        "policy_role": "human_visible_ai_real_control_permission",
        "policy_status": "real_control_allowed" if enabled else "operator_control_paused",
        "real_control_enabled": enabled,
        "observation_requests_allowed": enabled,
        "mouse_input_enabled": enabled,
        "keyboard_input_enabled": enabled,
        "effect": {
            "deny_future_real_control": not enabled,
            "request_host_agent_shutdown": False,
            "write_watchdog_stop_marker": False,
            "uninstall_host_agent": False,
        },
        "reason": reason or ("operator_allowed_ai_control" if enabled else "operator_paused_ai_control"),
        "updated_by": updated_by,
        "timestamp_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def default_operator_control_policy() -> dict[str, Any]:
    return build_operator_control_policy(real_control_enabled=True, reason="default_allow")


def load_operator_control_policy(path: Path | None = None) -> dict[str, Any]:
    policy_path = path or default_operator_control_policy_file()
    payload = read_json_file(policy_path)
    if not isinstance(payload, dict) or payload.get("schema") != OPERATOR_CONTROL_POLICY_SCHEMA:
        policy = default_operator_control_policy()
        policy["policy_file"] = str(policy_path)
        return policy
    enabled = bool(payload.get("real_control_enabled"))
    normalized = build_operator_control_policy(
        real_control_enabled=enabled,
        reason=str(payload.get("reason") or ("operator_allowed_ai_control" if enabled else "operator_paused_ai_control")),
        updated_by=str(payload.get("updated_by") or "operator"),
        now_ms=int(payload.get("timestamp_ms") or time.time() * 1000),
    )
    normalized["policy_file"] = str(policy_path)
    return normalized


def write_operator_control_policy(
    *,
    real_control_enabled: bool,
    reason: str,
    updated_by: str = "operator",
    operator_control_policy_file: Path | None = None,
) -> dict[str, Any]:
    policy_path = operator_control_policy_file or default_operator_control_policy_file()
    payload = build_operator_control_policy(
        real_control_enabled=real_control_enabled,
        reason=reason,
        updated_by=updated_by,
    )
    payload["policy_file"] = str(policy_path)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "object_type": "AIControlOperatorControlPolicyWriteReport",
        "schema": "ai_control_operator_control_policy_write_report_v1",
        "operator_control_policy_file": str(policy_path),
        "policy_written": True,
        "policy": payload,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def operator_control_allows_real_control(path: Path | None = None) -> bool:
    return bool(load_operator_control_policy(path).get("real_control_enabled"))


def read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _service_state_from_session_supervisor_state(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    host_agent = payload.get("host_agent")
    probe = host_agent.get("probe") if isinstance(host_agent, dict) else None
    health = probe.get("health") if isinstance(probe, dict) else None
    if isinstance(health, dict):
        service_status = health.get("service_status")
        if service_status:
            return {
                "object_type": "AIControlServiceStateProjection",
                "schema": "ai_control_service_state_projection_v1",
                "service_status": service_status,
                "can_attempt_real_control": bool(health.get("can_attempt_real_control")),
                "control_blockers": list(health.get("control_blockers") or []),
                "source": "session_supervisor_state.host_agent_health",
            }
        service_state = health.get("service_state")
        if isinstance(service_state, dict):
            return service_state
        service_health = health.get("service_health")
        if isinstance(service_health, dict):
            return {
                "object_type": "AIControlServiceStateProjection",
                "schema": "ai_control_service_state_projection_v1",
                "service_status": service_health.get("service_status"),
                "can_attempt_real_control": bool(service_health.get("can_attempt_real_control")),
                "control_blockers": list(service_health.get("control_blockers") or []),
                "source": "session_supervisor_state.service_health",
            }
    supervisor_projection = probe.get("service_state") if isinstance(probe, dict) else None
    return supervisor_projection if isinstance(supervisor_projection, dict) else None


def load_service_state_for_tray(service_state_file: Path | None = None) -> tuple[dict[str, Any] | None, str]:
    service_path = service_state_file or default_service_state_file()
    service_state = read_json_file(service_path)
    if isinstance(service_state, dict):
        return service_state, str(service_path)
    supervisor_path = default_session_supervisor_state_file()
    fallback = _service_state_from_session_supervisor_state(read_json_file(supervisor_path))
    if isinstance(fallback, dict):
        fallback.setdefault("service_state_file", str(supervisor_path))
        fallback.setdefault("service_state_source", "session_supervisor_state")
        return fallback, str(supervisor_path)
    return None, str(service_path)


def redact_token(token: Any) -> dict[str, Any]:
    if not isinstance(token, str) or not token:
        return {"present": False}
    return {
        "present": True,
        "length": len(token),
        "sha256_12": hashlib.sha256(token.encode("utf-8")).hexdigest()[:12],
        "redacted": True,
    }


def redact_discovery(discovery: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(discovery, dict):
        return {"status": "missing"}
    safe_keys = [
        "object_type",
        "schema",
        "url",
        "health_url",
        "observe_url",
        "click_url",
        "mouse_url",
        "input_url",
        "p0_url",
        "shutdown_url",
        "service_state_file",
        "host",
        "port",
        "pid",
        "process_session_id",
        "active_console_session_id",
        "auth_header",
        "runs_dir",
        "armed_for_real_input",
    ]
    redacted = {key: discovery.get(key) for key in safe_keys if key in discovery}
    redacted["token"] = redact_token(discovery.get("token"))
    redacted["status"] = "present"
    redacted["omitted_fields"] = [key for key in discovery if key not in set(safe_keys) | {"token"}]
    return redacted


def emergency_stop_active(path: Path | None = None) -> bool:
    return (path or default_emergency_stop_file()).exists()


def build_emergency_stop_payload(reason: str, *, now_ms: int | None = None) -> dict[str, Any]:
    return {
        "object_type": "AIControlEmergencyStop",
        "schema": EMERGENCY_STOP_SCHEMA,
        "active": True,
        "reason": reason or "operator_requested_emergency_stop",
        "timestamp_ms": int(now_ms if now_ms is not None else time.time() * 1000),
        "effect": {
            "deny_future_real_control": True,
            "request_host_agent_shutdown": True,
            "write_watchdog_stop_marker": True,
            "uninstall_host_agent": False,
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def write_emergency_stop(
    *,
    reason: str,
    emergency_stop_file: Path | None = None,
    watchdog_stop_file: Path | None = None,
) -> dict[str, Any]:
    emergency_path = emergency_stop_file or default_emergency_stop_file()
    watchdog_path = watchdog_stop_file or default_watchdog_stop_file()
    payload = build_emergency_stop_payload(reason)
    emergency_path.parent.mkdir(parents=True, exist_ok=True)
    emergency_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    watchdog_path.parent.mkdir(parents=True, exist_ok=True)
    watchdog_path.write_text("AI-Control emergency stop active. Clear explicitly to allow restart.\n", encoding="utf-8")
    return {
        "object_type": "AIControlEmergencyStopWriteReport",
        "schema": "ai_control_emergency_stop_write_report_v1",
        "emergency_stop_file": str(emergency_path),
        "watchdog_stop_file": str(watchdog_path),
        "emergency_stop_written": True,
        "watchdog_stop_marker_written": True,
        "payload": payload,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def clear_emergency_stop(
    *,
    emergency_stop_file: Path | None = None,
    watchdog_stop_file: Path | None = None,
) -> dict[str, Any]:
    emergency_path = emergency_stop_file or default_emergency_stop_file()
    watchdog_path = watchdog_stop_file or default_watchdog_stop_file()
    emergency_existed = emergency_path.exists()
    watchdog_existed = watchdog_path.exists()
    emergency_path.unlink(missing_ok=True)
    watchdog_path.unlink(missing_ok=True)
    return {
        "object_type": "AIControlEmergencyStopClearReport",
        "schema": "ai_control_emergency_stop_clear_report_v1",
        "emergency_stop_file": str(emergency_path),
        "watchdog_stop_file": str(watchdog_path),
        "emergency_stop_removed": emergency_existed,
        "watchdog_stop_marker_removed": watchdog_existed,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def load_tray_status(
    *,
    discovery_file: Path | None = None,
    service_state_file: Path | None = None,
    emergency_stop_file: Path | None = None,
    tray_policy_file: Path | None = None,
    operator_control_policy_file: Path | None = None,
) -> dict[str, Any]:
    discovery_path = discovery_file or default_discovery_file()
    service_path = service_state_file or default_service_state_file()
    emergency_path = emergency_stop_file or default_emergency_stop_file()
    policy_path = tray_policy_file or default_tray_config_file()
    operator_policy_path = operator_control_policy_file or default_operator_control_policy_file()
    discovery = read_json_file(discovery_path)
    service_state, service_source_path = load_service_state_for_tray(service_path)
    policy = normalize_recording_policy(read_jsonc_file(policy_path) or read_json_file(policy_path))
    policy["config_file"] = str(policy_path)
    operator_policy = load_operator_control_policy(operator_policy_path)
    emergency_payload = read_json_file(emergency_path)
    emergency_active = emergency_path.exists()
    service_status = service_state.get("service_status") if isinstance(service_state, dict) else None
    service_allows = bool(service_state.get("can_attempt_real_control")) if isinstance(service_state, dict) else False
    operator_allows = bool(operator_policy.get("real_control_enabled"))
    discovery_present = isinstance(discovery, dict)
    windows = _is_windows()
    if emergency_active:
        tray_status = "emergency_stopped"
        can_attempt = False
        blockers = ["kill_switch_active"]
    elif not operator_allows:
        tray_status = "operator_control_paused"
        can_attempt = False
        blockers = ["operator_control_paused"]
    elif discovery_present and service_status == "ok_active_default_desktop" and service_allows:
        tray_status = "ready"
        can_attempt = True
        blockers = []
    elif discovery_present:
        tray_status = "blocked"
        can_attempt = False
        blockers = (
            list(service_state.get("control_blockers") or [service_status or "service_state_unavailable"])
            if isinstance(service_state, dict)
            else ["service_state_unavailable"]
        )
    else:
        tray_status = "discovery_missing"
        can_attempt = False
        blockers = ["discovery_missing"]
    return {
        "object_type": "AIControlTrayStatus",
        "schema": TRAY_STATUS_SCHEMA,
        "tray_role": "human_visible_control_surface",
        "tray_status": tray_status,
        "can_attempt_real_control": can_attempt,
        "control_blockers": blockers,
        "paths": {
            "agent_dir": str(default_agent_dir()),
            "discovery_file": str(discovery_path),
            "service_state_file": service_source_path,
            "emergency_stop_file": str(emergency_path),
            "watchdog_stop_file": str(default_watchdog_stop_file()),
            "tray_config_file": str(policy_path),
            "tray_policy_file": str(policy_path),
            "operator_control_policy_file": str(operator_policy_path),
            "evidence_root": str(default_evidence_root()),
            "caller_lock_file": str(default_caller_lock_file()),
            "prompt_inbox_file": str(default_prompt_inbox_file()),
            "operator_notification_file": str(default_operator_notification_file()),
        },
        "host_agent": {
            "discovery_present": discovery_present,
            "discovery": redact_discovery(discovery),
            "pid": discovery.get("pid") if isinstance(discovery, dict) else None,
        },
        "service": {
            "state_present": isinstance(service_state, dict),
            "service_status": service_status,
            "can_attempt_real_control": service_allows,
            "control_blockers": list(service_state.get("control_blockers") or []) if isinstance(service_state, dict) else [],
        },
        "emergency_stop": {
            "active": emergency_active,
            "payload": emergency_payload,
            "human_visible_control": True,
        },
        "recording_policy": policy,
        "operator_control_policy": operator_policy,
        "caller_lock": caller_lock_status(),
        "prompt_inbox": prompt_inbox_status(),
        "operator_notifications": notification_status(),
        "operator_workflow": workflow_status(),
        "controls": {
            "can_start_host_agent": True,
            "can_stop_host_agent": discovery_present,
            "can_emergency_stop": True,
            "can_run_physical_emergency_hotkey_monitor": windows,
            "physical_emergency_hotkey": {
                "default_chord": "Ctrl+Alt+Shift+Esc",
                "entrypoint": "ai-control-tray emergency-hotkey run",
                "describe_entrypoint": "ai-control-tray emergency-hotkey describe",
                "tray_gui_starts_monitor_by_default": windows,
                "tray_gui_disable_flag": "--no-hotkey",
                "ignore_injected_keyboard_events": True,
                "not_started_by_status": True,
            },
            "can_clear_emergency_stop": emergency_active,
            "can_pause_ai_real_control": operator_allows,
            "can_allow_ai_real_control": not operator_allows,
            "can_open_evidence_folder": False,
            "can_open_agent_data_folder": False,
            "can_open_timeline": True,
            "can_open_operation_log": True,
            "can_open_recording_settings": True,
            "recording_policy_editable": True,
            "tray_icon_gui_available": windows,
            "tray_icon_gui_entrypoint": "ai-control-tray-gui",
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
