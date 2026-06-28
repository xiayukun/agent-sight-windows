from __future__ import annotations

import sys

from ai_control.session_supervisor import main as session_supervisor_main


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args and getattr(sys, "frozen", False):
        args = ["install", "--start-now", "--arm-real-input", "--wait-seconds", "60"]
    if not args:
        args = ["install", "--start-now", "--arm-real-input"]
    return session_supervisor_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
