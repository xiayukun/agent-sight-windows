from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


RELEASE_READINESS_SCHEMA = "agentsight_github_release_readiness_v1"
REQUIRED_GITIGNORE_PATTERNS = [
    "runs*/",
    "runs*.json",
    "runs*.txt",
    "round*-runs/",
    "round*-*runs/",
    "build/",
    "dist/",
]
CORE_SOURCE_PATHS = [
    "AGENTS.md",
    "README.md",
    "README.en.md",
    "pyproject.toml",
    "src/agentsight/adapters/skill/SKILL.md",
    "docs/SCREEN_LOOK_DO_PROTOCOL.md",
    "docs/user-guide.md",
    "docs/user-guide.en.md",
    "docs/visual-memory-and-attention.md",
    "docs/visual-memory-and-attention.en.md",
    "docs/branding-and-workspace-migration.md",
    "docs/branding-and-workspace-migration.en.md",
]
PUBLICATION_ASSET_PATHS = [
    "LICENSE",
    "SECURITY.md",
    "SECURITY.en.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING.en.md",
    "CHANGELOG.md",
    "CHANGELOG.en.md",
    "PRIVACY.md",
    "PRIVACY.en.md",
    "THIRD-PARTY-NOTICES.md",
    "THIRD-PARTY-NOTICES.en.md",
    "MAINTAINERS.md",
    "MAINTAINERS.en.md",
    ".github/workflows",
    ".github/ISSUE_TEMPLATE",
    "docs/github-launch-checklist.md",
    "docs/github-launch-checklist.en.md",
    "docs/release-checklist.md",
    "docs/release-checklist.en.md",
    "docs/release-notes-template.md",
    "docs/release-notes-template.en.md",
    "docs/repository-profile.md",
    "docs/repository-profile.en.md",
]
PUBLIC_CONTENT_SAFETY_EXPLICIT_PATHS = [
    "README.md",
    "README.en.md",
    "CHANGELOG.md",
    "CHANGELOG.en.md",
    "SECURITY.md",
    "SECURITY.en.md",
    "PRIVACY.md",
    "PRIVACY.en.md",
    "CONTRIBUTING.md",
    "CONTRIBUTING.en.md",
    "THIRD-PARTY-NOTICES.md",
    "THIRD-PARTY-NOTICES.en.md",
    "MAINTAINERS.md",
    "MAINTAINERS.en.md",
    "docs/user-guide.md",
    "docs/user-guide.en.md",
    "docs/repository-profile.md",
    "docs/repository-profile.en.md",
    "docs/release-notes-template.md",
    "docs/release-notes-template.en.md",
]
PUBLIC_CONTENT_SAFETY_TEXT_SUFFIXES = {".md", ".svg", ".txt", ".html", ".json"}
PUBLICATION_DECISION_KEYS = [
    "license_choice",
    "repository_path",
    "evidence_hygiene",
    "release_asset_approval",
    "public_showcase_approval",
    "public_readme_approval",
    "repository_metadata_approval",
    "release_notes_approval",
    "public_content_safety_review",
]
PUBLIC_CONTENT_SAFETY_PATTERNS = [
    ("secret_like", "bearer_token_literal", re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}")),
    ("secret_like", "token_assignment", re.compile(r"\btoken\s*=", re.IGNORECASE)),
    ("secret_like", "api_key_assignment", re.compile(r"\bapi[_-]?key\s*=", re.IGNORECASE)),
    ("secret_like", "password_assignment", re.compile(r"\bpassword\s*=", re.IGNORECASE)),
    ("secret_like", "secret_assignment", re.compile(r"\bsecret\s*=", re.IGNORECASE)),
    ("secret_like", "github_token_prefix", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{8,}")),
    ("secret_like", "openai_key_prefix", re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("private_identity", "email_address", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("private_identity", "windows_user_profile_path", re.compile(r"C:[\\/]+Users[\\/]+", re.IGNORECASE)),
    ("misleading_success", "causal_loop_true", re.compile(r"\bcausal_loop_ok\s*=\s*true\b", re.IGNORECASE)),
    ("misleading_success", "business_success_confirmed", re.compile(r"\bbusiness success confirmed\b", re.IGNORECASE)),
    ("misleading_success", "target_hit_confirmed", re.compile(r"\btarget hit confirmed\b", re.IGNORECASE)),
    ("misleading_success", "delivery_succeeded", re.compile(r"\b(delivery succeeded|message delivered)\b", re.IGNORECASE)),
]


def build_release_readiness_report(root: Path | str = ".") -> dict[str, Any]:
    workspace = Path(root).resolve()
    gitignore = _read_text(workspace / ".gitignore")
    gitignore_patterns = {
        pattern: bool(gitignore is not None and pattern in gitignore)
        for pattern in REQUIRED_GITIGNORE_PATTERNS
    }
    core_sources = _path_presence(workspace, CORE_SOURCE_PATHS)
    publication_assets = _path_presence(workspace, PUBLICATION_ASSET_PATHS)
    evidence_inventory = _evidence_inventory(workspace)
    dist_inventory = _dist_inventory(workspace)
    showcase_inventory = _showcase_inventory(workspace)
    evidence_hygiene_plan = _evidence_hygiene_plan(evidence_inventory=evidence_inventory, dist_inventory=dist_inventory)
    release_asset_review_manifest = _release_asset_review_manifest(dist_inventory=dist_inventory)
    public_content_safety_scan = _public_content_safety_scan(workspace)
    publication_decision_ledger = _publication_decision_ledger(
        workspace=workspace,
        evidence_hygiene_plan=evidence_hygiene_plan,
        release_asset_review_manifest=release_asset_review_manifest,
        showcase_inventory=showcase_inventory,
        public_content_safety_scan=public_content_safety_scan,
    )
    publication_decision_template = _publication_decision_template_inventory(workspace)
    publication_operator_decision_file_validation = _publication_operator_decision_file_validation(workspace)
    attention_items = _attention_items(
        workspace=workspace,
        gitignore_patterns=gitignore_patterns,
        core_sources=core_sources,
        publication_assets=publication_assets,
        evidence_inventory=evidence_inventory,
    )
    ordinary_ai_release_summary = _ordinary_ai_release_summary(
        attention_items=attention_items,
        publication_decision_ledger=publication_decision_ledger,
        publication_decision_template=publication_decision_template,
        publication_operator_decision_file_validation=publication_operator_decision_file_validation,
        public_content_safety_scan=public_content_safety_scan,
        release_asset_review_manifest=release_asset_review_manifest,
    )
    stage_gate_index = _stage_gate_index(workspace)
    operator_action_plan = _operator_action_plan(
        attention_items=attention_items,
        publication_decision_ledger=publication_decision_ledger,
        publication_decision_template=publication_decision_template,
        publication_operator_decision_file_validation=publication_operator_decision_file_validation,
        ordinary_ai_release_summary=ordinary_ai_release_summary,
        stage_gate_index=stage_gate_index,
    )
    ordinary_ai_release_summary["operator_action_plan_summary"] = (
        _operator_action_plan_summary(operator_action_plan)
    )
    ordinary_ai_release_summary["safe_report_lines"].append(
        "Next safe step: ask the operator for the first unresolved publication decision; do not execute blocked actions."
    )
    publication_packet_manifest = _publication_packet_manifest(
        workspace=workspace,
        attention_items=attention_items,
        core_sources=core_sources,
        publication_assets=publication_assets,
        evidence_hygiene_plan=evidence_hygiene_plan,
        dist_inventory=dist_inventory,
        showcase_inventory=showcase_inventory,
        public_content_safety_scan=public_content_safety_scan,
    )
    return {
        "object_type": "AgentSightGitHubReleaseReadinessReport",
        "schema": RELEASE_READINESS_SCHEMA,
        "workspace_root": str(workspace),
        "report_role": "github_publication_preflight_facts",
        "readiness_status": "github_publication_preflight_attention_required" if attention_items else "github_publication_preflight_ready_for_user_review",
        "attention_items": attention_items,
        "workspace_state": {
            "git_repository_present": (workspace / ".git").exists(),
            "git_status_checked": False,
            "git_status_check_reason": "filesystem_preflight_only_no_git_command",
        },
        "gitignore": {
            "path": str(workspace / ".gitignore"),
            "exists": gitignore is not None,
            "required_patterns": gitignore_patterns,
            "missing_required_patterns": [pattern for pattern, present in gitignore_patterns.items() if not present],
        },
        "core_sources": core_sources,
        "publication_assets": publication_assets,
        "evidence_inventory": evidence_inventory,
        "evidence_hygiene_plan": evidence_hygiene_plan,
        "dist_inventory": dist_inventory,
        "release_asset_review_manifest": release_asset_review_manifest,
        "showcase_inventory": showcase_inventory,
        "public_content_safety_scan": public_content_safety_scan,
        "publication_decision_ledger": publication_decision_ledger,
        "publication_decision_template": publication_decision_template,
        "publication_operator_decision_file_validation": publication_operator_decision_file_validation,
        "ordinary_ai_release_summary": ordinary_ai_release_summary,
        "stage_gate_index": stage_gate_index,
        "operator_action_plan": operator_action_plan,
        "publication_packet_manifest": publication_packet_manifest,
        "release_artifact_plan": {
            "source_tree_should_include": [
                "src/",
                "tests/",
                "docs/",
                "packaging/",
                "tools/",
                "README.md",
                "README.en.md",
                "pyproject.toml",
            ],
            "source_tree_should_exclude": [
                "runs* evidence outputs",
                "round*-runs evidence outputs",
                "build/",
                "dist/",
                "__pycache__/",
                "*.pyc",
            ],
            "dist_artifacts_candidate_for_release_assets": dist_inventory["exe_files"],
            "do_not_publish_without_user_review": True,
        },
        "github_publication_next_steps": [
            "initialize_or_restore_git_repository_before_github_push" if not (workspace / ".git").exists() else "inspect_git_status_before_commit",
            "choose_license_before_public_release" if not (workspace / "LICENSE").exists() else "license_present",
            "decide_which_runs_evidence_to_archive_as_release_artifacts",
            "create_github_metadata_after_user_approval",
            "run_full_tests_and_packaging_smoke_before_tag",
        ],
        "non_actions": {
            "files_deleted": False,
            "git_commands_executed": False,
            "network_accessed": False,
            "github_repository_created": False,
            "release_published": False,
            "host_input_sent": False,
        },
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": _boundary_facts(),
    }


def _path_presence(workspace: Path, paths: list[str]) -> dict[str, Any]:
    items = []
    for rel in paths:
        path = workspace / rel
        items.append(
            {
                "path": rel,
                "exists": path.exists(),
                "is_dir": path.is_dir(),
                "is_file": path.is_file(),
            }
        )
    return {
        "items": items,
        "missing": [item["path"] for item in items if not item["exists"]],
    }


def _evidence_inventory(workspace: Path) -> dict[str, Any]:
    root_entries = list(workspace.iterdir()) if workspace.exists() else []
    evidence_dirs = [
        entry for entry in root_entries
        if entry.is_dir() and (entry.name.startswith("runs") or (entry.name.startswith("round") and "runs" in entry.name))
    ]
    evidence_files = [
        entry for entry in root_entries
        if entry.is_file() and entry.name.startswith("runs") and entry.suffix.lower() in {".json", ".txt", ".jsonl", ".gif", ".mp4"}
    ]
    return {
        "root_evidence_dir_count": len(evidence_dirs),
        "root_evidence_file_count": len(evidence_files),
        "root_evidence_file_bytes": sum(_safe_size(path) for path in evidence_files),
        "all_evidence_dir_names": sorted(path.name for path in evidence_dirs),
        "all_evidence_file_names": sorted(path.name for path in evidence_files),
        "sample_evidence_dirs": sorted(path.name for path in evidence_dirs)[:20],
        "sample_evidence_files": sorted(path.name for path in evidence_files)[:20],
        "gitignored_by_current_patterns_expected": True,
    }


def _evidence_hygiene_plan(*, evidence_inventory: dict[str, Any], dist_inventory: dict[str, Any]) -> dict[str, Any]:
    dirs = list(evidence_inventory.get("all_evidence_dir_names") or [])
    files = list(evidence_inventory.get("all_evidence_file_names") or [])
    return {
        "plan_role": "non_destructive_publication_hygiene_plan",
        "plan_applied": False,
        "files_moved": False,
        "files_deleted": False,
        "recommended_archive_root": "local_artifacts/evidence_archive",
        "recommended_release_asset_root": "release_artifacts",
        "source_tree_keep_policy": [
            "keep source, tests, docs, packaging, tools",
            "keep only durable public, user, developer, and release-maintenance docs under docs/",
            "do not keep stage review packages, describe JSON, screenshots, or local subagent review files under docs/",
            "do not keep root runs* or round*-runs artifacts in source commits",
            "do not keep build/ or dist/ in source commits",
        ],
        "evidence_archive_candidates": {
            "dir_count": len(dirs),
            "file_count": len(files),
            "sample_dirs": dirs[:20],
            "sample_files": files[:20],
        },
        "release_asset_candidates": {
            "dist_exe_count": dist_inventory.get("exe_count", 0),
            "dist_exe_names": [item.get("name") for item in dist_inventory.get("exe_files", [])],
        },
        "operator_review_required_before_applying": True,
    }


def _dist_inventory(workspace: Path) -> dict[str, Any]:
    dist = workspace / "dist"
    exe_files = []
    if dist.exists():
        for path in sorted(dist.glob("*.exe")):
            exe_files.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "bytes": _safe_size(path),
                    "sha256": _safe_sha256(path),
                    "hash_algorithm": "sha256",
                    "release_asset_candidate": True,
                }
            )
    return {
        "dist_dir_exists": dist.exists(),
        "exe_count": len(exe_files),
        "exe_files": exe_files,
        "dist_should_remain_out_of_source_tree": True,
    }


def _release_asset_review_manifest(*, dist_inventory: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        {
            "name": item.get("name"),
            "bytes": item.get("bytes", 0),
            "sha256": item.get("sha256"),
            "hash_algorithm": item.get("hash_algorithm", "sha256"),
            "release_asset_candidate": item.get("release_asset_candidate", False),
        }
        for item in dist_inventory.get("exe_files", [])
    ]
    return {
        "manifest_role": "non_destructive_release_asset_review_manifest",
        "manifest_applied": False,
        "files_modified": False,
        "files_deleted": False,
        "assets_uploaded": False,
        "release_published": False,
        "hash_algorithm": "sha256",
        "candidate_count": len(candidates),
        "candidates": candidates,
        "operator_review_required_before_upload": True,
        "signing_status": "not_evaluated",
        "malware_scan_status": "not_performed",
        "checksum_status": "available_for_current_dist_candidates" if candidates else "no_dist_exe_candidates",
        "safe_to_upload_without_operator_review": False,
    }


def _showcase_inventory(workspace: Path) -> dict[str, Any]:
    showcase_dir = workspace / "docs" / "assets" / "public_showcase"
    assets = []
    if showcase_dir.exists():
        for path in sorted(showcase_dir.iterdir()):
            if path.is_file() and path.name != "README.md":
                assets.append(
                    {
                        "name": path.name,
                        "path": str(path),
                        "bytes": _safe_size(path),
                        "synthetic_or_redacted_review_required": True,
                    }
                )
    return {
        "showcase_dir_exists": showcase_dir.exists(),
        "asset_count": len(assets),
        "assets": assets,
        "guide_path": str(workspace / "docs" / "user-guide.md"),
        "assets_require_operator_review_before_public_use": True,
    }


def _publication_packet_manifest(
    *,
    workspace: Path,
    attention_items: list[str],
    core_sources: dict[str, Any],
    publication_assets: dict[str, Any],
    evidence_hygiene_plan: dict[str, Any],
    dist_inventory: dict[str, Any],
    showcase_inventory: dict[str, Any],
    public_content_safety_scan: dict[str, Any],
) -> dict[str, Any]:
    review_inventory = _review_inventory(workspace)
    operator_decisions = _publication_operator_decisions(
        attention_items=attention_items,
        dist_inventory=dist_inventory,
        showcase_inventory=showcase_inventory,
    )
    return {
        "manifest_role": "non_destructive_github_publication_packet_candidate",
        "manifest_status": (
            "github_publication_packet_blocked_pending_operator_decisions"
            if attention_items
            else "github_publication_packet_candidate_ready_for_operator_review"
        ),
        "manifest_applied": False,
        "files_copied": False,
        "files_moved": False,
        "files_deleted": False,
        "git_commands_executed": False,
        "network_accessed": False,
        "github_repository_created": False,
        "release_published": False,
        "source_packet": {
            "recommended_source_paths": [
                "src/",
                "tests/",
                "docs/",
                "packaging/",
                "tools/",
                "README.md",
                "README.en.md",
                "pyproject.toml",
            ],
            "present_core_paths": [item["path"] for item in core_sources["items"] if item["exists"]],
            "missing_core_paths": list(core_sources["missing"]),
            "present_publication_assets": [item["path"] for item in publication_assets["items"] if item["exists"]],
            "missing_publication_assets": list(publication_assets["missing"]),
            "review_doc_count": review_inventory["review_doc_count"],
            "sample_review_docs": review_inventory["sample_review_docs"],
        },
        "evidence_archive_plan": {
            "recommended_archive_root": evidence_hygiene_plan["recommended_archive_root"],
            "archive_candidate_dir_count": evidence_hygiene_plan["evidence_archive_candidates"]["dir_count"],
            "archive_candidate_file_count": evidence_hygiene_plan["evidence_archive_candidates"]["file_count"],
            "operator_review_required_before_applying": evidence_hygiene_plan[
                "operator_review_required_before_applying"
            ],
        },
        "release_asset_candidates": {
            "dist_exe_count": dist_inventory.get("exe_count", 0),
            "dist_exe_names": [item.get("name") for item in dist_inventory.get("exe_files", [])],
            "operator_review_required_before_upload": True,
        },
        "public_showcase_candidates": {
            "asset_count": showcase_inventory.get("asset_count", 0),
            "asset_names": [item.get("name") for item in showcase_inventory.get("assets", [])],
            "operator_review_required_before_public_use": showcase_inventory.get(
                "assets_require_operator_review_before_public_use",
                True,
            ),
        },
        "public_content_safety_summary": {
            "scanned_file_count": public_content_safety_scan["scanned_file_count"],
            "finding_count": public_content_safety_scan["finding_count"],
            "safe_candidate_content_without_operator_review": False,
            "operator_review_required_before_publication": public_content_safety_scan[
                "operator_review_required_before_publication"
            ],
        },
        "blockers": list(attention_items),
        "operator_decisions_required": operator_decisions,
        "safe_to_publish_without_operator_review": False,
        "stage_gate": {
            "requires_independent_subagent_review": True,
            "stage_specific_review_path_declared_in_review_package": False,
            "local_review_files_required": False,
            "release_readiness_does_not_self_certify_stage_pass": True,
        },
    }


def _public_content_safety_scan(workspace: Path) -> dict[str, Any]:
    candidate_paths = _public_content_candidate_paths(workspace)
    findings = []
    for path in candidate_paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "category": "read_error",
                    "pattern": "read_error",
                    "line": None,
                    "matched_text": str(exc),
                }
            )
            continue
        lines = text.splitlines()
        for index, line in enumerate(lines, start=1):
            for category, name, pattern in PUBLIC_CONTENT_SAFETY_PATTERNS:
                match = pattern.search(line)
                if match:
                    findings.append(
                        {
                            "path": path.relative_to(workspace).as_posix(),
                            "category": category,
                            "pattern": name,
                            "line": index,
                            "matched_text": _redact_public_scan_match(match.group(0)),
                        }
                    )
    return {
        "scan_role": "non_destructive_public_content_safety_scan",
        "scan_applied": False,
        "files_modified": False,
        "files_deleted": False,
        "network_accessed": False,
        "scanned_file_count": len(candidate_paths),
        "scanned_paths": [path.relative_to(workspace).as_posix() for path in candidate_paths],
        "finding_count": len(findings),
        "findings": findings,
        "operator_review_required_before_publication": True,
        "scan_does_not_approve_publication": True,
    }


