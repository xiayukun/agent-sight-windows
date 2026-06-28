from __future__ import annotations

import os
import platform
import sys
import time
from pathlib import Path
from typing import Any, Mapping

from ai_control.adapters.mcp import MCPStdioAdapter, MCP_TOOL_NAMES
from ai_control.diagnostics.capture_smoke import run_post_install_capture_smoke
from ai_control.runtime_platform import platform_system_label


ARMING_FLAG = "AI_CONTROL_REAL_INPUT_SMOKE"
ARMING_REF = "AI_CONTROL_REAL_INPUT_ARMING_REF"
CONSENT_REF = "AI_CONTROL_REAL_INPUT_CONSENT_REF"
PYTHON_EXECUTABLE_OVERRIDE = "AI_CONTROL_DOCTOR_PYTHON_EXECUTABLE"


def _platform_system_label() -> str:
    return platform_system_label()


def build_first_use_doctor_report(
    adapter: MCPStdioAdapter | None = None,
    *,
    runs_dir: str | Path = "runs_first_use_doctor",
    env: Mapping[str, str] | None = None,
    include_capture_smoke: bool = False,
) -> dict[str, Any]:
    active_env = env if env is not None else os.environ
    active_runs_dir = Path(runs_dir)
    active_adapter = adapter or MCPStdioAdapter(runs_dir=active_runs_dir)
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    capabilities = active_adapter.call_tool("get_capabilities", {"probe_mode": "passive"})
    diagnostics = capabilities.get("data", {}).get("capture_diagnostics", {}) if capabilities.get("ok") else {}
    capture_smoke = (
        run_post_install_capture_smoke(active_adapter, runs_dir=active_runs_dir)
        if include_capture_smoke
        else _capture_smoke_not_run()
    )
    package = active_adapter.call_tool("get_evidence_package")
    replay = active_adapter.call_tool("read_replay")
    integrity = active_adapter.call_tool("verify_integrity")

    optional_dependencies = diagnostics.get("optional_dependencies", [])
    missing_optional_dependencies = [
        item.get("name")
        for item in optional_dependencies
        if item.get("install_required") and item.get("name")
    ]
    integrity_ok = bool(integrity.get("ok") and integrity.get("data", {}).get("ok"))
    evidence_package_ok = bool(package.get("ok"))
    replay_read_only = bool(replay.get("ok") and replay.get("data", {}).get("read_only"))
    input_authorization = _input_authorization_status(active_env)
    python_status = _python_status(active_env)
    next_steps = _ordinary_ai_next_steps(
        real_capture_available=bool(diagnostics.get("real_capture_available", False)),
        missing_optional_dependencies=missing_optional_dependencies,
        include_capture_smoke=include_capture_smoke,
        capture_smoke_status=capture_smoke.get("smoke_status"),
        input_authorization=input_authorization,
        python_status=python_status,
    )
    status, exit_code = _doctor_status(
        capabilities_ok=bool(capabilities.get("ok")),
        evidence_package_ok=evidence_package_ok,
        replay_read_only=replay_read_only,
        integrity_ok=integrity_ok,
        real_capture_available=bool(diagnostics.get("real_capture_available", False)),
        include_capture_smoke=include_capture_smoke,
        capture_smoke=capture_smoke,
        python_status=python_status,
    )

    return {
        "object_type": "FirstUseDoctorReport",
        "schema": "first_use_doctor_v1",
        "doctor_status": status,
        "exit_code": exit_code,
        "started_at": started_at,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": active_adapter.session_id,
        "runs_dir": str(active_runs_dir),
        "platform": {
            "system": _platform_system_label(),
            "platform": sys.platform,
            "machine": platform.machine(),
            "architecture": platform.architecture()[0],
        },
        "python": python_status,
        "public_mcp_surface": {
            "tool_count": len(MCP_TOOL_NAMES),
            "tools": list(MCP_TOOL_NAMES),
            "changed_by_doctor": False,
        },
        "capture_readiness": {
            "real_capture_available": bool(diagnostics.get("real_capture_available", False)),
            "available_real_channels": diagnostics.get("available_real_channels", []),
            "recommended_next": diagnostics.get("recommended_next"),
            "missing_optional_dependencies": missing_optional_dependencies,
            "optional_dependencies": optional_dependencies,
            "capture_smoke": capture_smoke,
        },
        "input_readiness": input_authorization,
        "evidence_readiness": {
            "diagnostics_ok": bool(capabilities.get("ok")),
            "diagnostics_ref": capabilities.get("evidence_ref"),
            "evidence_package_ok": evidence_package_ok,
            "replay_read_only": replay_read_only,
            "integrity_ok": integrity_ok,
            "install_executed": False,
            "input_executed": False,
            "background_action_executed": False,
        },
        "first_use_commands": _first_use_commands(python_status),
        "ordinary_ai_next_steps": next_steps,
        "safe_report_lines": _safe_report_lines(status, python_status, diagnostics, capture_smoke, input_authorization),
        "transcript": [
            _compact_response("get_capabilities_passive_probe", capabilities),
            _compact_response("get_evidence_package", package),
            _compact_response("read_replay", replay),
            _compact_response("verify_integrity", integrity),
        ],
    }


def _capture_smoke_not_run() -> dict[str, Any]:
    return {
        "object_type": "CaptureSmokeDecision",
        "smoke_status": "not_run",
        "exit_code": 0,
        "reason": "first_use_doctor_default_is_passive",
        "recommended_next": "run_first_use_doctor_with_capture_smoke_or_ai_control_capture_smoke",
        "install_executed": False,
        "input_executed": False,
        "background_action_executed": False,
    }


def _input_authorization_status(env: Mapping[str, str]) -> dict[str, Any]:
    present = [key for key in [ARMING_FLAG, ARMING_REF, CONSENT_REF] if env.get(key)]
    armed = env.get(ARMING_FLAG) == "armed" and bool(env.get(ARMING_REF)) and bool(env.get(CONSENT_REF))
    if armed:
        state = "armed_env_detected_doctor_will_not_execute_input"
        recommended_next = "run_host_side_input_smoke_only_if_the_human_host_intends_real_input"
    else:
        state = "not_armed_no_host_input"
        recommended_next = "run_safe_dry_run_or_have_host_arm_ai_control_input_smoke"
    return {
        "state": state,
        "host_input_sent": False,
        "host_input_executed": False,
        "host_sent_event_count": 0,
        "required_env_keys": [ARMING_FLAG, ARMING_REF, CONSENT_REF],
        "env_keys_present": present,
        "doctor_executes_input": False,
        "entrypoint": "ai-control-input-smoke",
        "recommended_next": recommended_next,
    }


def _python_status(env: Mapping[str, str]) -> dict[str, Any]:
    executable = env.get(PYTHON_EXECUTABLE_OVERRIDE) or sys.executable
    windows_store_alias_risk = _looks_like_windows_store_alias(executable)
    return {
        "executable": executable,
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "windows_store_alias_risk": windows_store_alias_risk,
        "recommended_launcher": "py" if windows_store_alias_risk or _platform_system_label() == "Windows" else _quote_command_part(executable),
        "current_executable_command": _quote_command_part(executable),
        "fallback_launcher": "py",
        "unittest_command": f"{_quote_command_part(executable)} -m unittest discover -s tests\\acceptance",
    }


def _looks_like_windows_store_alias(executable: str) -> bool:
    normalized = executable.replace("/", "\\").lower()
    return "\\windowsapps\\" in normalized and ("python.exe" in normalized or "python3.exe" in normalized)


def _first_use_commands(python_status: dict[str, Any]) -> dict[str, Any]:
    launcher = python_status["recommended_launcher"]
    return {
        "doctor_passive": f"{launcher} -m ai_control.diagnostics.first_use_doctor_cli",
        "doctor_with_capture_smoke": f"{launcher} -m ai_control.diagnostics.first_use_doctor_cli --capture-smoke",
        "capture_smoke_console": "ai-control-capture-smoke",
        "safe_real_input_demo": f"{launcher} examples\\ai_user_round34_real_input_authorized_demo.py",
        "acceptance_tests": f"{launcher} -m unittest discover -s tests\\acceptance",
        "fault_injection_tests": f"{launcher} -m unittest discover -s tests\\fault_injection",
        "host_real_input_smoke_entrypoint": "ai-control-input-smoke",
        "host_real_input_required_env": [ARMING_FLAG, ARMING_REF, CONSENT_REF],
    }


