from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agentsight.operator_notifications import (
    claim_next_notification,
    enqueue_notification,
    list_notifications,
    mark_notification_blocked,
    mark_notification_sent,
    notification_status,
    prepare_notification_delivery_draft,
)
from agentsight.operator_workflow import (
    workflow_claim_next,
    workflow_complete_prompt,
    workflow_report_progress,
    workflow_report_stage_completion,
    workflow_status,
)
from agentsight.prompt_inbox import append_prompt, claim_next_prompt, complete_prompt, list_prompts, prompt_inbox_status
from agentsight.tray.ai_status import ai_status_report
from agentsight.tray.actions import allow_agentsight, clear_emergency, emergency_stop, pause_agentsight
from agentsight.tray.gui import build_tray_gui_description
from agentsight.tray.hotkey import build_hotkey_description, main as hotkey_main
from agentsight.tray.state import boundary_facts, load_tray_status
from agentsight.tray.viewers import materialize_look_preview_cache_from_operation_log


def main(argv: list[str] | None = None) -> int:
    effective_argv = list(sys.argv[1:] if argv is None else argv)
    if effective_argv and effective_argv[0] == "look-preview" and len(effective_argv) > 1 and effective_argv[1] not in {"materialize"}:
        report = _unsupported_look_preview_command_report(effective_argv[1])
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return int(report["exit_code"])
    parser = argparse.ArgumentParser(description="AgentSight tray-visible control surface.")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("status", description="Print human-visible tray status as JSON.")
    emergency = subcommands.add_parser("emergency-stop", description="Activate emergency stop and request Host Agent shutdown.")
    emergency.add_argument("--reason", default="operator_requested_emergency_stop")
    subcommands.add_parser("clear-emergency-stop", description="Clear the local emergency stop marker.")
    hotkey = subcommands.add_parser("emergency-hotkey", description="Describe or run the physical emergency hotkey monitor.")
    hotkey.add_argument("hotkey_command", nargs="?", choices=["describe", "run"], default="describe")
    hotkey.add_argument("--seconds", type=int)
    hotkey.add_argument("--output")
    operator_control = subcommands.add_parser("operator-control", description="Manage local operator permission for AI real control.")
    operator_commands = operator_control.add_subparsers(dest="operator_command")
    operator_commands.add_parser("status", description="Print operator AI-control policy.")
    pause = operator_commands.add_parser("pause", description="Pause AI real control without shutting down Host Agent.")
    pause.add_argument("--reason", default="operator_paused_agentsight")
    allow = operator_commands.add_parser("allow", description="Allow AI real control after an operator pause.")
    allow.add_argument("--reason", default="operator_allowed_agentsight")
    ai_status = subcommands.add_parser("ai-status", description="Print compact ordinary-AI startup status.")
    ai_status.add_argument("--caller-id")
    subcommands.add_parser("ai-help", description="Print concise AI usage guidance for the tray surface.")
    subcommands.add_parser("gui-describe", description="Print Tray GUI capability description from the console CLI.")
    prompt_inbox = subcommands.add_parser("prompt-inbox", description="Manage the local mobile/relay prompt inbox.")
    prompt_commands = prompt_inbox.add_subparsers(dest="prompt_command")
    prompt_commands.add_parser("status", description="Print prompt inbox counts without prompt text.")
    add_prompt = prompt_commands.add_parser("add", description="Append a prompt to the local inbox.")
    add_prompt.add_argument("--text", required=True)
    add_prompt.add_argument("--source-channel", default="local_cli")
    add_prompt.add_argument("--sender-hint")
    add_prompt.add_argument("--priority", type=int, default=0)
    list_prompt = prompt_commands.add_parser("list", description="List prompts for debugging or audit.")
    list_prompt.add_argument("--status", default="open", choices=["open", "pending", "claimed", "completed", "cancelled", "all"])
    list_prompt.add_argument("--limit", type=int, default=20)
    list_prompt.add_argument("--hide-text", action="store_true")
    claim_prompt = prompt_commands.add_parser("claim", description="Claim the next available prompt for one AI caller.")
    claim_prompt.add_argument("--caller-id", required=True)
    claim_prompt.add_argument("--claim-ttl-ms", type=int, default=30 * 60 * 1000)
    complete = prompt_commands.add_parser("complete", description="Mark a claimed prompt as caller-reported complete.")
    complete.add_argument("--prompt-id", required=True)
    complete.add_argument("--caller-id", required=True)
    complete.add_argument("--note")
    notify = subcommands.add_parser("notify", description="Manage operator progress notification outbox.")
    notify_commands = notify.add_subparsers(dest="notify_command")
    notify_commands.add_parser("status", description="Print notification outbox counts without message text.")
    enqueue = notify_commands.add_parser("enqueue", description="Queue a progress notification for later visible delivery.")
    enqueue.add_argument("--text", required=True)
    enqueue.add_argument("--stage")
    enqueue.add_argument("--channel", default="wechat_file_transfer_assistant")
    enqueue.add_argument("--priority", type=int, default=0)
    notify_list = notify_commands.add_parser("list", description="List notification outbox entries.")
    notify_list.add_argument("--status", default="open", choices=["open", "pending", "claimed", "sent", "blocked", "cancelled", "all"])
    notify_list.add_argument("--limit", type=int, default=20)
    notify_list.add_argument("--hide-text", action="store_true")
    notify_claim = notify_commands.add_parser("claim", description="Claim the next progress notification for visible GUI delivery.")
    notify_claim.add_argument("--caller-id", required=True)
    notify_claim.add_argument("--claim-ttl-ms", type=int, default=30 * 60 * 1000)
    notify_prepare = notify_commands.add_parser("prepare-delivery", description="Prepare a claimed notification draft for visible GUI delivery.")
    notify_prepare.add_argument("--caller-id", required=True)
    notify_prepare.add_argument("--notification-id")
    sent = notify_commands.add_parser("mark-sent", description="Mark a notification as externally observed sent.")
    sent.add_argument("--notification-id", required=True)
    sent.add_argument("--channel", default="wechat_file_transfer_assistant")
    sent.add_argument("--evidence-path")
    sent.add_argument("--host-sent-event-count", type=int, default=0)
    blocked = notify_commands.add_parser("mark-blocked", description="Mark a notification delivery attempt as blocked.")
    blocked.add_argument("--notification-id", required=True)
    blocked.add_argument("--reason", required=True)
    blocked.add_argument("--channel", default="wechat_file_transfer_assistant")
    blocked.add_argument("--evidence-path")
    blocked.add_argument("--host-sent-event-count", type=int, default=0)
    workflow = subcommands.add_parser("ai-workflow", description="Bridge prompt inbox and notification outbox for ordinary AI use.")
    workflow_commands = workflow.add_subparsers(dest="workflow_command")
    workflow_status_cmd = workflow_commands.add_parser("status", description="Print AI workflow summary and recommended next steps.")
    workflow_status_cmd.add_argument("--caller-id")
    workflow_claim = workflow_commands.add_parser("claim", description="Claim the next prompt and queue a progress notification.")
    workflow_claim.add_argument("--caller-id", required=True)
    workflow_complete = workflow_commands.add_parser("complete", description="Complete a claimed prompt and queue a progress notification.")
    workflow_complete.add_argument("--prompt-id", required=True)
    workflow_complete.add_argument("--caller-id", required=True)
    workflow_complete.add_argument("--note")
    workflow_report = workflow_commands.add_parser("report", description="Queue an operator progress notification through the workflow bridge.")
    workflow_report.add_argument("--text", required=True)
    workflow_report.add_argument("--stage")
    workflow_stage_report = workflow_commands.add_parser("stage-report", description="Queue a standardized stage completion progress notification.")
    workflow_stage_report.add_argument("--stage", required=True)
    workflow_stage_report.add_argument("--result", required=True)
    workflow_stage_report.add_argument("--summary")
    workflow_stage_report.add_argument("--review-path")
    workflow_stage_report.add_argument("--evidence-path")
    look_preview = subcommands.add_parser("look-preview", description="Materialize explicit derived review previews for AI /look records.")
    look_preview_commands = look_preview.add_subparsers(dest="look_preview_command")
    look_preview_materialize = look_preview_commands.add_parser(
        "materialize",
        description="Generate one derived review cache image from an operation-log look_preview_ref.",
    )
    look_preview_materialize.add_argument("--log-index", type=int, required=True)
    look_preview_materialize.add_argument("--preview-index", type=int, default=0)
    args = parser.parse_args(effective_argv)
    command = args.command or "status"
    if command == "status":
        report: dict[str, Any] = load_tray_status()
    elif command == "emergency-stop":
        report = emergency_stop(args.reason)
    elif command == "clear-emergency-stop":
        report = clear_emergency()
    elif command == "emergency-hotkey":
        hotkey_args = [args.hotkey_command]
        if args.seconds is not None:
            hotkey_args.extend(["--seconds", str(args.seconds)])
        if args.output:
            hotkey_args.extend(["--output", args.output])
        return hotkey_main(hotkey_args)
    elif command == "operator-control":
        report = operator_control_report(args)
    elif command == "ai-status":
        report = ai_status_report(caller_id=args.caller_id)
    elif command == "gui-describe":
        report = build_tray_gui_description()
    elif command == "prompt-inbox":
        report = prompt_inbox_report(args)
    elif command == "notify":
        report = operator_notification_report(args)
    elif command == "ai-workflow":
        report = ai_workflow_report(args)
    elif command == "look-preview":
        report = look_preview_report(args)
    else:
        report = ai_help_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report.get("exit_code", 0))


