from __future__ import annotations

import argparse
import ctypes
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from subprocess import DEVNULL, Popen
from typing import Any

from agentsight.host_agent.interactive_task import completed_process_report, run_process
from agentsight.session_supervisor import (
    SESSION_SUPERVISOR_SCHEMA,
    main as session_supervisor_main,
    session_supervisor_status,
    uninstall_session_supervisor,
)
from agentsight.tray.state import (
    boundary_facts,
    default_operator_control_policy,
    default_operator_control_policy_file,
    default_recording_policy,
    default_tray_config_file,
)


INSTALLER_SCHEMA = "agentsight_setup_installer_v1"
AGENTSIGHT_VERSION = "1.0.0"
PAYLOAD_DIR_NAME = "agentsight_payload"
MCP_PUBLIC_EXE_NAME = "AgentSightMcp.exe"
MCP_SERVER_EXE_NAME = "AgentSightMcpServer.exe"
PAYLOAD_EXE_NAMES = [
    "AgentSightSupervisor.exe",
    "AgentSightHostAgent.exe",
    "AgentSightTray.exe",
    "AgentSightTrayCli.exe",
    "AgentSightTimelineViewer.exe",
    MCP_SERVER_EXE_NAME,
    "AgentSightHostAgentScenarios.exe",
    "AgentSightHostAgentInstaller.exe",
]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if _should_delegate_to_session_supervisor(args):
        return session_supervisor_main(args or ["install", "--start-now", "--arm-real-input"])

    parser = _build_parser()
    parsed = parser.parse_args(args or ["install"])
    if parsed.command == "install":
        report = install_agentsight(
            payload_dir=Path(parsed.payload_dir) if parsed.payload_dir else None,
            version=parsed.version,
            host=parsed.host,
            port=int(parsed.port),
            runs_dir=parsed.runs_dir,
            agent_exe=Path(parsed.agent_exe) if parsed.agent_exe else None,
            tray_gui_exe=Path(parsed.tray_gui_exe) if parsed.tray_gui_exe else None,
            start_method=parsed.start_method,
            start_now=bool(parsed.start_now),
            arm_real_input=bool(parsed.arm_real_input),
            wait_seconds=float(parsed.wait_seconds),
            show_prompt=not bool(parsed.no_gui),
            output=parsed.output,
        )
        _write_json_report(report, parsed.output)
        _print_human_summary(report)
        return int(report.get("exit_code", 0))
    if parsed.command == "status":
        report = product_status(output=parsed.output)
        _write_json_report(report, parsed.output)
        return int(report.get("exit_code", 0))
    if parsed.command == "uninstall":
        report = uninstall_agentsight(keep_running=bool(parsed.keep_running), output=parsed.output)
        _write_json_report(report, parsed.output)
        return int(report.get("exit_code", 0))
    if parsed.command == "prompt":
        text = _copy_prompt(_default_install_root())
        print(text)
        return 0
    parser.print_help()
    return 2


def _should_delegate_to_session_supervisor(args: list[str]) -> bool:
    if getattr(sys, "frozen", False):
        return False
    if args and args[0] in {"run", "start", "stop"}:
        return True
    return False


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install AgentSight for Windows into the current user's local app directory.")
    subcommands = parser.add_subparsers(dest="command", required=True)

    install = subcommands.add_parser("install", description="Self-extract AgentSight and register current-user startup.")
    install.add_argument("--payload-dir", default=None, help="Developer/test override for the payload directory.")
    install.add_argument("--version", default=AGENTSIGHT_VERSION)
    install.add_argument("--host", default="127.0.0.1")
    install.add_argument("--port", type=int, default=8765)
    install.add_argument("--runs-dir", default="runs_host_agent")
    install.add_argument("--agent-exe", default=None, help="Optional packaged Host Agent exe override.")
    install.add_argument("--tray-gui-exe", default=None, help="Optional packaged Tray GUI exe override.")
    install.add_argument("--start-method", choices=["auto", "run_key", "onlogon_task", "startup_vbs"], default="auto")
    install.set_defaults(start_now=True, arm_real_input=True)
    install.add_argument("--start-now", dest="start_now", action="store_true", help="Start AgentSight after installing (default).")
    install.add_argument("--no-start-now", dest="start_now", action="store_false")
    install.add_argument("--arm-real-input", dest="arm_real_input", action="store_true", help="Allow the installed Host Agent to attempt real input when ready (default).")
    install.add_argument("--no-arm-real-input", dest="arm_real_input", action="store_false")
    install.add_argument("--wait-seconds", type=float, default=60.0)
    install.add_argument("--no-gui", action="store_true", help="Do not show the final MessageBox prompt.")
    install.add_argument("--output", default=None)

    status = subcommands.add_parser("status", description="Read AgentSight install/runtime status.")
    status.add_argument("--output", default=None)

    uninstall = subcommands.add_parser("uninstall", description="Remove current-user startup and stop AgentSight; evidence is kept.")
    uninstall.add_argument("--keep-running", action="store_true")
    uninstall.add_argument("--output", default=None)

    subcommands.add_parser("prompt", description="Print the prompt the user can copy to an AI client.")
    return parser


