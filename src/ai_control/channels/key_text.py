from __future__ import annotations

import hashlib


MAX_KEY_TEXT_STREAM_CHARS = 256
KEY_TEXT_EVENT_POLICY = "keyboard_event_expansion_only"
KEY_TEXT_SOURCE_POLICY = "caller_literal_only_no_file_command_clipboard"


def validate_key_text_stream(text: object) -> str:
    if not isinstance(text, str):
        raise ValueError("key_text_stream requires text")
    if not text:
        raise ValueError("key_text_stream text must not be empty")
    if text.strip() == "":
        raise ValueError("key_text_stream text must not be whitespace-only")
    if len(text) > MAX_KEY_TEXT_STREAM_CHARS:
        raise ValueError(f"key_text_stream text must be <= {MAX_KEY_TEXT_STREAM_CHARS} characters")
    for char in text:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            raise ValueError("key_text_stream text must not contain control characters")
        if code > 0xFFFF:
            raise ValueError("key_text_stream text must use BMP characters only")
    return text


def key_text_summary(text: str) -> dict[str, object]:
    encoded = text.encode("utf-8")
    return {
        "text_length": len(text),
        "text_sha256": hashlib.sha256(encoded).hexdigest(),
        "text_encoding": "utf-8",
        "text_redacted": True,
        "text_recording_policy": "hash_only",
        "human_input_equivalent": "keyboard_events",
        "keyboard_event_policy": KEY_TEXT_EVENT_POLICY,
        "text_source_policy": KEY_TEXT_SOURCE_POLICY,
        "clipboard_used": False,
        "file_source_used": False,
        "command_source_used": False,
    }