def _unsupported_look_preview_command_report(command: str) -> dict[str, Any]:
    return {
        "object_type": "AgentSightLookPreviewCliReport",
        "schema": "agentsight_look_preview_cli_v1",
        "status": "blocked",
        "reason": "unsupported_look_preview_command",
        "requested_command": command,
        "supported_commands": ["materialize"],
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "exit_code": 2,
        "boundary": boundary_facts(),
    }


def operator_control_report(args: argparse.Namespace) -> dict[str, Any]:
    command = args.operator_command or "status"
    if command == "pause":
        return pause_agentsight(args.reason)
    if command == "allow":
        return allow_agentsight(args.reason)
    return load_tray_status()["operator_control_policy"]


def prompt_inbox_report(args: argparse.Namespace) -> dict[str, Any]:
    command = args.prompt_command or "status"
    if command == "status":
        return prompt_inbox_status()
    if command == "add":
        return append_prompt(
            args.text,
            source_channel=args.source_channel,
            sender_hint=args.sender_hint,
            priority=args.priority,
        )
    if command == "list":
        return list_prompts(status_filter=args.status, include_text=not args.hide_text, limit=args.limit)
    if command == "claim":
        return claim_next_prompt(caller_id=args.caller_id, claim_ttl_ms=args.claim_ttl_ms)
    return complete_prompt(prompt_id=args.prompt_id, caller_id=args.caller_id, note=args.note)