def _public_content_candidate_paths(workspace: Path) -> list[Path]:
    paths = []
    for rel in PUBLIC_CONTENT_SAFETY_EXPLICIT_PATHS:
        path = workspace / rel
        if path.exists() and path.is_file():
            paths.append(path)
    showcase_dir = workspace / "docs" / "assets" / "public_showcase"
    if showcase_dir.exists():
        for path in sorted(showcase_dir.iterdir()):
            if (
                path.is_file()
                and path.suffix.lower() in PUBLIC_CONTENT_SAFETY_TEXT_SUFFIXES
                and path not in paths
            ):
                paths.append(path)
    return sorted(paths)


def _publication_decision_ledger(
    *,
    workspace: Path,
    evidence_hygiene_plan: dict[str, Any],
    release_asset_review_manifest: dict[str, Any],
    showcase_inventory: dict[str, Any],
    public_content_safety_scan: dict[str, Any],
) -> dict[str, Any]:
    decisions = [
        _decision_item(
            key="license_choice",
            status="operator_decision_required" if not (workspace / "LICENSE").exists() else "present_for_review",
            question="Which project license should be used before public release?",
            evidence_paths=["LICENSE", "THIRD-PARTY-NOTICES.md", "THIRD-PARTY-NOTICES.en.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="repository_path",
            status="operator_decision_required" if not (workspace / ".git").exists() else "present_for_review",
            question="Should this workspace become the public repository, or should a clean publication copy be created?",
            evidence_paths=[".git", "docs/github-launch-checklist.md", "docs/repository-profile.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="evidence_hygiene",
            status=(
                "operator_decision_required"
                if evidence_hygiene_plan["evidence_archive_candidates"]["dir_count"]
                or evidence_hygiene_plan["evidence_archive_candidates"]["file_count"]
                else "no_root_evidence_candidates_detected"
            ),
            question="Which local evidence outputs should be archived outside source before publication?",
            evidence_paths=["runs*", "round*-runs", ".gitignore"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="release_asset_approval",
            status=(
                "operator_decision_required"
                if release_asset_review_manifest["candidate_count"]
                else "no_dist_exe_candidates_detected"
            ),
            question="Which executable candidates, if any, may become release assets?",
            evidence_paths=["dist/*.exe", "docs/release-checklist.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="public_showcase_approval",
            status=(
                "operator_decision_required"
                if showcase_inventory["asset_count"]
                else "create_or_select_showcase_assets"
            ),
            question="Which public showcase assets are approved for README or releases?",
            evidence_paths=["docs/user-guide.md", "README.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="public_readme_approval",
            status=(
                "operator_decision_required"
                if (workspace / "README.md").exists() and (workspace / "README.en.md").exists()
                else "readme_pair_missing"
            ),
            question="Are the public README files approved for publication?",
            evidence_paths=["README.md", "README.en.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="repository_metadata_approval",
            status=(
                "operator_decision_required"
                if (workspace / "docs" / "repository-profile.md").exists()
                and (workspace / "docs" / "repository-profile.en.md").exists()
                else "repository_profile_pair_missing"
            ),
            question="Are the GitHub repository name, description, about text, and topics approved?",
            evidence_paths=["docs/repository-profile.md", "docs/repository-profile.en.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="release_notes_approval",
            status=(
                "operator_decision_required"
                if (workspace / "docs" / "release-notes-template.md").exists()
                and (workspace / "docs" / "release-notes-template.en.md").exists()
                else "release_notes_template_pair_missing"
            ),
            question="Are the preview release notes approved?",
            evidence_paths=["docs/release-notes-template.md", "docs/release-notes-template.en.md"],
            blocks_public_release=True,
        ),
        _decision_item(
            key="public_content_safety_review",
            status=(
                "operator_review_required"
                if public_content_safety_scan["finding_count"] == 0
                else "operator_remediation_required"
            ),
            question="Has the operator reviewed public content safety scan findings?",
            evidence_paths=["README.md", "README.en.md", "docs/user-guide.md", "docs/repository-profile.md"],
            blocks_public_release=True,
        ),
    ]
    unresolved = [
        item["key"]
        for item in decisions
        if item["status"]
        in {
            "operator_decision_required",
            "operator_review_required",
            "operator_remediation_required",
            "create_or_select_showcase_assets",
            "draft_missing",
            "readme_pair_missing",
            "repository_profile_pair_missing",
            "release_notes_template_pair_missing",
        }
    ]
    return {
        "ledger_role": "operator_publication_decision_ledger",
        "ledger_applied": False,
        "operator_decisions_written": False,
        "publication_approved": False,
        "release_published": False,
        "github_repository_created": False,
        "decision_count": len(decisions),
        "unresolved_decision_count": len(unresolved),
        "unresolved_decision_keys": unresolved,
        "decisions": decisions,
        "safe_to_publish_without_operator_review": False,
    }


def _publication_decision_template_inventory(workspace: Path) -> dict[str, Any]:
    rel = "docs/release-checklist.md"
    path = workspace / rel
    text = _read_text(path) or ""
    present_keys = sorted(set(re.findall(r"^\s*-?\s*key:\s*([a-z0-9_]+)\s*$", text, re.MULTILINE)))
    non_placeholder_answers = [
        value.strip()
        for value in re.findall(r"^\s*operator_answer:\s*(.+?)\s*$", text, re.MULTILINE)
        if value.strip() not in {"TODO_OPERATOR_DECISION", "null", "None", ""}
    ]
    return {
        "template_role": "blank_operator_publication_decision_template",
        "path": rel,
        "exists": path.exists(),
        "schema_hint_present": "agentsight_publication_operator_decisions_v1" in text,
        "expected_decision_keys": list(PUBLICATION_DECISION_KEYS),
        "present_decision_keys": present_keys,
        "missing_decision_keys": [key for key in PUBLICATION_DECISION_KEYS if key not in present_keys],
        "unexpected_decision_keys": [key for key in present_keys if key not in PUBLICATION_DECISION_KEYS],
        "operator_answers_recorded": False,
        "non_placeholder_operator_answers": non_placeholder_answers,
        "template_applied": False,
        "operator_decisions_written": False,
        "publication_approved": False,
        "safe_to_apply_without_operator_review": False,
    }


def _publication_operator_decision_file_validation(workspace: Path) -> dict[str, Any]:
    rel = "docs/PUBLICATION_OPERATOR_DECISIONS.md"
    path = workspace / rel
    text = _read_text(path) or ""
    answers = _parse_operator_decision_answers(text)
    present_keys = sorted(answers.keys())
    missing_keys = [key for key in PUBLICATION_DECISION_KEYS if key not in answers]
    unexpected_keys = [key for key in present_keys if key not in PUBLICATION_DECISION_KEYS]
    placeholder_keys = sorted(
        [
        key
        for key, value in answers.items()
        if key in PUBLICATION_DECISION_KEYS and _is_placeholder_operator_answer(value)
        ]
    )
    answered_keys = sorted(
        [
        key
        for key, value in answers.items()
        if key in PUBLICATION_DECISION_KEYS and not _is_placeholder_operator_answer(value)
        ]
    )
    if not path.exists():
        validation_status = "operator_decision_file_missing"
    elif missing_keys or unexpected_keys or placeholder_keys:
        validation_status = "operator_decision_file_invalid_or_incomplete"
    else:
        validation_status = "operator_decision_file_complete_for_operator_review"
    return {
        "validation_role": "non_applying_operator_decision_file_validator",
        "path": rel,
        "exists": path.exists(),
        "schema_hint_present": "agentsight_publication_operator_decisions_v1" in text,
        "expected_decision_keys": list(PUBLICATION_DECISION_KEYS),
        "present_decision_keys": present_keys,
        "missing_decision_keys": missing_keys,
        "unexpected_decision_keys": unexpected_keys,
        "placeholder_answer_keys": placeholder_keys,
        "answered_decision_keys": answered_keys,
        "validation_status": validation_status,
        "file_applied": False,
        "operator_decisions_written": False,
        "publication_approved": False,
        "release_published": False,
        "safe_to_apply_without_operator_review": False,
    }


def _ordinary_ai_release_summary(
    *,
    attention_items: list[str],
    publication_decision_ledger: dict[str, Any],
    publication_decision_template: dict[str, Any],
    publication_operator_decision_file_validation: dict[str, Any],
    public_content_safety_scan: dict[str, Any],
    release_asset_review_manifest: dict[str, Any],
) -> dict[str, Any]:
    unresolved = list(publication_decision_ledger["unresolved_decision_keys"])
    next_files = [
        "README.md",
        "README.en.md",
        "docs/repository-profile.md",
        "docs/repository-profile.en.md",
        "docs/release-notes-template.md",
        "docs/release-notes-template.en.md",
        "docs/github-launch-checklist.md",
    ]
    next_questions = [
        decision["question"]
        for decision in publication_decision_ledger["decisions"]
        if decision["key"] in unresolved
    ][:5]
    safe_report_lines = [
        "Release readiness is blocked; do not publish or upload anything.",
        f"Attention items: {', '.join(attention_items) if attention_items else 'none'}",
        f"Unresolved operator decisions: {len(unresolved)}",
        f"Decision file validation: {publication_operator_decision_file_validation['validation_status']}",
        f"Public content safety findings: {public_content_safety_scan['finding_count']}",
        f"Release asset candidates: {release_asset_review_manifest['candidate_count']}",
        "Tool did not choose answers, create GitHub state, upload assets, or publish releases.",
    ]
    return {
        "summary_role": "ordinary_ai_release_readiness_summary",
        "release_ready": False,
        "publication_blocked": bool(attention_items or unresolved),
        "safe_to_publish_without_operator_review": False,
        "attention_items": list(attention_items),
        "unresolved_decision_count": len(unresolved),
        "unresolved_decision_keys": unresolved,
        "next_questions_for_operator": next_questions,
        "next_files_to_review": next_files,
        "decision_template_status": {
            "exists": publication_decision_template["exists"],
            "missing_decision_keys": publication_decision_template["missing_decision_keys"],
            "operator_answers_recorded": publication_decision_template["operator_answers_recorded"],
        },
        "decision_file_validation_status": publication_operator_decision_file_validation[
            "validation_status"
        ],
        "public_content_safety_finding_count": public_content_safety_scan["finding_count"],
        "release_asset_candidate_count": release_asset_review_manifest["candidate_count"],
        "safe_report_lines": safe_report_lines,
        "non_actions": {
            "operator_answers_chosen": False,
            "github_repository_created": False,
            "release_assets_uploaded": False,
            "release_published": False,
            "host_input_sent": False,
        },
    }


def _operator_action_plan(
    *,
    attention_items: list[str],
    publication_decision_ledger: dict[str, Any],
    publication_decision_template: dict[str, Any],
    publication_operator_decision_file_validation: dict[str, Any],
    ordinary_ai_release_summary: dict[str, Any],
    stage_gate_index: dict[str, Any],
) -> dict[str, Any]:
    unresolved_keys = list(publication_decision_ledger["unresolved_decision_keys"])
    decisions = [
        decision
        for decision in publication_decision_ledger["decisions"]
        if decision["key"] in unresolved_keys
    ]
    steps = [
        {
            "step_index": index,
            "decision_key": decision["key"],
            "status": decision["status"],
            "operator_question": decision["question"],
            "evidence_paths": decision["evidence_paths"],
            "operator_action_required": True,
            "tool_may_answer": False,
            "tool_may_execute": False,
            "suggested_record_path": publication_operator_decision_file_validation["path"],
        }
        for index, decision in enumerate(decisions, start=1)
    ]
    blocked_actions = [
        {
            "action": "create_or_push_github_repository",
            "blocked_by_decision_keys": ["repository_path", "license_choice", "public_readme_approval"],
            "tool_may_execute_now": False,
        },
        {
            "action": "upload_release_assets_or_publish_release",
            "blocked_by_decision_keys": [
                "release_asset_approval",
                "release_notes_approval",
                "public_content_safety_review",
            ],
            "tool_may_execute_now": False,
        },
        {
            "action": "move_delete_or_archive_local_evidence",
            "blocked_by_decision_keys": ["evidence_hygiene"],
            "tool_may_execute_now": False,
        },
    ]
    next_files = list(ordinary_ai_release_summary["next_files_to_review"])
    return {
        "plan_role": "operator_review_only_publication_action_plan",
        "plan_status": (
            "blocked_pending_operator_decisions"
            if unresolved_keys
            else "ready_for_final_operator_review"
        ),
        "blocked": bool(unresolved_keys or attention_items),
        "attention_items": list(attention_items),
        "unresolved_decision_count": len(unresolved_keys),
        "unresolved_decision_keys": unresolved_keys,
        "decision_template_path": "docs/release-checklist.md",
        "decision_template_exists": publication_decision_template["exists"],
        "decision_file_path": publication_operator_decision_file_validation["path"],
        "decision_file_validation_status": publication_operator_decision_file_validation[
            "validation_status"
        ],
        "recommended_first_step": (
            "ask_operator_to_fill_publication_decision_file"
            if unresolved_keys
            else "ask_operator_for_final_publication_review"
        ),
        "operator_action_steps": steps,
        "blocked_actions": blocked_actions,
        "next_files_to_review": next_files,
        "stage_gate_reference": {
            "stage_gate_index_present": True,
            "stage_range": stage_gate_index["stage_range"],
            "passed_stage_count": stage_gate_index["passed_stage_count"],
            "missing_artifact_stage_keys": stage_gate_index["missing_artifact_stage_keys"],
        },
        "plan_applied": False,
        "operator_answers_written": False,
        "commands_executed": False,
        "git_commands_executed": False,
        "network_accessed": False,
        "files_moved": False,
        "files_deleted": False,
        "github_repository_created": False,
        "release_assets_uploaded": False,
        "release_published": False,
        "publication_approved": False,
        "safe_to_execute_without_operator_review": False,
    }


def _operator_action_plan_summary(operator_action_plan: dict[str, Any]) -> dict[str, Any]:
    steps = list(operator_action_plan.get("operator_action_steps") or [])
    blocked_actions = list(operator_action_plan.get("blocked_actions") or [])
    first_step = steps[0] if steps else {}
    return {
        "summary_role": "ordinary_ai_operator_action_plan_summary",
        "plan_status": operator_action_plan.get("plan_status"),
        "recommended_first_step": operator_action_plan.get("recommended_first_step"),
        "unresolved_decision_count": operator_action_plan.get("unresolved_decision_count"),
        "first_decision_key": first_step.get("decision_key"),
        "first_operator_question": first_step.get("operator_question"),
        "decision_file_path": operator_action_plan.get("decision_file_path"),
        "blocked_action_count": len(blocked_actions),
        "blocked_action_names": [item.get("action") for item in blocked_actions],
        "tool_may_execute_actions": False,
        "tool_may_answer_for_operator": False,
        "publication_approved": False,
    }


def _stage_gate_index(workspace: Path) -> dict[str, Any]:
    stages: list[dict[str, str]] = []
    hydrated = [_hydrate_stage_gate_entry(workspace, entry) for entry in stages]
    missing = [
        item["stage"]
        for item in hydrated
        if not (
            item["review_package_exists"]
            and item["full_test_report_exists"]
        )
    ]
    return {
        "index_role": "read_only_stage_gate_traceability_index",
        "stage_range": "current_policy_no_persisted_local_stage_reviews",
        "stage_count": len(hydrated),
        "passed_stage_count": sum(1 for item in hydrated if item["review_verdict"] == "PASS"),
        "missing_artifact_stage_keys": missing,
        "index_applied": False,
        "reviews_fabricated": False,
        "local_review_files_required": False,
        "review_storage_policy": "conversation_or_external_review_by_default_no_docs_reviews_files",
        "historical_stage_packages_persisted": False,
        "tests_run_by_index": False,
        "publication_approved": False,
        "stages": hydrated,
    }


def _stage_gate_entry(
    stage: str,
    title: str,
    review_package: str,
    independent_review: str,
    full_test_report: str,
) -> dict[str, str]:
    return {
        "stage": stage,
        "title": title,
        "review_package": f"docs/{review_package}",
        "independent_review": independent_review,
        "full_test_report": full_test_report,
    }


def _hydrate_stage_gate_entry(workspace: Path, entry: dict[str, str]) -> dict[str, Any]:
    test_path = workspace / entry["full_test_report"]
    test_text = _read_text(test_path) or ""
    return {
        **entry,
        "review_package_exists": (workspace / entry["review_package"]).exists(),
        "independent_review_exists": False,
        "independent_review_persisted_locally": False,
        "independent_review_storage_policy": "conversation_or_external_review_by_default_no_local_file",
        "full_test_report_exists": test_path.exists(),
        "review_verdict": "NOT_PERSISTED",
        "full_test_ok": "OK" in test_text,
        "operator_review_required_for_publication": True,
    }


def _review_verdict_from_text(text: str) -> str:
    verdict_line = re.search(
        r"(?im)^\s*(?:conclusion|verdict|结论)\s*:?\s*(conditional pass|pass|fail)[\.。]?\s*$",
        text,
    )
    if verdict_line:
        return verdict_line.group(1).upper()

    conclusion_heading = re.search(r"(?im)^\s*#{1,6}\s*(?:conclusion|verdict|结论)\s*$", text)
    if conclusion_heading:
        after_heading = text[conclusion_heading.end() :]
        for line in after_heading.splitlines():
            normalized = line.strip().strip(".。").upper()
            if not normalized:
                continue
            if normalized in {"PASS", "FAIL", "CONDITIONAL PASS"}:
                return normalized
            break

    return "unknown"


def _parse_operator_decision_answers(text: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    current_key: str | None = None
    for line in text.splitlines():
        key_match = re.match(r"^\s*-?\s*key:\s*([a-z0-9_]+)\s*$", line)
        if key_match:
            current_key = key_match.group(1)
            answers.setdefault(current_key, "")
            continue
        answer_match = re.match(r"^\s*operator_answer:\s*(.*?)\s*$", line)
        if answer_match and current_key:
            answers[current_key] = answer_match.group(1).strip()
    return answers


def _is_placeholder_operator_answer(value: str | None) -> bool:
    normalized = (value or "").strip()
    return normalized in {"", "TODO_OPERATOR_DECISION", "null", "None"}


def _decision_item(
    *,
    key: str,
    status: str,
    question: str,
    evidence_paths: list[str],
    blocks_public_release: bool,
) -> dict[str, Any]:
    return {
        "key": key,
        "status": status,
        "question": question,
        "evidence_paths": evidence_paths,
        "operator_answer": None,
        "blocks_public_release": blocks_public_release,
        "tool_chose_for_operator": False,
    }


def _redact_public_scan_match(value: str) -> str:
    if len(value) <= 16:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _review_inventory(workspace: Path) -> dict[str, Any]:
    review_dir = workspace / "docs" / "reviews"
    review_docs = []
    if review_dir.exists():
        review_docs = sorted(path.name for path in review_dir.glob("*.md") if path.is_file())
    return {
        "review_doc_count": len(review_docs),
        "sample_review_docs": review_docs[-20:],
        "review_dir_exists": review_dir.exists(),
        "local_review_files_required": False,
        "review_storage_policy": "conversation_or_external_review_by_default_no_docs_reviews_files",
    }


def _publication_operator_decisions(
    *,
    attention_items: list[str],
    dist_inventory: dict[str, Any],
    showcase_inventory: dict[str, Any],
) -> list[str]:
    decisions = []
    if "license_missing_before_public_release" in attention_items:
        decisions.append("choose_license_before_public_release")
    if "workspace_not_git_repository" in attention_items:
        decisions.append("choose_repository_creation_or_restore_path")
    if "root_contains_local_evidence_outputs_verify_gitignore_before_commit" in attention_items:
        decisions.append("approve_evidence_archive_or_clean_publication_workspace")
    if dist_inventory.get("exe_count", 0):
        decisions.append("decide_which_dist_executables_become_release_assets")
    if showcase_inventory.get("asset_count", 0):
        decisions.append("approve_showcase_assets_before_public_use")
    else:
        decisions.append("create_or_select_safe_public_showcase_assets")
    if not decisions:
        decisions.append("final_operator_publication_review")
    return decisions


def _attention_items(
    *,
    workspace: Path,
    gitignore_patterns: dict[str, bool],
    core_sources: dict[str, Any],
    publication_assets: dict[str, Any],
    evidence_inventory: dict[str, Any],
) -> list[str]:
    items: list[str] = []
    if not (workspace / ".git").exists():
        items.append("workspace_not_git_repository")
    if any(not present for present in gitignore_patterns.values()):
        items.append("gitignore_missing_required_artifact_patterns")
    if core_sources["missing"]:
        items.append("core_release_source_or_docs_missing")
    if "LICENSE" in publication_assets["missing"]:
        items.append("license_missing_before_public_release")
    if ".github/workflows" in publication_assets["missing"]:
        items.append("github_actions_workflow_missing")
    if evidence_inventory["root_evidence_dir_count"] or evidence_inventory["root_evidence_file_count"]:
        items.append("root_contains_local_evidence_outputs_verify_gitignore_before_commit")
    return items


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _boundary_facts() -> dict[str, bool]:
    return {
        "ocr_used": False,
        "clipboard_used": False,
        "accessibility_tree_used": False,
        "dom_used": False,
        "window_semantics_used": False,
        "business_success_judged": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect GitHub release readiness facts for AgentSight.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--output")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print/write only ordinary_ai_release_summary instead of the full release-readiness report.",
    )
    args = parser.parse_args(argv)
    report = build_release_readiness_report(Path(args.root))
    payload = report["ordinary_ai_release_summary"] if args.summary_only else report
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
