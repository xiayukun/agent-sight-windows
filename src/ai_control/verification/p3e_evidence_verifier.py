from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


EXPECTED_COUNTS = {
    "total_recorded": 30,
    "pass": 30,
    "fail": 0,
    "wechat_report_attempted": 30,
    "wechat_report_do_ok": 30,
}

BOUNDARY_KEYS = {
    "ocr_used",
    "clipboard_used",
    "accessibility_tree_used",
    "dom_used",
    "window_semantics_used",
    "business_success_judged",
}

WINDOW_SEMANTICS_PATTERN = re.compile(
    r"EnumWindows|GetClassNameW|GetWindowThreadProcessId|IsWindowVisible|"
    r"AIControlTrayWindow|tray_window_class|\bhwnd\b|ctypes\.windll\.user32|"
    r"health_at_start|foreground"
)
TOKEN_FIELD_PATTERN = re.compile(r'"token"\s*:', re.IGNORECASE)
BEARER_TOKEN_PATTERN = re.compile(r"Bearer\s+(?!<token>)[A-Za-z0-9._~+/=-]{12,}")
PATH_LIKE_PATTERN = re.compile(r"^[A-Za-z]:\\|^(runs_|docs\\|scripts\\)")
POSITIVE_CLAUDE_REVIEW_PATTERN = re.compile(
    r"claude_realtime_review_completed\"\s*:\s*true|"
    r"claude_review_status\"\s*:\s*\"PASS\"|"
    r"Claude\s+reviewed\s+PASS|Claude\s+已审核|Claude\s+已审",
    re.IGNORECASE,
)
STRUCTURED_OVERCLAIM_PATTERN = re.compile(
    r"\"tool_asserts_(?:target_hit|click_hit_target|business_success|task_success|text_entered|success)\"\s*:\s*true|"
    r"\"business_success_judged\"\s*:\s*true|"
    r"\"causal_loop_ok\"\s*:\s*true|"
    r"\"causal_change_observed\"\s*:\s*true|"
    r"\"message_delivery_succeeded\"\s*:\s*true|"
    r"\"wechat_delivery_succeeded\"\s*:\s*true|"
    r"\"input_visual_relationship_judgment\"\s*:\s*\"(?!external_review_only)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class VerifierInputs:
    summary_path: Path
    scenario_dir: Path
    handoff_dir: Path
    review_doc_path: Path | None = None
    workspace_root: Path = Path(".")
    actual_token: str | None = None


def verify_p3e(inputs: VerifierInputs) -> dict[str, Any]:
    workspace_root = inputs.workspace_root.resolve()
    summary = _read_json(inputs.summary_path)
    scenario_paths = sorted(inputs.scenario_dir.glob("scenario_*.json"))
    scenarios = [_read_json(path) for path in scenario_paths]
    handoff_paths = sorted(inputs.handoff_dir.glob("*.json"))
    handoff_json = {path.name: _read_json(path) for path in handoff_paths}

    checks: list[dict[str, Any]] = []
    checks.append(_check_counts(summary))
    checks.append(_check_scenarios(scenario_paths, scenarios))
    checks.append(_check_wechat_reports(scenarios))
    checks.append(_check_boundaries(summary, scenarios, handoff_json))

    scanned_paths = [inputs.summary_path, *scenario_paths, *handoff_paths]
    checks.append(_check_window_semantics(scanned_paths))
    checks.append(_check_tokens(scanned_paths, inputs.actual_token))
    checks.append(_check_evidence_paths(scanned_paths, workspace_root))
    checks.append(_check_overclaim(scanned_paths))
    checks.append(_check_handoff_gate(inputs.handoff_dir, handoff_json, inputs.review_doc_path))

    status = "PASS" if all(check["status"] == "PASS" for check in checks) else "FAIL"
    return {
        "object_type": "P3EEvidenceVerifierReport",
        "schema": "ai_control_p3e_evidence_verifier_report_v1",
        "stage": "P3-E evidence verifier and handoff safety gate",
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "summary_path": str(inputs.summary_path),
            "scenario_dir": str(inputs.scenario_dir),
            "handoff_dir": str(inputs.handoff_dir),
            "review_doc_path": str(inputs.review_doc_path) if inputs.review_doc_path else None,
            "workspace_root": str(workspace_root),
        },
        "counts": summary.get("counts", {}),
        "scenario_file_count": len(scenario_paths),
        "handoff_json_count": len(handoff_paths),
        "checks": checks,
        "allowed_to_continue": status == "PASS",
        "safe_report_lines": _safe_report_lines(status, checks),
        "boundary": {
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
            "windows_service_added": False,
            "privileged_launch_api_used": False,
            "external_handoff_success_asserted": False,
        },
    }


