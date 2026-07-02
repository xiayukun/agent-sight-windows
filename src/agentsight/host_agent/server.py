from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from subprocess import DEVNULL, Popen
from typing import Any
from ctypes import wintypes


class _AgentSightThreadingHTTPServer(ThreadingHTTPServer):
    # Python's http.server enables SO_REUSEADDR by default. On Windows this can
    # allow two localhost listeners to bind the same host/port, leaving clients
    # nondeterministically talking to a stale HostAgent with a different token.
    allow_reuse_address = False

    def server_bind(self) -> None:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

from agentsight.caller_lock import (
    caller_id_from_request,
    caller_lock_status,
    check_single_caller_lock,
    default_caller_lock_file,
    enforce_single_caller_lock,
)
from agentsight.channels.key_text import key_text_summary, validate_key_text_stream
from agentsight.channels.keyboard_events import keyboard_action_summary
from agentsight.channels.pointer_events import is_mouse_action, mouse_action_points, mouse_action_summary
from agentsight.diagnostics.input_smoke import (
    DEFAULT_INPUT_CHANNEL_REF,
    DEFAULT_OBSERVATION_CHANNEL_REF,
    build_manual_windows_input_adapter,
)
from agentsight.channels.windows_software.observation import WindowsSoftwareObservationChannel
from agentsight.diagnostics.p0_real_input_closed_loop import (
    ARMING_REF,
    CONSENT_REF,
    P0_ARMING_FLAG,
    WindowsHostProbe,
    _media_content_diagnostics,
    run_p0_real_input_closed_loop_smoke,
)
from agentsight.host_agent.service_state import (
    build_service_health,
    project_host_agent_health_to_service_state,
    project_supervisor_child_health_to_service_state,
)
from agentsight.protocol.schemas import SchemaError, validate_post_observe, validate_request
from agentsight.segments.decoder import (
    decode_segment_region_to_image_content,
    query_segment_change_index,
    query_segment_decoder_near_time,
    query_segment_review_clip,
    query_segment_timeline_diff,
)
from agentsight.tray.state import (
    default_tray_config_file,
    emergency_stop_active,
    load_operator_control_policy,
    normalize_recording_policy,
    read_jsonc_file,
)
from agentsight.tray.viewers import append_operation_log, public_operation_log_entry
from agentsight.visual_memory.post_observe import build_post_action_observation_window, should_stop_post_observe_sampling


def build_host_agent_health_report() -> dict[str, Any]:
    probe = WindowsHostProbe()
    session = probe.session_report()
    station = probe.window_station_report()
    input_desktop = probe.ensure_input_desktop()
    foreground = probe.foreground_window_info()
    cursor_probe = _cursor_probe()
    capture_probe = _capture_probe()
    metrics = probe.system_metrics()
    active_interactive_session = _active_interactive_session(session)
    visible_station = station.get("window_station_name") == "WinSta0"
    input_desktop_ready = bool(input_desktop.get("opened") and input_desktop.get("desktop_name") == "Default")
    cursor_ready = bool(cursor_probe.get("ok"))
    capture_ready = bool(capture_probe.get("ok"))
    kill_switch_active = emergency_stop_active()
    operator_control_policy = load_operator_control_policy()
    operator_allows_real_control = bool(operator_control_policy.get("real_control_enabled"))
    raw_can_attempt_real_control = bool(
        active_interactive_session
        and visible_station
        and input_desktop_ready
        and cursor_ready
        and capture_ready
        and not kill_switch_active
        and operator_allows_real_control
    )
    raw_control_blockers = _control_blockers(
        active_interactive_session=active_interactive_session,
        visible_station=visible_station,
        input_desktop=input_desktop,
        cursor_probe=cursor_probe,
        capture_probe=capture_probe,
        kill_switch_active=kill_switch_active,
        operator_allows_real_control=operator_allows_real_control,
    )
    return _build_host_agent_health_payload(
        session=session,
        station=station,
        input_desktop=input_desktop,
        foreground_window=foreground,
        virtual_screen_metrics=metrics,
        cursor_probe=cursor_probe,
        capture_probe=capture_probe,
        raw_can_attempt_real_control=raw_can_attempt_real_control,
        raw_control_blockers=raw_control_blockers,
        caller_lock=caller_lock_status(),
        operator_control_policy=operator_control_policy,
    )