def install_agentsight(
    *,
    payload_dir: Path | None = None,
    version: str = AGENTSIGHT_VERSION,
    host: str = "127.0.0.1",
    port: int = 8765,
    runs_dir: str = "runs_host_agent",
    agent_exe: Path | None = None,
    tray_gui_exe: Path | None = None,
    start_method: str = "auto",
    start_now: bool = True,
    arm_real_input: bool = True,
    wait_seconds: float = 60.0,
    show_prompt: bool = True,
    output: str | None = None,
) -> dict[str, Any]:
    install_root = _default_install_root()
    app_version_dir = install_root / "app" / version
    current_dir = install_root / "current"
    ai_install_dir = install_root / "ai-install"
    install_root.mkdir(parents=True, exist_ok=True)
    app_version_dir.mkdir(parents=True, exist_ok=True)
    current_dir.mkdir(parents=True, exist_ok=True)

    pre_existing_supervisor = _stop_existing_packaged_supervisor_before_payload_copy(
        current_dir=current_dir,
        wait_seconds=wait_seconds,
    )
    resolved_payload = _payload_source_dir(payload_dir)
    payload_copy = _copy_payload(resolved_payload, app_version_dir=app_version_dir, current_dir=current_dir)
    self_copy = _copy_self_setup(payload_dir=resolved_payload, app_version_dir=app_version_dir, current_dir=current_dir)
    mcp_public_alias = _ensure_mcp_public_exe_alias(app_version_dir=app_version_dir, current_dir=current_dir)
    defaults = _write_default_runtime_files()
    ai_install = _write_ai_install_package(install_root=install_root, ai_install_dir=ai_install_dir, current_dir=current_dir)
    uninstall_entry = _write_uninstall_entry(install_root=install_root, current_dir=current_dir)

    supervisor_install = _install_with_packaged_supervisor(
        current_dir=current_dir,
        host=host,
        port=port,
        runs_dir=runs_dir,
        agent_exe=agent_exe,
        tray_gui_exe=tray_gui_exe,
        start_method=start_method,
        start_now=start_now,
        arm_real_input=arm_real_input,
        wait_seconds=wait_seconds,
    )
    status = product_status(output=None)
    copy_prompt = _copy_prompt(install_root)
    report: dict[str, Any] = {
        "object_type": "AgentSightSetupInstallReport",
        "schema": INSTALLER_SCHEMA,
        "install_status": "installed" if supervisor_install.get("exit_code") in {0, None} else "installed_with_startup_error",
        "version": version,
        "install_root": str(install_root),
        "app_version_dir": str(app_version_dir),
        "current_dir": str(current_dir),
        "ai_install_dir": str(ai_install_dir),
        "payload_source_dir": str(resolved_payload) if resolved_payload else None,
        "pre_existing_supervisor": pre_existing_supervisor,
        "payload_copy": payload_copy,
        "self_copy": self_copy,
        "mcp_public_alias": mcp_public_alias,
        "default_runtime_files": defaults,
        "ai_install": ai_install,
        "uninstall_entry": uninstall_entry,
        "startup": supervisor_install,
        "status": status,
        "copy_prompt": copy_prompt,
        "prompt_shown": False,
        "output": output,
        "network_binding_default": host,
        "public_port_opened": False,
        "registered_startup_components": ["AgentSight"],
        "self_start_entry_is_unified_supervisor": True,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": int(supervisor_install.get("exit_code", 0) or 0),
    }
    report_file = install_root / "last-install-report.json"
    _write_json_path(report_file, report)
    report["install_report_file"] = str(report_file)
    if show_prompt:
        report["prompt_shown"] = _show_install_prompt(copy_prompt)
        _write_json_path(report_file, report)
    return report


def product_status(*, output: str | None = None) -> dict[str, Any]:
    install_root = _default_install_root()
    current_dir = install_root / "current"
    supervisor = current_dir / "AgentSightSupervisor.exe"
    supervisor_status = _run_supervisor_command(["status"], current_dir=current_dir, output=None) if supervisor.exists() else _source_supervisor_status()
    report = {
        "object_type": "AgentSightSetupStatus",
        "schema": INSTALLER_SCHEMA,
        "install_root": str(install_root),
        "current_dir": str(current_dir),
        "ai_install_dir": str(install_root / "ai-install"),
        "installed_payloads": {name: (current_dir / name).exists() for name in ["AgentSightSetup.exe", *PAYLOAD_EXE_NAMES, MCP_PUBLIC_EXE_NAME]},
        "supervisor": supervisor_status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": int(supervisor_status.get("exit_code", 0) or 0) if isinstance(supervisor_status, dict) else 0,
    }
    return report


def uninstall_agentsight(*, keep_running: bool = False, output: str | None = None) -> dict[str, Any]:
    install_root = _default_install_root()
    current_dir = install_root / "current"
    args = ["uninstall"]
    if keep_running:
        args.append("--keep-running")
    supervisor_report = _run_supervisor_command(args, current_dir=current_dir, output=output) if (current_dir / "AgentSightSupervisor.exe").exists() else _source_supervisor_uninstall(keep_running=keep_running)
    report = {
        "object_type": "AgentSightSetupUninstallReport",
        "schema": INSTALLER_SCHEMA,
        "uninstall_status": "startup_removed",
        "install_root": str(install_root),
        "program_files_removed": False,
        "evidence_kept": True,
        "supervisor": supervisor_report,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": int(supervisor_report.get("exit_code", 0) or 0) if isinstance(supervisor_report, dict) else 0,
    }
    report_file = install_root / "last-uninstall-report.json"
    _write_json_path(report_file, report)
    report["uninstall_report_file"] = str(report_file)
    return report


def _payload_source_dir(payload_dir: Path | None) -> Path | None:
    candidates: list[Path] = []
    if payload_dir:
        candidates.append(payload_dir)
    if getattr(sys, "frozen", False):
        bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        candidates.append(bundle_root / PAYLOAD_DIR_NAME)
        candidates.append(Path(sys.executable).resolve().parent)
    source_root = Path(__file__).resolve().parents[2]
    candidates.append(source_root / "dist")
    for candidate in candidates:
        if candidate.exists() and any((candidate / name).exists() for name in PAYLOAD_EXE_NAMES):
            return candidate
    return None


def _copy_payload(payload_dir: Path | None, *, app_version_dir: Path, current_dir: Path) -> dict[str, Any]:
    copied: list[dict[str, str]] = []
    missing: list[str] = []
    if payload_dir is None:
        return {"copy_status": "payload_missing", "copied": copied, "missing": PAYLOAD_EXE_NAMES}
    for name in PAYLOAD_EXE_NAMES:
        source = payload_dir / name
        if not source.exists():
            missing.append(name)
            continue
        app_target = app_version_dir / name
        current_target = current_dir / name
        _copy_file(source, app_target)
        _copy_file(app_target, current_target)
        copied.append({"name": name, "app_target": str(app_target), "current_target": str(current_target)})
    status = "copied" if copied and not missing else ("partial" if copied else "payload_missing")
    return {"copy_status": status, "payload_dir": str(payload_dir), "copied": copied, "missing": missing}


def _copy_self_setup(*, payload_dir: Path | None, app_version_dir: Path, current_dir: Path) -> dict[str, Any]:
    payload_setup = payload_dir / "AgentSightSetup.exe" if payload_dir else None
    if payload_setup and payload_setup.exists():
        source = payload_setup.resolve()
    else:
        source = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve()
    targets = [app_version_dir / "AgentSightSetup.exe", current_dir / "AgentSightSetup.exe"]
    copied = []
    for target in targets:
        _copy_file(source, target)
        copied.append(str(target))
    return {"source": str(source), "copied_to": copied}


def _ensure_mcp_public_exe_alias(*, app_version_dir: Path, current_dir: Path) -> dict[str, Any]:
    """Install the concise public MCP exe name expected by ai-install/mcp.json."""
    source = current_dir / MCP_SERVER_EXE_NAME
    if not source.exists():
        return {
            "status": "source_missing",
            "source": str(source),
            "alias_name": MCP_PUBLIC_EXE_NAME,
            "copied_to": [],
        }
    copied = []
    for target in [app_version_dir / MCP_PUBLIC_EXE_NAME, current_dir / MCP_PUBLIC_EXE_NAME]:
        _copy_file(source, target)
        copied.append(str(target))
    return {
        "status": "written",
        "source": str(source),
        "alias_name": MCP_PUBLIC_EXE_NAME,
        "copied_to": copied,
    }


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.resolve(strict=False) == target.resolve(strict=False):
            return
    except OSError:
        pass
    shutil.copy2(source, target)


