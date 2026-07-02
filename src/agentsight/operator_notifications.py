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

from agentsight.caller_lock import caller_hash, caller_hint, normalize_caller_id


OPERATOR_NOTIFICATION_SCHEMA = "agentsight_operator_notification_v1"
OPERATOR_NOTIFICATION_REPORT_SCHEMA = "agentsight_operator_notification_report_v1"
DEFAULT_NOTIFICATION_CLAIM_TTL_MS = 30 * 60 * 1000
MAX_NOTIFICATION_TEXT_CHARS = 1000
_NOTIFICATION_PROCESS_MUTEX = threading.RLock()


def default_agent_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "AgentSight"
    return Path.home() / "AppData" / "Local" / "AgentSight"


def default_operator_notification_file() -> Path:
    return default_agent_dir() / "operator-notifications.jsonl"


def boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def _now_ms(now_ms: int | None = None) -> int:
    return int(now_ms if now_ms is not None else time.time() * 1000)


def validate_notification_text(text: Any) -> str:
    value = "" if text is None else str(text)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        raise ValueError("notification text is required")
    if len(value) > MAX_NOTIFICATION_TEXT_CHARS:
        raise ValueError(f"notification text must be at most {MAX_NOTIFICATION_TEXT_CHARS} characters")
    return value


def text_summary(text: str) -> dict[str, Any]:
    return {
        "text_length": len(text),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text_encoding": "utf-8",
    }