def load_default_actual_token(discovery_path: Path | None = None) -> str | None:
    path = discovery_path or Path.home() / "AppData" / "Local" / "ai-control" / "host-agent.json"
    if not path.exists():
        return None
    try:
        token = _read_json(path).get("token")
    except Exception:
        return None
    if isinstance(token, str) and token:
        return token
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify P3-D evidence and P3-E handoff safety facts.")
    parser.add_argument("--summary", default="runs_30_scenarios_goal_report.json")
    parser.add_argument("--scenario-dir", default="runs_30_scenarios_goal")
    parser.add_argument("--handoff-dir", default="runs_claude_p3d_review_handoff")
    parser.add_argument(
        "--review-doc",
        default=None,
        help="Optional review note to scan. Reviews are not persisted as local files by default.",
    )
    parser.add_argument("--workspace-root", default=".")
    parser.add_argument("--actual-token", default=None)
    parser.add_argument("--no-host-token", action="store_true")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    review_doc = Path(args.review_doc) if args.review_doc else None
    token = args.actual_token
    if token is None and not args.no_host_token:
        token = load_default_actual_token()

    report = verify_p3e(
        VerifierInputs(
            summary_path=Path(args.summary),
            scenario_dir=Path(args.scenario_dir),
            handoff_dir=Path(args.handoff_dir),
            review_doc_path=review_doc if review_doc and review_doc.exists() else None,
            workspace_root=Path(args.workspace_root),
            actual_token=token,
        )
    )

    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if report["status"] == "PASS" else 1


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _check_counts(summary: dict[str, Any]) -> dict[str, Any]:
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    failures = {
        key: {"expected": expected, "actual": counts.get(key)}
        for key, expected in EXPECTED_COUNTS.items()
        if counts.get(key) != expected
    }
    return _check(
        "p3d_summary_counts",
        not failures,
        "P3-D summary counts match 30/30 PASS and 30 WeChat GUI reports.",
        {"counts": counts, "failures": failures},
    )