def _stop_existing_packaged_supervisor_before_payload_copy(*, current_dir: Path, wait_seconds: float) -> dict[str, Any]:
    supervisor_exe = current_dir / "AgentSightSupervisor.exe"
    if not supervisor_exe.exists():
        return {
            "stop_attempted": False,
            "reason": "packaged_supervisor_not_installed",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    status = _run_supervisor_command(["status"], current_dir=current_dir, output=None)
    if not _packaged_supervisor_status_running(status, current_dir=current_dir):
        cleanup = _cleanup_reported_payload_processes(
            current_dir=current_dir,
            reports=[status],
            reason="stale_packaged_supervisor_children_before_payload_copy",
        )
        return {
            "stop_attempted": False,
            "reason": "packaged_supervisor_not_running_from_current_dir",
            "status_before_copy": status,
            "process_cleanup": cleanup,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    stop_wait_seconds = max(1.0, min(float(wait_seconds or 0.0), 30.0))
    stop = _run_supervisor_command(
        ["stop", "--wait-seconds", str(stop_wait_seconds)],
        current_dir=current_dir,
        output=None,
    )
    status_after_stop = _run_supervisor_command(["status"], current_dir=current_dir, output=None)
    cleanup = _cleanup_reported_payload_processes(
        current_dir=current_dir,
        reports=[status, status_after_stop],
        reason="packaged_supervisor_stopped_before_payload_copy",
    )
    return {
        "stop_attempted": True,
        "reason": "packaged_supervisor_was_running_from_current_dir",
        "status_before_copy": status,
        "stop": stop,
        "status_after_stop": status_after_stop,
        "process_cleanup": cleanup,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _cleanup_reported_payload_processes(*, current_dir: Path, reports: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    pids: list[int] = []
    for report in reports:
        payload = report.get("json") if isinstance(report, dict) else None
        if not isinstance(payload, dict):
            continue
        state = payload.get("state") if isinstance(payload.get("state"), dict) else payload
        for section_name in ["host_agent", "tray_gui"]:
            section = state.get(section_name) if isinstance(state.get(section_name), dict) else {}
            pid = _safe_positive_int(section.get("last_child_pid"))
            if pid is not None:
                pids.append(pid)
        supervisor_identity = state.get("process_identity") if isinstance(state.get("process_identity"), dict) else {}
        supervisor_pid = _safe_positive_int(state.get("supervisor_pid"))
        if supervisor_pid is not None and _path_matches_current_dir(supervisor_identity.get("executable"), current_dir=current_dir):
            pids.append(supervisor_pid)
        single_instance = payload.get("single_instance") if isinstance(payload.get("single_instance"), dict) else state.get("single_instance")
        if isinstance(single_instance, dict):
            owner_identity = single_instance.get("owner_process_identity") if isinstance(single_instance.get("owner_process_identity"), dict) else {}
            owner_pid = _safe_positive_int(single_instance.get("owner_pid"))
            if owner_pid is not None and _path_matches_current_dir(owner_identity.get("executable"), current_dir=current_dir):
                pids.append(owner_pid)
    pids.extend(_find_current_dir_payload_process_ids(current_dir=current_dir))
    seen: set[int] = set()
    terminated = []
    for pid in pids:
        if pid in seen or pid == os.getpid():
            continue
        seen.add(pid)
        if not _process_running(pid):
            terminated.append({"pid": pid, "status": "already_exited"})
            continue
        force = _terminate_process_for_upgrade(pid, reason=reason)
        terminated.append(force)
    return {
        "attempted": bool(terminated),
        "reason": reason,
        "pids": sorted(seen),
        "terminated": terminated,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _find_current_dir_payload_process_ids(*, current_dir: Path) -> list[int]:
    if os.name != "nt":
        return []
    try:
        return _find_current_dir_payload_process_ids_winapi(current_dir=current_dir)
    except Exception:
        return []


def _find_current_dir_payload_process_ids_winapi(*, current_dir: Path) -> list[int]:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    MAX_PATH = 260
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ProcessID", ctypes.c_ulong),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", ctypes.c_ulong),
            ("cntThreads", ctypes.c_ulong),
            ("th32ParentProcessID", ctypes.c_ulong),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
            ("szExeFile", ctypes.c_wchar * MAX_PATH),
        ]

    kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = ctypes.c_bool
    kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = ctypes.c_bool

    expected_names = {"AgentSightSetup.exe", *PAYLOAD_EXE_NAMES}
    expected_names_lower = {name.lower() for name in expected_names}
    current_dir_resolved = current_dir.resolve(strict=False)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        return []
    pids: list[int] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            pid = int(entry.th32ProcessID)
            image_name = str(entry.szExeFile or "")
            if pid != os.getpid() and image_name.lower() in expected_names_lower:
                executable = _query_process_image_path(pid, access=PROCESS_QUERY_LIMITED_INFORMATION)
                if executable and _path_matches_current_dir(executable, current_dir=current_dir_resolved):
                    pids.append(pid)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return pids


def _query_process_image_path(pid: int, *, access: int) -> str | None:
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_ulong),
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.OpenProcess(access, False, int(pid))
    if not handle:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _safe_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _path_matches_current_dir(path: Any, *, current_dir: Path) -> bool:
    if not path:
        return False
    try:
        return Path(str(path)).resolve(strict=False).parent == current_dir.resolve(strict=False)
    except OSError:
        return False


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(0x1000, False, int(pid))
            if not handle:
                return False
            try:
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_process_for_upgrade(pid: int, *, reason: str) -> dict[str, Any]:
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"pid": pid, "force_attempted": True, "force_status": "already_exited", "reason": reason}
    except Exception as exc:
        return {"pid": pid, "force_attempted": True, "force_status": "failed", "reason": reason, "error": str(exc)}
    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not _process_running(pid):
            return {"pid": pid, "force_attempted": True, "force_status": "terminated", "reason": reason}
        time.sleep(0.1)
    return {"pid": pid, "force_attempted": True, "force_status": "signal_sent_still_running", "reason": reason}


def _write_default_runtime_files() -> dict[str, Any]:
    writes: list[str] = []
    tray_config = default_tray_config_file()
    operator_policy = default_operator_control_policy_file()
    if not tray_config.exists():
        _write_json_path(tray_config, default_recording_policy())
        writes.append(str(tray_config))
    if not operator_policy.exists():
        _write_json_path(operator_policy, default_operator_control_policy())
        writes.append(str(operator_policy))
    return {"written": writes, "tray_config": str(tray_config), "operator_control_policy": str(operator_policy)}


def _write_ai_install_package(*, install_root: Path, ai_install_dir: Path, current_dir: Path) -> dict[str, Any]:
    skill_dir = ai_install_dir / "agentsight"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_source = _skill_source_path()
    if skill_source and skill_source.exists():
        skill_text = skill_source.read_text(encoding="utf-8")
    else:
        skill_text = "# AgentSight for Windows\n\nUse only screen, look, and do. Do not use OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, or shell as a GUI substitute.\n"
    skill_target = ai_install_dir / "SKILL.md"
    skill_nested_target = skill_dir / "SKILL.md"
    for path in [skill_target, skill_nested_target]:
        path.write_text(skill_text, encoding="utf-8")

    mcp_config = {
        "mcpServers": {
            "agentsight": {
                "command": str(current_dir / MCP_PUBLIC_EXE_NAME),
                "args": [],
                "env": {},
            }
        },
        "notes": [
            "AgentSight MCP public tools are only screen, look, and do.",
            "No token is stored in this MCP config; the MCP server reads local AgentSight discovery at runtime.",
            "Default Host Agent binding is 127.0.0.1, not a public network interface.",
        ],
    }
    mcp_path = ai_install_dir / "mcp.json"
    legacy_mcp_path = ai_install_dir / "mcp.config.example.json"
    mcp_text = json.dumps(mcp_config, ensure_ascii=False, indent=2) + "\n"
    for path in [mcp_path, legacy_mcp_path]:
        path.write_text(mcp_text, encoding="utf-8")

    prompt_path = ai_install_dir / "AGENTSIGHT_AI_INSTALL_PROMPT.txt"
    legacy_prompt_path = ai_install_dir / "PROMPT_FOR_AI.md"
    prompt_text = _copy_prompt(install_root) + "\n"
    for path in [prompt_path, legacy_prompt_path]:
        path.write_text(prompt_text, encoding="utf-8")
    readme_path = ai_install_dir / "README_FOR_AI.md"
    legacy_readme_path = ai_install_dir / "README.md"
    readme_text = _ai_install_readme(install_root, current_dir)
    for path in [readme_path, legacy_readme_path]:
        path.write_text(readme_text, encoding="utf-8")
    return {
        "status": "written",
        "prompt": str(prompt_path),
        "legacy_prompt": str(legacy_prompt_path),
        "mcp_config": str(mcp_path),
        "legacy_mcp_config": str(legacy_mcp_path),
        "skill": str(skill_target),
        "nested_skill": str(skill_nested_target),
        "readme": str(readme_path),
        "legacy_readme": str(legacy_readme_path),
    }


def _skill_source_path() -> Path | None:
    if getattr(sys, "frozen", False):
        bundled = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)) / PAYLOAD_DIR_NAME / "SKILL.md"
        if bundled.exists():
            return bundled
    source = Path(__file__).resolve().parent / "adapters" / "skill" / "SKILL.md"
    return source if source.exists() else None


