from __future__ import annotations

from typing import Any


MOUSE_BUTTONS = {"left", "right", "middle"}
MOUSE_INPUT_TYPES = {
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_button_down",
    "mouse_button_up",
    "mouse_drag",
    "mouse_scroll",
}
MAX_SCROLL_DELTA = 9600


def is_mouse_action(input_type: str | None) -> bool:
    return input_type in MOUSE_INPUT_TYPES


def mouse_action_summary(input_type: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    if input_type not in MOUSE_INPUT_TYPES:
        raise ValueError(f"unsupported mouse input_type: {input_type!r}")
    if input_type == "mouse_drag":
        start = _point_from_payload(payload, x_field="x", y_field="y")
        end = _point_from_payload(payload, x_field="to_x", y_field="to_y")
        button = _button(payload.get("button", "left"))
        return _summary(
            input_type=input_type,
            requested_event_count=4,
            points=[{"role": "start", **start}, {"role": "end", **end}],
            button=button,
            pointer_policy="explicit_drag_down_move_up_only",
            extra={
                "requested_coordinates": start,
                "target_coordinates": end,
                "drag_button": button,
            },
        )
    if input_type == "mouse_scroll":
        point = _point_from_payload(payload, x_field="x", y_field="y")
        vertical = _scroll_delta(payload.get("wheel_delta", payload.get("vertical_wheel_delta", 0)), "wheel_delta")
        horizontal = _scroll_delta(payload.get("horizontal_wheel_delta", 0), "horizontal_wheel_delta")
        if vertical == 0 and horizontal == 0:
            raise ValueError("mouse_scroll requires a non-zero wheel_delta or horizontal_wheel_delta")
        return _summary(
            input_type=input_type,
            requested_event_count=1 + int(vertical != 0) + int(horizontal != 0),
            points=[{"role": "point", **point}],
            button=None,
            pointer_policy="explicit_wheel_events_only",
            extra={
                "requested_coordinates": point,
                "wheel_delta": vertical,
                "horizontal_wheel_delta": horizontal,
            },
        )
    point = _point_from_payload(payload, x_field="x", y_field="y")
    button = _button(payload.get("button", "left")) if input_type != "mouse_move" else None
    event_counts = {
        "mouse_move": 1,
        "mouse_click": 3,
        "mouse_double_click": 5,
        "mouse_button_down": 2,
        "mouse_button_up": 2,
    }
    return _summary(
        input_type=input_type,
        requested_event_count=event_counts[input_type],
        points=[{"role": "point", **point}],
        button=button,
        pointer_policy="explicit_pointer_event_expansion_only",
        extra={"requested_coordinates": point},
    )


def mouse_action_points(input_type: str | None, payload: dict[str, Any]) -> list[dict[str, int | str]]:
    return list(mouse_action_summary(input_type, payload)["coordinate_points"])


def _point_from_payload(payload: dict[str, Any], *, x_field: str, y_field: str) -> dict[str, int]:
    x = payload.get(x_field)
    y = payload.get(y_field)
    if not isinstance(x, int) or not isinstance(y, int):
        raise ValueError(f"{x_field}/{y_field} must be integer screen coordinates")
    return {"x": x, "y": y}


def _button(value: Any) -> str:
    button = str(value or "left").strip().lower()
    if button not in MOUSE_BUTTONS:
        raise ValueError(f"unsupported mouse button: {value!r}")
    return button


def _scroll_delta(value: Any, field: str) -> int:
    if not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if value < -MAX_SCROLL_DELTA or value > MAX_SCROLL_DELTA:
        raise ValueError(f"{field} must be between {-MAX_SCROLL_DELTA} and {MAX_SCROLL_DELTA}")
    return value


def _summary(
    *,
    input_type: str,
    requested_event_count: int,
    points: list[dict[str, int | str]],
    button: str | None,
    pointer_policy: str,
    extra: dict[str, Any],
) -> dict[str, Any]:
    return {
        "input_type": input_type,
        "button": button,
        "coordinate_points": points,
        "requested_event_count": requested_event_count,
        "human_input_equivalent": "pointer_events",
        "pointer_event_policy": pointer_policy,
        "semantic_action": False,
        "tool_generated_coordinates": False,
        "ocr_used": False,
        "clipboard_api_used": False,
        "clipboard_read_by_tool": False,
        "clipboard_written_by_tool": False,
        "file_source_used": False,
        "command_source_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
        "application_effect_unverified": True,
        **extra,
    }
