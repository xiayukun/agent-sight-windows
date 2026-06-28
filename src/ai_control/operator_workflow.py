from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ai_control.caller_lock import caller_hash, caller_hint, normalize_caller_id
from ai_control.operator_notifications import (
    enqueue_notification,
    notification_status,
)
from ai_control.prompt_inbox import (
    claim_next_prompt,
    complete_prompt,
    prompt_inbox_status,
)


OPERATOR_WORKFLOW_SCHEMA = "ai_control_operator_workflow_v1"
OPERATOR_WORKFLOW_REPORT_SCHEMA = "ai_control_operator_workflow_report_v1"


def boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def workflow_status(
    *,
    caller_id: str | None = None,
    prompt_path: Path | None = None,
    notification_path: Path | None = None,
) -> dict[str, Any]:
    prompt_status = prompt_inbox_status(path=prompt_path)
    notify_status = notification_status(path=notification_path)
    normalized = normalize_caller_id(caller_id)
    recommendations = _recommended_next(prompt_status=prompt_status, notification_status=notify_status, caller_id=normalized)
    return {
        "object_type": "AIControlOperatorWorkflowStatus",
        "schema": OPERATOR_WORKFLOW_SCHEMA,
        "caller": _caller_summary(normalized),
        "prompt_inbox": prompt_status,
        "operator_notifications": notify_status,
        "recommended_next": recommendations,
        "workflow_policy": {
            "claim_prompt_before_acting": True,
            "queue_operator_progress_notifications": True,
            "completion_is_caller_bookkeeping_only": True,
            "tool_does_not_execute_prompt_tasks": True,
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def workflow_claim_next(
    *,
    caller_id: str | None,
    prompt_path: Path | None = None,
    notification_path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return _workflow_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="ai-workflow claim requires caller_id",
            caller_id=caller_id,
            prompt_path=prompt_path,
            notification_path=notification_path,
            exit_code=4,
        )
    claim = claim_next_prompt(caller_id=normalized, path=prompt_path, now_ms=now)
    notification = None
    if claim.get("status") == "prompt_claimed" and isinstance(claim.get("prompt"), dict):
        prompt = claim["prompt"]
        notification = enqueue_notification(
            _claimed_notification_text(caller_id=normalized, prompt=prompt),
            stage="prompt-claimed",
            channel="operator_notification_outbox",
            path=notification_path,
            now_ms=now,
        )
    return _workflow_report(
        status="workflow_prompt_claimed" if claim.get("status") == "prompt_claimed" else str(claim.get("status")),
        caller_id=normalized,
        prompt_report=claim,
        notification_report=notification,
        prompt_path=prompt_path,
        notification_path=notification_path,
        include_prompt_text=claim.get("status") == "prompt_claimed",
        exit_code=int(claim.get("exit_code") or 0),
    )


def workflow_complete_prompt(
    *,
    prompt_id: str,
    caller_id: str | None,
    note: str | None = None,
    prompt_path: Path | None = None,
    notification_path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    normalized = normalize_caller_id(caller_id)
    if not normalized:
        return _workflow_report(
            status="caller_identity_required",
            failure_code="CALLER_IDENTITY_REQUIRED",
            detail="ai-workflow complete requires caller_id",
            caller_id=caller_id,
            prompt_path=prompt_path,
            notification_path=notification_path,
            exit_code=4,
        )
    complete = complete_prompt(prompt_id=prompt_id, caller_id=normalized, note=note, path=prompt_path, now_ms=now)
    notification = None
    if complete.get("status") == "prompt_completed":
        notification = enqueue_notification(
            _completed_notification_text(caller_id=normalized, prompt_id=prompt_id),
            stage="prompt-completed",
            channel="operator_notification_outbox",
            path=notification_path,
            now_ms=now,
        )
    return _workflow_report(
        status="workflow_prompt_completed" if complete.get("status") == "prompt_completed" else str(complete.get("status")),
        caller_id=normalized,
        prompt_report=complete,
        notification_report=notification,
        prompt_path=prompt_path,
        notification_path=notification_path,
        exit_code=int(complete.get("exit_code") or 0),
    )


def workflow_report_progress(
    *,
    text: str,
    stage: str | None = None,
    notification_path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    notification = enqueue_notification(
        text,
        stage=stage,
        channel="operator_notification_outbox",
        path=notification_path,
        now_ms=now,
    )
    return _workflow_report(
        status="workflow_progress_report_queued",
        notification_report=notification,
        notification_path=notification_path,
    )


def workflow_report_stage_completion(
    *,
    stage: str,
    result: str,
    summary: str | None = None,
    review_path: str | None = None,
    evidence_path: str | None = None,
    notification_path: Path | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    stage_summary = _stage_completion_summary(
        stage=stage,
        result=result,
        summary=summary,
        review_path=review_path,
        evidence_path=evidence_path,
    )
    notification = enqueue_notification(
        stage_summary["message"],
        stage=stage_summary["stage"],
        channel="wechat_file_transfer_assistant",
        path=notification_path,
        now_ms=now,
    )
    return _workflow_report(
        status="workflow_stage_completion_notification_queued",
        notification_report=notification,
        notification_path=notification_path,
        detail=stage_summary,
    )


def _caller_summary(caller_id: str | None) -> dict[str, Any]:
    if not caller_id:
        return {"present": False}
    return {
        "present": True,
        "caller_hash_12": caller_hash(caller_id)[:12],
        "caller_hint": caller_hint(caller_id),
    }


def _recommended_next(
    *,
    prompt_status: dict[str, Any],
    notification_status: dict[str, Any],
    caller_id: str | None,
) -> list[str]:
    recommendations: list[str] = []
    pending_prompts = int(prompt_status.get("counts", {}).get("pending") or 0)
    pending_notifications = int(notification_status.get("counts", {}).get("pending") or 0)
    claimed_notifications = int(notification_status.get("counts", {}).get("claimed") or 0)
    blocked_notifications = int(notification_status.get("counts", {}).get("blocked") or 0)
    if pending_prompts and caller_id:
        recommendations.append("ai-control-tray ai-workflow claim --caller-id <stable-id>")
    elif pending_prompts:
        recommendations.append("choose_stable_caller_id_then_claim_prompt")
    if pending_notifications:
        recommendations.append("ai-control-tray notify claim --caller-id <stable-id>")
    if claimed_notifications:
        recommendations.append("continue_visible_operator_delivery_or_mark_sent_or_blocked")
    if blocked_notifications:
        recommendations.append("report_blocked_operator_notifications")
    if not recommendations:
        recommendations.append("no_local_operator_work_pending")
    return recommendations


def _claimed_notification_text(*, caller_id: str, prompt: dict[str, Any]) -> str:
    summary = prompt.get("text_summary") if isinstance(prompt.get("text_summary"), dict) else {}
    return (
        f"AI-Control prompt claimed: prompt_id={prompt.get('prompt_id')}; "
        f"caller={caller_hint(caller_id)}; text_length={summary.get('text_length')}; "
        "the tool has not executed the task or judged business success."
    )


def _completed_notification_text(*, caller_id: str, prompt_id: str) -> str:
    return (
        f"AI-Control prompt marked complete by caller bookkeeping: prompt_id={prompt_id}; "
        f"caller={caller_hint(caller_id)}; external evidence is still required for GUI/business success."
    )


def _stage_completion_summary(
    *,
    stage: str,
    result: str,
    summary: str | None,
    review_path: str | None,
    evidence_path: str | None,
) -> dict[str, Any]:
    normalized_stage = str(stage or "").strip()[:64] or "unknown-stage"
    normalized_result = str(result or "").strip()[:32] or "reported"
    summary_text = str(summary or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    summary_text = " ".join(summary_text.split())[:240]
    review = str(review_path or "").strip()[:240] or None
    evidence = str(evidence_path or "").strip()[:240] or None
    parts = [
        f"AI-Control stage update: {normalized_stage} {normalized_result}.",
    ]
    if summary_text:
        parts.append(summary_text)
    if review:
        parts.append(f"Review: {review}.")
    if evidence:
        parts.append(f"Evidence: {evidence}.")
    parts.append(
        "This is a queued progress notification only; visible delivery and external review are still required."
    )
    return {
        "stage": normalized_stage,
        "result": normalized_result,
        "summary": summary_text,
        "review_path": review,
        "evidence_path": evidence,
        "message": " ".join(parts),
        "message_channel": "wechat_file_transfer_assistant",
        "tool_sends_message": False,
        "host_input_sent": False,
        "business_success_judged": False,
    }


def _workflow_report(
    *,
    status: str,
    caller_id: str | None = None,
    prompt_report: dict[str, Any] | None = None,
    notification_report: dict[str, Any] | None = None,
    prompt_path: Path | None = None,
    notification_path: Path | None = None,
    include_prompt_text: bool = False,
    failure_code: str | None = None,
    detail: Any = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlOperatorWorkflowReport",
        "schema": OPERATOR_WORKFLOW_REPORT_SCHEMA,
        "status": status,
        "failure_code": failure_code,
        "detail": detail,
        "exit_code": exit_code,
        "caller": _caller_summary(normalize_caller_id(caller_id)),
        "prompt_report": prompt_report,
        "notification_report": notification_report,
        "status_snapshot": workflow_status(
            caller_id=caller_id,
            prompt_path=prompt_path,
            notification_path=notification_path,
        ),
        "include_prompt_text": include_prompt_text,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "tool_executes_prompt_task": False,
        "tool_asserts_business_success": False,
        "boundary": boundary_facts(),
    }