def operator_notification_report(args: argparse.Namespace) -> dict[str, Any]:
    command = args.notify_command or "status"
    if command == "status":
        return notification_status()
    if command == "enqueue":
        return enqueue_notification(args.text, stage=args.stage, channel=args.channel, priority=args.priority)
    if command == "list":
        return list_notifications(status_filter=args.status, include_text=not args.hide_text, limit=args.limit)
    if command == "claim":
        return claim_next_notification(caller_id=args.caller_id, claim_ttl_ms=args.claim_ttl_ms)
    if command == "prepare-delivery":
        return prepare_notification_delivery_draft(caller_id=args.caller_id, notification_id=args.notification_id)
    if command == "mark-sent":
        return mark_notification_sent(
            notification_id=args.notification_id,
            channel=args.channel,
            evidence_path=args.evidence_path,
            host_sent_event_count=args.host_sent_event_count,
        )
    return mark_notification_blocked(
        notification_id=args.notification_id,
        reason=args.reason,
        channel=args.channel,
        evidence_path=args.evidence_path,
        host_sent_event_count=args.host_sent_event_count,
    )


def ai_workflow_report(args: argparse.Namespace) -> dict[str, Any]:
    command = args.workflow_command or "status"
    if command == "status":
        return workflow_status(caller_id=args.caller_id)
    if command == "claim":
        return workflow_claim_next(caller_id=args.caller_id)
    if command == "complete":
        return workflow_complete_prompt(prompt_id=args.prompt_id, caller_id=args.caller_id, note=args.note)
    if command == "stage-report":
        return workflow_report_stage_completion(
            stage=args.stage,
            result=args.result,
            summary=args.summary,
            review_path=args.review_path,
            evidence_path=args.evidence_path,
        )
    return workflow_report_progress(text=args.text, stage=args.stage)


def look_preview_report(args: argparse.Namespace) -> dict[str, Any]:
    command = args.look_preview_command
    if not command:
        return {
            "object_type": "AgentSightLookPreviewCliReport",
            "schema": "agentsight_look_preview_cli_v1",
            "status": "blocked",
            "reason": "look_preview_subcommand_required",
            "supported_commands": ["materialize"],
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "exit_code": 2,
            "boundary": boundary_facts(),
        }
    if command == "materialize":
        return materialize_look_preview_cache_from_operation_log(
            log_index=args.log_index,
            preview_index=args.preview_index,
        )
    return _unsupported_look_preview_command_report(str(command))


