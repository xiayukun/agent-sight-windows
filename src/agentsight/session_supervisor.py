from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from subprocess import DEVNULL, Popen
from typing import Any

from agentsight.host_agent.interactive_task import completed_process_report, run_schtasks
from agentsight.host_agent.server import (
    _redacted_process_command,
    _watchdog_probe,
    _watchdog_should_start_agent,
    default_discovery_file,
    default_service_state_file,
    default_watchdog_stop_file as default_host_watchdog_stop_file,
)
from agentsight.host_agent.service_state import (
    HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION,
    project_supervisor_child_health_to_service_state,
)
from agentsight.runtime_platform import is_windows
from agentsight.operator_notifications import enqueue_notification
from agentsight.tray.actions import request_host_agent_shutdown
from agentsight.tray.gui import TRAY_WINDOW_CLASS_NAME, _request_tray_window_close, _tray_window_present
from agentsight.tray.state import (
    boundary_facts,
    default_agent_dir,
    default_emergency_stop_file,
    load_operator_control_policy,
    read_json_file,
)


SESSION_SUPERVISOR_SCHEMA = "agentsight_session_supervisor_v1"
SESSION_SUPERVISOR_ONLOGON_TASK_NAME = "AgentSightSupervisorOnLogon"
SESSION_SUPERVISOR_RUN_KEY_NAME = "AgentSight"
SESSION_SUPERVISOR_RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
RESTART_STORM_WINDOW_MS = 5 * 60 * 1000
RESTART_STORM_LIMIT = 5


def _is_windows() -> bool:
    return is_windows()


def default_session_supervisor_state_file() -> Path:
    return default_agent_dir() / "session-supervisor-state.json"


def default_session_supervisor_report_file() -> Path:
    return default_agent_dir() / "last-session-supervisor-report.json"


def default_session_supervisor_install_report_file() -> Path:
    return default_agent_dir() / "last-session-supervisor-install-report.json"


def default_session_supervisor_install_progress_file() -> Path:
    return default_agent_dir() / "last-session-supervisor-install-progress.json"


def default_session_supervisor_uninstall_report_file() -> Path:
    return default_agent_dir() / "last-session-supervisor-uninstall-report.json"


def default_session_supervisor_stop_file() -> Path:
    return default_agent_dir() / "session-supervisor.stop"


def default_session_supervisor_lock_file() -> Path:
    return default_agent_dir() / "session-supervisor.lock"


def default_session_supervisor_command_file() -> Path:
    return default_agent_dir() / "AgentSightSupervisor.cmd"


def default_unified_supervisor_enabled_file() -> Path:
    return default_agent_dir() / "unified-session-supervisor.enabled"


