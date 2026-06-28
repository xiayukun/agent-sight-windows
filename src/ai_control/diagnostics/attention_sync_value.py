from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ai_control.tray.state import boundary_facts, default_discovery_file, read_json_file


ATTENTION_SYNC_SCHEMA = "ai_control_attention_sync_value_proof_v1"
ATTENTION_VALUE_COMPARISON_SCHEMA = "ai_control_attention_value_comparison_v1"


class PublicHostClient:
    def __init__(self, *, discovery: dict[str, Any], caller_id: str) -> None:
        self.discovery = discovery
        self.caller_id = caller_id
        self.url = str(discovery.get("url") or "").rstrip("/")
        self.token = str(discovery.get("token") or "")

    def screen(self) -> dict[str, Any]:
        return self._post("/screen", {"v": "V1", "op": "screen"})

    def look(
        self,
        *,
        request_id: str,
        region: dict[str, int],
        scale_down: int = 1,
        src: dict[str, Any] | None = None,
        q: str = "frame",
        max_artifacts: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "v": "V1",
            "id": request_id,
            "op": "look",
            "q": q,
            "src": src or {"type": "screen", "t": "latest"},
            "r": region,
            "scale_down": scale_down,
        }
        if max_artifacts is not None:
            payload["max_artifacts"] = max_artifacts
        return self._post("/look", payload)

    def do(self, *, request_id: str, basis: dict[str, Any], seq: list[Any], post_observe: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "v": "V1",
            "id": request_id,
            "op": "do",
            "basis": basis,
            "seq": seq,
        }
        if post_observe is not None:
            payload["post_observe"] = post_observe
        return self._post("/do", payload)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.url or not self.token:
            raise RuntimeError("Host Agent discovery requires url and token")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.url + path,
            data=data,
            headers={
                "Authorization": f"Bearer {self.token}",
                "X-AI-Control-Caller": self.caller_id,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read().decode("utf-8", errors="replace")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            parsed = json.loads(body) if body else {}
            return {
                "ok": False,
                "status": "http_error",
                "http_status": exc.code,
                "body": parsed,
                "error": str(exc),
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": boundary_facts(),
            }


def run_attention_sync_value_proof(
    *,
    region: dict[str, int],
    target_view_x: int,
    target_view_y: int,
    caller_id: str,
    coordinate_source: str,
    post_observe: dict[str, Any],
    scale_down: int = 1,
    cleanup_key: str | None = None,
    discovery_file: Path | None = None,
    output: Path | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    started_at_ms = _now_ms()
    discovery = getattr(client, "discovery", None)
    if client is None:
        discovery_path = discovery_file or default_discovery_file()
        discovery = read_json_file(discovery_path)
        if not isinstance(discovery, dict) or not discovery.get("url") or not discovery.get("token"):
            report = _blocked_report(
                started_at_ms=started_at_ms,
                status="blocked_discovery_missing_or_unauthenticated",
                discovery_file=discovery_path,
                region=region,
                target_view_x=target_view_x,
                target_view_y=target_view_y,
                coordinate_source=coordinate_source,
            )
            _write_report(report, output)
            return report
        client = PublicHostClient(discovery=discovery, caller_id=caller_id)

    screen = client.screen()
    look = client.look(request_id="attention-sync-look-before", region=region, scale_down=scale_down)
    view = look.get("view") if isinstance(look.get("view"), dict) else {}
    view_id = view.get("id")
    if not view_id:
        report = _blocked_report(
            started_at_ms=started_at_ms,
            status="blocked_look_failed",
            discovery_file=discovery_file or default_discovery_file(),
            region=region,
            target_view_x=target_view_x,
            target_view_y=target_view_y,
            coordinate_source=coordinate_source,
            screen=screen,
            look=look,
        )
        _write_report(report, output)
        return report

    do_result = client.do(
        request_id="attention-sync-do",
        basis={"view_id": view_id},
        seq=[
            {"t": "move", "x": target_view_x, "y": target_view_y, "coord": "view", "move": "instant"},
            {"t": "click", "b": "left"},
        ],
        post_observe=post_observe,
    )
    after_look = client.look(request_id="attention-sync-look-after", region=region, scale_down=scale_down)
    cleanup = None
    if cleanup_key:
        cleanup = client.do(
            request_id="attention-sync-cleanup",
            basis={"view_id": view_id},
            seq=[{"t": "key", "key": cleanup_key}],
        )

    report = {
        "object_type": "AIControlAttentionSyncValueProof",
        "schema": ATTENTION_SYNC_SCHEMA,
        "proof_status": "recorded",
        "started_at_ms": started_at_ms,
        "ended_at_ms": _now_ms(),
        "public_flow": ["screen", "look", "do(post_observe)", "look(optional)"],
        "cleanup_is_separate_from_proof_window": bool(cleanup_key),
        "caller_id_present": bool(caller_id),
        "coordinate_source": coordinate_source,
        "coordinate_source_policy": "external_ai_or_operator_visual_selection_only",
        "tool_selected_target_semantics": False,
        "screen": _screen_summary(screen),
        "region": region,
        "target": {
            "coord": "view",
            "x": target_view_x,
            "y": target_view_y,
            "tool_asserts_target_hit": False,
        },
        "discovery": _redact_discovery(discovery),
        "look_before": _look_summary(look),
        "do_summary": summarize_do_result(do_result),
        "look_after": _look_summary(after_look),
        "cleanup": _cleanup_summary(cleanup),
        "attention_value_facts": _attention_value_facts(do_result),
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "semantic_result_judged": False,
        "host_input_sent": bool((do_result.get("input") or {}).get("sent")) if isinstance(do_result, dict) else False,
        "host_sent_event_count": int((do_result.get("input") or {}).get("host_event_count") or 0) if isinstance(do_result, dict) else 0,
        "boundary": boundary_facts(),
    }
    report["safe_report_lines"] = _safe_report_lines(report)
    _write_report(report, output)
    return report


def run_attention_value_comparison(
    *,
    attention_region: dict[str, int],
    target_view_x: int,
    target_view_y: int,
    caller_id: str,
    coordinate_source: str,
    post_observe: dict[str, Any],
    full_region: dict[str, int] | None = None,
    attention_scale_down: int = 1,
    coarse_scale_down: int = 5,
    baseline_frame_count: int = 6,
    baseline_interval_ms: int = 120,
    cleanup_key: str | None = None,
    discovery_file: Path | None = None,
    output: Path | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    started_at_ms = _now_ms()
    discovery = getattr(client, "discovery", None)
    if client is None:
        discovery_path = discovery_file or default_discovery_file()
        discovery = read_json_file(discovery_path)
        if not isinstance(discovery, dict) or not discovery.get("url") or not discovery.get("token"):
            report = _blocked_comparison_report(
                started_at_ms=started_at_ms,
                status="blocked_discovery_missing_or_unauthenticated",
                discovery_file=discovery_path,
                attention_region=attention_region,
                target_view_x=target_view_x,
                target_view_y=target_view_y,
                coordinate_source=coordinate_source,
            )
            _write_report(report, output)
            return report
        client = PublicHostClient(discovery=discovery, caller_id=caller_id)

    screen = client.screen()
    resolved_full_region = full_region or _full_region_from_screen(screen)
    if resolved_full_region is None:
        report = _blocked_comparison_report(
            started_at_ms=started_at_ms,
            status="blocked_screen_virtual_region_unavailable",
            discovery_file=discovery_file or default_discovery_file(),
            attention_region=attention_region,
            target_view_x=target_view_x,
            target_view_y=target_view_y,
            coordinate_source=coordinate_source,
            screen=screen,
        )
        _write_report(report, output)
        return report

    target_screen = {
        "x": int(attention_region["x"]) + int(target_view_x) * int(attention_scale_down),
        "y": int(attention_region["y"]) + int(target_view_y) * int(attention_scale_down),
    }
    baseline_target = {
        "coord": "view",
        "x": int(target_screen["x"]) - int(resolved_full_region["x"]),
        "y": int(target_screen["y"]) - int(resolved_full_region["y"]),
    }

    baseline = _run_baseline_fullscreen_path(
        client=client,
        full_region=resolved_full_region,
        baseline_target=baseline_target,
        baseline_frame_count=baseline_frame_count,
        baseline_interval_ms=baseline_interval_ms,
        cleanup_key=cleanup_key,
    )
    attention = _run_attention_local_path(
        client=client,
        full_region=resolved_full_region,
        attention_region=attention_region,
        target_view_x=target_view_x,
        target_view_y=target_view_y,
        post_observe=post_observe,
        attention_scale_down=attention_scale_down,
        coarse_scale_down=coarse_scale_down,
        cleanup_key=cleanup_key,
    )

    baseline_metrics = _summarize_media_items(baseline["media_items"])
    attention_metrics = _summarize_media_items(attention["media_items"])
    baseline_input = baseline["do_summary"].get("input") if isinstance(baseline["do_summary"].get("input"), dict) else {}
    attention_input = attention["do_summary"].get("input") if isinstance(attention["do_summary"].get("input"), dict) else {}
    comparison = _comparison_metrics(
        baseline_metrics=baseline_metrics,
        attention_metrics=attention_metrics,
        baseline_frame_count=baseline_frame_count,
        attention_do_summary=attention["do_summary"],
    )
    report = {
        "object_type": "AIControlAttentionValueComparison",
        "schema": ATTENTION_VALUE_COMPARISON_SCHEMA,
        "comparison_status": "recorded",
        "started_at_ms": started_at_ms,
        "ended_at_ms": _now_ms(),
        "public_flow": {
            "baseline": ["screen", "look(fullscreen)", "do", "look(fullscreen)*N", "do(cleanup optional)"],
            "attention": ["screen", "look(coarse fullscreen)", "look(local)", "do(post_observe)", "look(q=diff)", "do(cleanup optional)"],
        },
        "caller_id_present": bool(caller_id),
        "coordinate_source": coordinate_source,
        "coordinate_source_policy": "external_ai_or_operator_visual_selection_only",
        "tool_selected_target_semantics": False,
        "screen": _screen_summary(screen),
        "discovery": _redact_discovery(discovery),
        "task": {
            "name": "real_gui_attention_value_comparison",
            "full_region": resolved_full_region,
            "attention_region": attention_region,
            "target": {
                "screen": target_screen,
                "baseline_view": baseline_target,
                "attention_view": {"coord": "view", "x": target_view_x, "y": target_view_y},
                "tool_asserts_target_hit": False,
            },
        },
        "baseline": {
            "path_kind": "fullscreen_full_resolution_after_each_step",
            "look_before": _look_summary(baseline["look_before"]),
            "do_summary": baseline["do_summary"],
            "sampled_looks": [_look_summary(item) for item in baseline["sampled_looks"]],
            "cleanup": _cleanup_summary(baseline["cleanup"]),
            "media_items": baseline["media_items"],
            "metrics": baseline_metrics,
        },
        "attention": {
            "path_kind": "coarse_fullscreen_then_local_post_observe_then_diff",
            "coarse_look": _look_summary(attention["coarse_look"]),
            "local_look_before": _look_summary(attention["local_look_before"]),
            "do_summary": attention["do_summary"],
            "diff_look": _diff_look_summary(attention["diff_look"]),
            "cleanup": _cleanup_summary(attention["cleanup"]),
            "media_items": attention["media_items"],
            "metrics": attention_metrics,
        },
        "comparison": comparison,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "semantic_result_judged": False,
        "host_input_sent": bool(baseline_input.get("sent") or attention_input.get("sent")),
        "host_sent_event_count": int((baseline_input.get("host_event_count") or 0) + (attention_input.get("host_event_count") or 0)),
        "boundary": boundary_facts(),
    }
    report["safe_report_lines"] = _comparison_safe_report_lines(report)
    _write_report(report, output)
    return report


def _run_baseline_fullscreen_path(
    *,
    client: Any,
    full_region: dict[str, int],
    baseline_target: dict[str, int],
    baseline_frame_count: int,
    baseline_interval_ms: int,
    cleanup_key: str | None,
) -> dict[str, Any]:
    look_before = client.look(request_id="attention-value-baseline-before", region=full_region, scale_down=1)
    view = look_before.get("view") if isinstance(look_before.get("view"), dict) else {}
    view_id = view.get("id")
    do_result = client.do(
        request_id="attention-value-baseline-do",
        basis={"view_id": view_id},
        seq=[
            {"t": "move", "x": baseline_target["x"], "y": baseline_target["y"], "coord": "view", "move": "instant"},
            {"t": "click", "b": "left"},
        ],
    )
    sampled_looks = []
    for index in range(max(0, int(baseline_frame_count))):
        if baseline_interval_ms > 0:
            time.sleep(float(baseline_interval_ms) / 1000.0)
        sampled_looks.append(
            client.look(
                request_id=f"attention-value-baseline-frame-{index + 1}",
                region=full_region,
                scale_down=1,
            )
        )
    cleanup = None
    if cleanup_key:
        cleanup = client.do(
            request_id="attention-value-baseline-cleanup",
            basis={"view_id": view_id},
            seq=[{"t": "key", "key": cleanup_key}],
        )
    media_items = _media_items_from_look(look_before, role="baseline.before")
    for index, look in enumerate(sampled_looks, start=1):
        media_items.extend(_media_items_from_look(look, role=f"baseline.frame.{index}"))
    return {
        "look_before": look_before,
        "do_summary": summarize_do_result(do_result),
        "sampled_looks": sampled_looks,
        "cleanup": cleanup,
        "media_items": media_items,
    }


def _run_attention_local_path(
    *,
    client: Any,
    full_region: dict[str, int],
    attention_region: dict[str, int],
    target_view_x: int,
    target_view_y: int,
    post_observe: dict[str, Any],
    attention_scale_down: int,
    coarse_scale_down: int,
    cleanup_key: str | None,
) -> dict[str, Any]:
    coarse_look = client.look(
        request_id="attention-value-attention-coarse",
        region=full_region,
        scale_down=coarse_scale_down,
    )
    local_look_before = client.look(
        request_id="attention-value-attention-local-before",
        region=attention_region,
        scale_down=attention_scale_down,
    )
    local_view = local_look_before.get("view") if isinstance(local_look_before.get("view"), dict) else {}
    view_id = local_view.get("id")
    do_result = client.do(
        request_id="attention-value-attention-do",
        basis={"view_id": view_id},
        seq=[
            {"t": "move", "x": target_view_x, "y": target_view_y, "coord": "view", "move": "instant"},
            {"t": "click", "b": "left"},
        ],
        post_observe=post_observe,
    )
    view_w = int(local_view.get("w") or max(1, int(attention_region["w"]) // max(1, int(attention_scale_down))))
    view_h = int(local_view.get("h") or max(1, int(attention_region["h"]) // max(1, int(attention_scale_down))))
    diff_look = client.look(
        request_id="attention-value-attention-diff",
        region={"x": 0, "y": 0, "w": view_w, "h": view_h},
        scale_down=1,
        src={"type": "view", "view_id": view_id},
        q="diff",
        max_artifacts=1,
    )
    cleanup = None
    if cleanup_key:
        cleanup = client.do(
            request_id="attention-value-attention-cleanup",
            basis={"view_id": view_id},
            seq=[{"t": "key", "key": cleanup_key}],
        )
    media_items = _media_items_from_look(coarse_look, role="attention.coarse")
    media_items.extend(_media_items_from_look(local_look_before, role="attention.local_before"))
    media_items.extend(_media_items_from_diff(diff_look, role="attention.diff"))
    return {
        "coarse_look": coarse_look,
        "local_look_before": local_look_before,
        "do_summary": summarize_do_result(do_result),
        "diff_look": diff_look,
        "cleanup": cleanup,
        "media_items": media_items,
    }


def summarize_do_result(do_result: dict[str, Any]) -> dict[str, Any]:
    post = do_result.get("post_observe") if isinstance(do_result.get("post_observe"), dict) else {}
    post_summary = post.get("summary") if isinstance(post.get("summary"), dict) else {}
    return {
        "ok": bool(do_result.get("ok")),
        "status": do_result.get("status"),
        "input": do_result.get("input"),
        "step_count": len(do_result.get("steps") or []),
        "steps": [
            {
                "i": step.get("i"),
                "ok": step.get("ok"),
                "route": step.get("route"),
                "host_event_count": step.get("host_event_count"),
                "post_observe_fast_path": step.get("post_observe_fast_path"),
                "after_observation_skipped": step.get("after_observation_skipped"),
            }
            for step in (do_result.get("steps") or [])
            if isinstance(step, dict)
        ],
        "post_observe_present": bool(post),
        "post_observe_status": post.get("status"),
        "sampled_frame_count": post.get("sampled_frame_count"),
        "comparison_count": post.get("comparison_count"),
        "post_observe_summary": {
            "changed": post_summary.get("changed"),
            "changed_frame_indexes": post_summary.get("changed_frame_indexes"),
            "max_changed_pixel_ratio": post_summary.get("max_changed_pixel_ratio"),
            "largest_changed_bbox": post_summary.get("largest_changed_bbox"),
            "stability_status": post_summary.get("stability_status"),
            "stable": post_summary.get("stable"),
            "still_changing": post_summary.get("still_changing"),
            "stopped_early": post_summary.get("stopped_early"),
            "sampling_stop_reason": post_summary.get("sampling_stop_reason"),
            "tool_asserts_semantic_change": post_summary.get("tool_asserts_semantic_change", False),
            "tool_asserts_business_success": post_summary.get("tool_asserts_business_success", False),
        },
        "returns_images": post.get("returns_images", False),
        "raw_media_returned": post.get("raw_media_returned", False),
        "derived_review_artifact_returned": post.get("derived_review_artifact_returned", False),
        "raw_frames_are_canonical_evidence": post.get("raw_frames_are_canonical_evidence"),
        "tool_asserts_target_hit": do_result.get("tool_asserts_target_hit", False),
        "tool_asserts_business_success": do_result.get("tool_asserts_business_success", False),
        "input_visual_relationship_judgment": do_result.get("input_visual_relationship_judgment", "external_review_only"),
        "boundary": do_result.get("boundary", boundary_facts()),
    }


def parse_region(text: str) -> dict[str, int]:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 4:
        raise ValueError("region must be x,y,w,h")
    try:
        x, y, w, h = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("region must contain integers") from exc
    if w <= 0 or h <= 0:
        raise ValueError("region width and height must be positive")
    return {"x": x, "y": y, "w": w, "h": h}


def _full_region_from_screen(screen: dict[str, Any]) -> dict[str, int] | None:
    virtual = screen.get("virtual") if isinstance(screen.get("virtual"), dict) else {}
    try:
        return {
            "x": int(virtual.get("x", 0)),
            "y": int(virtual.get("y", 0)),
            "w": int(virtual["w"]),
            "h": int(virtual["h"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _media_items_from_look(look: dict[str, Any], *, role: str) -> list[dict[str, Any]]:
    view = look.get("view") if isinstance(look.get("view"), dict) else {}
    item = _media_item_from_view(view, role=role, media_kind="raw_view", raw_canonical=True, derived_review=False)
    return [item] if item else []


def _media_items_from_diff(diff_look: dict[str, Any], *, role: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    after_view = diff_look.get("after_view") if isinstance(diff_look.get("after_view"), dict) else {}
    after_item = _media_item_from_view(after_view, role=f"{role}.after_view", media_kind="raw_view", raw_canonical=True, derived_review=False)
    if after_item:
        items.append(after_item)
    for index, artifact in enumerate(diff_look.get("artifacts") or [], start=1):
        if not isinstance(artifact, dict):
            continue
        artifact_path = artifact.get("media_path_abs") or artifact.get("path")
        item = _media_item(
            role=f"{role}.artifact.{index}",
            media_kind=str(artifact.get("artifact_type") or "derived_review_artifact"),
            path=artifact_path,
            width=None,
            height=None,
            raw_canonical=bool(artifact.get("canonical")),
            derived_review=True,
            integrity_truth_source=bool(artifact.get("integrity_truth_source")),
        )
        if item:
            items.append(item)
    return items


def _media_item_from_view(
    view: dict[str, Any],
    *,
    role: str,
    media_kind: str,
    raw_canonical: bool,
    derived_review: bool,
) -> dict[str, Any] | None:
    return _media_item(
        role=role,
        media_kind=media_kind,
        path=view.get("path") or view.get("media_path_abs"),
        width=_int_or_none(view.get("w")),
        height=_int_or_none(view.get("h")),
        raw_canonical=raw_canonical,
        derived_review=derived_review,
        integrity_truth_source=raw_canonical and not derived_review,
    )


def _media_item(
    *,
    role: str,
    media_kind: str,
    path: Any,
    width: int | None,
    height: int | None,
    raw_canonical: bool,
    derived_review: bool,
    integrity_truth_source: bool,
) -> dict[str, Any] | None:
    if not path:
        return None
    media_path = Path(str(path))
    png_dimensions = _png_dimensions(media_path)
    if width is None and png_dimensions is not None:
        width = png_dimensions[0]
    if height is None and png_dimensions is not None:
        height = png_dimensions[1]
    byte_count = _file_size(media_path)
    pixel_count = int(width) * int(height) if width is not None and height is not None else 0
    return {
        "role": role,
        "media_kind": media_kind,
        "path": str(media_path),
        "bytes": byte_count,
        "width": width,
        "height": height,
        "pixel_count": pixel_count,
        "raw_canonical": bool(raw_canonical),
        "derived_review": bool(derived_review),
        "integrity_truth_source": bool(integrity_truth_source),
        "tool_asserts_visual_meaning": False,
    }


def _summarize_media_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    raw_items = [item for item in items if item.get("raw_canonical")]
    derived_items = [item for item in items if item.get("derived_review")]
    return {
        "available_image_count": len(items),
        "raw_canonical_image_count": len(raw_items),
        "derived_review_image_count": len(derived_items),
        "available_image_bytes": sum(int(item.get("bytes") or 0) for item in items),
        "raw_canonical_image_bytes": sum(int(item.get("bytes") or 0) for item in raw_items),
        "derived_review_image_bytes": sum(int(item.get("bytes") or 0) for item in derived_items),
        "token_proxy_pixels": sum(int(item.get("pixel_count") or 0) for item in raw_items),
        "derived_review_pixels": sum(int(item.get("pixel_count") or 0) for item in derived_items),
        "missing_file_count": sum(1 for item in items if int(item.get("bytes") or 0) <= 0),
    }


def _comparison_metrics(
    *,
    baseline_metrics: dict[str, Any],
    attention_metrics: dict[str, Any],
    baseline_frame_count: int,
    attention_do_summary: dict[str, Any],
) -> dict[str, Any]:
    attention_sampled_frames = int(attention_do_summary.get("sampled_frame_count") or 0)
    baseline_bytes = int(baseline_metrics.get("available_image_bytes") or 0)
    attention_bytes = int(attention_metrics.get("available_image_bytes") or 0)
    baseline_pixels = int(baseline_metrics.get("token_proxy_pixels") or 0)
    attention_pixels = int(attention_metrics.get("token_proxy_pixels") or 0)
    return {
        "baseline_available_image_bytes": baseline_bytes,
        "attention_available_image_bytes": attention_bytes,
        "available_image_byte_savings_ratio": _savings_ratio(baseline_bytes, attention_bytes),
        "baseline_raw_canonical_image_bytes": int(baseline_metrics.get("raw_canonical_image_bytes") or 0),
        "attention_raw_canonical_image_bytes": int(attention_metrics.get("raw_canonical_image_bytes") or 0),
        "baseline_token_proxy_pixels": baseline_pixels,
        "attention_token_proxy_pixels": attention_pixels,
        "token_proxy_pixel_savings_ratio": _savings_ratio(baseline_pixels, attention_pixels),
        "baseline_returned_image_count": int(baseline_metrics.get("available_image_count") or 0),
        "attention_returned_image_count": int(attention_metrics.get("available_image_count") or 0),
        "baseline_fullscreen_sample_count_after_input": int(baseline_frame_count),
        "attention_post_observe_sampled_frame_count": attention_sampled_frames,
        "attention_post_observe_returns_images": bool(attention_do_summary.get("returns_images")),
        "attention_post_observe_metadata_only": bool(attention_do_summary.get("post_observe_present")) and not bool(attention_do_summary.get("returns_images")),
        "tool_asserts_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
    }


def _diff_look_summary(diff_look: dict[str, Any]) -> dict[str, Any]:
    after_view = diff_look.get("after_view") if isinstance(diff_look.get("after_view"), dict) else {}
    summary = diff_look.get("summary") if isinstance(diff_look.get("summary"), dict) else {}
    return {
        "ok": diff_look.get("ok"),
        "status": diff_look.get("status"),
        "type": diff_look.get("type"),
        "mode": diff_look.get("mode"),
        "after_view_id": after_view.get("id"),
        "after_path": after_view.get("path") or after_view.get("media_path_abs"),
        "summary": {
            "status": summary.get("status"),
            "changed": summary.get("changed"),
            "frame_pairs": summary.get("frame_pairs"),
            "computed_comparison_count": summary.get("computed_comparison_count"),
            "max_changed_pixel_ratio": summary.get("max_changed_pixel_ratio"),
            "largest_changed_bbox": summary.get("largest_changed_bbox"),
        },
        "artifact_count": len(diff_look.get("artifacts") or []),
        "raw_media_returned": diff_look.get("raw_media_returned", False),
        "derived_review_artifact_returned": diff_look.get("derived_review_artifact_returned", False),
        "derived_artifacts_are_canonical": diff_look.get("derived_artifacts_are_canonical", False),
        "tool_asserts_semantic_change": diff_look.get("tool_asserts_semantic_change", False),
        "tool_asserts_target_hit": diff_look.get("tool_asserts_target_hit", False),
        "tool_asserts_business_success": diff_look.get("tool_asserts_business_success", False),
    }


def _attention_value_facts(do_result: dict[str, Any]) -> dict[str, Any]:
    summary = summarize_do_result(do_result)
    post_summary = summary["post_observe_summary"]
    return {
        "post_observe_generated": summary["post_observe_present"] and summary["post_observe_status"] == "generated",
        "metadata_available_before_extra_look_interpretation": bool(summary["post_observe_present"]),
        "sampled_frame_count": summary.get("sampled_frame_count"),
        "comparison_count": summary.get("comparison_count"),
        "stability_status": post_summary.get("stability_status"),
        "changed_frame_indexes": post_summary.get("changed_frame_indexes"),
        "max_changed_pixel_ratio": post_summary.get("max_changed_pixel_ratio"),
        "largest_changed_bbox": post_summary.get("largest_changed_bbox"),
        "tool_asserts_semantic_change": False,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
    }


def _look_summary(look: dict[str, Any]) -> dict[str, Any]:
    view = look.get("view") if isinstance(look.get("view"), dict) else {}
    return {
        "ok": look.get("ok"),
        "status": look.get("status"),
        "view_id": view.get("id"),
        "path": view.get("path") or view.get("media_path_abs"),
        "raw_canonical": view.get("raw_canonical"),
        "host_input_sent": look.get("host_input_sent", False),
        "host_sent_event_count": look.get("host_sent_event_count", 0),
    }


def _screen_summary(screen: dict[str, Any]) -> dict[str, Any]:
    virtual = screen.get("virtual") if isinstance(screen.get("virtual"), dict) else {}
    return {
        "ok": screen.get("ok", True),
        "virtual": virtual,
        "host_input_sent": screen.get("host_input_sent", False),
        "host_sent_event_count": screen.get("host_sent_event_count", 0),
    }


def _cleanup_summary(cleanup: dict[str, Any] | None) -> dict[str, Any]:
    if cleanup is None:
        return {"attempted": False, "host_input_sent": False, "host_sent_event_count": 0}
    input_summary = cleanup.get("input") if isinstance(cleanup.get("input"), dict) else {}
    return {
        "attempted": True,
        "ok": cleanup.get("ok"),
        "status": cleanup.get("status"),
        "host_input_sent": bool(input_summary.get("sent")),
        "host_sent_event_count": int(input_summary.get("host_event_count") or 0),
        "tool_asserts_business_success": cleanup.get("tool_asserts_business_success", False),
        "boundary": cleanup.get("boundary", boundary_facts()),
    }


def _blocked_report(
    *,
    started_at_ms: int,
    status: str,
    discovery_file: Path,
    region: dict[str, int],
    target_view_x: int,
    target_view_y: int,
    coordinate_source: str,
    screen: dict[str, Any] | None = None,
    look: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlAttentionSyncValueProof",
        "schema": ATTENTION_SYNC_SCHEMA,
        "proof_status": status,
        "started_at_ms": started_at_ms,
        "ended_at_ms": _now_ms(),
        "discovery_file": str(discovery_file),
        "region": region,
        "target": {"coord": "view", "x": target_view_x, "y": target_view_y, "tool_asserts_target_hit": False},
        "coordinate_source": coordinate_source,
        "screen": screen,
        "look_before": _look_summary(look or {}) if look is not None else None,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "semantic_result_judged": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _blocked_comparison_report(
    *,
    started_at_ms: int,
    status: str,
    discovery_file: Path,
    attention_region: dict[str, int],
    target_view_x: int,
    target_view_y: int,
    coordinate_source: str,
    screen: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "object_type": "AIControlAttentionValueComparison",
        "schema": ATTENTION_VALUE_COMPARISON_SCHEMA,
        "comparison_status": status,
        "started_at_ms": started_at_ms,
        "ended_at_ms": _now_ms(),
        "discovery_file": str(discovery_file),
        "attention_region": attention_region,
        "target": {"coord": "view", "x": target_view_x, "y": target_view_y, "tool_asserts_target_hit": False},
        "coordinate_source": coordinate_source,
        "screen": screen,
        "tool_asserts_business_success": False,
        "tool_asserts_causality": False,
        "tool_asserts_target_hit": False,
        "semantic_result_judged": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def _redact_discovery(discovery: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(discovery, dict):
        return None
    safe = {key: value for key, value in discovery.items() if key not in {"token", "health_at_start"}}
    if "token" in discovery:
        safe["token"] = "<redacted>"
    return safe


def _safe_report_lines(report: dict[str, Any]) -> list[str]:
    facts = report.get("attention_value_facts") if isinstance(report.get("attention_value_facts"), dict) else {}
    return [
        "I used public screen/look/do only.",
        f"coordinate_source={report.get('coordinate_source')}",
        f"host_input_sent={str(report.get('host_input_sent')).lower()}",
        f"host_sent_event_count={report.get('host_sent_event_count')}",
        f"post_observe_generated={str(facts.get('post_observe_generated')).lower()}",
        f"stability_status={facts.get('stability_status')}",
        "tool_asserts_target_hit=false",
        "tool_asserts_causality=false",
        "tool_asserts_business_success=false",
        "Review before/after images externally; this report does not judge UI meaning or business success.",
    ]


def _comparison_safe_report_lines(report: dict[str, Any]) -> list[str]:
    comparison = report.get("comparison") if isinstance(report.get("comparison"), dict) else {}
    attention = report.get("attention") if isinstance(report.get("attention"), dict) else {}
    do_summary = attention.get("do_summary") if isinstance(attention.get("do_summary"), dict) else {}
    post_summary = do_summary.get("post_observe_summary") if isinstance(do_summary.get("post_observe_summary"), dict) else {}
    return [
        "I used public screen/look/do only.",
        "baseline_path=fullscreen_full_resolution_after_each_step",
        "attention_path=coarse_fullscreen_then_local_post_observe_then_look_diff",
        f"coordinate_source={report.get('coordinate_source')}",
        f"host_input_sent={str(report.get('host_input_sent')).lower()}",
        f"host_sent_event_count={report.get('host_sent_event_count')}",
        f"available_image_byte_savings_ratio={comparison.get('available_image_byte_savings_ratio')}",
        f"token_proxy_pixel_savings_ratio={comparison.get('token_proxy_pixel_savings_ratio')}",
        f"post_observe_stability_status={post_summary.get('stability_status')}",
        "tool_asserts_target_hit=false",
        "tool_asserts_causality=false",
        "tool_asserts_business_success=false",
        "Raw evidence and derived review artifacts are listed separately; image meaning remains external review only.",
    ]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except OSError:
        return 0


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as file:
            header = file.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        return None
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        return None
    return width, height


def _savings_ratio(baseline_value: int, attention_value: int) -> float | None:
    if baseline_value <= 0:
        return None
    return round(1.0 - (float(attention_value) / float(baseline_value)), 6)


def _write_report(report: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def _now_ms() -> int:
    return int(time.time() * 1000)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a public screen/look/do post_observe attention-sync proof.")
    parser.add_argument("--comparison", action="store_true", help="Run fullscreen baseline vs localized attention comparison.")
    parser.add_argument("--region", required=True, help="Screen region as x,y,w,h. Coordinate selection is external.")
    parser.add_argument("--target-view-x", required=True, type=int)
    parser.add_argument("--target-view-y", required=True, type=int)
    parser.add_argument("--full-region", default=None, help="Optional fullscreen baseline region as x,y,w,h. Defaults to screen.virtual.")
    parser.add_argument("--coordinate-source", default="external_ai_visual_selection")
    parser.add_argument("--caller-id", default="ai-control-attention-sync-proof")
    parser.add_argument("--scale-down", type=int, default=1)
    parser.add_argument("--coarse-scale-down", type=int, default=5)
    parser.add_argument("--baseline-frame-count", type=int, default=6)
    parser.add_argument("--baseline-interval-ms", type=int, default=120)
    parser.add_argument("--delay-ms", type=int, default=120)
    parser.add_argument("--frame-count", type=int, default=6)
    parser.add_argument("--interval-ms", type=int, default=120)
    parser.add_argument("--stable-threshold", type=float, default=0.001)
    parser.add_argument("--stable-frame-count", type=int, default=2)
    parser.add_argument("--stop-when-stable", action="store_true")
    parser.add_argument("--cleanup-key", default=None)
    parser.add_argument("--discovery-file", default=None)
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    post_observe = {
        "delay_ms": int(args.delay_ms),
        "frame_count": int(args.frame_count),
        "interval_ms": int(args.interval_ms),
        "stable_threshold": float(args.stable_threshold),
        "stable_frame_count": int(args.stable_frame_count),
        "stop_when_stable": bool(args.stop_when_stable),
    }
    if args.comparison:
        report = run_attention_value_comparison(
            attention_region=parse_region(args.region),
            target_view_x=int(args.target_view_x),
            target_view_y=int(args.target_view_y),
            caller_id=str(args.caller_id),
            coordinate_source=str(args.coordinate_source),
            post_observe=post_observe,
            full_region=parse_region(args.full_region) if args.full_region else None,
            attention_scale_down=int(args.scale_down),
            coarse_scale_down=int(args.coarse_scale_down),
            baseline_frame_count=int(args.baseline_frame_count),
            baseline_interval_ms=int(args.baseline_interval_ms),
            cleanup_key=args.cleanup_key,
            discovery_file=Path(args.discovery_file) if args.discovery_file else None,
            output=Path(args.output) if args.output else None,
        )
        return 0 if report.get("comparison_status") == "recorded" else 4

    report = run_attention_sync_value_proof(
        region=parse_region(args.region),
        target_view_x=int(args.target_view_x),
        target_view_y=int(args.target_view_y),
        caller_id=str(args.caller_id),
        coordinate_source=str(args.coordinate_source),
        post_observe=post_observe,
        scale_down=int(args.scale_down),
        cleanup_key=args.cleanup_key,
        discovery_file=Path(args.discovery_file) if args.discovery_file else None,
        output=Path(args.output) if args.output else None,
    )
    return 0 if report.get("proof_status") == "recorded" else 4


if __name__ == "__main__":
    raise SystemExit(main())