def _check_scenarios(paths: list[Path], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [scenario.get("scenario_id") for scenario in scenarios]
    names = [scenario.get("name") for scenario in scenarios]
    statuses = [scenario.get("status") for scenario in scenarios]
    expected_ids = list(range(1, 31))
    failures = {
        "file_count_ok": len(paths) == 30,
        "ids_ok": sorted(ids) == expected_ids,
        "names_unique": len(set(names)) == 30,
        "all_pass": all(status == "PASS" for status in statuses),
    }
    return _check(
        "p3d_scenario_files",
        all(failures.values()),
        "30 unique scenario JSON files are present and all are PASS.",
        {"scenario_file_count": len(paths), "scenario_ids": sorted(ids), "failures": failures},
    )


def _check_wechat_reports(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    missing: list[int] = []
    not_ok: list[int] = []
    for scenario in scenarios:
        sid = int(scenario.get("scenario_id") or 0)
        report = scenario.get("wechat_report")
        if not isinstance(report, dict):
            missing.append(sid)
        elif report.get("do_ok") is not True:
            not_ok.append(sid)
    return _check(
        "p3d_wechat_reports",
        not missing and not not_ok,
        "Every scenario has a recorded WeChat GUI report with do_ok=true.",
        {"missing": missing, "not_ok": not_ok},
    )


def _check_boundaries(
    summary: dict[str, Any],
    scenarios: list[dict[str, Any]],
    handoff_json: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    true_fields: list[dict[str, Any]] = []
    for label, obj in [("summary", summary), *[(f"scenario_{s.get('scenario_id')}", s) for s in scenarios], *handoff_json.items()]:
        for path, value in _walk(obj):
            if path.endswith(".boundary") and isinstance(value, dict):
                for key in BOUNDARY_KEYS:
                    if value.get(key) is True:
                        true_fields.append({"object": label, "path": path, "key": key})
            if path.split(".")[-1] in BOUNDARY_KEYS and value is True:
                true_fields.append({"object": label, "path": path, "key": path.split(".")[-1]})
    return _check(
        "boundary_false",
        not true_fields,
        "Boundary fields remain false across scenario and handoff evidence.",
        {"true_fields": true_fields[:20], "true_field_count": len(true_fields)},
    )


def _check_window_semantics(paths: list[Path]) -> dict[str, Any]:
    matches = _scan_text(paths, WINDOW_SEMANTICS_PATTERN)
    return _check(
        "window_semantics_keywords_absent",
        not matches,
        "No forbidden Win32/window-semantics keywords appear in current reports, scenarios, or handoff evidence.",
        {"match_count": len(matches), "matches": matches[:20]},
    )


def _check_tokens(paths: list[Path], actual_token: str | None) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    matches.extend(_scan_text(paths, TOKEN_FIELD_PATTERN, kind="token_json_field"))
    matches.extend(_scan_text(paths, BEARER_TOKEN_PATTERN, kind="bearer_non_placeholder"))
    if actual_token:
        escaped = re.compile(re.escape(actual_token))
        matches.extend(_scan_text(paths, escaped, kind="actual_token"))
    return _check(
        "token_hygiene",
        not matches,
        "No token field, non-placeholder bearer token, or current host token appears in scanned evidence.",
        {"match_count": len(matches), "matches": matches[:20], "actual_token_checked": bool(actual_token)},
    )


def _check_evidence_paths(paths: list[Path], workspace_root: Path) -> dict[str, Any]:
    refs: set[str] = set()
    for path in paths:
        if path.suffix.lower() != ".json":
            continue
        try:
            obj = _read_json(path)
        except Exception:
            continue
        for _, value in _walk(obj):
            if isinstance(value, str) and _looks_like_path(value):
                refs.add(value)

    missing: list[str] = []
    for ref in refs:
        target = Path(ref)
        if not target.is_absolute():
            target = workspace_root / ref
        if not target.exists():
            missing.append(ref)
    return _check(
        "evidence_paths_exist",
        not missing,
        "All path-like references in scanned JSON evidence resolve to existing files.",
        {"unique_path_refs": len(refs), "missing_count": len(missing), "missing": sorted(missing)[:30]},
    )


def _check_overclaim(paths: list[Path]) -> dict[str, Any]:
    json_paths = [path for path in paths if path.suffix.lower() == ".json"]
    matches = _scan_text(json_paths, STRUCTURED_OVERCLAIM_PATTERN, kind="structured_overclaim")
    return _check(
        "structured_overclaim_absent",
        not matches,
        "Reports contain no structured positive claims of target hit, delivery success, causality, or business success.",
        {"match_count": len(matches), "matches": matches[:20]},
    )


def _check_handoff_gate(
    handoff_dir: Path,
    handoff_json: dict[str, dict[str, Any]],
    review_doc_path: Path | None,
) -> dict[str, Any]:
    required_files = {
        "23_after_open_claude_url.json": "Chrome Claude login-page evidence",
        "27_after_click_purple_ai_icon.json": "wrong-target app evidence",
        "31_wechat_report_claude_login_blocked.json": "operator notification do report",
        "32_after_wechat_report_claude_login_blocked.json": "operator notification after image",
    }
    missing = [name for name in required_files if name not in handoff_json]
    do_not_ok = [
        name
        for name, obj in handoff_json.items()
        if _is_do_result(obj) and obj.get("ok") is not True
    ]
    handoff_files = list(handoff_dir.glob("*.json"))
    if review_doc_path:
        handoff_files.append(review_doc_path)
    positive_claim_matches = _scan_positive_claude_claims(handoff_files)
    review_doc_ok = review_doc_path is None or review_doc_path.exists()
    review_text = ""
    if review_doc_path and review_doc_path.exists():
        review_text = review_doc_path.read_text(encoding="utf-8", errors="replace").lower()
    blocked_marker_present = (
        True if review_doc_path is None else ("handoff blocked" in review_text or "handoff 阻塞" in review_text)
    )
    ok = not missing and not do_not_ok and not positive_claim_matches and review_doc_ok and blocked_marker_present
    return _check(
        "handoff_safety_gate",
        ok,
        "Claude realtime handoff is explicitly blocked, operator notification is recorded, and optional review notes are scanned when supplied.",
        {
            "handoff_status": "blocked_with_operator_notification_recorded" if ok else "incomplete",
            "required_files": required_files,
            "missing_required_files": missing,
            "do_result_not_ok": do_not_ok,
            "positive_claude_review_claim_count": len(positive_claim_matches),
            "review_doc_path": str(review_doc_path) if review_doc_path else None,
            "review_doc_exists": review_doc_ok,
            "local_review_doc_required": False,
            "blocked_marker_present": blocked_marker_present,
            "claude_realtime_review_completed": False,
            "allowed_to_continue_with_conversation_or_external_review": ok,
            "external_handoff_success_asserted": False,
        },
    )


def _check(check_id: str, ok: bool, detail: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": "PASS" if ok else "FAIL",
        "detail": detail,
        "evidence": evidence,
    }


def _safe_report_lines(status: str, checks: list[dict[str, Any]]) -> list[str]:
    failed = [check["id"] for check in checks if check["status"] != "PASS"]
    return [
        f"P3-E verifier status={status}; failed_checks={failed}.",
        "This verifier reads existing JSON/files only; it does not capture, input, OCR, inspect DOM/accessibility/window semantics, or judge business success.",
        "Claude realtime review remains blocked unless an external reviewer supplies a separate review; local subagent review may continue only when the handoff safety gate passes.",
    ]


def _is_do_result(obj: dict[str, Any]) -> bool:
    return obj.get("schema") in {"ai_control_do_result_v1", "ai_control_do_v1"} or obj.get("object_type") in {
        "DoResult",
        "P3ADoResult",
    }


def _looks_like_path(value: str) -> bool:
    if "\n" in value or len(value) > 500:
        return False
    if not PATH_LIKE_PATTERN.search(value):
        return False
    if "*" in value:
        return False
    lowered = value.lower()
    suffixes = (".json", ".png", ".md", ".txt", ".py", ".jsonl")
    if lowered.endswith(suffixes):
        return True
    return "\\media\\" in lowered or "\\runs_host_agent\\" in lowered


def _scan_text(paths: Iterable[Path], pattern: re.Pattern[str], *, kind: str = "match") -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                matches.append({"kind": kind, "path": str(path), "line": line_number, "snippet": line.strip()[:200]})
    return matches


def _scan_positive_claude_claims(paths: Iterable[Path]) -> list[dict[str, Any]]:
    negation_markers = (
        "不得",
        "不能",
        "不应",
        "未完成",
        "没有完成",
        "do not",
        "must not",
        "not claim",
        "never claim",
        "should not",
    )
    matches: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if not POSITIVE_CLAUDE_REVIEW_PATTERN.search(line):
                continue
            if any(marker in lowered or marker in line for marker in negation_markers):
                continue
            matches.append(
                {
                    "kind": "positive_claude_review_claim",
                    "path": str(path),
                    "line": line_number,
                    "snippet": line.strip()[:200],
                }
            )
    return matches


def _walk(value: Any, prefix: str = "$") -> Iterable[tuple[str, Any]]:
    yield prefix, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{prefix}[{index}]")


if __name__ == "__main__":
    raise SystemExit(main())
