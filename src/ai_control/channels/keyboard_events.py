from __future__ import annotations

from typing import Any


MODIFIER_KEYS = {"CTRL", "ALT", "SHIFT", "WIN"}
LETTER_KEYS = {chr(code) for code in range(ord("A"), ord("Z") + 1)}
DIGIT_KEYS = {str(value) for value in range(10)}
NAVIGATION_KEYS = {"LEFT", "RIGHT", "UP", "DOWN", "HOME", "END", "PAGE_UP", "PAGE_DOWN"}
EDITING_KEYS = {"ESCAPE", "BACKSPACE", "DELETE", "SPACE", "ENTER", "TAB"}
FUNCTION_KEYS = {f"F{value}" for value in range(1, 13)}
SUPPORTED_KEYS = MODIFIER_KEYS | LETTER_KEYS | DIGIT_KEYS | NAVIGATION_KEYS | EDITING_KEYS | FUNCTION_KEYS
CHORD_KEY_SET = SUPPORTED_KEYS - MODIFIER_KEYS
KEY_ALIASES = {
    "CONTROL": "CTRL",
    "CTRL": "CTRL",
    "CMD": "WIN",
    "COMMAND": "WIN",
    "META": "WIN",
    "WINDOWS": "WIN",
    "WIN": "WIN",
    "ESC": "ESCAPE",
    "ESCAPE": "ESCAPE",
    "SPACEBAR": "SPACE",
    "SPACE": "SPACE",
    "PGUP": "PAGE_UP",
    "PAGEUP": "PAGE_UP",
    "PAGE_UP": "PAGE_UP",
    "PGDN": "PAGE_DOWN",
    "PAGEDOWN": "PAGE_DOWN",
    "PAGE_DOWN": "PAGE_DOWN",
    "ARROWLEFT": "LEFT",
    "ARROW_LEFT": "LEFT",
    "LEFTARROW": "LEFT",
    "LEFT": "LEFT",
    "ARROWRIGHT": "RIGHT",
    "ARROW_RIGHT": "RIGHT",
    "RIGHTARROW": "RIGHT",
    "RIGHT": "RIGHT",
    "ARROWUP": "UP",
    "ARROW_UP": "UP",
    "UPARROW": "UP",
    "UP": "UP",
    "ARROWDOWN": "DOWN",
    "ARROW_DOWN": "DOWN",
    "DOWNARROW": "DOWN",
    "DOWN": "DOWN",
    "BKSP": "BACKSPACE",
    "BACKSPACE": "BACKSPACE",
    "DEL": "DELETE",
    "DELETE": "DELETE",
    "RETURN": "ENTER",
}


def keyboard_action_summary(input_type: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    if input_type == "key_press":
        if "modifiers" in payload:
            raise ValueError("key_press does not accept modifiers")
        key = normalize_supported_key(payload.get("key"))
        if key not in SUPPORTED_KEYS:
            raise ValueError(f"unsupported key_press key: {payload.get('key')!r}")
        sequence = [{"key": key, "action": "down"}, {"key": key, "action": "up"}]
        return _summary(
            input_type="key_press",
            key=key,
            modifiers=[],
            event_sequence=sequence,
            keyboard_event_policy="explicit_key_press_only",
            may_trigger_application_paste=False,
            intentional_hold=False,
        )
    if input_type in {"key_down", "key_up"}:
        if "modifiers" in payload:
            raise ValueError(f"{input_type} does not accept modifiers")
        key = normalize_supported_key(payload.get("key"))
        action = "down" if input_type == "key_down" else "up"
        return _summary(
            input_type=input_type,
            key=key,
            modifiers=[],
            event_sequence=[{"key": key, "action": action}],
            keyboard_event_policy=f"explicit_{input_type}_only",
            may_trigger_application_paste=False,
            intentional_hold=(input_type == "key_down"),
        )
    if input_type == "key_chord":
        raw_modifiers = payload.get("modifiers")
        if not isinstance(raw_modifiers, list) or not raw_modifiers:
            raise ValueError("key_chord requires modifiers")
        modifiers = [normalize_supported_key(item) for item in raw_modifiers]
        if any(modifier not in MODIFIER_KEYS for modifier in modifiers):
            raise ValueError("key_chord modifiers must be Ctrl, Alt, Shift, or Win")
        if len(set(modifiers)) != len(modifiers):
            raise ValueError("key_chord modifiers must not contain duplicates")
        key = normalize_supported_key(payload.get("key"))
        if key not in CHORD_KEY_SET:
            raise ValueError("key_chord key must be a supported non-modifier key")
        sequence = [{"key": modifier, "action": "down"} for modifier in modifiers]
        sequence.append({"key": key, "action": "down"})
        sequence.append({"key": key, "action": "up"})
        sequence.extend({"key": modifier, "action": "up"} for modifier in reversed(modifiers))
        return _summary(
            input_type="key_chord",
            key=key,
            modifiers=modifiers,
            event_sequence=sequence,
            keyboard_event_policy="explicit_key_chord_expansion_only",
            may_trigger_application_paste=("CTRL" in modifiers and key == "V"),
            intentional_hold=False,
        )
    raise ValueError(f"unsupported keyboard input_type: {input_type!r}")


def is_keyboard_action(input_type: str | None) -> bool:
    return input_type in {"key_press", "key_chord", "key_down", "key_up"}


def normalize_supported_key(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("keyboard action requires a non-empty key")
    normalized = value.strip().replace("-", "_").replace(" ", "_").upper()
    normalized = KEY_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_KEYS:
        raise ValueError(f"unsupported keyboard key: {value!r}")
    return normalized


def normalize_key(value: object) -> str:
    return normalize_supported_key(value)


def format_chord(modifiers: list[str], key: str) -> str:
    return "+".join([*modifiers, key])


def _summary(
    *,
    input_type: str,
    key: str,
    modifiers: list[str],
    event_sequence: list[dict[str, str]],
    keyboard_event_policy: str,
    may_trigger_application_paste: bool,
    intentional_hold: bool,
) -> dict[str, Any]:
    normalized_chord = format_chord(modifiers, key) if modifiers else key
    return {
        "input_type": input_type,
        "key": key,
        "modifiers": modifiers,
        "normalized_chord": normalized_chord,
        "event_sequence": event_sequence,
        "requested_event_count": len(event_sequence),
        "human_input_equivalent": "keyboard_events",
        "keyboard_event_policy": keyboard_event_policy,
        "key_allowlist_policy": "p1b_supported_human_key_set",
        "supported_key_set": "letters_digits_navigation_editing_function_modifiers",
        "intentional_hold": intentional_hold,
        "requires_matching_key_up": intentional_hold,
        "semantic_action": False,
        "clipboard_api_used": False,
        "clipboard_read_by_tool": False,
        "clipboard_written_by_tool": False,
        "clipboard_content_observed": False,
        "paste_api_used": False,
        "file_source_used": False,
        "command_source_used": False,
        "application_effect_unverified": True,
        "may_trigger_application_paste": may_trigger_application_paste,
    }
