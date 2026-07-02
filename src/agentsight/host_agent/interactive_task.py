from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from subprocess import TimeoutExpired
from subprocess import run as run_process


@dataclass
class ProcessResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def run_schtasks(args: list[str]) -> ProcessResult:
    exe_path = shutil.which("schtasks.exe")
    if not exe_path:
        return ProcessResult(returncode=1, stderr="schtasks.exe not found on PATH")
    try:
        completed = run_process(
            [exe_path, *args],
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return ProcessResult(returncode=int(completed.returncode), stdout=completed.stdout, stderr=completed.stderr)
    except TimeoutExpired as exc:
        return ProcessResult(
            returncode=124,
            stdout=exc.stdout or "",
            stderr=(exc.stderr or "") + "\nschtasks.exe timed out after 10 seconds",
        )
    except Exception as exc:
        return ProcessResult(returncode=1, stderr=str(exc))


def completed_process_report(completed: ProcessResult) -> dict[str, object]:
    return {
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-1000:],
        "stderr_tail": completed.stderr[-1000:],
    }
