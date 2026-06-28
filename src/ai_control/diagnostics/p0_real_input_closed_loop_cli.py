from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_control.diagnostics.p0_real_input_closed_loop import run_p0_real_input_closed_loop_smoke


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the host-side P0 real input closed-loop smoke.")
    parser.add_argument("--runs-dir", default="runs_p0_real_input_closed_loop")
    parser.add_argument("--output")
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("--notepad-only", action="store_true")
    task_group.add_argument("--calculator-only", action="store_true")
    args = parser.parse_args()

    report = run_p0_real_input_closed_loop_smoke(
        runs_dir=args.runs_dir,
        include_notepad=not args.calculator_only,
        include_calculator=not args.notepad_only,
    )
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
