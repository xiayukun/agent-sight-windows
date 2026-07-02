from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BoundaryViolation:
    boundary_type: str
    path: str
    value: str


FORBIDDEN_KEY_TYPES = {
    "window_handle": "window_semantics",
    "window_tree": "window_semantics",
    "control_tree": "control_semantics",
    "control_handle": "control_semantics",
    "accessibility_tree": "accessibility_semantics",
    "dom": "dom_semantics",
    "system_focus": "system_focus",
    "focused_element": "system_focus",
    "command": "command_line",
    "shell": "command_line",
    "script": "script",
    "terminal": "terminal",
    "pipe": "pipe",
    "clipboard": "clipboard",
    "clipboard_text": "clipboard",
    "background_action": "background_action",
    "delete_evidence": "evidence_tamper",
    "overwrite_evidence": "evidence_tamper",
    "hide_evidence": "evidence_tamper",
    "business_judgment": "business_judgment",
    "instruction": "business_task",
    "instructions": "business_task",
    "prompt": "business_task",
    "task": "business_task",
    "goal": "business_task",
    "objective": "business_task",
    "business_task": "business_task",
}

FORBIDDEN_VALUE_MARKERS = {
    "window_handle": "window_semantics",
    "control_tree": "control_semantics",
    "accessibility_tree": "accessibility_semantics",
    "dom": "dom_semantics",
    "cmd.exe": "command_line",
    "powershell": "command_line",
    "clipboard_text": "clipboard",
    "run this task": "business_task",
}


class BoundaryGuard:
    def check_payload(self, value: Any) -> BoundaryViolation | None:
        return self._scan(value, "$")

    def check_output(self, value: Any) -> BoundaryViolation | None:
        return self._scan(value, "$")

    def _scan(self, value: Any, path: str) -> BoundaryViolation | None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                key_lower = key_text.lower()
                if key_lower in FORBIDDEN_KEY_TYPES:
                    return BoundaryViolation(
                        boundary_type=FORBIDDEN_KEY_TYPES[key_lower],
                        path=f"{path}.{key_text}",
                        value=key_text,
                    )
                violation = self._scan(child, f"{path}.{key_text}")
                if violation:
                    return violation
        elif isinstance(value, list):
            for index, child in enumerate(value):
                violation = self._scan(child, f"{path}[{index}]")
                if violation:
                    return violation
        elif isinstance(value, str):
            lower = value.lower()
            for marker, boundary_type in FORBIDDEN_VALUE_MARKERS.items():
                if marker in lower:
                    return BoundaryViolation(boundary_type=boundary_type, path=path, value=value)
        return None