@contextmanager
def _notification_guard(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    guard_path = path.with_name(f"{path.name}.guard")
    with _NOTIFICATION_PROCESS_MUTEX:
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


def notification_status(*, path: Path | None = None, now_ms: int | None = None) -> dict[str, Any]:
    notification_path = path or default_operator_notification_file()
    entries = _read_entries(notification_path)
    now = _now_ms(now_ms)
    counts = {status: 0 for status in ("pending", "claimed", "sent", "blocked", "cancelled")}
    expired_claims = 0
    for entry in entries:
        status = str(entry.get("status") or "pending")
        claim = entry.get("delivery_claim") if isinstance(entry.get("delivery_claim"), dict) else {}
        if status == "claimed" and int(claim.get("claim_expires_at_ms") or 0) <= now:
            expired_claims += 1
            counts["pending"] += 1
            continue
        if status in counts:
            counts[status] += 1
    oldest_pending = _oldest_id(entries, status="pending", now_ms=now)
    oldest_claimed = _oldest_id(entries, status="claimed", now_ms=now)
    return {
        "object_type": "AgentSightOperatorNotificationStatus",
        "schema": OPERATOR_NOTIFICATION_SCHEMA,
        "notification_file": str(notification_path),
        "exists": notification_path.exists(),
        "entry_count": len(entries),
        "counts": counts,
        "expired_claim_count": expired_claims,
        "oldest_pending_notification_id": oldest_pending,
        "oldest_claimed_notification_id": oldest_claimed,
        "newest_notification_id": entries[-1].get("notification_id") if entries else None,
        "claim_ttl_ms_default": DEFAULT_NOTIFICATION_CLAIM_TTL_MS,
        "default_channel": "wechat_file_transfer_assistant",
        "delivery_attention": _notification_delivery_attention(
            counts=counts,
            expired_claim_count=expired_claims,
            oldest_pending_notification_id=oldest_pending,
            oldest_claimed_notification_id=oldest_claimed,
        ),
        "network_delivery_enabled": False,
        "wechat_api_used": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _notification_delivery_attention(
    *,
    counts: dict[str, int],
    expired_claim_count: int,
    oldest_pending_notification_id: str | None,
    oldest_claimed_notification_id: str | None,
) -> dict[str, Any]:
    if int(counts.get("claimed") or 0) > 0:
        next_action = "prepare_claimed_notification_delivery"
        next_cli = "agentsight-tray notify prepare-delivery --caller-id <stable-id>"
    elif int(counts.get("pending") or 0) > 0:
        next_action = "claim_pending_notification"
        next_cli = "agentsight-tray notify claim --caller-id <stable-id>"
    elif expired_claim_count:
        next_action = "claim_pending_notification_after_expired_claim_release"
        next_cli = "agentsight-tray notify claim --caller-id <stable-id>"
    else:
        next_action = "no_notification_delivery_needed"
        next_cli = None
    return {
        "summary_role": "notification_visible_delivery_attention",
        "default_channel": "wechat_file_transfer_assistant",
        "pending_delivery_count": int(counts.get("pending") or 0),
        "claimed_delivery_count": int(counts.get("claimed") or 0),
        "expired_claim_count": int(expired_claim_count),
        "oldest_pending_notification_id": oldest_pending_notification_id,
        "oldest_claimed_notification_id": oldest_claimed_notification_id,
        "recommended_next_action": next_action,
        "recommended_cli": next_cli,
        "message_text_included": False,
        "tool_sends_message": False,
        "network_delivery_enabled": False,
        "wechat_api_used": False,
        "clipboard_used": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "business_success_judged": False,
    }


def enqueue_notification(
    text: Any,
    *,
    stage: str | None = None,
    channel: str = "wechat_file_transfer_assistant",
    priority: int = 0,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    notification_path = path or default_operator_notification_file()
    now = _now_ms(now_ms)
    message = validate_notification_text(text)
    entry = {
        "object_type": "AgentSightOperatorNotification",
        "schema": OPERATOR_NOTIFICATION_SCHEMA,
        "notification_id": f"notify-{uuid.uuid4().hex[:12]}",
        "status": "pending",
        "stage": str(stage or "").strip()[:64] or None,
        "channel": str(channel or "wechat_file_transfer_assistant").strip()[:80],
        "priority": int(priority),
        "text": message,
        "text_summary": text_summary(message),
        "created_at_ms": now,
        "updated_at_ms": now,
        "delivery": None,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
    with _notification_guard(notification_path):
        entries = _read_entries(notification_path)
        entries.append(entry)
        _write_entries(notification_path, entries)
    return _notification_report(
        status="notification_enqueued",
        notification_file=notification_path,
        notification=entry,
        include_text=True,
        status_now_ms=now,
    )


def claim_next_notification(
    *,
    caller_id: str | None,
    path: Path | None = None,
    claim_ttl_ms: int = DEFAULT_NOTIFICATION_CLAIM_TTL_MS,
    now_ms: int | None = None,
) -> dict[str, Any]:
    notification_path = path or default_operator_notification_file()
    now = _now_ms(now_ms)
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return _notification_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="notify claim requires a stable caller_id",
            notification_file=notification_path,
            status_now_ms=now,
            exit_code=4,
        )
    owner_hash = caller_hash(normalized)
    with _notification_guard(notification_path):
        entries = _read_entries(notification_path)
        _release_expired_claims(entries, now_ms=now)
        existing = _active_claim_for_caller(entries, owner_hash=owner_hash, now_ms=now)
        target = existing or _next_pending_notification(entries)
        if not target:
            _write_entries(notification_path, entries)
            return _notification_report(
                status="notification_claim_none_available",
                notification_file=notification_path,
                detail="no pending operator notification is available for visible delivery",
                delivery_guidance=_visible_delivery_guidance(None),
                status_now_ms=now,
            )
        expires_at = now + int(claim_ttl_ms)
        target["status"] = "claimed"
        target["updated_at_ms"] = now
        target["delivery_claim"] = {
            "caller_hash_12": owner_hash[:12],
            "caller_hint": caller_hint(normalized),
            "claimed_at_ms": target.get("delivery_claim", {}).get("claimed_at_ms") if isinstance(target.get("delivery_claim"), dict) and existing else now,
            "last_seen_at_ms": now,
            "claim_expires_at_ms": expires_at,
            "claim_ttl_ms": int(claim_ttl_ms),
            "claim_role": "visible_gui_delivery_only",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "wechat_api_used": False,
            "clipboard_used": False,
            "business_success_judged": False,
        }
        _write_entries(notification_path, entries)
    return _notification_report(
        status="notification_claimed_for_visible_delivery",
        notification_file=notification_path,
        notification=target,
        include_text=True,
        delivery_guidance=_visible_delivery_guidance(target),
        status_now_ms=now,
    )


def prepare_notification_delivery_draft(
    *,
    notification_id: str | None = None,
    caller_id: str | None = None,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    notification_path = path or default_operator_notification_file()
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return _notification_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="notify prepare-delivery requires a stable caller_id",
            notification_file=notification_path,
            status_now_ms=_now_ms(now_ms),
            exit_code=4,
        )
    with _notification_guard(notification_path):
        entries = _read_entries(notification_path)
        released = _release_expired_claims(entries, now_ms=_now_ms(now_ms))
        if released:
            _write_entries(notification_path, entries)
    owner_hash_12 = caller_hash(normalized)[:12]
    target = _notification_for_draft(entries, notification_id=notification_id, owner_hash_12=owner_hash_12)
    if target is None:
        return _notification_report(
            status="notification_not_claimed_for_caller",
            failure_code="NOTIFICATION_NOT_CLAIMED_FOR_CALLER",
            detail="no claimed notification was found for this caller; run notify claim first",
            notification_file=notification_path,
            status_now_ms=_now_ms(now_ms),
            exit_code=4,
        )
    return _notification_report(
        status="notification_delivery_draft_prepared",
        notification_file=notification_path,
        notification=target,
        include_text=False,
        delivery_guidance=_visible_delivery_guidance(target),
        delivery_draft=_delivery_draft(target, caller_id=normalized),
        status_now_ms=_now_ms(now_ms),
    )


def list_notifications(
    *,
    status_filter: str = "open",
    include_text: bool = True,
    limit: int = 20,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    notification_path = path or default_operator_notification_file()
    with _notification_guard(notification_path):
        entries = _read_entries(notification_path)
        released = _release_expired_claims(entries, now_ms=_now_ms(now_ms))
        if released:
            _write_entries(notification_path, entries)
    filtered = _filter_entries(entries, status_filter=status_filter)
    filtered.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_at_ms") or 0)))
    prompts = [_public_entry(entry, include_text=include_text) for entry in filtered[: max(0, int(limit))]]
    return _notification_report(
        status="notifications_listed",
        notification_file=notification_path,
        notifications=prompts,
        include_text=include_text,
        status_now_ms=_now_ms(now_ms),
    )


def mark_notification_sent(
    *,
    notification_id: str,
    channel: str = "wechat_file_transfer_assistant",
    evidence_path: str | None = None,
    host_sent_event_count: int = 0,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    validation = _validate_delivery_receipt(
        new_status="sent",
        notification_file=path or default_operator_notification_file(),
        evidence_path=evidence_path,
        detail="operator notification was externally observed as sent",
        host_sent_event_count=host_sent_event_count,
        now_ms=now_ms,
    )
    if validation is not None:
        return validation
    return _mark_notification(
        notification_id=notification_id,
        new_status="sent",
        channel=channel,
        evidence_path=evidence_path,
        detail="operator notification was externally observed as sent",
        host_sent_event_count=host_sent_event_count,
        path=path,
        now_ms=now_ms,
    )


def mark_notification_blocked(
    *,
    notification_id: str,
    reason: str,
    channel: str = "wechat_file_transfer_assistant",
    evidence_path: str | None = None,
    host_sent_event_count: int = 0,
    path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    validation = _validate_delivery_receipt(
        new_status="blocked",
        notification_file=path or default_operator_notification_file(),
        evidence_path=evidence_path,
        detail=reason,
        host_sent_event_count=host_sent_event_count,
        now_ms=now_ms,
    )
    if validation is not None:
        return validation
    return _mark_notification(
        notification_id=notification_id,
        new_status="blocked",
        channel=channel,
        evidence_path=evidence_path,
        detail=reason,
        host_sent_event_count=host_sent_event_count,
        path=path,
        now_ms=now_ms,
    )


def _mark_notification(
    *,
    notification_id: str,
    new_status: str,
    channel: str,
    evidence_path: str | None,
    detail: str,
    host_sent_event_count: int,
    path: Path | None,
    now_ms: int | None,
) -> dict[str, Any]:
    notification_path = path or default_operator_notification_file()
    now = _now_ms(now_ms)
    with _notification_guard(notification_path):
        entries = _read_entries(notification_path)
        target = next((entry for entry in entries if entry.get("notification_id") == notification_id), None)
        if not target:
            return _notification_report(
                status="notification_not_found",
                failure_code="NOTIFICATION_NOT_FOUND",
                detail=f"notification_id was not found: {notification_id}",
                notification_file=notification_path,
                status_now_ms=now,
                exit_code=4,
            )
        target["status"] = new_status
        target["updated_at_ms"] = now
        target["delivery"] = {
            "object_type": "AgentSightOperatorNotificationDeliveryReceipt",
            "schema": "agentsight_operator_notification_delivery_receipt_v1",
            "channel": str(channel or "wechat_file_transfer_assistant")[:80],
            "status": new_status,
            "detail": str(detail or "")[:500],
            "evidence_path": str(evidence_path) if evidence_path else None,
            "updated_at_ms": now,
            "claimed_by": _delivery_claim_summary(target.get("delivery_claim")),
            "receipt_role": "visible_gui_delivery_attempt_record",
            "external_visual_review_required": True,
            "host_input_sent": bool(host_sent_event_count),
            "host_sent_event_count": int(host_sent_event_count),
            "wechat_api_used": False,
            "clipboard_used": False,
            "business_success_judged": False,
        }
        _write_entries(notification_path, entries)
    return _notification_report(
        status=f"notification_{new_status}",
        notification_file=notification_path,
        notification=target,
        include_text=False,
        status_now_ms=now,
    )


def _filter_entries(entries: list[dict[str, Any]], *, status_filter: str) -> list[dict[str, Any]]:
    status = str(status_filter or "open").strip().lower()
    if status == "all":
        return list(entries)
    if status == "open":
        return [entry for entry in entries if entry.get("status") == "pending"]
    return [entry for entry in entries if entry.get("status") == status]


def _release_expired_claims(entries: list[dict[str, Any]], *, now_ms: int) -> int:
    released = 0
    for entry in entries:
        if entry.get("status") != "claimed":
            continue
        claim = entry.get("delivery_claim") if isinstance(entry.get("delivery_claim"), dict) else {}
        if int(claim.get("claim_expires_at_ms") or 0) <= now_ms:
            entry["status"] = "pending"
            entry["updated_at_ms"] = now_ms
            entry["delivery_claim"] = {
                **claim,
                "expired_at_ms": now_ms,
                "claim_expired": True,
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "wechat_api_used": False,
                "clipboard_used": False,
                "business_success_judged": False,
            }
            released += 1
    return released


def _active_claim_for_caller(entries: list[dict[str, Any]], *, owner_hash: str, now_ms: int) -> dict[str, Any] | None:
    claimed = []
    for entry in entries:
        if entry.get("status") != "claimed":
            continue
        claim = entry.get("delivery_claim") if isinstance(entry.get("delivery_claim"), dict) else {}
        if claim.get("caller_hash_12") == owner_hash[:12] and int(claim.get("claim_expires_at_ms") or 0) > now_ms:
            claimed.append(entry)
    if not claimed:
        return None
    claimed.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_at_ms") or 0)))
    return claimed[0]


def _next_pending_notification(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    pending = [entry for entry in entries if entry.get("status") == "pending"]
    if not pending:
        return None
    pending.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_at_ms") or 0), str(item.get("notification_id") or "")))
    return pending[0]


def _notification_for_draft(
    entries: list[dict[str, Any]],
    *,
    notification_id: str | None,
    owner_hash_12: str,
) -> dict[str, Any] | None:
    candidates = []
    for entry in entries:
        if entry.get("status") != "claimed":
            continue
        if notification_id and entry.get("notification_id") != notification_id:
            continue
        claim = entry.get("delivery_claim") if isinstance(entry.get("delivery_claim"), dict) else {}
        if claim.get("caller_hash_12") != owner_hash_12:
            continue
        candidates.append(entry)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-int(item.get("priority") or 0), int(item.get("created_at_ms") or 0), str(item.get("notification_id") or "")))
    return candidates[0]


def _oldest_id(entries: list[dict[str, Any]], *, status: str, now_ms: int | None = None) -> str | None:
    now = _now_ms(now_ms)
    candidates = []
    for entry in entries:
        entry_status = entry.get("status")
        if entry_status == "claimed":
            claim = entry.get("delivery_claim") if isinstance(entry.get("delivery_claim"), dict) else {}
            expired = int(claim.get("claim_expires_at_ms") or 0) <= now
            if expired and status == "pending":
                candidates.append(entry)
            elif not expired and status == "claimed":
                candidates.append(entry)
        elif entry_status == status:
            candidates.append(entry)
    if not candidates:
        return None
    candidates.sort(key=lambda item: (int(item.get("created_at_ms") or 0), str(item.get("notification_id") or "")))
    return str(candidates[0].get("notification_id") or "")


def _public_entry(entry: dict[str, Any] | None, *, include_text: bool) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    public = dict(entry)
    if not include_text:
        public.pop("text", None)
        public["text_redacted"] = True
    return public


def _notification_report(
    *,
    status: str,
    notification_file: Path,
    notification: dict[str, Any] | None = None,
    notifications: list[dict[str, Any] | None] | None = None,
    include_text: bool = False,
    failure_code: str | None = None,
    detail: Any = None,
    delivery_guidance: dict[str, Any] | None = None,
    delivery_draft: dict[str, Any] | None = None,
    status_now_ms: int | None = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    return {
        "object_type": "AgentSightOperatorNotificationReport",
        "schema": OPERATOR_NOTIFICATION_REPORT_SCHEMA,
        "status": status,
        "failure_code": failure_code,
        "detail": detail,
        "exit_code": exit_code,
        "notification_file": str(notification_file),
        "notification": _public_entry(notification, include_text=include_text) if notification else None,
        "notifications": notifications,
        "include_text": include_text,
        "delivery_guidance": delivery_guidance,
        "delivery_draft": delivery_draft,
        "status_summary": notification_status(path=notification_file, now_ms=status_now_ms),
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _visible_delivery_guidance(notification: dict[str, Any] | None) -> dict[str, Any]:
    channel = "wechat_file_transfer_assistant"
    if isinstance(notification, dict) and notification.get("channel"):
        channel = str(notification.get("channel"))[:80]
    return {
        "object_type": "AgentSightVisibleOperatorNotificationDeliveryGuidance",
        "schema": "agentsight_visible_operator_notification_delivery_guidance_v1",
        "target_channel": channel,
        "delivery_surface": "visible_gui_only",
        "allowed_flow": [
            "call Host Agent /screen for readiness and screen facts",
            "call Host Agent /look for visible pixels",
            "visually locate the target conversation/input field outside the tool",
            "click/type/send with Host Agent /do using externally chosen visible-pixel coordinates",
            "save before/after evidence, replay, and integrity",
            "mark this notification sent or blocked with evidence_path and host_sent_event_count",
        ],
        "forbidden_flow": [
            "wechat_api",
            "clipboard",
            "dom",
            "accessibility_tree",
            "window_semantics",
            "background_message_send",
            "business_success_judgment",
        ],
        "claim_returns_text_for_visible_delivery": isinstance(notification, dict),
        "tool_sends_message": False,
        "tool_asserts_delivery_success": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _validate_delivery_receipt(
    *,
    new_status: str,
    notification_file: Path,
    evidence_path: str | None,
    detail: str | None,
    host_sent_event_count: int,
    now_ms: int | None,
) -> dict[str, Any] | None:
    evidence = str(evidence_path or "").strip()
    if not evidence:
        return _notification_report(
            status="notification_delivery_receipt_rejected",
            failure_code="DELIVERY_EVIDENCE_REQUIRED",
            detail=f"notify mark-{new_status} requires evidence_path for an externally reviewable visible-GUI attempt",
            notification_file=notification_file,
            status_now_ms=_now_ms(now_ms),
            exit_code=4,
        )
    if new_status == "sent" and int(host_sent_event_count) <= 0:
        return _notification_report(
            status="notification_delivery_receipt_rejected",
            failure_code="HOST_SENT_EVENT_COUNT_REQUIRED",
            detail="notify mark-sent requires host_sent_event_count > 0 because sent means a visible GUI input attempt was externally observed",
            notification_file=notification_file,
            status_now_ms=_now_ms(now_ms),
            exit_code=4,
        )
    if new_status == "blocked" and not str(detail or "").strip():
        return _notification_report(
            status="notification_delivery_receipt_rejected",
            failure_code="BLOCKED_REASON_REQUIRED",
            detail="notify mark-blocked requires a reason",
            notification_file=notification_file,
            status_now_ms=_now_ms(now_ms),
            exit_code=4,
        )
    return None


def _delivery_claim_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "caller_hash_12": value.get("caller_hash_12"),
        "caller_hint": value.get("caller_hint"),
        "claimed_at_ms": value.get("claimed_at_ms"),
        "last_seen_at_ms": value.get("last_seen_at_ms"),
        "claim_expires_at_ms": value.get("claim_expires_at_ms"),
        "claim_role": value.get("claim_role"),
    }


def _delivery_draft(notification: dict[str, Any], *, caller_id: str) -> dict[str, Any]:
    text = str(notification.get("text") or "")
    return {
        "object_type": "AgentSightOperatorNotificationDeliveryDraft",
        "schema": "agentsight_operator_notification_delivery_draft_v1",
        "notification_id": notification.get("notification_id"),
        "caller": {
            "caller_hash_12": caller_hash(caller_id)[:12],
            "caller_hint": caller_hint(caller_id),
        },
        "target_channel": str(notification.get("channel") or "wechat_file_transfer_assistant")[:80],
        "message": text,
        "message_summary": text_summary(text),
        "message_length": len(text),
        "send_via_visible_gui_only": True,
        "preflight_checklist": [
            "read Host Agent discovery, then use only /screen, /look, and /do for visible GUI control",
            "visually confirm the intended target conversation/input field outside this tool",
            "type the message through Host Agent /do keyboard steps; do not use clipboard",
            "send only through visible GUI mouse/keyboard action",
            "capture after evidence and then mark sent or blocked with receipt requirements",
        ],
        "host_agent_workflow_package": _host_agent_delivery_workflow(notification),
        "tool_sends_message": False,
        "tool_asserts_delivery_success": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _host_agent_delivery_workflow(notification: dict[str, Any]) -> dict[str, Any]:
    notification_id = notification.get("notification_id")
    return {
        "object_type": "AgentSightNotificationVisibleDeliveryWorkflowPackage",
        "schema": "agentsight_notification_visible_delivery_workflow_package_v1",
        "workflow_role": "ordinary_ai_followable_public_tool_sequence",
        "notification_id": notification_id,
        "target_channel": str(notification.get("channel") or "wechat_file_transfer_assistant")[:80],
        "required_public_tools": ["Host Agent /screen", "Host Agent /look", "Host Agent /do"],
        "supporting_receipt_tools": ["agentsight-tray notify mark-sent or mark-blocked"],
        "steps": [
            {
                "step_id": "preflight",
                "tool": "Host Agent /screen",
                "purpose": "confirm embedded readiness, screen geometry, and local blockers before visible delivery",
                "sends_host_input": False,
                "returns_message_text": False,
            },
            {
                "step_id": "observe_target_surface",
                "tool": "Host Agent /look",
                "purpose": "capture raw visible pixels for external AI visual location of the target conversation/input field",
                "sends_host_input": False,
                "returns_message_text": False,
            },
            {
                "step_id": "focus_input_field",
                "tool": "Host Agent /do",
                "purpose": "click only externally chosen visible-pixel coordinates",
                "sends_host_input": True,
                "coordinate_source": "external_ai_visual_judgment",
            },
            {
                "step_id": "type_message",
                "tool": "Host Agent /do",
                "purpose": "type delivery_draft.message through keyboard events; clipboard is forbidden",
                "sends_host_input": True,
                "text_source": "delivery_draft.message",
            },
            {
                "step_id": "send_visible_message",
                "tool": "Host Agent /do",
                "purpose": "activate the visible send control or keyboard shortcut chosen by external AI visual judgment",
                "sends_host_input": True,
                "coordinate_source": "external_ai_visual_judgment",
            },
            {
                "step_id": "collect_evidence",
                "tool": "Host Agent /look plus /do response facts",
                "purpose": "collect after-visible pixels, embedded evidence facts, and host_sent_event_count facts",
                "sends_host_input": False,
            },
            {
                "step_id": "record_receipt",
                "tool": f"agentsight-tray notify mark-sent --notification-id {notification_id} --evidence-path <path> --host-sent-event-count <n>",
                "blocked_tool": f"agentsight-tray notify mark-blocked --notification-id {notification_id} --reason <reason> --evidence-path <path> --host-sent-event-count <n>",
                "purpose": "record externally reviewable delivery attempt facts without claiming business success",
                "sends_host_input": False,
            },
        ],
        "forbidden_capabilities": [
            "clipboard",
            "wechat_api",
            "dom",
            "accessibility_tree",
            "window_semantics",
            "background_sender",
            "business_success_judgment",
        ],
        "tool_executes_workflow": False,
        "tool_asserts_delivery_success": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
