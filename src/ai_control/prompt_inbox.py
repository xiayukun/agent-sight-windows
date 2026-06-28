from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ai_control.caller_lock import caller_hash, caller_hint, normalize_caller_id


PROMPT_INBOX_SCHEMA = "ai_control_prompt_inbox_v1"
PROMPT_INBOX_ENTRY_SCHEMA = "ai_control_prompt_inbox_entry_v1"
PROMPT_INBOX_REPORT_SCHEMA = "ai_control_prompt_inbox_report_v1"
DEFAULT_PROMPT_CLAIM_TTL_MS = 30 * 60 * 1000
MAX_PROMPT_TEXT_CHARS = 4000
_PROMPT_INBOX_PROCESS_MUTEX = threading.RLock()


def default_agent_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control"
    return Path.home() / "AppData" / "Local" / "ai-control"


def default_prompt_inbox_file() -> Path:
    return default_agent_dir() / "prompt-inbox.jsonl"


def boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def prompt_text_summary(text: str) -> dict[str, Any]:
    return {
        "text_length": len(text),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_encoding": "utf-8",
    }


def validate_prompt_text(text: Any) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        raise ValueError("prompt text is required")
    if len(value) > MAX_PROMPT_TEXT_CHARS:
        raise ValueError(f"prompt text must be at most {MAX_PROMPT_TEXT_CHARS} characters")
    return value


def _now_ms(now_ms: int | None = None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


@contextmanager
def _prompt_inbox_guard(inbox_path: Path):
    inbox_path.parent.mkdir(parents=True, exist_ok=True)
    guard_path = inbox_path.with_name(f"{inbox_path.name}.guard")
    with _PROMPT_INBOX_PROCESS_MUTEX:
        with guard_path.open("a+b") as guard:
            guard.seek(0, os.SEEK_END)
            if guard.tell() == 0:
                guard.write(b"\0")
                guard.flush()
            guard.seek(0)
            locked = False
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
                locked = True
            else:
                import fcntl

                fcntl.flock(guard.fileno(), fcntl.LOCK_EX)
                locked = True
            try:
                yield
            finally:
                guard.seek(0)
                if os.name == "nt" and locked:
                    import msvcrt

                    msvcrt.locking(guard.fileno(), msvcrt.LK_UNLCK, 1)
                elif locked:
                    import fcntl

                    fcntl.flock(guard.fileno(), fcntl.LOCK_UN)


def _read_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)


def prompt_inbox_status(*, path: Path | None = None, now_ms: int | None = None) -> dict[str, Any]:
    inbox_path = path or default_prompt_inbox_file()
    now = _now_ms(now_ms)
    entries = _read_entries(inbox_path)
    counts = {status: 0 for status in ("pending", "claimed", "completed", "cancelled")}
    expired_claims = 0
    for entry in entries:
        status = str(entry.get("status") or "pending")
        if status in counts:
            counts[status] += 1
        if status == "claimed" and int(entry.get("claim_expires_at_ms") or 0) <= now:
            expired_claims += 1
    return {
        "object_type": "AIControlPromptInboxStatus",
        "schema": PROMPT_INBOX_SCHEMA,
        "inbox_file": str(inbox_path),
        "exists": inbox_path.exists(),
        "entry_count": len(entries),
        "counts": counts,
        "expired_claim_count": expired_claims,
        "oldest_pending_prompt_id": _oldest_prompt_id(entries, status="pending"),
        "newest_prompt_id": entries[-1].get("prompt_id") if entries else None,
        "claim_ttl_ms_default": DEFAULT_PROMPT_CLAIM_TTL_MS,
        "designed_for_mobile_or_relay_input": True,
        "network_relay_enabled": False,
        "apk_enabled": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _oldest_prompt_id(entries: list[dict[str, Any]], *, status: str) -> str | None:
    candidates = [entry for entry in entries if entry.get("status") == status]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (int(item.get("created_at_ms") or 0), str(item.get("prompt_id") or "")))
    return str(candidates[0].get("prompt_id") or "")