def _write_uninstall_entry(*, install_root: Path, current_dir: Path) -> dict[str, Any]:
    uninstall_dir = install_root / "uninstall"
    uninstall_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "cd /d \"%~dp0..\\current\"",
        "\"AgentSightSetup.exe\" uninstall",
        "pause",
        "",
    ]
    paths = [uninstall_dir / "UNINSTALL_AGENTSIGHT.cmd", current_dir / "UNINSTALL_AGENTSIGHT.cmd"]
    for path in paths:
        path.write_text("\r\n".join(lines), encoding="utf-8")
    return {"status": "written", "paths": [str(path) for path in paths]}


def _install_with_packaged_supervisor(
    *,
    current_dir: Path,
    host: str,
    port: int,
    runs_dir: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    start_method: str,
    start_now: bool,
    arm_real_input: bool,
    wait_seconds: float,
) -> dict[str, Any]:
    supervisor_exe = current_dir / "AgentSightSupervisor.exe"
    if not supervisor_exe.exists():
        return _source_supervisor_install(
            host=host,
            port=port,
            runs_dir=runs_dir,
            agent_exe=agent_exe,
            tray_gui_exe=tray_gui_exe,
            start_method=start_method,
            start_now=start_now,
            arm_real_input=arm_real_input,
            wait_seconds=wait_seconds,
        )
    command = [
        str(supervisor_exe),
        "install",
        "--host",
        host,
        "--port",
        str(port),
        "--runs-dir",
        runs_dir,
        "--repo-root",
        str(current_dir),
        "--agent-exe",
        str(agent_exe or (current_dir / "AgentSightHostAgent.exe")),
        "--tray-gui-exe",
        str(tray_gui_exe or (current_dir / "AgentSightTray.exe")),
        "--start-method",
        start_method,
        "--wait-seconds",
        str(wait_seconds),
    ]
    if arm_real_input:
        command.append("--arm-real-input")
    install_report = _run_packaged_command(command, cwd=current_dir)
    if not start_now:
        return install_report
    if int(install_report.get("exit_code", 0) or 0) != 0:
        return install_report
    start_report = _start_packaged_supervisor_detached(
        current_dir=current_dir,
        host=host,
        port=port,
        runs_dir=runs_dir,
        agent_exe=agent_exe or (current_dir / "AgentSightHostAgent.exe"),
        tray_gui_exe=tray_gui_exe or (current_dir / "AgentSightTray.exe"),
        arm_real_input=arm_real_input,
        wait_seconds=wait_seconds,
    )
    install_report["start_after_install"] = start_report
    start_exit = int(start_report.get("exit_code", 0) or 0) if isinstance(start_report, dict) else 5
    if start_exit != 0:
        install_report["exit_code"] = start_exit
    return install_report


