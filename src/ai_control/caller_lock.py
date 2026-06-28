from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any


CALLER_LOCK_SCHEMA = "ai_control_single_caller_lock_v1"
CALLER_LOCK_REPORT_SCHEMA = "ai_control_single_caller_lock_report_v1"
DEFAULT_CALLER_LOCK_TTL_MS = 10 * 60 * 1000
_CALLER_LOCK_PROCESS_MUTEX = threading.RLock()


def default_agent_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "ai-control"
    return Path.home() / "AppData" / "Local" / "ai-control"


def default_caller_lock_file() -> Path:
    return default_agent_dir() / "caller-lock.json"


def boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def caller_id_from_request(
    request: dict[str, Any] | None,
    *,
    header_value: str | None = None,
) -> str | None:
    if header_value and str(header_value).strip():
        return str(header_value).strip()
    if isinstance(request, dict):
        value = request.get("caller_id") or request.get("ai_caller_id") or request.get("operator_id")
        if value and str(value).strip():
            return str(value).strip()
    return None


def normalize_caller_id(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > 96:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.:@/+ -]+", text):
        return None
    return text


def caller_hash(caller_id: str) -> str:
    return hashlib.sha256(caller_id.encode("utf-8")).hexdigest()


def caller_hint(caller_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:@/+ -]", "_", caller_id).strip()
    if len(cleaned) <= 24:
        return cleaned
    return f"{cleaned[:12]}...{cleaned[-8:]}"


def read_caller_lock(path: Path | None = None) -> dict[str, Any] | None:
    lock_path = path or default_caller_lock_file()
    if not lock_path.exists():
        return None
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


@contextmanager
def _caller_lock_guard(lock_path: Path):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    guard_path = lock_path.with_name(f"{lock_path.name}.guard")
    with _CALLER_LOCK_PROCESS_MUTEX:
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


def caller_lock_status(*, path: Path | None = None, now_ms: int | None = None) -> dict[str, Any]:
    lock_path = path or default_caller_lock_file()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    lock = read_caller_lock(lock_path)
    active = False
    stale = False
    if isinstance(lock, dict):
        expires_at_ms = int(lock.get("expires_at_ms") or 0)
        active = bool(lock.get("active")) and expires_at_ms > now
        stale = bool(lock.get("active")) and expires_at_ms <= now
    return {
        "object_type": "AIControlSingleCallerLockStatus",
        "schema": CALLER_LOCK_SCHEMA,
        "lock_file": str(lock_path),
        "required_for_real_control": True,
        "active": active,
        "stale": stale,
        "owner_hash_12": str(lock.get("owner_hash", ""))[:12] if isinstance(lock, dict) else None,
        "owner_hint": lock.get("owner_hint") if isinstance(lock, dict) else None,
        "expires_at_ms": lock.get("expires_at_ms") if isinstance(lock, dict) else None,
        "now_ms": now,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def enforce_single_caller_lock(
    caller_id: str | None,
    *,
    request_path: str,
    path: Path | None = None,
    now_ms: int | None = None,
    ttl_ms: int = DEFAULT_CALLER_LOCK_TTL_MS,
) -> tuple[bool, int, dict[str, Any]]:
    lock_path = path or default_caller_lock_file()
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return False, 400, _caller_lock_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="real-control endpoints require caller_id in the JSON body or X-AI-Control-Caller header",
            request_path=request_path,
            lock_file=lock_path,
            now_ms=now,
        )
    owner_hash = caller_hash(normalized)
    with _caller_lock_guard(lock_path):
        lock = read_caller_lock(lock_path)
        if isinstance(lock, dict) and lock.get("active"):
            existing_hash = str(lock.get("owner_hash") or "")
            expires_at_ms = int(lock.get("expires_at_ms") or 0)
            if existing_hash and existing_hash != owner_hash and expires_at_ms > now:
                return False, 423, _caller_lock_report(
                    status="caller_lock_held_by_other_ai",
                    failure_code="CALLER_LOCK_HELD_BY_OTHER_AI",
                    detail="another caller currently owns the real-control lock",
                    request_path=request_path,
                    lock_file=lock_path,
                    now_ms=now,
                    owner_hash_12=existing_hash[:12],
                    owner_hint=lock.get("owner_hint"),
                    expires_at_ms=expires_at_ms,
                    requested_caller_hash_12=owner_hash[:12],
                    requested_caller_hint=caller_hint(normalized),
                )
        expires_at_ms = now + int(ttl_ms)
        payload = {
            "object_type": "AIControlSingleCallerLock",
            "schema": CALLER_LOCK_SCHEMA,
            "active": True,
            "owner_hash": owner_hash,
            "owner_hash_12": owner_hash[:12],
            "owner_hint": caller_hint(normalized),
            "acquired_at_ms": lock.get("acquired_at_ms") if isinstance(lock, dict) and lock.get("owner_hash") == owner_hash else now,
            "last_seen_at_ms": now,
            "expires_at_ms": expires_at_ms,
            "ttl_ms": int(ttl_ms),
            "lock_policy": "single_ai_caller_for_real_control",
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": boundary_facts(),
        }
        temp_path = lock_path.with_name(f"{lock_path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp")
        try:
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(lock_path)
        finally:
            temp_path.unlink(missing_ok=True)
        try:
            os.chmod(lock_path, 0o600)
        except OSError:
            pass
        return True, 200, _caller_lock_report(
            status="caller_lock_acquired_or_refreshed",
            failure_code=None,
            detail="caller is allowed to use real-control endpoints",
            request_path=request_path,
            lock_file=lock_path,
            now_ms=now,
            owner_hash_12=owner_hash[:12],
            owner_hint=caller_hint(normalized),
            expires_at_ms=expires_at_ms,
            lock_refreshed=True,
        )


def _caller_lock_report(
    *,
    status: str,
    failure_code: str | None,
    detail: Any,
    request_path: str,
    lock_file: Path,
    now_ms: int,
    owner_hash_12: str | None = None,
    owner_hint: str | None = None,
    expires_at_ms: int | None = None,
    requested_caller_hash_12: str | None = None,
    requested_caller_hint: str | None = None,
    lock_refreshed: bool = False,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlSingleCallerLockReport",
        "schema": CALLER_LOCK_REPORT_SCHEMA,
        "lock_policy": "single_ai_caller_for_real_control",
        "status": status,
        "failure_code": failure_code,
        "detail": detail,
        "request_path": request_path,
        "lock_file": str(lock_file),
        "required_for_real_control": True,
        "owner_hash_12": owner_hash_12,
        "owner_hint": owner_hint,
        "requested_caller_hash_12": requested_caller_hash_12,
        "requested_caller_hint": requested_caller_hint,
        "expires_at_ms": expires_at_ms,
        "now_ms": now_ms,
        "lock_refreshed": lock_refreshed,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }
