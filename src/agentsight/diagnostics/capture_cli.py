from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentsight.adapters.mcp import MCPStdioAdapter


def build_capture_diagnostics_report(
    adapter: MCPStdioAdapter | None = None,
    *,
    runs_dir: str | Path = "runs",
) -> dict[str, Any]:
    active_adapter = adapter or MCPStdioAdapter(runs_dir=runs_dir)
    capabilities = active_adapter.call_tool("get_capabilities", {"probe_mode": "passive"})
    package = active_adapter.call_tool("get_evidence_package")
    replay = active_adapter.call_tool("read_replay")
    integrity = active_adapter.call_tool("verify_integrity")

    diagnostics = capabilities.get("data", {}).get("capture_diagnostics", {})
    return {
        "object_type": "CaptureDiagnosticsCliReport",
        "adapter": "mcp_stdio",
        "session_id": active_adapter.session_id,
        "runs_dir": str(runs_dir),
        "diagnostics_ok": bool(capabilities.get("ok")),
        "diagnostics_ref": capabilities.get("evidence_ref"),
        "real_capture_available": diagnostics.get("real_capture_available", False),
        "available_real_channels": diagnostics.get("available_real_channels", []),
        "recommended_next": diagnostics.get("recommended_next"),
        "user_authorization_required": diagnostics.get("user_authorization_required"),
        "optional_dependencies": diagnostics.get("optional_dependencies", []),
        "channels": diagnostics.get("channels", []),
        "install_executed": bool(diagnostics.get("install_executed", False)),
        "input_executed": bool(diagnostics.get("input_executed", False)),
        "background_action_executed": bool(diagnostics.get("background_action_executed", False)),
        "suggested_command_text": diagnostics.get("suggested_command_text"),
        "retest_command_text": diagnostics.get("retest_command_text"),
        "evidence_package_ok": bool(package.get("ok")),
        "replay_read_only": bool(replay.get("ok") and replay.get("data", {}).get("read_only")),
        "integrity_ok": bool(integrity.get("ok") and integrity.get("data", {}).get("ok")),
        "transcript": [
            _compact_response("get_capabilities_passive_probe", capabilities),
            _compact_response("get_evidence_package", package),
            _compact_response("read_replay", replay),
            _compact_response("verify_integrity", integrity),
        ],
    }


def main() -> int:
    report = build_capture_diagnostics_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["diagnostics_ok"] and report["integrity_ok"] else 1


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


if __name__ == "__main__":
    raise SystemExit(main())
