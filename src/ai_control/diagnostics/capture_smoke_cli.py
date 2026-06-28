from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_control.adapters.mcp import MCPStdioAdapter
from ai_control.diagnostics.capture_smoke import run_post_install_capture_smoke


def build_capture_smoke_report(
    adapter: MCPStdioAdapter | None = None,
    *,
    runs_dir: str | Path = "runs",
) -> dict[str, Any]:
    active_adapter = adapter or MCPStdioAdapter(runs_dir=runs_dir)
    return run_post_install_capture_smoke(active_adapter, runs_dir=runs_dir)


def main() -> int:
    report = build_capture_smoke_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