def _start_packaged_supervisor_detached(
    *,
    current_dir: Path,
    host: str,
    port: int,
    runs_dir: str,
    agent_exe: Path,
    tray_gui_exe: Path,
    arm_real_input: bool,
    wait_seconds: float,
) -> dict[str, Any]:
    start_ms = _now_ms()
    before_status = _run_supervisor_command(["status"], current_dir=current_dir, output=None)
    if _packaged_supervisor_status_running(before_status, current_dir=current_dir):
        return {
            "object_type": "AgentSightPackagedSupervisorStartReport",
            "start_status": "already_running",
            "launch_strategy": "status_before_start",
            "status_before_start": before_status,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
            "exit_code": 0,
        }
    launch = _launch_packaged_supervisor_run(
        current_dir=current_dir,
        host=host,
        port=port,
        runs_dir=runs_dir,
        agent_exe=agent_exe,
        tray_gui_exe=tray_gui_exe,
        arm_real_input=arm_real_input,
    )
    state_seen = _wait_for_packaged_supervisor_state(current_dir=current_dir, start_ms=start_ms, wait_seconds=wait_seconds)
    after_status = _run_supervisor_command(["status"], current_dir=current_dir, output=None)
    running_after_start = _packaged_supervisor_status_running(after_status, current_dir=current_dir)
    return {
        "object_type": "AgentSightPackagedSupervisorStartReport",
        "start_status": "started" if running_after_start or state_seen else "start_attempted",
        "launch_strategy": "detached_run_loop",
        "launcher": launch,
        "state_seen_after_wait": state_seen,
        "status_after_start": after_status,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "exit_code": 0 if running_after_start or state_seen else 5,
    }