def ai_help_report() -> dict[str, Any]:
    return {
        "object_type": "AgentSightTrayAIHelp",
        "schema": "agentsight_tray_ai_help_v1",
        "recommended_flow": [
            "Run agentsight-tray status before using Host Agent endpoints.",
            "For a compact ordinary-AI preflight, run agentsight-tray ai-status --caller-id <stable-id> first.",
            "Choose a stable caller_id and send it on real-control requests as X-AgentSight-Caller or JSON caller_id.",
            "If tray_status is emergency_stopped or can_attempt_real_control is false, stop and report control_blockers.",
            "If caller_lock is active for another caller, stop and report CALLER_LOCK_HELD_BY_OTHER_AI.",
            "For mobile or relay-originated work, read agentsight-tray prompt-inbox status, then claim work with agentsight-tray prompt-inbox claim --caller-id <stable-id>.",
            "A claimed prompt is an operator instruction queue item; the tool does not judge business success when it is completed.",
            "Queue progress updates with agentsight-tray notify enqueue, then mark them sent or blocked after a visible delivery attempt.",
            "Use agentsight-tray notify claim --caller-id <stable-id> to claim one pending progress notification for visible GUI delivery.",
            "Use agentsight-tray notify prepare-delivery --caller-id <stable-id> after claim to get a visible-GUI-only draft and checklist.",
            "Notification outbox status reports counts only; message text is returned only by explicit notify list, notify claim, or notify prepare-delivery.",
            "Use agentsight-tray ai-workflow status to see prompt and notification next steps, then claim or report through that workflow bridge.",
            "P1-V: use agentsight-tray ai-workflow stage-report to queue a standardized stage progress notification for later visible delivery; it does not send messages and does not judge business success.",
            "For human review only, use agentsight-tray look-preview materialize --log-index N --preview-index M to regenerate one derived review cache image from a logged /look view; this is not an ordinary AI public look/do step.",
            "Use agentsight-tray ai-status to summarize readiness, operator-control, caller-lock, workflow counts, and public screen/look/do flow hints without screen capture or input.",
            "Use agentsight-tray operator-control status before real-control work if you need the explicit operator permission state.",
            "If operator-control status reports operator_control_paused, do not call Host Agent real-control endpoints; ask the operator to allow control.",
            "Use agentsight-tray emergency-stop only when the operator asks for an immediate visible denial of AI control.",
            "Use agentsight-tray emergency-hotkey describe to inspect the physical Ctrl+Alt+Shift+Esc emergency stop monitor. The monitor ignores injected keyboard events.",
            "Do not read clipboard, DOM, accessibility tree, window semantics, OCR, or business-result APIs.",
        ],
        "commands": {
            "status": "Read redacted discovery, service state, emergency stop, and recording policy facts.",
            "emergency-stop": "Write an emergency stop marker, write watchdog stop marker, and request token-protected Host Agent shutdown.",
            "clear-emergency-stop": "Remove emergency stop and watchdog stop markers; a separate Host Agent start is still required.",
            "emergency-hotkey describe": "Describe the physical Ctrl+Alt+Shift+Esc emergency stop monitor without starting it.",
            "emergency-hotkey run": "Run the Windows low-level keyboard hook monitor; injected key events are ignored.",
            "operator-control status": "Read the local operator policy for AI real-control permission.",
            "operator-control pause": "Pause future Host Agent real-control requests without killing the agent or watchdog.",
            "operator-control allow": "Allow future Host Agent real-control requests after an operator pause.",
            "ai-status": "Read a compact ordinary-AI startup status: readiness, operator-control, caller-lock, workflow counts, and public screen/look/do flow hints.",
            "gui-describe": "Print the Tray GUI capability model from the console CLI, useful when AgentSightTray.exe is packaged as a windowed app without stdout.",
            "prompt-inbox status": "Read local prompt queue counts without exposing prompt text.",
            "prompt-inbox claim": "Claim the next pending prompt for a stable caller_id.",
            "prompt-inbox complete": "Mark a claimed prompt as caller-reported complete without judging business success.",
            "notify status": "Read progress notification outbox counts without exposing message text.",
            "notify enqueue": "Queue an operator-visible progress update for a later GUI delivery attempt.",
            "notify claim": "Claim the next pending progress notification for visible GUI delivery; this explicit step returns message text.",
            "notify prepare-delivery": "Prepare a claimed notification draft and checklist for visible GUI delivery without sending it.",
            "notify mark-sent": "Record that an external visual review saw the notification as sent.",
            "notify mark-blocked": "Record that visible delivery was blocked, for example by WeChat re-login.",
            "ai-workflow status": "Read combined prompt inbox and notification outbox next-step guidance.",
            "ai-workflow claim": "Claim the next prompt and queue a non-semantic progress notification.",
            "ai-workflow complete": "Mark a prompt complete as caller bookkeeping and queue a progress notification.",
            "ai-workflow report": "Queue a progress notification through the same workflow bridge.",
            "ai-workflow stage-report": "P1-V: Queue a standardized stage completion notification for later visible delivery; it does not send messages and does not judge business success.",
            "look-preview materialize": "Human review only: regenerate one derived review cache image from operation-log look_preview_refs; not an ordinary AI public look/do step, not canonical evidence, and not a success judgment.",
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
        "emergency_hotkey": build_hotkey_description(),
    }


if __name__ == "__main__":
    raise SystemExit(main())