def append_prompt(
    text: Any,
    *,
    source_channel: str = "local_cli",
    sender_hint: str | None = None,
    priority: int = 0,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    inbox_path = path or default_prompt_inbox_file()
    now = _now_ms(now_ms)
    prompt_text = validate_prompt_text(text)
    source = str(source_channel or "local_cli").strip()[:64]
    sender = str(sender_hint or "").strip()[:64] or None
    with _prompt_inbox_guard(inbox_path):
        entries = _read_entries(inbox_path)
        entry = {
            "object_type": "AIControlPromptInboxEntry",
            "schema": PROMPT_INBOX_ENTRY_SCHEMA,
            "prompt_id": f"prompt-{uuid.uuid4().hex[:12]}",
            "status": "pending",
            "source_channel": source,
            "sender_hint": sender,
            "priority": int(priority),
            "text": prompt_text,
            "text_summary": prompt_text_summary(prompt_text),
            "created_at_ms": now,
            "updated_at_ms": now,
            "claim": None,
            "completion": None,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        entries.append(entry)
        _write_entries(inbox_path, entries)
    return _prompt_inbox_report(
        status="prompt_added",
        inbox_file=inbox_path,
        prompt=entry,
        include_text=True,
    )


def list_prompts(
    *,
    status_filter: str = "open",
    include_text: bool = True,
    limit: int = 20,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    inbox_path = path or default_prompt_inbox_file()
    now = _now_ms(now_ms)
    entries = _read_entries(inbox_path)
    filtered = _filter_entries(entries, status_filter=status_filter, now_ms=now)
    filtered.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_at_ms") or 0)))
    clipped = filtered[: max(0, int(limit))]
    return _prompt_inbox_report(
        status="prompts_listed",
        inbox_file=inbox_path,
        prompts=[_public_entry(entry, include_text=include_text, now_ms=now) for entry in clipped],
        include_text=include_text,
    )


def claim_next_prompt(
    *,
    caller_id: str | None,
    claim_ttl_ms: int = DEFAULT_PROMPT_CLAIM_TTL_MS,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    inbox_path = path or default_prompt_inbox_file()
    now = _now_ms(now_ms)
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return _prompt_inbox_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="prompt-inbox claim requires caller_id",
            inbox_file=inbox_path,
            exit_code=4,
        )
    with _prompt_inbox_guard(inbox_path):
        entries = _read_entries(inbox_path)
        candidates = [
            entry
            for entry in entries
            if entry.get("status") == "pending"
            or (entry.get("status") == "claimed" and int(entry.get("claim_expires_at_ms") or 0) <= now)
        ]
        if not candidates:
            return _prompt_inbox_report(
                status="no_prompt_available",
                detail="no pending or expired claimed prompt is available",
                inbox_file=inbox_path,
            )
        candidates.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_at_ms") or 0)))
        selected_id = candidates[0].get("prompt_id")
        selected: dict[str, Any] | None = None
        for entry in entries:
            if entry.get("prompt_id") == selected_id:
                entry["status"] = "claimed"
                entry["updated_at_ms"] = now
                entry["claimed_at_ms"] = now
                entry["claim_expires_at_ms"] = now + int(claim_ttl_ms)
                entry["claim"] = {
                    "caller_hash_12": caller_hash(normalized)[:12],
                    "caller_hint": caller_hint(normalized),
                    "claimed_at_ms": now,
                    "claim_expires_at_ms": now + int(claim_ttl_ms),
                    "claim_ttl_ms": int(claim_ttl_ms),
                }
                selected = entry
                break
        _write_entries(inbox_path, entries)
    return _prompt_inbox_report(
        status="prompt_claimed",
        inbox_file=inbox_path,
        prompt=selected,
        include_text=True,
    )


def complete_prompt(
    *,
    prompt_id: str,
    caller_id: str | None,
    note: str | None = None,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    inbox_path = path or default_prompt_inbox_file()
    now = _now_ms(now_ms)
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return _prompt_inbox_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="prompt-inbox complete requires caller_id",
            inbox_file=inbox_path,
            exit_code=4,
        )
    with _prompt_inbox_guard(inbox_path):
        entries = _read_entries(inbox_path)
        target = next((entry for entry in entries if entry.get("prompt_id") == prompt_id), None)
        if not target:
            return _prompt_inbox_report(
                status="prompt_not_found",
                failure_code="PROMPT_NOT_FOUND",
                detail=f"prompt_id was not found: {prompt_id}",
                inbox_file=inbox_path,
                exit_code=4,
            )
        claim = target.get("claim") if isinstance(target.get("claim"), dict) else {}
        expected_hash = claim.get("caller_hash_12")
        actual_hash = caller_hash(normalized)[:12]
        if expected_hash and expected_hash != actual_hash:
            return _prompt_inbox_report(
                status="prompt_claimed_by_other_caller",
                failure_code="PROMPT_CLAIMED_BY_OTHER_CALLER",
                detail="only the caller that claimed the prompt may complete it",
                inbox_file=inbox_path,
                prompt=_public_entry(target, include_text=False, now_ms=now),
                exit_code=4,
            )
        target["status"] = "completed"
        target["updated_at_ms"] = now
        target["completed_at_ms"] = now
        target["completion"] = {
            "caller_hash_12": actual_hash,
            "caller_hint": caller_hint(normalized),
            "completed_at_ms": now,
            "note": str(note or "").strip()[:500] or None,
            "tool_asserts_business_success": False,
        }
        _write_entries(inbox_path, entries)
    return _prompt_inbox_report(
        status="prompt_completed",
        inbox_file=inbox_path,
        prompt=target,
        include_text=False,
    )


def _filter_entries(entries: list[dict[str, Any]], *, status_filter: str, now_ms: int) -> list[dict[str, Any]]:
    status = str(status_filter or "open").strip().lower()
    if status == "all":
        return list(entries)
    if status == "open":
        return [
            entry
            for entry in entries
            if entry.get("status") == "pending"
            or (entry.get("status") == "claimed" and int(entry.get("claim_expires_at_ms") or 0) <= now_ms)
        ]
    return [entry for entry in entries if entry.get("status") == status]


def _public_entry(entry: dict[str, Any] | None, *, include_text: bool, now_ms: int) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    public = dict(entry)
    if not include_text:
        public.pop("text", None)
        public["text_redacted"] = True
    public["claim_expired"] = bool(public.get("status") == "claimed" and int(public.get("claim_expires_at_ms") or 0) <= now_ms)
    return public


def _prompt_inbox_report(
    *,
    status: str,
    inbox_file: Path,
    prompt: dict[str, Any] | None = None,
    prompts: list[dict[str, Any] | None] | None = None,
    include_text: bool = False,
    failure_code: str | None = None,
    detail: Any = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlPromptInboxReport",
        "schema": PROMPT_INBOX_REPORT_SCHEMA,
        "status": status,
        "failure_code": failure_code,
        "detail": detail,
        "exit_code": exit_code,
        "inbox_file": str(inbox_file),
        "prompt": _public_entry(prompt, include_text=include_text, now_ms=_now_ms()) if prompt else None,
        "prompts": prompts,
        "include_text": include_text,
        "status_summary": prompt_inbox_status(path=inbox_file),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
