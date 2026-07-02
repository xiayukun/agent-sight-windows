from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from agentsight.channels.key_text import validate_key_text_stream
from agentsight.channels.keyboard_events import keyboard_action_summary
from agentsight.channels.pointer_events import MOUSE_INPUT_TYPES, is_mouse_action, mouse_action_summary


class SchemaError(ValueError):
    def __init__(self, message: str, *, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


PUBLIC_COMMAND_ORDER = (
    "screen",
    "look",
    "do",
)

MAX_POST_OBSERVE_FRAME_COUNT = 3600

COMMAND_ORDER = (
    *PUBLIC_COMMAND_ORDER,
    "get_capabilities",
    "observe",
    "query_visual_memory",
    "derive_candidates",
    "create_lease",
    "execute_input",
    "run_limited_batch",
    "stop",
    "get_evidence_package",
    "read_replay",
    "verify_integrity",
)

COMMANDS = set(COMMAND_ORDER)

SCHEMA_VERSION = "dev-p1c"

TOP_LEVEL_FIELDS = {"command", "payload", "session_id"}

PAYLOAD_FIELDS: dict[str, set[str]] = {
    "get_capabilities": {"probe_mode"},
    "screen": {"v", "id", "op"},
    "look": {
        "v",
        "id",
        "op",
        "q",
        "src",
        "r",
        "scale_down",
        "time",
        "mode",
        "max_artifacts",
        "max_frames",
        "max_pairs",
        "min_changed_pixel_ratio",
    },
    "do": {"v", "id", "op", "basis", "seq", "duration_ms", "post_observe"},
    "observe": {
        "mode",
        "region",
        "after_action_ref",
        "channel_ref",
        "fallback_policy",
        "frame_count",
        "interval_ms",
        "change_detection",
        "baseline_observation_ref",
        "evidence_request",
    },
    "query_visual_memory": {"query_type", "sequence_id", "change_event_id", "requested_time", "max_entries", "before_count", "after_count"},
    "derive_candidates": {"observation_ref"},
    "create_lease": {
        "duration_ms",
        "scope",
        "budget",
        "input_channel_ref",
        "arming_ref",
        "operator_consent_ref",
        "before_observation_ref",
        "after_observation_channel_ref",
    },
    "execute_input": {
        "lease_id",
        "input_channel_ref",
        "input_type",
        "x",
        "y",
        "to_x",
        "to_y",
        "button",
        "wheel_delta",
        "vertical_wheel_delta",
        "horizontal_wheel_delta",
        "key",
        "modifiers",
        "text",
        "duration_ms",
        "simulate_evidence_prewrite_failure",
        "skip_after_observation",
        "after_observation_skip_reason",
    },
    "run_limited_batch": {"steps", "max_steps", "max_duration_ms", "max_input_events", "response_detail"},
    "stop": {"reason"},
    "get_evidence_package": set(),
    "read_replay": {"reexecute"},
    "verify_integrity": set(),
}

ALLOWED_INPUT_TYPES = {"wait", *MOUSE_INPUT_TYPES, "key_text_stream", "key_press", "key_chord", "key_down", "key_up"}
ALLOWED_OBSERVE_MODES = {"fullscreen", "region", "after_action", "sequence"}
ALLOWED_FALLBACK_POLICIES = {"none", "explicit_only"}
ALLOWED_PROBE_MODES = {"cached", "passive"}
ALLOWED_RESPONSE_DETAILS = {"summary", "full"}
ALLOWED_LOOK_QUERY_TYPES = {"frame", "diff", "changes", "clip"}
ALLOWED_LOOK_SOURCE_TYPES = {"screen", "view"}
ALLOWED_LOOK_DIFF_MODES = {"endpoints", "timeline", "timeline_with_artifacts"}
ALLOWED_DO_STEP_TYPES = {
    "move",
    "click",
    "dblclick",
    "down",
    "up",
    "wheel",
    "text",
    "key",
    "chord",
    "wait",
}
ALLOWED_DO_COORDS = {"view"}
ALLOWED_DO_MOVE_TYPES = {"instant", "linear"}
ALLOWED_VISUAL_EVIDENCE_ARTIFACT_TYPES = {"raw_frame", "raw_crop", "before_after", "diff_heatmap"}
ALLOWED_VISUAL_EVIDENCE_SOURCES = {"change_events"}
ALLOWED_VISUAL_MEMORY_QUERY_TYPES = {
    "status",
    "recent_frames",
    "frames_near_time",
    "sequence_change_index",
    "change_event",
    "event_window",
    "retention_status",
    "prune_unreferenced_buffer",
    "retention_class_projection",
    "storage_attention_summary",
}
ALLOWED_BATCH_STEP_TYPES = {
    "observe_once",
    "locate_visible_target",
    "click_candidate_with_evidence",
    "type_keys_with_evidence",
    "wait_visible_change",
    "build_evidence_package",
    "safe_stop",
}

BATCH_STEP_PAYLOAD_FIELDS: dict[str, set[str]] = {
    "observe_once": {"mode", "region", "channel_ref"},
    "locate_visible_target": {"observation_ref"},
    "click_candidate_with_evidence": {
        "candidate_id",
        "lease_id",
        "x",
        "y",
        "button",
        "input_channel_ref",
        "after_observation_channel_ref",
        "arming_ref",
        "operator_consent_ref",
        "before_observation_ref",
    },
    "type_keys_with_evidence": {
        "lease_id",
        "text",
        "input_type",
        "key",
        "modifiers",
        "input_channel_ref",
        "after_observation_channel_ref",
        "arming_ref",
        "operator_consent_ref",
        "before_observation_ref",
    },
    "wait_visible_change": {"observation_ref", "duration_ms", "channel_ref"},
    "build_evidence_package": set(),
    "safe_stop": {"reason"},
}


@dataclass(frozen=True)
class ProtocolRequest:
    command: str
    payload: dict[str, Any]
    session_id: str | None = None


def validate_region(region: Any) -> dict[str, int]:
    if not isinstance(region, dict):
        raise SchemaError("region must be an object", field="region")
    allowed = {"x", "y", "width", "height"}
    extra = set(region) - allowed
    if extra:
        raise SchemaError(f"unknown region fields: {sorted(extra)}", field=sorted(extra)[0])
    missing = allowed - set(region)
    if missing:
        raise SchemaError(f"missing region fields: {sorted(missing)}", field=sorted(missing)[0])
    parsed: dict[str, int] = {}
    for field in allowed:
        value = region[field]
        if not isinstance(value, int):
            raise SchemaError(f"region.{field} must be an integer", field=field)
        parsed[field] = value
    if parsed["width"] <= 0 or parsed["height"] <= 0:
        raise SchemaError("region width/height must be positive", field="region")
    return parsed


def validate_view_rect(rect: Any) -> dict[str, int]:
    if not isinstance(rect, dict):
        raise SchemaError("r must be an object", field="r")
    allowed = {"x", "y", "w", "h"}
    extra = set(rect) - allowed
    if extra:
        raise SchemaError(f"unknown r fields: {sorted(extra)}", field=sorted(extra)[0])
    missing = allowed - set(rect)
    if missing:
        raise SchemaError(f"missing r fields: {sorted(missing)}", field=sorted(missing)[0])
    parsed: dict[str, int] = {}
    for field in allowed:
        value = rect[field]
        if not isinstance(value, int):
            raise SchemaError(f"r.{field} must be an integer", field=field)
        parsed[field] = value
    if parsed["w"] <= 0 or parsed["h"] <= 0:
        raise SchemaError("r.w/r.h must be positive", field="r")
    return parsed


def validate_look_src(src: Any) -> dict[str, Any]:
    if not isinstance(src, dict):
        raise SchemaError("src must be an object", field="src")
    allowed = {"type", "t", "view_id"}
    extra = set(src) - allowed
    if extra:
        raise SchemaError(f"unknown src fields: {sorted(extra)}", field=sorted(extra)[0])
    src_type = src.get("type")
    if src_type not in ALLOWED_LOOK_SOURCE_TYPES:
        raise SchemaError(f"unsupported src.type: {src_type!r}", field="src")
    if src_type == "view":
        view_id = src.get("view_id")
        if not isinstance(view_id, str) or not view_id:
            raise SchemaError("src.type=view requires non-empty view_id", field="view_id")
    if "t" in src and src["t"] != "latest" and not isinstance(src["t"], str):
        raise SchemaError("src.t must be 'latest' or a time string", field="t")
    return dict(src)


def validate_do_basis(basis: Any) -> dict[str, Any]:
    if not isinstance(basis, dict):
        raise SchemaError("basis must be an object", field="basis")
    allowed = {"view_id", "point"}
    extra = set(basis) - allowed
    if extra:
        raise SchemaError(f"unknown basis fields: {sorted(extra)}", field=sorted(extra)[0])
    if not basis.get("view_id"):
        raise SchemaError("basis requires non-empty view_id", field="basis")
    if not isinstance(basis.get("view_id"), str):
        raise SchemaError("basis.view_id must be a string", field="view_id")
    point = basis.get("point")
    if point is not None:
        if not isinstance(point, dict):
            raise SchemaError("basis.point must be an object", field="point")
        allowed_point = {"x", "y"}
        extra_point = set(point) - allowed_point
        if extra_point:
            raise SchemaError(f"unknown basis.point fields: {sorted(extra_point)}", field=sorted(extra_point)[0])
        missing_point = allowed_point - set(point)
        if missing_point:
            raise SchemaError(f"missing basis.point fields: {sorted(missing_point)}", field="point")
        if not isinstance(point.get("x"), int) or not isinstance(point.get("y"), int):
            raise SchemaError("basis.point x/y must be integers", field="point")
    return dict(basis)


def validate_do_seq(seq: Any) -> list[Any]:
    if not isinstance(seq, list) or not seq:
        raise SchemaError("seq must be a non-empty array", field="seq")
    parsed: list[Any] = []
    for index, step in enumerate(seq):
        if isinstance(step, int):
            if step < 0:
                raise SchemaError(f"seq[{index}] wait milliseconds must be non-negative", field="seq")
            parsed.append(step)
            continue
        if not isinstance(step, dict):
            raise SchemaError(f"seq[{index}] must be a number or object", field="seq")
        step_type = step.get("t")
        if step_type not in ALLOWED_DO_STEP_TYPES:
            raise SchemaError(f"unsupported do step type: {step_type!r}", field="t")
        if step_type == "move":
            allowed = {"t", "x", "y", "coord", "move", "ms"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"unknown move step fields: {sorted(extra)}", field=sorted(extra)[0])
            if not isinstance(step.get("x"), int) or not isinstance(step.get("y"), int):
                raise SchemaError("move requires integer x/y", field="x")
            if step.get("coord") not in ALLOWED_DO_COORDS:
                raise SchemaError("move.coord must be 'view'", field="coord")
            if step.get("move", "instant") not in ALLOWED_DO_MOVE_TYPES:
                raise SchemaError("move.move must be instant or linear", field="move")
            if "ms" in step and (not isinstance(step["ms"], int) or step["ms"] < 0):
                raise SchemaError("move.ms must be a non-negative integer", field="ms")
        elif step_type in {"click", "dblclick"}:
            allowed = {"t", "b"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"{step_type} does not accept fields: {sorted(extra)}", field=sorted(extra)[0])
            button = str(step.get("b") or "left").lower()
            if button not in {"left", "right", "middle"}:
                raise SchemaError("mouse button must be left, right, or middle", field="b")
        elif step_type in {"down", "up"}:
            allowed = {"t", "b", "key"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"{step_type} does not accept fields: {sorted(extra)}", field=sorted(extra)[0])
            has_button = "b" in step
            has_key = "key" in step
            if has_button == has_key:
                raise SchemaError(f"{step_type} requires exactly one of b or key", field="b")
            if has_button:
                button = str(step.get("b") or "left").lower()
                if button not in {"left", "right", "middle"}:
                    raise SchemaError("mouse button must be left, right, or middle", field="b")
            else:
                input_type = "key_down" if step_type == "down" else "key_up"
                try:
                    keyboard_action_summary(input_type, {"key": step.get("key")})
                except ValueError as exc:
                    raise SchemaError(str(exc), field="key") from exc
        elif step_type == "wheel":
            allowed = {"t", "dy", "dx"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"unknown wheel step fields: {sorted(extra)}", field=sorted(extra)[0])
            if "dy" in step and not isinstance(step["dy"], int):
                raise SchemaError("wheel.dy must be an integer", field="dy")
            if "dx" in step and not isinstance(step["dx"], int):
                raise SchemaError("wheel.dx must be an integer", field="dx")
            if int(step.get("dy", 0)) == 0 and int(step.get("dx", 0)) == 0:
                raise SchemaError("wheel requires non-zero dy or dx", field="dy")
        elif step_type == "text":
            allowed = {"t", "text"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"unknown text step fields: {sorted(extra)}", field=sorted(extra)[0])
            try:
                validate_key_text_stream(step.get("text"))
            except ValueError as exc:
                raise SchemaError(str(exc), field="text") from exc
        elif step_type == "key":
            allowed = {"t", "key"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"unknown key step fields: {sorted(extra)}", field=sorted(extra)[0])
            try:
                keyboard_action_summary("key_press", {"key": step.get("key")})
            except ValueError as exc:
                raise SchemaError(str(exc), field="key") from exc
        elif step_type == "chord":
            allowed = {"t", "modifiers", "key"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"unknown chord step fields: {sorted(extra)}", field=sorted(extra)[0])
            try:
                keyboard_action_summary("key_chord", {"modifiers": step.get("modifiers"), "key": step.get("key")})
            except ValueError as exc:
                raise SchemaError(str(exc), field="key") from exc
        elif step_type == "wait":
            allowed = {"t", "ms"}
            extra = set(step) - allowed
            if extra:
                raise SchemaError(f"unknown wait step fields: {sorted(extra)}", field=sorted(extra)[0])
            if not isinstance(step.get("ms"), int) or step["ms"] < 0:
                raise SchemaError("wait.ms must be a non-negative integer", field="ms")
        parsed.append(step)
    return parsed


def validate_post_observe(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise SchemaError("post_observe must be an object", field="post_observe")
    allowed = {"delay_ms", "frame_count", "interval_ms", "stable_threshold", "stable_frame_count", "stop_when_stable"}
    extra = set(value) - allowed
    if extra:
        raise SchemaError(f"unknown post_observe fields: {sorted(extra)}", field=sorted(extra)[0])
    delay_ms = value.get("delay_ms", 0)
    if not isinstance(delay_ms, int) or delay_ms < 0 or delay_ms > 5000:
        raise SchemaError("post_observe.delay_ms must be 0..5000", field="delay_ms")
    frame_count = value.get("frame_count", 3)
    if not isinstance(frame_count, int) or frame_count < 1 or frame_count > MAX_POST_OBSERVE_FRAME_COUNT:
        raise SchemaError(f"post_observe.frame_count must be 1..{MAX_POST_OBSERVE_FRAME_COUNT}", field="frame_count")
    interval_ms = value.get("interval_ms", 150)
    if not isinstance(interval_ms, int) or interval_ms < 0 or interval_ms > 2000:
        raise SchemaError("post_observe.interval_ms must be 0..2000", field="interval_ms")
    stable_threshold = value.get("stable_threshold", 0.001)
    if not isinstance(stable_threshold, (int, float)) or stable_threshold < 0 or stable_threshold > 1:
        raise SchemaError("post_observe.stable_threshold must be 0..1", field="stable_threshold")
    stable_frame_count = value.get("stable_frame_count", 2)
    if not isinstance(stable_frame_count, int) or stable_frame_count < 1 or stable_frame_count > 5:
        raise SchemaError("post_observe.stable_frame_count must be 1..5", field="stable_frame_count")
    stop_when_stable = value.get("stop_when_stable", False)
    if not isinstance(stop_when_stable, bool):
        raise SchemaError("post_observe.stop_when_stable must be boolean", field="stop_when_stable")
    return {
        "delay_ms": delay_ms,
        "frame_count": frame_count,
        "interval_ms": interval_ms,
        "stable_threshold": float(stable_threshold),
        "stable_frame_count": stable_frame_count,
        "stop_when_stable": stop_when_stable,
    }


def validate_visual_evidence_request(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SchemaError("evidence_request must be an object", field="evidence_request")
    allowed = {"source", "artifact_types", "max_artifacts"}
    extra = set(value) - allowed
    if extra:
        raise SchemaError(f"unknown evidence_request fields: {sorted(extra)}", field=sorted(extra)[0])
    source = value.get("source", "change_events")
    if source not in ALLOWED_VISUAL_EVIDENCE_SOURCES:
        raise SchemaError(f"unsupported evidence_request.source: {source!r}", field="source")
    artifact_types = value.get("artifact_types")
    if not isinstance(artifact_types, list) or not artifact_types:
        raise SchemaError("evidence_request.artifact_types must be a non-empty array", field="artifact_types")
    for artifact_type in artifact_types:
        if artifact_type not in ALLOWED_VISUAL_EVIDENCE_ARTIFACT_TYPES:
            raise SchemaError(f"unsupported visual evidence artifact type: {artifact_type!r}", field="artifact_types")
    max_artifacts = value.get("max_artifacts", 1)
    if not isinstance(max_artifacts, int) or max_artifacts < 1 or max_artifacts > 5:
        raise SchemaError("evidence_request.max_artifacts must be between 1 and 5", field="max_artifacts")
    return {"source": source, "artifact_types": artifact_types, "max_artifacts": max_artifacts}


def schema_ref() -> dict[str, Any]:
    material = json.dumps(
        {
            "schema_version": SCHEMA_VERSION,
            "commands": sorted(COMMANDS),
            "payload_fields": {key: sorted(value) for key, value in sorted(PAYLOAD_FIELDS.items())},
            "batch_step_payload_fields": {
                key: sorted(value) for key, value in sorted(BATCH_STEP_PAYLOAD_FIELDS.items())
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return {"schema_version": SCHEMA_VERSION, "schema_sha256": hashlib.sha256(material.encode("utf-8")).hexdigest()}


def validate_request(raw: dict[str, Any]) -> ProtocolRequest:
    if not isinstance(raw, dict):
        raise SchemaError("request must be an object")

    extra = set(raw) - TOP_LEVEL_FIELDS
    if extra:
        raise SchemaError(f"unknown top-level fields: {sorted(extra)}", field=sorted(extra)[0])

    command = raw.get("command")
    if command not in COMMANDS:
        raise SchemaError(f"unsupported command: {command!r}", field="command")

    payload = raw.get("payload", {})
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise SchemaError("payload must be an object", field="payload")

    allowed = PAYLOAD_FIELDS[command]
    extra_payload = set(payload) - allowed
    if extra_payload:
        raise SchemaError(
            f"unknown payload fields for {command}: {sorted(extra_payload)}",
            field=sorted(extra_payload)[0],
        )

    if command == "observe":
        mode = payload.get("mode", "fullscreen")
        if mode not in ALLOWED_OBSERVE_MODES:
            raise SchemaError(f"unsupported observe mode: {mode!r}", field="mode")
        fallback_policy = payload.get("fallback_policy", "none")
        if fallback_policy not in ALLOWED_FALLBACK_POLICIES:
            raise SchemaError(f"unsupported fallback_policy: {fallback_policy!r}", field="fallback_policy")
        channel_ref = payload.get("channel_ref")
        if channel_ref is not None and not isinstance(channel_ref, str):
            raise SchemaError("channel_ref must be a string", field="channel_ref")
        if mode == "region" and "region" not in payload:
            raise SchemaError("region mode requires region", field="region")
        if "region" in payload:
            validate_region(payload["region"])
        if "evidence_request" in payload:
            validate_visual_evidence_request(payload["evidence_request"])
            if mode != "sequence":
                raise SchemaError("evidence_request is supported only for sequence observations", field="evidence_request")
        if mode == "sequence":
            frame_count = payload.get("frame_count", 3)
            if not isinstance(frame_count, int):
                raise SchemaError("frame_count must be an integer", field="frame_count")
            if frame_count < 2 or frame_count > 5:
                raise SchemaError("frame_count must be between 2 and 5", field="frame_count")
            interval_ms = payload.get("interval_ms", 100)
            if not isinstance(interval_ms, int):
                raise SchemaError("interval_ms must be an integer", field="interval_ms")
            if interval_ms < 50 or interval_ms > 250:
                raise SchemaError("interval_ms must be between 50 and 250", field="interval_ms")
            if (frame_count - 1) * interval_ms > 1000:
                raise SchemaError("sequence total duration must be <= 1000ms", field="interval_ms")
            change_detection = payload.get("change_detection", True)
            if not isinstance(change_detection, bool):
                raise SchemaError("change_detection must be a boolean", field="change_detection")
            baseline_observation_ref = payload.get("baseline_observation_ref")
            if baseline_observation_ref is not None and not isinstance(baseline_observation_ref, str):
                raise SchemaError("baseline_observation_ref must be a string", field="baseline_observation_ref")

    if command == "get_capabilities":
        probe_mode = payload.get("probe_mode", "cached")
        if probe_mode not in ALLOWED_PROBE_MODES:
            raise SchemaError(f"unsupported probe_mode: {probe_mode!r}", field="probe_mode")

    if command == "screen":
        if payload.get("op", "screen") != "screen":
            raise SchemaError("screen payload op must be 'screen'", field="op")

    if command == "look":
        if payload.get("op", "look") != "look":
            raise SchemaError("look payload op must be 'look'", field="op")
        q = payload.get("q")
        if q not in ALLOWED_LOOK_QUERY_TYPES:
            raise SchemaError(f"unsupported look q: {q!r}", field="q")
        scale_down = payload.get("scale_down")
        if not isinstance(scale_down, int) or scale_down < 1 or scale_down > 32:
            raise SchemaError("look.scale_down must be an integer between 1 and 32", field="scale_down")
        if q == "frame":
            validate_look_src(payload.get("src"))
            validate_view_rect(payload.get("r"))
            time_value = payload.get("time")
            if time_value is not None:
                if not isinstance(time_value, dict):
                    raise SchemaError("look.time must be an object", field="time")
                requested = time_value.get("near", time_value.get("at", time_value.get("requested_time")))
                if requested is None or not isinstance(requested, (str, int, float)):
                    raise SchemaError("look time query requires time.near, time.at, or time.requested_time", field="time")
        if q == "diff":
            validate_look_src(payload.get("src"))
            validate_view_rect(payload.get("r"))
            src = payload.get("src") if isinstance(payload.get("src"), dict) else {}
            mode = payload.get("mode")
            if mode is None:
                mode = "endpoints" if src.get("type") == "view" else "timeline"
            if mode not in ALLOWED_LOOK_DIFF_MODES:
                raise SchemaError(f"unsupported diff mode: {mode!r}", field="mode")
            max_artifacts = payload.get("max_artifacts", 0)
            if not isinstance(max_artifacts, int) or max_artifacts < 0 or max_artifacts > 5:
                raise SchemaError("max_artifacts must be between 0 and 5", field="max_artifacts")
            if mode == "endpoints":
                if src.get("type") != "view" or not isinstance(src.get("view_id"), str):
                    raise SchemaError("look q=diff mode=endpoints requires src.type='view' and src.view_id", field="src")
            else:
                time_value = payload.get("time")
                if not isinstance(time_value, dict) or not isinstance(time_value.get("from"), str) or not isinstance(time_value.get("to"), str):
                    raise SchemaError("look q=diff timeline modes require time.from and time.to strings", field="time")
        if q == "changes":
            src = validate_look_src(payload.get("src"))
            validate_view_rect(payload.get("r"))
            time_value = payload.get("time")
            if time_value is not None:
                if not isinstance(time_value, dict):
                    raise SchemaError("look.time must be an object", field="time")
                allowed_time = {"from", "to"}
                extra_time = set(time_value) - allowed_time
                if extra_time:
                    raise SchemaError("look q=changes supports only time.from/time.to", field="time")
                for field in allowed_time & set(time_value):
                    if not isinstance(time_value[field], (str, int, float)):
                        raise SchemaError(f"look.time.{field} must be a string or number", field="time")
            max_pairs = payload.get("max_pairs", 128)
            if not isinstance(max_pairs, int) or max_pairs < 1 or max_pairs > 10_000:
                raise SchemaError("look.max_pairs must be 1..10000", field="max_pairs")
            threshold = payload.get("min_changed_pixel_ratio", 0.0)
            if not isinstance(threshold, (int, float)) or threshold < 0 or threshold > 1:
                raise SchemaError("look.min_changed_pixel_ratio must be 0..1", field="min_changed_pixel_ratio")
        if q == "clip":
            validate_look_src(payload.get("src"))
            validate_view_rect(payload.get("r"))
            time_value = payload.get("time")
            if not isinstance(time_value, dict) or not isinstance(time_value.get("from"), str) or not isinstance(time_value.get("to"), str):
                raise SchemaError("look q=clip requires time.from and time.to strings", field="time")
            max_frames = payload.get("max_frames", 32)
            if not isinstance(max_frames, int) or max_frames < 1 or max_frames > 240:
                raise SchemaError("look.max_frames must be 1..240", field="max_frames")
            max_artifacts = payload.get("max_artifacts", 0)
            if not isinstance(max_artifacts, int) or max_artifacts < 0 or max_artifacts > 1:
                raise SchemaError("look q=clip max_artifacts must be 0 or 1", field="max_artifacts")

    if command == "do":
        if payload.get("op", "do") != "do":
            raise SchemaError("do payload op must be 'do'", field="op")
        validate_do_basis(payload.get("basis"))
        validate_do_seq(payload.get("seq"))
        validate_post_observe(payload.get("post_observe"))
        duration_ms = payload.get("duration_ms", 10_000)
        if not isinstance(duration_ms, int) or duration_ms <= 0 or duration_ms > 60_000:
            raise SchemaError("duration_ms must be 1..60000", field="duration_ms")

    if command == "query_visual_memory":
        query_type = payload.get("query_type")
        if query_type not in ALLOWED_VISUAL_MEMORY_QUERY_TYPES:
            raise SchemaError(f"unsupported query_type: {query_type!r}", field="query_type")
        for field in {"sequence_id", "change_event_id"}:
            value = payload.get(field)
            if value is not None and not isinstance(value, str):
                raise SchemaError(f"{field} must be a string", field=field)
        max_entries = payload.get("max_entries", 8)
        if not isinstance(max_entries, int) or max_entries < 1 or max_entries > 32:
            raise SchemaError("max_entries must be between 1 and 32", field="max_entries")
        before_count = payload.get("before_count", 1)
        after_count = payload.get("after_count", 1)
        for field, value in {"before_count": before_count, "after_count": after_count}.items():
            if not isinstance(value, int) or value < 0 or value > 5:
                raise SchemaError(f"{field} must be between 0 and 5", field=field)
        if query_type == "event_window" and before_count + after_count <= 0:
            raise SchemaError("event_window requires at least one before or after frame", field="before_count")
        requested_time = payload.get("requested_time")
        if query_type == "frames_near_time" and not isinstance(requested_time, (str, int, float)):
            raise SchemaError("frames_near_time requires requested_time", field="requested_time")

    if command == "execute_input":
        input_type = payload.get("input_type")
        if input_type not in ALLOWED_INPUT_TYPES:
            raise SchemaError(f"unsupported input_type: {input_type!r}", field="input_type")
        input_channel_ref = payload.get("input_channel_ref")
        if input_channel_ref is not None and not isinstance(input_channel_ref, str):
            raise SchemaError("input_channel_ref must be a string", field="input_channel_ref")
        if input_type == "key_text_stream":
            try:
                validate_key_text_stream(payload.get("text"))
            except ValueError as exc:
                raise SchemaError(str(exc), field="text") from exc
        if is_mouse_action(input_type):
            forbidden_for_mouse = {"text", "key", "modifiers", "duration_ms"} & set(payload)
            if forbidden_for_mouse:
                raise SchemaError(
                    f"{input_type} does not accept fields: {sorted(forbidden_for_mouse)}",
                    field=sorted(forbidden_for_mouse)[0],
                )
            try:
                mouse_action_summary(input_type, payload)
            except ValueError as exc:
                message = str(exc)
                field = "x"
                if "to_x" in message or "to_y" in message:
                    field = "to_x"
                elif "wheel_delta" in message:
                    field = "wheel_delta"
                elif "button" in message:
                    field = "button"
                raise SchemaError(message, field=field) from exc
        if input_type in {"key_press", "key_chord", "key_down", "key_up"}:
            forbidden_for_keyboard = {
                "text",
                "x",
                "y",
                "to_x",
                "to_y",
                "button",
                "wheel_delta",
                "vertical_wheel_delta",
                "horizontal_wheel_delta",
                "duration_ms",
            } & set(payload)
            if forbidden_for_keyboard:
                raise SchemaError(
                    f"{input_type} does not accept fields: {sorted(forbidden_for_keyboard)}",
                    field=sorted(forbidden_for_keyboard)[0],
                )
            try:
                keyboard_action_summary(input_type, payload)
            except ValueError as exc:
                raise SchemaError(str(exc), field="key") from exc

    if command == "create_lease":
        input_channel_ref = payload.get("input_channel_ref")
        if input_channel_ref is not None and not isinstance(input_channel_ref, str):
            raise SchemaError("input_channel_ref must be a string", field="input_channel_ref")
        for field in {"arming_ref", "operator_consent_ref", "before_observation_ref", "after_observation_channel_ref"}:
            value = payload.get(field)
            if value is not None and not isinstance(value, str):
                raise SchemaError(f"{field} must be a string", field=field)

    if command == "run_limited_batch":
        response_detail = payload.get("response_detail", "summary")
        if response_detail not in ALLOWED_RESPONSE_DETAILS:
            raise SchemaError(f"unsupported response_detail: {response_detail!r}", field="response_detail")
        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            raise SchemaError("steps must be a list", field="steps")
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise SchemaError(f"step {index} must be an object", field="steps")
            extra_step_fields = set(step) - {"step_type", "payload"}
            if extra_step_fields:
                raise SchemaError(
                    f"unknown step fields: {sorted(extra_step_fields)}",
                    field=sorted(extra_step_fields)[0],
                )
            step_type = step.get("step_type")
            if step_type not in ALLOWED_BATCH_STEP_TYPES:
                raise SchemaError(f"unsupported step_type: {step_type!r}", field="step_type")
            step_payload = step.get("payload", {})
            if step_payload is None:
                step_payload = {}
            if not isinstance(step_payload, dict):
                raise SchemaError(f"step {index} payload must be an object", field="payload")
            extra_payload_fields = set(step_payload) - BATCH_STEP_PAYLOAD_FIELDS[step_type]
            if extra_payload_fields:
                raise SchemaError(
                    f"unknown payload fields for {step_type}: {sorted(extra_payload_fields)}",
                    field=sorted(extra_payload_fields)[0],
                )

    session_id = raw.get("session_id")
    if session_id is not None and not isinstance(session_id, str):
        raise SchemaError("session_id must be a string", field="session_id")

    return ProtocolRequest(command=command, payload=payload, session_id=session_id)
