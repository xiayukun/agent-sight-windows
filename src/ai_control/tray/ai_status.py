from __future__ import annotations

from typing import Any

from ai_control.caller_lock import caller_hash, caller_hint, normalize_caller_id
from ai_control.diagnostics.release_readiness import build_release_readiness_report
from ai_control.tray.state import boundary_facts, load_tray_status


AI_STATUS_SCHEMA = "ai_control_tray_ai_status_v1"
VISUAL_MEMORY_CURRENT_STAGE = "p0n_storage_attention_summary"
VISUAL_MEMORY_QUERY_TYPES = [
    "status",
    "recent_frames",
    "sequence_change_index",
    "change_event",
    "event_window",
    "retention_status",
    "prune_unreferenced_buffer",
    "retention_class_projection",
    "storage_attention_summary",
]


def ai_status_report(
    *,
    caller_id: str | None = None,
    tray_status_snapshot: dict[str, Any] | None = None,
    release_summary_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tray_status = tray_status_snapshot or load_tray_status()
    normalized_caller = normalize_caller_id(caller_id)
    workflow = _dict(tray_status.get("operator_workflow"))
    prompt_inbox = _dict(tray_status.get("prompt_inbox"))
    notifications = _dict(tray_status.get("operator_notifications"))
    operator_policy = _dict(tray_status.get("operator_control_policy"))
    caller_lock = _dict(tray_status.get("caller_lock"))
    service = _dict(tray_status.get("service"))
    host_agent = _dict(tray_status.get("host_agent"))
    readiness = _readiness_summary(tray_status)
    release_summary = release_summary_snapshot or _load_release_summary()

    return {
        "object_type": "AIControlTrayAIStatus",
        "schema": AI_STATUS_SCHEMA,
        "report_role": "compact_ai_startup_preflight_status",
        "readiness": readiness,
        "caller": _caller_summary(normalized_caller),
        "host_agent": {
            "discovery_present": bool(host_agent.get("discovery_present")),
            "pid": host_agent.get("pid"),
            "service_status": service.get("service_status"),
            "service_state_present": bool(service.get("state_present")),
            "can_attempt_real_control": bool(service.get("can_attempt_real_control")),
            "control_blockers": list(service.get("control_blockers") or []),
        },
        "operator_control": {
            "policy_status": operator_policy.get("policy_status"),
            "real_control_enabled": bool(operator_policy.get("real_control_enabled")),
            "policy_file": operator_policy.get("policy_file"),
        },
        "emergency_stop": {
            "active": bool(_dict(tray_status.get("emergency_stop")).get("active")),
        },
        "caller_lock": _caller_lock_summary(caller_lock, normalized_caller),
        "operator_work": {
            "prompt_inbox_counts": _dict(prompt_inbox.get("counts")),
            "notification_counts": _dict(notifications.get("counts")),
            "notification_delivery_attention": _notification_delivery_attention_summary(notifications),
            "workflow_next": list(workflow.get("recommended_next") or []),
            "prompt_text_included": False,
            "notification_text_included": False,
        },
        "visual_memory_public_flow": {
            "current_stage": VISUAL_MEMORY_CURRENT_STAGE,
            "public_tools": ["screen", "look", "do"],
            "metadata_first_path": "Use /screen readiness and /look with q=\"frame\" plus time.near for indexed frame review.",
            "legacy_internal_query_types": list(VISUAL_MEMORY_QUERY_TYPES),
            "ai_status_performed_capture": False,
            "ai_status_returned_images": False,
            "ai_status_judged_semantics": False,
        },
        "release_readiness_publication": _release_readiness_summary(release_summary),
        "ai_startup_sequence": [
            "Read ai-control-tray ai-status --caller-id <stable-id>.",
            "If readiness.status is not ready_for_real_control_preflight, report readiness.control_blockers before real-control endpoints.",
            "If caller_lock.status is held_by_other_or_unknown_ai, wait or report CALLER_LOCK_HELD_BY_OTHER_AI.",
            "For visual memory, use /look time.near or region looks to query indexed pixels without falling back to legacy visual-memory tools.",
            "For publication work, read release_readiness_publication first; if publication_blocked is true, ask the operator for unresolved decisions before any GitHub action.",
            "For visible GUI work, read Host Agent discovery and use /screen, /look, and /do with the same stable caller_id.",
        ],
        "non_actions": {
            "screen_capture_performed": False,
            "host_input_sent": False,
            "cmd_or_shell_executed_for_gui_control": False,
            "clipboard_read": False,
            "clipboard_written": False,
            "github_repository_created": False,
            "release_published": False,
            "operator_publication_answers_chosen": False,
            "business_success_judged": False,
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _readiness_summary(tray_status: dict[str, Any]) -> dict[str, Any]:
    blockers = list(tray_status.get("control_blockers") or [])
    status = str(tray_status.get("tray_status") or "unknown")
    if bool(tray_status.get("can_attempt_real_control")) and status == "ready":
        readiness = "ready_for_real_control_preflight"
    elif status == "emergency_stopped" or "kill_switch_active" in blockers:
        readiness = "blocked_by_emergency_stop"
    elif status == "operator_control_paused" or "operator_control_paused" in blockers:
        readiness = "blocked_by_operator_control_pause"
    elif status == "discovery_missing" or "discovery_missing" in blockers:
        readiness = "blocked_by_missing_host_agent_discovery"
    else:
        readiness = "blocked_by_host_agent_or_desktop_state"
    return {
        "status": readiness,
        "tray_status": status,
        "can_attempt_real_control": bool(tray_status.get("can_attempt_real_control")),
        "control_blockers": blockers,
    }


def _caller_summary(caller_id: str | None) -> dict[str, Any]:
    if not caller_id:
        return {
            "present": False,
            "caller_id_valid": False,
            "stable_caller_id_required_for_real_control": True,
        }
    return {
        "present": True,
        "caller_id_valid": True,
        "caller_hash_12": caller_hash(caller_id)[:12],
        "caller_hint": caller_hint(caller_id),
        "stable_caller_id_required_for_real_control": True,
    }


def _caller_lock_summary(lock: dict[str, Any], caller_id: str | None) -> dict[str, Any]:
    active = bool(lock.get("active"))
    stale = bool(lock.get("stale"))
    owner_hash_12 = lock.get("owner_hash_12")
    if active and caller_id and owner_hash_12 == caller_hash(caller_id)[:12]:
        status = "held_by_this_caller"
    elif active:
        status = "held_by_other_or_unknown_ai"
    elif stale:
        status = "stale_lock_present"
    else:
        status = "not_locked"
    return {
        "status": status,
        "required_for_real_control": bool(lock.get("required_for_real_control", True)),
        "active": active,
        "stale": stale,
        "owner_hash_12": owner_hash_12,
        "owner_hint": lock.get("owner_hint"),
        "expires_at_ms": lock.get("expires_at_ms"),
    }


def _load_release_summary() -> dict[str, Any]:
    try:
        return _dict(build_release_readiness_report().get("ordinary_ai_release_summary"))
    except Exception as exc:  # pragma: no cover - defensive status fallback
        return {
            "summary_role": "ordinary_ai_release_readiness_summary_unavailable",
            "publication_blocked": True,
            "release_ready": False,
            "safe_to_publish_without_operator_review": False,
            "attention_items": ["release_readiness_summary_unavailable"],
            "unresolved_decision_count": None,
            "unresolved_decision_keys": [],
            "next_files_to_review": [],
            "next_questions_for_operator": [],
            "safe_report_lines": [f"Release readiness summary unavailable: {type(exc).__name__}"],
            "non_actions": {
                "operator_answers_chosen": False,
                "github_repository_created": False,
                "release_assets_uploaded": False,
                "release_published": False,
                "host_input_sent": False,
            },
        }


def _release_readiness_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary_role": summary.get("summary_role"),
        "release_ready": bool(summary.get("release_ready")),
        "publication_blocked": bool(summary.get("publication_blocked", True)),
        "safe_to_publish_without_operator_review": bool(
            summary.get("safe_to_publish_without_operator_review")
        ),
        "attention_items": list(summary.get("attention_items") or []),
        "unresolved_decision_count": summary.get("unresolved_decision_count"),
        "unresolved_decision_keys": list(summary.get("unresolved_decision_keys") or []),
        "next_files_to_review": list(summary.get("next_files_to_review") or [])[:8],
        "next_questions_for_operator": list(summary.get("next_questions_for_operator") or [])[:5],
        "operator_action_plan_summary": _operator_action_plan_publication_summary(summary),
        "safe_report_lines": list(summary.get("safe_report_lines") or [])[:10],
        "non_actions": {
            "operator_answers_chosen": bool(
                _dict(summary.get("non_actions")).get("operator_answers_chosen")
            ),
            "github_repository_created": bool(
                _dict(summary.get("non_actions")).get("github_repository_created")
            ),
            "release_assets_uploaded": bool(
                _dict(summary.get("non_actions")).get("release_assets_uploaded")
            ),
            "release_published": bool(_dict(summary.get("non_actions")).get("release_published")),
            "host_input_sent": bool(_dict(summary.get("non_actions")).get("host_input_sent")),
        },
    }


def _operator_action_plan_publication_summary(summary: dict[str, Any]) -> dict[str, Any]:
    action_summary = _dict(summary.get("operator_action_plan_summary"))
    return {
        "summary_role": action_summary.get("summary_role"),
        "plan_status": action_summary.get("plan_status"),
        "recommended_first_step": action_summary.get("recommended_first_step"),
        "unresolved_decision_count": action_summary.get("unresolved_decision_count"),
        "first_decision_key": action_summary.get("first_decision_key"),
        "first_operator_question": action_summary.get("first_operator_question"),
        "decision_file_path": action_summary.get("decision_file_path"),
        "blocked_action_count": action_summary.get("blocked_action_count"),
        "blocked_action_names": list(action_summary.get("blocked_action_names") or [])[:5],
        "tool_may_execute_actions": bool(action_summary.get("tool_may_execute_actions")),
        "tool_may_answer_for_operator": bool(action_summary.get("tool_may_answer_for_operator")),
        "publication_approved": bool(action_summary.get("publication_approved")),
    }


def _notification_delivery_attention_summary(notifications: dict[str, Any]) -> dict[str, Any]:
    attention = _dict(notifications.get("delivery_attention"))
    return {
        "summary_role": attention.get("summary_role"),
        "default_channel": attention.get("default_channel"),
        "pending_delivery_count": int(attention.get("pending_delivery_count") or 0),
        "claimed_delivery_count": int(attention.get("claimed_delivery_count") or 0),
        "expired_claim_count": int(attention.get("expired_claim_count") or 0),
        "oldest_pending_notification_id": attention.get("oldest_pending_notification_id"),
        "oldest_claimed_notification_id": attention.get("oldest_claimed_notification_id"),
        "recommended_next_action": attention.get("recommended_next_action"),
        "recommended_cli": attention.get("recommended_cli"),
        "message_text_included": False,
        "tool_sends_message": False,
        "network_delivery_enabled": False,
        "wechat_api_used": False,
        "clipboard_used": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "business_success_judged": False,
    }


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