def _ordinary_ai_next_steps(
    *,
    real_capture_available: bool,
    missing_optional_dependencies: list[str],
    include_capture_smoke: bool,
    capture_smoke_status: str | None,
    input_authorization: dict[str, Any],
    python_status: dict[str, Any],
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    if python_status["windows_store_alias_risk"]:
        steps.append(
            {
                "step": "switch_python_launcher",
                "reason": "python_executable_looks_like_windows_store_alias",
                "suggested_command": "py -m ai_control.diagnostics.first_use_doctor_cli",
            }
        )
    if not real_capture_available:
        steps.append(
            {
                "step": "install_or_fix_capture_dependencies",
                "missing_optional_dependencies": missing_optional_dependencies,
                "suggested_command": 'py -m pip install -e ".[windows-capture]"',
                "boundary": "host_setup_only_not_public_mcp_control",
            }
        )
    if not include_capture_smoke:
        steps.append(
            {
                "step": "run_real_capture_smoke",
                "suggested_command": "ai-control-capture-smoke",
                "reason": "capability_status_is_not_proof_actual_capture_succeeds",
            }
        )
    elif capture_smoke_status != "real_capture_succeeded":
        steps.append(
            {
                "step": "fix_real_capture_smoke",
                "smoke_status": capture_smoke_status,
                "reason": "actual_capture_smoke_is_authoritative_for_real_observation",
            }
        )
    steps.append(
        {
            "step": "run_safe_real_input_authorization_demo",
            "suggested_command": f"{python_status['recommended_launcher']} examples\\ai_user_round34_real_input_authorized_demo.py",
            "expected_default": "not_armed_no_host_input",
        }
    )
    if input_authorization["state"] == "not_armed_no_host_input":
        steps.append(
            {
                "step": "host_may_arm_real_input_smoke",
                "entrypoint": "ai-control-input-smoke",
                "required_env_keys": input_authorization["required_env_keys"],
                "boundary": "human_host_only_public_mcp_must_not_arm_real_input",
            }
        )
    else:
        steps.append(
            {
                "step": "doctor_detected_arming_but_did_not_execute_input",
                "entrypoint": "ai-control-input-smoke",
                "boundary": "run_real_input_smoke_only_as_an_explicit_host_action",
            }
        )
    return steps


def _doctor_status(
    *,
    capabilities_ok: bool,
    evidence_package_ok: bool,
    replay_read_only: bool,
    integrity_ok: bool,
    real_capture_available: bool,
    include_capture_smoke: bool,
    capture_smoke: dict[str, Any],
    python_status: dict[str, Any],
) -> tuple[str, int]:
    if not (capabilities_ok and evidence_package_ok and replay_read_only and integrity_ok):
        return "doctor_failed_evidence_or_adapter_unhealthy", 1
    if python_status["windows_store_alias_risk"]:
        return "python_launcher_attention_required", 0
    if include_capture_smoke:
        if capture_smoke.get("smoke_status") == "real_capture_succeeded":
            return "ready_for_real_observation_smoke_passed", 0
        return "capture_smoke_not_ready", int(capture_smoke.get("exit_code", 2))
    if real_capture_available:
        return "base_ready_real_capture_declared_run_smoke_next", 0
    return "base_ready_capture_install_or_smoke_needed", 0


def _safe_report_lines(
    status: str,
    python_status: dict[str, Any],
    diagnostics: dict[str, Any],
    capture_smoke: dict[str, Any],
    input_authorization: dict[str, Any],
) -> list[str]:
    return [
        f"Doctor: status={status}; public_mcp_tool_count={len(MCP_TOOL_NAMES)}; changed_by_doctor=False.",
        (
            "Python: "
            f"executable={python_status['executable']}; "
            f"windows_store_alias_risk={python_status['windows_store_alias_risk']}."
        ),
        (
            "Capture: "
            f"real_capture_available={bool(diagnostics.get('real_capture_available', False))}; "
            f"available_real_channels={diagnostics.get('available_real_channels', [])}; "
            f"capture_smoke_status={capture_smoke.get('smoke_status')}."
        ),
        (
            "Input: "
            f"state={input_authorization['state']}; "
            f"host_input_sent={input_authorization['host_input_sent']}; "
            f"host_sent_event_count={input_authorization['host_sent_event_count']}; "
            "doctor_executes_input=False."
        ),
        "Boundary: install_executed=False; input_executed=False; background_action_executed=False; business_result_evaluated=False.",
    ]


def _compact_response(step: str, response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok"):
        return {
            "step": step,
            "ok": True,
            "object_type": response.get("data", {}).get("object_type"),
            "evidence_ref": response.get("evidence_ref"),
        }
    failure = response.get("failure", {})
    return {
        "step": step,
        "ok": False,
        "failure_code": failure.get("failure_code"),
        "evidence_ref": failure.get("evidence_ref"),
    }


def _quote_command_part(value: str) -> str:
    if not value:
        return "python"
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value
