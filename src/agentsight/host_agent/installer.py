from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from agentsight.host_agent.interactive_task import completed_process_report, run_schtasks
from agentsight.host_agent.server import (
    default_agent_report_file,
    default_discovery_file,
    default_service_state_file,
    default_watchdog_report_file,
    default_watchdog_stop_file,
)

START_METHODS = ["auto", "onlogon_task", "startup_vbs", "interactive_task"]
ONLOGON_TASK_NAME = "AgentSightHostAgentOnLogon"


def main() -> int:
    parser = argparse.ArgumentParser(description="Install, start, check, or remove the AgentSight Host Agent.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install = subcommands.add_parser("install", description="Install the current-user hidden startup launcher.")
    install.add_argument("--repo-root", default=str(_default_repo_root()))
    install.add_argument("--python", default=_default_python_command())
    install.add_argument("--host", default="127.0.0.1")
    install.add_argument("--port", type=int, default=8765)
    install.add_argument("--runs-dir", default="runs_host_agent")
    install.add_argument("--agent-exe")
    install.add_argument("--start-now", action="store_true")
    install.add_argument("--start-method", choices=START_METHODS, default="auto")
    install.add_argument("--wait-seconds", type=float, default=12.0)

    start = subcommands.add_parser("start", description="Start the installed hidden launcher once.")
    start.add_argument("--start-method", choices=START_METHODS, default="auto")
    start.add_argument("--wait-seconds", type=float, default=12.0)

    subcommands.add_parser("status", description="Read discovery and call /health.")
    uninstall = subcommands.add_parser("uninstall", description="Stop the host agent and remove the current-user startup launcher.")
    uninstall.add_argument("--keep-running", action="store_true", help="Remove startup files without asking the running host agent to stop.")
    uninstall.add_argument("--wait-seconds", type=float, default=5.0)

    args = parser.parse_args(_argv_or_frozen_installer_default(sys.argv[1:]))
    if args.command == "install":
        report = install_host_agent(
            repo_root=Path(args.repo_root),
            python_command=args.python,
            host=args.host,
            port=args.port,
            runs_dir=args.runs_dir,
            agent_exe=Path(args.agent_exe) if args.agent_exe else _default_adjacent_agent_exe(),
            start_now=args.start_now,
            start_method=args.start_method,
            wait_seconds=args.wait_seconds,
        )
    elif args.command == "start":
        report = start_installed_host_agent(start_method=args.start_method, wait_seconds=args.wait_seconds)
    elif args.command == "status":
        report = host_agent_status()
    else:
        report = uninstall_host_agent(stop_running=not args.keep_running, wait_seconds=args.wait_seconds)
    _attach_and_write_last_installer_report(report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report.get("exit_code", 0))


def _argv_or_frozen_installer_default(argv: list[str]) -> list[str]:
    if argv:
        return argv
    if getattr(sys, "frozen", False):
        return ["install", "--start-now", "--wait-seconds", "60"]
    return argv


def _attach_and_write_last_installer_report(report: dict[str, Any]) -> None:
    path = _last_installer_report_file()
    report["installer_report_file"] = str(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        report["installer_report_write_error"] = str(exc)


def _last_installer_report_file() -> Path:
    return _paths()["agent_dir"] / "last-installer-report.json"


def install_host_agent(
    *,
    repo_root: Path,
    python_command: str,
    host: str,
    port: int,
    runs_dir: str,
    agent_exe: Path | None,
    start_now: bool,
    wait_seconds: float,
    start_method: str = "auto",
) -> dict[str, Any]:
    paths = _paths()
    paths["agent_dir"].mkdir(parents=True, exist_ok=True)
    paths["startup"].mkdir(parents=True, exist_ok=True)
    watchdog_stop_file_removed = _clear_watchdog_stop_file()
    installed_runs_dir = _installed_runs_dir(runs_dir, agent_dir=paths["agent_dir"])
    command = _agent_command(
        repo_root=repo_root,
        python_command=python_command,
        host=host,
        port=port,
        runs_dir=str(installed_runs_dir),
        agent_exe=agent_exe if agent_exe and agent_exe.exists() else None,
    )
    paths["cmd"].write_text(command, encoding="utf-8")
    paths["vbs"].write_text(_hidden_vbs(paths["cmd"]), encoding="ascii")
    onlogon_task = _install_onlogon_task(paths["cmd"])
    stale_runtime_reports_removed = _cleanup_stale_runtime_reports_when_not_starting(start_now=start_now)
    report: dict[str, Any] = {
        "object_type": "AgentSightHostAgentInstallerReport",
        "schema": "agentsight_host_agent_installer_v1",
        "installer_status": "installed",
        "exit_code": 0,
        "startup_launcher": str(paths["vbs"]),
        "agent_command": str(paths["cmd"]),
        "discovery_file": str(default_discovery_file()),
        "service_state_file": str(default_service_state_file()),
        "repo_root": str(repo_root),
        "python_command": python_command,
        "agent_exe": str(agent_exe) if agent_exe else None,
        "runs_dir": str(installed_runs_dir),
        "install_mode": "agent_exe" if agent_exe and agent_exe.exists() else "source_python",
        "watchdog": {
            "enabled": True,
            "stop_file": str(default_watchdog_stop_file()),
            "stop_file_removed": watchdog_stop_file_removed,
            "report_file": str(default_watchdog_report_file()),
        },
        "start_now": start_now,
        "start_method": start_method,
        "onlogon_task": onlogon_task,
        "stale_runtime_reports_removed": stale_runtime_reports_removed,
    }
    if start_now:
        report["pre_start_stop"] = stop_running_host_agent(wait_seconds=min(wait_seconds, 2.0))
        default_discovery_file().unlink(missing_ok=True)
        report["start_launcher"] = _start_hidden(
            paths["vbs"],
            command_path=paths["cmd"],
            start_method=start_method,
        )
        report["start"] = _wait_for_health(wait_seconds=wait_seconds)
        if report["start"].get("status") not in {"ready", "not_ready"}:
            report["exit_code"] = 5
    return report


def _cleanup_stale_runtime_reports_when_not_starting(*, start_now: bool) -> list[str]:
    if start_now or default_discovery_file().exists():
        return []
    removed: list[str] = []
    for path in (default_agent_report_file(), default_watchdog_report_file(), default_service_state_file()):
        if path.exists():
            path.unlink()
            removed.append(str(path))
    return removed


def start_installed_host_agent(*, wait_seconds: float, start_method: str = "auto") -> dict[str, Any]:
    paths = _paths()
    if not paths["vbs"].exists():
        return {
            "object_type": "AgentSightHostAgentInstallerReport",
            "installer_status": "not_installed",
            "exit_code": 4,
            "startup_launcher": str(paths["vbs"]),
        }
    watchdog_stop_file_removed = _clear_watchdog_stop_file()
    pre_start_stop = stop_running_host_agent(wait_seconds=min(wait_seconds, 2.0))
    default_discovery_file().unlink(missing_ok=True)
    launcher_report = _start_hidden(paths["vbs"], command_path=paths["cmd"], start_method=start_method)
    return {
        "object_type": "AgentSightHostAgentInstallerReport",
        "installer_status": "started",
        "exit_code": 0,
        "startup_launcher": str(paths["vbs"]),
        "start_method": start_method,
        "service_state_file": str(default_service_state_file()),
        "watchdog": {
            "enabled": True,
            "stop_file": str(default_watchdog_stop_file()),
            "stop_file_removed": watchdog_stop_file_removed,
            "report_file": str(default_watchdog_report_file()),
        },
        "pre_start_stop": pre_start_stop,
        "start_launcher": launcher_report,
        "start": _wait_for_health(wait_seconds=wait_seconds),
    }


def host_agent_status() -> dict[str, Any]:
    discovery = _read_discovery()
    if not discovery:
        return {
            "object_type": "AgentSightHostAgentInstallerReport",
            "installer_status": "discovery_missing",
            "exit_code": 4,
            "discovery_file": str(default_discovery_file()),
            "service_state_file": str(default_service_state_file()),
            "service_state": _read_service_state(),
        }
    return {
        "object_type": "AgentSightHostAgentInstallerReport",
        "installer_status": "status",
        "exit_code": 0,
        "discovery": _redact_token(discovery),
        "health": _request_health(discovery),
        "service_state_file": str(default_service_state_file()),
        "service_state": _read_service_state(),
    }


def stop_running_host_agent(*, wait_seconds: float = 5.0) -> dict[str, Any]:
    discovery = _read_discovery()
    if not discovery:
        return {
            "object_type": "AgentSightHostAgentStopReport",
            "shutdown_status": "not_running_discovery_missing",
            "exit_code": 0,
            "discovery_file": str(default_discovery_file()),
            "service_state_file": str(default_service_state_file()),
            "shutdown_requested": False,
            "stopped_confirmed": True,
        }
    shutdown = _request_shutdown(discovery)
    stopped_confirmed = False
    health_after: dict[str, Any] | None = None
    if shutdown.get("status") == 200:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            health_after = _request_health(discovery)
            if health_after.get("request_failed"):
                stopped_confirmed = True
                break
            time.sleep(0.25)
    return {
        "object_type": "AgentSightHostAgentStopReport",
        "shutdown_status": "stopped" if stopped_confirmed else "shutdown_unconfirmed",
        "exit_code": 0 if stopped_confirmed else 5,
        "discovery": _redact_token(discovery),
        "shutdown_request": shutdown,
        "health_after": health_after,
        "shutdown_requested": shutdown.get("status") == 200,
        "stopped_confirmed": stopped_confirmed,
    }


def uninstall_host_agent(*, stop_running: bool = True, wait_seconds: float = 5.0) -> dict[str, Any]:
    paths = _paths()
    watchdog_stop = _request_watchdog_stop()
    onlogon_task = _delete_onlogon_task()
    stop_report = (
        stop_running_host_agent(wait_seconds=wait_seconds)
        if stop_running
        else {
            "object_type": "AgentSightHostAgentStopReport",
            "shutdown_status": "skipped_keep_running",
            "exit_code": 0,
            "shutdown_requested": False,
            "stopped_confirmed": False,
        }
    )
    removed = []
    for key in ("vbs", "cmd"):
        path = paths[key]
        if path.exists():
            path.unlink()
            removed.append(str(path))
    discovery_file = default_discovery_file()
    if stop_report.get("exit_code") == 0 and discovery_file.exists():
        discovery_file.unlink()
        removed.append(str(discovery_file))
    exit_code = int(stop_report.get("exit_code") or 0)
    return {
        "object_type": "AgentSightHostAgentInstallerReport",
        "installer_status": "uninstalled" if exit_code == 0 else "uninstalled_but_agent_stop_unconfirmed",
        "exit_code": exit_code,
        "removed": removed,
        "startup_launcher": str(paths["vbs"]),
        "agent_command": str(paths["cmd"]),
        "discovery_file": str(discovery_file),
        "service_state_file": str(default_service_state_file()),
        "onlogon_task": onlogon_task,
        "watchdog_stop": watchdog_stop,
        "stop": stop_report,
    }


def _clear_watchdog_stop_file() -> bool:
    path = default_watchdog_stop_file()
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def _request_watchdog_stop() -> dict[str, Any]:
    path = default_watchdog_stop_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "object_type": "AgentSightHostAgentWatchdogStopRequest",
                    "requested_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "reason": "uninstall_host_agent",
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return {"stop_requested": True, "stop_file": str(path)}
    except Exception as exc:
        return {"stop_requested": False, "stop_file": str(path), "error": str(exc)}


def _wait_for_health(*, wait_seconds: float) -> dict[str, Any]:
    deadline = time.time() + wait_seconds
    discovery = None
    while time.time() < deadline:
        discovery = _read_discovery()
        if discovery:
            health = _request_health(discovery)
            return {
                "status": "ready" if health.get("can_attempt_real_control") else "not_ready",
                "discovery": _redact_token(discovery),
                "health": health,
            }
        time.sleep(0.25)
    return {
        "status": "discovery_timeout",
        "discovery_file": str(default_discovery_file()),
    }


def _request_health(discovery: dict[str, Any]) -> dict[str, Any]:
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


def _request_shutdown(discovery: dict[str, Any]) -> dict[str, Any]:
    url = str(discovery.get("shutdown_url") or f"{str(discovery.get('url', '')).rstrip('/')}/shutdown")
    request = urllib.request.Request(url, data=b"{}", method="POST")
    request.add_header("accept", "application/json")
    request.add_header("content-type", "application/json")
    request.add_header("authorization", f"Bearer {discovery.get('token', '')}")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return {
                "request_failed": False,
                "status": response.status,
                "body": json.loads(response.read().decode("utf-8")),
            }
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", errors="replace"))
        except Exception:
            body = {"error": str(exc)}
        finally:
            exc.close()
        return {"request_failed": True, "status": exc.code, "body": body}
    except Exception as exc:
        return {"request_failed": True, "status": None, "error": str(exc)}


def _read_discovery() -> dict[str, Any] | None:
    path = default_discovery_file()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_service_state() -> dict[str, Any] | None:
    path = default_service_state_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _redact_token(discovery: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in discovery.items() if key != "token"}


def _paths() -> dict[str, Path]:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        agent_dir = Path(local_appdata) / "AgentSight"
    else:
        agent_dir = Path.home() / ".agentsight"
    startup = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return {
        "agent_dir": agent_dir,
        "startup": startup,
        "cmd": agent_dir / "AgentSightHostAgent.cmd",
        "vbs": startup / "AgentSightHostAgent.vbs",
    }


def _installed_runs_dir(runs_dir: str, *, agent_dir: Path) -> Path:
    path = Path(runs_dir)
    if path.is_absolute():
        return path
    return agent_dir / path


def _agent_command(
    *,
    repo_root: Path,
    python_command: str,
    host: str,
    port: int,
    runs_dir: str,
    agent_exe: Path | None,
) -> str:
    if agent_exe:
        launch_line = (
            f'"{agent_exe}" --watchdog --host "{host}" --port {port} '
            f'--runs-dir "{runs_dir}" --arm-real-input'
        )
        return "\r\n".join(["@echo off", "chcp 65001 >nul", launch_line, ""])
    return "\r\n".join(
        [
            "@echo off",
            "chcp 65001 >nul",
            f'cd /d "{repo_root}"',
            "set PYTHONPATH=src",
            (
                f'"{python_command}" -m agentsight.host_agent.server --watchdog '
                f'--host "{host}" --port {port} --runs-dir "{runs_dir}" --arm-real-input'
            ),
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


def _start_hidden(vbs_path: Path, *, command_path: Path | None = None, start_method: str = "auto") -> dict[str, Any]:
    if start_method in {"auto", "onlogon_task"}:
        task_report = _run_onlogon_task()
        if task_report.get("started") or start_method == "onlogon_task":
            return task_report
    if start_method in {"auto", "startup_vbs"}:
        vbs_report = _start_via_startup_vbs(vbs_path)
        if vbs_report.get("started") or start_method == "startup_vbs":
            return vbs_report
    if start_method in {"auto", "interactive_task"}:
        task_report = _start_via_interactive_task(command_path or vbs_path, startup_vbs_path=vbs_path)
        if task_report.get("started") or start_method == "interactive_task":
            return task_report
    return _start_via_startup_vbs(vbs_path)


def _start_via_startup_vbs(vbs_path: Path) -> dict[str, Any]:
    try:
        os.startfile(str(vbs_path))  # type: ignore[attr-defined]
        return {
            "start_method_used": "startup_vbs",
            "started": True,
            "startup_launcher": str(vbs_path),
        }
    except Exception as exc:
        return {
            "start_method_used": "startup_vbs",
            "started": False,
            "startup_launcher": str(vbs_path),
            "error": str(exc),
        }


def _install_onlogon_task(command_path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "task_name": ONLOGON_TASK_NAME,
            "install_status": "skipped_non_windows",
            "task_launcher": str(command_path),
        }
    create = run_schtasks(
        [
            "/Create",
            "/TN",
            ONLOGON_TASK_NAME,
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
        "task_name": ONLOGON_TASK_NAME,
        "install_status": "installed" if create.returncode == 0 else "install_failed",
        "task_launcher": str(command_path),
        "schedule": "ONLOGON",
        "run_level": "LIMITED",
        "create": completed_process_report(create),
    }


def _run_onlogon_task() -> dict[str, Any]:
    if os.name != "nt":
        return {
            "start_method_used": "onlogon_task",
            "started": False,
            "task_name": ONLOGON_TASK_NAME,
            "error": "onlogon_task_start_requires_windows",
        }
    run = run_schtasks(["/Run", "/TN", ONLOGON_TASK_NAME])
    return {
        "start_method_used": "onlogon_task",
        "started": run.returncode == 0,
        "task_name": ONLOGON_TASK_NAME,
        "run": completed_process_report(run),
    }


def _delete_onlogon_task() -> dict[str, Any]:
    if os.name != "nt":
        return {
            "task_name": ONLOGON_TASK_NAME,
            "delete_status": "skipped_non_windows",
        }
    delete = run_schtasks(["/Delete", "/TN", ONLOGON_TASK_NAME, "/F"])
    return {
        "task_name": ONLOGON_TASK_NAME,
        "delete_status": "deleted" if delete.returncode == 0 else "delete_failed_or_missing",
        "delete": completed_process_report(delete),
    }


def _start_via_interactive_task(task_path: Path, *, startup_vbs_path: Path) -> dict[str, Any]:
    if os.name != "nt":
        return {
            "start_method_used": "interactive_task",
            "started": False,
            "startup_launcher": str(startup_vbs_path),
            "task_launcher": str(task_path),
            "error": "interactive_task_start_requires_windows",
        }
    task_name = "AgentSightHostAgentInteractiveLaunch"
    action = str(task_path)
    create = run_schtasks(
        [
            "/Create",
            "/TN",
            task_name,
            "/TR",
            action,
            "/SC",
            "ONCE",
            "/ST",
            "23:59",
            "/F",
            "/IT",
        ]
    )
    if create.returncode != 0:
        return {
            "start_method_used": "interactive_task",
            "started": False,
            "startup_launcher": str(startup_vbs_path),
            "task_launcher": str(task_path),
            "task_name": task_name,
            "create": completed_process_report(create),
        }
    run = run_schtasks(["/Run", "/TN", task_name])
    time.sleep(0.25)
    delete = run_schtasks(["/Delete", "/TN", task_name, "/F"])
    return {
        "start_method_used": "interactive_task",
        "started": run.returncode == 0,
        "startup_launcher": str(startup_vbs_path),
        "task_launcher": str(task_path),
        "task_name": task_name,
        "create": completed_process_report(create),
        "run": completed_process_report(run),
        "delete": completed_process_report(delete),
    }


def _default_repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _default_python_command() -> str:
    executable = Path(sys.executable)
    if executable.name.lower().startswith("python"):
        return str(executable)
    return "py"


def _default_adjacent_agent_exe() -> Path | None:
    candidate = Path(sys.executable).with_name("AgentSightHostAgent.exe")
    return candidate if candidate.exists() else None


if __name__ == "__main__":
    raise SystemExit(main())
