from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import sys
import time
from typing import Any

from agentsight.channels.registry import ObservationChannelRegistry
from agentsight.runtime_platform import platform_system_label


OPTIONAL_CAPTURE_DEPENDENCIES = [
    {"name": "mss", "module": "mss", "distribution": "mss", "optional_extra": "windows-capture"},
    {"name": "Pillow", "module": "PIL", "distribution": "Pillow", "optional_extra": "windows-capture"},
    {
        "name": "windows-capture",
        "module": "windows_capture",
        "distribution": "windows-capture",
        "optional_extra": "windows-capture",
    },
]


def _platform_system_label() -> str:
    return platform_system_label()


class CaptureDiagnosticsService:
    def __init__(self, registry: ObservationChannelRegistry) -> None:
        self.registry = registry

    def build_report(self, *, probe_mode: str = "cached") -> dict[str, Any]:
        channels = [self._channel_probe(descriptor, probe_mode=probe_mode) for descriptor in self.registry.describe_all()]
        optional_dependencies = [self._dependency_probe(item) for item in OPTIONAL_CAPTURE_DEPENDENCIES]
        real_channels = [
            channel
            for channel in channels
            if channel["channel_ref"] != "mock_screen" and channel["status"] == "available"
        ]
        recommended_next = "try_available_channel" if real_channels else "request_optional_dependency_install"
        return {
            "object_type": "CaptureDiagnosticsReport",
            "probe_mode": probe_mode,
            "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platform": {
                "system": _platform_system_label(),
                "platform": sys.platform,
                "python_version": platform.python_version(),
                "architecture": platform.architecture()[0],
            },
            "real_capture_available": bool(real_channels),
            "available_real_channels": [channel["channel_ref"] for channel in real_channels],
            "channels": channels,
            "optional_dependencies": optional_dependencies,
            "recommended_next": recommended_next,
            "user_authorization_required": recommended_next == "request_optional_dependency_install",
            "suggested_command_text": 'py -m pip install -e ".[windows-capture]"',
            "retest_command_text": "agentsight-capture-smoke",
            "install_executed": False,
            "input_executed": False,
            "background_action_executed": False,
        }

    def _channel_probe(self, descriptor: dict[str, Any], *, probe_mode: str) -> dict[str, Any]:
        status = descriptor.get("status", "unknown")
        unavailable_reason = descriptor.get("unavailable_reason")
        failure_code = None
        if unavailable_reason:
            if str(unavailable_reason).startswith("dependency_missing"):
                failure_code = "OBSERVATION_DEPENDENCY_MISSING"
            else:
                failure_code = "SCREEN_CAPTURE_UNAVAILABLE"
        return {
            "channel_ref": descriptor.get("name"),
            "channel_type": descriptor.get("type"),
            "implementation": descriptor.get("implementation"),
            "source_kind": descriptor.get("source_kind"),
            "modes": descriptor.get("modes", []),
            "supports_sequence": bool(descriptor.get("supports_sequence", False)),
            "media_mime_types": descriptor.get("media_mime_types", []),
            "status": status,
            "probe": {
                "probe_mode": probe_mode,
                "status": status,
                "last_failure_code": failure_code,
                "last_failure_detail": unavailable_reason,
                "last_probe_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "dependencies": descriptor.get("dependencies", []),
            "unavailable_reason": unavailable_reason,
            "last_failure_code": failure_code,
            "install_hint": descriptor.get("install_hint"),
        }

    def _dependency_probe(self, dependency: dict[str, str]) -> dict[str, Any]:
        installed = importlib.util.find_spec(dependency["module"]) is not None
        version = None
        if installed:
            try:
                version = importlib.metadata.version(dependency["distribution"])
            except importlib.metadata.PackageNotFoundError:
                version = "unknown"
        return {
            "name": dependency["name"],
            "module": dependency["module"],
            "installed": installed,
            "version": version,
            "optional_extra": dependency["optional_extra"],
            "install_required": not installed,
            "install_hint": 'py -m pip install -e ".[windows-capture]"',
        }