def default_session_supervisor_vbs_file() -> Path:
    startup = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return startup / "AgentSightSupervisor.vbs"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run or install the AgentSight current-user session supervisor.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", description="Run the current-user Host Agent + Tray GUI supervisor.")
    _add_runtime_args(run)
    run.add_argument("--interval-seconds", type=float, default=5.0)
    run.add_argument("--once", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--output", default=None)

    install = subcommands.add_parser("install", description="Install current-user startup for the session supervisor.")
    _add_runtime_args(install)
    install.add_argument("--start-now", action="store_true")
    install.add_argument("--start-method", choices=["auto", "run_key", "onlogon_task", "startup_vbs"], default="auto")
    install.add_argument("--wait-seconds", type=float, default=10.0)
    install.add_argument("--output", default=None)

    start = subcommands.add_parser("start", description="Start the installed session supervisor once.")
    start.add_argument("--start-method", choices=["auto", "run_key", "onlogon_task", "startup_vbs"], default="auto")
    start.add_argument("--wait-seconds", type=float, default=10.0)
    start.add_argument("--output", default=None)

    status = subcommands.add_parser("status", description="Read session supervisor install/runtime status.")
    status.add_argument("--output", default=None)

    stop = subcommands.add_parser("stop", description="Stop Host Agent, Tray GUI, and the supervisor.")
    stop.add_argument("--reason", default="operator_requested_stop_agentsight")
    stop.add_argument("--wait-seconds", type=float, default=6.0)
    stop.add_argument("--no-force-after-timeout", action="store_true")
    stop.add_argument("--output", default=None)

    uninstall = subcommands.add_parser("uninstall", description="Remove session supervisor startup and stop running pieces.")
    uninstall.add_argument("--keep-running", action="store_true")
    uninstall.add_argument("--output", default=None)

    args = parser.parse_args(argv)
    command = args.command
    if command == "run":
        return run_session_supervisor(
            host=args.host,
            port=int(args.port),
            runs_dir=args.runs_dir,
            repo_root=Path(args.repo_root),
            python_command=args.python,
            agent_exe=Path(args.agent_exe) if args.agent_exe else None,
            tray_gui_exe=Path(args.tray_gui_exe) if args.tray_gui_exe else None,
            arm_real_input=bool(args.arm_real_input),
            interval_seconds=float(args.interval_seconds),
            once=bool(args.once),
            output=args.output,
        )
    if command == "install":
        report = install_session_supervisor(
            host=args.host,
            port=int(args.port),
            runs_dir=args.runs_dir,
            repo_root=Path(args.repo_root),
            python_command=args.python,
            agent_exe=Path(args.agent_exe) if args.agent_exe else None,
            tray_gui_exe=Path(args.tray_gui_exe) if args.tray_gui_exe else None,
            arm_real_input=bool(args.arm_real_input),
            start_now=bool(args.start_now),
            start_method=args.start_method,
            wait_seconds=float(args.wait_seconds),
        )
        _write_json_report(report, args.output)
        return int(report.get("exit_code", 0))
    if command == "start":
        report = start_installed_session_supervisor(start_method=args.start_method, wait_seconds=float(args.wait_seconds))
        _write_json_report(report, args.output)
        return int(report.get("exit_code", 0))
    if command == "status":
        report = session_supervisor_status()
        _write_json_report(report, args.output)
        return int(report.get("exit_code", 0))
    if command == "stop":
        report = stop_session_supervisor(
            reason=args.reason,
            wait_seconds=float(args.wait_seconds),
            force_after_timeout=not bool(args.no_force_after_timeout),
        )
        _write_json_report(report, args.output)
        return int(report.get("exit_code", 0))
    report = uninstall_session_supervisor(stop_running=not bool(args.keep_running))
    _write_json_report(report, args.output)
    return int(report.get("exit_code", 0))


def _add_runtime_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", default="runs_host_agent")
    parser.add_argument("--repo-root", default=str(_default_runtime_root()))
    parser.add_argument("--python", default=_default_python_command())
    parser.add_argument("--agent-exe", default=_default_host_agent_exe_arg())
    parser.add_argument("--tray-gui-exe", default=_default_tray_gui_exe_arg())
    parser.add_argument("--arm-real-input", action="store_true")


def run_session_supervisor(
    *,
    host: str,
    port: int,
    runs_dir: str,
    repo_root: Path,
    python_command: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    arm_real_input: bool,
    interval_seconds: float,
    once: bool = False,
    output: str | None = None,
) -> int:
    cycle = 0
    started_at_ms = _now_ms()
    last_host_child: Popen[Any] | None = None
    last_tray_child: Popen[Any] | None = None
    restart_history: dict[str, list[int]] = {"host_agent": [], "tray_gui": []}
    restart_storm_notifications: set[str] = set()
    lock = _acquire_session_supervisor_lock()
    if not lock.get("acquired"):
        report = _build_supervisor_already_running_report(
            started_at_ms=started_at_ms,
            lock=lock,
        )
        _write_json_path(default_session_supervisor_report_file(), report)
        _write_json_report(report, output)
        return 0
    try:
        while True:
            cycle += 1
            if default_session_supervisor_stop_file().exists():
                report = _build_supervisor_state(
                    supervisor_status="stopped_by_stop_marker",
                    cycle=cycle,
                    started_at_ms=started_at_ms,
                    host_probe={"watchdog_probe_status": "supervisor_stop_requested", "restart_recommended": False},
                    host_action={"action": "none", "reason": "supervisor_stop_requested"},
                    host_child_pid=last_host_child.pid if last_host_child else None,
                    tray_window_present=_tray_window_present(),
                    tray_action={"action": "none", "reason": "supervisor_stop_requested"},
                    tray_child_pid=last_tray_child.pid if last_tray_child else None,
                    single_instance_lock=lock,
                )
                _write_supervisor_state(report)
                _write_json_report(report, output)
                return 0

            emergency_active = default_emergency_stop_file().exists()
            host_probe = _host_agent_probe(emergency_active=emergency_active)
            host_action: dict[str, Any] = {"action": "none"}
            active_session_restart_needed = _host_probe_reports_non_active_host_session(host_probe)
            if active_session_restart_needed:
                supervisor_session = _current_process_session_report()
                if not _session_report_is_active_visible(supervisor_session):
                    host_action = _host_agent_restart_blocked_by_supervisor_session(
                        host_probe=host_probe,
                        supervisor_session=supervisor_session,
                    )
                else:
                    pre_restart = _prepare_host_agent_active_session_restart(host_probe)
                    if pre_restart.get("restart_prepare_status") != "stopped":
                        host_action = _host_agent_restart_blocked_by_existing_process(pre_restart=pre_restart)
                    else:
                        storm = _restart_storm_status("host_agent", restart_history=restart_history, now_ms=_now_ms())
                        if storm["restart_storm_active"]:
                            host_action = _restart_storm_action("host_agent", storm)
                            host_action["pre_restart"] = pre_restart
                            _notify_restart_storm_once("host_agent", storm, notified=restart_storm_notifications)
                        else:
                            command = _host_agent_child_command(
                                host=host,
                                port=port,
                                runs_dir=runs_dir,
                                repo_root=repo_root,
                                python_command=python_command,
                                agent_exe=agent_exe if agent_exe and agent_exe.exists() else None,
                                arm_real_input=arm_real_input,
                            )
                            host_action, last_host_child = _start_child_process(command, cwd=repo_root, action_name="start_host_agent")
                            host_action["pre_restart"] = pre_restart
                            _record_restart_attempt("host_agent", restart_history=restart_history, now_ms=_now_ms())
            elif _watchdog_should_start_agent(host_probe):
                restart_suppression = _host_agent_restart_suppression(host_probe)
                if restart_suppression is not None:
                    host_action = restart_suppression
                else:
                    storm = _restart_storm_status("host_agent", restart_history=restart_history, now_ms=_now_ms())
                    if storm["restart_storm_active"]:
                        host_action = _restart_storm_action("host_agent", storm)
                        _notify_restart_storm_once("host_agent", storm, notified=restart_storm_notifications)
                    else:
                        command = _host_agent_child_command(
                            host=host,
                            port=port,
                            runs_dir=runs_dir,
                            repo_root=repo_root,
                            python_command=python_command,
                            agent_exe=agent_exe if agent_exe and agent_exe.exists() else None,
                            arm_real_input=arm_real_input,
                        )
                        host_action, last_host_child = _start_child_process(command, cwd=repo_root, action_name="start_host_agent")
                        _record_restart_attempt("host_agent", restart_history=restart_history, now_ms=_now_ms())

            tray_window_present = _tray_window_present()
            tray_action = {"action": "none"}
            if not tray_window_present:
                storm = _restart_storm_status("tray_gui", restart_history=restart_history, now_ms=_now_ms())
                if storm["restart_storm_active"]:
                    tray_action = _restart_storm_action("tray_gui", storm)
                    _notify_restart_storm_once("tray_gui", storm, notified=restart_storm_notifications)
                else:
                    command = _tray_gui_child_command(
                        repo_root=repo_root,
                        python_command=python_command,
                        tray_gui_exe=tray_gui_exe if tray_gui_exe and tray_gui_exe.exists() else None,
                    )
                    tray_action, last_tray_child = _start_child_process(command, cwd=repo_root, action_name="start_tray_gui")
                    _record_restart_attempt("tray_gui", restart_history=restart_history, now_ms=_now_ms())
                    time.sleep(0.5)
                    tray_window_present = _tray_window_present()

            status = "emergency_stopped" if emergency_active else "running"
            report = _build_supervisor_state(
                supervisor_status=status,
                cycle=cycle,
                started_at_ms=started_at_ms,
                host_probe=host_probe,
                host_action=host_action,
                host_child_pid=last_host_child.pid if last_host_child else None,
                tray_window_present=tray_window_present,
                tray_action=tray_action,
                tray_child_pid=last_tray_child.pid if last_tray_child else None,
                single_instance_lock=lock,
            )
            _write_supervisor_state(report)
            if once:
                _write_json_report(report, output)
                return 0
            _sleep_with_stop_poll(interval_seconds)
    finally:
        _release_session_supervisor_lock(lock, reason="run_session_supervisor_exit")


def _host_agent_probe(*, emergency_active: bool) -> dict[str, Any]:
    if emergency_active:
        return {
            "watchdog_probe_status": "emergency_stop_active",
            "agent_running": False,
            "restart_recommended": False,
            "control_blockers": ["kill_switch_active"],
        }
    return _watchdog_probe(
        default_discovery_file(),
        suppress_when_unified_supervisor_enabled=False,
        respect_watchdog_stop_file=False,
    )


def _host_agent_restart_suppression(host_probe: dict[str, Any]) -> dict[str, Any] | None:
    if host_probe.get("watchdog_probe_status") != "health_unreachable":
        return None
    if host_probe.get("agent_running") is not True:
        return None
    discovery = host_probe.get("discovery") if isinstance(host_probe.get("discovery"), dict) else {}
    existing_pid = _safe_int(discovery.get("pid"))
    if existing_pid is None:
        return None
    return {
        "action": "host_agent_restart_suppressed",
        "reason": "existing_host_agent_unreachable_pid_still_running",
        "existing_pid": existing_pid,
        "health_probe_status": host_probe.get("watchdog_probe_status"),
        "health_request_failed": True,
        "operator_action_hint": "Use AgentSightSupervisor stop/start or restart AgentSight after inspecting last-agent-error.json; no new HostAgent was spawned over the live pid.",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _host_probe_reports_non_active_host_session(host_probe: dict[str, Any]) -> bool:
    if host_probe.get("watchdog_probe_status") != "agent_responding":
        return False
    health = host_probe.get("health") if isinstance(host_probe.get("health"), dict) else {}
    if health.get("service_status") == HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION:
        return True
    blockers = set(str(item) for item in health.get("control_blockers") or [])
    if HOST_AGENT_NOT_ACTIVE_CONSOLE_SESSION in blockers:
        return True
    signals = _health_classification_signals(health)
    if "process_session_not_active_console_session" in signals:
        if {
            "process_session_is_active_visible_session",
            "process_session_is_wts_active",
        } & signals:
            return False
        blockers.update(str(item) for item in health.get("raw_control_blockers") or [])
        return bool({"cursor_position_unavailable", "screen_capture_unavailable"} & blockers)
    session = health.get("session") if isinstance(health.get("session"), dict) else {}
    if _session_report_is_active_visible(session):
        return False
    process_session_id = _safe_int(session.get("process_session_id"))
    active_console_session_id = _safe_int(session.get("active_console_session_id"))
    if process_session_id is None or active_console_session_id is None:
        return False
    if process_session_id == active_console_session_id:
        return False
    return health.get("service_status") == "child_unhealthy" and health.get("can_attempt_real_control") is False


def _health_classification_signals(health: dict[str, Any]) -> set[str]:
    service_state = health.get("service_state") if isinstance(health.get("service_state"), dict) else {}
    last_error = service_state.get("last_error") if isinstance(service_state.get("last_error"), dict) else {}
    classification = last_error.get("classification") if isinstance(last_error.get("classification"), dict) else {}
    return set(str(item) for item in classification.get("signals") or [])


def _host_agent_restart_blocked_by_supervisor_session(
    *,
    host_probe: dict[str, Any],
    supervisor_session: dict[str, Any],
) -> dict[str, Any]:
    health = host_probe.get("health") if isinstance(host_probe.get("health"), dict) else {}
    return {
        "action": "host_agent_restart_blocked_supervisor_not_active_console_session",
        "reason": "supervisor_process_not_in_active_console_session_restart_would_recreate_same_blocker",
        "restart_recommended": False,
        "required_operator_action": "Start AgentSightSupervisor from the current active visible Windows session, or log off/on so the current-user startup entry runs in that session.",
        "supervisor_session": supervisor_session,
        "host_session": health.get("session"),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _prepare_host_agent_active_session_restart(host_probe: dict[str, Any]) -> dict[str, Any]:
    discovery = _host_restart_discovery(host_probe)
    if not isinstance(discovery, dict):
        return {
            "restart_prepare_status": "blocked_discovery_unavailable",
            "reason": "host_agent_not_active_console_session_without_discovery",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    shutdown = request_host_agent_shutdown(discovery)
    stopped = _ensure_host_agent_stopped(
        discovery=discovery,
        shutdown=shutdown,
        wait_seconds=3.0,
        force_after_timeout=False,
    )
    restart_prepare_status = "stopped" if stopped.get("running_after_stop_attempt") is False or stopped.get("status") == "stopped" else "blocked_existing_process_still_running"
    return {
        "restart_prepare_status": restart_prepare_status,
        "reason": "host_agent_not_active_console_session",
        "shutdown": shutdown,
        "stop": stopped,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _host_restart_discovery(host_probe: dict[str, Any]) -> dict[str, Any] | None:
    probe_discovery = host_probe.get("discovery") if isinstance(host_probe.get("discovery"), dict) else None
    file_discovery = read_json_file(default_discovery_file())
    if isinstance(file_discovery, dict):
        probe_pid = _safe_int(probe_discovery.get("pid")) if isinstance(probe_discovery, dict) else None
        file_pid = _safe_int(file_discovery.get("pid"))
        if probe_pid is None or probe_pid == file_pid:
            return file_discovery
    return probe_discovery


def _host_agent_restart_blocked_by_existing_process(*, pre_restart: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "host_agent_restart_blocked_existing_process_still_running",
        "reason": "existing_host_agent_did_not_exit_after_authenticated_shutdown_request",
        "pre_restart": pre_restart,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _build_supervisor_state(
    *,
    supervisor_status: str,
    cycle: int,
    started_at_ms: int,
    host_probe: dict[str, Any],
    host_action: dict[str, Any],
    host_child_pid: int | None,
    tray_window_present: bool,
    tray_action: dict[str, Any],
    tray_child_pid: int | None,
    single_instance_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operator_policy = load_operator_control_policy()
    host_probe_status = str(host_probe.get("watchdog_probe_status") or "unknown")
    service_state = _session_supervisor_service_state_from_probe(
        host_probe=host_probe,
        host_action=host_action,
        cycle=cycle,
        host_child_pid=host_child_pid,
    )
    service_state_write = _write_service_state(service_state)
    return {
        "object_type": "AgentSightSupervisorState",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "supervisor_status": supervisor_status,
        "supervisor_pid": os.getpid(),
        "process_identity": _current_process_identity(role="session_supervisor"),
        "supervisor_cycle": cycle,
        "started_at_ms": started_at_ms,
        "updated_at_ms": _now_ms(),
        "state_file": str(default_session_supervisor_state_file()),
        "stop_file": str(default_session_supervisor_stop_file()),
        "single_instance": _single_instance_status(lock=single_instance_lock),
        "host_agent": {
            "component_status": _host_component_status(host_probe_status=host_probe_status, host_action=host_action),
            "probe": host_probe,
            "last_action": host_action,
            "last_child_pid": host_child_pid,
            "discovery_file": str(default_discovery_file()),
            "host_watchdog_stop_file": str(default_host_watchdog_stop_file()),
        },
        "tray_gui": {
            "component_status": "visible" if tray_window_present else "not_visible",
            "tray_window_class": TRAY_WINDOW_CLASS_NAME,
            "tray_window_present": tray_window_present,
            "last_action": tray_action,
            "last_child_pid": tray_child_pid,
            "backend_status": _tray_backend_status(host_probe_status=host_probe_status, supervisor_status=supervisor_status),
        },
        "control_plane": {
            "emergency_stop_active": default_emergency_stop_file().exists(),
            "emergency_stop_file": str(default_emergency_stop_file()),
            "operator_control_policy": operator_policy,
        },
        "service_state": service_state,
        "service_state_file": str(default_service_state_file()),
        "service_state_write": service_state_write,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _session_supervisor_service_state_from_probe(
    *,
    host_probe: dict[str, Any],
    host_action: dict[str, Any],
    cycle: int,
    host_child_pid: int | None,
) -> dict[str, Any]:
    probe_status = str(host_probe.get("watchdog_probe_status") or "unknown")
    discovery = host_probe.get("discovery") if isinstance(host_probe.get("discovery"), dict) else None
    health = _service_state_child_health_summary(host_probe.get("health"))
    kill_switch_active = probe_status == "emergency_stop_active"

    if probe_status in {"discovery_missing", "stale_discovery_pid_not_running"}:
        child_pid_running: bool | None = False
        child_health_request_ok: bool | None = None
    elif probe_status == "health_unreachable":
        child_pid_running = True
        child_health_request_ok = False
    elif probe_status == "agent_responding":
        child_pid_running = True
        child_health_request_ok = True
    else:
        child_pid_running = None
        child_health_request_ok = None

    child_start_error = None
    if host_action.get("action") == "start_host_agent_failed":
        child_start_error = {
            "action": host_action.get("action"),
            "error": host_action.get("error"),
            "command": host_action.get("command"),
        }

    state = project_supervisor_child_health_to_service_state(
        discovery=discovery,
        discovery_file_exists=default_discovery_file().exists() or isinstance(discovery, dict),
        child_pid_running=child_pid_running,
        child_health=health,
        child_health_request_ok=child_health_request_ok,
        child_start_error=child_start_error,
        kill_switch_active=kill_switch_active,
    )
    state["state_artifact_role"] = "unified_session_supervisor_projection"
    state["supervisor_runtime"] = {
        "supervisor_pid": os.getpid(),
        "supervisor_cycle": cycle,
        "watchdog_probe_status": probe_status,
        "last_action": host_action,
        "last_child_pid": host_child_pid,
        "discovery_file": str(default_discovery_file()),
        "service_state_file": str(default_service_state_file()),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    return state


def _service_state_child_health_summary(health: Any) -> dict[str, Any] | None:
    if not isinstance(health, dict):
        return None
    summary = {
        "service_status": health.get("service_status"),
        "can_attempt_real_control": health.get("can_attempt_real_control"),
        "control_blockers": list(health.get("control_blockers") or []),
    }
    if "raw_can_attempt_real_control" in health:
        summary["raw_can_attempt_real_control"] = health.get("raw_can_attempt_real_control")
    if "raw_control_blockers" in health:
        summary["raw_control_blockers"] = list(health.get("raw_control_blockers") or [])
    return summary


def _write_service_state(state: dict[str, Any]) -> dict[str, Any]:
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


def _build_supervisor_already_running_report(*, started_at_ms: int, lock: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_type": "AgentSightSupervisorAlreadyRunningReport",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "supervisor_status": "already_running",
        "supervisor_pid": os.getpid(),
        "process_identity": _current_process_identity(role="duplicate_session_supervisor_attempt"),
        "started_at_ms": started_at_ms,
        "updated_at_ms": _now_ms(),
        "state_file": str(default_session_supervisor_state_file()),
        "stop_file": str(default_session_supervisor_stop_file()),
        "single_instance": _single_instance_status(lock=lock),
        "new_supervisor_started": False,
        "host_agent_action": {"action": "none", "reason": "active_supervisor_lock"},
        "tray_gui_action": {"action": "none", "reason": "active_supervisor_lock"},
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }


def _single_instance_status(*, lock: dict[str, Any] | None = None) -> dict[str, Any]:
    if isinstance(lock, dict) and lock.get("lock_file"):
        status = dict(lock)
    else:
        status = _session_supervisor_lock_status()
    status.setdefault("guard_enabled", True)
    status.setdefault("lock_file", str(default_session_supervisor_lock_file()))
    status.setdefault("current_pid", os.getpid())
    return status


def _acquire_session_supervisor_lock() -> dict[str, Any]:
    status = _session_supervisor_lock_status()
    if status.get("lock_status") == "active" and int(status.get("owner_pid") or 0) != os.getpid():
        return {
            **status,
            "acquired": False,
            "acquire_status": "already_running",
            "active_supervisor_pid": status.get("owner_pid"),
        }
    lock_file = default_session_supervisor_lock_file()
    previous_status = status.get("lock_status")
    payload = {
        "object_type": "AgentSightSupervisorLock",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "lock_status": "active",
        "owner_pid": os.getpid(),
        "owner_parent_pid": os.getppid(),
        "owner_executable": sys.executable,
        "owner_process_identity": _current_process_identity(role="session_supervisor_lock_owner"),
        "acquired_at_ms": _now_ms(),
        "lock_file": str(lock_file),
        "previous_lock_status": previous_status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    _write_json_path(lock_file, payload)
    return {
        **payload,
        "acquired": True,
        "acquire_status": "acquired",
        "guard_enabled": True,
        "current_pid": os.getpid(),
        "owner_running": True,
        "active": True,
        "stale": False,
        "owned_by_current_process": True,
    }


def _release_session_supervisor_lock(lock: dict[str, Any], *, reason: str) -> None:
    if not isinstance(lock, dict) or not lock.get("acquired"):
        return
    lock_file = default_session_supervisor_lock_file()
    current = read_json_file(lock_file)
    if not isinstance(current, dict):
        return
    if int(current.get("owner_pid") or 0) != os.getpid():
        return
    released = dict(current)
    released.update(
        {
            "lock_status": "released",
            "released_at_ms": _now_ms(),
            "release_reason": reason,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    )
    _write_json_path(lock_file, released)


def _session_supervisor_lock_status() -> dict[str, Any]:
    lock_file = default_session_supervisor_lock_file()
    payload = read_json_file(lock_file)
    if not isinstance(payload, dict):
        return {
            "guard_enabled": True,
            "lock_status": "missing",
            "lock_file": str(lock_file),
            "current_pid": os.getpid(),
            "active": False,
            "stale": False,
        }
    owner_pid = _safe_int(payload.get("owner_pid"))
    owner_running = _process_running(owner_pid)
    active = payload.get("lock_status") == "active" and owner_pid is not None and owner_running
    stale = payload.get("lock_status") == "active" and not active
    status = {
        "guard_enabled": True,
        "lock_status": "active" if active else ("stale" if stale else str(payload.get("lock_status") or "unknown")),
        "lock_file": str(lock_file),
        "current_pid": os.getpid(),
        "owner_pid": owner_pid,
        "owner_parent_pid": payload.get("owner_parent_pid"),
        "owner_process_identity": payload.get("owner_process_identity"),
        "owner_running": owner_running,
        "active": active,
        "stale": stale,
        "owned_by_current_process": owner_pid == os.getpid(),
        "acquired_at_ms": payload.get("acquired_at_ms"),
        "released_at_ms": payload.get("released_at_ms"),
        "release_reason": payload.get("release_reason"),
    }
    return status


def _safe_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _process_running(pid: int | None) -> bool:
    if pid is None:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        return _windows_process_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _windows_process_running(pid: int) -> bool:
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == 259
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _current_process_identity(*, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "pid": os.getpid(),
        "parent_pid": os.getppid(),
        "executable": sys.executable,
        "install_layout": _install_layout(),
        "packaged_onefile_process_note": "pyinstaller_one_file_may_show_parent_and_child_process_rows",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _current_process_session_report() -> dict[str, Any]:
    if os.name != "nt":
        return {
            "platform_supported": False,
            "process_id": os.getpid(),
            "process_session_id": None,
            "active_console_session_id": None,
            "process_is_active_console_session": True,
            "process_session_is_wts_active": True,
            "process_is_active_visible_session": True,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        session_id = wintypes.DWORD()
        ok = bool(kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)))
        active_console = int(kernel32.WTSGetActiveConsoleSessionId())
        process_session_id = int(session_id.value) if ok else None
        connect_state = _wts_connect_state(process_session_id)
        process_is_active_console_session = bool(ok and process_session_id == active_console)
        process_session_is_wts_active = connect_state.get("state_name") == "WTSActive"
        return {
            "platform_supported": True,
            "query_ok": ok,
            "process_id": os.getpid(),
            "process_session_id": process_session_id,
            "active_console_session_id": active_console,
            "process_is_active_console_session": process_is_active_console_session,
            "process_session_connect_state": connect_state,
            "process_session_is_wts_active": process_session_is_wts_active,
            "process_is_active_visible_session": bool(process_is_active_console_session or process_session_is_wts_active),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    except Exception as exc:
        return {
            "platform_supported": True,
            "query_ok": False,
            "process_id": os.getpid(),
            "process_session_id": None,
            "active_console_session_id": None,
            "process_is_active_console_session": False,
            "process_session_is_wts_active": False,
            "process_is_active_visible_session": False,
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }


def _session_report_is_active_visible(session: dict[str, Any]) -> bool:
    return bool(
        session.get("process_is_active_visible_session")
        or session.get("process_is_active_console_session")
        or session.get("process_session_is_wts_active")
    )


def _wts_connect_state(session_id: int | None) -> dict[str, Any]:
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


def _host_component_status(*, host_probe_status: str, host_action: dict[str, Any]) -> str:
    if host_action.get("action") == "host_agent_restart_blocked_supervisor_not_active_console_session":
        return "blocked_by_session_mismatch"
    if host_action.get("action") == "host_agent_restart_blocked_existing_process_still_running":
        return "restart_blocked"
    if host_probe_status == "agent_responding":
        return "running"
    if host_probe_status in {"emergency_stop_active", "stop_requested"}:
        return "blocked_by_control_plane"
    if host_action.get("action") == "start_host_agent":
        return "restarting"
    if host_action.get("action") == "start_host_agent_failed":
        return "restart_failed"
    return "unavailable"


def _tray_backend_status(*, host_probe_status: str, supervisor_status: str) -> str:
    if supervisor_status == "emergency_stopped":
        return "emergency_stopped"
    if host_probe_status == "agent_responding":
        return "available"
    if host_probe_status in {"discovery_missing", "stale_discovery_pid_not_running", "health_unreachable"}:
        return "backend_unavailable_or_restarting"
    return "unknown"


def install_session_supervisor(
    *,
    host: str,
    port: int,
    runs_dir: str,
    repo_root: Path,
    python_command: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    arm_real_input: bool,
    start_now: bool,
    start_method: str,
    wait_seconds: float,
) -> dict[str, Any]:
    resolved_start_method = _resolve_start_method(start_method)
    default_agent_dir().mkdir(parents=True, exist_ok=True)
    _write_install_progress("started", start_method=start_method, resolved_start_method=resolved_start_method, start_now=start_now)
    default_session_supervisor_stop_file().unlink(missing_ok=True)
    enabled_marker = _write_unified_supervisor_enabled_marker()
    _write_install_progress("unified_marker_written", marker_file=str(default_unified_supervisor_enabled_file()))
    run_command = _session_supervisor_run_command_line(
        host=host,
        port=port,
        runs_dir=runs_dir,
        repo_root=repo_root,
        python_command=python_command,
        agent_exe=agent_exe if agent_exe and agent_exe.exists() else None,
        tray_gui_exe=tray_gui_exe if tray_gui_exe and tray_gui_exe.exists() else None,
        arm_real_input=arm_real_input,
    )
    command = _session_supervisor_command(
        host=host,
        port=port,
        runs_dir=runs_dir,
        repo_root=repo_root,
        python_command=python_command,
        agent_exe=agent_exe if agent_exe and agent_exe.exists() else None,
        tray_gui_exe=tray_gui_exe if tray_gui_exe and tray_gui_exe.exists() else None,
        arm_real_input=arm_real_input,
    )
    command_file = default_session_supervisor_command_file()
    vbs_file = default_session_supervisor_vbs_file()
    command_file.write_text(command, encoding="utf-8")
    _write_install_progress("command_written", command_file=str(command_file), run_command=run_command)
    vbs_launcher = {"install_status": "not_used", "reason": f"{resolved_start_method}_selected", "startup_launcher": str(vbs_file)}
    run_key = {"install_status": "not_used", "reason": f"{resolved_start_method}_selected"}
    onlogon_task = {
        "task_name": SESSION_SUPERVISOR_ONLOGON_TASK_NAME,
        "install_status": "skipped_by_start_method",
        "start_method": resolved_start_method,
        "task_launcher": str(command_file),
        "reason": f"{resolved_start_method}_selected",
    }
    if resolved_start_method == "startup_vbs":
        default_session_supervisor_vbs_file().parent.mkdir(parents=True, exist_ok=True)
        vbs_file.write_text(_hidden_vbs(command_file), encoding="ascii")
        vbs_launcher = {"install_status": "installed", "startup_launcher": str(vbs_file)}
        _write_install_progress("vbs_written", startup_launcher=str(vbs_file))
    elif resolved_start_method == "onlogon_task":
        onlogon_task = _install_onlogon_task(command_file)
        _write_install_progress("onlogon_task_resolved", onlogon_task=onlogon_task)
    else:
        run_key = _install_run_key(run_command)
        _write_install_progress("run_key_written", run_key=run_key)
    report: dict[str, Any] = {
        "object_type": "AgentSightSupervisorInstallReport",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "install_status": "installed",
        "startup_launcher": str(vbs_file) if resolved_start_method == "startup_vbs" else run_key.get("run_key_path"),
        "supervisor_command": str(command_file),
        "supervisor_run_command": run_command,
        "install_report_file": str(default_session_supervisor_install_report_file()),
        "last_report_file": str(default_session_supervisor_report_file()),
        "install_layout": _install_layout(),
        "install_root": str(_default_runtime_root()),
        "packaged_layout": _packaged_layout(),
        "self_start_entry": "AgentSight",
        "self_start_entry_is_unified_supervisor": True,
        "registered_startup_components": ["AgentSight"],
        "supervisor_exe": str(_frozen_session_supervisor_exe()) if getattr(sys, "frozen", False) else None,
        "unified_supervisor_enabled_file": str(default_unified_supervisor_enabled_file()),
        "unified_supervisor_enabled": enabled_marker,
        "legacy_split_watchdogs_recommended": False,
        "legacy_split_watchdogs": {
            "host_agent_watchdog": "legacy_compatibility_only",
            "tray_gui_watchdog": "legacy_compatibility_only",
            "suppressed_when_unified_marker_exists": True,
        },
        "state_file": str(default_session_supervisor_state_file()),
        "stop_file": str(default_session_supervisor_stop_file()),
        "repo_root": str(repo_root),
        "python_command": python_command,
        "agent_exe": str(agent_exe) if agent_exe else None,
        "tray_gui_exe": str(tray_gui_exe) if tray_gui_exe else None,
        "start_now": start_now,
        "start_method": start_method,
        "resolved_start_method": resolved_start_method,
        "run_key": run_key,
        "startup_vbs": vbs_launcher,
        "onlogon_task": onlogon_task,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }
    _write_install_progress("report_constructed", exit_code=report["exit_code"])
    if start_now:
        _write_install_progress("start_now_entered", wait_seconds=wait_seconds)
        report["start"] = start_installed_session_supervisor(start_method=resolved_start_method, wait_seconds=wait_seconds)
        _write_install_progress("start_now_finished", start=report["start"])
        if report["start"].get("exit_code") not in {0, None}:
            report["exit_code"] = report["start"].get("exit_code", 5)
            _write_install_progress("start_now_exit_code_adjusted", exit_code=report["exit_code"])
    _write_json_path(default_session_supervisor_install_report_file(), report)
    _write_json_path(default_session_supervisor_report_file(), report)
    _write_install_progress("reports_written", install_report_file=str(default_session_supervisor_install_report_file()))
    return report


def start_installed_session_supervisor(*, start_method: str = "auto", wait_seconds: float = 10.0) -> dict[str, Any]:
    resolved_start_method = _resolve_start_method(start_method)
    vbs_file = default_session_supervisor_vbs_file()
    command_file = default_session_supervisor_command_file()
    lock_status = _session_supervisor_lock_status()
    if not command_file.exists():
        return {
            "object_type": "AgentSightSupervisorStartReport",
            "schema": SESSION_SUPERVISOR_SCHEMA,
            "start_status": "not_installed",
            "startup_launcher": str(vbs_file) if resolved_start_method == "startup_vbs" else str(command_file),
            "supervisor_command": str(command_file),
            "process_identity": _current_process_identity(role="session_supervisor_start"),
            "single_instance": lock_status,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
            "exit_code": 4,
        }
    if lock_status.get("lock_status") == "active":
        report = {
            "object_type": "AgentSightSupervisorStartReport",
            "schema": SESSION_SUPERVISOR_SCHEMA,
            "start_status": "already_running",
            "startup_launcher": str(vbs_file) if resolved_start_method == "startup_vbs" else str(command_file),
            "supervisor_command": str(command_file),
            "start_method": start_method,
            "resolved_start_method": resolved_start_method,
            "state_file": str(default_session_supervisor_state_file()),
            "state_seen_after_wait": True,
            "last_state": read_json_file(default_session_supervisor_state_file()),
            "process_identity": _current_process_identity(role="session_supervisor_start"),
            "single_instance": lock_status,
            "launcher": {"started": False, "reason": "active_supervisor_lock"},
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
            "exit_code": 0,
        }
        _write_json_path(default_session_supervisor_report_file(), report)
        return report
    default_session_supervisor_stop_file().unlink(missing_ok=True)
    launcher = _start_hidden(vbs_file, command_file=command_file, start_method=resolved_start_method)
    state_seen = _wait_for_state_update(wait_seconds=wait_seconds)
    report = {
        "object_type": "AgentSightSupervisorStartReport",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "start_status": "started" if launcher.get("started") else "start_attempted",
        "startup_launcher": str(vbs_file) if resolved_start_method == "startup_vbs" else str(command_file),
        "supervisor_command": str(command_file),
        "start_method": start_method,
        "resolved_start_method": resolved_start_method,
        "state_file": str(default_session_supervisor_state_file()),
        "state_seen_after_wait": state_seen,
        "last_state": read_json_file(default_session_supervisor_state_file()),
        "process_identity": _current_process_identity(role="session_supervisor_start"),
        "single_instance": _session_supervisor_lock_status(),
        "launcher": launcher,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0 if launcher.get("started") else 5,
    }
    _write_json_path(default_session_supervisor_report_file(), report)
    return report


def session_supervisor_status() -> dict[str, Any]:
    command_file = default_session_supervisor_command_file()
    vbs_file = default_session_supervisor_vbs_file()
    run_key = _read_run_key()
    state = read_json_file(default_session_supervisor_state_file())
    return {
        "object_type": "AgentSightSupervisorStatus",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "installed": command_file.exists() and (run_key.get("exists") or vbs_file.exists()),
        "startup_launcher": run_key.get("run_key_path") if run_key.get("exists") else str(vbs_file),
        "supervisor_command": str(command_file),
        "run_key": run_key,
        "startup_vbs_exists": vbs_file.exists(),
        "state_file": str(default_session_supervisor_state_file()),
        "stop_file": str(default_session_supervisor_stop_file()),
        "stop_requested": default_session_supervisor_stop_file().exists(),
        "process_identity": _current_process_identity(role="session_supervisor_status"),
        "single_instance": _session_supervisor_lock_status(),
        "state": state,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }


def stop_session_supervisor(*, reason: str, wait_seconds: float = 6.0, force_after_timeout: bool = True) -> dict[str, Any]:
    stop_file = default_session_supervisor_stop_file()
    requested_at_ms = _now_ms()
    payload = {
        "object_type": "AgentSightSupervisorStopRequest",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "stop_kind": "full_agentsight_shutdown",
        "requested_at_ms": requested_at_ms,
        "reason": reason,
        "semantics": {
            "emergency_stop": False,
            "operator_pause": False,
            "full_shutdown": True,
            "request_host_agent_shutdown": True,
            "request_tray_gui_close": True,
            "request_supervisor_exit": True,
        },
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
    }
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    discovery = read_json_file(default_discovery_file())
    shutdown = request_host_agent_shutdown(discovery)
    host_process = _ensure_host_agent_stopped(
        discovery=discovery,
        shutdown=shutdown,
        wait_seconds=wait_seconds,
        force_after_timeout=force_after_timeout,
    )
    tray_close = _request_tray_window_close()
    tray_process = _ensure_tray_gui_closed(
        tray_close=tray_close,
        wait_seconds=wait_seconds,
        force_after_timeout=force_after_timeout,
    )
    supervisor_process = _ensure_supervisor_lock_released(
        wait_seconds=wait_seconds,
        force_after_timeout=force_after_timeout,
    )
    report = {
        "object_type": "AgentSightSupervisorStopReport",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "stop_kind": "full_agentsight_shutdown",
        "stop_requested": True,
        "stop_file": str(stop_file),
        "payload": payload,
        "process_identity": _current_process_identity(role="session_supervisor_stop"),
        "single_instance": _session_supervisor_lock_status(),
        "host_agent_shutdown": shutdown,
        "host_agent_process": host_process,
        "tray_close": tray_close,
        "tray_process": tray_process,
        "supervisor_process": supervisor_process,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }
    _write_json_path(default_session_supervisor_report_file(), report)
    return report


def _ensure_host_agent_stopped(
    *,
    discovery: dict[str, Any] | None,
    shutdown: dict[str, Any],
    wait_seconds: float,
    force_after_timeout: bool,
) -> dict[str, Any]:
    pid = _pid_from_discovery(discovery)
    if pid is None:
        return {
            "component": "host_agent",
            "pid": None,
            "graceful_wait_attempted": False,
            "force_attempted": False,
            "status": "pid_unavailable",
            "shutdown_status": shutdown.get("shutdown_status") if isinstance(shutdown, dict) else None,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    exited_gracefully = _wait_until_process_exits(pid, wait_seconds=wait_seconds)
    force = {"force_attempted": False, "reason": "process_exited_gracefully" if exited_gracefully else "force_disabled"}
    if not exited_gracefully and force_after_timeout:
        force = _terminate_process(pid, reason="host_agent_stop_timeout")
    running_after = _process_running(pid)
    return {
        "component": "host_agent",
        "pid": pid,
        "graceful_wait_attempted": True,
        "graceful_wait_seconds": max(0.0, float(wait_seconds or 0.0)),
        "exited_gracefully": exited_gracefully,
        "force": force,
        "running_after_stop_attempt": running_after,
        "status": "stopped" if not running_after else "still_running_after_stop_attempt",
        "shutdown_status": shutdown.get("shutdown_status") if isinstance(shutdown, dict) else None,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _ensure_tray_gui_closed(
    *,
    tray_close: dict[str, Any],
    wait_seconds: float,
    force_after_timeout: bool,
) -> dict[str, Any]:
    pid = _tray_window_process_id()
    if pid == os.getpid():
        return {
            "component": "tray_gui",
            "pid": pid,
            "close_requested": bool(tray_close.get("close_requested")) if isinstance(tray_close, dict) else False,
            "graceful_wait_attempted": False,
            "force": {"force_attempted": False, "reason": "current_tray_process_exits_after_command_handler"},
            "window_closed": False,
            "status": "current_process_will_exit_after_handler",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    window_closed = _wait_until_tray_window_absent(wait_seconds=wait_seconds)
    force = {"force_attempted": False, "reason": "tray_window_closed" if window_closed else "force_disabled"}
    if not window_closed and force_after_timeout and pid is not None:
        force = _terminate_process(pid, reason="tray_gui_close_timeout")
        window_closed = _wait_until_tray_window_absent(wait_seconds=1.0)
    return {
        "component": "tray_gui",
        "pid": pid,
        "close_requested": bool(tray_close.get("close_requested")) if isinstance(tray_close, dict) else False,
        "graceful_wait_attempted": True,
        "graceful_wait_seconds": max(0.0, float(wait_seconds or 0.0)),
        "window_closed": window_closed,
        "force": force,
        "status": "closed" if window_closed else "still_visible_after_stop_attempt",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _ensure_supervisor_lock_released(*, wait_seconds: float, force_after_timeout: bool) -> dict[str, Any]:
    before = _session_supervisor_lock_status()
    owner_pid = before.get("owner_pid") if isinstance(before, dict) else None
    released_gracefully = _wait_for_lock_release(wait_seconds=wait_seconds)
    force = {"force_attempted": False, "reason": "lock_released_gracefully" if released_gracefully else "force_disabled"}
    if not released_gracefully and force_after_timeout and isinstance(owner_pid, int) and owner_pid != os.getpid():
        force = _terminate_process(owner_pid, reason="session_supervisor_stop_timeout")
        _wait_until_process_exits(owner_pid, wait_seconds=1.0)
    stale_release = _release_lock_if_owner_not_running(reason="stop_session_supervisor_stale_or_forced_lock")
    after = _session_supervisor_lock_status()
    return {
        "component": "session_supervisor",
        "lock_before": before,
        "graceful_wait_attempted": True,
        "graceful_wait_seconds": max(0.0, float(wait_seconds or 0.0)),
        "released_gracefully": released_gracefully,
        "force": force,
        "stale_release": stale_release,
        "lock_after": after,
        "status": "released" if after.get("lock_status") in {"released", "missing", "stale"} else "still_active_after_stop_attempt",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _pid_from_discovery(discovery: dict[str, Any] | None) -> int | None:
    if not isinstance(discovery, dict):
        return None
    try:
        pid = int(discovery.get("pid"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _wait_until_process_exits(pid: int, *, wait_seconds: float) -> bool:
    deadline = time.time() + max(0.0, float(wait_seconds or 0.0))
    while True:
        if not _process_running(pid):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(min(0.2, max(0.0, deadline - time.time())))


def _terminate_process(pid: int, *, reason: str) -> dict[str, Any]:
    if pid <= 0:
        return {
            "force_attempted": False,
            "force_status": "invalid_pid",
            "pid": pid,
            "reason": reason,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {
            "force_attempted": True,
            "force_status": "already_exited",
            "pid": pid,
            "reason": reason,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    except Exception as exc:
        return {
            "force_attempted": True,
            "force_status": "failed",
            "pid": pid,
            "reason": reason,
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    return {
        "force_attempted": True,
        "force_status": "signal_sent",
        "pid": pid,
        "reason": reason,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _tray_window_process_id() -> int | None:
    if not _is_windows():
        return None
    try:
        hwnd = ctypes.windll.user32.FindWindowW(TRAY_WINDOW_CLASS_NAME, None)
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) if int(pid.value) > 0 else None
    except Exception:
        return None


def _wait_until_tray_window_absent(*, wait_seconds: float) -> bool:
    deadline = time.time() + max(0.0, float(wait_seconds or 0.0))
    while True:
        if not _tray_window_present():
            return True
        if time.time() >= deadline:
            return False
        time.sleep(min(0.2, max(0.0, deadline - time.time())))


def _wait_for_lock_release(*, wait_seconds: float) -> bool:
    deadline = time.time() + max(0.0, float(wait_seconds or 0.0))
    while True:
        status = _session_supervisor_lock_status()
        if status.get("lock_status") in {"missing", "released", "stale"}:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(min(0.2, max(0.0, deadline - time.time())))


def _release_lock_if_owner_not_running(*, reason: str) -> dict[str, Any]:
    status = _session_supervisor_lock_status()
    owner_pid = status.get("owner_pid")
    if status.get("lock_status") not in {"active", "stale"} or not isinstance(owner_pid, int):
        return {
            "release_attempted": False,
            "reason": "lock_not_active_with_owner_pid",
            "lock_status": status.get("lock_status"),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    if _process_running(owner_pid):
        return {
            "release_attempted": False,
            "reason": "owner_still_running",
            "owner_pid": owner_pid,
            "lock_status": status.get("lock_status"),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    payload = {
        "object_type": "AgentSightSupervisorLock",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "lock_status": "released",
        "owner_pid": owner_pid,
        "released_by_pid": os.getpid(),
        "released_at_ms": _now_ms(),
        "release_reason": reason,
        "previous_lock_status": status.get("lock_status"),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    try:
        _write_json_path(default_session_supervisor_lock_file(), payload)
        status_text = "released"
    except OSError as exc:
        status_text = "release_failed"
        payload["error"] = str(exc)
    return {
        "release_attempted": True,
        "release_status": status_text,
        "owner_pid": owner_pid,
        "payload": payload,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def uninstall_session_supervisor(*, stop_running: bool = True) -> dict[str, Any]:
    stop_report = stop_session_supervisor(reason="uninstall_session_supervisor") if stop_running else None
    command_file = default_session_supervisor_command_file()
    vbs_file = default_session_supervisor_vbs_file()
    command_existed = command_file.exists()
    vbs_existed = vbs_file.exists()
    command_file.unlink(missing_ok=True)
    vbs_file.unlink(missing_ok=True)
    run_key_delete = _delete_run_key()
    marker_existed = default_unified_supervisor_enabled_file().exists()
    default_unified_supervisor_enabled_file().unlink(missing_ok=True)
    task_delete = _delete_onlogon_task()
    discovery_stale = (
        _mark_discovery_stale(reason="session_supervisor_uninstalled")
        if stop_running
        else {"status": "not_marked_keep_running", "discovery_file": str(default_discovery_file())}
    )
    report = {
        "object_type": "AgentSightSupervisorUninstallReport",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "uninstall_status": "removed",
        "uninstall_report_file": str(default_session_supervisor_uninstall_report_file()),
        "last_report_file": str(default_session_supervisor_report_file()),
        "uninstall_keeps_evidence": True,
        "supervisor_command_removed": command_existed,
        "startup_launcher_removed": vbs_existed,
        "run_key": run_key_delete,
        "unified_supervisor_enabled_removed": marker_existed,
        "onlogon_task": task_delete,
        "stop": stop_report or {"stop_requested": False, "reason": "keep_running"},
        "discovery_stale": discovery_stale,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0,
    }
    _write_json_path(default_session_supervisor_uninstall_report_file(), report)
    _write_json_path(default_session_supervisor_report_file(), report)
    return report


def _session_supervisor_command(
    *,
    host: str,
    port: int,
    runs_dir: str,
    repo_root: Path,
    python_command: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    arm_real_input: bool,
) -> str:
    args = _session_supervisor_run_args(
        host=host,
        port=port,
        runs_dir=runs_dir,
        repo_root=repo_root,
        python_command=python_command,
        agent_exe=agent_exe,
        tray_gui_exe=tray_gui_exe,
        arm_real_input=arm_real_input,
    )
    if getattr(sys, "frozen", False):
        launch_line = _session_supervisor_run_command_line(
            host=host,
            port=port,
            runs_dir=runs_dir,
            repo_root=repo_root,
            python_command=python_command,
            agent_exe=agent_exe,
            tray_gui_exe=tray_gui_exe,
            arm_real_input=arm_real_input,
        )
        return "\r\n".join(["@echo off", "chcp 65001 >nul", launch_line, ""])
    launch_line = " ".join([_quote(python_command), "-m", "agentsight.session_supervisor", *(_quote(part) for part in args)])
    return "\r\n".join(["@echo off", "chcp 65001 >nul", f'cd /d "{repo_root}"', "set PYTHONPATH=src", launch_line, ""])


def _session_supervisor_run_args(
    *,
    host: str,
    port: int,
    runs_dir: str,
    repo_root: Path,
    python_command: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    arm_real_input: bool,
) -> list[str]:
    effective_python_command = "AgentSightSupervisor.exe" if getattr(sys, "frozen", False) else python_command
    args = [
        "run",
        "--host",
        host,
        "--port",
        str(port),
        "--runs-dir",
        runs_dir,
        "--repo-root",
        str(repo_root),
        "--python",
        effective_python_command,
    ]
    if arm_real_input:
        args.append("--arm-real-input")
    if agent_exe:
        args.extend(["--agent-exe", str(agent_exe)])
    if tray_gui_exe:
        args.extend(["--tray-gui-exe", str(tray_gui_exe)])
    return args


def _session_supervisor_run_command_line(
    *,
    host: str,
    port: int,
    runs_dir: str,
    repo_root: Path,
    python_command: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    arm_real_input: bool,
) -> str:
    args = _session_supervisor_run_args(
        host=host,
        port=port,
        runs_dir=runs_dir,
        repo_root=repo_root,
        python_command=python_command,
        agent_exe=agent_exe,
        tray_gui_exe=tray_gui_exe,
        arm_real_input=arm_real_input,
    )
    if getattr(sys, "frozen", False):
        return " ".join([_quote(str(_frozen_session_supervisor_exe())), *(_quote(part) for part in args)])
    return " ".join([_quote(python_command), "-m", "agentsight.session_supervisor", *(_quote(part) for part in args)])


def _host_agent_child_command(
    *,
    host: str,
    port: int,
    runs_dir: str,
    repo_root: Path,
    python_command: str,
    agent_exe: Path | None,
    arm_real_input: bool,
) -> list[str]:
    if agent_exe:
        command = [str(agent_exe)]
    else:
        command = [python_command, "-m", "agentsight.host_agent.server"]
    command.extend(["--host", host, "--port", str(port), "--runs-dir", runs_dir, "--discovery-file", str(default_discovery_file())])
    if arm_real_input:
        command.append("--arm-real-input")
    return command


def _tray_gui_child_command(*, repo_root: Path, python_command: str, tray_gui_exe: Path | None) -> list[str]:
    if tray_gui_exe:
        return [str(tray_gui_exe), "run"]
    return [python_command, "-m", "agentsight.tray.gui", "run"]


def _write_unified_supervisor_enabled_marker() -> dict[str, Any]:
    payload = {
        "object_type": "AgentSightUnifiedSessionSupervisorEnabled",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "enabled": True,
        "enabled_at_ms": _now_ms(),
        "reason": "session_supervisor_install",
        "legacy_split_watchdogs_should_not_restart_children": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    _write_json_path(default_unified_supervisor_enabled_file(), payload)
    return payload


def _packaged_layout() -> dict[str, Any]:
    install_root = _default_runtime_root()
    expected = {
        "installer": install_root / "AgentSightSetup.exe",
        "session_supervisor": install_root / "AgentSightSupervisor.exe",
        "host_agent": install_root / "AgentSightHostAgent.exe",
        "tray_gui": install_root / "AgentSightTray.exe",
    }
    return {
        "mode": _install_layout(),
        "install_root": str(install_root),
        "source_mode_entry": "py -m agentsight.session_supervisor run",
        "frozen_mode_entry": "AgentSightSupervisor.exe run",
        "final_user_requires_pythonpath": False,
        "self_start_registers_only": ["AgentSightSupervisor"],
        "adjacent_exes": {name: str(path) for name, path in expected.items()},
        "adjacent_exes_present": {name: path.exists() for name, path in expected.items()},
    }


def _mark_discovery_stale(*, reason: str) -> dict[str, Any]:
    discovery_file = default_discovery_file()
    previous = read_json_file(discovery_file)
    if not discovery_file.exists() and not isinstance(previous, dict):
        return {"status": "already_missing", "discovery_file": str(discovery_file)}
    payload = {
        "object_type": "AgentSightHostAgentDiscoveryStaleMarker",
        "schema": "agentsight_host_agent_discovery_stale_v1",
        "stale": True,
        "stale_reason": reason,
        "marked_at_ms": _now_ms(),
        "discovery_file": str(discovery_file),
        "previous": _redact_previous_discovery(previous),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    try:
        _write_json_path(discovery_file, payload)
    except OSError as exc:
        return {"status": "mark_failed", "discovery_file": str(discovery_file), "error": str(exc)}
    return {"status": "marked_stale", "discovery_file": str(discovery_file), "marker": payload}


def _redact_previous_discovery(discovery: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(discovery, dict):
        return None
    redacted = dict(discovery)
    if "token" in redacted:
        redacted["token"] = "<redacted>"
    return redacted


def _record_restart_attempt(component: str, *, restart_history: dict[str, list[int]], now_ms: int) -> None:
    entries = restart_history.setdefault(component, [])
    entries.append(int(now_ms))
    cutoff = int(now_ms) - RESTART_STORM_WINDOW_MS
    restart_history[component] = [item for item in entries if int(item) >= cutoff]


def _restart_storm_status(component: str, *, restart_history: dict[str, list[int]], now_ms: int) -> dict[str, Any]:
    cutoff = int(now_ms) - RESTART_STORM_WINDOW_MS
    entries = [item for item in restart_history.get(component, []) if int(item) >= cutoff]
    restart_history[component] = entries
    return {
        "component": component,
        "restart_storm_active": len(entries) >= RESTART_STORM_LIMIT,
        "restart_count": len(entries),
        "window_ms": RESTART_STORM_WINDOW_MS,
        "limit": RESTART_STORM_LIMIT,
        "since_ms": min(entries) if entries else None,
    }


def _restart_storm_action(component: str, storm: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": f"{component}_restart_suppressed",
        "reason": "restart_storm_guard",
        "restart_storm": storm,
        "operator_notification_enqueued": True,
    }


def _notify_restart_storm_once(component: str, storm: dict[str, Any], *, notified: set[str]) -> None:
    key = f"{component}:{storm.get('since_ms')}"
    if key in notified:
        return
    notified.add(key)
    label = "Host Agent" if component == "host_agent" else "Tray GUI"
    count = int(storm.get("restart_count") or 0)
    minutes = max(1, int(RESTART_STORM_WINDOW_MS / 60000))
    text = (
        f"AgentSight 检测到 {label} 在 {minutes} 分钟内连续重启 {count} 次仍未稳定，"
        "已暂停该组件自动重启以避免占满磁盘或反复弹错。请检查 C 盘空间后重启 AgentSight 或重启电脑。"
    )
    try:
        enqueue_notification(
            text=text,
            stage="restart_storm_guard",
            priority=90,
            channel="wechat_file_transfer_assistant",
        )
    except Exception:
        pass


def _start_child_process(command: list[str], *, cwd: Path, action_name: str) -> tuple[dict[str, Any], Popen[Any] | None]:
    try:
        process = Popen(
            command,
            cwd=str(cwd),
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return {
            "action": f"{action_name}_failed",
            "error": str(exc),
            "command": _redacted_process_command(command),
        }, None
    return {
        "action": action_name,
        "child_pid": process.pid,
        "launcher_pid": os.getpid(),
        "launcher_parent_pid": os.getppid(),
        "command": _redacted_process_command(command),
    }, process


def _write_supervisor_state(report: dict[str, Any]) -> None:
    _write_json_path(default_session_supervisor_state_file(), report)
    _write_json_path(default_session_supervisor_report_file(), report)


def _write_json_report(report: dict[str, Any], output: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _write_json_path(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_install_progress(stage: str, **extra: Any) -> None:
    payload = {
        "object_type": "AgentSightSupervisorInstallProgress",
        "schema": SESSION_SUPERVISOR_SCHEMA,
        "stage": stage,
        "timestamp_ms": _now_ms(),
        "install_progress_file": str(default_session_supervisor_install_progress_file()),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        **extra,
    }
    try:
        _write_json_path(default_session_supervisor_install_progress_file(), payload)
    except Exception:
        pass


def _hidden_vbs(cmd_path: Path) -> str:
    return "\n".join(
        [
            'Set shell = CreateObject("WScript.Shell")',
            f'shell.Run """" & "{cmd_path}" & """", 0, False',
            "",
        ]
    )


def _resolve_start_method(start_method: str) -> str:
    if start_method == "auto":
        return "run_key" if os.name == "nt" else "startup_vbs"
    return start_method


def _install_run_key(command_line: str) -> dict[str, Any]:
    if os.name != "nt":
        return {"install_status": "skipped_non_windows", "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SESSION_SUPERVISOR_RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, SESSION_SUPERVISOR_RUN_KEY_NAME, 0, winreg.REG_SZ, command_line)
    except Exception as exc:
        return {
            "install_status": "install_failed",
            "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME,
            "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH,
            "error": str(exc),
        }
    return {
        "install_status": "installed",
        "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME,
        "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH,
        "command": command_line,
    }


def _read_run_key() -> dict[str, Any]:
    if os.name != "nt":
        return {"exists": False, "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME, "reason": "non_windows"}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SESSION_SUPERVISOR_RUN_KEY_PATH, 0, winreg.KEY_READ) as key:
            value, value_type = winreg.QueryValueEx(key, SESSION_SUPERVISOR_RUN_KEY_NAME)
    except FileNotFoundError:
        return {"exists": False, "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME, "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH}
    except Exception as exc:
        return {
            "exists": False,
            "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME,
            "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH,
            "error": str(exc),
        }
    return {
        "exists": True,
        "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME,
        "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH,
        "command": value,
        "value_type": value_type,
    }


def _delete_run_key() -> dict[str, Any]:
    if os.name != "nt":
        return {"delete_status": "skipped_non_windows", "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME}
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SESSION_SUPERVISOR_RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, SESSION_SUPERVISOR_RUN_KEY_NAME)
    except FileNotFoundError:
        return {"delete_status": "already_missing", "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME, "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH}
    except Exception as exc:
        return {
            "delete_status": "delete_failed",
            "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME,
            "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH,
            "error": str(exc),
        }
    return {"delete_status": "deleted", "run_key_name": SESSION_SUPERVISOR_RUN_KEY_NAME, "run_key_path": "HKCU\\" + SESSION_SUPERVISOR_RUN_KEY_PATH}


def _start_hidden(vbs_path: Path, *, command_file: Path, start_method: str) -> dict[str, Any]:
    if start_method in {"auto", "onlogon_task"}:
        task_report = _run_onlogon_task()
        if task_report.get("started") or start_method == "onlogon_task":
            return task_report
    if start_method == "run_key":
        run_key = _read_run_key()
        command_line = run_key.get("command") if isinstance(run_key, dict) else None
        if isinstance(command_line, str) and command_line.strip():
            return _start_via_command_line(command_line, source="run_key")
        fallback = _start_via_command_file(command_file)
        fallback["run_key"] = run_key
        return fallback
    if start_method in {"auto", "startup_vbs"}:
        return _start_via_startup_vbs(vbs_path)
    return _start_via_startup_vbs(vbs_path)


def _start_via_command_file(command_file: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"start_method_used": "command_file", "started": False, "supervisor_command": str(command_file), "error": "requires_windows"}
    try:
        process = Popen(
            ["cmd.exe", "/d", "/s", "/c", str(command_file)],
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
            creationflags=getattr(__import__("subprocess"), "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return {"start_method_used": "command_file", "started": False, "supervisor_command": str(command_file), "error": str(exc)}
    return {
        "start_method_used": "command_file",
        "started": True,
        "supervisor_command": str(command_file),
        "pid": process.pid,
        "launcher_pid": os.getpid(),
        "launcher_parent_pid": os.getppid(),
    }


def _start_via_command_line(command_line: str, *, source: str) -> dict[str, Any]:
    if os.name != "nt":
        return {"start_method_used": source, "started": False, "command": command_line, "error": "requires_windows"}
    try:
        process = Popen(
            command_line,
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        return {"start_method_used": source, "started": False, "command": command_line, "error": str(exc)}
    return {
        "start_method_used": source,
        "started": True,
        "command": command_line,
        "pid": process.pid,
        "launcher_pid": os.getpid(),
        "launcher_parent_pid": os.getppid(),
    }


def _start_via_startup_vbs(vbs_path: Path) -> dict[str, Any]:
    try:
        os.startfile(str(vbs_path))  # type: ignore[attr-defined]
        return {"start_method_used": "startup_vbs", "started": True, "startup_launcher": str(vbs_path)}
    except Exception as exc:
        return {"start_method_used": "startup_vbs", "started": False, "startup_launcher": str(vbs_path), "error": str(exc)}


def _install_onlogon_task(command_path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {"task_name": SESSION_SUPERVISOR_ONLOGON_TASK_NAME, "install_status": "skipped_non_windows"}
    create = run_schtasks(
        [
            "/Create",
            "/TN",
            SESSION_SUPERVISOR_ONLOGON_TASK_NAME,
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
        "task_name": SESSION_SUPERVISOR_ONLOGON_TASK_NAME,
        "install_status": "installed" if create.returncode == 0 else "install_failed",
        "task_launcher": str(command_path),
        "schedule": "ONLOGON",
        "run_level": "LIMITED",
        "create": completed_process_report(create),
    }


def _run_onlogon_task() -> dict[str, Any]:
    if os.name != "nt":
        return {"start_method_used": "onlogon_task", "started": False, "error": "requires_windows"}
    run = run_schtasks(["/Run", "/TN", SESSION_SUPERVISOR_ONLOGON_TASK_NAME])
    return {
        "start_method_used": "onlogon_task",
        "started": run.returncode == 0,
        "task_name": SESSION_SUPERVISOR_ONLOGON_TASK_NAME,
        "run": completed_process_report(run),
    }


def _delete_onlogon_task() -> dict[str, Any]:
    if os.name != "nt":
        return {"task_name": SESSION_SUPERVISOR_ONLOGON_TASK_NAME, "delete_status": "skipped_non_windows"}
    delete = run_schtasks(["/Delete", "/TN", SESSION_SUPERVISOR_ONLOGON_TASK_NAME, "/F"])
    return {
        "task_name": SESSION_SUPERVISOR_ONLOGON_TASK_NAME,
        "delete_status": "deleted" if delete.returncode == 0 else "delete_failed_or_missing",
        "delete": completed_process_report(delete),
    }


def _wait_for_state_update(*, wait_seconds: float) -> bool:
    start = _now_ms()
    deadline = time.time() + max(0.0, wait_seconds)
    while time.time() <= deadline:
        state = read_json_file(default_session_supervisor_state_file())
        if isinstance(state, dict) and int(state.get("updated_at_ms") or 0) >= start:
            return True
        time.sleep(0.25)
    return False


def _sleep_with_stop_poll(interval_seconds: float) -> None:
    deadline = time.time() + max(0.5, float(interval_seconds or 5.0))
    while time.time() < deadline:
        if default_session_supervisor_stop_file().exists():
            return
        time.sleep(min(0.25, max(0.0, deadline - time.time())))


def _install_layout() -> str:
    return "frozen_exe" if getattr(sys, "frozen", False) else "source_tree"


def _default_runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _default_python_command() -> str:
    return Path(sys.executable).name if Path(sys.executable).name else "py"


def _default_host_agent_exe_arg() -> str:
    path = _adjacent_exe("AgentSightHostAgent.exe")
    return str(path) if path else ""


def _default_tray_gui_exe_arg() -> str:
    path = _adjacent_exe("AgentSightTray.exe")
    return str(path) if path else ""


def _frozen_session_supervisor_exe() -> Path:
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "agentsightsupervisor.exe":
        return executable
    candidate = executable.with_name("AgentSightSupervisor.exe")
    return candidate if candidate.exists() else executable


def _adjacent_exe(filename: str) -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().with_name(filename)
    return candidate if candidate.exists() else None


def _quote(value: str) -> str:
    if not value:
        return '""'
    if any(ch.isspace() for ch in value) or any(ch in value for ch in ['"', "&", "(", ")"]):
        return '"' + value.replace('"', '\\"') + '"'
    return value


def _now_ms() -> int:
    return int(time.time() * 1000)


if __name__ == "__main__":
    raise SystemExit(main())
