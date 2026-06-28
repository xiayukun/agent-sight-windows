from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai_control.host_agent.service_state import ServiceStateError, validate_service_state


def main() -> int:
    parser = argparse.ArgumentParser(description="Run AI-facing P0 scenarios through a discovered AI Control Host Agent.")
    parser.add_argument("--discovery-file")
    parser.add_argument("--scenario", choices=["health", "notepad", "calculator", "all"], default="notepad")
    parser.add_argument("--runs-dir", default="runs_host_agent_p0_scenarios")
    parser.add_argument("--output", default="runs_host_agent_p0_scenarios_report.json")
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    args = parser.parse_args()

    discovery_path = Path(args.discovery_file) if args.discovery_file else _default_discovery_file()
    report = run_scenarios(
        discovery_path=discovery_path,
        scenario=args.scenario,
        runs_dir=args.runs_dir,
        wait_seconds=args.wait_seconds,
        poll_seconds=args.poll_seconds,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return int(report["exit_code"])


def run_scenarios(
    *,
    discovery_path: Path,
    scenario: str,
    runs_dir: str,
    wait_seconds: float = 0.0,
    poll_seconds: float = 0.5,
) -> dict[str, Any]:
    service_state_path = _service_state_path_for_discovery(discovery_path)
    service_state_read = _read_service_state_report(service_state_path)
    preflight_wait = {
        "wait_status": "not_started_service_state_blocked",
        "found": discovery_path.exists(),
        "discovery_file": str(discovery_path),
        "attempts": 0,
        "elapsed_seconds": 0.0,
    }
    preflight_readiness = _build_ai_readiness_report(
        discovery_path=discovery_path,
        wait=preflight_wait,
        service_state_read=service_state_read,
        discovery=None,
        health_response=None,
    )
    if _service_state_preflight_blocks_scenario(service_state_read, scenario=scenario):
        return _scenario_report(
            {},
            health={},
            scenario=scenario,
            runs_dir=runs_dir,
            p0=None,
            status="host_agent_not_ready" if scenario == "health" else "host_agent_not_ready_for_real_control",
            exit_code=5,
            wait=preflight_wait,
            readiness=preflight_readiness,
        )
    wait = _wait_for_discovery(discovery_path, wait_seconds=wait_seconds, poll_seconds=poll_seconds)
    if not wait["found"]:
        readiness = _build_ai_readiness_report(
            discovery_path=discovery_path,
            wait=wait,
            service_state_read=service_state_read,
            discovery=None,
            health_response=None,
        )
        return {
            "object_type": "AIHostAgentScenarioReport",
            "exit_code": 4,
            "scenario_status": "host_agent_discovery_missing",
            "discovery_file": str(discovery_path),
            "service_state_file": str(service_state_path),
            "service_state_read": service_state_read,
            "readiness": readiness,
            "wait": wait,
            "installer_report": _read_installer_report(),
            "agent_report": _read_agent_report(),
            "suggested_next": _readiness_suggestions(readiness),
            "input_executed": False,
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    token = discovery.get("token")
    health = _request_json(discovery["health_url"], token=token)
    health_data = health.get("data", health) if not health.get("ok") is False else health.get("data", health)
    readiness = _build_ai_readiness_report(
        discovery_path=discovery_path,
        wait=wait,
        service_state_read=service_state_read,
        discovery=discovery,
        health_response=health,
    )
    if not health.get("ok", True):
        return _scenario_report(
            discovery,
            health=health_data,
            scenario=scenario,
            runs_dir=runs_dir,
            p0=None,
            status="host_agent_health_request_failed",
            exit_code=5,
            wait=wait,
            readiness=readiness,
        )
    if scenario == "health":
        status = "host_agent_ready" if readiness["can_attempt_real_control"] else "host_agent_not_ready"
        return _scenario_report(
            discovery,
            health=health_data,
            scenario=scenario,
            runs_dir=runs_dir,
            p0=None,
            status=status,
            exit_code=0 if readiness["can_attempt_real_control"] else 5,
            wait=wait,
            readiness=readiness,
        )
    if not readiness["can_attempt_real_control"]:
        return _scenario_report(
            discovery,
            health=health_data,
            scenario=scenario,
            runs_dir=runs_dir,
            p0=None,
            status="host_agent_not_ready_for_real_control",
            exit_code=5,
            wait=wait,
            readiness=readiness,
        )
    p0_reports = []
    if scenario in {"notepad", "all"}:
        p0_reports.append(
            _request_json(
                discovery["p0_url"],
                token=token,
                payload={"notepad_only": True, "runs_dir": _absolute_runs_dir(f"{runs_dir}_notepad")},
            )
        )
    if scenario in {"calculator", "all"}:
        p0_reports.append(
            _request_json(
                discovery["p0_url"],
                token=token,
                payload={"calculator_only": True, "runs_dir": _absolute_runs_dir(f"{runs_dir}_calculator")},
            )
        )
    gates = [_p0_gate(report) for report in p0_reports]
    ok = bool(gates) and all(gate["gate_passed"] for gate in gates)
    return _scenario_report(
        discovery,
        health=health_data,
        scenario=scenario,
        runs_dir=runs_dir,
        p0=p0_reports,
        gates=gates,
        status="scenario_completed_with_host_input" if ok else "scenario_did_not_complete_host_input",
        exit_code=0 if ok else 5,
        wait=wait,
        readiness=readiness,
    )


def _build_ai_readiness_report(
    *,
    discovery_path: Path,
    wait: dict[str, Any],
    service_state_read: dict[str, Any],
    discovery: dict[str, Any] | None,
    health_response: dict[str, Any] | None,
) -> dict[str, Any]:
    health_data = None
    health_request_ok = None
    if isinstance(health_response, dict):
        health_request_ok = bool(health_response.get("ok", True))
        health_data = health_response.get("data", health_response)
    service_state = service_state_read.get("data") if isinstance(service_state_read.get("data"), dict) else None
    service_state_valid = bool(service_state_read.get("valid"))
    service_status = _service_status_from(service_state=service_state, health=health_data)
    service_can_attempt = (
        service_state.get("can_attempt_real_control") if isinstance(service_state, dict) else None
    )
    health_can_attempt = health_data.get("can_attempt_real_control") if isinstance(health_data, dict) else None
    readiness_checks = {
        "service_state_read": bool(service_state_read.get("read_ok")),
        "service_state_valid": service_state_valid,
        "service_state_allows_control": service_status == "ok_active_default_desktop"
        and service_state_valid
        and service_can_attempt is not False,
        "discovery_found": bool(wait.get("found")),
        "discovery_has_token": bool(discovery and discovery.get("token")),
        "discovery_has_health_url": bool(discovery and discovery.get("health_url")),
        "health_request_ok": health_request_ok is True,
        "health_allows_control": health_can_attempt is True,
    }
    can_attempt = all(readiness_checks.values())
    blockers = _readiness_blockers(
        readiness_checks=readiness_checks,
        service_status=service_status,
        health=health_data,
    )
    return {
        "object_type": "AIControlHostAgentReadinessReport",
        "schema": "ai_control_host_agent_readiness_v1",
        "readiness_flow": ["service_state", "discovery", "health"],
        "discovery_file": str(discovery_path),
        "service_state_file": str(service_state_read.get("path") or _service_state_path_for_discovery(discovery_path)),
        "wait": wait,
        "service_state_read": service_state_read,
        "service_status": service_status,
        "service_can_attempt_real_control": service_can_attempt,
        "health_can_attempt_real_control": health_can_attempt,
        "health_request_ok": health_request_ok,
        "readiness_checks": readiness_checks,
        "can_attempt_real_control": can_attempt,
        "control_blockers": blockers,
        "suggested_next": _readiness_suggestions_from_blockers(blockers),
        "input_executed": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _boundary_facts(),
        "safe_report_lines": [
            f"readiness_flow=service_state->discovery->health",
            f"service_status={service_status}",
            f"discovery_found={bool(wait.get('found'))}",
            f"health_request_ok={health_request_ok}",
            f"can_attempt_real_control={can_attempt}",
            f"control_blockers={','.join(blockers) if blockers else 'none'}",
        ],
    }


def _readiness_blockers(
    *,
    readiness_checks: dict[str, bool],
    service_status: str | None,
    health: dict[str, Any] | None,
) -> list[str]:
    blockers: list[str] = []
    if not readiness_checks["service_state_read"]:
        blockers.append("service_state_missing_or_unreadable")
    elif not readiness_checks["service_state_valid"]:
        blockers.append("service_state_malformed")
    elif service_status != "ok_active_default_desktop":
        blockers.append(str(service_status or "service_status_unknown"))
    if not readiness_checks["discovery_found"]:
        blockers.append("host_agent_discovery_missing")
    if not readiness_checks["discovery_has_token"]:
        blockers.append("host_agent_discovery_token_missing")
    if not readiness_checks["discovery_has_health_url"]:
        blockers.append("host_agent_discovery_health_url_missing")
    if not readiness_checks["health_request_ok"]:
        blockers.append("host_agent_health_request_failed")
    elif not readiness_checks["health_allows_control"]:
        if isinstance(health, dict):
            for blocker in health.get("control_blockers") or []:
                if blocker not in blockers:
                    blockers.append(str(blocker))
        if "host_agent_not_ready" not in blockers:
            blockers.append("host_agent_not_ready")
    return blockers


def _service_status_from(*, service_state: dict[str, Any] | None, health: Any) -> str | None:
    if isinstance(service_state, dict) and service_state.get("service_status"):
        return str(service_state.get("service_status"))
    if isinstance(health, dict) and health.get("service_status"):
        return str(health.get("service_status"))
    return None


def _readiness_suggestions(readiness: dict[str, Any]) -> list[str]:
    return _readiness_suggestions_from_blockers(list(readiness.get("control_blockers") or []))


def _readiness_suggestions_from_blockers(blockers: list[str]) -> list[str]:
    if not blockers:
        return ["ready_for_control_requests"]
    suggestions: list[str] = []
    blocker_set = set(blockers)
    if (
        "service_state_missing_or_unreadable" in blocker_set
        or "service_state_malformed" in blocker_set
        or "host_agent_discovery_missing" in blocker_set
    ):
        suggestions.append("install_or_start_ai_control_host_agent_in_visible_interactive_session")
    if {"locked_desktop", "secure_desktop_active", "uac_secure_desktop_active"} & blocker_set:
        suggestions.append("wait_for_human_visible_default_desktop")
    if "discovery_stale" in blocker_set or "child_unhealthy" in blocker_set or "child_not_running" in blocker_set:
        suggestions.append("wait_for_watchdog_or_restart_host_agent")
    if "host_agent_health_request_failed" in blocker_set:
        suggestions.append("read_last_watchdog_report_and_wait_for_health")
    return suggestions or ["do_not_send_real_input_until_readiness_passes"]


def _service_state_is_hard_blocked(service_state_read: dict[str, Any]) -> bool:
    if not service_state_read.get("read_ok"):
        return False
    if not service_state_read.get("valid"):
        return True
    state = service_state_read.get("data")
    if not isinstance(state, dict):
        return True
    return str(state.get("service_status") or "") != "ok_active_default_desktop"


def _service_state_preflight_blocks_scenario(service_state_read: dict[str, Any], *, scenario: str) -> bool:
    if _service_state_is_hard_blocked(service_state_read):
        return True
    if scenario != "health" and not service_state_read.get("valid"):
        return True
    return False


def _wait_for_discovery(discovery_path: Path, *, wait_seconds: float, poll_seconds: float) -> dict[str, Any]:
    started = time.time()
    deadline = started + max(wait_seconds, 0.0)
    attempts = 0
    while True:
        attempts += 1
        if discovery_path.exists():
            return {
                "wait_status": "discovery_found",
                "found": True,
                "discovery_file": str(discovery_path),
                "attempts": attempts,
                "elapsed_seconds": round(time.time() - started, 3),
            }
        if time.time() >= deadline:
            return {
                "wait_status": "discovery_timeout" if wait_seconds > 0 else "not_waited",
                "found": False,
                "discovery_file": str(discovery_path),
                "attempts": attempts,
                "elapsed_seconds": round(time.time() - started, 3),
            }
        time.sleep(max(poll_seconds, 0.05))


def _request_json(url: str, *, token: str | None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if payload is not None else "GET")
    request.add_header("accept", "application/json")
    if payload is not None:
        request.add_header("content-type", "application/json")
    if token:
        request.add_header("authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            body = json.loads(response.read().decode("utf-8"))
            return {"status": response.status, "data": body}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            body: Any = json.loads(body_text)
        except json.JSONDecodeError:
            body = {"raw": body_text}
        return {"status": exc.code, "data": body, "ok": False}
    except Exception as exc:
        return {"status": None, "data": {"error": str(exc)}, "ok": False}


def _absolute_runs_dir(runs_dir: str) -> str:
    return str(Path(runs_dir).resolve())


def _read_installer_report() -> dict[str, Any] | None:
    path = _default_installer_report_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "object_type": "AIControlHostAgentInstallerReportReadFailure",
            "installer_report_file": str(path),
            "read_failed": True,
            "error": str(exc),
        }
    if isinstance(data, dict):
        data = dict(data)
        data.pop("token", None)
        return data
    return {
        "object_type": "AIControlHostAgentInstallerReportReadFailure",
        "installer_report_file": str(path),
        "read_failed": True,
        "error": "installer_report_not_json_object",
    }


def _read_agent_report() -> dict[str, Any] | None:
    path = _default_agent_report_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "object_type": "AIControlHostAgentRuntimeReportReadFailure",
            "agent_report_file": str(path),
            "read_failed": True,
            "error": str(exc),
        }
    if not isinstance(data, dict):
        return {
            "object_type": "AIControlHostAgentRuntimeReportReadFailure",
            "agent_report_file": str(path),
            "read_failed": True,
            "error": "agent_report_not_json_object",
        }
    report = dict(data)
    pid = report.get("pid")
    report["pid_running"] = _pid_running(pid)
    report["runtime_report_status"] = "live_process" if report["pid_running"] else "stale_or_stopped_process"
    return report


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
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


def _scenario_report(
    discovery: dict[str, Any],
    *,
    health: dict[str, Any],
    scenario: str,
    runs_dir: str,
    p0: Any,
    status: str,
    exit_code: int,
    gates: list[dict[str, Any]] | None = None,
    wait: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    p0_items = p0 if isinstance(p0, list) else []
    host_event_count = sum(int(item.get("data", {}).get("host_sent_event_count") or 0) for item in p0_items)
    gate_items = gates or []
    readiness_report = readiness or {}
    return {
        "object_type": "AIHostAgentScenarioReport",
        "schema": "ai_host_agent_scenario_report_v1",
        "scenario": scenario,
        "runs_dir": runs_dir,
        "scenario_status": status,
        "exit_code": exit_code,
        "wait": wait,
        "installer_report": _read_installer_report(),
        "agent_report": _read_agent_report(),
        "discovery": {key: value for key, value in discovery.items() if key != "token"},
        "health": health,
        "readiness": readiness,
        "service_state_file": readiness_report.get("service_state_file"),
        "service_state_read": readiness_report.get("service_state_read"),
        "service_status": readiness_report.get("service_status") or health.get("service_status"),
        "control_blockers": readiness_report.get("control_blockers") or health.get("control_blockers") or [],
        "suggested_next": readiness_report.get("suggested_next") or [],
        "p0_reports": p0,
        "scenario_gates": gate_items,
        "scenario_gate_passed": bool(gate_items) and all(item.get("gate_passed") for item in gate_items),
        "host_input_sent": host_event_count > 0,
        "host_sent_event_count": host_event_count,
        "safe_report_lines": [
            f"scenario_status={status}",
            f"wait_status={(wait or {}).get('wait_status')}",
            f"readiness_can_attempt_real_control={readiness_report.get('can_attempt_real_control')}",
            f"health_can_attempt_real_control={health.get('can_attempt_real_control')}",
            f"service_status={readiness_report.get('service_status') or health.get('service_status')}",
            f"host_sent_event_count={host_event_count}",
            f"scenario_gate_passed={bool(gate_items) and all(item.get('gate_passed') for item in gate_items)}",
            "Review saved before/after images externally; the scenario client does not judge business success.",
        ],
    }


def _p0_gate(response: dict[str, Any]) -> dict[str, Any]:
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
    action_reports = [report for task in tasks for report in task.get("action_reports", []) if isinstance(report, dict)]
    after_paths = [report.get("after_media_path_abs") for report in action_reports if report.get("after_media_path_abs")]
    external_review_after_paths = [
        report.get("external_review_after_media_path_abs") or report.get("after_media_path_abs")
        for report in action_reports
        if report.get("external_review_after_media_path_abs") or report.get("after_media_path_abs")
    ]
    evidence_ok = bool(tasks) and all(bool(task.get("evidence_chain_complete")) for task in tasks)
    no_degenerate = data.get("capture_content_degenerate") is False and all(
        report.get("after_capture_content_degenerate") is not True
        and report.get("external_review_after_capture_content_degenerate") is not True
        for report in action_reports
    )
    host_events = int(data.get("host_sent_event_count") or 0)
    no_tool_success_claims = _no_tool_success_claims(data)
    checks = {
        "http_status_ok": response.get("status") == 200,
        "p0_status_ok": data.get("p0_status") == "host_input_events_and_visual_evidence_recorded",
        "host_sent_event_count_positive": host_events > 0,
        "after_media_present": bool(external_review_after_paths),
        "capture_content_not_degenerate": bool(no_degenerate),
        "evidence_chain_complete": evidence_ok,
        "no_tool_owned_success_claims": no_tool_success_claims,
    }
    return {
        "task_names": [task.get("task_name") for task in tasks],
        "gate_passed": all(checks.values()),
        "checks": checks,
        "host_sent_event_count": host_events,
        "after_media_paths": after_paths,
        "external_review_after_media_paths": external_review_after_paths,
        "external_visual_review_required": True,
    }


def _no_tool_success_claims(value: Any) -> bool:
    forbidden = {"causal_loop_ok", "causal_change_observed", "business_success", "business_result", "ocr_text"}
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key) in forbidden:
                return False
            if not _no_tool_success_claims(nested):
                return False
    elif isinstance(value, list):
        return all(_no_tool_success_claims(item) for item in value)
    return True


def _default_discovery_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control" / "host-agent.json"
    return Path.home() / ".ai-control" / "host-agent.json"


def _service_state_path_for_discovery(discovery_path: Path) -> Path:
    if discovery_path.name == "host-agent.json":
        return discovery_path.with_name("service-state.json")
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control" / "service-state.json"
    return Path.home() / ".ai-control" / "service-state.json"


def _read_service_state_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "object_type": "AIControlServiceStateReadReport",
            "path": str(path),
            "read_ok": False,
            "valid": False,
            "status": "missing",
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "object_type": "AIControlServiceStateReadReport",
            "path": str(path),
            "read_ok": False,
            "valid": False,
            "status": "read_failed",
            "error": str(exc),
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    if not isinstance(data, dict):
        return {
            "object_type": "AIControlServiceStateReadReport",
            "path": str(path),
            "read_ok": False,
            "valid": False,
            "status": "not_json_object",
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
    validation_errors: list[str] = []
    validation_status = "valid"
    try:
        data = validate_service_state(data)
    except ServiceStateError as exc:
        validation_errors = [str(exc)]
        validation_status = "invalid"
    return {
        "object_type": "AIControlServiceStateReadReport",
        "path": str(path),
        "read_ok": True,
        "valid": not validation_errors,
        "status": "read" if not validation_errors else "malformed",
        "validator": "ai_control.host_agent.service_state.validate_service_state",
        "validation_status": validation_status,
        "validation_errors": validation_errors,
        "data": data,
        "host_input_sent": False,
        "host_sent_event_count": 0,
    }


def _default_installer_report_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control" / "last-installer-report.json"
    return Path.home() / ".ai-control" / "last-installer-report.json"


def _default_agent_report_file() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control" / "last-agent-report.json"
    return Path.home() / ".ai-control" / "last-agent-report.json"


def _boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


if __name__ == "__main__":
    raise SystemExit(main())
