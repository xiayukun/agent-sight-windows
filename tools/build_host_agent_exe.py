from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPECS = [
    ROOT / "packaging" / "pyinstaller" / "AIControlHostAgent.spec",
    ROOT / "packaging" / "pyinstaller" / "AIControlHostAgentInstaller.spec",
    ROOT / "packaging" / "pyinstaller" / "AIControlHostAgentScenarios.spec",
    ROOT / "packaging" / "pyinstaller" / "AIControlSessionSupervisor.spec",
    ROOT / "packaging" / "pyinstaller" / "AIControlInstaller.spec",
    ROOT / "packaging" / "pyinstaller" / "AIControlTray.spec",
    ROOT / "packaging" / "pyinstaller" / "AIControlTrayGui.spec",
    ROOT / "packaging" / "pyinstaller" / "AgentSightTimelineViewer.spec",
]


def main() -> int:
    report = build_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])


def build_report() -> dict[str, object]:
    missing = [str(path) for path in SPECS if not path.exists()]
    if missing:
        return {
            "object_type": "AIControlExeBuildReport",
            "build_status": "spec_missing",
            "exit_code": 4,
            "missing_specs": missing,
        }
    if importlib.util.find_spec("PyInstaller") is None:
        return {
            "object_type": "AIControlExeBuildReport",
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
                "object_type": "AIControlExeBuildReport",
                "build_status": "build_failed",
                "exit_code": completed.returncode,
                "results": results,
            }
    wrappers = _write_dist_wrappers()
    return {
        "object_type": "AIControlExeBuildReport",
        "build_status": "built",
        "exit_code": 0,
        "results": results,
        "wrapper_outputs": [str(path) for path in wrappers],
        "expected_outputs": [
            str(ROOT / "dist" / "AIControlInstaller.exe"),
            str(ROOT / "dist" / "AIControlSessionSupervisor.exe"),
            str(ROOT / "dist" / "AIControlHostAgent.exe"),
            str(ROOT / "dist" / "AIControlHostAgentInstaller.exe"),
            str(ROOT / "dist" / "AIControlHostAgentScenarios.exe"),
            str(ROOT / "dist" / "AIControlTray.exe"),
            str(ROOT / "dist" / "AIControlTrayGui.exe"),
            str(ROOT / "dist" / "AgentSightTimelineViewer.exe"),
        ],
    }


def _write_dist_wrappers() -> list[Path]:
    dist = ROOT / "dist"
    product_install = dist / "INSTALL_AI_CONTROL.cmd"
    product_uninstall = dist / "UNINSTALL_AI_CONTROL.cmd"
    install = dist / "INSTALL_AI_CONTROL_HOST_AGENT.cmd"
    uninstall = dist / "UNINSTALL_AI_CONTROL_HOST_AGENT.cmd"
    product_install.write_text(
        "\r\n".join(
            [
                "@echo off",
                "cd /d \"%~dp0\"",
                "echo Installing and starting AI Control Session Supervisor...",
                "\"AIControlInstaller.exe\" install --agent-exe \"%~dp0AIControlHostAgent.exe\" --tray-gui-exe \"%~dp0AIControlTrayGui.exe\" --start-now --arm-real-input --wait-seconds 60",
                "echo.",
                "echo Checking AI Control Session Supervisor status...",
                "\"AIControlSessionSupervisor.exe\" status --output \"%~dp0..\\runs_session_supervisor_install_status_report.json\"",
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
                "\"AIControlInstaller.exe\" uninstall",
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
                "echo Legacy: installing and starting AI Control Host Agent split watchdog...",
                "\"AIControlHostAgentInstaller.exe\" install --agent-exe \"%~dp0AIControlHostAgent.exe\" --start-now --wait-seconds 60",
                "echo.",
                "echo Checking AI Control Host Agent health...",
                "\"AIControlHostAgentScenarios.exe\" --scenario health --wait-seconds 60 --output \"%~dp0..\\runs_host_agent_install_health_report.json\"",
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
                "\"AIControlHostAgentInstaller.exe\" uninstall",
                "pause",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return [product_install, product_uninstall, install, uninstall]


if __name__ == "__main__":
    raise SystemExit(main())