def _build_host_agent_health_payload(
    *,
    session: dict[str, Any],
    station: dict[str, Any],
    input_desktop: dict[str, Any],
    foreground_window: dict[str, Any],
    virtual_screen_metrics: dict[str, Any],
    cursor_probe: dict[str, Any],
    capture_probe: dict[str, Any],
    raw_can_attempt_real_control: bool,
    raw_control_blockers: list[str],
    caller_lock: dict[str, Any] | None = None,
    operator_control_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    service_state = project_host_agent_health_to_service_state(
        session=session,
        station=station,
        input_desktop=input_desktop,
        foreground_window=foreground_window,
        cursor_probe=cursor_probe,
        capture_probe=capture_probe,
        raw_can_attempt_real_control=raw_can_attempt_real_control,
        raw_control_blockers=raw_control_blockers,
    )
    service_health = build_service_health(service_state)
    service_control_blockers = list(service_health.get("control_blockers") or [])
    can_attempt_real_control = bool(raw_can_attempt_real_control and service_health.get("can_attempt_real_control"))
    return {
        "object_type": "AgentSightHostAgentHealth",
        "schema": "agentsight_host_agent_health_v1",
        "agent_role": "visible_interactive_session_host",
        "session": session,
        "window_station": station,
        "input_desktop": input_desktop,
        "foreground_window": foreground_window,
        "virtual_screen_metrics": virtual_screen_metrics,
        "cursor_probe": cursor_probe,
        "capture_probe": capture_probe,
        "raw_can_attempt_real_control": raw_can_attempt_real_control,
        "raw_control_blockers": list(raw_control_blockers),
        "can_attempt_real_control": can_attempt_real_control,
        "control_blockers": _merge_control_blockers(raw_control_blockers, service_control_blockers),
        "service_state": service_state,
        "service_health": service_health,
        "service_status": service_health.get("service_status"),
        "caller_lock": caller_lock or caller_lock_status(),
        "operator_control_policy": operator_control_policy or load_operator_control_policy(),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": {
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the AgentSight local host agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", default="runs_host_agent")
    parser.add_argument("--arm-real-input", action="store_true")
    parser.add_argument("--discovery-file")
    parser.add_argument("--token")
    parser.add_argument("--watchdog", action="store_true")
    parser.add_argument("--watchdog-interval-seconds", type=float, default=5.0)
    parser.add_argument("--watchdog-once", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(_argv_or_frozen_agent_default(sys.argv[1:]))

    if args.watchdog:
        return run_host_agent_watchdog(
            host=args.host,
            port=args.port,
            runs_dir=args.runs_dir,
            arm_real_input=args.arm_real_input,
            discovery_file=Path(args.discovery_file) if args.discovery_file else default_discovery_file(),
            interval_seconds=args.watchdog_interval_seconds,
            once=args.watchdog_once,
        )

    token = args.token or secrets.token_urlsafe(32)
    handler = _handler_class(runs_dir=args.runs_dir, arm_real_input=args.arm_real_input, token=token)
    server = _bind_server(args.host, args.port, handler)
    host, port = server.server_address[:2]
    discovery_file = Path(args.discovery_file) if args.discovery_file else default_discovery_file()
    discovery = write_discovery_file(
        discovery_file,
        host=str(host),
        port=int(port),
        token=token,
        runs_dir=args.runs_dir,
        armed=args.arm_real_input,
    )
    _write_last_agent_report(
        {
            "object_type": "AgentSightHostAgentRuntimeReport",
            "schema": "agentsight_host_agent_runtime_report_v1",
            "agent_status": "started",
            "url": discovery["url"],
            "armed": args.arm_real_input,
            "discovery_file": str(discovery_file),
            "pid": os.getpid(),
        }
    )
    default_agent_error_file().unlink(missing_ok=True)
    idle_stop = threading.Event()
    idle_thread = _start_host_agent_idle_capture_loop(
        runs_dir=args.runs_dir,
        stop_event=idle_stop,
    )
    print(
        json.dumps(
            {
                "agent": "agentsight-host-agent",
                "url": discovery["url"],
                "armed": args.arm_real_input,
                "discovery_file": str(discovery_file),
            },
            ensure_ascii=False,
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        idle_stop.set()
        if idle_thread is not None:
            idle_thread.join(timeout=2.0)
        server.server_close()
    return 0


def _argv_or_frozen_agent_default(argv: list[str]) -> list[str]:
    if argv:
        return argv
    if getattr(sys, "frozen", False):
        return ["--arm-real-input"]
    return argv


def run_host_agent_watchdog(
    *,
    host: str,
    port: int,
    runs_dir: str,
    arm_real_input: bool,
    discovery_file: Path,
    interval_seconds: float,
    once: bool = False,
) -> int:
    default_watchdog_stop_file().unlink(missing_ok=True)
    cycle = 0
    last_child: Popen[Any] | None = None
    while True:
        cycle += 1
        probe = _watchdog_probe(discovery_file)
        action: dict[str, Any] = {"action": "none"}
        if probe.get("watchdog_probe_status") == "unified_session_supervisor_enabled":
            report = {
                "object_type": "AgentSightHostAgentWatchdogReport",
                "schema": "agentsight_host_agent_watchdog_v1",
                "watchdog_status": "stopped_by_unified_session_supervisor",
                "watchdog_pid": os.getpid(),
                "cycle": cycle,
                "discovery_file": str(discovery_file),
                "stop_file": str(default_watchdog_stop_file()),
                "last_probe": probe,
                "last_action": action,
                "last_child_pid": last_child.pid if last_child else None,
            }
            _write_last_watchdog_report(report)
            if once:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        if _watchdog_should_start_agent(probe):
            command = _watchdog_child_agent_command(
                host=host,
                port=port,
                runs_dir=runs_dir,
                arm_real_input=arm_real_input,
                discovery_file=discovery_file,
            )
            try:
                last_child = Popen(
                    command,
                    stdin=DEVNULL,
                    stdout=DEVNULL,
                    stderr=DEVNULL,
                    close_fds=True,
                )
                action = {
                    "action": "start_agent",
                    "child_pid": last_child.pid,
                    "command": _redacted_process_command(command),
                }
            except Exception as exc:
                action = {
                    "action": "start_agent_failed",
                    "error": str(exc),
                    "command": _redacted_process_command(command),
                }
        report = {
            "object_type": "AgentSightHostAgentWatchdogReport",
            "schema": "agentsight_host_agent_watchdog_v1",
            "watchdog_status": "running",
            "watchdog_pid": os.getpid(),
            "cycle": cycle,
            "discovery_file": str(discovery_file),
            "stop_file": str(default_watchdog_stop_file()),
            "last_probe": probe,
            "last_action": action,
            "last_child_pid": last_child.pid if last_child else None,
        }
        service_state = _watchdog_service_state_from_probe(
            probe=probe,
            action=action,
            watchdog_pid=os.getpid(),
            cycle=cycle,
            discovery_file=discovery_file,
            last_child_pid=last_child.pid if last_child else None,
        )
        service_health = build_service_health(service_state)
        report["service_state"] = service_state
        report["service_health"] = service_health
        report["service_status"] = service_state.get("service_status")
        report["service_state_file"] = str(default_service_state_file())
        report["service_state_write"] = _write_service_state_file(service_state)
        _write_last_watchdog_report(report)
        if once:
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if action.get("action") != "start_agent_failed" else 5
        sleep_seconds = max(0.5, float(interval_seconds or 5.0))
        deadline = time.time() + sleep_seconds
        while time.time() < deadline:
            if default_watchdog_stop_file().exists():
                _write_last_watchdog_report(
                    {
                        **report,
                        "watchdog_status": "stopped_by_stop_file",
                        "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
                return 0
            time.sleep(min(0.25, max(0.0, deadline - time.time())))


def _watchdog_probe(
    discovery_file: Path,
    *,
    suppress_when_unified_supervisor_enabled: bool = True,
    respect_watchdog_stop_file: bool = True,
) -> dict[str, Any]:
    if suppress_when_unified_supervisor_enabled and _unified_session_supervisor_enabled_file().exists():
        return {
            "watchdog_probe_status": "unified_session_supervisor_enabled",
            "unified_supervisor_enabled_file": str(_unified_session_supervisor_enabled_file()),
            "agent_running": None,
            "restart_recommended": False,
        }
    if respect_watchdog_stop_file and default_watchdog_stop_file().exists():
        return {"watchdog_probe_status": "stop_requested"}
    discovery = _read_json_file(discovery_file)
    if not discovery:
        return {
            "watchdog_probe_status": "discovery_missing",
            "discovery_file": str(discovery_file),
            "agent_running": False,
            "restart_recommended": True,
        }
    pid = discovery.get("pid")
    pid_running = _pid_running(pid)
    if pid_running and not _pid_looks_like_host_agent(pid, discovery):
        pid_running = False
    if not pid_running:
        return {
            "watchdog_probe_status": "stale_discovery_pid_not_running",
            "discovery": _redact_token(discovery),
            "agent_running": False,
            "restart_recommended": True,
            "stale_discovery_reason": "pid_not_running_or_not_host_agent",
        }
    health = _watchdog_request_health(discovery)
    if health.get("request_failed"):
        return {
            "watchdog_probe_status": "health_unreachable",
            "discovery": _redact_token(discovery),
            "agent_running": True,
            "health": health,
            "restart_recommended": True,
        }
    return {
        "watchdog_probe_status": "agent_responding",
        "discovery": _redact_token(discovery),
        "agent_running": True,
        "health": health,
        "can_attempt_real_control": bool(health.get("can_attempt_real_control")),
        "control_blockers": health.get("control_blockers"),
        "restart_recommended": False,
    }


def _watchdog_should_start_agent(probe: dict[str, Any]) -> bool:
    return bool(probe.get("restart_recommended")) and probe.get("watchdog_probe_status") != "stop_requested"


def _watchdog_service_state_from_probe(
    *,
    probe: dict[str, Any],
    action: dict[str, Any],
    watchdog_pid: int,
    cycle: int,
    discovery_file: Path,
    last_child_pid: int | None,
) -> dict[str, Any]:
    probe_status = str(probe.get("watchdog_probe_status") or "unknown")
    discovery = probe.get("discovery") if isinstance(probe.get("discovery"), dict) else None
    health = probe.get("health") if isinstance(probe.get("health"), dict) else None
    child_pid_running: bool | None
    child_health_request_ok: bool | None
    kill_switch_active = probe_status == "stop_requested"

    if probe_status == "discovery_missing":
        child_pid_running = False
        child_health_request_ok = None
    elif probe_status == "stale_discovery_pid_not_running":
        child_pid_running = False
        child_health_request_ok = None
    elif probe_status == "health_unreachable":
        child_pid_running = True
        child_health_request_ok = False
    elif probe_status == "agent_responding":
        child_pid_running = True
        child_health_request_ok = True
    elif kill_switch_active:
        child_pid_running = None
        child_health_request_ok = None
    else:
        child_pid_running = None
        child_health_request_ok = None

    child_start_error = None
    if action.get("action") == "start_agent_failed":
        child_start_error = {
            "action": action.get("action"),
            "error": action.get("error"),
            "command": action.get("command"),
        }

    state = project_supervisor_child_health_to_service_state(
        discovery=discovery,
        discovery_file_exists=discovery_file.exists() or isinstance(discovery, dict),
        child_pid_running=child_pid_running,
        child_health=health,
        child_health_request_ok=child_health_request_ok,
        child_start_error=child_start_error,
        kill_switch_active=kill_switch_active,
    )
    state["state_artifact_role"] = "user_mode_watchdog_projection"
    state["supervisor_runtime"] = {
        "watchdog_pid": watchdog_pid,
        "watchdog_cycle": cycle,
        "watchdog_probe_status": probe_status,
        "last_action": action,
        "last_child_pid": last_child_pid,
        "discovery_file": str(discovery_file),
        "service_state_file": str(default_service_state_file()),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _host_agent_boundary_facts(),
    }
    return state


def _watchdog_child_agent_command(
    *,
    host: str,
    port: int,
    runs_dir: str,
    arm_real_input: bool,
    discovery_file: Path,
) -> list[str]:
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        command = [sys.executable, "-m", "agentsight.host_agent.server"]
    command.extend(
        [
            "--host",
            host,
            "--port",
            str(port),
            "--runs-dir",
            runs_dir,
            "--discovery-file",
            str(discovery_file),
        ]
    )
    if arm_real_input:
        command.append("--arm-real-input")
    return command


def _redact_token(discovery: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in discovery.items() if key != "token"}


def _redacted_process_command(command: list[str]) -> list[str]:
    redacted: list[str] = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("<redacted>")
            skip_next = False
            continue
        redacted.append(item)
        if item == "--token":
            skip_next = True
    return redacted


def _watchdog_request_health(discovery: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(str(discovery["health_url"]), method="GET")
    request.add_header("accept", "application/json")
    request.add_header("authorization", f"Bearer {discovery['token']}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            body = {"error": str(exc)}
        finally:
            exc.close()
        return {"request_failed": True, "status": exc.code, "body": body}
    except Exception as exc:
        return {"request_failed": True, "error": str(exc)}


def _pid_running(pid: Any) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid_int)  # type: ignore[attr-defined]
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):  # type: ignore[attr-defined]
                return False
            return int(exit_code.value) == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def _pid_looks_like_host_agent(pid: Any, discovery: dict[str, Any]) -> bool:
    expected_executable = _discovery_host_agent_executable(discovery)
    if not expected_executable:
        return True
    actual_executable = _process_executable_path(pid)
    if not actual_executable:
        return False
    return _same_filesystem_path(actual_executable, expected_executable)


def _discovery_host_agent_executable(discovery: dict[str, Any]) -> str | None:
    identity = discovery.get("process_identity")
    if isinstance(identity, dict):
        executable = identity.get("executable")
        if executable:
            return str(executable)
    executable = discovery.get("executable")
    return str(executable) if executable else None


def _process_executable_path(pid: Any) -> str | None:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return None
    if pid_int <= 0:
        return None
    if os.name != "nt":
        exe = Path("/proc") / str(pid_int) / "exe"
        try:
            return str(exe.resolve(strict=True))
        except OSError:
            return None
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid_int)
        if not handle:
            return None
        try:
            buffer_len = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(buffer_len.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(buffer_len)):
                return None
            return buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None


def _same_filesystem_path(left: str, right: str) -> bool:
    try:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
    except OSError:
        return str(left) == str(right)


def default_discovery_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "host-agent.json"
    return Path.home() / ".agentsight" / "host-agent.json"


def _default_agent_dir() -> Path:
    return default_discovery_file().parent


def default_agent_report_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "last-agent-report.json"
    return Path.home() / ".agentsight" / "last-agent-report.json"


def default_agent_error_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "last-agent-error.json"
    return Path.home() / ".agentsight" / "last-agent-error.json"


def default_watchdog_report_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "last-watchdog-report.json"
    return Path.home() / ".agentsight" / "last-watchdog-report.json"


def default_service_state_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "service-state.json"
    return Path.home() / ".agentsight" / "service-state.json"


def default_watchdog_stop_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "host-agent-watchdog.stop"
    return Path.home() / ".agentsight" / "host-agent-watchdog.stop"


def _unified_session_supervisor_enabled_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight" / "unified-session-supervisor.enabled"
    return Path.home() / ".agentsight" / "unified-session-supervisor.enabled"


def _write_last_agent_report(report: dict[str, Any]) -> None:
    path = default_agent_report_file()
    report["agent_report_file"] = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _write_last_agent_error(report: dict[str, Any]) -> None:
    path = default_agent_error_file()
    report["agent_error_file"] = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _host_agent_error_report(*, request_path: str, exc: Exception) -> dict[str, Any]:
    return {
        "object_type": "AgentSightHostAgentErrorReport",
        "schema": "agentsight_host_agent_error_v1",
        "ok": False,
        "request_path": request_path,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback_tail": traceback.format_exc()[-4000:],
        "input_executed": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _write_last_watchdog_report(report: dict[str, Any]) -> None:
    path = default_watchdog_report_file()
    report["watchdog_report_file"] = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _write_service_state_file(state: dict[str, Any]) -> dict[str, Any]:
    path = default_service_state_file()
    state["service_state_file"] = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return {"written": True, "path": str(path)}
    except OSError as exc:
        return {"written": False, "path": str(path), "error": str(exc)}


def _read_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_discovery_file(
    path: Path,
    *,
    host: str,
    port: int,
    token: str,
    runs_dir: str,
    armed: bool,
) -> dict[str, Any]:
    url = f"http://{host}:{port}"
    health = build_host_agent_health_report()
    session = health.get("session", {}) if isinstance(health.get("session"), dict) else {}
    payload = {
        "object_type": "AgentSightHostAgentDiscovery",
        "schema": "discovery_v2",
        "legacy_schema": "agentsight_host_agent_discovery_v1",
        "url": url,
        "api": {
            "screen": "/screen",
            "look": "/look",
            "do": "/do",
        },
        "files": {
            "service_state": str(default_service_state_file()),
            "session_supervisor_state": str(_default_agent_dir() / "session-supervisor-state.json"),
            "tray_config": str(_default_agent_dir() / "tray-config.jsonc"),
        },
        "health_url": f"{url}/health",
        "screen_url": f"{url}/screen",
        "look_url": f"{url}/look",
        "do_url": f"{url}/do",
        "observe_url": f"{url}/observe",
        "click_url": f"{url}/click",
        "mouse_url": f"{url}/mouse",
        "input_url": f"{url}/input",
        "p0_url": f"{url}/p0",
        "shutdown_url": f"{url}/shutdown",
        "service_state_file": str(default_service_state_file()),
        "host": host,
        "port": port,
        "pid": os.getpid(),
        "process_identity": _current_host_agent_process_identity(),
        "process_session_id": session.get("process_session_id"),
        "active_console_session_id": session.get("active_console_session_id"),
        "token": token,
        "auth_header": "Authorization: Bearer ***",
        "caller_lock": {
            "required_for_real_control": True,
            "header": "X-AgentSight-Caller",
            "body_field": "caller_id",
            "lock_file": str(default_caller_lock_file()),
            "ttl_ms": 600000,
            "policy": "one_ai_caller_at_a_time_for_real_control",
        },
        "runs_dir": runs_dir,
        "armed_for_real_input": armed,
        "health_at_start": health,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return payload


def _bind_server(host: str, port: int, handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    try:
        return _AgentSightThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        if port == 0:
            raise
        if not _port_may_be_busy(exc):
            raise
        return _AgentSightThreadingHTTPServer((host, 0), handler)


def _port_may_be_busy(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98, 10048}


def _host_agent_visual_observe(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    visual_session_id = str(request.get("visual_session_id") or "visual-default")
    request_error = _review_request_error(request)
    if request_error:
        return 400, _host_agent_visual_failure(
            status=request_error["status"],
            failure_code=request_error["failure_code"],
            detail=request_error["detail"],
            visual_session_id=visual_session_id,
        )
    session = visual_sessions.get(visual_session_id)
    if session is None:
        session_runs_dir = str(Path(_resolve_agent_runs_dir(str(request.get("runs_dir") or runs_dir))) / visual_session_id)
        arming_ref = str(request.get("arming_ref") or "host-agent-visual-arming")
        operator_consent_ref = str(request.get("operator_consent_ref") or "host-agent-visual-consent")
        adapter = build_manual_windows_input_adapter(
            runs_dir=session_runs_dir,
            arming_ref=arming_ref,
            operator_consent_ref=operator_consent_ref,
            default_observation_channel_ref=DEFAULT_OBSERVATION_CHANNEL_REF,
        )
        session = {
            "adapter": adapter,
            "runs_dir": session_runs_dir,
            "arming_ref": arming_ref,
            "operator_consent_ref": operator_consent_ref,
            "observations": {},
            "observation_content": {},
            "observation_review_media": {},
            "observation_cursor_review": {},
            "created_at": time.time(),
        }
        visual_sessions[visual_session_id] = session
    adapter = session["adapter"]
    observe_payload = _visual_observe_payload(request)
    capabilities = adapter.call_tool("get_capabilities", {"probe_mode": "passive"})
    observe = adapter.call_tool("observe", observe_payload)
    if not observe.get("ok"):
        return 500, _host_agent_visual_failure(
            status="visual_observation_failed",
            failure_code=observe.get("failure", {}).get("failure_code") or "OBSERVE_FAILED",
            detail=observe.get("failure", {}).get("detail"),
            visual_session_id=visual_session_id,
            capabilities=capabilities,
            observe=observe,
        )

    frame = observe.get("data", {})
    content = _media_content_diagnostics(_frame_media_path(frame))
    observation_ref = str(frame.get("observation_id"))
    review_media = _review_media_for_frame(frame, request=request, role="observe")
    cursor_review = review_media if _cursor_review_mode(request) != "false" else _cursor_review_media_for_frame(frame, request=request, role="observe")
    raw_media_path = _frame_media_path(frame)
    session.setdefault("observations", {})[observation_ref] = frame
    session.setdefault("observation_content", {})[observation_ref] = content
    session.setdefault("observation_review_media", {})[observation_ref] = review_media
    session.setdefault("observation_cursor_review", {})[observation_ref] = cursor_review
    session["last_observed_at"] = time.time()
    report = {
        "object_type": "P0BVisualObserveReport",
        "schema": "agentsight_p0b_visual_observe_v1",
        "visual_session_id": visual_session_id,
        "observation_ref": observation_ref,
        "observation_channel_ref": frame.get("channel_ref"),
        "coordinate_system": frame.get("coordinate_system"),
        "screen_region": frame.get("screen_region"),
        "media_ref": frame.get("media_ref"),
        "media_path_abs": raw_media_path,
        "raw_media_path_abs": raw_media_path,
        "raw_evidence": True,
        "raw_media_role": "canonical_evidence",
        "raw_canonical": True,
        "media_sha256": frame.get("media_sha256"),
        "segment_frame": frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else None,
        "observation": frame,
        "observation_content": content,
        "capture_content_degenerate": bool(content.get("capture_content_degenerate")),
        "review_scale": review_media.get("scale_factor"),
        "review_media": review_media,
        "review_media_path_abs": review_media.get("review_media_path_abs"),
        "review_image_size": review_media.get("review_image_size"),
        "review_transform": review_media.get("transform"),
        "include_cursor": _cursor_review_mode(request),
        "cursor_review_media": cursor_review,
        "cursor_media_path_abs": cursor_review.get("cursor_media_path_abs"),
        "annotated_media_path_abs": cursor_review.get("annotated_media_path_abs"),
        "capabilities": capabilities,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "tool_asserts_text_entered": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "boundary": _host_agent_boundary_facts(),
        "safe_report_lines": [
            f"visual_session_id={visual_session_id}; observation_ref={observation_ref}; raw_media_path_abs={raw_media_path}.",
            f"capture_content_degenerate={bool(content.get('capture_content_degenerate'))}; coordinate_system={frame.get('coordinate_system')}.",
            f"review_media_path_abs={review_media.get('review_media_path_abs')}; review_scale={review_media.get('scale_factor')}; review_image_size={review_media.get('review_image_size')}; raw_canonical=true.",
            f"cursor_review_media_status={cursor_review.get('status')}; annotated_media_path_abs={cursor_review.get('annotated_media_path_abs')}.",
            "The tool captured pixels only; target selection and click correctness require external visual review.",
        ],
    }
    return 200, report


def _host_agent_screen_layout(
    *,
    health: dict[str, Any] | None = None,
    armed: bool = True,
    caller_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    health = health or build_host_agent_health_report()
    metrics = health.get("virtual_screen_metrics") if isinstance(health.get("virtual_screen_metrics"), dict) else {}
    virtual = {
        "x": int(metrics.get("virtual_screen_x", 0) or 0),
        "y": int(metrics.get("virtual_screen_y", 0) or 0),
        "w": int(metrics.get("virtual_screen_width", metrics.get("screen_width", 0)) or 0),
        "h": int(metrics.get("virtual_screen_height", metrics.get("screen_height", 0)) or 0),
    }
    if virtual["w"] <= 0:
        virtual["w"] = int(metrics.get("screen_width", 1920) or 1920)
    if virtual["h"] <= 0:
        virtual["h"] = int(metrics.get("screen_height", 1080) or 1080)
    report = {
        "object_type": "ScreenLayout",
        "schema": "agentsight_screen_v1",
        "v": "V1",
        "ok": True,
        "virtual": virtual,
        "monitors": [
            {
                "id": "m1",
                "primary": True,
                "x": virtual["x"],
                "y": virtual["y"],
                "w": virtual["w"],
                "h": virtual["h"],
            }
        ],
        "coordinate_system": "virtual_screen_pixels",
        "readonly": True,
        **_host_agent_public_status_fields(_host_agent_public_readiness(health, armed=armed, arm_required=False)),
        "boundary": _host_agent_boundary_facts(),
    }
    if caller_lock is not None:
        report["caller_lock"] = caller_lock
    return report


def _host_agent_protocol_look_requires_visual_lock(request: dict[str, Any]) -> bool:
    """Return whether a public /look request needs the live visual-session lock.

    Segment-review look queries only read indexed evidence from disk (and may read an
    existing view transform) so they must not block live /look or /do requests while a
    long clip/diff query is still running after a client-side timeout.
    """
    q = request.get("q")
    if q in {"changes", "clip"}:
        return False
    if q == "diff":
        src = request.get("src") if isinstance(request.get("src"), dict) else {}
        mode = str(request.get("mode") or ("endpoints" if src.get("type") == "view" else "timeline"))
        return mode == "endpoints"
    time_query = request.get("time") if isinstance(request.get("time"), dict) else None
    if time_query and any(key in time_query for key in ("near", "at", "requested_time")):
        return False
    return True


def _host_agent_protocol_look(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    protocol_views: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    try:
        validate_request({"command": "look", "payload": request})
    except SchemaError as exc:
        return 400, _host_agent_visual_failure(
            status="schema_invalid",
            failure_code="SCHEMA_INVALID",
            detail=str(exc),
        )
    if request.get("q") == "diff":
        return _host_agent_protocol_look_diff(
            visual_sessions=visual_sessions,
            protocol_views=protocol_views,
            runs_dir=runs_dir,
            request=request,
        )
    if request.get("q") == "changes":
        return _host_agent_protocol_look_changes(protocol_views=protocol_views, runs_dir=runs_dir, request=request)
    if request.get("q") == "clip":
        return _host_agent_protocol_look_clip(protocol_views=protocol_views, runs_dir=runs_dir, request=request)

    src = request.get("src") if isinstance(request.get("src"), dict) else {}
    rect = request.get("r") if isinstance(request.get("r"), dict) else {}
    scale_down = int(request.get("scale_down", 1))
    parent_view: dict[str, Any] | None = None
    source_rect_in_parent: dict[str, int] | None = None
    if src.get("type") == "view":
        parent_view = protocol_views.get(str(src.get("view_id")))
        if not parent_view:
            return 404, _host_agent_visual_failure(
                status="view_not_found",
                failure_code="VIEW_NOT_FOUND",
                detail="src.view_id does not refer to a known view",
            )
        source_failure = _host_view_current_screen_source_failure(parent_view)
        if source_failure:
            return 409, _host_agent_visual_failure(status="view_not_current_screen_basis", **source_failure)
        source_rect_in_parent = _look_rect(rect)
        bounds_failure = _host_view_rect_bounds_failure(parent_view, source_rect_in_parent)
        if bounds_failure:
            return 409, _host_agent_visual_failure(status="view_region_out_of_bounds", **bounds_failure)
        try:
            screen_rect = _screen_rect_from_host_parent_view(parent_view, source_rect_in_parent)
        except ValueError:
            return 409, _host_agent_visual_failure(
                status="view_transform_unavailable",
                failure_code="VIEW_TRANSFORM_UNAVAILABLE",
                detail="src.view_id does not have a usable view-to-screen transform",
            )
    else:
        screen_rect = _look_rect(rect)

    time_query = request.get("time") if isinstance(request.get("time"), dict) else None
    if time_query and any(key in time_query for key in ("near", "at", "requested_time")):
        return _host_agent_protocol_look_time_near(
            protocol_views=protocol_views,
            runs_dir=runs_dir,
            request=request,
            screen_rect=screen_rect,
            parent_view=parent_view,
        )

    status, observe = _host_agent_visual_observe(
        visual_sessions=visual_sessions,
        runs_dir=runs_dir,
        request={"mode": "region", "region": _region_from_look_rect(screen_rect), "raw_evidence": True},
    )
    if status != 200:
        return status, observe
    frame = observe.get("observation") if isinstance(observe.get("observation"), dict) else {}
    view_record = _remember_host_protocol_view(
        protocol_views,
        frame=frame,
        visual_session_id=str(observe.get("visual_session_id")),
        screen_rect=screen_rect,
        scale_down=scale_down,
        parent_view_id=parent_view.get("view", {}).get("id") if parent_view else None,
        source_rect_in_parent=source_rect_in_parent,
        request_id=request.get("id"),
    )
    response_src = dict(src)
    response_src.setdefault("t", "latest")
    if view_record.get("source_time"):
        response_src["source_time"] = view_record["source_time"]
    return 200, {
        "object_type": "LookResult",
        "schema": "agentsight_look_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": True,
        "type": "frame",
        "view": view_record["view"],
        "view_record": view_record["public_record"],
        "content": view_record.get("mcp_content") or [],
        "image_content_returned": bool(view_record.get("mcp_content")),
        "image_content_type": "mcp_image_content",
        "derived_review_file_written": False,
        "raw_or_derived": "derived_review_only",
        "src": response_src,
        "r": {
            **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
            "unit": "parent_view_px" if parent_view else "virtual_screen_px",
        },
        "capture_content_degenerate": bool(observe.get("capture_content_degenerate")),
        "segment_frame": frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else None,
        "tool_asserts_target_found": False,
        "tool_asserts_business_success": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_agent_protocol_look_time_near(
    *,
    protocol_views: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
    screen_rect: dict[str, int],
    parent_view: dict[str, Any] | None,
) -> tuple[int, dict[str, Any]]:
    time_query = request.get("time") if isinstance(request.get("time"), dict) else {}
    requested_time = time_query.get("near", time_query.get("at", time_query.get("requested_time")))
    root = Path(_resolve_agent_runs_dir(str(request.get("runs_dir") or runs_dir)))
    near = query_segment_decoder_near_time(root, requested_time)
    nearest = near.get("nearest_frame") if isinstance(near.get("nearest_frame"), dict) else None
    decoded_review: dict[str, Any] | None = None
    decode_error: dict[str, Any] | None = None
    decode_errors: list[dict[str, Any]] = []
    historical_view: dict[str, Any] | None = None
    view_record: dict[str, Any] | None = None
    coordinate_unit = "stored_frame_px"

    for candidate in _host_time_near_decode_candidates(near):
        if not isinstance(candidate.get("segment_restore_ref"), dict):
            continue
        view_id = f"sv_{uuid.uuid4().hex[:8]}"
        decode_region = _host_historical_decode_region(screen_rect, candidate)
        if decode_region.get("status") == "no_overlap":
            decode_errors.append(
                {
                    "status": "decode_skipped_no_overlap",
                    "segment_id": candidate.get("segment_id"),
                    "segment_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
                    "frame_id": candidate.get("frame_id"),
                    "relation": candidate.get("relation"),
                    "decode_region_basis": decode_region,
                    "tool_asserts_business_success": False,
                    "tool_asserts_causality": False,
                    "tool_asserts_target_hit": False,
                }
            )
            if coordinate_unit == "stored_frame_px":
                coordinate_unit = decode_region["unit"]
            continue
        try:
            decoded_review = decode_segment_region_to_image_content(
                candidate["segment_restore_ref"],
                region=decode_region["region"],
                scale_down=int(request.get("scale_down") or 1),
            )
            mcp_content = decoded_review.pop("mcp_content", [])
            decoded_review["requested_screen_region"] = dict(screen_rect)
            decoded_review["decode_region_basis"] = decode_region
            decoded_review["selected_segment_frame"] = {
                "segment_id": candidate.get("segment_id"),
                "segment_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
                "frame_id": candidate.get("frame_id"),
                "relation": candidate.get("relation"),
                "delta_ms": candidate.get("delta_ms"),
            }
            historical_view = {
                "id": view_id,
                "w": decoded_review.get("region", {}).get("w"),
                "h": decoded_review.get("region", {}).get("h"),
                "scale_down": request.get("scale_down"),
                "view_is_current_action_basis": False,
                "view_role": "historical_segment_review",
                "image_content_returned": bool(mcp_content),
                "derived_review_file_written": False,
            }
            view_record = _host_historical_view_record(
                view_id=view_id,
                request=request,
                candidate=candidate,
                decoded_review=decoded_review,
                decode_region=decode_region,
                screen_rect=screen_rect,
            )
            protocol_views[view_id] = _host_historical_view_index_entry(
                view_id=view_id,
                historical_view=historical_view,
                view_record=view_record,
                screen_rect=screen_rect,
                request_id=request.get("id"),
            )
            decoded_review["image_content_returned"] = bool(mcp_content)
            decoded_review["_mcp_content"] = mcp_content
            coordinate_unit = decode_region["unit"]
            break
        except Exception as exc:
            decode_errors.append(
                {
                    "status": "decode_failed",
                    "segment_id": candidate.get("segment_id"),
                    "segment_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
                    "frame_id": candidate.get("frame_id"),
                    "relation": candidate.get("relation"),
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                    "tool_asserts_business_success": False,
                    "tool_asserts_causality": False,
                    "tool_asserts_target_hit": False,
                }
            )

    if decoded_review is None and decode_errors:
        decode_error = {
            "status": "decode_failed",
            "attempt_count": len(decode_errors),
            "attempts": decode_errors,
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
        }

    ok = near.get("query_status") == "generated"
    mcp_content = decoded_review.pop("_mcp_content", []) if isinstance(decoded_review, dict) else []
    return 200, {
        "object_type": "LookResult",
        "schema": "agentsight_look_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": ok,
        "type": "time_near_frames",
        "mode": "segment_decoder_nearest_indexed_frames",
        "src": request.get("src"),
        "r": {
            "x": int(screen_rect["x"]),
            "y": int(screen_rect["y"]),
            "w": int(screen_rect["w"]),
            "h": int(screen_rect["h"]),
            "unit": coordinate_unit if nearest else "stored_frame_px",
            "coordinate_caveat": None
            if nearest and coordinate_unit != "stored_frame_px"
            else "historical Segment frame lacks screen_region metadata; r was decoded as stored-frame pixels",
        },
        "time": time_query,
        "frames_near_time": near,
        "historical_view": historical_view,
        "view_record": view_record,
        "decoded_review": decoded_review,
        "content": mcp_content,
        "decode_error": decode_error,
        "decode_errors": decode_errors,
        "raw_media_returned": False,
        "decoded_review_returned": decoded_review is not None,
        "image_bytes_returned": bool(mcp_content),
        "image_content_returned": bool(mcp_content),
        "no_capture_performed": True,
        "view_is_current_action_basis": False,
        "tool_asserts_target_found": False,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_historical_view_record(
    *,
    view_id: str,
    request: dict[str, Any],
    candidate: dict[str, Any],
    decoded_review: dict[str, Any],
    decode_region: dict[str, Any],
    screen_rect: dict[str, int],
) -> dict[str, Any]:
    scale_down = int(request.get("scale_down") or decoded_review.get("scale_down") or 1)
    region = decoded_review.get("region") if isinstance(decoded_review.get("region"), dict) else decode_region.get("region")
    region = dict(region) if isinstance(region, dict) else dict(screen_rect)
    output_w = max(1, (int(region["w"]) + scale_down - 1) // scale_down)
    output_h = max(1, (int(region["h"]) + scale_down - 1) // scale_down)
    restore_ref = candidate.get("segment_restore_ref") if isinstance(candidate.get("segment_restore_ref"), dict) else None
    transform = {
        "schema": "agentsight_view_transform_v1",
        "coordinate_system": "historical_view_pixels_to_requested_screen_pixels",
        "view_pixels_to_virtual_screen_pixels": {
            "origin_x": int(screen_rect["x"]),
            "origin_y": int(screen_rect["y"]),
            "scale_x": scale_down,
            "scale_y": scale_down,
            "formula": "screen_x=origin_x+view_x*scale_x; screen_y=origin_y+view_y*scale_y",
        },
        "view_is_current_action_basis": False,
        "blur_changes_coordinates": False,
        "cursor_overlay_changes_coordinates": False,
    }
    return {
        "view_id": view_id,
        "created_at": _format_hms_ms(time.time()),
        "view_role": "historical_segment_review",
        "view_is_current_action_basis": False,
        "source_frame_ref": candidate.get("frame_id"),
        "source_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
        "segment_restore_ref": restore_ref,
        "source_segment_path": restore_ref.get("segment_path") if isinstance(restore_ref, dict) else None,
        "segment_id": candidate.get("segment_id"),
        "requested_screen_region": dict(screen_rect),
        "actual_decoded_region": region,
        "output_image_size": {"w": output_w, "h": output_h},
        "scale_down": scale_down,
        "blur": bool(decoded_review.get("blur_radius")),
        "blur_radius": int(decoded_review.get("blur_radius") or 0),
        "cursor_mode": "none",
        "raw_or_derived": "derived_review_only",
        "coordinate_system": decode_region.get("unit", "stored_frame_px"),
        "transform": transform,
        "capture_content_degenerate": bool(candidate.get("capture_content_degenerate")),
        "request_id": request.get("id"),
        "operation_log_linkage": {"request_id": request.get("id"), "route": "/look"},
        "derived_review_file_written": False,
        "canonical_evidence_storage": ".mkv/raw_observation",
    }


def _host_historical_view_index_entry(
    *,
    view_id: str,
    historical_view: dict[str, Any],
    view_record: dict[str, Any],
    screen_rect: dict[str, int],
    request_id: Any,
) -> dict[str, Any]:
    return {
        "object_type": "AgentSightViewIndexEntry",
        "schema": "agentsight_view_index_v1",
        "request_id": request_id,
        "view": historical_view,
        "public_record": view_record,
        "screen_rect": dict(screen_rect),
        "source_rect_in_parent": None,
        "source_timestamp": time.time(),
        "source_time": _format_hms_ms(time.time()),
        "segment_restore_ref": view_record.get("segment_restore_ref"),
        "source_frame_id": view_record.get("source_frame_id"),
        "source_segment_path": view_record.get("source_segment_path"),
        "transform": view_record.get("transform"),
        "coordinate_system": view_record.get("coordinate_system"),
        "raw_or_derived": "derived_review_only",
        "cursor_mode": view_record.get("cursor_mode"),
        "capture_content_degenerate": bool(view_record.get("capture_content_degenerate")),
        "scale_down": int(view_record.get("scale_down") or 1),
        "view_role": "historical_segment_review",
        "view_is_current_action_basis": False,
        "view_is_derived_review": True,
        "derived_review_file_written": False,
        "mcp_content": [],
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _host_agent_protocol_look_changes(
    *,
    protocol_views: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    src = request.get("src") if isinstance(request.get("src"), dict) else {}
    rect = request.get("r") if isinstance(request.get("r"), dict) else {}
    parent_view: dict[str, Any] | None = None
    source_rect_in_parent: dict[str, int] | None = None
    if src.get("type") == "view":
        parent_view = protocol_views.get(str(src.get("view_id")))
        if not parent_view:
            return 404, _host_agent_visual_failure(
                status="view_not_found",
                failure_code="VIEW_NOT_FOUND",
                detail="src.view_id does not refer to a known view",
            )
        source_failure = _host_view_current_screen_source_failure(parent_view)
        if source_failure:
            return 409, _host_agent_visual_failure(status="view_not_current_screen_basis", **source_failure)
        source_rect_in_parent = _look_rect(rect)
        bounds_failure = _host_view_rect_bounds_failure(parent_view, source_rect_in_parent)
        if bounds_failure:
            return 409, _host_agent_visual_failure(status="view_region_out_of_bounds", **bounds_failure)
        try:
            screen_rect = _screen_rect_from_host_parent_view(parent_view, source_rect_in_parent)
        except ValueError:
            return 409, _host_agent_visual_failure(
                status="view_transform_unavailable",
                failure_code="VIEW_TRANSFORM_UNAVAILABLE",
                detail="src.view_id does not have a usable view-to-screen transform",
            )
    else:
        screen_rect = _look_rect(rect)
    time_query = request.get("time") if isinstance(request.get("time"), dict) else {}
    root = Path(_resolve_agent_runs_dir(str(request.get("runs_dir") or runs_dir)))
    changes = query_segment_change_index(
        root,
        region=screen_rect,
        region_coordinate_system="virtual_screen_pixels",
        max_pairs=int(request.get("max_pairs", 128)),
        min_changed_pixel_ratio=float(request.get("min_changed_pixel_ratio", 0.0)),
        start_time=time_query.get("from"),
        end_time=time_query.get("to"),
    )
    return 200, {
        "object_type": "LookResult",
        "schema": "agentsight_look_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": True,
        "type": "changes",
        "mode": "segment_metadata_change_index",
        "src": request.get("src"),
        "r": {
            **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
            "unit": "parent_view_px" if parent_view else "virtual_screen_px",
            "coordinate_caveat": "changes maps r through indexed Segment screen_region metadata when available; it does not capture or inspect the live screen",
        },
        "screen_region": dict(screen_rect),
        "parent_view": {"id": parent_view.get("view", {}).get("id")} if parent_view else None,
        "source_rect_in_parent": source_rect_in_parent,
        "time": time_query,
        "changes": changes,
        "raw_media_returned": False,
        "image_bytes_returned": False,
        "derived_review_artifact_returned": False,
        "no_capture_performed": True,
        "no_media_exported": True,
        "view_is_current_action_basis": False,
        "tool_asserts_target_found": False,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_agent_protocol_look_clip(
    *,
    protocol_views: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    src = request.get("src") if isinstance(request.get("src"), dict) else {}
    rect = request.get("r") if isinstance(request.get("r"), dict) else {}
    parent_view: dict[str, Any] | None = None
    source_rect_in_parent: dict[str, int] | None = None
    if src.get("type") == "view":
        parent_view = protocol_views.get(str(src.get("view_id")))
        if not parent_view:
            return 404, _host_agent_visual_failure(
                status="view_not_found",
                failure_code="VIEW_NOT_FOUND",
                detail="src.view_id does not refer to a known view",
            )
        source_failure = _host_view_current_screen_source_failure(parent_view)
        if source_failure:
            return 409, _host_agent_visual_failure(status="view_not_current_screen_basis", **source_failure)
        source_rect_in_parent = _look_rect(rect)
        bounds_failure = _host_view_rect_bounds_failure(parent_view, source_rect_in_parent)
        if bounds_failure:
            return 409, _host_agent_visual_failure(status="view_region_out_of_bounds", **bounds_failure)
        try:
            screen_rect = _screen_rect_from_host_parent_view(parent_view, source_rect_in_parent)
        except ValueError:
            return 409, _host_agent_visual_failure(
                status="view_transform_unavailable",
                failure_code="VIEW_TRANSFORM_UNAVAILABLE",
                detail="src.view_id does not have a usable view-to-screen transform",
            )
    else:
        screen_rect = _look_rect(rect)
    time_query = request.get("time") if isinstance(request.get("time"), dict) else {}
    root = Path(_resolve_agent_runs_dir(str(request.get("runs_dir") or runs_dir)))
    clip = query_segment_review_clip(
        root,
        region=screen_rect,
        region_coordinate_system="virtual_screen_pixels",
        start_time=time_query.get("from"),
        end_time=time_query.get("to"),
        max_frames=int(request.get("max_frames", 32)),
        scale_down=int(request.get("scale_down", 1)),
        max_artifacts=int(request.get("max_artifacts", 0)),
        output_dir=root / "derived_review" / "clips",
        request_id=str(request.get("id") or "look-clip"),
    )
    return 200, {
        "object_type": "LookResult",
        "schema": "agentsight_look_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": True,
        "type": "clip",
        "mode": "segment_review_clip",
        "src": request.get("src"),
        "r": {
            **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
            "unit": "parent_view_px" if parent_view else "virtual_screen_px",
            "coordinate_caveat": "clip maps r through indexed Segment screen_region metadata when available; it does not capture or inspect the live screen",
        },
        "screen_region": dict(screen_rect),
        "parent_view": {"id": parent_view.get("view", {}).get("id")} if parent_view else None,
        "source_rect_in_parent": source_rect_in_parent,
        "time": time_query,
        "clip": clip,
        "artifacts": clip.get("artifacts") or [],
        "raw_media_returned": False,
        "image_bytes_returned": False,
        "derived_review_artifact_returned": bool(clip.get("artifacts")),
        "derived_artifacts_are_canonical": False,
        "no_capture_performed": True,
        "no_media_exported": not bool(clip.get("artifacts")),
        "view_is_current_action_basis": False,
        "tool_asserts_target_found": False,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_time_near_decode_candidates(near: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    ordered = [
        near.get("nearest_frame"),
        near.get("before_frame"),
        near.get("after_frame"),
        *(near.get("frames") or []),
    ]
    for candidate in ordered:
        if not isinstance(candidate, dict):
            continue
        restore_ref = candidate.get("segment_restore_ref") if isinstance(candidate.get("segment_restore_ref"), dict) else {}
        key = (str(candidate.get("segment_path_abs") or restore_ref.get("segment_path")), str(candidate.get("frame_id")))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(candidate)
    return candidates


def _host_historical_decode_region(screen_rect: dict[str, int], frame: dict[str, Any]) -> dict[str, Any]:
    stored_region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else None
    coordinate_system = frame.get("coordinate_system")
    if coordinate_system in {"virtual_screen_pixels", "monitor_pixels"} and stored_region:
        x0 = int(stored_region.get("x", 0))
        y0 = int(stored_region.get("y", 0))
        w0 = int(stored_region.get("w", stored_region.get("width", 0)))
        h0 = int(stored_region.get("h", stored_region.get("height", 0)))
        left = max(int(screen_rect["x"]), x0)
        top = max(int(screen_rect["y"]), y0)
        right = min(int(screen_rect["x"]) + int(screen_rect["w"]), x0 + w0)
        bottom = min(int(screen_rect["y"]) + int(screen_rect["h"]), y0 + h0)
        if right > left and bottom > top:
            return {
                "status": "mapped",
                "unit": "virtual_screen_px",
                "source_coordinate_system": coordinate_system,
                "stored_frame_region": {"x": x0, "y": y0, "w": w0, "h": h0},
                "requested_screen_region": dict(screen_rect),
                "region": {"x": left - x0, "y": top - y0, "w": right - left, "h": bottom - top},
                "clipped_to_stored_frame": left != int(screen_rect["x"])
                or top != int(screen_rect["y"])
                or right != int(screen_rect["x"]) + int(screen_rect["w"])
                or bottom != int(screen_rect["y"]) + int(screen_rect["h"]),
            }
        return {
            "status": "no_overlap",
            "unit": "virtual_screen_px",
            "source_coordinate_system": coordinate_system,
            "stored_frame_region": {"x": x0, "y": y0, "w": w0, "h": h0},
            "requested_screen_region": dict(screen_rect),
            "clipped_to_stored_frame": True,
        }
    return {
        "status": "stored_frame_fallback",
        "unit": "stored_frame_px",
        "source_coordinate_system": coordinate_system,
        "stored_frame_region": stored_region,
        "requested_screen_region": dict(screen_rect),
        "region": {"x": int(screen_rect["x"]), "y": int(screen_rect["y"]), "w": int(screen_rect["w"]), "h": int(screen_rect["h"])},
        "clipped_to_stored_frame": False,
    }


def _host_agent_protocol_look_diff(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    protocol_views: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    src = request.get("src") if isinstance(request.get("src"), dict) else {}
    mode = str(request.get("mode") or ("endpoints" if src.get("type") == "view" else "timeline"))
    if mode != "endpoints":
        return _host_agent_protocol_look_diff_timeline(protocol_views=protocol_views, runs_dir=runs_dir, request=request, mode=mode)
    if src.get("type") != "view" or not src.get("view_id"):
        return 400, _host_agent_visual_failure(
            status="look_diff_requires_view_source",
            failure_code="LOOK_DIFF_REQUIRES_VIEW_SOURCE",
            detail="look q=diff compares a previous view against the latest pixels for the same visible region",
        )
    parent_view = protocol_views.get(str(src.get("view_id")))
    if not parent_view:
        return 404, _host_agent_visual_failure(
            status="view_not_found",
            failure_code="VIEW_NOT_FOUND",
            detail="src.view_id does not refer to a known view",
        )
    source_failure = _host_view_current_screen_source_failure(parent_view)
    if source_failure:
        return 409, _host_agent_visual_failure(status="view_not_current_screen_basis", **source_failure)
    rect = request.get("r") if isinstance(request.get("r"), dict) else {}
    source_rect_in_parent = _look_rect(rect)
    bounds_failure = _host_view_rect_bounds_failure(parent_view, source_rect_in_parent)
    if bounds_failure:
        return 409, _host_agent_visual_failure(status="view_region_out_of_bounds", **bounds_failure)
    try:
        screen_rect = _screen_rect_from_host_parent_view(parent_view, source_rect_in_parent)
    except ValueError:
        return 409, _host_agent_visual_failure(
            status="view_transform_unavailable",
            failure_code="VIEW_TRANSFORM_UNAVAILABLE",
            detail="src.view_id does not have a usable view-to-screen transform",
        )
    status, observe = _host_agent_visual_observe(
        visual_sessions=visual_sessions,
        runs_dir=runs_dir,
        request={"mode": "region", "region": _region_from_look_rect(screen_rect), "raw_evidence": True},
    )
    if status != 200:
        return status, observe
    frame = observe.get("observation") if isinstance(observe.get("observation"), dict) else {}
    after_view = _remember_host_protocol_view(
        protocol_views,
        frame=frame,
        visual_session_id=str(observe.get("visual_session_id")),
        screen_rect=screen_rect,
        scale_down=int(request.get("scale_down", 1)),
        parent_view_id=parent_view.get("view", {}).get("id"),
        source_rect_in_parent=source_rect_in_parent,
        request_id=request.get("id"),
    )
    diff = _host_look_diff_view_against_latest(
        parent_view=parent_view,
        screen_rect=screen_rect,
        after_frame=frame,
        after_view=after_view,
        max_artifacts=int(request.get("max_artifacts", 0) or 0),
        request_id=str(request.get("id") or "look-diff"),
    )
    return 200, {
        "object_type": "LookResult",
        "schema": "agentsight_look_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": True,
        "type": "diff",
        "mode": "endpoint_latest_vs_view_baseline",
        "src": {"type": "view", "view_id": parent_view.get("view", {}).get("id"), "source_time": parent_view.get("source_time")},
        "baseline_view": {
            "id": parent_view.get("view", {}).get("id"),
            "path": parent_view.get("view", {}).get("path"),
            "source_time": parent_view.get("source_time"),
        },
        "after_view": after_view["view"],
        "r": {
            **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
            "unit": "parent_view_px",
        },
        "screen_region": dict(screen_rect),
        "diffs": [diff["comparison"]],
        "summary": diff["summary"],
        "artifacts": diff["artifacts"],
        "raw_media_returned": False,
        "derived_review_artifact_returned": bool(diff["artifacts"]),
        "derived_artifacts_are_canonical": False,
        "segment_frame": frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else None,
        "tool_asserts_semantic_change": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_business_success": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_agent_protocol_look_diff_timeline(
    *,
    protocol_views: dict[str, dict[str, Any]],
    runs_dir: str,
    request: dict[str, Any],
    mode: str,
) -> tuple[int, dict[str, Any]]:
    src = request.get("src") if isinstance(request.get("src"), dict) else {}
    rect = request.get("r") if isinstance(request.get("r"), dict) else {}
    parent_view: dict[str, Any] | None = None
    source_rect_in_parent: dict[str, int] | None = None
    if src.get("type") == "view":
        parent_view = protocol_views.get(str(src.get("view_id")))
        if not parent_view:
            return 404, _host_agent_visual_failure(
                status="view_not_found",
                failure_code="VIEW_NOT_FOUND",
                detail="src.view_id does not refer to a known view",
            )
        source_failure = _host_view_current_screen_source_failure(parent_view)
        if source_failure:
            return 409, _host_agent_visual_failure(status="view_not_current_screen_basis", **source_failure)
        source_rect_in_parent = _look_rect(rect)
        bounds_failure = _host_view_rect_bounds_failure(parent_view, source_rect_in_parent)
        if bounds_failure:
            return 409, _host_agent_visual_failure(status="view_region_out_of_bounds", **bounds_failure)
        try:
            screen_rect = _screen_rect_from_host_parent_view(parent_view, source_rect_in_parent)
        except ValueError:
            return 409, _host_agent_visual_failure(
                status="view_transform_unavailable",
                failure_code="VIEW_TRANSFORM_UNAVAILABLE",
                detail="src.view_id does not have a usable view-to-screen transform",
            )
    else:
        screen_rect = _look_rect(rect)
    time_query = request.get("time") if isinstance(request.get("time"), dict) else {}
    root = Path(_resolve_agent_runs_dir(str(request.get("runs_dir") or runs_dir)))
    max_artifacts = int(request.get("max_artifacts", 0) or 0) if mode == "timeline_with_artifacts" else 0
    timeline = query_segment_timeline_diff(
        root,
        region=screen_rect,
        region_coordinate_system="virtual_screen_pixels",
        start_time=time_query.get("from"),
        end_time=time_query.get("to"),
        max_artifacts=max_artifacts,
        output_dir=root / "derived_review" / "diffs",
        request_id=str(request.get("id") or "look-diff-timeline"),
    )
    return 200, {
        "object_type": "LookResult",
        "schema": "agentsight_look_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": True,
        "type": "diff",
        "mode": "timeline_segment_diff",
        "requested_mode": mode,
        "src": request.get("src"),
        "r": {
            **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
            "unit": "parent_view_px" if parent_view else "virtual_screen_px",
            "coordinate_caveat": "diff timeline maps r through indexed Segment screen_region metadata when available; it does not capture or inspect the live screen",
        },
        "screen_region": dict(screen_rect),
        "parent_view": {"id": parent_view.get("view", {}).get("id")} if parent_view else None,
        "source_rect_in_parent": source_rect_in_parent,
        "time": time_query,
        "diffs": timeline,
        "summary": {
            "status": "computed",
            "frame_pairs": timeline.get("change_count", 0),
            "computed_comparison_count": timeline.get("change_count", 0),
            "artifact_count": timeline.get("artifact_count", 0),
            "changed": bool(timeline.get("change_count", 0)),
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
        },
        "artifacts": timeline.get("artifacts") or [],
        "raw_media_returned": False,
        "image_bytes_returned": False,
        "derived_review_artifact_returned": bool(timeline.get("artifacts")),
        "derived_artifacts_are_canonical": False,
        "no_capture_performed": True,
        "no_media_exported": not bool(timeline.get("artifacts")),
        "view_is_current_action_basis": False,
        "tool_asserts_semantic_change": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_business_success": False,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_agent_protocol_do(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    protocol_views: dict[str, dict[str, Any]],
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    try:
        validate_request({"command": "do", "payload": request})
    except SchemaError as exc:
        return 400, _host_agent_visual_failure(
            status="schema_invalid",
            failure_code="SCHEMA_INVALID",
            detail=str(exc),
        )
    started = time.time()
    basis = request.get("basis") if isinstance(request.get("basis"), dict) else {}
    view = _resolve_host_do_view(protocol_views, basis)
    if not view:
        return 404, _host_agent_visual_failure(
            status="view_not_found",
            failure_code="VIEW_NOT_FOUND",
            detail="basis.view_id is required for public do",
        )
    if not _host_view_can_be_action_basis(view):
        return 409, {
            "object_type": "DoResult",
            "schema": "agentsight_do_v1",
            "v": request.get("v", "V1"),
            "id": request.get("id"),
            "ok": False,
            "status": "failed",
            "basis": {"view_id": view.get("view", {}).get("id")},
            "input": {"sent": False, "host_event_count": 0, "step_count": 0},
            "steps": [],
            "failed_step": {
                "i": 0,
                "failure_code": "VIEW_NOT_ACTION_BASIS",
                "detail": "basis.view_id refers to a historical or review-only view, not a current action basis",
            },
            "capture_windows": [],
            "anchors": [],
            "tool_asserts_target_hit": False,
            "tool_asserts_business_success": False,
            "input_visual_relationship_judgment": "external_review_only",
            "boundary": _host_agent_boundary_facts(),
        }
    visual_session_id = str(view.get("visual_session_id"))
    source_observation_ref = str(view.get("source_observation_ref"))
    seq = request.get("seq") if isinstance(request.get("seq"), list) else []
    basis_point = basis.get("point") if isinstance(basis.get("point"), dict) else None
    basis_point_failure = _host_view_point_bounds_failure(view, int(basis_point["x"]), int(basis_point["y"])) if basis_point else None
    if basis_point_failure:
        return 409, {
            "object_type": "DoResult",
            "schema": "agentsight_do_v1",
            "v": request.get("v", "V1"),
            "id": request.get("id"),
            "ok": False,
            "status": "failed",
            "basis": {"view_id": view["view"]["id"], "point": {"x": int(basis_point["x"]), "y": int(basis_point["y"])}},
            "input": {"sent": False, "host_event_count": 0, "step_count": 0},
            "steps": [],
            "failed_step": {"i": 0, **basis_point_failure},
            "capture_windows": [],
            "anchors": [],
            "tool_asserts_target_hit": False,
            "tool_asserts_business_success": False,
            "input_visual_relationship_judgment": "external_review_only",
            "boundary": _host_agent_boundary_facts(),
        }
    basis_screen_point = _host_basis_point_to_screen(view, basis_point) if basis_point else None
    if basis_point and basis_screen_point is None:
        return 409, {
            "object_type": "DoResult",
            "schema": "agentsight_do_v1",
            "v": request.get("v", "V1"),
            "id": request.get("id"),
            "ok": False,
            "status": "failed",
            "basis": {"view_id": view["view"]["id"], "point": {"x": int(basis_point["x"]), "y": int(basis_point["y"])}},
            "input": {"sent": False, "host_event_count": 0, "step_count": 0},
            "steps": [],
            "failed_step": {
                "i": 0,
                "failure_code": "VIEW_TRANSFORM_UNAVAILABLE",
                "detail": "basis.view_id does not have a usable view-to-screen transform",
            },
            "capture_windows": [],
            "anchors": [],
            "tool_asserts_target_hit": False,
            "tool_asserts_business_success": False,
            "input_visual_relationship_judgment": "external_review_only",
            "boundary": _host_agent_boundary_facts(),
        }
    current_point: dict[str, int] | None = basis_screen_point
    steps: list[dict[str, Any]] = []
    capture_window_ranges: list[dict[str, Any]] = []
    anchors: list[dict[str, Any]] = []
    host_event_count = 0
    status_text = "done"
    failed_step: dict[str, Any] | None = None
    pre_action_anchor_written = False
    post_observe_requested = request.get("post_observe") is not None

    for index, step in enumerate(seq, start=1):
        step_time = time.time()
        if isinstance(step, int) or (isinstance(step, dict) and step.get("t") == "wait"):
            wait_ms = int(step if isinstance(step, int) else step.get("ms", 0))
            time.sleep(wait_ms / 1000)
            steps.append({"i": index, "req": step, "ok": True, "host_event_count": 0, "time": _format_hms_ms(step_time)})
            continue
        if not isinstance(step, dict):
            status_text = "partial" if steps else "failed"
            failed_step = {"i": index, "failure_code": "INVALID_DO_STEP", "detail": "step must be number or object"}
            break
        routed_request, screen_point, step_error = _host_do_routed_request(
            step,
            view=view,
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            current_point=current_point,
        )
        if step_error:
            status_text = "partial" if steps else "failed"
            failed_step = {"i": index, **step_error}
            steps.append(
                {
                    "i": index,
                    "req": _safe_host_do_req(step),
                    "ok": False,
                    "failure_code": step_error["failure_code"],
                    "detail": step_error["detail"],
                    "host_event_count": 0,
                    "time": _format_hms_ms(step_time),
                }
            )
            break
        if screen_point:
            current_point = screen_point
        if not pre_action_anchor_written:
            anchors.append({"kind": "pre_action", "step_i": index, "ts": _format_hms_ms(max(started, step_time - 0.001))})
            pre_action_anchor_written = True
        input_started = time.time()
        if routed_request["route"] == "mouse":
            if post_observe_requested:
                route_status, route_report = _host_agent_fast_do_input(
                    visual_sessions=visual_sessions,
                    visual_session_id=visual_session_id,
                    source_observation_ref=source_observation_ref,
                    route=routed_request["route"],
                    input_payload=routed_request["payload"],
                )
            else:
                route_status, route_report = _host_agent_visual_mouse(visual_sessions=visual_sessions, request=routed_request["payload"])
        else:
            if post_observe_requested:
                route_status, route_report = _host_agent_fast_do_input(
                    visual_sessions=visual_sessions,
                    visual_session_id=visual_session_id,
                    source_observation_ref=source_observation_ref,
                    route=routed_request["route"],
                    input_payload=routed_request["payload"],
                )
            else:
                route_status, route_report = _host_agent_visual_input(visual_sessions=visual_sessions, request=routed_request["payload"])
        sent_count = _int_or_zero(route_report.get("host_sent_event_count")) if isinstance(route_report, dict) else 0
        host_event_count += sent_count
        _record_action_capture_window(capture_window_ranges, input_started=input_started, step_i=index)
        anchors.append({"kind": "burst_start", "step_i": index, "ts": _format_hms_ms(input_started)})
        step_result = {
            "i": index,
            "req": _safe_host_do_req(step),
            "ok": route_status == 200,
            "route": routed_request["route"],
            "screen": screen_point,
            "host_event_count": sent_count,
            "time": _format_hms_ms(input_started),
            "legacy_report_schema": route_report.get("schema") if isinstance(route_report, dict) else None,
            "after_observation_ref": route_report.get("after_observation_ref") if isinstance(route_report, dict) else None,
        }
        if post_observe_requested and isinstance(route_report, dict):
            step_result["post_observe_fast_path"] = bool(route_report.get("post_observe_fast_path"))
            step_result["after_observation_skipped"] = bool(route_report.get("after_observation_skipped"))
        if route_status != 200:
            step_result["failure_code"] = route_report.get("failure_code") if isinstance(route_report, dict) else "INPUT_FAILED"
            step_result["detail"] = route_report.get("detail") if isinstance(route_report, dict) else None
            failed_step = {"i": index, "failure_code": step_result["failure_code"], "detail": step_result.get("detail")}
            status_text = "partial" if any(item.get("ok") for item in steps) else "failed"
            steps.append(step_result)
            break
        steps.append(step_result)

    capture_windows = _format_action_capture_windows(capture_window_ranges)
    if anchors:
        anchors.append({"kind": "burst_end", "ts": capture_windows[-1]["to"] if capture_windows else _format_hms_ms(time.time())})
    http_status = 200 if status_text == "done" else 409
    post_observe = None
    if status_text == "done" and request.get("post_observe") is not None:
        post_observe = _host_do_post_observe(visual_sessions=visual_sessions, view=view, request=request)
    result = {
        "object_type": "DoResult",
        "schema": "agentsight_do_v1",
        "v": request.get("v", "V1"),
        "id": request.get("id"),
        "ok": status_text == "done",
        "status": status_text,
        "time": {"start": _format_hms_ms(started), "end": _format_hms_ms(time.time())},
        "basis": {
            "view_id": view["view"]["id"],
            **({"point": {"x": int(basis_point["x"]), "y": int(basis_point["y"])}, "screen_point": basis_screen_point} if basis_point else {}),
        },
        "input": {
            "sent": host_event_count > 0,
            "host_event_count": host_event_count,
            "step_count": len(steps),
        },
        "steps": steps,
        "failed_step": failed_step,
        "capture_windows": capture_windows,
        "anchors": anchors,
        "tool_asserts_target_hit": False,
        "tool_asserts_business_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "boundary": _host_agent_boundary_facts(),
    }
    if post_observe is not None:
        result["post_observe"] = post_observe
    return http_status, result


def _host_agent_apply_recording_policy_defaults(request: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("post_observe") is not None:
        return request
    policy = normalize_recording_policy(read_jsonc_file(default_tray_config_file()))
    action_capture = (policy.get("recording") or {}).get("action_capture")
    if not isinstance(action_capture, dict):
        return request
    if not bool(action_capture.get("enabled", True)):
        return request
    if not bool(action_capture.get("capture_post_action_frames")):
        return request
    fps = _coerce_int_range(action_capture.get("post_action_fps"), default=1, minimum=1, maximum=60)
    duration_ms = _coerce_int_range(action_capture.get("post_action_duration_ms"), default=10000, minimum=1, maximum=60000)
    frame_count = max(1, int((fps * duration_ms + 999) // 1000))
    interval_ms = max(1, int(round(1000 / fps)))
    payload = dict(request)
    payload["post_observe"] = {
        "delay_ms": 0,
        "frame_count": frame_count,
        "interval_ms": interval_ms,
        "stable_threshold": 0.001,
        "stable_frame_count": 2,
        "stop_when_stable": False,
    }
    return payload


def _coerce_int_range(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError("bool_is_not_int")
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return min(maximum, max(minimum, parsed))


def _coerce_float_range(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError("bool_is_not_float")
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return min(maximum, max(minimum, parsed))


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return bool(default)


def _look_rect(rect: dict[str, Any]) -> dict[str, int]:
    return {"x": int(rect["x"]), "y": int(rect["y"]), "w": int(rect["w"]), "h": int(rect["h"])}


def _region_from_look_rect(rect: dict[str, int]) -> dict[str, int]:
    return {"x": int(rect["x"]), "y": int(rect["y"]), "width": int(rect["w"]), "height": int(rect["h"])}


def _screen_rect_from_host_parent_view(parent_view: dict[str, Any], rect: dict[str, int]) -> dict[str, int]:
    mapping = _host_view_pixels_to_screen_mapping(parent_view)
    if mapping:
        return {
            "x": int(mapping["origin_x"]) + int(rect["x"]) * int(mapping["scale_x"]),
            "y": int(mapping["origin_y"]) + int(rect["y"]) * int(mapping["scale_y"]),
            "w": int(rect["w"]) * int(mapping["scale_x"]),
            "h": int(rect["h"]) * int(mapping["scale_y"]),
        }
    raise ValueError("VIEW_TRANSFORM_UNAVAILABLE")


def _host_view_rect_bounds_failure(view: dict[str, Any], rect: dict[str, int]) -> dict[str, str] | None:
    view_meta = view.get("view") if isinstance(view.get("view"), dict) else {}
    width = int(view_meta.get("w") or 0)
    height = int(view_meta.get("h") or 0)
    if width <= 0 or height <= 0:
        return {
            "failure_code": "VIEW_DIMENSIONS_UNAVAILABLE",
            "detail": "src.view_id does not have usable view dimensions",
        }
    x, y, w, h = int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"])
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
        return {
            "failure_code": "VIEW_REGION_OUT_OF_BOUNDS",
            "detail": f"view region ({x}, {y}, {w}, {h}) is outside view bounds {width}x{height}",
        }
    return None


def _host_view_pixels_to_screen_mapping(view: dict[str, Any]) -> dict[str, Any] | None:
    transform = view.get("transform") if isinstance(view.get("transform"), dict) else {}
    mapping = transform.get("view_pixels_to_virtual_screen_pixels")
    if not isinstance(mapping, dict):
        return None
    try:
        origin_x = int(mapping["origin_x"])
        origin_y = int(mapping["origin_y"])
        scale_x = int(mapping["scale_x"])
        scale_y = int(mapping["scale_y"])
    except (KeyError, TypeError, ValueError):
        return None
    if scale_x <= 0 or scale_y <= 0:
        return None
    return {"origin_x": origin_x, "origin_y": origin_y, "scale_x": scale_x, "scale_y": scale_y}


def _host_view_current_screen_source_failure(view: dict[str, Any]) -> dict[str, str] | None:
    if _host_view_can_be_action_basis(view):
        return None
    return {
        "failure_code": "VIEW_NOT_CURRENT_SCREEN_BASIS",
        "detail": "src.view_id refers to a historical or review-only view, not a current screen basis",
    }


def _remember_host_protocol_view(
    protocol_views: dict[str, dict[str, Any]],
    *,
    frame: dict[str, Any],
    visual_session_id: str,
    screen_rect: dict[str, int],
    scale_down: int,
    parent_view_id: str | None,
    source_rect_in_parent: dict[str, int] | None,
    request_id: Any,
) -> dict[str, Any]:
    view_id = f"v_{uuid.uuid4().hex[:8]}"
    export = _export_host_protocol_view_image_content(frame, screen_rect=screen_rect, scale_down=scale_down)
    source_timestamp = float(frame.get("captured_at") or frame.get("timestamp") or time.time())
    segment_frame = frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else {}
    segment_restore_ref = segment_frame.get("restore_ref") or segment_frame.get("segment_restore_ref")
    transform = {
        "schema": "agentsight_view_transform_v1",
        "coordinate_system": "view_pixels_to_virtual_screen_pixels",
        "view_pixels_to_virtual_screen_pixels": {
            "origin_x": int(screen_rect["x"]),
            "origin_y": int(screen_rect["y"]),
            "scale_x": int(scale_down),
            "scale_y": int(scale_down),
            "formula": "screen_x=origin_x+view_x*scale_x; screen_y=origin_y+view_y*scale_y",
        },
        "blur_changes_coordinates": False,
        "cursor_overlay_changes_coordinates": False,
    }
    view = {
        "id": view_id,
        "w": export["w"],
        "h": export["h"],
        "scale_down": scale_down,
    }
    source_screen_region = _host_frame_screen_region(frame, fallback=screen_rect)
    actual_decoded_region = _host_decoded_region_from_screen_rect(screen_rect, source_screen_region)
    public_record = {
        "view_id": view_id,
        "created_at": _format_hms_ms(source_timestamp),
        "source_frame_ref": frame.get("observation_id"),
        "source_frame_id": segment_frame.get("frame_id") or segment_frame.get("segment_frame_id"),
        "segment_restore_ref": segment_restore_ref,
        "source_segment_path": segment_restore_ref.get("segment_path") if isinstance(segment_restore_ref, dict) else None,
        "segment_id": segment_frame.get("segment_id"),
        "source_frame_screen_region": source_screen_region,
        "requested_screen_region": dict(screen_rect),
        "actual_decoded_region": actual_decoded_region,
        "output_image_size": {"w": export["w"], "h": export["h"]},
        "scale_down": scale_down,
        "blur": False,
        "blur_radius": 0,
        "cursor_mode": "none",
        "raw_or_derived": "derived_review_only",
        "coordinate_system": "virtual_screen_pixels",
        "transform": transform,
        "capture_content_degenerate": False,
        "request_id": request_id,
        "operation_log_linkage": {"request_id": request_id, "route": "/look"},
        "derived_review_file_written": False,
        "canonical_evidence_storage": ".mkv/raw_observation",
    }
    record = {
        "object_type": "AgentSightViewIndexEntry",
        "schema": "agentsight_view_index_v1",
        "request_id": request_id,
        "view": view,
        "public_record": public_record,
        "visual_session_id": visual_session_id,
        "source_observation_ref": frame.get("observation_id"),
        "parent_view_id": parent_view_id,
        "screen_rect": dict(screen_rect),
        "source_rect_in_parent": source_rect_in_parent,
        "source_timestamp": source_timestamp,
        "source_time": _format_hms_ms(source_timestamp),
        "source_media_ref": frame.get("media_ref"),
        "source_media_path_abs": _frame_media_path(frame),
        "segment_restore_ref": segment_restore_ref,
        "source_frame_id": public_record["source_frame_id"],
        "source_segment_path": public_record["source_segment_path"],
        "transform": transform,
        "coordinate_system": "virtual_screen_pixels",
        "raw_or_derived": "derived_review_only",
        "cursor_mode": "none",
        "capture_content_degenerate": False,
        "raw_frame_canonical": True,
        "view_is_derived_review": True,
        "derived_review_file_written": False,
        "mcp_content": export.get("mcp_content") or [],
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
    protocol_views[view_id] = record
    return record


def _host_frame_screen_region(frame: dict[str, Any], *, fallback: dict[str, int]) -> dict[str, int]:
    region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else {}
    try:
        return {
            "x": int(region["x"]),
            "y": int(region["y"]),
            "w": int(region.get("w", region.get("width"))),
            "h": int(region.get("h", region.get("height"))),
        }
    except (KeyError, TypeError, ValueError):
        return dict(fallback)


def _host_decoded_region_from_screen_rect(
    screen_rect: dict[str, int],
    source_screen_region: dict[str, int],
) -> dict[str, int]:
    return {
        "x": int(screen_rect["x"]) - int(source_screen_region["x"]),
        "y": int(screen_rect["y"]) - int(source_screen_region["y"]),
        "w": int(screen_rect["w"]),
        "h": int(screen_rect["h"]),
    }


def _host_look_diff_view_against_latest(
    *,
    parent_view: dict[str, Any],
    screen_rect: dict[str, int],
    after_frame: dict[str, Any],
    after_view: dict[str, Any],
    max_artifacts: int,
    request_id: str,
) -> dict[str, Any]:
    baseline_path = _path_or_none(parent_view.get("source_media_path_abs"))
    after_path = _path_or_none(_frame_media_path(after_frame))
    comparison = _host_look_diff_not_computed(
        before_view_id=parent_view.get("view", {}).get("id"),
        after_view_id=after_view.get("view", {}).get("id"),
        reason="frame_media_path_missing",
        screen_rect=screen_rect,
    )
    artifacts: list[dict[str, Any]] = []
    if baseline_path and after_path:
        try:
            comparison, heatmap_bytes = _host_compute_image_diff(
                before_path=baseline_path,
                before_box=_host_baseline_box(parent_view, screen_rect),
                after_path=after_path,
                screen_rect=screen_rect,
                before_view_id=str(parent_view.get("view", {}).get("id")),
                after_view_id=str(after_view.get("view", {}).get("id")),
            )
            if max_artifacts > 0 and heatmap_bytes and comparison.get("changed"):
                artifact_path = after_path.with_name(f"{_safe_file_token(request_id)}-diff-heatmap.png")
                artifact_path.write_bytes(heatmap_bytes)
                artifacts.append(
                    {
                        "artifact_type": "diff_heatmap",
                        "artifact_role": "derived_review_image",
                        "path": str(artifact_path),
                        "media_path_abs": str(artifact_path),
                        "canonical": False,
                        "visualization_only": True,
                        "integrity_truth_source": False,
                        "excluded_from_integrity_truth_source": True,
                    }
                )
        except Exception as exc:
            comparison = {
                **comparison,
                "status": "not_computed",
                "not_computed_reason": "frame_decode_failed",
                "failure_detail": str(exc),
            }
    return {
        "comparison": comparison,
        "summary": _host_look_diff_summary(comparison),
        "artifacts": artifacts,
    }


def _host_compute_image_diff(
    *,
    before_path: Path,
    before_box: tuple[int, int, int, int],
    after_path: Path,
    screen_rect: dict[str, int],
    before_view_id: str,
    after_view_id: str,
) -> tuple[dict[str, Any], bytes | None]:
    before_width, before_height, before_rows = _read_png_rgb_rows(before_path)
    left, top, right, bottom = before_box
    before_crop = _crop_rgb_rows(before_rows, width=before_width, height=before_height, left=left, top=top, right=right, bottom=bottom)
    after_width, after_height, after_rows = _read_png_rgb_rows(after_path)
    width = right - left
    height = bottom - top
    if width != after_width or height != after_height:
        raise ValueError(f"diff image size mismatch before={(width, height)} after={(after_width, after_height)}")
    changed = 0
    min_x = width
    min_y = height
    max_x = -1
    max_y = -1
    heatmap_rows = [bytearray(width * 3) for _ in range(height)]
    for y in range(height):
        before_row = before_crop[y]
        after_row = after_rows[y]
        heatmap_row = heatmap_rows[y]
        for x in range(width):
            offset = x * 3
            if before_row[offset : offset + 3] == after_row[offset : offset + 3]:
                continue
            changed += 1
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
            heatmap_row[offset : offset + 3] = b"\xff\x00\x00"
    bbox_frame = None
    bbox_screen = None
    if changed:
        bbox_frame = {"x": min_x, "y": min_y, "width": max_x - min_x + 1, "height": max_y - min_y + 1}
        bbox_screen = {
            "x": int(screen_rect["x"]) + min_x,
            "y": int(screen_rect["y"]) + min_y,
            "width": bbox_frame["width"],
            "height": bbox_frame["height"],
        }
    total = width * height
    comparison = {
        "status": "computed",
        "comparison_kind": "view_baseline_to_latest_screen_region",
        "before_view_id": before_view_id,
        "after_view_id": after_view_id,
        "frame_width": width,
        "frame_height": height,
        "changed": changed > 0,
        "changed_pixel_count": changed,
        "total_pixel_count": total,
        "changed_pixel_ratio": round(changed / total, 8) if total else 0.0,
        "changed_bbox_frame": bbox_frame,
        "changed_bbox": bbox_screen,
        "changed_bbox_coordinate_system": "virtual_screen_pixels",
        "noise_assessment": _host_look_diff_noise_assessment(changed, total, bbox_frame),
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
    }
    return comparison, _png_from_rgb_rows(heatmap_rows, width=width, height=height)


def _host_look_diff_not_computed(
    *,
    before_view_id: Any,
    after_view_id: Any,
    reason: str,
    screen_rect: dict[str, int],
) -> dict[str, Any]:
    return {
        "status": "not_computed",
        "not_computed_reason": reason,
        "comparison_kind": "view_baseline_to_latest_screen_region",
        "before_view_id": before_view_id,
        "after_view_id": after_view_id,
        "screen_region": dict(screen_rect),
        "changed": False,
        "changed_pixel_count": 0,
        "total_pixel_count": 0,
        "changed_pixel_ratio": 0.0,
        "changed_bbox_frame": None,
        "changed_bbox": None,
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
    }


def _host_look_diff_summary(comparison: dict[str, Any]) -> dict[str, Any]:
    computed = comparison.get("status") == "computed"
    return {
        "status": "computed" if computed else "not_computed",
        "frame_pairs": 1 if computed else 0,
        "comparison_count": 1,
        "computed_comparison_count": 1 if computed else 0,
        "changed": bool(comparison.get("changed")) if computed else False,
        "changed_pixel_count": int(comparison.get("changed_pixel_count") or 0),
        "total_pixel_count": int(comparison.get("total_pixel_count") or 0),
        "max_changed_pixel_ratio": float(comparison.get("changed_pixel_ratio") or 0.0),
        "largest_change": comparison.get("changed_bbox"),
        "largest_changed_bbox": comparison.get("changed_bbox"),
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
    }


def _host_look_diff_noise_assessment(changed: int, total: int, bbox: dict[str, int] | None) -> dict[str, Any]:
    ratio = changed / total if total else 0.0
    width = int((bbox or {}).get("width") or 0)
    height = int((bbox or {}).get("height") or 0)
    return {
        "cursor_or_caret_noise_possible": bool(changed and (ratio <= 0.01 or width <= 2 or height <= 2)),
        "basis": "thin_bbox_or_tiny_changed_ratio",
        "tool_does_not_classify_semantic_noise": True,
    }


def _host_baseline_box(parent_view: dict[str, Any], screen_rect: dict[str, int]) -> tuple[int, int, int, int]:
    parent_rect = parent_view["screen_rect"]
    left = int(screen_rect["x"]) - int(parent_rect["x"])
    top = int(screen_rect["y"]) - int(parent_rect["y"])
    return (left, top, left + int(screen_rect["w"]), top + int(screen_rect["h"]))


def _crop_rgb_rows(
    rows: list[bytearray],
    *,
    width: int,
    height: int,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> list[bytearray]:
    if left < 0 or top < 0 or right > width or bottom > height or right <= left or bottom <= top:
        raise ValueError("crop outside baseline image")
    cropped: list[bytearray] = []
    for y in range(top, bottom):
        cropped.append(bytearray(rows[y][left * 3 : right * 3]))
    return cropped


def _path_or_none(value: Any) -> Path | None:
    if not value:
        return None
    return value if isinstance(value, Path) else Path(str(value))


def _safe_file_token(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value)
    return cleaned[:80] or f"look-diff-{uuid.uuid4().hex[:8]}"


def _export_host_protocol_view_image_content(
    frame: dict[str, Any],
    *,
    screen_rect: dict[str, int],
    scale_down: int,
) -> dict[str, Any]:
    raw_path_text = _frame_media_path(frame)
    target_w = max(1, (int(screen_rect["w"]) + scale_down - 1) // scale_down)
    target_h = max(1, (int(screen_rect["h"]) + scale_down - 1) // scale_down)
    if raw_path_text:
        raw_path = Path(raw_path_text)
        try:
            width, height, rows = _read_png_rgb_rows(raw_path)
            scaled_w, scaled_h, scaled_rows = _scale_rgb_rows(
                rows,
                width=width,
                height=height,
                scale=1.0 / max(1, scale_down),
            )
            png_bytes = _png_from_rgb_rows(scaled_rows, width=scaled_w, height=scaled_h)
            return {
                "mcp_content": [
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": base64.b64encode(png_bytes).decode("ascii"),
                        "raw_or_derived": "derived_review_only",
                        "canonical": False,
                    }
                ],
                "w": scaled_w,
                "h": scaled_h,
                "status": "generated_mcp_image_content",
            }
        except Exception:
            return {"mcp_content": [], "w": target_w, "h": target_h, "status": "image_content_unavailable"}
    return {"mcp_content": [], "w": target_w, "h": target_h, "status": "image_content_unavailable"}


def _resolve_host_do_view(protocol_views: dict[str, dict[str, Any]], basis: dict[str, Any]) -> dict[str, Any] | None:
    if basis.get("view_id"):
        return protocol_views.get(str(basis["view_id"]))
    return None


def _host_view_can_be_action_basis(view: dict[str, Any]) -> bool:
    records = [view]
    for key in ("view", "public_record"):
        nested = view.get(key)
        if isinstance(nested, dict):
            records.append(nested)
    for record in records:
        if record.get("view_is_current_action_basis") is False:
            return False
        if record.get("view_role") == "historical_segment_review":
            return False
    return True


def _host_agent_fast_do_input(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    visual_session_id: str,
    source_observation_ref: str,
    route: str,
    input_payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    session = visual_sessions.get(visual_session_id)
    if not session:
        return 404, _host_agent_visual_failure(
            status="visual_session_missing",
            failure_code="VISUAL_SESSION_MISSING",
            detail="visual_session_id was not found; call /screen and /look first",
            visual_session_id=visual_session_id,
        )
    source_frame = session.get("observations", {}).get(source_observation_ref)
    if not source_frame:
        return 404, _host_agent_visual_failure(
            status="source_observation_missing",
            failure_code="SOURCE_OBSERVATION_MISSING",
            detail="source observation was not found in the visual session",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    source_content = session.get("observation_content", {}).get(source_observation_ref) or _media_content_diagnostics(
        _frame_media_path(source_frame)
    )
    if source_content.get("capture_content_degenerate"):
        return 409, _host_agent_visual_failure(
            status="source_observation_degenerate",
            failure_code="SOURCE_OBSERVATION_DEGENERATE",
            detail="source observation is not a useful visual reference",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            source_observation_content=source_content,
        )

    adapter = session["adapter"]
    lease = adapter.call_tool(
        "create_lease",
        {
            "duration_ms": 10_000,
            "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
            "arming_ref": session["arming_ref"],
            "operator_consent_ref": session["operator_consent_ref"],
            "before_observation_ref": source_observation_ref,
            "budget": {"max_input_events": 1},
        },
    )
    execute: dict[str, Any] | None = None
    execute_payload = _host_agent_fast_execute_payload(input_payload)
    if lease.get("ok"):
        execute = adapter.call_tool(
            "execute_input",
            {
                "lease_id": lease["data"]["lease_id"],
                **execute_payload,
                "skip_after_observation": True,
                "after_observation_skip_reason": "post_observe_requested",
            },
        )
    execute_data = execute.get("data", {}) if isinstance(execute, dict) else {}
    host_sent_event_count = _int_or_zero(execute_data.get("host_sent_event_count") or execute_data.get("sent_event_count"))
    host_input_sent = bool(execute_data.get("host_input_sent") or execute_data.get("host_input_executed") or host_sent_event_count)
    ok = bool(lease.get("ok") and execute and execute.get("ok"))
    report = {
        "object_type": "PObserveFastDoInputReport",
        "schema": "agentsight_pobserve_fast_do_input_v1",
        "visual_session_id": visual_session_id,
        "source_observation_ref": source_observation_ref,
        "route": route,
        "post_observe_fast_path": True,
        "after_observation_skipped": True,
        "after_observation_skip_reason": "post_observe_requested",
        "input_type": execute_payload.get("input_type"),
        "execute_payload": _safe_fast_execute_payload(execute_payload),
        "lease": lease,
        "execute": execute,
        "execute_ok": bool(execute and execute.get("ok")),
        "host_input_sent": host_input_sent,
        "host_input_executed": bool(execute_data.get("host_input_executed") or host_input_sent),
        "host_sent_event_count": host_sent_event_count,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "boundary": _host_agent_boundary_facts(),
    }
    if execute_data.get("input_event_id"):
        report["input_event_ref"] = execute_data.get("input_event_id")
    if not lease.get("ok"):
        report["failure_code"] = (lease.get("failure") or {}).get("failure_code") or "LEASE_FAILED"
        report["detail"] = (lease.get("failure") or {}).get("detail")
    elif not (execute and execute.get("ok")):
        failure = execute.get("failure") if isinstance(execute, dict) else {}
        report["failure_code"] = failure.get("failure_code") or "INPUT_FAILED"
        report["detail"] = failure.get("detail")
    return (200 if ok else 500), report


def _host_agent_fast_execute_payload(input_payload: dict[str, Any]) -> dict[str, Any]:
    if input_payload.get("input_type"):
        return {
            key: value
            for key, value in input_payload.items()
            if key not in {"visual_session_id", "source_observation_ref", "observation_ref"}
        }
    action = str(input_payload.get("action") or "")
    input_type_by_action = {
        "move": "mouse_move",
        "click": "mouse_click",
        "double_click": "mouse_double_click",
        "button_down": "mouse_button_down",
        "button_up": "mouse_button_up",
        "wheel": "mouse_scroll",
    }
    execute_payload = {
        key: value
        for key, value in input_payload.items()
        if key not in {"visual_session_id", "source_observation_ref", "observation_ref", "action"}
    }
    if action in input_type_by_action:
        execute_payload["input_type"] = input_type_by_action[action]
    return execute_payload


def _safe_fast_execute_payload(payload: dict[str, Any]) -> dict[str, Any]:
    safe = dict(payload)
    if safe.get("input_type") == "key_text_stream" and "text" in safe:
        try:
            summary = key_text_summary(validate_key_text_stream(safe["text"]))
        except ValueError:
            summary = {"text_valid": False}
        safe.pop("text", None)
        safe["text_summary"] = summary
    return safe


def _host_do_post_observe(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    view: dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    config = validate_post_observe(request.get("post_observe"))
    if config["delay_ms"]:
        time.sleep(config["delay_ms"] / 1000)
    visual_session_id = str(view.get("visual_session_id"))
    session = visual_sessions.get(visual_session_id)
    if not session:
        return _host_do_post_observe_not_generated(config, reason="visual_session_missing", view=view)
    adapter = session.get("adapter")
    if adapter is None:
        return _host_do_post_observe_not_generated(config, reason="visual_session_adapter_missing", view=view)
    region = _region_from_look_rect(view["screen_rect"])
    frames: list[dict[str, Any]] = []
    for index in range(config["frame_count"]):
        observe = adapter.call_tool("observe", {"mode": "region", "region": region})
        if not observe.get("ok"):
            break
        frame = observe.get("data", {}) if isinstance(observe.get("data"), dict) else {}
        if not frame:
            break
        frame["post_observe_frame_index"] = index
        observation_ref = str(frame.get("observation_id"))
        session.setdefault("observations", {})[observation_ref] = frame
        session.setdefault("observation_content", {})[observation_ref] = _media_content_diagnostics(_frame_media_path(frame))
        frames.append(frame)
        baseline_ref = str(view.get("source_observation_ref") or "")
        baseline_frame = session.get("observations", {}).get(baseline_ref)

        def frame_path(frame: dict[str, Any]) -> Path | None:
            path_text = _frame_media_path(frame)
            return Path(path_text) if path_text else None

        if should_stop_post_observe_sampling(
            baseline_frame=baseline_frame,
            frames=frames,
            screen_region=view["screen_rect"],
            coordinate_system=frames[0].get("coordinate_system") if frames else None,
            request=config,
            frame_path=frame_path,
        ):
            break
        if index < config["frame_count"] - 1 and config["interval_ms"]:
            time.sleep(config["interval_ms"] / 1000)
    baseline_ref = str(view.get("source_observation_ref") or "")
    baseline_frame = session.get("observations", {}).get(baseline_ref)

    def frame_path(frame: dict[str, Any]) -> Path | None:
        path_text = _frame_media_path(frame)
        return Path(path_text) if path_text else None

    return build_post_action_observation_window(
        baseline_frame=baseline_frame,
        frames=frames,
        screen_region=view["screen_rect"],
        coordinate_system=frames[0].get("coordinate_system") if frames else None,
        request=config,
        frame_path=frame_path,
    )


def _host_do_post_observe_not_generated(config: dict[str, Any], *, reason: str, view: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_type": "PostActionObservationWindow",
        "schema": "agentsight_post_action_observation_window_v1",
        "status": "not_generated",
        "mode": "post_action_observation_window",
        "not_generated_reason": reason,
        "request": dict(config),
        "screen_region": dict(view.get("screen_rect", {})),
        "coordinate_system": None,
        "baseline_frame_ref": view.get("source_observation_ref"),
        "sampled_frame_count": 0,
        "sampled_frames": [],
        "comparison_count": 0,
        "comparisons": [],
        "summary": {
            "sampled_frame_count": 0,
            "comparison_count": 0,
            "computed_comparison_count": 0,
            "changed_comparison_count": 0,
            "changed": False,
            "changed_frame_indexes": [],
            "max_changed_pixel_ratio": 0.0,
            "largest_changed_bbox": None,
            "stable_threshold": float(config.get("stable_threshold", 0.001)),
            "stable_frame_count_required": int(config.get("stable_frame_count", 2)),
            "stability_status": "not_generated",
            "stable": False,
            "not_stable": False,
            "still_changing": False,
            "still_changing_at_window_end": False,
            "tool_asserts_semantic_change": False,
            "tool_asserts_business_success": False,
        },
        "returns_images": False,
        "raw_media_returned": False,
        "derived_review_artifact_returned": False,
        "derived_metadata": False,
        "raw_frames_are_canonical_evidence": True,
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "boundary": _host_agent_boundary_facts(),
    }


def _host_do_routed_request(
    step: dict[str, Any],
    *,
    view: dict[str, Any],
    visual_session_id: str,
    source_observation_ref: str,
    current_point: dict[str, int] | None,
) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, str] | None]:
    step_type = str(step.get("t"))
    base = {"visual_session_id": visual_session_id, "source_observation_ref": source_observation_ref}
    if step_type == "move":
        bounds_failure = _host_view_point_bounds_failure(view, int(step["x"]), int(step["y"]))
        if bounds_failure:
            return {}, None, bounds_failure
        point = _host_view_point_to_screen(view, int(step["x"]), int(step["y"]))
        if point is None:
            return {}, None, {
                "failure_code": "VIEW_TRANSFORM_UNAVAILABLE",
                "detail": "basis.view_id does not have a usable view-to-screen transform",
            }
        return {"route": "mouse", "payload": {**base, "action": "move", "x": point["x"], "y": point["y"]}}, point, None
    if step_type in {"click", "dblclick", "down", "up", "wheel"} and (step_type not in {"down", "up"} or "b" in step):
        if current_point is None:
            return {}, None, {
                "failure_code": "DO_REQUIRES_PRIOR_MOVE",
                "detail": f"{step_type} uses the current mouse position; call move first in the same do.seq",
            }
        action = {"dblclick": "double_click", "down": "button_down", "up": "button_up"}.get(step_type, step_type)
        payload = {
            **base,
            "action": action,
            "x": current_point["x"],
            "y": current_point["y"],
        }
        if step_type in {"click", "dblclick", "down", "up"}:
            payload["button"] = str(step.get("b") or "left")
        if step_type == "wheel":
            payload["wheel_delta"] = int(step.get("dy", 0))
            payload["horizontal_wheel_delta"] = int(step.get("dx", 0))
        return {"route": "mouse", "payload": payload}, current_point, None
    if step_type == "text":
        return {"route": "input", "payload": {**base, "input_type": "key_text_stream", "text": validate_key_text_stream(step.get("text"))}}, None, None
    if step_type == "key":
        return {"route": "input", "payload": {**base, "input_type": "key_press", "key": step.get("key")}}, None, None
    if step_type == "chord":
        return {
            "route": "input",
            "payload": {**base, "input_type": "key_chord", "modifiers": step.get("modifiers"), "key": step.get("key")},
        }, None, None
    if step_type == "down" and "key" in step:
        return {"route": "input", "payload": {**base, "input_type": "key_down", "key": step.get("key")}}, None, None
    if step_type == "up" and "key" in step:
        return {"route": "input", "payload": {**base, "input_type": "key_up", "key": step.get("key")}}, None, None
    return {}, None, {"failure_code": "DO_STEP_UNSUPPORTED", "detail": f"unsupported do step: {step_type}"}


def _host_view_point_bounds_failure(view: dict[str, Any], x: int, y: int) -> dict[str, str] | None:
    view_meta = view.get("view") if isinstance(view.get("view"), dict) else {}
    width = int(view_meta.get("w") or 0)
    height = int(view_meta.get("h") or 0)
    if width <= 0 or height <= 0:
        return {
            "failure_code": "VIEW_DIMENSIONS_UNAVAILABLE",
            "detail": "basis.view_id does not have usable view dimensions",
        }
    if x < 0 or y < 0 or x >= width or y >= height:
        return {
            "failure_code": "VIEW_POINT_OUT_OF_BOUNDS",
            "detail": f"view point ({x}, {y}) is outside view bounds {width}x{height}",
        }
    return None


def _host_view_point_to_screen(view: dict[str, Any], x: int, y: int) -> dict[str, int] | None:
    transform = view.get("transform") if isinstance(view.get("transform"), dict) else {}
    mapping = transform.get("view_pixels_to_virtual_screen_pixels") if isinstance(transform.get("view_pixels_to_virtual_screen_pixels"), dict) else {}
    if mapping:
        return {
            "x": int(mapping["origin_x"]) + x * int(mapping["scale_x"]),
            "y": int(mapping["origin_y"]) + y * int(mapping["scale_y"]),
        }
    return None


def _host_basis_point_to_screen(view: dict[str, Any], point: dict[str, Any]) -> dict[str, int] | None:
    return _host_view_point_to_screen(view, int(point["x"]), int(point["y"]))


def _safe_host_do_req(step: dict[str, Any]) -> dict[str, Any]:
    safe = dict(step)
    if safe.get("t") == "text" and "text" in safe:
        try:
            summary = key_text_summary(validate_key_text_stream(safe["text"]))
        except ValueError:
            summary = {"text_valid": False}
        safe.pop("text", None)
        safe["text_summary"] = summary
    return safe


def _record_action_capture_window(windows: list[dict[str, Any]], *, input_started: float, step_i: int) -> None:
    """Record one /do action-capture window, merging adjacent inputs within 60s."""
    window_to = input_started + 60.0
    if windows and input_started <= float(windows[-1]["to_ts"]):
        windows[-1]["to_ts"] = max(float(windows[-1]["to_ts"]), window_to)
        windows[-1]["last_step_i"] = step_i
        return
    windows.append({"kind": "burst", "from_ts": input_started, "to_ts": window_to, "first_step_i": step_i, "last_step_i": step_i})


def _format_action_capture_windows(windows: list[dict[str, Any]]) -> list[dict[str, str]]:
    formatted: list[dict[str, str]] = []
    for window in windows:
        first_step = int(window["first_step_i"])
        last_step = int(window["last_step_i"])
        reason = f"step_{first_step}_input" if first_step == last_step else f"steps_{first_step}-{last_step}_input_merged_within_60s"
        formatted.append(
            {
                "kind": str(window["kind"]),
                "from": _format_hms_ms(float(window["from_ts"])),
                "to": _format_hms_ms(float(window["to_ts"])),
                "reason": reason,
            }
        )
    return formatted


def _format_hms_ms(timestamp: float) -> str:
    local = time.localtime(timestamp)
    millis = int((timestamp - int(timestamp)) * 1000)
    return time.strftime("%H:%M:%S", local) + f".{millis:03d}"


def _host_agent_visual_click(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    visual_session_id = str(request.get("visual_session_id") or "")
    source_observation_ref = str(request.get("source_observation_ref") or request.get("observation_ref") or "")
    session = visual_sessions.get(visual_session_id)
    if not session:
        return 404, _host_agent_visual_failure(
            status="visual_session_missing",
            failure_code="VISUAL_SESSION_MISSING",
            detail="visual_session_id was not found; call /observe first",
            visual_session_id=visual_session_id,
        )
    source_frame = session["observations"].get(source_observation_ref)
    if not source_frame:
        return 404, _host_agent_visual_failure(
            status="source_observation_missing",
            failure_code="SOURCE_OBSERVATION_MISSING",
            detail="source_observation_ref was not found in the visual session",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    source_content = session["observation_content"].get(source_observation_ref) or _media_content_diagnostics(
        _frame_media_path(source_frame)
    )
    if source_content.get("capture_content_degenerate"):
        return 409, _host_agent_visual_failure(
            status="source_observation_degenerate",
            failure_code="SOURCE_OBSERVATION_DEGENERATE",
            detail="source observation is not a useful visual reference",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            source_observation_content=source_content,
        )
    request_error = _review_request_error(request)
    if request_error:
        return 400, _host_agent_visual_failure(
            status=request_error["status"],
            failure_code=request_error["failure_code"],
            detail=request_error["detail"],
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    point, coordinate_resolution, point_error = _resolve_visual_click_point(
        request,
        session=session,
        source_observation_ref=source_observation_ref,
        source_frame=source_frame,
    )
    if point_error:
        return 400, _host_agent_visual_failure(
            status=point_error["status"],
            failure_code=point_error["failure_code"],
            detail=point_error["detail"],
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            **point_error.get("extra", {}),
        )
    if not point:
        return 400, _host_agent_visual_failure(
            status="invalid_click_coordinates",
            failure_code="INVALID_CLICK_COORDINATES",
            detail="provide integer monitor-pixel x/y or review_observation_ref plus integer review_x/review_y",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    if not _point_inside_region(point, source_frame.get("screen_region")):
        return 400, _host_agent_visual_failure(
            status="click_coordinates_outside_source_observation",
            failure_code="INPUT_COORDINATE_OUT_OF_SCOPE",
            detail="click coordinates must be inside the referenced source observation screen_region",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            selected_point=point,
            coordinate_resolution=coordinate_resolution,
            screen_region=source_frame.get("screen_region"),
        )
    button = str(request.get("button") or "left")
    if button not in {"left", "right"}:
        return 400, _host_agent_visual_failure(
            status="invalid_click_button",
            failure_code="INVALID_CLICK_BUTTON",
            detail="button must be left or right",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            selected_point=point,
        )
    source_review_media = session.get("observation_review_media", {}).get(source_observation_ref)
    pverify_preinput = _pverify_preinput_check(
        request,
        input_kind="mouse",
        source_frame=source_frame,
        source_review_media=source_review_media,
        coordinate_resolutions=[coordinate_resolution],
        coordinate_points=[point],
    )
    if pverify_preinput.get("blocking"):
        return 409, _host_agent_visual_failure(
            status="pverify_preinput_confirmation_required",
            failure_code="PVERIFY_PREINPUT_CONFIRMATION_REQUIRED",
            detail="require_pverify=true requires caller step_verification confirmations before host input is sent",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            pverify=pverify_preinput,
            selected_point=point,
            coordinate_resolution=coordinate_resolution,
        )

    adapter = session["adapter"]
    transcript: list[dict[str, Any]] = []
    lease = adapter.call_tool(
        "create_lease",
        {
            "duration_ms": int(request.get("duration_ms") or 10_000),
            "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
            "arming_ref": session["arming_ref"],
            "operator_consent_ref": session["operator_consent_ref"],
            "before_observation_ref": source_observation_ref,
            "after_observation_channel_ref": DEFAULT_OBSERVATION_CHANNEL_REF,
            "budget": {"max_input_events": 1},
        },
    )
    transcript.append(_compact_tool_response("create_lease", lease))
    execute: dict[str, Any] | None = None
    if lease.get("ok"):
        execute = adapter.call_tool(
            "execute_input",
            {
                "lease_id": lease["data"]["lease_id"],
                "input_type": "mouse_click",
                "x": point["x"],
                "y": point["y"],
                "button": button,
            },
        )
        transcript.append(_compact_tool_response("execute_input", execute))
    package, replay, integrity = _read_visual_evidence(adapter, transcript)
    execute_data = execute.get("data", {}) if isinstance(execute, dict) else {}
    after_observation = execute.get("after_observation") if isinstance(execute, dict) else None
    after_content = _media_content_diagnostics(_frame_media_path(after_observation if isinstance(after_observation, dict) else {}))
    after_cursor_review = _cursor_review_media_for_frame(
        after_observation if isinstance(after_observation, dict) else {},
        request=request,
        role="after_action",
    )
    after_review_media = _review_media_for_frame(
        after_observation if isinstance(after_observation, dict) else {},
        request=request,
        role="after_action",
    )
    after_observation_ref = (
        str(after_observation.get("observation_id"))
        if isinstance(after_observation, dict) and after_observation.get("observation_id")
        else None
    )
    if after_observation_ref and isinstance(after_observation, dict):
        session.setdefault("observations", {})[after_observation_ref] = after_observation
        session.setdefault("observation_content", {})[after_observation_ref] = after_content
        session.setdefault("observation_review_media", {})[after_observation_ref] = after_review_media
        session.setdefault("observation_cursor_review", {})[after_observation_ref] = after_cursor_review
    source_cursor_review = session.get("observation_cursor_review", {}).get(source_observation_ref)
    host_sent_event_count = _int_or_zero(execute_data.get("host_sent_event_count") or execute_data.get("sent_event_count"))
    host_input_sent = bool(execute_data.get("host_input_sent") or execute_data.get("host_input_executed") or host_sent_event_count)
    integrity_ok = bool(integrity.get("ok") and integrity.get("data", {}).get("ok"))
    click_status = "input_and_evidence_recorded" if execute and execute.get("ok") and integrity_ok else "input_or_evidence_failed"
    source_media_path = _frame_media_path(source_frame)
    after_media_path = _frame_media_path(after_observation if isinstance(after_observation, dict) else {})
    coordinate_integrity = _host_coordinate_integrity(source_frame, [point], coordinate_resolutions=[coordinate_resolution])
    report = {
        "object_type": "P0BVisualClickReport",
        "schema": "agentsight_p0b_visual_click_v1",
        "visual_session_id": visual_session_id,
        "click_status": click_status,
        "source_observation_ref": source_observation_ref,
        "source_media_path_abs": source_media_path,
        "raw_source_media_path_abs": source_media_path,
        "source_media_sha256": source_frame.get("media_sha256"),
        "source_observation_content": source_content,
        "source_review_media": source_review_media,
        "source_cursor_review_media": source_cursor_review,
        "selected_point": point,
        "coordinate_resolution": coordinate_resolution,
        "review_point": coordinate_resolution.get("review_point") if isinstance(coordinate_resolution, dict) else None,
        "monitor_point": coordinate_resolution.get("monitor_point") if isinstance(coordinate_resolution, dict) else point,
        "actual_execution_point": point,
        "coordinate_integrity": coordinate_integrity,
        "pverify": _pverify_after_report(
            pverify_preinput,
            after_observation=after_observation if isinstance(after_observation, dict) else None,
            after_content=after_content,
            after_review_media=after_review_media,
        ),
        "button": button,
        "coordinate_source": _coordinate_source_summary(request, source_frame=source_frame, point=point),
        "lease": lease,
        "execute": execute,
        "after_observation": after_observation,
        "after_media_path_abs": after_media_path,
        "raw_after_media_path_abs": after_media_path,
        "raw_after_media_role": "canonical_evidence",
        "raw_after_canonical": True,
        "after_media_sha256": after_observation.get("media_sha256") if isinstance(after_observation, dict) else None,
        "after_observation_content": after_content,
        "after_review_media": after_review_media,
        "after_review_media_path_abs": after_review_media.get("review_media_path_abs"),
        "after_review_image_size": after_review_media.get("review_image_size"),
        "after_review_transform": after_review_media.get("transform"),
        "include_cursor": _cursor_review_mode(request),
        "after_cursor_review_media": after_cursor_review,
        "after_cursor_media_path_abs": after_cursor_review.get("cursor_media_path_abs"),
        "after_annotated_media_path_abs": after_cursor_review.get("annotated_media_path_abs"),
        "host_input_sent": host_input_sent,
        "host_input_executed": bool(execute_data.get("host_input_executed") or host_input_sent),
        "host_sent_event_count": host_sent_event_count,
        "evidence_package": package,
        "replay": replay,
        "integrity": integrity,
        "evidence_package_ok": bool(package.get("ok")),
        "replay_read_only": bool(replay.get("ok")),
        "integrity_ok": integrity_ok,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "boundary": _host_agent_boundary_facts(),
        "transcript": transcript,
        "safe_report_lines": [
            f"P0-B visual click: visual_session_id={visual_session_id}; source_observation_ref={source_observation_ref}; selected_point=({point['x']},{point['y']}); host_sent_event_count={host_sent_event_count}.",
            f"coordinate_input_mode={coordinate_resolution.get('coordinate_input_mode')}; actual_execution_point={coordinate_resolution.get('actual_execution_point')}.",
            f"coordinate_integrity_schema={coordinate_integrity.get('schema')}; all_points_inside_source_screen_region={coordinate_integrity.get('all_points_inside_source_screen_region')}.",
            f"raw_source_media_path_abs={source_media_path}; raw_after_media_path_abs={after_media_path}.",
            f"source_review_media_path_abs={(source_review_media or {}).get('review_media_path_abs')}; after_review_media_path_abs={after_review_media.get('review_media_path_abs')}.",
            f"after_cursor_review_media_status={after_cursor_review.get('status')}; after_annotated_media_path_abs={after_cursor_review.get('annotated_media_path_abs')}.",
            f"pverify_status={pverify_preinput.get('status')}; pverify_missing_confirmations={pverify_preinput.get('missing_required_confirmations')}; after_image_review_status={_pverify_after_report(pverify_preinput, after_observation=after_observation if isinstance(after_observation, dict) else None, after_content=after_content, after_review_media=after_review_media).get('after_image_review_status')}.",
            f"evidence_package_ok={bool(package.get('ok'))}; replay_read_only={bool(replay.get('ok'))}; integrity_ok={integrity_ok}; after_capture_content_degenerate={bool(after_content.get('capture_content_degenerate'))}.",
            "The tool does not assert the target was hit, the click caused the visible change, or any business task succeeded; external visual review must inspect the saved images.",
        ],
    }
    session.setdefault("click_reports", []).append(report)
    return (200 if click_status == "input_and_evidence_recorded" else 500), report


def _host_agent_visual_input(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    visual_session_id = str(request.get("visual_session_id") or "")
    source_observation_ref = str(request.get("source_observation_ref") or request.get("observation_ref") or "")
    session = visual_sessions.get(visual_session_id)
    if not session:
        return 404, _host_agent_visual_failure(
            status="visual_session_missing",
            failure_code="VISUAL_SESSION_MISSING",
            detail="visual_session_id was not found; call /observe first",
            visual_session_id=visual_session_id,
        )
    source_frame = session["observations"].get(source_observation_ref)
    if not source_frame:
        return 404, _host_agent_visual_failure(
            status="source_observation_missing",
            failure_code="SOURCE_OBSERVATION_MISSING",
            detail="source_observation_ref was not found in the visual session",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    source_content = session["observation_content"].get(source_observation_ref) or _media_content_diagnostics(
        _frame_media_path(source_frame)
    )
    if source_content.get("capture_content_degenerate"):
        return 409, _host_agent_visual_failure(
            status="source_observation_degenerate",
            failure_code="SOURCE_OBSERVATION_DEGENERATE",
            detail="source observation is not a useful visual reference",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            source_observation_content=source_content,
        )
    request_error = _review_request_error(request)
    if request_error:
        return 400, _host_agent_visual_failure(
            status=request_error["status"],
            failure_code=request_error["failure_code"],
            detail=request_error["detail"],
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    forbidden = _forbidden_visual_input_fields(request)
    if forbidden:
        return 400, _host_agent_visual_failure(
            status="forbidden_input_source_fields",
            failure_code="FORBIDDEN_INPUT_SOURCE_FIELDS",
            detail={"forbidden_fields": forbidden},
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    input_payload, payload_error = _visual_input_payload(request)
    if payload_error:
        return 400, _host_agent_visual_failure(
            status="invalid_visual_input_payload",
            failure_code="INVALID_VISUAL_INPUT_PAYLOAD",
            detail=payload_error,
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    source_review_media = session.get("observation_review_media", {}).get(source_observation_ref)
    pverify_preinput = _pverify_preinput_check(
        request,
        input_kind="keyboard",
        source_frame=source_frame,
        source_review_media=source_review_media,
        coordinate_resolutions=[],
        coordinate_points=[],
    )
    if pverify_preinput.get("blocking"):
        return 409, _host_agent_visual_failure(
            status="pverify_preinput_confirmation_required",
            failure_code="PVERIFY_PREINPUT_CONFIRMATION_REQUIRED",
            detail="require_pverify=true requires caller step_verification confirmations before host input is sent",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            pverify=pverify_preinput,
        )

    adapter = session["adapter"]
    transcript: list[dict[str, Any]] = []
    lease = adapter.call_tool(
        "create_lease",
        {
            "duration_ms": int(request.get("duration_ms") or 10_000),
            "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
            "arming_ref": session["arming_ref"],
            "operator_consent_ref": session["operator_consent_ref"],
            "before_observation_ref": source_observation_ref,
            "after_observation_channel_ref": DEFAULT_OBSERVATION_CHANNEL_REF,
            "budget": {"max_input_events": 1},
        },
    )
    transcript.append(_compact_tool_response("create_lease", lease))
    execute: dict[str, Any] | None = None
    if lease.get("ok"):
        execute = adapter.call_tool("execute_input", {"lease_id": lease["data"]["lease_id"], **input_payload})
        transcript.append(_compact_tool_response("execute_input", execute))
    package, replay, integrity = _read_visual_evidence(adapter, transcript)
    execute_data = execute.get("data", {}) if isinstance(execute, dict) else {}
    after_observation = execute.get("after_observation") if isinstance(execute, dict) else None
    after_content = _media_content_diagnostics(_frame_media_path(after_observation if isinstance(after_observation, dict) else {}))
    after_cursor_review = _cursor_review_media_for_frame(
        after_observation if isinstance(after_observation, dict) else {},
        request=request,
        role="after_action",
    )
    after_review_media = _review_media_for_frame(
        after_observation if isinstance(after_observation, dict) else {},
        request=request,
        role="after_action",
    )
    after_observation_ref = (
        str(after_observation.get("observation_id"))
        if isinstance(after_observation, dict) and after_observation.get("observation_id")
        else None
    )
    if after_observation_ref and isinstance(after_observation, dict):
        session.setdefault("observations", {})[after_observation_ref] = after_observation
        session.setdefault("observation_content", {})[after_observation_ref] = after_content
        session.setdefault("observation_review_media", {})[after_observation_ref] = after_review_media
        session.setdefault("observation_cursor_review", {})[after_observation_ref] = after_cursor_review
    host_sent_event_count = _int_or_zero(execute_data.get("host_sent_event_count") or execute_data.get("sent_event_count"))
    host_input_sent = bool(execute_data.get("host_input_sent") or execute_data.get("host_input_executed") or host_sent_event_count)
    integrity_ok = bool(integrity.get("ok") and integrity.get("data", {}).get("ok"))
    input_status = "input_and_evidence_recorded" if execute and execute.get("ok") and integrity_ok else "input_or_evidence_failed"
    source_media_path = _frame_media_path(source_frame)
    after_media_path = _frame_media_path(after_observation if isinstance(after_observation, dict) else {})
    source_cursor_review = session.get("observation_cursor_review", {}).get(source_observation_ref)
    input_summary = _visual_input_summary(execute_data, input_payload)
    report = {
        "object_type": "P0CVisualInputReport",
        "schema": "agentsight_p0c_visual_input_v1",
        "visual_session_id": visual_session_id,
        "input_status": input_status,
        "source_observation_ref": source_observation_ref,
        "source_media_path_abs": source_media_path,
        "raw_source_media_path_abs": source_media_path,
        "source_media_sha256": source_frame.get("media_sha256"),
        "source_observation_content": source_content,
        "source_review_media": source_review_media,
        "source_cursor_review_media": source_cursor_review,
        "input_type": input_payload.get("input_type"),
        "input_summary": input_summary,
        "lease": lease,
        "execute": execute,
        "after_observation": after_observation,
        "after_observation_ref": after_observation_ref,
        "after_media_path_abs": after_media_path,
        "raw_after_media_path_abs": after_media_path,
        "raw_after_media_role": "canonical_evidence",
        "raw_after_canonical": True,
        "after_media_sha256": after_observation.get("media_sha256") if isinstance(after_observation, dict) else None,
        "after_observation_content": after_content,
        "pverify": _pverify_after_report(
            pverify_preinput,
            after_observation=after_observation if isinstance(after_observation, dict) else None,
            after_content=after_content,
            after_review_media=after_review_media,
        ),
        "after_review_media": after_review_media,
        "after_review_media_path_abs": after_review_media.get("review_media_path_abs"),
        "after_review_image_size": after_review_media.get("review_image_size"),
        "after_review_transform": after_review_media.get("transform"),
        "include_cursor": _cursor_review_mode(request),
        "after_cursor_review_media": after_cursor_review,
        "after_cursor_media_path_abs": after_cursor_review.get("cursor_media_path_abs"),
        "after_annotated_media_path_abs": after_cursor_review.get("annotated_media_path_abs"),
        "host_input_sent": host_input_sent,
        "host_input_executed": bool(execute_data.get("host_input_executed") or host_input_sent),
        "host_sent_event_count": host_sent_event_count,
        "evidence_package": package,
        "replay": replay,
        "integrity": integrity,
        "evidence_package_ok": bool(package.get("ok")),
        "replay_read_only": bool(replay.get("ok")),
        "integrity_ok": integrity_ok,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "tool_asserts_text_entered": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "boundary": _host_agent_boundary_facts(),
        "transcript": transcript,
        "safe_report_lines": [
            f"P0-C visual input: visual_session_id={visual_session_id}; source_observation_ref={source_observation_ref}; input_type={input_payload.get('input_type')}; host_sent_event_count={host_sent_event_count}.",
            f"raw_source_media_path_abs={source_media_path}; raw_after_media_path_abs={after_media_path}.",
            f"after_cursor_review_media_status={after_cursor_review.get('status')}; after_annotated_media_path_abs={after_cursor_review.get('annotated_media_path_abs')}.",
            f"pverify_status={pverify_preinput.get('status')}; pverify_missing_confirmations={pverify_preinput.get('missing_required_confirmations')}; after_image_review_status={_pverify_after_report(pverify_preinput, after_observation=after_observation if isinstance(after_observation, dict) else None, after_content=after_content, after_review_media=after_review_media).get('after_image_review_status')}.",
            f"evidence_package_ok={bool(package.get('ok'))}; replay_read_only={bool(replay.get('ok'))}; integrity_ok={integrity_ok}; after_capture_content_degenerate={bool(after_content.get('capture_content_degenerate'))}.",
            "The tool does not assert text entry, app launch, visible change causality, or business/task success; external visual review must inspect the saved images.",
        ],
    }
    session.setdefault("input_reports", []).append(report)
    return (200 if input_status == "input_and_evidence_recorded" else 500), report


def _host_agent_visual_mouse(
    *,
    visual_sessions: dict[str, dict[str, Any]],
    request: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    visual_session_id = str(request.get("visual_session_id") or "")
    source_observation_ref = str(request.get("source_observation_ref") or request.get("observation_ref") or "")
    session = visual_sessions.get(visual_session_id)
    if not session:
        return 404, _host_agent_visual_failure(
            status="visual_session_missing",
            failure_code="VISUAL_SESSION_MISSING",
            detail="visual_session_id was not found; call /observe first",
            visual_session_id=visual_session_id,
        )
    source_frame = session["observations"].get(source_observation_ref)
    if not source_frame:
        return 404, _host_agent_visual_failure(
            status="source_observation_missing",
            failure_code="SOURCE_OBSERVATION_MISSING",
            detail="source_observation_ref was not found in the visual session",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    source_content = session["observation_content"].get(source_observation_ref) or _media_content_diagnostics(
        _frame_media_path(source_frame)
    )
    if source_content.get("capture_content_degenerate"):
        return 409, _host_agent_visual_failure(
            status="source_observation_degenerate",
            failure_code="SOURCE_OBSERVATION_DEGENERATE",
            detail="source observation is not a useful visual reference",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            source_observation_content=source_content,
        )
    request_error = _review_request_error(request)
    if request_error:
        return 400, _host_agent_visual_failure(
            status=request_error["status"],
            failure_code=request_error["failure_code"],
            detail=request_error["detail"],
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    input_payload, coordinate_resolutions, payload_error = _visual_mouse_payload(
        request,
        session=session,
        source_observation_ref=source_observation_ref,
        source_frame=source_frame,
    )
    if payload_error:
        return 400, _host_agent_visual_failure(
            status=payload_error["status"],
            failure_code=payload_error["failure_code"],
            detail=payload_error["detail"],
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            **payload_error.get("extra", {}),
        )
    try:
        action_summary = mouse_action_summary(input_payload.get("input_type"), input_payload)
        coordinate_points = mouse_action_points(input_payload.get("input_type"), input_payload)
    except ValueError as exc:
        return 400, _host_agent_visual_failure(
            status="invalid_mouse_action",
            failure_code="INVALID_MOUSE_ACTION",
            detail=str(exc),
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
        )
    out_of_scope_points = [point for point in coordinate_points if not _point_inside_region(point, source_frame.get("screen_region"))]
    if out_of_scope_points:
        return 400, _host_agent_visual_failure(
            status="mouse_coordinates_outside_source_observation",
            failure_code="INPUT_COORDINATE_OUT_OF_SCOPE",
            detail="mouse action coordinates must be inside the referenced source observation screen_region",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            coordinate_points=coordinate_points,
            out_of_scope_points=out_of_scope_points,
            coordinate_resolutions=coordinate_resolutions,
            screen_region=source_frame.get("screen_region"),
        )
    source_review_media = session.get("observation_review_media", {}).get(source_observation_ref)
    pverify_preinput = _pverify_preinput_check(
        request,
        input_kind="mouse",
        source_frame=source_frame,
        source_review_media=source_review_media,
        coordinate_resolutions=coordinate_resolutions,
        coordinate_points=[{"x": int(point["x"]), "y": int(point["y"])} for point in coordinate_points],
    )
    if pverify_preinput.get("blocking"):
        return 409, _host_agent_visual_failure(
            status="pverify_preinput_confirmation_required",
            failure_code="PVERIFY_PREINPUT_CONFIRMATION_REQUIRED",
            detail="require_pverify=true requires caller step_verification confirmations before host input is sent",
            visual_session_id=visual_session_id,
            source_observation_ref=source_observation_ref,
            pverify=pverify_preinput,
            coordinate_points=coordinate_points,
            coordinate_resolutions=coordinate_resolutions,
        )

    adapter = session["adapter"]
    transcript: list[dict[str, Any]] = []
    lease = adapter.call_tool(
        "create_lease",
        {
            "duration_ms": int(request.get("duration_ms") or 10_000),
            "input_channel_ref": DEFAULT_INPUT_CHANNEL_REF,
            "arming_ref": session["arming_ref"],
            "operator_consent_ref": session["operator_consent_ref"],
            "before_observation_ref": source_observation_ref,
            "after_observation_channel_ref": DEFAULT_OBSERVATION_CHANNEL_REF,
            "budget": {"max_input_events": 1},
        },
    )
    transcript.append(_compact_tool_response("create_lease", lease))
    execute: dict[str, Any] | None = None
    if lease.get("ok"):
        execute = adapter.call_tool("execute_input", {"lease_id": lease["data"]["lease_id"], **input_payload})
        transcript.append(_compact_tool_response("execute_input", execute))
    package, replay, integrity = _read_visual_evidence(adapter, transcript)
    execute_data = execute.get("data", {}) if isinstance(execute, dict) else {}
    after_observation = execute.get("after_observation") if isinstance(execute, dict) else None
    after_content = _media_content_diagnostics(_frame_media_path(after_observation if isinstance(after_observation, dict) else {}))
    after_cursor_review = _cursor_review_media_for_frame(
        after_observation if isinstance(after_observation, dict) else {},
        request=request,
        role="after_action",
    )
    after_review_media = _review_media_for_frame(
        after_observation if isinstance(after_observation, dict) else {},
        request=request,
        role="after_action",
    )
    after_observation_ref = (
        str(after_observation.get("observation_id"))
        if isinstance(after_observation, dict) and after_observation.get("observation_id")
        else None
    )
    if after_observation_ref and isinstance(after_observation, dict):
        session.setdefault("observations", {})[after_observation_ref] = after_observation
        session.setdefault("observation_content", {})[after_observation_ref] = after_content
        session.setdefault("observation_review_media", {})[after_observation_ref] = after_review_media
        session.setdefault("observation_cursor_review", {})[after_observation_ref] = after_cursor_review
    host_sent_event_count = _int_or_zero(execute_data.get("host_sent_event_count") or execute_data.get("sent_event_count"))
    host_input_sent = bool(execute_data.get("host_input_sent") or execute_data.get("host_input_executed") or host_sent_event_count)
    integrity_ok = bool(integrity.get("ok") and integrity.get("data", {}).get("ok"))
    mouse_status = "input_and_evidence_recorded" if execute and execute.get("ok") and integrity_ok else "input_or_evidence_failed"
    source_media_path = _frame_media_path(source_frame)
    after_media_path = _frame_media_path(after_observation if isinstance(after_observation, dict) else {})
    source_cursor_review = session.get("observation_cursor_review", {}).get(source_observation_ref)
    coordinate_integrity = _host_coordinate_integrity(
        source_frame,
        [{"x": int(point["x"]), "y": int(point["y"])} for point in coordinate_points],
        coordinate_resolutions=coordinate_resolutions,
    )
    report = {
        "object_type": "P1AMouseActionReport",
        "schema": "agentsight_p1a_mouse_action_v1",
        "visual_session_id": visual_session_id,
        "mouse_status": mouse_status,
        "source_observation_ref": source_observation_ref,
        "source_media_path_abs": source_media_path,
        "raw_source_media_path_abs": source_media_path,
        "source_media_sha256": source_frame.get("media_sha256"),
        "source_observation_content": source_content,
        "source_review_media": source_review_media,
        "source_cursor_review_media": source_cursor_review,
        "input_type": input_payload.get("input_type"),
        "action": _mouse_action_name(input_payload.get("input_type")),
        "action_summary": action_summary,
        "coordinate_points": coordinate_points,
        "coordinate_resolutions": coordinate_resolutions,
        "coordinate_integrity": coordinate_integrity,
        "pverify": _pverify_after_report(
            pverify_preinput,
            after_observation=after_observation if isinstance(after_observation, dict) else None,
            after_content=after_content,
            after_review_media=after_review_media,
        ),
        "coordinate_source": _coordinate_source_summary(
            request,
            source_frame=source_frame,
            point={"x": int(coordinate_points[0]["x"]), "y": int(coordinate_points[0]["y"])},
        ),
        "lease": lease,
        "execute": execute,
        "after_observation": after_observation,
        "after_observation_ref": after_observation_ref,
        "after_media_path_abs": after_media_path,
        "raw_after_media_path_abs": after_media_path,
        "raw_after_media_role": "canonical_evidence",
        "raw_after_canonical": True,
        "after_media_sha256": after_observation.get("media_sha256") if isinstance(after_observation, dict) else None,
        "after_observation_content": after_content,
        "after_review_media": after_review_media,
        "after_review_media_path_abs": after_review_media.get("review_media_path_abs"),
        "after_review_image_size": after_review_media.get("review_image_size"),
        "after_review_transform": after_review_media.get("transform"),
        "include_cursor": _cursor_review_mode(request),
        "after_cursor_review_media": after_cursor_review,
        "after_cursor_media_path_abs": after_cursor_review.get("cursor_media_path_abs"),
        "after_annotated_media_path_abs": after_cursor_review.get("annotated_media_path_abs"),
        "host_input_sent": host_input_sent,
        "host_input_executed": bool(execute_data.get("host_input_executed") or host_input_sent),
        "host_sent_event_count": host_sent_event_count,
        "evidence_package": package,
        "replay": replay,
        "integrity": integrity,
        "evidence_package_ok": bool(package.get("ok")),
        "replay_read_only": bool(replay.get("ok")),
        "integrity_ok": integrity_ok,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_drag_completed": False,
        "tool_asserts_scroll_effect": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "boundary": _host_agent_boundary_facts(),
        "transcript": transcript,
        "safe_report_lines": [
            f"P1-A visual mouse action: visual_session_id={visual_session_id}; source_observation_ref={source_observation_ref}; input_type={input_payload.get('input_type')}; host_sent_event_count={host_sent_event_count}.",
            f"coordinate_points={coordinate_points}; requested_event_count={action_summary.get('requested_event_count')}.",
            f"coordinate_integrity_schema={coordinate_integrity.get('schema')}; all_points_inside_source_screen_region={coordinate_integrity.get('all_points_inside_source_screen_region')}.",
            f"raw_source_media_path_abs={source_media_path}; raw_after_media_path_abs={after_media_path}.",
            f"after_cursor_review_media_status={after_cursor_review.get('status')}; after_annotated_media_path_abs={after_cursor_review.get('annotated_media_path_abs')}.",
            f"pverify_status={pverify_preinput.get('status')}; pverify_missing_confirmations={pverify_preinput.get('missing_required_confirmations')}; after_image_review_status={_pverify_after_report(pverify_preinput, after_observation=after_observation if isinstance(after_observation, dict) else None, after_content=after_content, after_review_media=after_review_media).get('after_image_review_status')}.",
            f"evidence_package_ok={bool(package.get('ok'))}; replay_read_only={bool(replay.get('ok'))}; integrity_ok={integrity_ok}; after_capture_content_degenerate={bool(after_content.get('capture_content_degenerate'))}.",
            "The tool does not assert the target was hit, a drag/scroll had the intended effect, or any business task succeeded; external visual review must inspect the saved images.",
        ],
    }
    session.setdefault("mouse_reports", []).append(report)
    return (200 if mouse_status == "input_and_evidence_recorded" else 500), report


def _visual_observe_payload(request: dict[str, Any]) -> dict[str, Any]:
    mode = str(request.get("mode") or ("region" if isinstance(request.get("region"), dict) else "fullscreen"))
    payload: dict[str, Any] = {"mode": mode, "channel_ref": DEFAULT_OBSERVATION_CHANNEL_REF}
    if mode == "region" and isinstance(request.get("region"), dict):
        payload["region"] = request["region"]
    return payload


def _visual_click_point(request: dict[str, Any]) -> dict[str, int] | None:
    try:
        x = int(request.get("x"))
        y = int(request.get("y"))
    except (TypeError, ValueError):
        return None
    return {"x": x, "y": y}


def _resolve_visual_click_point(
    request: dict[str, Any],
    *,
    session: dict[str, Any],
    source_observation_ref: str,
    source_frame: dict[str, Any],
) -> tuple[dict[str, int] | None, dict[str, Any], dict[str, Any] | None]:
    direct = _visual_click_point(request)
    if direct:
        return direct, _direct_coordinate_resolution(direct, source_frame=source_frame), None
    if not (request.get("review_observation_ref") and request.get("review_x") is not None and request.get("review_y") is not None):
        return None, {}, None
    review_ref = str(request.get("review_observation_ref"))
    review_media = session.get("observation_review_media", {}).get(review_ref)
    if not isinstance(review_media, dict) or review_media.get("status") != "generated":
        return None, {}, {
            "status": "review_observation_missing",
            "failure_code": "REVIEW_OBSERVATION_MISSING",
            "detail": "review_observation_ref does not point to a generated review image in this visual session",
            "extra": {"review_observation_ref": review_ref},
        }
    try:
        review_x = int(request.get("review_x"))
        review_y = int(request.get("review_y"))
        scale = float(review_media["scale_factor"])
        crop = review_media["crop_region"]
        crop_x = int(crop["x"])
        crop_y = int(crop["y"])
        crop_width = int(crop["width"])
        crop_height = int(crop["height"])
        review_size = review_media["review_image_size"]
        review_width = int(review_size["width"])
        review_height = int(review_size["height"])
    except (KeyError, TypeError, ValueError):
        return None, {}, {
            "status": "invalid_review_coordinate_transform",
            "failure_code": "INVALID_REVIEW_COORDINATE_TRANSFORM",
            "detail": "stored review transform metadata is incomplete",
            "extra": {"review_observation_ref": review_ref, "review_media": review_media},
        }
    if review_x < 0 or review_y < 0 or review_x >= review_width or review_y >= review_height:
        return None, {}, {
            "status": "review_coordinate_out_of_scope",
            "failure_code": "INPUT_COORDINATE_OUT_OF_SCOPE",
            "detail": "review_x/review_y must be inside the referenced review image",
            "extra": {
                "review_observation_ref": review_ref,
                "review_point": {"x": review_x, "y": review_y},
                "review_image_size": {"width": review_width, "height": review_height},
            },
        }
    monitor_point = {
        "x": crop_x + int(review_x / scale),
        "y": crop_y + int(review_y / scale),
    }
    coordinate_resolution = {
        "coordinate_input_mode": "review_image_pixels",
        "review_observation_ref": review_ref,
        "source_observation_ref": source_observation_ref,
        "review_point": {"x": review_x, "y": review_y},
        "scale_factor": scale,
        "crop_region": {"x": crop_x, "y": crop_y, "width": crop_width, "height": crop_height},
        "review_image_size": {"width": review_width, "height": review_height},
        "coordinate_space": "review_image_pixels",
        "monitor_coordinate_space": review_media.get("monitor_coordinate_space") or source_frame.get("coordinate_system"),
        "transform": {
            "formula": "monitor_x = crop_region.x + floor(review_x / scale_factor); monitor_y = crop_region.y + floor(review_y / scale_factor)",
            "rounding_rule": "floor_after_dividing_review_coordinate_by_uniform_scale",
            "scale_type": "uniform",
        },
        "monitor_point": monitor_point,
        "actual_execution_point": monitor_point,
        "tool_generated_target": False,
        "tool_generated_coordinates": False,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
    if not _point_inside_region(monitor_point, coordinate_resolution["crop_region"]) or not _point_inside_region(
        monitor_point, source_frame.get("screen_region")
    ):
        return None, coordinate_resolution, {
            "status": "resolved_monitor_coordinate_out_of_scope",
            "failure_code": "INPUT_COORDINATE_OUT_OF_SCOPE",
            "detail": "resolved monitor coordinate is outside the crop/source observation region",
            "extra": {"coordinate_resolution": coordinate_resolution, "screen_region": source_frame.get("screen_region")},
        }
    return monitor_point, coordinate_resolution, None


def _direct_coordinate_resolution(point: dict[str, int], *, source_frame: dict[str, Any]) -> dict[str, Any]:
    return {
        "coordinate_input_mode": "monitor_pixels",
        "coordinate_space": source_frame.get("coordinate_system") or "monitor_pixels",
        "monitor_point": point,
        "actual_execution_point": point,
        "screen_region": source_frame.get("screen_region"),
        "tool_generated_target": False,
        "tool_generated_coordinates": False,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _point_inside_region(point: dict[str, int], region: Any) -> bool:
    if not isinstance(region, dict):
        return False
    try:
        x = int(region["x"])
        y = int(region["y"])
        width = int(region["width"])
        height = int(region["height"])
    except (KeyError, TypeError, ValueError):
        return False
    return x <= point["x"] < x + width and y <= point["y"] < y + height


def _host_coordinate_integrity(
    source_frame: dict[str, Any],
    points: list[dict[str, int]],
    *,
    coordinate_resolutions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    screen_region = source_frame.get("screen_region")
    return {
        "schema": "agentsight_p1c_coordinate_integrity_v1",
        "source_observation_ref": source_frame.get("observation_id"),
        "source_coordinate_system": source_frame.get("coordinate_system"),
        "source_screen_region": screen_region,
        "execution_coordinate_space": "visible_screen_pixels",
        "coordinate_points": points,
        "coordinate_resolutions": coordinate_resolutions or [],
        "scope_checked_against_source_screen_region": True,
        "all_points_inside_source_screen_region": bool(points) and all(_point_inside_region(point, screen_region) for point in points),
        "negative_virtual_coordinates_supported": True,
        "coordinate_transform_performed_by_tool": any(
            item.get("coordinate_input_mode") == "review_image_pixels" for item in (coordinate_resolutions or [])
        ),
        "transform_kind": "review_pixel_arithmetic_only"
        if any(item.get("coordinate_input_mode") == "review_image_pixels" for item in (coordinate_resolutions or []))
        else "none",
        "tool_generated_coordinates": False,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _pverify_preinput_check(
    request: dict[str, Any],
    *,
    input_kind: str,
    source_frame: dict[str, Any],
    source_review_media: dict[str, Any] | None,
    coordinate_resolutions: list[dict[str, Any]],
    coordinate_points: list[dict[str, int]],
) -> dict[str, Any]:
    required = _truthy(request.get("require_pverify") or request.get("pverify_required"))
    caller_verification = _caller_step_verification(request)
    if input_kind == "keyboard":
        required_confirmations = [
            "keyboard_focus_target_visible",
            "target_not_occluded",
            "foreground_or_focus_checked",
            "after_image_review_planned",
        ]
    else:
        required_confirmations = [
            "coordinate_space_confirmed",
            "target_visible_in_source",
            "target_not_occluded",
            "foreground_or_focus_checked",
            "after_image_review_planned",
        ]
    confirmations = {
        name: _truthy(caller_verification.get(name))
        for name in required_confirmations
    }
    missing = [name for name in required_confirmations if not confirmations.get(name)]
    coordinate_modes = [
        str(item.get("coordinate_input_mode") or "")
        for item in coordinate_resolutions
        if isinstance(item, dict)
    ]
    scaled_review_available = (
        isinstance(source_review_media, dict)
        and source_review_media.get("status") == "generated"
        and _float_or_zero(source_review_media.get("scale_factor")) < 1.0
    )
    direct_monitor_coordinates = bool(coordinate_modes) and all(mode == "monitor_pixels" for mode in coordinate_modes)
    risk_flags: list[str] = []
    if scaled_review_available:
        risk_flags.append("source_has_scaled_review_media")
    if direct_monitor_coordinates and scaled_review_available:
        risk_flags.append("direct_monitor_coordinates_while_scaled_review_available")
    if not caller_verification:
        risk_flags.append("caller_step_verification_missing")
    if not confirmations.get("after_image_review_planned"):
        risk_flags.append("after_image_review_not_predeclared")
    if input_kind == "keyboard" and not confirmations.get("foreground_or_focus_checked"):
        risk_flags.append("keyboard_focus_or_foreground_not_predeclared")
    if input_kind != "keyboard" and not confirmations.get("coordinate_space_confirmed"):
        risk_flags.append("coordinate_space_not_predeclared")

    blocking = required and bool(missing)
    return {
        "object_type": "AgentSightPVerifyPreInputCheck",
        "schema": "agentsight_pverify_step_verification_v1",
        "pverify_required": required,
        "status": "blocked_missing_preinput_confirmations"
        if blocking
        else ("preinput_confirmations_recorded" if required else "pverify_not_required_recorded"),
        "blocking": blocking,
        "input_kind": input_kind,
        "source_observation_ref": source_frame.get("observation_id"),
        "source_media_sha256": source_frame.get("media_sha256"),
        "source_screen_region": source_frame.get("screen_region"),
        "required_confirmations": required_confirmations,
        "caller_confirmations": confirmations,
        "missing_required_confirmations": missing if required else [],
        "risk_flags": risk_flags,
        "coordinate_modes": coordinate_modes,
        "coordinate_points": coordinate_points,
        "source_review_scale_factor": source_review_media.get("scale_factor") if isinstance(source_review_media, dict) else None,
        "source_review_media_path_abs": source_review_media.get("review_media_path_abs") if isinstance(source_review_media, dict) else None,
        "external_visual_review_required": True,
        "tool_asserts_target_visible": False,
        "tool_asserts_target_not_occluded": False,
        "tool_asserts_after_review_passed": False,
        "tool_asserts_business_success": False,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _pverify_after_report(
    preinput: dict[str, Any],
    *,
    after_observation: dict[str, Any] | None,
    after_content: dict[str, Any],
    after_review_media: dict[str, Any],
) -> dict[str, Any]:
    after_path = _frame_media_path(after_observation or {})
    after_present = bool(after_path)
    after_degenerate = bool(after_content.get("capture_content_degenerate"))
    if preinput.get("blocking"):
        review_status = "blocked_before_host_input"
    elif not after_present:
        review_status = "after_image_missing"
    elif after_degenerate:
        review_status = "after_image_degenerate_external_review_not_valid"
    else:
        review_status = "pending_external_after_image_review"
    return {
        **preinput,
        "after_observation_ref": after_observation.get("observation_id") if isinstance(after_observation, dict) else None,
        "after_media_path_abs": after_path,
        "after_capture_content_degenerate": after_degenerate,
        "after_review_media_path_abs": after_review_media.get("review_media_path_abs") if isinstance(after_review_media, dict) else None,
        "after_image_review_status": review_status,
        "after_image_available_for_external_review": after_present and not after_degenerate,
        "tool_asserts_after_image_checked_by_ai": False,
        "tool_asserts_visible_change": False,
        "tool_asserts_causality": False,
        "tool_asserts_business_success": False,
    }


def _caller_step_verification(request: dict[str, Any]) -> dict[str, Any]:
    for key in ("step_verification", "caller_verification", "pverify"):
        value = request.get(key)
        if isinstance(value, dict):
            return dict(value)
    return {}


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "on", "confirmed", "pass"}
    return False


def _float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _coordinate_source_summary(
    request: dict[str, Any],
    *,
    source_frame: dict[str, Any],
    point: dict[str, int],
) -> dict[str, Any]:
    source = request.get("coordinate_source")
    summary = dict(source) if isinstance(source, dict) else {}
    summary.update(
        {
            "coordinate_source_kind": summary.get("coordinate_source_kind") or "external_visual_ai_review",
            "source_observation_ref": source_frame.get("observation_id"),
            "source_media_path_abs": _frame_media_path(source_frame),
            "source_media_sha256": source_frame.get("media_sha256"),
            "selected_point": point,
            "tool_generated_coordinates": False,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
        }
    )
    if "target_label" in request:
        summary["caller_target_label"] = request.get("target_label")
    if "visual_basis" in request:
        summary["caller_visual_basis"] = request.get("visual_basis")
    return summary


def _forbidden_visual_input_fields(request: dict[str, Any]) -> list[str]:
    disallowed_paste_field = "paste" + "_text"
    forbidden = {
        "clipboard_text",
        "clipboard",
        disallowed_paste_field,
        "pasted_text",
        "source",
        "text_file",
        "text_source",
        "file_path",
        "file_source",
        "command",
        "cmd",
        "shell",
        "powershell",
        "window_handle",
        "hwnd",
        "selector",
        "dom",
        "accessibility_tree",
        "window_semantics",
        "ocr_text",
    }
    return sorted(key for key in forbidden if key in request)


def _visual_input_payload(request: dict[str, Any]) -> tuple[dict[str, Any] | None, Any]:
    input_type = str(request.get("input_type") or "")
    try:
        if input_type == "key_text_stream":
            text = validate_key_text_stream(request.get("text"))
            return {"input_type": "key_text_stream", "text": text}, None
        if input_type in {"key_press", "key_down", "key_up"}:
            payload = {"input_type": input_type, "key": request.get("key")}
            keyboard_action_summary(input_type, payload)
            return payload, None
        if input_type == "key_chord":
            payload = {
                "input_type": "key_chord",
                "modifiers": request.get("modifiers"),
                "key": request.get("key"),
            }
            keyboard_action_summary("key_chord", payload)
            return payload, None
    except ValueError as exc:
        return None, str(exc)
    return None, "input_type must be key_text_stream, key_press, key_chord, key_down, or key_up"


def _visual_input_summary(execute_data: dict[str, Any], input_payload: dict[str, Any]) -> dict[str, Any]:
    input_type = input_payload.get("input_type")
    if input_type == "key_text_stream":
        text = validate_key_text_stream(input_payload.get("text"))
        summary = key_text_summary(text)
        return {
            "input_type": "key_text_stream",
            **summary,
            "text_redacted": True,
            "clipboard_api_used": False,
            "file_source_used": False,
            "command_source_used": False,
        }
    if input_type in {"key_press", "key_chord", "key_down", "key_up"}:
        payload = dict(input_payload)
        payload.pop("lease_id", None)
        if payload.get("modifiers") is None:
            payload.pop("modifiers", None)
        return {
            **keyboard_action_summary(str(input_type), payload),
            "clipboard_api_used": False,
            "file_source_used": False,
            "command_source_used": False,
        }
    return {"input_type": input_type, "clipboard_api_used": False, "file_source_used": False, "command_source_used": False}


def _visual_mouse_payload(
    request: dict[str, Any],
    *,
    session: dict[str, Any],
    source_observation_ref: str,
    source_frame: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
    input_type = _mouse_input_type_from_request(request)
    if not is_mouse_action(input_type):
        return {}, [], {
            "status": "invalid_mouse_action",
            "failure_code": "INVALID_MOUSE_ACTION",
            "detail": "action must be move, click, double_click, button_down, button_up, drag, or scroll",
        }
    point, coordinate_resolution, point_error = _resolve_visual_click_point(
        request,
        session=session,
        source_observation_ref=source_observation_ref,
        source_frame=source_frame,
    )
    if point_error:
        return {}, [], point_error
    if not point:
        return {}, [], {
            "status": "invalid_mouse_coordinates",
            "failure_code": "INVALID_MOUSE_COORDINATES",
            "detail": "provide integer monitor-pixel x/y or review_observation_ref plus integer review_x/review_y",
        }
    payload: dict[str, Any] = {"input_type": input_type, "x": point["x"], "y": point["y"]}
    coordinate_resolutions = [{"role": "point", **coordinate_resolution}]
    if input_type not in {"mouse_move", "mouse_scroll"}:
        payload["button"] = str(request.get("button") or "left").strip().lower()
    if input_type == "mouse_drag":
        target, target_resolution, target_error = _resolve_visual_mouse_target_point(request, source_frame=source_frame)
        if target_error:
            return {}, coordinate_resolutions, target_error
        payload["to_x"] = target["x"]
        payload["to_y"] = target["y"]
        coordinate_resolutions.append({"role": "target", **target_resolution})
    if input_type == "mouse_scroll":
        if "wheel_delta" in request:
            payload["wheel_delta"] = request.get("wheel_delta")
        if "vertical_wheel_delta" in request:
            payload["vertical_wheel_delta"] = request.get("vertical_wheel_delta")
        if "horizontal_wheel_delta" in request:
            payload["horizontal_wheel_delta"] = request.get("horizontal_wheel_delta")
    try:
        mouse_action_summary(input_type, payload)
    except ValueError as exc:
        return {}, coordinate_resolutions, {
            "status": "invalid_mouse_action",
            "failure_code": "INVALID_MOUSE_ACTION",
            "detail": str(exc),
        }
    return payload, coordinate_resolutions, None


def _resolve_visual_mouse_target_point(
    request: dict[str, Any],
    *,
    source_frame: dict[str, Any],
) -> tuple[dict[str, int], dict[str, Any], dict[str, Any] | None]:
    try:
        target = {"x": int(request.get("to_x")), "y": int(request.get("to_y"))}
    except (TypeError, ValueError):
        return {}, {}, {
            "status": "invalid_drag_target_coordinates",
            "failure_code": "INVALID_DRAG_TARGET_COORDINATES",
            "detail": "mouse_drag requires integer to_x/to_y monitor-pixel target coordinates",
        }
    return target, _direct_coordinate_resolution(target, source_frame=source_frame), None


def _mouse_input_type_from_request(request: dict[str, Any]) -> str:
    raw = request.get("input_type")
    if isinstance(raw, str) and raw.startswith("mouse_"):
        return raw.strip().lower()
    action = str(request.get("mouse_action") or request.get("action") or "click").strip().lower()
    aliases = {
        "move": "mouse_move",
        "mouse_move": "mouse_move",
        "click": "mouse_click",
        "mouse_click": "mouse_click",
        "double_click": "mouse_double_click",
        "dblclick": "mouse_double_click",
        "mouse_double_click": "mouse_double_click",
        "button_down": "mouse_button_down",
        "down": "mouse_button_down",
        "mouse_button_down": "mouse_button_down",
        "button_up": "mouse_button_up",
        "up": "mouse_button_up",
        "mouse_button_up": "mouse_button_up",
        "drag": "mouse_drag",
        "mouse_drag": "mouse_drag",
        "scroll": "mouse_scroll",
        "wheel": "mouse_scroll",
        "mouse_scroll": "mouse_scroll",
    }
    return aliases.get(action, action)


def _mouse_action_name(input_type: Any) -> str | None:
    if not isinstance(input_type, str) or not input_type.startswith("mouse_"):
        return None
    return input_type.removeprefix("mouse_")


def _read_visual_evidence(adapter: Any, transcript: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    package = adapter.call_tool("get_evidence_package")
    replay = adapter.call_tool("read_replay")
    integrity = adapter.call_tool("verify_integrity")
    transcript.append(_compact_tool_response("get_evidence_package", package))
    transcript.append(_compact_tool_response("read_replay", replay))
    transcript.append(_compact_tool_response("verify_integrity", integrity))
    return package, replay, integrity


def _compact_tool_response(step: str, response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data", {}) if isinstance(response.get("data"), dict) else {}
    failure = response.get("failure", {}) if isinstance(response.get("failure"), dict) else {}
    return {
        "step": step,
        "ok": bool(response.get("ok")),
        "evidence_ref": response.get("evidence_ref"),
        "failure_code": failure.get("failure_code"),
        "object_type": data.get("object_type"),
        "observation_id": data.get("observation_id"),
        "input_event_id": data.get("input_event_id"),
        "host_input_sent": data.get("host_input_sent"),
        "host_input_executed": data.get("host_input_executed"),
        "host_sent_event_count": data.get("host_sent_event_count") or data.get("sent_event_count"),
    }


def _frame_media_path(frame: dict[str, Any]) -> str | None:
    media_access = frame.get("media_access") if isinstance(frame, dict) else None
    if isinstance(media_access, dict) and media_access.get("media_path_abs"):
        return str(media_access["media_path_abs"])
    if isinstance(frame, dict) and frame.get("media_path_abs"):
        return str(frame["media_path_abs"])
    return None


def _cursor_review_mode(request: dict[str, Any]) -> str:
    value = request.get("include_cursor", request.get("cursor_capture", False))
    if value is True:
        return "true"
    if value is False or value is None:
        return "false"
    text = str(value).strip().lower()
    if text in {"both", "raw_and_cursor"}:
        return "both"
    if text in {"true", "1", "yes", "on"}:
        return "true"
    return "false"


def _review_request_error(request: dict[str, Any]) -> dict[str, Any] | None:
    if str(request.get("raw_evidence", True)).strip().lower() in {"false", "0", "no", "off"}:
        return {
            "status": "raw_evidence_required",
            "failure_code": "RAW_EVIDENCE_REQUIRED",
            "detail": "P0-D requires raw_evidence=true so canonical raw frames remain the integrity truth source",
        }
    non_uniform_fields = [field for field in ("review_scale_x", "review_scale_y", "scale_x", "scale_y") if field in request]
    if non_uniform_fields:
        return {
            "status": "non_uniform_review_scale_rejected",
            "failure_code": "NON_UNIFORM_REVIEW_SCALE_UNSUPPORTED",
            "detail": {"fields": non_uniform_fields, "allowed": "review_scale uniform scalar only"},
        }
    if "review_scale" in request:
        try:
            scale = float(request["review_scale"])
        except (TypeError, ValueError):
            return {
                "status": "invalid_review_scale",
                "failure_code": "INVALID_REVIEW_SCALE",
                "detail": "review_scale must be a uniform number between 0.25 and 1.0",
            }
        if scale < 0.25 or scale > 1.0:
            return {
                "status": "invalid_review_scale",
                "failure_code": "INVALID_REVIEW_SCALE",
                "detail": "review_scale must be between 0.25 and 1.0; v1 does not upscale or generate ultra-low review frames",
            }
    return None


def _review_scale_for_frame(frame: dict[str, Any], request: dict[str, Any]) -> tuple[float, str]:
    if "review_scale" in request:
        return float(request["review_scale"]), "request.review_scale"
    if frame.get("mode") == "region":
        return 1.0, "default_region_scale_1"
    return 0.5, "default_fullscreen_preview_scale_0.5"


def _review_media_for_frame(frame: dict[str, Any], *, request: dict[str, Any], role: str) -> dict[str, Any]:
    raw_path = _frame_media_path(frame)
    scale, scale_source = _review_scale_for_frame(frame if isinstance(frame, dict) else {}, request)
    region = frame.get("screen_region") if isinstance(frame, dict) else None
    if not isinstance(region, dict):
        region = {"x": 0, "y": 0, "width": frame.get("width"), "height": frame.get("height")} if isinstance(frame, dict) else {}
    base: dict[str, Any] = {
        "object_type": "HostAgentReviewMedia",
        "schema": "agentsight_host_review_media_v1",
        "status": "not_generated",
        "media_role": "derived_review_only",
        "canonical": False,
        "visualization_only": True,
        "excluded_from_integrity_truth_source": True,
        "raw_frame_remains_canonical_evidence": True,
        "raw_media_path_abs": raw_path,
        "derived_from_media_ref": frame.get("media_ref") if isinstance(frame, dict) else None,
        "derived_from_observation_ref": frame.get("observation_id") if isinstance(frame, dict) else None,
        "derived_from_sha256": frame.get("media_sha256") if isinstance(frame, dict) else None,
        "derived_from_media_path_abs": raw_path,
        "scale_factor": scale,
        "scale_source": scale_source,
        "scale_type": "uniform",
        "crop_region": region,
        "monitor_coordinate_space": frame.get("coordinate_system") if isinstance(frame, dict) else None,
        "include_cursor": _cursor_review_mode(request),
        "review_media_path_abs": None,
        "cursor_media_path_abs": None,
        "annotated_media_path_abs": None,
        "cursor_capture_native_used": False,
        "overlay_used": False,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
    if not raw_path:
        return {**base, "status": "raw_media_missing"}
    try:
        crop_x = int(region["x"])
        crop_y = int(region["y"])
        crop_width = int(region["width"])
        crop_height = int(region["height"])
    except (KeyError, TypeError, ValueError):
        return {**base, "status": "screen_region_unavailable"}
    raw = Path(raw_path)
    try:
        width, height, rows = _read_png_rgb_rows(raw)
        review_width, review_height, review_rows = _scale_rgb_rows(rows, width=width, height=height, scale=scale)
    except Exception as exc:
        return {**base, "status": "review_generation_failed", "error": str(exc)}

    cursor_overlay: dict[str, Any] = {"cursor_overlay_status": "not_requested"}
    if _cursor_review_mode(request) != "false":
        cursor = _cursor_probe()
        cursor_overlay = {"cursor_overlay_status": "cursor_position_unavailable", "cursor_probe": cursor}
        if cursor.get("ok"):
            try:
                cursor_x = int(cursor["x"])
                cursor_y = int(cursor["y"])
            except (TypeError, ValueError, KeyError):
                cursor_overlay = {"cursor_overlay_status": "cursor_position_unavailable", "cursor_probe": cursor}
            else:
                if crop_x <= cursor_x < crop_x + crop_width and crop_y <= cursor_y < crop_y + crop_height:
                    review_cursor_x = int((cursor_x - crop_x) * scale)
                    review_cursor_y = int((cursor_y - crop_y) * scale)
                    _draw_cursor_marker(
                        review_rows,
                        width=review_width,
                        height=review_height,
                        x=review_cursor_x,
                        y=review_cursor_y,
                    )
                    cursor_overlay = {
                        "cursor_overlay_status": "generated",
                        "overlay_used": True,
                        "cursor_position_source": "GetCursorPos_at_review_image_generation_time",
                        "cursor_position_screen": {"x": cursor_x, "y": cursor_y},
                        "cursor_position_in_frame": {"x": cursor_x - crop_x, "y": cursor_y - crop_y},
                        "cursor_position_in_review": {"x": review_cursor_x, "y": review_cursor_y},
                    }
                else:
                    cursor_overlay = {
                        "cursor_overlay_status": "cursor_outside_frame",
                        "cursor_position_source": "GetCursorPos_at_review_image_generation_time",
                        "cursor_position_screen": {"x": cursor_x, "y": cursor_y},
                    }

    scale_label = str(scale).replace(".", "p")
    cursor_label = ".cursor" if cursor_overlay.get("overlay_used") else ""
    output_path = raw.with_name(f"{raw.stem}.{role}.review-s{scale_label}{cursor_label}.png")
    try:
        png_bytes = _png_from_rgb_rows(review_rows, width=review_width, height=review_height)
        output_path.write_bytes(png_bytes)
    except Exception as exc:
        return {**base, "status": "review_write_failed", "error": str(exc)}
    review_sha256 = hashlib.sha256(png_bytes).hexdigest()
    review_image_size = {"width": review_width, "height": review_height}
    transform = {
        "formula": "review_x = floor((monitor_x - crop_region.x) * scale_factor); review_y = floor((monitor_y - crop_region.y) * scale_factor)",
        "inverse_formula": "monitor_x = crop_region.x + floor(review_x / scale_factor); monitor_y = crop_region.y + floor(review_y / scale_factor)",
        "rounding_rule": "floor_for_forward_projection_and_floor_after_inverse_division",
        "scale_type": "uniform",
    }
    return {
        **base,
        **cursor_overlay,
        "status": "generated",
        "review_media_path_abs": str(output_path),
        "cursor_media_path_abs": str(output_path) if cursor_overlay.get("overlay_used") else None,
        "annotated_media_path_abs": str(output_path) if cursor_overlay.get("overlay_used") else None,
        "media_format": "png",
        "width": review_width,
        "height": review_height,
        "byte_size": len(png_bytes),
        "media_sha256": review_sha256,
        "review_image_size": review_image_size,
        "source_image_size": {"width": width, "height": height},
        "crop_region": {"x": crop_x, "y": crop_y, "width": crop_width, "height": crop_height},
        "transform": transform,
        "coordinate_space": "review_image_pixels",
        "monitor_coordinate_space": frame.get("coordinate_system") if isinstance(frame, dict) else None,
    }


def _cursor_review_media_for_frame(frame: dict[str, Any], *, request: dict[str, Any], role: str) -> dict[str, Any]:
    mode = _cursor_review_mode(request)
    raw_path = _frame_media_path(frame)
    base: dict[str, Any] = {
        "include_cursor": mode,
        "status": "not_requested" if mode == "false" else "not_generated",
        "raw_media_path_abs": raw_path,
        "cursor_media_path_abs": None,
        "annotated_media_path_abs": None,
        "media_role": "derived_review_only",
        "canonical": False,
        "visualization_only": True,
        "excluded_from_integrity_truth_source": True,
        "raw_frame_remains_canonical_evidence": True,
        "cursor_capture_native_used": False,
        "overlay_used": False,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }
    if mode == "false":
        return base
    if not raw_path:
        return {**base, "status": "raw_media_missing"}
    cursor = _cursor_probe()
    if not cursor.get("ok"):
        return {**base, "status": "cursor_position_unavailable", "cursor_probe": cursor}
    region = frame.get("screen_region") if isinstance(frame, dict) else None
    if not isinstance(region, dict):
        region = {"x": 0, "y": 0, "width": frame.get("width"), "height": frame.get("height")} if isinstance(frame, dict) else {}
    try:
        left = int(region.get("x", 0))
        top = int(region.get("y", 0))
        width = int(region.get("width"))
        height = int(region.get("height"))
        cursor_x = int(cursor["x"])
        cursor_y = int(cursor["y"])
    except (TypeError, ValueError, KeyError):
        return {**base, "status": "cursor_or_region_unavailable", "cursor_probe": cursor, "screen_region": region}
    if cursor_x < left or cursor_y < top or cursor_x >= left + width or cursor_y >= top + height:
        return {
            **base,
            "status": "cursor_outside_frame",
            "cursor_position_source": "GetCursorPos_at_review_image_generation_time",
            "cursor_position_screen": {"x": cursor_x, "y": cursor_y},
            "screen_region": region,
        }
    raw = Path(raw_path)
    output_path = raw.with_name(f"{raw.stem}.{role}.cursor-overlay.png")
    try:
        overlay = _write_cursor_overlay_png(
            raw,
            output_path,
            cursor_x=cursor_x - left,
            cursor_y=cursor_y - top,
        )
    except Exception as exc:
        return {
            **base,
            "status": "overlay_generation_failed",
            "error": str(exc),
            "cursor_position_source": "GetCursorPos_at_review_image_generation_time",
            "cursor_position_screen": {"x": cursor_x, "y": cursor_y},
            "screen_region": region,
        }
    return {
        **base,
        **overlay,
        "status": "generated",
        "cursor_media_path_abs": str(output_path),
        "annotated_media_path_abs": str(output_path),
        "annotation_method": "cursor_position_overlay",
        "overlay_used": True,
        "cursor_position_source": "GetCursorPos_at_review_image_generation_time",
        "cursor_position_screen": {"x": cursor_x, "y": cursor_y},
        "cursor_position_in_frame": {"x": cursor_x - left, "y": cursor_y - top},
        "screen_region": region,
    }


def _write_cursor_overlay_png(raw_path: Path, output_path: Path, *, cursor_x: int, cursor_y: int) -> dict[str, Any]:
    width, height, rows = _read_png_rgb_rows(raw_path)
    _draw_cursor_marker(rows, width=width, height=height, x=cursor_x, y=cursor_y)
    png_bytes = _png_from_rgb_rows(rows, width=width, height=height)
    output_path.write_bytes(png_bytes)
    return {
        "media_format": "png",
        "width": width,
        "height": height,
        "byte_size": len(png_bytes),
        "media_sha256": hashlib.sha256(png_bytes).hexdigest(),
    }


def _scale_rgb_rows(
    rows: list[bytearray],
    *,
    width: int,
    height: int,
    scale: float,
) -> tuple[int, int, list[bytearray]]:
    review_width = max(1, int(round(width * scale)))
    review_height = max(1, int(round(height * scale)))
    if scale == 1.0:
        return review_width, review_height, [bytearray(row) for row in rows]
    scaled: list[bytearray] = []
    for y in range(review_height):
        source_y = min(height - 1, int(y / scale))
        source_row = rows[source_y]
        target = bytearray()
        for x in range(review_width):
            source_x = min(width - 1, int(x / scale))
            offset = source_x * 3
            target.extend(source_row[offset : offset + 3])
        scaled.append(target)
    return review_width, review_height, scaled


def _read_png_rgb_rows(path: Path) -> tuple[int, int, list[bytearray]]:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("raw media is not a PNG")
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
        raise ValueError("unsupported PNG layout for cursor overlay")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[int(color_type)]
    raw = zlib.decompress(bytes(idat))
    stride = int(width) * channels
    rows: list[bytearray] = []
    source_offset = 0
    previous = bytes(stride)
    for _y in range(int(height)):
        filter_type = raw[source_offset]
        source_offset += 1
        row = bytearray(raw[source_offset : source_offset + stride])
        source_offset += stride
        _png_unfilter_row(row, previous, filter_type, channels)
        previous = bytes(row)
        rows.append(_png_row_to_rgb(row, color_type=int(color_type), channels=channels))
    return int(width), int(height), rows


def _png_row_to_rgb(row: bytearray, *, color_type: int, channels: int) -> bytearray:
    rgb = bytearray()
    for offset in range(0, len(row), channels):
        if color_type == 0:
            value = row[offset]
            rgb.extend((value, value, value))
        elif color_type == 2:
            rgb.extend(row[offset : offset + 3])
        elif color_type == 4:
            value = row[offset]
            rgb.extend((value, value, value))
        else:
            rgb.extend(row[offset : offset + 3])
    return rgb


def _png_unfilter_row(row: bytearray, previous: bytes, filter_type: int, bpp: int) -> None:
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
            value = row[index] + _png_paeth(left, up, upper_left)
        else:
            raise ValueError(f"unsupported png filter type {filter_type}")
        row[index] = value & 0xFF


def _png_paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    distance_left = abs(estimate - left)
    distance_up = abs(estimate - up)
    distance_upper_left = abs(estimate - upper_left)
    if distance_left <= distance_up and distance_left <= distance_upper_left:
        return left
    if distance_up <= distance_upper_left:
        return up
    return upper_left


def _png_from_rgb_rows(rows: list[bytearray], *, width: int, height: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    raw = bytearray()
    for row in rows:
        if len(row) != width * 3:
            raise ValueError("unexpected RGB row length")
        raw.append(0)
        raw.extend(row)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b"")


def _draw_cursor_marker(rows: list[bytearray], *, width: int, height: int, x: int, y: int) -> None:
    fill_points: set[tuple[int, int]] = set()
    for dy in range(0, 20):
        max_dx = min(9, dy // 2 + 1)
        for dx in range(max_dx):
            fill_points.add((x + dx, y + dy))
    for dx in range(5, 12):
        for dy in range(13, 17):
            fill_points.add((x + dx, y + dy))
    for px, py in list(fill_points):
        for ox in (-1, 0, 1):
            for oy in (-1, 0, 1):
                _set_rgb(rows, width=width, height=height, x=px + ox, y=py + oy, color=(0, 0, 0))
    for px, py in fill_points:
        _set_rgb(rows, width=width, height=height, x=px, y=py, color=(255, 255, 255))
    for dx in range(-3, 4):
        _set_rgb(rows, width=width, height=height, x=x + dx, y=y, color=(255, 40, 40))
        _set_rgb(rows, width=width, height=height, x=x, y=y + dx, color=(255, 40, 40))


def _set_rgb(
    rows: list[bytearray],
    *,
    width: int,
    height: int,
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    if x < 0 or y < 0 or x >= width or y >= height:
        return
    offset = x * 3
    rows[y][offset : offset + 3] = bytes(color)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _host_agent_boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _current_host_agent_process_identity() -> dict[str, Any]:
    return {
        "role": "host_agent",
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "executable": sys.executable,
        "install_layout": "frozen_exe" if getattr(sys, "frozen", False) else "source_tree",
        "packaged_onefile_process_note": "pyinstaller_one_file_may_show_parent_and_child_process_rows",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_agent_public_readiness(
    health: dict[str, Any],
    *,
    armed: bool,
    arm_required: bool,
) -> dict[str, Any]:
    blockers = [str(item) for item in health.get("control_blockers") or []]
    service_status = str(health.get("service_status") or "service_status_unknown")
    code = _host_agent_public_readiness_code(
        health=health,
        blockers=blockers,
        service_status=service_status,
        armed=armed,
        arm_required=arm_required,
    )
    return {
        "schema": "agentsight_public_readiness_v1",
        "source": "embedded_host_agent_health",
        "ok": code == "READY",
        "code": code,
        "message": _host_agent_public_readiness_message(code),
        "service_status": service_status,
        "can_attempt_real_control": bool(health.get("can_attempt_real_control")) and (armed or not arm_required),
        "control_blockers": blockers,
        "real_input_armed": bool(armed),
        "arm_required": bool(arm_required),
        "health_endpoint_internal": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
    }


def _host_agent_public_readiness_code(
    *,
    health: dict[str, Any],
    blockers: list[str],
    service_status: str,
    armed: bool,
    arm_required: bool,
) -> str:
    blocker_set = set(blockers)
    if service_status == "kill_switch_active" or "kill_switch_active" in blocker_set:
        return "EMERGENCY_STOP_ACTIVE"
    if service_status == "operator_control_paused" or "operator_control_paused" in blocker_set:
        return "OPERATOR_PAUSED"
    if service_status == "uac_secure_desktop_active" or "uac_secure_desktop_active" in blocker_set:
        return "UAC_SECURE_DESKTOP_ACTIVE"
    if service_status == "secure_desktop_active" or "secure_desktop_active" in blocker_set:
        return "SECURE_DESKTOP_ACTIVE"
    if service_status == "locked_desktop" or "locked_desktop" in blocker_set or "session_locked_or_secure_desktop" in blocker_set:
        return "DESKTOP_LOCKED"
    if service_status in {"no_active_console_session", "session_disconnected"}:
        return "NOT_IN_ACTIVE_SESSION"
    if service_status == "discovery_stale" or "discovery_stale" in blocker_set:
        return "DISCOVERY_STALE"
    if service_status in {"child_not_running", "child_start_failed", "child_unhealthy"}:
        return "HOST_AGENT_NOT_READY"
    if "screen_capture_unavailable" in blocker_set:
        return "CAPTURE_UNAVAILABLE"
    if {"cursor_position_unavailable", "input_desktop_unavailable", "input_desktop_not_default"} & blocker_set:
        return "INPUT_UNAVAILABLE"
    if not bool(health.get("can_attempt_real_control")):
        return "HOST_AGENT_NOT_READY"
    if arm_required and not armed:
        return "HOST_AGENT_NOT_ARMED"
    return "READY"


def _host_agent_public_readiness_message(code: str) -> str:
    return {
        "READY": "Host Agent is ready for this public request.",
        "HOST_AGENT_NOT_ARMED": "Host Agent is reachable but not armed for real input.",
        "HOST_AGENT_NOT_READY": "Host Agent is reachable but not ready for real Windows control.",
        "DESKTOP_LOCKED": "The visible desktop appears locked; wait for the user desktop before GUI control.",
        "SECURE_DESKTOP_ACTIVE": "A secure desktop is active; AgentSight reports the blocker and does not bypass it.",
        "UAC_SECURE_DESKTOP_ACTIVE": "A UAC secure desktop is active; AgentSight reports the blocker and does not control it.",
        "OPERATOR_PAUSED": "The operator paused AI real-control requests.",
        "EMERGENCY_STOP_ACTIVE": "Emergency stop is active; real control is blocked.",
        "DISCOVERY_STALE": "The discovery/service state is stale; refresh Host Agent lifecycle before control.",
        "CAPTURE_UNAVAILABLE": "Screen capture is unavailable in the current session.",
        "INPUT_UNAVAILABLE": "Mouse/keyboard input readiness is unavailable in the current session.",
        "NOT_IN_ACTIVE_SESSION": "The process is not in an active visible user session.",
    }.get(code, "Host Agent readiness is blocked.")


def _host_agent_public_status_fields(readiness: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": readiness["code"],
        "readiness": readiness,
        "service_status": readiness["service_status"],
        "can_attempt_real_control": readiness["can_attempt_real_control"],
        "control_blockers": list(readiness["control_blockers"]),
    }


def _host_agent_apply_public_readiness(
    report: dict[str, Any],
    health: dict[str, Any],
    *,
    armed: bool,
    arm_required: bool,
) -> dict[str, Any]:
    report.update(_host_agent_public_status_fields(_host_agent_public_readiness(health, armed=armed, arm_required=arm_required)))
    return report


def _start_host_agent_idle_capture_loop(
    *,
    runs_dir: str,
    stop_event: threading.Event,
    interval_seconds: float = 0.5,
    adapter_factory: Any | None = None,
) -> threading.Thread | None:
    factory = adapter_factory or build_manual_windows_input_adapter

    def run() -> None:
        import gc

        try:
            adapter = factory(
                runs_dir=_resolve_agent_runs_dir(runs_dir),
                arming_ref="host-agent-idle-capture-arming",
                operator_consent_ref="host-agent-idle-capture-consent",
                default_observation_channel_ref=DEFAULT_OBSERVATION_CHANNEL_REF,
            )
        except Exception:
            return
        tick_count = 0
        next_tick_at = time.monotonic()
        while not stop_event.is_set():
            now = time.monotonic()
            if now < next_tick_at:
                if stop_event.wait(next_tick_at - now):
                    break
            _host_agent_idle_capture_tick(adapter=adapter)
            tick_count += 1
            if tick_count % 120 == 0:
                try:
                    gc.collect()
                except Exception:
                    pass
            interval = _host_agent_idle_capture_wait_seconds(default_interval_seconds=interval_seconds)
            next_tick_at = max(next_tick_at + interval, time.monotonic())

    thread = threading.Thread(target=run, name="AgentSightIdleCapture", daemon=True)
    thread.start()
    return thread


def _host_agent_idle_capture_wait_seconds(*, default_interval_seconds: float = 0.5) -> float:
    try:
        policy = normalize_recording_policy(read_jsonc_file(default_tray_config_file()))
        idle = policy.get("recording", {}).get("idle_capture", {}) if isinstance(policy.get("recording"), dict) else {}
        if not bool(idle.get("enabled", False)):
            return max(0.5, float(default_interval_seconds))
        fps = float(idle.get("fps") or 0.0)
        if fps <= 0:
            return max(0.5, float(default_interval_seconds))
        return max(0.03, min(0.5, 1.0 / fps))
    except Exception:
        return max(0.1, float(default_interval_seconds))


def _host_agent_idle_capture_tick(*, adapter: Any, now_ms: int | None = None) -> dict[str, Any]:
    policy = normalize_recording_policy(read_jsonc_file(default_tray_config_file()))
    health = build_host_agent_health_report()
    readiness = _host_agent_public_readiness(health, armed=True, arm_required=False)
    if not readiness.get("ok", False):
        return {
            "object_type": "AgentSightIdleCaptureTickReport",
            "schema": "agentsight_idle_capture_tick_v1",
            "captured": False,
            "skip_reason": "readiness_blocked",
            "readiness": readiness,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": _host_agent_boundary_facts(),
        }
    gateway = getattr(getattr(adapter, "session", None), "gateway", None)
    if gateway is None or not hasattr(gateway, "capture_idle_frame"):
        return {
            "object_type": "AgentSightIdleCaptureTickReport",
            "schema": "agentsight_idle_capture_tick_v1",
            "captured": False,
            "skip_reason": "adapter_gateway_missing",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": _host_agent_boundary_facts(),
        }
    try:
        report = gateway.capture_idle_frame(policy=policy, now_ms=now_ms)
    except Exception as exc:
        return {
            "object_type": "AgentSightIdleCaptureTickReport",
            "schema": "agentsight_idle_capture_tick_v1",
            "captured": False,
            "skip_reason": "idle_capture_tick_failed",
            "error_type": type(exc).__name__,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": _host_agent_boundary_facts(),
        }
    return report if isinstance(report, dict) else {
        "object_type": "AgentSightIdleCaptureTickReport",
        "schema": "agentsight_idle_capture_tick_v1",
        "captured": False,
        "skip_reason": "invalid_tick_report",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _host_agent_boundary_facts(),
    }


def _host_agent_public_blocked_response(
    *,
    operation: str,
    health: dict[str, Any],
    armed: bool,
    arm_required: bool,
    caller_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    readiness = _host_agent_public_readiness(health, armed=armed, arm_required=arm_required)
    report = {
        "object_type": "AgentSightPublicReadinessBlocked",
        "schema": "agentsight_public_readiness_blocked_v1",
        "ok": False,
        "status": "blocked",
        "operation": operation,
        "error": readiness["code"].lower(),
        "message": readiness["message"],
        **_host_agent_public_status_fields(readiness),
        "input_executed": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "input_visual_relationship_judgment": "external_review_only",
        "boundary": _host_agent_boundary_facts(),
    }
    if caller_lock is not None:
        report["caller_lock"] = caller_lock
    return report


def _host_agent_visual_failure(
    *,
    status: str,
    failure_code: str,
    detail: Any = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "object_type": "P0BVisualControlFailure",
        "schema": "agentsight_p0b_visual_control_failure_v1",
        "status": status,
        "failure_code": failure_code,
        "detail": detail,
        **extra,
        "input_executed": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_asserts_target_found": False,
        "tool_asserts_click_hit_target": False,
        "tool_asserts_business_success": False,
        "tool_asserts_task_success": False,
        "tool_asserts_text_entered": False,
        "input_visual_relationship_judgment": "external_review_only",
        "external_visual_review_required": True,
        "boundary": _host_agent_boundary_facts(),
    }


def _handler_class(*, runs_dir: str, arm_real_input: bool, token: str) -> type[BaseHTTPRequestHandler]:
    visual_sessions: dict[str, dict[str, Any]] = {}
    protocol_views: dict[str, dict[str, Any]] = {}
    visual_lock = threading.Lock()

    class HostAgentHandler(BaseHTTPRequestHandler):
        server_version = "AgentSightHostAgent/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if not self._request_allowed():
                return
            if self.path == "/health":
                try:
                    self._send_json(200, build_host_agent_health_report())
                except Exception as exc:
                    error_report = _host_agent_error_report(request_path=self.path, exc=exc)
                    _write_last_agent_error(error_report)
                    self._send_json(500, error_report)
                return
            if self.path == "/screen":
                caller_id, caller_allowed, caller_status, caller_report = self._caller_lock_preflight({})
                if not caller_allowed:
                    self._append_public_operation_log(route=self.path, request={}, response=caller_report, http_status=caller_status)
                    self._send_json(caller_status, caller_report)
                    return
                health = build_host_agent_health_report()
                readiness = _host_agent_public_readiness(health, armed=arm_real_input, arm_required=False)
                if not readiness["ok"]:
                    report = _host_agent_public_blocked_response(
                            operation="screen",
                            health=health,
                            armed=arm_real_input,
                            arm_required=False,
                    )
                    self._append_public_operation_log(route=self.path, request={}, response=report, http_status=503)
                    self._send_json(503, report)
                    return
                report = _host_agent_screen_layout(
                    health=health,
                    armed=arm_real_input,
                    caller_lock=caller_report if caller_id else None,
                )
                self._append_public_operation_log(route=self.path, request={}, response=report, http_status=200)
                self._send_json(200, report)
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._request_allowed():
                return
            if self.path == "/shutdown":
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "shutdown_requested": True,
                        "input_executed": False,
                        "host_input_sent": False,
                        "host_sent_event_count": 0,
                    },
                )
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if self.path == "/screen":
                request = self._read_json_body()
                caller_id, caller_allowed, caller_status, caller_report = self._caller_lock_preflight(request)
                if not caller_allowed:
                    self._append_public_operation_log(route=self.path, request=request, response=caller_report, http_status=caller_status)
                    self._send_json(caller_status, caller_report)
                    return
                health = build_host_agent_health_report()
                readiness = _host_agent_public_readiness(health, armed=arm_real_input, arm_required=False)
                if not readiness["ok"]:
                    report = _host_agent_public_blocked_response(
                            operation="screen",
                            health=health,
                            armed=arm_real_input,
                            arm_required=False,
                    )
                    self._append_public_operation_log(route=self.path, request=request, response=report, http_status=503)
                    self._send_json(503, report)
                    return
                report = _host_agent_screen_layout(
                    health=health,
                    armed=arm_real_input,
                    caller_lock=caller_report if caller_id else None,
                )
                self._append_public_operation_log(route=self.path, request=request, response=report, http_status=200)
                self._send_json(200, report)
                return
            if self.path in {"/observe", "/click", "/mouse", "/input", "/look", "/do"}:
                request = self._read_json_body()
                caller_allowed, caller_status, caller_report = self._caller_lock_gate(request)
                if not caller_allowed:
                    self._append_public_operation_log(
                        route=self.path,
                        request=request,
                        response=caller_report,
                        http_status=caller_status,
                    )
                    self._send_json(caller_status, caller_report)
                    return
                health = build_host_agent_health_report()
                if not health.get("can_attempt_real_control"):
                    report = _host_agent_public_blocked_response(
                        operation=self.path.lstrip("/"),
                        health=health,
                        armed=arm_real_input,
                        arm_required=True,
                        caller_lock=caller_report,
                    )
                    self._append_public_operation_log(route=self.path, request=request, response=report, http_status=503)
                    self._send_json(503, report)
                    return
                if not arm_real_input:
                    report = _host_agent_public_blocked_response(
                        operation=self.path.lstrip("/"),
                        health=health,
                        armed=arm_real_input,
                        arm_required=True,
                        caller_lock=caller_report,
                    )
                    self._append_public_operation_log(route=self.path, request=request, response=report, http_status=403)
                    self._send_json(403, report)
                    return
                try:
                    def run_public_protocol_request() -> tuple[int, dict[str, Any]]:
                        if self.path == "/look":
                            return _host_agent_protocol_look(
                                visual_sessions=visual_sessions,
                                protocol_views=protocol_views,
                                runs_dir=runs_dir,
                                request=request,
                            )
                        if self.path == "/do":
                            original_request = request
                            effective_request = _host_agent_apply_recording_policy_defaults(request)
                            tray_policy_post_observe_applied = (
                                isinstance(original_request, dict)
                                and original_request.get("post_observe") is None
                                and isinstance(effective_request, dict)
                                and effective_request.get("post_observe") is not None
                            )
                            status, report = _host_agent_protocol_do(
                                visual_sessions=visual_sessions,
                                protocol_views=protocol_views,
                                request=effective_request,
                            )
                            if tray_policy_post_observe_applied and isinstance(report, dict):
                                report["recording_policy"] = {
                                    "source": str(default_tray_config_file()),
                                    "applied_default_post_observe": True,
                                    "reason": "tray_action_capture_policy_post_observe",
                                    "policy_basis": "recording.action_capture.post_action_fps/post_action_duration_ms",
                                    "host_input_sent": False,
                                    "host_sent_event_count": 0,
                                    "boundary": _host_agent_boundary_facts(),
                                }
                            return status, report
                        if self.path == "/observe":
                            return _host_agent_visual_observe(
                                visual_sessions=visual_sessions,
                                runs_dir=runs_dir,
                                request=request,
                            )
                        if self.path == "/click":
                            return _host_agent_visual_click(
                                visual_sessions=visual_sessions,
                                request=request,
                            )
                        if self.path == "/mouse":
                            return _host_agent_visual_mouse(
                                visual_sessions=visual_sessions,
                                request=request,
                            )
                        return _host_agent_visual_input(
                            visual_sessions=visual_sessions,
                            request=request,
                        )

                    if self.path == "/look" and not _host_agent_protocol_look_requires_visual_lock(request):
                        status, report = run_public_protocol_request()
                    else:
                        with visual_lock:
                            status, report = run_public_protocol_request()
                    if isinstance(report, dict):
                        _host_agent_apply_public_readiness(report, health, armed=arm_real_input, arm_required=True)
                        report["caller_lock"] = caller_report
                        self._append_public_operation_log(
                            route=self.path,
                            request=request,
                            response=report,
                            http_status=status,
                        )
                except Exception as exc:
                    error_report = {
                        "object_type": "AgentSightHostAgentErrorReport",
                        "schema": "agentsight_host_agent_error_v1",
                        "request_path": self.path,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "traceback_tail": traceback.format_exc()[-4000:],
                        "input_executed": False,
                        "host_input_sent": False,
                        "host_sent_event_count": 0,
                    }
                    _write_last_agent_error(error_report)
                    self._append_public_operation_log(
                        route=self.path,
                        request=request,
                        response=error_report,
                        http_status=500,
                    )
                    self._send_json(500, error_report)
                    return
                self._send_json(status, report)
                return
            if self.path != "/p0":
                self._send_json(404, {"ok": False, "error": "not_found"})
                return
            request = self._read_json_body()
            caller_allowed, caller_status, caller_report = self._caller_lock_gate(request)
            if not caller_allowed:
                self._append_public_operation_log(
                    route=self.path,
                    request=request,
                    response=caller_report,
                    http_status=caller_status,
                )
                self._send_json(caller_status, caller_report)
                return
            health = build_host_agent_health_report()
            if not health.get("can_attempt_real_control"):
                report = {
                    "ok": False,
                    "error": "host_agent_not_ready_for_real_control",
                    "health": health,
                    "caller_lock": caller_report,
                    "input_executed": False,
                    "host_input_sent": False,
                    "host_sent_event_count": 0,
                }
                self._append_public_operation_log(route=self.path, request=request, response=report, http_status=409)
                self._send_json(409, report)
                return
            if not arm_real_input:
                report = {
                    "ok": False,
                    "error": "host_agent_not_armed_for_real_input",
                    "health": health,
                    "caller_lock": caller_report,
                    "input_executed": False,
                    "host_input_sent": False,
                    "host_sent_event_count": 0,
                }
                self._append_public_operation_log(route=self.path, request=request, response=report, http_status=403)
                self._send_json(403, report)
                return
            env = dict(os.environ)
            env[P0_ARMING_FLAG] = "armed"
            env[ARMING_REF] = request.get("arming_ref") or "host-agent-p0-arming"
            env[CONSENT_REF] = request.get("operator_consent_ref") or "host-agent-p0-consent"
            try:
                report = run_p0_real_input_closed_loop_smoke(
                    runs_dir=_resolve_agent_runs_dir(str(request.get("runs_dir") or runs_dir)),
                    env=env,
                    include_notepad=not bool(request.get("calculator_only")),
                    include_calculator=not bool(request.get("notepad_only")),
                )
            except Exception as exc:
                error_report = {
                    "object_type": "AgentSightHostAgentErrorReport",
                    "schema": "agentsight_host_agent_error_v1",
                    "request_path": self.path,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback_tail": traceback.format_exc()[-4000:],
                    "input_executed": False,
                    "host_input_sent": False,
                    "host_sent_event_count": 0,
                }
                _write_last_agent_error(error_report)
                self._append_public_operation_log(
                    route=self.path,
                    request=request,
                    response=error_report,
                    http_status=500,
                )
                self._send_json(500, error_report)
                return
            report["caller_lock"] = caller_report
            self._append_public_operation_log(route=self.path, request=request, response=report, http_status=200)
            self._send_json(200, report)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _request_allowed(self) -> bool:
            if not _host_header_allowed(self.headers.get("host")):
                self._send_json(403, {"ok": False, "error": "host_header_not_allowed"})
                return False
            if not _origin_allowed(self.headers.get("origin")):
                self._send_json(403, {"ok": False, "error": "origin_not_allowed"})
                return False
            if not _authorized(self.headers.get("authorization"), token=token):
                self._send_json(401, {"ok": False, "error": "authorization_required"})
                return False
            return True

        def _caller_lock_gate(self, request: dict[str, Any]) -> tuple[bool, int, dict[str, Any]]:
            return enforce_single_caller_lock(
                caller_id_from_request(request, header_value=self.headers.get("x-agentsight-caller")),
                request_path=self.path,
            )

        def _caller_lock_preflight(self, request: dict[str, Any]) -> tuple[str | None, bool, int, dict[str, Any]]:
            caller_id = caller_id_from_request(request, header_value=self.headers.get("x-agentsight-caller"))
            allowed, status, report = check_single_caller_lock(caller_id, request_path=self.path)
            return caller_id, allowed, status, report

        def _append_public_operation_log(
            self,
            *,
            route: str,
            request: dict[str, Any] | None,
            response: dict[str, Any] | None,
            http_status: int,
        ) -> None:
            try:
                append_operation_log(
                    public_operation_log_entry(
                        route=route,
                        request=request,
                        response=response,
                        http_status=http_status,
                        caller_hint=self.headers.get("x-agentsight-caller"),
                    )
                )
            except Exception:
                return

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("content-length", "0") or "0")
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                return {}
            return data if isinstance(data, dict) else {}

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json; charset=utf-8")
            self.send_header("content-length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return HostAgentHandler


def _host_header_allowed(value: str | None) -> bool:
    if value is None:
        return True
    host = value.rsplit("@", 1)[-1].split(":", 1)[0].strip("[]").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _resolve_agent_runs_dir(runs_dir: str) -> str:
    path = Path(runs_dir)
    if path.is_absolute():
        return str(path)
    return str(default_agent_report_file().parent / path)


def _origin_allowed(value: str | None) -> bool:
    if not value:
        return True
    lowered = value.lower()
    return lowered.startswith("http://127.0.0.1:") or lowered.startswith("http://localhost:")


def _authorized(value: str | None, *, token: str) -> bool:
    if not value:
        return False
    prefix = "Bearer "
    return value.startswith(prefix) and secrets.compare_digest(value[len(prefix) :], token)


def _active_interactive_session(session: dict[str, Any]) -> bool:
    return bool(
        session.get("process_is_active_visible_session")
        or session.get("process_is_active_console_session")
        or session.get("process_session_is_wts_active")
    )


def _control_blockers(
    *,
    active_interactive_session: bool,
    visible_station: bool,
    input_desktop: dict[str, Any],
    cursor_probe: dict[str, Any] | None = None,
    capture_probe: dict[str, Any] | None = None,
    kill_switch_active: bool = False,
    operator_allows_real_control: bool = True,
) -> list[str]:
    blockers: list[str] = []
    if kill_switch_active:
        blockers.append("kill_switch_active")
    if not operator_allows_real_control:
        blockers.append("operator_control_paused")
    if not active_interactive_session:
        blockers.append("process_not_in_active_interactive_session")
    if not visible_station:
        blockers.append("process_not_in_visible_window_station")
    if not input_desktop.get("opened"):
        blockers.append("input_desktop_unavailable")
    elif input_desktop.get("desktop_name") != "Default":
        if input_desktop.get("desktop_name") == "Winlogon":
            blockers.append("session_locked_or_secure_desktop")
        else:
            blockers.append("input_desktop_not_default")
    if cursor_probe is not None and not cursor_probe.get("ok"):
        blockers.append("cursor_position_unavailable")
    if capture_probe is not None and not capture_probe.get("ok"):
        blockers.append("screen_capture_unavailable")
    return blockers


def _merge_control_blockers(raw_blockers: list[str], service_blockers: list[str]) -> list[str]:
    merged: list[str] = []
    for blocker in [*raw_blockers, *service_blockers]:
        if blocker not in merged:
            merged.append(blocker)
    return merged


def _cursor_probe() -> dict[str, Any]:
    if os.name != "nt":
        return {"platform_supported": False, "ok": False, "error": "non_windows_platform"}
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        class Point(ctypes.Structure):
            _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

        point = Point()
        ctypes.set_last_error(0)
        ok = bool(user32.GetCursorPos(ctypes.byref(point)))
        last_error = ctypes.get_last_error()
        return {
            "platform_supported": True,
            "ok": ok,
            "x": int(point.x) if ok else None,
            "y": int(point.y) if ok else None,
            "last_error": last_error,
            "error_text": ctypes.FormatError(last_error).strip() if last_error else None,
            "probe_kind": "read_current_cursor_position_only",
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    except Exception as exc:
        return {
            "platform_supported": True,
            "ok": False,
            "error": str(exc),
            "probe_kind": "read_current_cursor_position_only",
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }


def _capture_probe() -> dict[str, Any]:
    if os.name != "nt":
        return {"platform_supported": False, "ok": False, "error": "non_windows_platform"}
    channel = WindowsSoftwareObservationChannel()
    available, reason = channel._probe_available()
    return {
        "platform_supported": True,
        "ok": bool(available),
        "channel_ref": channel.name,
        "probe_kind": "one_pixel_gdi_screen_capture_only",
        "source_kind": "software_screen_capture",
        "unavailable_reason": None if available else reason,
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
