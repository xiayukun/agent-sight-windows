from __future__ import annotations

import platform
import sys
import time
from typing import Any

from agentsight.channels.registry import InputChannelRegistry
from agentsight.runtime_platform import platform_system_label


def _platform_system_label() -> str:
    return platform_system_label()


class InputDiagnosticsService:
    def __init__(self, registry: InputChannelRegistry) -> None:
        self.registry = registry

    def build_report(self, *, probe_mode: str = "cached") -> dict[str, Any]:
        channels = [self._channel_probe(descriptor, probe_mode=probe_mode) for descriptor in self.registry.describe_all()]
        real_channels = [
            channel
            for channel in channels
            if channel["real_input"] and channel["status"] == "available" and not channel["requires_explicit_enable"]
        ]
        declared_real_channels = [channel["channel_ref"] for channel in channels if channel["real_input"]]
        return {
            "object_type": "InputDiagnosticsReport",
            "probe_mode": probe_mode,
            "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platform": {
                "system": _platform_system_label(),
                "platform": sys.platform,
                "python_version": platform.python_version(),
                "architecture": platform.architecture()[0],
            },
            "default_input_channel_ref": self.registry.default_channel_ref,
            "real_input_available": bool(real_channels),
            "available_real_input_channels": [channel["channel_ref"] for channel in real_channels],
            "declared_real_input_channels": declared_real_channels,
            "dry_run_available": any(channel["channel_ref"] == "dry_run_input" and channel["status"] == "available" for channel in channels),
            "requires_user_enable": bool(declared_real_channels) and not bool(real_channels),
            "real_input_gate": {
                "object_type": "RealInputGatePolicy",
                "requires_explicit_arming": True,
                "requires_real_before_observation": True,
                "requires_real_after_observation": True,
                "arming_ref_source": "host_or_test_injected",
                "default_real_input_armed": False,
            },
            "input_executed": False,
            "background_action_executed": False,
            "install_executed": False,
            "channels": channels,
            "recommended_next": "use_dry_run_or_request_explicit_real_input_enablement",
        }

    def _channel_probe(self, descriptor: dict[str, Any], *, probe_mode: str) -> dict[str, Any]:
        status = descriptor.get("status", "unknown")
        unavailable_reason = descriptor.get("unavailable_reason")
        failure_code = None
        if status in {"disabled", "unavailable"}:
            failure_code = "INPUT_CHANNEL_DISABLED" if status == "disabled" else "INPUT_CHANNEL_UNAVAILABLE"
        return {
            "channel_ref": descriptor.get("name"),
            "channel_type": descriptor.get("type"),
            "implementation": descriptor.get("implementation"),
            "source_kind": descriptor.get("source_kind"),
            "execution_mode": descriptor.get("execution_mode"),
            "status": status,
            "real_input": bool(descriptor.get("real_input", False)),
            "requires_explicit_enable": bool(descriptor.get("requires_explicit_enable", False)),
            "requires_arming": bool(descriptor.get("real_input", False)) and status == "available",
            "arming_state": descriptor.get("arming_state", "not_armed"),
            "active_arming_ref": descriptor.get("active_arming_ref"),
            "input_types": descriptor.get("input_types", []),
            "input_executed": False,
            "unavailable_reason": unavailable_reason,
            "last_failure_code": failure_code,
            "probe": {
                "probe_mode": probe_mode,
                "status": status,
                "last_failure_code": failure_code,
                "last_failure_detail": unavailable_reason,
                "last_probe_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        }
