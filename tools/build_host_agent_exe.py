from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPECS = [
    ROOT / "packaging" / "pyinstaller" / "AgentSightHostAgent.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightHostAgentInstaller.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightHostAgentScenarios.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightSupervisor.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightTray.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightTrayCli.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightTimelineViewer.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightMcpServer.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightSetup.spec",
]


def main() -> int:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])


def build_report() -> dict[str, object]:
    missing = [str(path) for path in SPECS if not path.exists()]
    if missing:
        return {
            "object_type": "AgentSightExeBuildReport",
            "build_status": "spec_missing",
            "exit_code": 4,
            "missing_specs": missing,
        }
    if importlib.util.find_spec("PyInstaller") is None:
        return {
            "object_type": "AgentSightExeBuildReport",
            "build_status": "pyinstaller_missing",
            "exit_code": 4,
            "suggested_next": ["py -m pip install pyinstaller", "py tools/build_host_agent_exe.py"],
            "specs": [str(path) for path in SPECS],
        }
    commands = []
    results = []
    for spec in SPECS:
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(spec)]
        commands.append(command)
        completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
        results.append(
            {
                "command": command,
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            return {
                "object_type": "AgentSightExeBuildReport",
                "build_status": "build_failed",
                "exit_code": completed.returncode,
                "results": results,
            }
    wrappers = _write_dist_wrappers()
    return {
        "object_type": "AgentSightExeBuildReport",
        "build_status": "built",
        "exit_code": 0,
        "results": results,
        "wrapper_outputs": [str(path) for path in wrappers],
        "expected_outputs": [
            str(ROOT / "dist" / "AgentSightSetup.exe"),
            str(ROOT / "dist" / "AgentSightSupervisor.exe"),
            str(ROOT / "dist" / "AgentSightHostAgent.exe"),
            str(ROOT / "dist" / "AgentSightHostAgentInstaller.exe"),
            str(ROOT / "dist" / "AgentSightHostAgentScenarios.exe"),
            str(ROOT / "dist" / "AgentSightTray.exe"),
            str(ROOT / "dist" / "AgentSightTrayCli.exe"),
            str(ROOT / "dist" / "AgentSightTimelineViewer.exe"),
            str(ROOT / "dist" / "AgentSightMcpServer.exe"),
        ],
    }


def _write_dist_wrappers() -> list[Path]:
    dist = ROOT / "dist"
    product_install = dist / "INSTALL_AGENTSIGHT.cmd"
    product_uninstall = dist / "UNINSTALL_AGENTSIGHT.cmd"
    install = dist / "INSTALL_AGENTSIGHT_HOST_AGENT.cmd"
    uninstall = dist / "UNINSTALL_AGENTSIGHT_HOST_AGENT.cmd"
    product_install.write_text(
        "\r\n".join(
            [
                "@echo off",
                "cd /d \"%~dp0\"",
                "echo Installing and starting AgentSight Session Supervisor...",
                "\"AgentSightSetup.exe\" install --start-now --arm-real-input --wait-seconds 60",
                "echo.",
                "echo Checking AgentSight Session Supervisor status...",
                "\"AgentSightSetup.exe\" status --output \"%~dp0..\\runs_session_supervisor_install_status_report.json\"",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    product_uninstall.write_text(
        "\r\n".join(
            [
                "@echo off",
                "cd /d \"%~dp0\"",
                "\"AgentSightSetup.exe\" uninstall",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    install.write_text(
        "\r\n".join(
            [
                "@echo off",
                "cd /d \"%~dp0\"",
                "echo Legacy: installing and starting AgentSight Host Agent split watchdog...",
                "\"AgentSightHostAgentInstaller.exe\" install --agent-exe \"%~dp0AgentSightHostAgent.exe\" --start-now --wait-seconds 60",
                "echo.",
                "echo Checking AgentSight Host Agent health...",
                "\"AgentSightHostAgentScenarios.exe\" --scenario health --wait-seconds 60 --output \"%~dp0..\\runs_host_agent_install_health_report.json\"",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    uninstall.write_text(
        "\r\n".join(
            [
                "@echo off",
                "cd /d \"%~dp0\"",
                "\"AgentSightHostAgentInstaller.exe\" uninstall",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return [product_install, product_uninstall, install, uninstall]


if __name__ == "__main__":
    raise SystemExit(main())