def _launch_packaged_supervisor_run(
    *,
    current_dir: Path,
    host: str,
    port: int,
    runs_dir: str,
    agent_exe: Path,
    tray_gui_exe: Path,
    arm_real_input: bool,
) -> dict[str, Any]:
    supervisor_exe = current_dir / "AgentSightSupervisor.exe"
    command = [
        str(supervisor_exe),
        "run",
        "--host",
        host,
        "--port",
        str(port),
        "--runs-dir",
        runs_dir,
        "--repo-root",
        str(current_dir),
        "--python",
        supervisor_exe.name,
        "--agent-exe",
        str(agent_exe),
        "--tray-gui-exe",
        str(tray_gui_exe),
    ]
    if arm_real_input:
        command.append("--arm-real-input")
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        process = Popen(
            command,
            cwd=str(current_dir),
            stdin=DEVNULL,
            stdout=DEVNULL,
            stderr=DEVNULL,
            creationflags=creationflags,
        )
    except Exception as exc:
        return {
            "start_method_used": "detached_run_loop",
            "started": False,
            "command": command,
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
    return {
        "start_method_used": "detached_run_loop",
        "started": True,
        "command": command,
        "pid": process.pid,
        "launcher_pid": os.getpid(),
        "launcher_parent_pid": os.getppid(),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _wait_for_packaged_supervisor_state(*, current_dir: Path, start_ms: int, wait_seconds: float) -> bool:
    state_file = current_dir.parent / "session-supervisor-state.json"
    deadline = time.time() + max(0.0, float(wait_seconds or 0.0))
    while time.time() <= deadline:
        state = _read_json_path(state_file)
        if (
            isinstance(state, dict)
            and state.get("supervisor_status") == "running"
            and int(state.get("updated_at_ms") or 0) >= start_ms
            and _state_executable_matches_current_supervisor(state, current_dir=current_dir)
        ):
            return True
        time.sleep(0.25)
    return False


def _packaged_supervisor_status_running(report: dict[str, Any], *, current_dir: Path | None = None) -> bool:
    payload = report.get("json") if isinstance(report, dict) else None
    if not isinstance(payload, dict):
        return False
    state = payload.get("state")
    state_matches = current_dir is None or (isinstance(state, dict) and _state_executable_matches_current_supervisor(state, current_dir=current_dir))
    if isinstance(state, dict) and state.get("supervisor_status") == "running" and state_matches:
        return True
    single_instance = payload.get("single_instance")
    if not (isinstance(single_instance, dict) and bool(single_instance.get("active")) and single_instance.get("lock_status") == "active"):
        return False
    if current_dir is None:
        return True
    owner_identity = single_instance.get("owner_process_identity")
    return isinstance(owner_identity, dict) and _executable_matches_current_supervisor(owner_identity.get("executable"), current_dir=current_dir)


def _state_executable_matches_current_supervisor(state: dict[str, Any], *, current_dir: Path) -> bool:
    identity = state.get("process_identity")
    if not isinstance(identity, dict):
        return False
    return _executable_matches_current_supervisor(identity.get("executable"), current_dir=current_dir)


def _executable_matches_current_supervisor(executable: Any, *, current_dir: Path) -> bool:
    if not isinstance(executable, str) or not executable:
        return False
    try:
        return Path(executable).resolve(strict=False) == (current_dir / "AgentSightSupervisor.exe").resolve(strict=False)
    except OSError:
        return str(executable).lower() == str(current_dir / "AgentSightSupervisor.exe").lower()


def _run_supervisor_command(args: list[str], *, current_dir: Path, output: str | None) -> dict[str, Any]:
    supervisor_exe = current_dir / "AgentSightSupervisor.exe"
    command = [str(supervisor_exe), *args]
    if output and "--output" not in command:
        command.extend(["--output", output])
    return _run_packaged_command(command, cwd=current_dir)


def _run_packaged_command(command: list[str], *, cwd: Path) -> dict[str, Any]:
    try:
        completed = run_process(command, cwd=str(cwd), text=True, capture_output=True, timeout=180, check=False)
    except Exception as exc:
        return {
            "object_type": "AgentSightPackagedCommandReport",
            "command": command,
            "cwd": str(cwd),
            "run_status": "failed_to_start",
            "error": str(exc),
            "exit_code": 5,
        }
    report: dict[str, Any] = {
        "object_type": "AgentSightPackagedCommandReport",
        "command": command,
        "cwd": str(cwd),
        "run_status": "completed",
        "completed": completed_process_report(completed),
        "exit_code": int(completed.returncode),
    }
    parsed = _try_parse_json(completed.stdout)
    if isinstance(parsed, dict):
        report["json"] = parsed
    return report


def _source_supervisor_install(
    *,
    host: str,
    port: int,
    runs_dir: str,
    agent_exe: Path | None,
    tray_gui_exe: Path | None,
    start_method: str,
    start_now: bool,
    arm_real_input: bool,
    wait_seconds: float,
) -> dict[str, Any]:
    args = [
        "install",
        "--host",
        host,
        "--port",
        str(port),
        "--runs-dir",
        runs_dir,
        "--repo-root",
        str(Path(__file__).resolve().parents[2]),
        "--start-method",
        start_method,
        "--wait-seconds",
        str(wait_seconds),
    ]
    if agent_exe:
        args.extend(["--agent-exe", str(agent_exe)])
    if tray_gui_exe:
        args.extend(["--tray-gui-exe", str(tray_gui_exe)])
    if arm_real_input:
        args.append("--arm-real-input")
    if start_now:
        args.append("--start-now")
    exit_code = session_supervisor_main(args)
    return {"object_type": "AgentSightSourceSupervisorInstallReport", "schema": SESSION_SUPERVISOR_SCHEMA, "exit_code": exit_code}


def _source_supervisor_status() -> dict[str, Any]:
    report = session_supervisor_status()
    report["source_mode_fallback"] = True
    return report


def _source_supervisor_uninstall(*, keep_running: bool) -> dict[str, Any]:
    report = uninstall_session_supervisor(stop_running=not keep_running)
    report["source_mode_fallback"] = True
    return report


def _try_parse_json(text: str) -> Any:
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _read_json_path(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _copy_prompt(install_root: Path) -> str:
    return (
        "AgentSight for Windows 已安装在本机。请读取下面目录中的 mcp.json、SKILL.md 和 README_FOR_AI.md，"
        "然后按你当前 AI 客户端的方式把 AgentSight 注册为本地 stdio MCP server。\n"
        "接入后只使用 screen、look、do 三个工具，通过真实屏幕像素观察，用鼠标键盘行动，并保留证据记录。\n"
        "不要写入或要求我提供 token；mcp.json 不含 token，MCP server 会在本机运行时读取 AgentSight discovery。\n"
        "不要使用 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API 或 shell 作为 GUI 替代。\n\n"
        f"安装资料目录：{install_root / 'ai-install'}\n"
        "请先读：README_FOR_AI.md"
    )


def _ai_install_readme(install_root: Path, current_dir: Path) -> str:
    return "\n".join(
        [
            "# AgentSight AI install package",
            "",
            f"安装目录：`{install_root}`",
            f"当前程序目录：`{current_dir}`",
            "",
            "本目录给任意 AI 客户端接入 AgentSight for Windows 使用。AgentSight 是本机像素级 observe-and-act 宿主：AI 通过真实屏幕像素观察，通过人类等价鼠标键盘输入行动，并保留证据记录。",
            "",
            "接入步骤：",
            "",
            "1. 读取 `mcp.json`，把其中的 `mcpServers.agentsight` 合并进当前 AI 客户端的 MCP 配置。",
            "2. 读取同目录 `SKILL.md`，并把它作为 `agentsight` Skill / 使用说明安装到当前 AI 客户端。",
            "3. 如果客户端只接受目录式 Skill，也可以使用兼容副本 `agentsight/SKILL.md`。",
            "4. 完成接入后，MCP public tools 只使用 `screen`、`look`、`do`。",
            "",
            "文件说明：",
            "",
            "- `mcp.json`：首选 MCP 配置，server 名称是 `agentsight`，command 是本机安装目录下的 `AgentSightMcp.exe` 绝对路径，不包含 token。",
            "- `SKILL.md`：普通 AI 使用 AgentSight 的权威说明。",
            "- `README_FOR_AI.md`：当前文件。",
            "- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`：用户可复制给任意 AI 的安装提示。",
            "- `mcp.config.example.json`、`PROMPT_FOR_AI.md`、`README.md`、`agentsight/SKILL.md` 是兼容副本。",
            "",
            "边界：",
            "",
            "- 不使用 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API 或 shell 作为 GUI 替代。",
            "- `integrity_ok`、事件计数、像素变化只能说明协议/证据事实，不代表目标命中、因果成立或业务成功。",
            "- 默认 Host Agent 绑定 127.0.0.1；不要为了接入 MCP 打开公网端口。",
            "",
            "本机维护命令：",
            "",
            f"- 查看状态：`{current_dir / 'AgentSightSetup.exe'} status`",
            f"- 卸载自启动并停止 AgentSight：`{current_dir / 'AgentSightSetup.exe'} uninstall`",
            "- 卸载默认保留 evidence / runs 数据。",
            "",
        ]
    )


def _show_install_prompt(text: str) -> bool:
    if os.name != "nt":
        return False
    try:
        ctypes.windll.user32.MessageBoxW(None, text, "AgentSight for Windows 已安装", 0x00000040)
        return True
    except Exception:
        return False


def _default_install_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight"
    return Path.home() / "AppData" / "Local" / "AgentSight"


def _write_json_report(report: dict[str, Any], output: str | None) -> None:
    if not output:
        return
    _write_json_path(Path(output), report)


def _write_json_path(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _print_human_summary(report: dict[str, Any]) -> None:
    summary = {
        "install_status": report.get("install_status"),
        "install_root": report.get("install_root"),
        "ai_install_dir": report.get("ai_install_dir"),
        "startup_exit_code": report.get("startup", {}).get("exit_code") if isinstance(report.get("startup"), dict) else None,
        "public_port_opened": report.get("public_port_opened"),
        "copy_prompt": report.get("copy_prompt"),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
