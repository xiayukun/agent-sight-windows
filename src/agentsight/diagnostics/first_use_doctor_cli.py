from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose AgentSight first-use readiness without sending host input.")
    parser.add_argument("--runs-dir", default="runs_first_use_doctor")
    parser.add_argument("--capture-smoke", action="store_true")
    args = parser.parse_args(argv)

    try:
        from agentsight.diagnostics.first_use_doctor import build_first_use_doctor_report

        report = build_first_use_doctor_report(
            runs_dir=Path(args.runs_dir),
            include_capture_smoke=args.capture_smoke,
        )
    except Exception as exc:
        report = _cli_failure_report(exc, runs_dir=args.runs_dir, capture_smoke=args.capture_smoke)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])


def _cli_failure_report(exc: Exception, *, runs_dir: str, capture_smoke: bool) -> dict[str, object]:
    exception_type = type(exc).__name__
    detail = str(exc) or exception_type
    python_executable = sys.executable
    windows_store_alias_risk = _looks_like_windows_store_alias(python_executable)
    suggested_next = [
        "try_py_launcher_on_windows",
        "ensure_PYTHONPATH_points_to_src_when_running_from_repo",
        "run_from_the_project_root_or_install_the_package",
        "do_not_continue_to_real_input_until_doctor_runs_successfully",
    ]
    return {
        "object_type": "FirstUseDoctorCliFailure",
        "schema": "first_use_doctor_cli_failure_v1",
        "doctor_status": "first_use_doctor_failed_before_report",
        "exit_code": 1,
        "stage": "cli_startup_or_report_build",
        "failure_code": "FIRST_USE_DOCTOR_CLI_FAILED",
        "exception_type": exception_type,
        "detail": detail,
        "runs_dir": runs_dir,
        "capture_smoke_requested": capture_smoke,
        "python": {
            "executable": python_executable,
            "version": platform.python_version(),
            "windows_store_alias_risk": windows_store_alias_risk,
            "recommended_launcher": "py" if windows_store_alias_risk else "py_or_packaged_agentsight_first_use_doctor",
        },
        "install_executed": False,
        "input_executed": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "background_action_executed": False,
        "business_result_evaluated": False,
        "safe_to_continue_to_real_input": False,
        "suggested_next": suggested_next,
        "safe_report_lines": [
            "First-use doctor failed before a normal report could be built.",
            "No install, host input, background action, clipboard access, OCR, window semantics, DOM, accessibility, or business-result evaluation was performed by this CLI failure path.",
            "Use py on Windows or the packaged agentsight-first-use-doctor entrypoint, then rerun the passive doctor before any real-input smoke.",
        ],
    }


def _looks_like_windows_store_alias(executable: str) -> bool:
    normalized = executable.replace("/", "\\").lower()
    return "\\windowsapps\\" in normalized and ("python.exe" in normalized or "python3.exe" in normalized)


if __name__ == "__main__":
    raise SystemExit(main())
