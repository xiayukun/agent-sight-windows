from __future__ import annotations

import time
import uuid
import base64
from io import BytesIO
from pathlib import Path
from typing import Any

from agentsight.boundary.guard import BoundaryGuard, BoundaryViolation
from agentsight.capture_quality import analyze_capture_image_bytes
from agentsight.channels.base import ChannelFailure, InputChannel, ObservationChannel
from agentsight.channels.defaults import (
    DEFAULT_INPUT_CHANNEL_REF,
    DEFAULT_OBSERVATION_CHANNEL_REF,
    default_input_channels,
    default_observation_channels,
)
from agentsight.channels.key_text import key_text_summary, validate_key_text_stream
from agentsight.channels.keyboard_events import is_keyboard_action, keyboard_action_summary
from agentsight.channels.mock import MockInputChannel
from agentsight.channels.pointer_events import is_mouse_action, mouse_action_points, mouse_action_summary
from agentsight.channels.registry import InputChannelRegistry, ObservationChannelRegistry
from agentsight.diagnostics.capture import CaptureDiagnosticsService
from agentsight.diagnostics.input import InputDiagnosticsService
from agentsight.evidence.sequence_artifacts import build_sequence_gif_artifact
from agentsight.evidence.store import EvidenceReplayService
from agentsight.failure.service import CapabilityFailureService
from agentsight.protocol.schemas import COMMAND_ORDER, PUBLIC_COMMAND_ORDER, SchemaError, schema_ref, validate_post_observe, validate_request
from agentsight.segments import (
    SegmentFrameRecorder,
    decode_segment_region_to_image_content,
    query_segment_change_index,
    query_segment_decoder_near_time,
    query_segment_review_clip,
    query_segment_timeline_diff,
)
from agentsight.visual_memory.change_index import build_region_change_index
from agentsight.visual_memory.evidence_retrieval import build_visual_evidence_artifacts
from agentsight.visual_memory.frame_buffer import FrameBufferConfig, VisualFrameBuffer
from agentsight.visual_memory.post_observe import build_post_action_observation_window, should_stop_post_observe_sampling
from agentsight.visual_memory.query import query_visual_memory

_MAX_RUNTIME_OBSERVATIONS = 96
_MAX_RUNTIME_VIEWS = 160
_RUNTIME_PAYLOAD_KEYS = {
    "_media_bytes",
    "_mcp_content",
    "mcp_content",
    "bgra_bytes",
    "_bgra_bytes",
    "image_bytes",
    "raw_bytes",
}


def _segment_recorder_options_from_tray_config() -> dict[str, Any]:
    try:
        from agentsight.tray.state import default_tray_config_file, normalize_recording_policy, read_jsonc_file

        raw_policy = read_jsonc_file(default_tray_config_file())
        policy = normalize_recording_policy(raw_policy)
    except Exception:
        return {}
    raw_recording = raw_policy.get("recording") if isinstance(raw_policy, dict) else None
    raw_recording_segment = raw_recording.get("segment") if isinstance(raw_recording, dict) else None
    raw_top_level_segment = raw_policy.get("segment") if isinstance(raw_policy, dict) else None
    options: dict[str, Any] = {}
    for raw_segment in (raw_recording_segment, raw_top_level_segment):
        if not isinstance(raw_segment, dict):
            continue
        if raw_segment.get("storage_format"):
            options["storage_format"] = str(raw_segment.get("storage_format"))

        if raw_segment.get("bucket_granularity"):
            options["segment_bucket_granularity"] = str(raw_segment.get("bucket_granularity"))
        if raw_segment.get("image_encoding"):
            options["image_encoding"] = str(raw_segment.get("image_encoding"))
        if raw_segment.get("image_quality") is not None:
            options["image_quality"] = int(raw_segment.get("image_quality") or 70)
        if raw_segment.get("image_lossless") is not None:
            options["image_lossless"] = bool(raw_segment.get("image_lossless"))
        if raw_segment.get("max_segment_size_mb") is not None:
            options["max_segment_size_mb"] = float(raw_segment.get("max_segment_size_mb") or 0)
        elif raw_segment.get("max_size_mb") is not None:
            options["max_segment_size_mb"] = float(raw_segment.get("max_size_mb") or 0)
    if isinstance(raw_policy, dict):
        if raw_policy.get("segment_storage_format"):
            options["storage_format"] = str(raw_policy.get("segment_storage_format"))
        if raw_policy.get("segment_bucket_granularity"):
            options["segment_bucket_granularity"] = str(raw_policy.get("segment_bucket_granularity"))
        if raw_policy.get("segment_image_encoding"):
            options["image_encoding"] = str(raw_policy.get("segment_image_encoding"))
        if raw_policy.get("segment_image_quality") is not None:
            options["image_quality"] = int(raw_policy.get("segment_image_quality") or 70)
        if raw_policy.get("segment_image_lossless") is not None:
            options["image_lossless"] = bool(raw_policy.get("segment_image_lossless"))
        if raw_policy.get("daily_segment_boundary_local_time"):
            options["daily_segment_boundary_local_time"] = str(raw_policy.get("daily_segment_boundary_local_time"))
        if raw_policy.get("max_segment_size_mb") is not None:
            options["max_segment_size_mb"] = float(raw_policy.get("max_segment_size_mb") or 0)
        elif raw_policy.get("segment_max_size_mb") is not None:
            options["max_segment_size_mb"] = float(raw_policy.get("segment_max_size_mb") or 0)
    if isinstance(policy, dict) and policy.get("daily_segment_boundary_local_time"):
        options["daily_segment_boundary_local_time"] = str(policy.get("daily_segment_boundary_local_time"))
    return options


def _coerce_idle_fps(value: Any) -> float:
    try:
        if isinstance(value, bool):
            raise ValueError("bool_is_not_fps")
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 1.0
    return max(0.1, min(60.0, parsed))


class ProtocolGateway:
    def __init__(
        self,
        runs_dir: str | Path = "runs",
        *,
        adapter_ref: str = "local_client",
        observation_channel: ObservationChannel | None = None,
        observation_channels: list[ObservationChannel] | None = None,
        default_observation_channel_ref: str | None = None,
        input_channel: InputChannel | None = None,
        input_channels: list[InputChannel] | None = None,
        default_input_channel_ref: str | None = None,
        frame_buffer_config: FrameBufferConfig | None = None,
        segment_storage_format: str | None = None,
        segment_bucket_granularity: str | None = None,
        daily_segment_boundary_local_time: str | None = None,
        segment_image_encoding: str | None = None,
        segment_image_quality: int | None = None,
        segment_image_lossless: bool | None = None,
        max_segment_size_mb: int | float | None = None,
    ) -> None:
        self.adapter_ref = adapter_ref
        self.boundary = BoundaryGuard()
        self.failures = CapabilityFailureService()
        self.evidence = EvidenceReplayService(runs_dir)
        if observation_channels is None:
            if observation_channel is None:
                observation_channels = default_observation_channels()
                default_observation_channel_ref = default_observation_channel_ref or DEFAULT_OBSERVATION_CHANNEL_REF
            else:
                observation_channels = [observation_channel]
                default_observation_channel_ref = default_observation_channel_ref or observation_channel.name
        self.observation_registry = ObservationChannelRegistry(
            observation_channels,
            default_channel_ref=default_observation_channel_ref,
        )
        if input_channels is None:
            if input_channel is None:
                input_channels = default_input_channels()
                default_input_channel_ref = default_input_channel_ref or DEFAULT_INPUT_CHANNEL_REF
            else:
                input_channels = [input_channel]
                default_input_channel_ref = default_input_channel_ref or input_channel.name
        self.input_registry = InputChannelRegistry(input_channels, default_channel_ref=default_input_channel_ref)
        self.input_channel = self.input_registry.resolve()
        self.leases: dict[str, dict[str, Any]] = {}
        self.observations: dict[str, dict[str, Any]] = {}
        self.views: dict[str, dict[str, Any]] = {}
        self.candidates: dict[str, dict[str, Any]] = {}
        self.last_candidate_list: dict[str, Any] | None = None
        self.last_observation_id: str | None = None
        self.frame_buffer = VisualFrameBuffer(self.evidence, config=frame_buffer_config)
        segment_options = _segment_recorder_options_from_tray_config()
        self.segment_recorder = SegmentFrameRecorder(
            self.evidence,
            storage_format=segment_storage_format or segment_options.get("storage_format", "mkv_vfr"),
            segment_bucket_granularity=segment_bucket_granularity
            or segment_options.get("segment_bucket_granularity", "daily"),
            daily_segment_boundary_local_time=daily_segment_boundary_local_time
            or segment_options.get("daily_segment_boundary_local_time", "00:00"),
            image_encoding=segment_image_encoding or segment_options.get("image_encoding", "webp"),
            image_quality=segment_image_quality if segment_image_quality is not None else int(segment_options.get("image_quality", 70)),
            image_lossless=segment_image_lossless if segment_image_lossless is not None else bool(segment_options.get("image_lossless", False)),
            max_segment_size_mb=max_segment_size_mb if max_segment_size_mb is not None else segment_options.get("max_segment_size_mb", 1024),
        )
        self._last_idle_capture_ms: int | None = None
        self._last_idle_capture_enabled = False
        self.stopped = False

    def close(self) -> None:
        self.segment_recorder.close()

    def _runtime_metadata_only(self, value: Any) -> Any:
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, child in value.items():
                if key in _RUNTIME_PAYLOAD_KEYS:
                    if isinstance(child, (bytes, bytearray, memoryview)):
                        compact[f"{key}_stripped_bytes"] = len(child)
                    elif isinstance(child, list):
                        compact[f"{key}_stripped_count"] = len(child)
                    else:
                        compact[f"{key}_stripped"] = True
                    continue
                compact[key] = self._runtime_metadata_only(child)
            return compact
        if isinstance(value, list):
            return [self._runtime_metadata_only(item) for item in value]
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {"stripped_runtime_bytes": len(value)}
        return value

    def _remember_runtime_observation(self, observation_id: str | None, frame: dict[str, Any]) -> None:
        if not observation_id:
            return
        stored = self._runtime_metadata_only(frame)
        if isinstance(stored, dict):
            stored["runtime_payload_retained"] = False
            stored["runtime_payload_storage"] = "stripped_after_segment_record"
        self.observations[str(observation_id)] = stored
        self._prune_runtime_indexes()

    def _remember_runtime_view(self, view_id: str, record: dict[str, Any]) -> None:
        stored = self._runtime_metadata_only(record)
        if isinstance(stored, dict):
            stored["runtime_payload_retained"] = False
            stored["runtime_payload_storage"] = "mcp_image_content_response_only"
        self.views[str(view_id)] = stored
        self._prune_runtime_indexes()

    def _prune_runtime_indexes(self) -> None:
        while len(self.observations) > _MAX_RUNTIME_OBSERVATIONS:
            self.observations.pop(next(iter(self.observations)), None)
        while len(self.views) > _MAX_RUNTIME_VIEWS:
            self.views.pop(next(iter(self.views)), None)

    def _compact_runtime_payloads(self, *, reason: str) -> dict[str, Any]:
        before = {"observations": len(self.observations), "views": len(self.views)}
        self.observations = {
            key: self._runtime_metadata_only(value)
            for key, value in list(self.observations.items())[-_MAX_RUNTIME_OBSERVATIONS:]
        }
        self.views = {
            key: self._runtime_metadata_only(value)
            for key, value in list(self.views.items())[-_MAX_RUNTIME_VIEWS:]
        }
        self._prune_runtime_indexes()
        try:
            import gc

            collected = gc.collect()
        except Exception:
            collected = None
        return {
            "schema": "agentsight_runtime_memory_compaction_v1",
            "reason": reason,
            "before": before,
            "after": {"observations": len(self.observations), "views": len(self.views)},
            "gc_collected": collected,
        }

    def _discard_frame_runtime_payload(self, frame: dict[str, Any], *, reason: str) -> dict[str, Any]:
        stripped: dict[str, Any] = {}
        for key in list(_RUNTIME_PAYLOAD_KEYS):
            if key not in frame:
                continue
            value = frame.pop(key, None)
            if isinstance(value, (bytes, bytearray, memoryview)):
                stripped[f"{key}_bytes"] = len(value)
            elif isinstance(value, list):
                stripped[f"{key}_count"] = len(value)
            else:
                stripped[key] = True
        if stripped:
            frame["runtime_payload_retained"] = False
            frame["runtime_payload_discarded_reason"] = reason
            frame["runtime_payload_discarded"] = stripped
        return stripped

    def handle(self, raw_request: dict[str, Any]) -> dict[str, Any]:
        raw_payload = raw_request.get("payload", {}) if isinstance(raw_request, dict) else {}
        violation = self.boundary.check_payload(raw_payload)
        if violation:
            return self._boundary_failure(violation)

        try:
            request = validate_request(raw_request)
        except SchemaError as exc:
            return self._failure("SCHEMA_INVALID", stage="ProtocolGateway", detail=str(exc))

        violation = self.boundary.check_payload(request.payload)
        if violation:
            return self._boundary_failure(violation)

        command = request.command
        try:
            if command == "screen":
                return self._ok(self._screen(request.payload), "screen", append=False)
            if command == "look":
                return self._ok(self._look(request.payload), "look", append=False)
            if command == "do":
                return self._ok(self._do(request.payload), "do", append=False)
            if command == "get_capabilities":
                return self._ok(self._get_capabilities(request.payload), "capability_manifest")
            if command == "observe":
                return self._ok(self._observe(request.payload), "observation")
            if command == "query_visual_memory":
                return self._ok(self._query_visual_memory(request.payload), "visual_memory_query")
            if command == "derive_candidates":
                candidates = self._derive_candidates(request.payload)
                if candidates is None:
                    return self._failure("FLOW_ORDER_VIOLATION", stage="CandidateDeriver", retryable=True)
                return self._ok(candidates, "visual_candidates")
            if command == "create_lease":
                return self._ok(self._create_lease(request.payload), "input_lease")
            if command == "execute_input":
                return self._execute_input(request.payload)
            if command == "get_evidence_package":
                return self._ok(self.evidence.package(), "evidence_package", append=False)
            if command == "read_replay":
                if request.payload.get("reexecute") is True:
                    return self._failure(
                        "REQUEST_FORBIDDEN_BY_BOUNDARY",
                        stage="ReplayReader",
                        boundary_type="replay_reexecute",
                    )
                return self._ok(self.evidence.replay_index(), "replay_index", append=False)
            if command == "verify_integrity":
                return self._ok(self.evidence.verify_integrity(), "integrity", append=False)
            if command == "run_limited_batch":
                return self._ok(self._run_limited_batch(request.payload), "limited_batch")
            if command == "stop":
                return self._ok(self._stop(request.payload), "execution_control")
        except OSError as exc:
            return self._failure(
                "EVIDENCE_RECORD_FAILED",
                stage="EvidenceReplayService",
                evidence_incomplete=True,
                detail=str(exc),
            )
        except ChannelFailure as exc:
            return self._failure(
                exc.failure_code,
                stage=exc.stage,
                retryable=exc.retryable,
                detail=exc.detail,
                input_executed=False,
                channel_ref=exc.channel_ref,
                channel_type=exc.channel_type,
                implementation=exc.implementation,
                requested_mode=exc.requested_mode,
                requested_region=exc.requested_region,
            )

        return self._failure("CAPABILITY_NOT_AVAILABLE", stage="ProtocolGateway")

    def _ok(self, data: dict[str, Any], event_type: str, *, append: bool = True) -> dict[str, Any]:
        transient_content = data.get("_mcp_content") if isinstance(data.get("_mcp_content"), list) else None
        event_data = self._strip_internal_fields({key: value for key, value in data.items() if key != "_mcp_content"})
        response_data = self._with_response_hints(event_data)
        violation = self.boundary.check_output(response_data)
        if violation:
            return self._boundary_failure(violation)
        evidence_ref = None
        if append:
            evidence_ref = self._append_event(event_type, event_data)
        response = {
            "ok": True,
            "data": response_data,
            "evidence_ref": evidence_ref,
            "next_allowed_commands": self._next_allowed_commands(event_type),
        }
        if transient_content:
            response["content"] = transient_content
        return response

    def _failure(
        self,
        failure_code: str,
        *,
        stage: str,
        boundary_type: str | None = None,
        retryable: bool = False,
        evidence_incomplete: bool = False,
        detail: str | None = None,
        boundary_result: dict[str, Any] | None = None,
        input_executed: bool = False,
        channel_ref: str | None = None,
        channel_type: str | None = None,
        implementation: str | None = None,
        requested_mode: str | None = None,
        requested_region: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        failure = self.failures.make_failure(
            failure_code,
            stage=stage,
            boundary_type=boundary_type,
            retryable=retryable,
            evidence_incomplete=evidence_incomplete,
            detail=detail,
        )
        failure["input_executed"] = input_executed
        if channel_ref:
            failure["channel_ref"] = channel_ref
        if channel_type:
            failure["channel_type"] = channel_type
        if implementation:
            failure["implementation"] = implementation
        if requested_mode:
            failure["requested_mode"] = requested_mode
        if requested_region:
            failure["requested_region"] = requested_region
        if boundary_result:
            failure["boundary_result"] = boundary_result
        if not evidence_incomplete:
            try:
                evidence_ref = self._append_event("failure", failure)
                failure["evidence_ref"] = evidence_ref
            except OSError:
                failure["evidence_incomplete"] = True
        return {"ok": False, "failure": failure, "next_allowed_commands": failure.get("suggested_next", [])}

    def _boundary_failure(self, violation: BoundaryViolation) -> dict[str, Any]:
        return self._failure(
            "REQUEST_FORBIDDEN_BY_BOUNDARY",
            stage="BoundaryGuard",
            boundary_type=violation.boundary_type,
            detail=f"{violation.path}={violation.value}",
            boundary_result={
                "checked": True,
                "direction": "input",
                "result": "blocked",
                "boundary_type": violation.boundary_type,
                "path": violation.path,
            },
        )

    def _append_event(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        evidence_payload = self._evidence_payload(event_type, data)
        event_data = {
            **evidence_payload,
            "adapter_ref": self.adapter_ref,
            "schema_ref": schema_ref(),
            "gateway_route": event_type,
            "boundary_result": evidence_payload.get(
                "boundary_result",
                {"checked": True, "direction": "input_output", "result": "passed"},
            ),
        }
        return self.evidence.append(event_type, event_data)

    def _evidence_payload(self, event_type: str, data: dict[str, Any]) -> dict[str, Any]:
        if event_type != "limited_batch":
            return data
        return self._strip_response_only_access(data)

    def _strip_response_only_access(self, value: Any) -> Any:
        if isinstance(value, dict):
            stripped: dict[str, Any] = {}
            for key, child in value.items():
                if str(key).startswith("_"):
                    continue
                if key in {"media_access", "frame_media_access", "sequence_media_access"}:
                    continue
                if key in {"media_path_abs", "sequence_media_path_abs"}:
                    continue
                stripped[key] = self._strip_response_only_access(child)
            return stripped
        if isinstance(value, list):
            return [self._strip_response_only_access(child) for child in value]
        return value

    def _next_allowed_commands(self, event_type: str) -> list[str]:
        if event_type == "screen":
            return ["look", "do", "stop"]
        if event_type == "look":
            return ["look", "do", "screen", "stop"]
        if event_type == "do":
            return ["look", "screen", "stop"]
        if event_type == "capability_manifest":
            return ["screen", "look", "do", "stop"]
        if event_type == "observation":
            return ["query_visual_memory", "derive_candidates", "create_lease", "get_evidence_package", "stop"]
        if event_type == "visual_memory_query":
            return ["observe", "query_visual_memory", "get_evidence_package", "stop"]
        if event_type == "visual_candidates":
            return ["create_lease", "observe", "stop"]
        if event_type == "input_lease":
            return ["execute_input", "stop"]
        if event_type == "input":
            return ["observe", "get_evidence_package", "read_replay", "verify_integrity", "stop"]
        return ["get_capabilities", "observe", "stop"]

    def _get_capabilities(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        probe_mode = payload.get("probe_mode", "cached")
        diagnostics = CaptureDiagnosticsService(self.observation_registry).build_report(probe_mode=probe_mode)
        input_diagnostics = InputDiagnosticsService(self.input_registry).build_report(probe_mode=probe_mode)
        return {
            "object_type": "CapabilityManifest",
            "capabilities": ["screen", "look", "do"],
            "legacy_internal_capabilities": [
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
            ],
            "public_protocol": {
                "schema": "agentsight_screen_look_do_v1",
                "flow": ["screen", "look", "do", "look"],
                "ordinary_ai_tools": ["screen", "look", "do"],
                "legacy_tools_publicly_recommended": False,
                "health_required_as_first_step": False,
                "look_requires_scale_down": True,
                "do_requires_view_basis": True,
                "click_accepts_xy": False,
                "move_then_click_required": True,
                "tool_asserts_target_hit": False,
                "tool_asserts_business_success": False,
            },
            "adapters": [
                {"name": "local_client", "status": "available"},
                {"name": "local_cli", "status": "available"},
                {"name": "mcp_stdio", "status": "available"},
                {"name": "skill", "status": "documented"},
            ],
            "adapter_ref": self.adapter_ref,
            "schema_ref": schema_ref(),
            "ai_usage_guide": self._ai_usage_guide(),
            "channels": [*self.observation_registry.describe_all(), *self.input_registry.describe_all()],
            "capture_diagnostics": diagnostics,
            "input_diagnostics": input_diagnostics,
            "visual_memory": {
                "frame_buffer": self.frame_buffer.status(),
                "current_stage": "p0n_storage_attention_summary",
                "region_change_index": {
                    "schema": "agentsight_p0g_region_change_index_v1",
                    "available_on_sequence_observe": True,
                    "returns_images": False,
                    "derived_metadata": True,
                    "canonical": False,
                    "raw_frames_are_integrity_truth_source": True,
                },
                "visual_evidence_request": {
                    "schema": "agentsight_p0h_visual_evidence_artifacts_v1",
                    "available_on_sequence_observe": True,
                    "artifact_types": ["raw_frame", "raw_crop", "before_after", "diff_heatmap"],
                    "source": "change_events",
                    "max_artifacts": 5,
                    "raw_frame_and_raw_crop_can_be_canonical": True,
                    "diff_heatmap_is_derived_review_only": True,
                },
                "query_api_public": False,
                "query_api_legacy_internal": True,
                "ordinary_public_facade": "look",
                "query_tool": {
                    "name": "query_visual_memory",
                    "role": "legacy_internal_attention_metadata_query",
                    "schema": "agentsight_p0j_visual_memory_query_v1",
                    "available_query_types": [
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
                    ],
                    "metadata_only": True,
                    "no_capture_performed": True,
                    "returns_images": False,
                    "raw_media_returned": False,
                    "derived_review_artifact_returned": False,
                    "ai_reading_summary": {
                        "schema": "agentsight_p0j3_visual_memory_query_reading_summary_v1",
                        "summary_kind": "deterministic_metadata_only",
                        "freeform_language_generated": False,
                        "semantic_interpretation_in_tool": False,
                        "action_completion_judged": False,
                    },
                    "granular_future_tools_public": False,
                    "retention_prune_plan": {
                        "schema": "agentsight_p0l_visual_memory_retention_v1",
                        "query_types": ["retention_status", "prune_unreferenced_buffer"],
                        "dry_run_only": True,
                        "prune_applied": False,
                        "prune_deletes_raw_media": False,
                        "canonical_raw_evidence_not_deleted_by_buffer": True,
                    },
                    "retention_class_projection": {
                        "schema": "agentsight_p0m_visual_memory_retention_class_projection_v1",
                        "query_type": "retention_class_projection",
                        "metadata_only": True,
                        "pin_state_written": False,
                        "semantic_importance_judged": False,
                        "receipt_evidence_references_inspected": False,
                    },
                    "storage_attention_summary": {
                        "schema": "agentsight_p0n_visual_memory_storage_attention_summary_v1",
                        "query_type": "storage_attention_summary",
                        "metadata_only": True,
                        "combines_existing_metadata_views": True,
                        "tool_recommends_delete": False,
                        "tool_recommends_keep": False,
                        "semantic_importance_judged": False,
                    },
                },
                "video_encoding_status": "not_implemented",
            },
            "boundaries": ["no_window_semantics", "no_command_line", "no_clipboard", "no_background_actions"],
            "unavailable_capabilities": [],
        }

    def _observe(
        self,
        payload: dict[str, Any],
        *,
        attention_scope: str = "observe",
        frame_source: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        if payload.get("mode", "fullscreen") == "sequence":
            return self._observe_sequence(payload)

        channel = self.observation_registry.resolve(payload.get("channel_ref"))
        descriptor = channel.describe()
        try:
            frame = channel.capture(payload, self.evidence)
        except ChannelFailure as exc:
            exc.channel_ref = exc.channel_ref or channel.name
            exc.channel_type = exc.channel_type or channel.channel_type
            exc.requested_mode = exc.requested_mode or payload.get("mode", "fullscreen")
            exc.requested_region = exc.requested_region or payload.get("region")
            exc.implementation = exc.implementation or descriptor.get("implementation")
            raise
        self._enrich_observation_frame(frame, descriptor)
        if frame.get("capture_content_degenerate"):
            frame["segment_frame"] = self.segment_recorder._not_recorded(
                "capture_content_degenerate",
                frame=frame,
                source=frame_source or attention_scope,
                event_id=event_id,
                view_id=None,
            )
        else:
            frame["segment_frame"] = self.segment_recorder.record_frame(
                frame,
                source=frame_source or attention_scope,
                event_id=event_id,
            )
        frame["frame_buffer"] = self.frame_buffer.remember_frame(
            frame,
            attention_scope=attention_scope,
            source=frame_source,
            event_id=event_id,
        )
        observation_id = frame["observation_id"]
        self._remember_runtime_observation(observation_id, frame)
        self.last_observation_id = observation_id
        return frame

    def _screen(self, payload: dict[str, Any]) -> dict[str, Any]:
        virtual = self._current_virtual_screen()
        screen_frame_index = self._screen_frame_index(payload, virtual)
        return {
            "object_type": "ScreenLayout",
            "schema": "agentsight_screen_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": True,
            "virtual": {"x": virtual["x"], "y": virtual["y"], "w": virtual["w"], "h": virtual["h"]},
            "monitors": [
                {
                    "id": "m1",
                    "primary": True,
                    "x": virtual["x"],
                    "y": virtual["y"],
                    "w": virtual["w"],
                    "h": virtual["h"],
                }
            ],
            "coordinate_system": "virtual_screen_pixels",
            "readonly": True,
            "screen_frame_index": screen_frame_index,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }

    def capture_idle_frame(self, *, policy: dict[str, Any], now_ms: int | None = None) -> dict[str, Any]:
        """Capture one idle frame if the human-visible idle policy says it is due.

        This is a tick method, not a background thread. The supervisor/host can
        call it from a loop later without changing the public /screen/look/do
        protocol.
        """

        now = int(now_ms if now_ms is not None else time.time() * 1000)
        idle = ((policy.get("recording") or {}).get("idle_capture") or {}) if isinstance(policy, dict) else {}
        continuous = bool(policy.get("continuous_recording_enabled")) if isinstance(policy, dict) else False
        enabled = continuous and bool(idle.get("enabled"))
        fps = _coerce_idle_fps(idle.get("fps"))
        interval_ms = max(1, int(round(1000.0 / fps)))
        if not enabled:
            compacted = None
            if self._last_idle_capture_enabled:
                compacted = self._compact_runtime_payloads(reason="idle_capture_disabled")
            self._last_idle_capture_enabled = False
            return self._idle_capture_report(
                captured=False,
                reason="idle_capture_disabled",
                now_ms=now,
                interval_ms=interval_ms,
                compaction=compacted,
            )
        if self._last_idle_capture_ms is not None and now - self._last_idle_capture_ms < interval_ms:
            self._last_idle_capture_enabled = True
            return self._idle_capture_report(captured=False, reason="idle_interval_not_elapsed", now_ms=now, interval_ms=interval_ms)
        readiness = self._public_readiness_fields().get("readiness") or {}
        if isinstance(readiness, dict) and not readiness.get("ok", True):
            self._last_idle_capture_enabled = True
            return self._idle_capture_report(
                captured=False,
                reason="readiness_blocked",
                now_ms=now,
                interval_ms=interval_ms,
                readiness=readiness,
            )
        frame = self._observe({"mode": "fullscreen"}, attention_scope="idle", frame_source="idle", event_id=f"idle-{now}")
        self._last_idle_capture_ms = now
        self._last_idle_capture_enabled = True
        self._discard_frame_runtime_payload(frame, reason="idle_capture_recorded_to_segment")
        return self._idle_capture_report(captured=True, reason=None, now_ms=now, interval_ms=interval_ms, frame=frame)

    def _idle_capture_report(
        self,
        *,
        captured: bool,
        reason: str | None,
        now_ms: int,
        interval_ms: int,
        frame: dict[str, Any] | None = None,
        compaction: dict[str, Any] | None = None,
        readiness: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "object_type": "AgentSightIdleCaptureTickReport",
            "schema": "agentsight_idle_capture_tick_v1",
            "captured": bool(captured),
            "skip_reason": reason,
            "now_ms": now_ms,
            "idle_interval_ms": interval_ms,
            "frame_ref": frame.get("observation_id") if isinstance(frame, dict) else None,
            "segment_frame": frame.get("segment_frame") if isinstance(frame, dict) else None,
            "readiness": readiness,
            "runtime_memory_compaction": compaction,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "boundary": self._public_boundary_facts(),
        }

    def _screen_frame_index(self, payload: dict[str, Any], virtual: dict[str, int]) -> dict[str, Any]:
        try:
            frame = self._observe(
                {
                    "mode": "fullscreen",
                    "region": {"x": virtual["x"], "y": virtual["y"], "width": virtual["w"], "height": virtual["h"]},
                },
                attention_scope="screen",
                frame_source="screen",
                event_id=payload.get("id"),
            )
        except ChannelFailure as exc:
            return {
                "object_type": "ScreenFrameIndexStatus",
                "schema": self.frame_buffer.schema,
                "status": "not_indexed",
                "indexed": False,
                "source": "screen",
                "not_indexed_reason": exc.failure_code,
                "detail": exc.detail,
                "retryable": exc.retryable,
                "requested_region": {"x": virtual["x"], "y": virtual["y"], "w": virtual["w"], "h": virtual["h"]},
                "raw_media_returned": False,
                "image_bytes_returned": False,
                "tool_asserts_business_success": False,
            }
        frame_buffer = frame.get("frame_buffer") if isinstance(frame.get("frame_buffer"), dict) else None
        self._discard_frame_runtime_payload(frame, reason="screen_frame_index_recorded_to_segment")
        return {
            "object_type": "ScreenFrameIndexStatus",
            "schema": self.frame_buffer.schema,
            "status": "indexed" if frame_buffer and frame_buffer.get("indexed") is not False else "not_indexed",
            "indexed": bool(frame_buffer and frame_buffer.get("indexed") is not False),
            "source": "screen",
            "observation_ref": frame.get("observation_id"),
            "frame_buffer": frame_buffer,
            "requested_region": {"x": virtual["x"], "y": virtual["y"], "w": virtual["w"], "h": virtual["h"]},
            "raw_media_returned": False,
            "image_bytes_returned": False,
            "tool_asserts_business_success": False,
        }

    def _current_virtual_screen(self) -> dict[str, int]:
        regions: list[dict[str, int]] = []
        for observation in self.observations.values():
            region = self._screen_rect_from_observation(observation)
            if region:
                regions.append(region)
        if not regions:
            return {"x": 0, "y": 0, "w": 1920, "h": 1080}
        left = min(region["x"] for region in regions)
        top = min(region["y"] for region in regions)
        right = max(region["x"] + region["w"] for region in regions)
        bottom = max(region["y"] + region["h"] for region in regions)
        return {"x": left, "y": top, "w": max(1, right - left), "h": max(1, bottom - top)}

    def _look(self, payload: dict[str, Any]) -> dict[str, Any]:
        query_type = str(payload.get("q"))
        if query_type == "diff":
            return self._look_diff(payload)
        if query_type == "changes":
            return self._look_changes(payload)
        if query_type == "clip":
            return self._look_clip(payload)
        if query_type != "frame":
            raise ChannelFailure(
                "LOOK_QUERY_NOT_AVAILABLE",
                stage="LookProtocol",
                detail=f"look q={query_type!r} is reserved for a later stage",
                retryable=False,
            )

        src = payload.get("src") if isinstance(payload.get("src"), dict) else {}
        rect = payload.get("r") if isinstance(payload.get("r"), dict) else {}
        scale_down = int(payload["scale_down"])
        parent_view: dict[str, Any] | None = None
        source_rect_in_parent: dict[str, int] | None = None
        if src.get("type") == "view":
            parent_view = self.views.get(str(src.get("view_id")))
            if not parent_view:
                raise ChannelFailure(
                    "VIEW_NOT_FOUND",
                    stage="LookProtocol.view_index",
                    detail="src.view_id does not refer to a known view",
                    retryable=True,
                )
            self._ensure_view_can_be_current_screen_source(parent_view, stage="LookProtocol.view_index")
            source_rect_in_parent = self._rect_payload_to_screen_rect(rect)
            self._validate_view_rect_in_bounds(parent_view, source_rect_in_parent, stage="LookProtocol.view_index")
            screen_rect = self._screen_rect_from_parent_view(parent_view, source_rect_in_parent)
        else:
            screen_rect = self._rect_payload_to_screen_rect(rect)

        time_query = payload.get("time") if isinstance(payload.get("time"), dict) else None
        if time_query and any(key in time_query for key in ("near", "at", "requested_time")):
            return self._look_frames_near_time(payload, screen_rect=screen_rect)

        frame = self._observe(
            {"mode": "region", "region": self._region_from_screen_rect(screen_rect)},
            attention_scope="look",
            frame_source="look",
            event_id=payload.get("id"),
        )
        view = self._remember_view(
            frame=frame,
            screen_rect=screen_rect,
            scale_down=scale_down,
            parent_view_id=parent_view.get("view", {}).get("id") if parent_view else None,
            source_rect_in_parent=source_rect_in_parent,
            request_id=payload.get("id"),
        )
        frame_buffer_ref = self.frame_buffer.update_entry_metadata(
            frame.get("observation_id"),
            view_id=view["view"]["id"],
            event_id=payload.get("id"),
            source="look",
        )
        if frame_buffer_ref:
            frame["frame_buffer"] = frame_buffer_ref
        self._discard_frame_runtime_payload(frame, reason="look_mcp_content_generated")
        response_src = dict(src)
        response_src.setdefault("t", "latest")
        if frame.get("captured_at") or frame.get("timestamp"):
            response_src["source_time"] = self._format_hms_ms(float(frame.get("captured_at") or frame.get("timestamp")))
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": True,
            "type": "frame",
            "view": view["view"],
            "view_record": view["public_record"],
            "image_content_returned": bool(view.get("mcp_content")),
            "image_content_type": "mcp_image_content",
            "derived_review_file_written": False,
            "raw_or_derived": "derived_review_only",
            "src": response_src,
            "r": {
                **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
                "unit": "parent_view_px" if parent_view else "virtual_screen_px",
            },
            "capture_content_degenerate": False,
            "tool_asserts_target_found": False,
            "tool_asserts_business_success": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
            "_mcp_content": view.get("mcp_content") or [],
        }

    def _look_frames_near_time(self, payload: dict[str, Any], *, screen_rect: dict[str, int]) -> dict[str, Any]:
        time_query = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        requested_time = time_query.get("near", time_query.get("at", time_query.get("requested_time")))
        result = self.frame_buffer.frames_near_time(requested_time)
        if result.get("query_status") != "generated":
            return self._look_segment_frames_near_time(payload, screen_rect=screen_rect, requested_time=requested_time)
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": result.get("query_status") == "generated",
            "type": "time_near_frames",
            "mode": "nearest_indexed_frames",
            "src": payload.get("src"),
            "r": {
                "x": int(screen_rect["x"]),
                "y": int(screen_rect["y"]),
                "w": int(screen_rect["w"]),
                "h": int(screen_rect["h"]),
                "unit": "virtual_screen_px",
            },
            "time": time_query,
            "frames_near_time": result,
            "raw_media_returned": False,
            "media_paths_returned": bool(result.get("media_paths_returned")),
            "image_bytes_returned": False,
            "no_capture_performed": True,
            "tool_asserts_target_found": False,
            "tool_asserts_business_success": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }

    def _look_segment_frames_near_time(self, payload: dict[str, Any], *, screen_rect: dict[str, int], requested_time: Any) -> dict[str, Any]:
        time_query = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        near = query_segment_decoder_near_time(self.evidence.root.parent, requested_time)
        nearest = near.get("nearest_frame") if isinstance(near.get("nearest_frame"), dict) else None
        decoded_review: dict[str, Any] | None = None
        decode_errors: list[dict[str, Any]] = []
        decode_error: dict[str, Any] | None = None
        historical_view: dict[str, Any] | None = None
        view_record: dict[str, Any] | None = None
        coordinate_unit = "stored_frame_px"
        for candidate in self._time_near_decode_candidates(near):
            if not isinstance(candidate.get("segment_restore_ref"), dict):
                continue
            view_id = f"sv_{uuid.uuid4().hex[:8]}"
            decode_region = self._historical_decode_region(screen_rect, candidate)
            if decode_region.get("status") == "no_overlap":
                decode_errors.append(
                    {
                        "status": "decode_skipped_no_overlap",
                        "segment_id": candidate.get("segment_id"),
                        "segment_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
                        "frame_id": candidate.get("frame_id"),
                        "relation": candidate.get("relation"),
                        "decode_region_basis": decode_region,
                        "tool_asserts_business_success": False,
                        "tool_asserts_causality": False,
                        "tool_asserts_target_hit": False,
                    }
                )
                if coordinate_unit == "stored_frame_px":
                    coordinate_unit = decode_region["unit"]
                continue
            try:
                decoded_review = decode_segment_region_to_image_content(
                    candidate["segment_restore_ref"],
                    region=decode_region["region"],
                    scale_down=int(payload.get("scale_down") or 1),
                )
                mcp_content = decoded_review.pop("mcp_content", [])
                decoded_review["requested_screen_region"] = dict(screen_rect)
                decoded_review["decode_region_basis"] = decode_region
                decoded_review["selected_segment_frame"] = {
                    "segment_id": candidate.get("segment_id"),
                    "segment_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
                    "frame_id": candidate.get("frame_id"),
                    "relation": candidate.get("relation"),
                    "delta_ms": candidate.get("delta_ms"),
                }
                historical_view = {
                    "id": view_id,
                    "w": decoded_review.get("region", {}).get("w"),
                    "h": decoded_review.get("region", {}).get("h"),
                    "scale_down": payload.get("scale_down"),
                    "view_is_current_action_basis": False,
                    "view_role": "historical_segment_review",
                    "image_content_returned": bool(mcp_content),
                    "derived_review_file_written": False,
                }
                view_record = self._historical_view_record(
                    view_id=view_id,
                    payload=payload,
                    candidate=candidate,
                    decoded_review=decoded_review,
                    decode_region=decode_region,
                    screen_rect=screen_rect,
                )
                self._remember_runtime_view(
                    view_id=view_id,
                    record=self._historical_view_index_entry(
                        view_id=view_id,
                        historical_view=historical_view,
                        view_record=view_record,
                        screen_rect=screen_rect,
                        request_id=payload.get("id"),
                    ),
                )
                decoded_review["image_content_returned"] = bool(mcp_content)
                decoded_review["_mcp_content"] = mcp_content
                coordinate_unit = decode_region["unit"]
                break
            except Exception as exc:
                decode_errors.append(
                    {
                        "status": "decode_failed",
                        "segment_id": candidate.get("segment_id"),
                        "segment_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
                        "frame_id": candidate.get("frame_id"),
                        "relation": candidate.get("relation"),
                        "error_type": type(exc).__name__,
                        "detail": str(exc),
                        "tool_asserts_business_success": False,
                        "tool_asserts_causality": False,
                        "tool_asserts_target_hit": False,
                    }
                )
        if decoded_review is None and decode_errors:
            decode_error = {
                "status": "decode_failed",
                "attempt_count": len(decode_errors),
                "attempts": decode_errors,
                "tool_asserts_business_success": False,
                "tool_asserts_causality": False,
                "tool_asserts_target_hit": False,
            }
        mcp_content = decoded_review.pop("_mcp_content", []) if isinstance(decoded_review, dict) else []
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": near.get("query_status") == "generated",
            "type": "time_near_frames",
            "mode": "segment_decoder_nearest_indexed_frames",
            "src": payload.get("src"),
            "r": {
                "x": int(screen_rect["x"]),
                "y": int(screen_rect["y"]),
                "w": int(screen_rect["w"]),
                "h": int(screen_rect["h"]),
                "unit": coordinate_unit if nearest else "stored_frame_px",
                "coordinate_caveat": None
                if nearest and coordinate_unit != "stored_frame_px"
                else "historical Segment frame lacks screen_region metadata; r was decoded as stored-frame pixels",
            },
            "time": time_query,
            "frames_near_time": near,
            "historical_view": historical_view,
            "view_record": view_record,
            "decoded_review": decoded_review,
            "decode_error": decode_error,
            "decode_errors": decode_errors,
            "raw_media_returned": False,
            "decoded_review_returned": decoded_review is not None,
            "image_bytes_returned": bool(mcp_content),
            "image_content_returned": bool(mcp_content),
            "no_capture_performed": True,
            "view_is_current_action_basis": False,
            "tool_asserts_target_found": False,
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
            "_mcp_content": mcp_content,
        }

    def _historical_view_record(
        self,
        *,
        view_id: str,
        payload: dict[str, Any],
        candidate: dict[str, Any],
        decoded_review: dict[str, Any],
        decode_region: dict[str, Any],
        screen_rect: dict[str, int],
    ) -> dict[str, Any]:
        scale_down = int(payload.get("scale_down") or decoded_review.get("scale_down") or 1)
        region = decoded_review.get("region") if isinstance(decoded_review.get("region"), dict) else decode_region.get("region")
        region = dict(region) if isinstance(region, dict) else dict(screen_rect)
        output_w = max(1, (int(region["w"]) + scale_down - 1) // scale_down)
        output_h = max(1, (int(region["h"]) + scale_down - 1) // scale_down)
        restore_ref = candidate.get("segment_restore_ref") if isinstance(candidate.get("segment_restore_ref"), dict) else None
        transform = {
            "schema": "agentsight_view_transform_v1",
            "coordinate_system": "historical_view_pixels_to_requested_screen_pixels",
            "view_pixels_to_virtual_screen_pixels": {
                "origin_x": int(screen_rect["x"]),
                "origin_y": int(screen_rect["y"]),
                "scale_x": scale_down,
                "scale_y": scale_down,
                "formula": "screen_x=origin_x+view_x*scale_x; screen_y=origin_y+view_y*scale_y",
            },
            "view_is_current_action_basis": False,
            "blur_changes_coordinates": False,
            "cursor_overlay_changes_coordinates": False,
        }
        return {
            "view_id": view_id,
            "created_at": self._format_hms_ms(time.time()),
            "view_role": "historical_segment_review",
            "view_is_current_action_basis": False,
            "source_frame_ref": candidate.get("frame_id"),
            "source_frame_id": candidate.get("segment_frame_id") or candidate.get("frame_id"),
            "segment_restore_ref": restore_ref,
            "source_segment_path": restore_ref.get("segment_path") if isinstance(restore_ref, dict) else None,
            "segment_id": candidate.get("segment_id"),
            "requested_screen_region": dict(screen_rect),
            "actual_decoded_region": region,
            "output_image_size": {"w": output_w, "h": output_h},
            "scale_down": scale_down,
            "blur": bool(decoded_review.get("blur_radius")),
            "blur_radius": int(decoded_review.get("blur_radius") or 0),
            "cursor_mode": "none",
            "raw_or_derived": "derived_review_only",
            "coordinate_system": decode_region.get("unit", "stored_frame_px"),
            "transform": transform,
            "capture_content_degenerate": bool(candidate.get("capture_content_degenerate")),
            "request_id": payload.get("id"),
            "operation_log_linkage": {"request_id": payload.get("id"), "route": "/look"},
            "derived_review_file_written": False,
            "canonical_evidence_storage": ".mkv/raw_observation",
        }

    def _historical_view_index_entry(
        self,
        *,
        view_id: str,
        historical_view: dict[str, Any],
        view_record: dict[str, Any],
        screen_rect: dict[str, int],
        request_id: Any,
    ) -> dict[str, Any]:
        return {
            "object_type": "AgentSightViewIndexEntry",
            "schema": "agentsight_view_index_v1",
            "request_id": request_id,
            "view": historical_view,
            "public_record": view_record,
            "screen_rect": dict(screen_rect),
            "source_rect_in_parent": None,
            "source_timestamp": time.time(),
            "source_time": self._format_hms_ms(time.time()),
            "segment_restore_ref": view_record.get("segment_restore_ref"),
            "source_frame_id": view_record.get("source_frame_id"),
            "source_segment_path": view_record.get("source_segment_path"),
            "transform": view_record.get("transform"),
            "coordinate_system": view_record.get("coordinate_system"),
            "raw_or_derived": "derived_review_only",
            "cursor_mode": view_record.get("cursor_mode"),
            "capture_content_degenerate": bool(view_record.get("capture_content_degenerate")),
            "scale_down": int(view_record.get("scale_down") or 1),
            "view_role": "historical_segment_review",
            "view_is_current_action_basis": False,
            "view_is_derived_review": True,
            "derived_review_file_written": False,
            "mcp_content": [],
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def _time_near_decode_candidates(self, near: dict[str, Any]) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        ordered = [
            near.get("nearest_frame"),
            near.get("before_frame"),
            near.get("after_frame"),
            *(near.get("frames") or []),
        ]
        for candidate in ordered:
            if not isinstance(candidate, dict):
                continue
            restore_ref = candidate.get("segment_restore_ref") if isinstance(candidate.get("segment_restore_ref"), dict) else {}
            key = (str(candidate.get("segment_path_abs") or restore_ref.get("segment_path")), str(candidate.get("frame_id")))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        return candidates

    def _historical_decode_region(self, screen_rect: dict[str, int], frame: dict[str, Any]) -> dict[str, Any]:
        stored_region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else None
        coordinate_system = frame.get("coordinate_system")
        if coordinate_system in {"virtual_screen_pixels", "monitor_pixels"} and stored_region:
            x0 = int(stored_region.get("x", 0))
            y0 = int(stored_region.get("y", 0))
            w0 = int(stored_region.get("w", stored_region.get("width", 0)))
            h0 = int(stored_region.get("h", stored_region.get("height", 0)))
            left = max(int(screen_rect["x"]), x0)
            top = max(int(screen_rect["y"]), y0)
            right = min(int(screen_rect["x"]) + int(screen_rect["w"]), x0 + w0)
            bottom = min(int(screen_rect["y"]) + int(screen_rect["h"]), y0 + h0)
            if right > left and bottom > top:
                return {
                    "unit": "virtual_screen_px",
                    "source_coordinate_system": coordinate_system,
                    "stored_frame_region": {"x": x0, "y": y0, "w": w0, "h": h0},
                    "requested_screen_region": dict(screen_rect),
                    "region": {"x": left - x0, "y": top - y0, "w": right - left, "h": bottom - top},
                    "clipped_to_stored_frame": left != int(screen_rect["x"])
                    or top != int(screen_rect["y"])
                    or right != int(screen_rect["x"]) + int(screen_rect["w"])
                    or bottom != int(screen_rect["y"]) + int(screen_rect["h"]),
                }
            return {
                "status": "no_overlap",
                "unit": "virtual_screen_px",
                "source_coordinate_system": coordinate_system,
                "stored_frame_region": {"x": x0, "y": y0, "w": w0, "h": h0},
                "requested_screen_region": dict(screen_rect),
                "clipped_to_stored_frame": True,
            }
        return {
            "status": "stored_frame_fallback",
            "unit": "stored_frame_px",
            "source_coordinate_system": coordinate_system,
            "stored_frame_region": stored_region,
            "requested_screen_region": dict(screen_rect),
            "region": {"x": int(screen_rect["x"]), "y": int(screen_rect["y"]), "w": int(screen_rect["w"]), "h": int(screen_rect["h"])},
            "clipped_to_stored_frame": False,
        }

    def _safe_file_token(self, value: str) -> str:
        cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(value))
        return cleaned.strip("-_")[:80] or f"look-{uuid.uuid4().hex[:8]}"

    def _look_changes(self, payload: dict[str, Any]) -> dict[str, Any]:
        src = payload.get("src") if isinstance(payload.get("src"), dict) else {}
        rect = payload.get("r") if isinstance(payload.get("r"), dict) else {}
        parent_view: dict[str, Any] | None = None
        source_rect_in_parent: dict[str, int] | None = None
        if src.get("type") == "view":
            parent_view = self.views.get(str(src.get("view_id")))
            if not parent_view:
                raise ChannelFailure(
                    "VIEW_NOT_FOUND",
                    stage="LookProtocol.changes.view_index",
                    detail="src.view_id does not refer to a known view",
                    retryable=True,
                )
            self._ensure_view_can_be_current_screen_source(parent_view, stage="LookProtocol.changes.view_index")
            source_rect_in_parent = self._rect_payload_to_screen_rect(rect)
            self._validate_view_rect_in_bounds(parent_view, source_rect_in_parent, stage="LookProtocol.changes.view_index")
            screen_rect = self._screen_rect_from_parent_view(parent_view, source_rect_in_parent)
        else:
            screen_rect = self._rect_payload_to_screen_rect(rect)
        time_query = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        changes = query_segment_change_index(
            self.evidence.root.parent,
            region=screen_rect,
            region_coordinate_system="virtual_screen_pixels",
            max_pairs=int(payload.get("max_pairs", 128)),
            min_changed_pixel_ratio=float(payload.get("min_changed_pixel_ratio", 0.0)),
            start_time=time_query.get("from"),
            end_time=time_query.get("to"),
        )
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": True,
            "type": "changes",
            "mode": "segment_metadata_change_index",
            "src": payload.get("src"),
            "r": {
                **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
                "unit": "parent_view_px" if parent_view else "virtual_screen_px",
                "coordinate_caveat": "changes maps r through indexed Segment screen_region metadata when available; it does not capture or inspect the live screen",
            },
            "screen_region": dict(screen_rect),
            "parent_view": {"id": parent_view.get("view", {}).get("id")} if parent_view else None,
            "source_rect_in_parent": source_rect_in_parent,
            "time": time_query,
            "changes": changes,
            "raw_media_returned": False,
            "image_bytes_returned": False,
            "derived_review_artifact_returned": False,
            "no_capture_performed": True,
            "no_media_exported": True,
            "view_is_current_action_basis": False,
            "tool_asserts_target_found": False,
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }

    def _look_clip(self, payload: dict[str, Any]) -> dict[str, Any]:
        src = payload.get("src") if isinstance(payload.get("src"), dict) else {}
        rect = payload.get("r") if isinstance(payload.get("r"), dict) else {}
        parent_view: dict[str, Any] | None = None
        source_rect_in_parent: dict[str, int] | None = None
        if src.get("type") == "view":
            parent_view = self.views.get(str(src.get("view_id")))
            if not parent_view:
                raise ChannelFailure(
                    "VIEW_NOT_FOUND",
                    stage="LookProtocol.clip.view_index",
                    detail="src.view_id does not refer to a known view",
                    retryable=True,
                )
            self._ensure_view_can_be_current_screen_source(parent_view, stage="LookProtocol.clip.view_index")
            source_rect_in_parent = self._rect_payload_to_screen_rect(rect)
            self._validate_view_rect_in_bounds(parent_view, source_rect_in_parent, stage="LookProtocol.clip.view_index")
            screen_rect = self._screen_rect_from_parent_view(parent_view, source_rect_in_parent)
        else:
            screen_rect = self._rect_payload_to_screen_rect(rect)
        time_query = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        clip = query_segment_review_clip(
            self.evidence.root.parent,
            region=screen_rect,
            region_coordinate_system="virtual_screen_pixels",
            start_time=time_query.get("from"),
            end_time=time_query.get("to"),
            max_frames=int(payload.get("max_frames", 32)),
            scale_down=int(payload.get("scale_down", 1)),
            max_artifacts=int(payload.get("max_artifacts", 0)),
            output_dir=self.evidence.media_dir,
            request_id=str(payload.get("id") or "look-clip"),
        )
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": True,
            "type": "clip",
            "mode": "segment_review_clip",
            "src": payload.get("src"),
            "r": {
                **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
                "unit": "parent_view_px" if parent_view else "virtual_screen_px",
                "coordinate_caveat": "clip maps r through indexed Segment screen_region metadata when available; it does not capture or inspect the live screen",
            },
            "screen_region": dict(screen_rect),
            "parent_view": {"id": parent_view.get("view", {}).get("id")} if parent_view else None,
            "source_rect_in_parent": source_rect_in_parent,
            "time": time_query,
            "clip": clip,
            "artifacts": clip.get("artifacts") or [],
            "raw_media_returned": False,
            "image_bytes_returned": False,
            "derived_review_artifact_returned": bool(clip.get("artifacts")),
            "derived_artifacts_are_canonical": False,
            "no_capture_performed": True,
            "no_media_exported": not bool(clip.get("artifacts")),
            "view_is_current_action_basis": False,
            "tool_asserts_target_found": False,
            "tool_asserts_business_success": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }

    def _look_diff(self, payload: dict[str, Any]) -> dict[str, Any]:
        src = payload.get("src") if isinstance(payload.get("src"), dict) else {}
        mode = str(payload.get("mode") or ("endpoints" if src.get("type") == "view" else "timeline"))
        if mode != "endpoints":
            return self._look_diff_timeline(payload, mode=mode)
        if src.get("type") != "view" or not src.get("view_id"):
            raise ChannelFailure(
                "LOOK_DIFF_REQUIRES_VIEW_SOURCE",
                stage="LookProtocol.diff",
                detail="look q=diff compares a previous view against the latest pixels for the same visible region",
                retryable=False,
            )
        parent_view = self.views.get(str(src.get("view_id")))
        if not parent_view:
            raise ChannelFailure(
                "VIEW_NOT_FOUND",
                stage="LookProtocol.diff.view_index",
                detail="src.view_id does not refer to a known view",
                retryable=True,
            )
        self._ensure_view_can_be_current_screen_source(parent_view, stage="LookProtocol.diff.view_index")
        rect = payload.get("r") if isinstance(payload.get("r"), dict) else {}
        source_rect_in_parent = self._rect_payload_to_screen_rect(rect)
        self._validate_view_rect_in_bounds(parent_view, source_rect_in_parent, stage="LookProtocol.diff.view_index")
        screen_rect = self._screen_rect_from_parent_view(parent_view, source_rect_in_parent)
        frame = self._observe({"mode": "region", "region": self._region_from_screen_rect(screen_rect)})
        after_view = self._remember_view(
            frame=frame,
            screen_rect=screen_rect,
            scale_down=int(payload["scale_down"]),
            parent_view_id=parent_view.get("view", {}).get("id"),
            source_rect_in_parent=source_rect_in_parent,
            request_id=payload.get("id"),
        )
        diff = self._diff_view_against_latest(
            parent_view=parent_view,
            screen_rect=screen_rect,
            after_frame=frame,
            after_view=after_view,
            max_artifacts=int(payload.get("max_artifacts", 0) or 0),
            request_id=str(payload.get("id") or "look-diff"),
        )
        self._discard_frame_runtime_payload(frame, reason="look_diff_view_generated")
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": True,
            "type": "diff",
            "mode": "endpoint_latest_vs_view_baseline",
            "src": {"type": "view", "view_id": parent_view.get("view", {}).get("id"), "source_time": parent_view.get("source_time")},
            "baseline_view": {
                "id": parent_view.get("view", {}).get("id"),
                "path": parent_view.get("view", {}).get("path"),
                "source_time": parent_view.get("source_time"),
            },
            "after_view": after_view["view"],
            "r": {
                **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
                "unit": "parent_view_px",
            },
            "screen_region": dict(screen_rect),
            "diffs": [diff["comparison"]],
            "summary": diff["summary"],
            "artifacts": diff["artifacts"],
            "raw_media_returned": False,
            "derived_review_artifact_returned": bool(diff["artifacts"]),
            "derived_artifacts_are_canonical": False,
            "tool_asserts_semantic_change": False,
            "tool_asserts_target_hit": False,
            "tool_asserts_business_success": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }

    def _look_diff_timeline(self, payload: dict[str, Any], *, mode: str) -> dict[str, Any]:
        src = payload.get("src") if isinstance(payload.get("src"), dict) else {}
        rect = payload.get("r") if isinstance(payload.get("r"), dict) else {}
        parent_view: dict[str, Any] | None = None
        source_rect_in_parent: dict[str, int] | None = None
        if src.get("type") == "view":
            parent_view = self.views.get(str(src.get("view_id")))
            if not parent_view:
                raise ChannelFailure(
                    "VIEW_NOT_FOUND",
                    stage="LookProtocol.diff.timeline.view_index",
                    detail="src.view_id does not refer to a known view",
                    retryable=True,
                )
            self._ensure_view_can_be_current_screen_source(parent_view, stage="LookProtocol.diff.timeline.view_index")
            source_rect_in_parent = self._rect_payload_to_screen_rect(rect)
            self._validate_view_rect_in_bounds(parent_view, source_rect_in_parent, stage="LookProtocol.diff.timeline.view_index")
            screen_rect = self._screen_rect_from_parent_view(parent_view, source_rect_in_parent)
        else:
            screen_rect = self._rect_payload_to_screen_rect(rect)
        time_query = payload.get("time") if isinstance(payload.get("time"), dict) else {}
        max_artifacts = int(payload.get("max_artifacts", 0) or 0) if mode == "timeline_with_artifacts" else 0
        timeline = query_segment_timeline_diff(
            self.evidence.root.parent,
            region=screen_rect,
            region_coordinate_system="virtual_screen_pixels",
            start_time=time_query.get("from"),
            end_time=time_query.get("to"),
            max_artifacts=max_artifacts,
            output_dir=self.evidence.media_dir,
            request_id=str(payload.get("id") or "look-diff-timeline"),
        )
        return {
            "object_type": "LookResult",
            "schema": "agentsight_look_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": True,
            "type": "diff",
            "mode": "timeline_segment_diff",
            "requested_mode": mode,
            "src": payload.get("src"),
            "r": {
                **{key: int(rect[key]) for key in ("x", "y", "w", "h")},
                "unit": "parent_view_px" if parent_view else "virtual_screen_px",
                "coordinate_caveat": "diff timeline maps r through indexed Segment screen_region metadata when available; it does not capture or inspect the live screen",
            },
            "screen_region": dict(screen_rect),
            "parent_view": {"id": parent_view.get("view", {}).get("id")} if parent_view else None,
            "source_rect_in_parent": source_rect_in_parent,
            "time": time_query,
            "diffs": timeline,
            "summary": {
                "status": "computed",
                "frame_pairs": timeline.get("change_count", 0),
                "computed_comparison_count": timeline.get("change_count", 0),
                "artifact_count": timeline.get("artifact_count", 0),
                "changed": bool(timeline.get("change_count", 0)),
                "tool_asserts_business_success": False,
                "tool_asserts_causality": False,
                "tool_asserts_target_hit": False,
            },
            "artifacts": timeline.get("artifacts") or [],
            "raw_media_returned": False,
            "image_bytes_returned": False,
            "derived_review_artifact_returned": bool(timeline.get("artifacts")),
            "derived_artifacts_are_canonical": False,
            "no_capture_performed": True,
            "no_media_exported": not bool(timeline.get("artifacts")),
            "view_is_current_action_basis": False,
            "tool_asserts_semantic_change": False,
            "tool_asserts_causality": False,
            "tool_asserts_target_hit": False,
            "tool_asserts_business_success": False,
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }

    def _diff_view_against_latest(
        self,
        *,
        parent_view: dict[str, Any],
        screen_rect: dict[str, int],
        after_frame: dict[str, Any],
        after_view: dict[str, Any],
        max_artifacts: int,
        request_id: str,
    ) -> dict[str, Any]:
        baseline_path = self._path_or_none(parent_view.get("source_media_path_abs"))
        after_path = self._media_path_abs(after_frame)
        comparison = self._look_diff_not_computed(
            before_view_id=parent_view.get("view", {}).get("id"),
            after_view_id=after_view.get("view", {}).get("id"),
            reason="frame_media_path_missing",
            screen_rect=screen_rect,
        )
        artifacts: list[dict[str, Any]] = []
        if baseline_path and after_path:
            try:
                comparison, heatmap_bytes = self._compute_image_diff(
                    before_path=baseline_path,
                    before_box=self._baseline_box(parent_view, screen_rect),
                    after_path=after_path,
                    screen_rect=screen_rect,
                    before_view_id=str(parent_view.get("view", {}).get("id")),
                    after_view_id=str(after_view.get("view", {}).get("id")),
                )
                if max_artifacts > 0 and heatmap_bytes and comparison.get("changed"):
                    artifact_path = self.evidence.media_dir / f"{request_id}-diff-heatmap.png"
                    artifact_path.write_bytes(heatmap_bytes)
                    artifacts.append(
                        {
                            "artifact_type": "diff_heatmap",
                            "artifact_role": "derived_review_image",
                            "path": str(artifact_path),
                            "media_path_abs": str(artifact_path),
                            "canonical": False,
                            "visualization_only": True,
                            "integrity_truth_source": False,
                            "excluded_from_integrity_truth_source": True,
                        }
                    )
            except ImportError as exc:
                comparison = {**comparison, "status": "not_computed", "not_computed_reason": "dependency_missing:Pillow", "failure_detail": str(exc)}
            except Exception as exc:
                comparison = {**comparison, "status": "not_computed", "not_computed_reason": "frame_decode_failed", "failure_detail": str(exc)}
        return {
            "comparison": comparison,
            "summary": self._look_diff_summary(comparison),
            "artifacts": artifacts,
        }

    def _compute_image_diff(
        self,
        *,
        before_path: Path,
        before_box: tuple[int, int, int, int],
        after_path: Path,
        screen_rect: dict[str, int],
        before_view_id: str,
        after_view_id: str,
    ) -> tuple[dict[str, Any], bytes | None]:
        from io import BytesIO

        from PIL import Image

        with Image.open(before_path) as before_image, Image.open(after_path) as after_image:
            before = before_image.convert("RGBA").crop(before_box)
            after = after_image.convert("RGBA")
            if before.size != after.size:
                raise ValueError(f"diff image size mismatch before={before.size} after={after.size}")
            width, height = before.size
            before_pixels = before.tobytes()
            after_pixels = after.tobytes()
            min_x = width
            min_y = height
            max_x = -1
            max_y = -1
            changed = 0
            heatmap = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            heatmap_pixels = heatmap.load()
            for offset in range(0, len(before_pixels), 4):
                if before_pixels[offset : offset + 4] == after_pixels[offset : offset + 4]:
                    continue
                pixel_index = offset // 4
                y, x = divmod(pixel_index, width)
                changed += 1
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
                heatmap_pixels[x, y] = (255, 0, 0, 220)
            bbox_frame = None
            bbox_screen = None
            if changed:
                bbox_frame = {"x": min_x, "y": min_y, "width": max_x - min_x + 1, "height": max_y - min_y + 1}
                bbox_screen = {
                    "x": int(screen_rect["x"]) + min_x,
                    "y": int(screen_rect["y"]) + min_y,
                    "width": bbox_frame["width"],
                    "height": bbox_frame["height"],
                }
            buffer = BytesIO()
            heatmap.save(buffer, format="PNG")
            total = width * height
            comparison = {
                "status": "computed",
                "comparison_kind": "view_baseline_to_latest_screen_region",
                "before_view_id": before_view_id,
                "after_view_id": after_view_id,
                "frame_width": width,
                "frame_height": height,
                "changed": changed > 0,
                "changed_pixel_count": changed,
                "total_pixel_count": total,
                "changed_pixel_ratio": round(changed / total, 8) if total else 0.0,
                "changed_bbox_frame": bbox_frame,
                "changed_bbox": bbox_screen,
                "changed_bbox_coordinate_system": "virtual_screen_pixels",
                "noise_assessment": self._look_diff_noise_assessment(changed, total, bbox_frame),
                "tool_asserts_semantic_change": False,
                "tool_asserts_business_success": False,
            }
            return comparison, buffer.getvalue()

    def _look_diff_not_computed(
        self,
        *,
        before_view_id: Any,
        after_view_id: Any,
        reason: str,
        screen_rect: dict[str, int],
    ) -> dict[str, Any]:
        return {
            "status": "not_computed",
            "not_computed_reason": reason,
            "comparison_kind": "view_baseline_to_latest_screen_region",
            "before_view_id": before_view_id,
            "after_view_id": after_view_id,
            "screen_region": dict(screen_rect),
            "changed": False,
            "changed_pixel_count": 0,
            "total_pixel_count": 0,
            "changed_pixel_ratio": 0.0,
            "changed_bbox_frame": None,
            "changed_bbox": None,
            "tool_asserts_semantic_change": False,
            "tool_asserts_business_success": False,
        }

    def _look_diff_summary(self, comparison: dict[str, Any]) -> dict[str, Any]:
        computed = comparison.get("status") == "computed"
        return {
            "status": "computed" if computed else "not_computed",
            "frame_pairs": 1 if computed else 0,
            "comparison_count": 1,
            "computed_comparison_count": 1 if computed else 0,
            "changed": bool(comparison.get("changed")) if computed else False,
            "changed_pixel_count": int(comparison.get("changed_pixel_count") or 0),
            "total_pixel_count": int(comparison.get("total_pixel_count") or 0),
            "max_changed_pixel_ratio": float(comparison.get("changed_pixel_ratio") or 0.0),
            "largest_change": comparison.get("changed_bbox"),
            "largest_changed_bbox": comparison.get("changed_bbox"),
            "tool_asserts_semantic_change": False,
            "tool_asserts_business_success": False,
        }

    def _look_diff_noise_assessment(self, changed: int, total: int, bbox: dict[str, int] | None) -> dict[str, Any]:
        ratio = changed / total if total else 0.0
        width = int((bbox or {}).get("width") or 0)
        height = int((bbox or {}).get("height") or 0)
        return {
            "cursor_or_caret_noise_possible": bool(changed and (ratio <= 0.01 or width <= 2 or height <= 2)),
            "basis": "thin_bbox_or_tiny_changed_ratio",
            "tool_does_not_classify_semantic_noise": True,
        }

    def _baseline_box(self, parent_view: dict[str, Any], screen_rect: dict[str, int]) -> tuple[int, int, int, int]:
        parent_rect = parent_view["screen_rect"]
        left = int(screen_rect["x"]) - int(parent_rect["x"])
        top = int(screen_rect["y"]) - int(parent_rect["y"])
        return (left, top, left + int(screen_rect["w"]), top + int(screen_rect["h"]))

    def _path_or_none(self, value: Any) -> Path | None:
        if not value:
            return None
        return value if isinstance(value, Path) else Path(str(value))

    def _remember_view(
        self,
        *,
        frame: dict[str, Any],
        screen_rect: dict[str, int],
        scale_down: int,
        parent_view_id: str | None,
        source_rect_in_parent: dict[str, int] | None,
        request_id: Any,
    ) -> dict[str, Any]:
        view_id = f"v_{uuid.uuid4().hex[:8]}"
        export = self._export_view_image_content(frame, screen_rect=screen_rect, scale_down=scale_down)
        source_time = float(frame.get("captured_at") or frame.get("timestamp") or time.time())
        target_w = int(export["w"])
        target_h = int(export["h"])
        segment_frame = frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else {}
        segment_restore_ref = segment_frame.get("restore_ref") or segment_frame.get("segment_restore_ref")
        transform = {
            "schema": "agentsight_view_transform_v1",
            "coordinate_system": "view_pixels_to_virtual_screen_pixels",
            "view_pixels_to_virtual_screen_pixels": {
                "origin_x": int(screen_rect["x"]),
                "origin_y": int(screen_rect["y"]),
                "scale_x": int(scale_down),
                "scale_y": int(scale_down),
                "formula": "screen_x=origin_x+view_x*scale_x; screen_y=origin_y+view_y*scale_y",
            },
            "blur_changes_coordinates": False,
            "cursor_overlay_changes_coordinates": False,
        }
        view = {
            "id": view_id,
            "w": target_w,
            "h": target_h,
            "scale_down": scale_down,
        }
        source_media_path = self._media_path_abs(frame)
        source_screen_region = self._frame_screen_region(frame, fallback=screen_rect)
        actual_decoded_region = self._decoded_region_from_screen_rect(screen_rect, source_screen_region)
        public_record = {
            "view_id": view_id,
            "created_at": self._format_hms_ms(source_time),
            "source_frame_ref": frame.get("observation_id"),
            "source_frame_id": segment_frame.get("frame_id") or segment_frame.get("segment_frame_id"),
            "segment_restore_ref": segment_restore_ref,
            "source_segment_path": segment_restore_ref.get("segment_path") if isinstance(segment_restore_ref, dict) else None,
            "segment_id": segment_frame.get("segment_id"),
            "source_frame_screen_region": source_screen_region,
            "requested_screen_region": dict(screen_rect),
            "actual_decoded_region": actual_decoded_region,
            "output_image_size": {"w": target_w, "h": target_h},
            "scale_down": scale_down,
            "blur": False,
            "blur_radius": 0,
            "cursor_mode": "none",
            "raw_or_derived": "derived_review_only",
            "coordinate_system": "virtual_screen_pixels",
            "transform": transform,
            "capture_content_degenerate": False,
            "request_id": request_id,
            "operation_log_linkage": {"request_id": request_id, "route": "/look"},
            "derived_review_file_written": False,
            "canonical_evidence_storage": ".mkv/raw_observation",
        }
        record = {
            "object_type": "AgentSightViewIndexEntry",
            "schema": "agentsight_view_index_v1",
            "request_id": request_id,
            "view": view,
            "public_record": public_record,
            "parent_view_id": parent_view_id,
            "screen_rect": dict(screen_rect),
            "source_rect_in_parent": source_rect_in_parent,
            "source_timestamp": source_time,
            "source_time": self._format_hms_ms(source_time),
            "source_observation_ref": frame.get("observation_id"),
            "source_media_ref": frame.get("media_ref"),
            "source_media_path_abs": str(source_media_path) if source_media_path else None,
            "segment_restore_ref": segment_restore_ref,
            "source_frame_id": public_record["source_frame_id"],
            "source_segment_path": public_record["source_segment_path"],
            "transform": transform,
            "coordinate_system": "virtual_screen_pixels",
            "raw_or_derived": "derived_review_only",
            "cursor_mode": "none",
            "capture_content_degenerate": False,
            "scale_down": scale_down,
            "raw_frame_canonical": True,
            "view_is_derived_review": True,
            "derived_review_file_written": False,
            "mcp_content": export.get("mcp_content") or [],
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }
        self._remember_runtime_view(view_id, record)
        return record

    def _frame_screen_region(self, frame: dict[str, Any], *, fallback: dict[str, int]) -> dict[str, int]:
        region = frame.get("screen_region") if isinstance(frame.get("screen_region"), dict) else {}
        try:
            return {
                "x": int(region["x"]),
                "y": int(region["y"]),
                "w": int(region.get("w", region.get("width"))),
                "h": int(region.get("h", region.get("height"))),
            }
        except (KeyError, TypeError, ValueError):
            return dict(fallback)

    def _decoded_region_from_screen_rect(
        self,
        screen_rect: dict[str, int],
        source_screen_region: dict[str, int],
    ) -> dict[str, int]:
        return {
            "x": int(screen_rect["x"]) - int(source_screen_region["x"]),
            "y": int(screen_rect["y"]) - int(source_screen_region["y"]),
            "w": int(screen_rect["w"]),
            "h": int(screen_rect["h"]),
        }

    def _export_view_image_content(
        self,
        frame: dict[str, Any],
        *,
        screen_rect: dict[str, int],
        scale_down: int,
    ) -> dict[str, Any]:
        target_w = max(1, (int(screen_rect["w"]) + scale_down - 1) // scale_down)
        target_h = max(1, (int(screen_rect["h"]) + scale_down - 1) // scale_down)
        media_bytes = frame.get("_media_bytes")
        if isinstance(media_bytes, (bytes, bytearray, memoryview)):
            try:
                from PIL import Image

                with Image.open(BytesIO(bytes(media_bytes))) as image:
                    converted = image.convert("RGBA")
                    resized = converted.resize((target_w, target_h), Image.Resampling.BILINEAR)
                    buffer = BytesIO()
                    resized.save(buffer, format="PNG")
                return {
                    "mcp_content": [
                        {
                            "type": "image",
                            "mimeType": "image/png",
                            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                            "raw_or_derived": "derived_review_only",
                            "canonical": False,
                        }
                    ],
                    "w": target_w,
                    "h": target_h,
                    "status": "generated_mcp_image_content_from_memory_frame",
                }
            except Exception:
                pass
        media_path = self._media_path_abs(frame)
        if media_path and media_path.exists():
            try:
                from PIL import Image

                with Image.open(media_path) as image:
                    converted = image.convert("RGBA")
                    resized = converted.resize((target_w, target_h), Image.Resampling.BILINEAR)
                    buffer = BytesIO()
                    resized.save(buffer, format="PNG")
                return {
                    "mcp_content": [
                        {
                            "type": "image",
                            "mimeType": "image/png",
                            "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
                            "raw_or_derived": "derived_review_only",
                            "canonical": False,
                        }
                    ],
                    "w": target_w,
                    "h": target_h,
                    "status": "generated_mcp_image_content",
                }
            except Exception:
                pass
        return {"mcp_content": [], "w": target_w, "h": target_h, "status": "image_content_unavailable"}

    def _media_path_abs(self, frame: dict[str, Any]) -> Path | None:
        if not isinstance(frame, dict):
            return None
        media_access = frame.get("media_access")
        if isinstance(media_access, dict) and media_access.get("media_path_abs"):
            return Path(str(media_access["media_path_abs"]))
        if frame.get("media_path_abs"):
            return Path(str(frame["media_path_abs"]))
        media_ref = frame.get("media_ref")
        if isinstance(media_ref, str):
            return self.evidence.root / media_ref
        return None

    def _rect_payload_to_screen_rect(self, rect: dict[str, Any]) -> dict[str, int]:
        return {"x": int(rect["x"]), "y": int(rect["y"]), "w": int(rect["w"]), "h": int(rect["h"])}

    def _region_from_screen_rect(self, rect: dict[str, int]) -> dict[str, int]:
        return {"x": int(rect["x"]), "y": int(rect["y"]), "width": int(rect["w"]), "height": int(rect["h"])}

    def _screen_rect_from_observation(self, observation: dict[str, Any]) -> dict[str, int] | None:
        region = observation.get("screen_region") or observation.get("region")
        if not isinstance(region, dict):
            return None
        try:
            return {
                "x": int(region["x"]),
                "y": int(region["y"]),
                "w": int(region.get("w", region.get("width"))),
                "h": int(region.get("h", region.get("height"))),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def _screen_rect_from_parent_view(self, parent_view: dict[str, Any], rect: dict[str, int]) -> dict[str, int]:
        mapping = self._view_pixels_to_screen_mapping(parent_view)
        if mapping:
            return {
                "x": int(mapping["origin_x"]) + int(rect["x"]) * int(mapping["scale_x"]),
                "y": int(mapping["origin_y"]) + int(rect["y"]) * int(mapping["scale_y"]),
                "w": int(rect["w"]) * int(mapping["scale_x"]),
                "h": int(rect["h"]) * int(mapping["scale_y"]),
            }
        raise ChannelFailure(
            "VIEW_TRANSFORM_UNAVAILABLE",
            stage="LookProtocol.view_transform",
            detail="src.view_id does not have a usable view-to-screen transform",
            retryable=True,
        )

    def _validate_view_rect_in_bounds(self, view: dict[str, Any], rect: dict[str, int], *, stage: str) -> None:
        view_meta = view.get("view") if isinstance(view.get("view"), dict) else {}
        width = int(view_meta.get("w") or 0)
        height = int(view_meta.get("h") or 0)
        if width <= 0 or height <= 0:
            raise ChannelFailure(
                "VIEW_DIMENSIONS_UNAVAILABLE",
                stage=stage,
                detail="src.view_id does not have usable view dimensions",
                retryable=True,
            )
        x, y, w, h = int(rect["x"]), int(rect["y"]), int(rect["w"]), int(rect["h"])
        if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > width or y + h > height:
            raise ChannelFailure(
                "VIEW_REGION_OUT_OF_BOUNDS",
                stage=stage,
                detail=f"view region ({x}, {y}, {w}, {h}) is outside view bounds {width}x{height}",
                retryable=False,
            )

    def _view_pixels_to_screen_mapping(self, view: dict[str, Any]) -> dict[str, Any] | None:
        transform = view.get("transform") if isinstance(view.get("transform"), dict) else {}
        mapping = transform.get("view_pixels_to_virtual_screen_pixels")
        if not isinstance(mapping, dict):
            return None
        try:
            origin_x = int(mapping["origin_x"])
            origin_y = int(mapping["origin_y"])
            scale_x = int(mapping["scale_x"])
            scale_y = int(mapping["scale_y"])
        except (KeyError, TypeError, ValueError):
            return None
        if scale_x <= 0 or scale_y <= 0:
            return None
        return {"origin_x": origin_x, "origin_y": origin_y, "scale_x": scale_x, "scale_y": scale_y}

    def _ensure_view_can_be_current_screen_source(self, view: dict[str, Any], *, stage: str) -> None:
        if not self._view_can_be_action_basis(view):
            raise ChannelFailure(
                "VIEW_NOT_CURRENT_SCREEN_BASIS",
                stage=stage,
                detail="src.view_id refers to a historical or review-only view, not a current screen basis",
                retryable=False,
            )

    def _do(self, payload: dict[str, Any]) -> dict[str, Any]:
        started = time.time()
        basis = self._resolve_do_basis(payload.get("basis") if isinstance(payload.get("basis"), dict) else {})
        seq = payload.get("seq") if isinstance(payload.get("seq"), list) else []
        input_step_count = sum(1 for step in seq if not self._do_step_is_wait(step))
        lease_response: dict[str, Any] | None = None
        lease_id: str | None = None
        if input_step_count:
            lease_response = self._create_do_lease(basis, payload=payload, max_input_events=input_step_count)
            lease_id = lease_response.get("lease_id")

        steps: list[dict[str, Any]] = []
        capture_window_ranges: list[dict[str, Any]] = []
        anchors: list[dict[str, Any]] = []
        current_point: dict[str, int] | None = basis.get("screen_point") if isinstance(basis.get("screen_point"), dict) else None
        host_event_count = 0
        status = "done"
        failed_step: dict[str, Any] | None = None
        pre_action_anchor_written = False

        for index, step in enumerate(seq, start=1):
            step_time = time.time()
            if self._do_step_is_wait(step):
                wait_ms = int(step if isinstance(step, int) else step.get("ms", 0))
                time.sleep(wait_ms / 1000)
                steps.append(
                    {
                        "i": index,
                        "req": step,
                        "ok": True,
                        "host_event_count": 0,
                        "time": self._format_hms_ms(step_time),
                    }
                )
                continue

            input_payload, screen_point, step_error = self._do_input_payload(step, basis=basis, current_point=current_point)
            if step_error:
                status = "partial" if steps else "failed"
                failed_step = {"i": index, "failure_code": step_error["failure_code"], "detail": step_error["detail"]}
                steps.append(
                    {
                        "i": index,
                        "req": self._safe_do_req(step),
                        "ok": False,
                        "failure_code": step_error["failure_code"],
                        "detail": step_error["detail"],
                        "host_event_count": 0,
                        "time": self._format_hms_ms(step_time),
                    }
                )
                break
            if screen_point:
                current_point = screen_point
            if not pre_action_anchor_written:
                anchors.append({"kind": "pre_action", "step_i": index, "ts": self._format_hms_ms(max(started, step_time - 0.001))})
                pre_action_anchor_written = True
            input_started = time.time()
            execute_response = self._execute_input({"lease_id": lease_id, **input_payload, "_skip_legacy_evidence": True})
            execute_data = execute_response.get("data", {}) if isinstance(execute_response.get("data"), dict) else {}
            sent_count = int(execute_data.get("host_sent_event_count") or execute_data.get("sent_event_count") or 0)
            host_event_count += sent_count
            self._record_action_capture_window(capture_window_ranges, input_started=input_started, step_i=index)
            anchors.append({"kind": "burst_start", "step_i": index, "ts": self._format_hms_ms(input_started)})
            step_result = {
                "i": index,
                "req": self._safe_do_req(step),
                "ok": bool(execute_response.get("ok")),
                "screen": screen_point,
                "host_event_count": sent_count,
                "time": self._format_hms_ms(input_started),
                "input_type": input_payload.get("input_type"),
            }
            if not execute_response.get("ok"):
                failure = execute_response.get("failure") if isinstance(execute_response.get("failure"), dict) else {}
                step_result["failure_code"] = failure.get("failure_code") or "INPUT_FAILED"
                step_result["detail"] = failure.get("detail")
                failed_step = {"i": index, "failure_code": step_result["failure_code"], "detail": step_result.get("detail")}
                status = "partial" if any(item.get("ok") for item in steps) else "failed"
                steps.append(step_result)
                break
            after_observation = execute_response.get("after_observation")
            if isinstance(after_observation, dict) and after_observation.get("observation_id"):
                step_result["after_observation_ref"] = after_observation.get("observation_id")
            steps.append(step_result)

        capture_windows = self._format_action_capture_windows(capture_window_ranges)
        if anchors:
            last_to = capture_windows[-1]["to"] if capture_windows else self._format_hms_ms(time.time())
            anchors.append({"kind": "burst_end", "ts": last_to})
        post_observe = None
        if status == "done" and payload.get("post_observe") is not None:
            post_observe = self._do_post_observe(payload, basis=basis)
        ended = time.time()
        result = {
            "object_type": "DoResult",
            "schema": "agentsight_do_v1",
            "v": payload.get("v", "V1"),
            "id": payload.get("id"),
            "ok": status == "done",
            "status": status,
            "time": {"start": self._format_hms_ms(started), "end": self._format_hms_ms(ended)},
            "basis": basis["public_basis"],
            "input": {
                "sent": host_event_count > 0,
                "host_event_count": host_event_count,
                "step_count": len(steps),
            },
            "steps": steps,
            "failed_step": failed_step,
            "capture_windows": capture_windows,
            "anchors": anchors,
            "lease": {
                "created": bool(lease_response),
                "lease_id": lease_id,
                "max_input_events": input_step_count,
            },
            "tool_asserts_target_hit": False,
            "tool_asserts_business_success": False,
            "input_visual_relationship_judgment": "external_review_only",
            **self._public_readiness_fields(),
            "boundary": self._public_boundary_facts(),
        }
        if post_observe is not None:
            result["post_observe"] = post_observe
        return result

    def _resolve_do_basis(self, basis: dict[str, Any]) -> dict[str, Any]:
        if basis.get("view_id"):
            view = self.views.get(str(basis["view_id"]))
            if not view:
                raise ChannelFailure("VIEW_NOT_FOUND", stage="DoProtocol.basis", detail="basis.view_id is unknown", retryable=True)
            if not self._view_can_be_action_basis(view):
                raise ChannelFailure(
                    "VIEW_NOT_ACTION_BASIS",
                    stage="DoProtocol.basis",
                    detail="basis.view_id refers to a historical or review-only view, not a current action basis",
                    retryable=False,
                )
            screen_point = self._basis_point_to_screen(view, basis.get("point") if isinstance(basis.get("point"), dict) else None)
            public_basis = {"view_id": view["view"]["id"]}
            if isinstance(basis.get("point"), dict):
                public_basis["point"] = {"x": int(basis["point"]["x"]), "y": int(basis["point"]["y"])}
                public_basis["screen_point"] = screen_point
            return {
                "kind": "view",
                "view": view,
                "screen_rect": view["screen_rect"],
                "scale_down": int(view["view"]["scale_down"]),
                "screen_point": screen_point,
                "before_observation_ref": view.get("source_observation_ref"),
                "public_basis": public_basis,
            }
        raise ChannelFailure(
            "VIEW_BASIS_REQUIRED",
            stage="DoProtocol.basis",
            detail="public do requires basis.view_id",
            retryable=False,
        )

    def _view_can_be_action_basis(self, view: dict[str, Any]) -> bool:
        records = [view]
        for key in ("view", "public_record"):
            nested = view.get(key)
            if isinstance(nested, dict):
                records.append(nested)
        for record in records:
            if record.get("view_is_current_action_basis") is False:
                return False
            if record.get("view_role") == "historical_segment_review":
                return False
        return True

    def _basis_point_to_screen(self, view: dict[str, Any], point: dict[str, Any] | None) -> dict[str, int] | None:
        if not point:
            return None
        view_x = int(point["x"])
        view_y = int(point["y"])
        self._validate_view_point_in_bounds(view, view_x, view_y, stage="DoProtocol.basis")
        transform = view.get("transform") if isinstance(view.get("transform"), dict) else {}
        mapping = transform.get("view_pixels_to_virtual_screen_pixels") if isinstance(transform.get("view_pixels_to_virtual_screen_pixels"), dict) else {}
        if not mapping:
            raise ChannelFailure(
                "VIEW_TRANSFORM_UNAVAILABLE",
                stage="DoProtocol.basis",
                detail="basis.view_id does not have a usable view-to-screen transform",
                retryable=True,
            )
        return {
            "x": int(mapping["origin_x"]) + view_x * int(mapping["scale_x"]),
            "y": int(mapping["origin_y"]) + view_y * int(mapping["scale_y"]),
        }

    def _validate_view_point_in_bounds(self, view: dict[str, Any], x: int, y: int, *, stage: str) -> None:
        view_meta = view.get("view") if isinstance(view.get("view"), dict) else {}
        width = int(view_meta.get("w") or 0)
        height = int(view_meta.get("h") or 0)
        if width <= 0 or height <= 0:
            raise ChannelFailure(
                "VIEW_DIMENSIONS_UNAVAILABLE",
                stage=stage,
                detail="basis.view_id does not have usable view dimensions",
                retryable=True,
            )
        if x < 0 or y < 0 or x >= width or y >= height:
            raise ChannelFailure(
                "VIEW_POINT_OUT_OF_BOUNDS",
                stage=stage,
                detail=f"view point ({x}, {y}) is outside view bounds {width}x{height}",
                retryable=False,
            )

    def _create_do_lease(self, basis: dict[str, Any], *, payload: dict[str, Any], max_input_events: int) -> dict[str, Any]:
        lease_payload: dict[str, Any] = {
            "duration_ms": int(payload.get("duration_ms", 10_000)),
            "budget": {"max_input_events": max(1, max_input_events)},
        }
        if basis.get("before_observation_ref"):
            lease_payload["before_observation_ref"] = basis["before_observation_ref"]
        if self.observation_registry.default_channel_ref:
            lease_payload["after_observation_channel_ref"] = self.observation_registry.default_channel_ref
        return self._create_lease(lease_payload)

    def _do_post_observe(self, payload: dict[str, Any], *, basis: dict[str, Any]) -> dict[str, Any]:
        config = validate_post_observe(payload.get("post_observe"))
        if config["delay_ms"]:
            time.sleep(config["delay_ms"] / 1000)
        frames: list[dict[str, Any]] = []
        region = self._region_from_screen_rect(basis["screen_rect"])
        for index in range(config["frame_count"]):
            frame = self._observe(
                {"mode": "region", "region": region},
                attention_scope="do_after_frame",
                frame_source="do_after_frame",
                event_id=payload.get("id"),
            )
            frame["post_observe_frame_index"] = index
            frame_buffer_ref = self.frame_buffer.update_entry_metadata(
                frame.get("observation_id"),
                source="do_after_frame",
                event_id=payload.get("id"),
                view_id=basis.get("public_basis", {}).get("view_id"),
            )
            if frame_buffer_ref:
                frame["frame_buffer"] = frame_buffer_ref
            self._discard_frame_runtime_payload(frame, reason="post_observe_frame_recorded_to_segment")
            frames.append(frame)
            baseline_ref = basis.get("before_observation_ref")
            baseline_frame = self.observations.get(str(baseline_ref)) if baseline_ref else None
            if should_stop_post_observe_sampling(
                baseline_frame=baseline_frame,
                frames=frames,
                screen_region=basis["screen_rect"],
                coordinate_system=frames[0].get("coordinate_system") if frames else None,
                request=config,
                frame_path=self._media_path_abs,
            ):
                break
            if index < config["frame_count"] - 1 and config["interval_ms"]:
                time.sleep(config["interval_ms"] / 1000)
        baseline_ref = basis.get("before_observation_ref")
        baseline_frame = self.observations.get(str(baseline_ref)) if baseline_ref else None
        return build_post_action_observation_window(
            baseline_frame=baseline_frame,
            frames=frames,
            screen_region=basis["screen_rect"],
            coordinate_system=frames[0].get("coordinate_system") if frames else None,
            request=config,
            frame_path=self._media_path_abs,
        )

    def _do_input_payload(
        self,
        step: dict[str, Any],
        *,
        basis: dict[str, Any],
        current_point: dict[str, int] | None,
    ) -> tuple[dict[str, Any], dict[str, int] | None, dict[str, str] | None]:
        step_type = str(step.get("t"))
        if step_type == "move":
            point = self._view_point_to_screen(basis, int(step["x"]), int(step["y"]))
            return {"input_type": "mouse_move", "x": point["x"], "y": point["y"]}, point, None
        if step_type in {"click", "dblclick", "down", "up", "wheel"} and (step_type not in {"down", "up"} or "b" in step):
            if current_point is None:
                return {}, None, {
                    "failure_code": "DO_REQUIRES_PRIOR_MOVE",
                    "detail": f"{step_type} uses the current mouse position; call move first in the same do.seq",
                }
            button = str(step.get("b") or "left")
            input_type_by_step = {
                "click": "mouse_click",
                "dblclick": "mouse_double_click",
                "down": "mouse_button_down",
                "up": "mouse_button_up",
                "wheel": "mouse_scroll",
            }
            payload: dict[str, Any] = {
                "input_type": input_type_by_step[step_type],
                "x": current_point["x"],
                "y": current_point["y"],
            }
            if step_type in {"click", "dblclick", "down", "up"}:
                payload["button"] = button
            if step_type == "wheel":
                payload["wheel_delta"] = int(step.get("dy", 0))
                payload["horizontal_wheel_delta"] = int(step.get("dx", 0))
            return payload, current_point, None
        if step_type == "text":
            return {"input_type": "key_text_stream", "text": validate_key_text_stream(step.get("text"))}, None, None
        if step_type == "key":
            return {"input_type": "key_press", "key": step.get("key")}, None, None
        if step_type == "chord":
            return {"input_type": "key_chord", "modifiers": step.get("modifiers"), "key": step.get("key")}, None, None
        if step_type == "down" and "key" in step:
            return {"input_type": "key_down", "key": step.get("key")}, None, None
        if step_type == "up" and "key" in step:
            return {"input_type": "key_up", "key": step.get("key")}, None, None
        return {}, None, {"failure_code": "DO_STEP_UNSUPPORTED", "detail": f"unsupported do step: {step_type}"}

    def _view_point_to_screen(self, basis: dict[str, Any], x: int, y: int) -> dict[str, int]:
        view = basis.get("view") if isinstance(basis.get("view"), dict) else {}
        self._validate_view_point_in_bounds(view, x, y, stage="DoProtocol.view_transform")
        transform = view.get("transform") if isinstance(view.get("transform"), dict) else {}
        mapping = transform.get("view_pixels_to_virtual_screen_pixels") if isinstance(transform.get("view_pixels_to_virtual_screen_pixels"), dict) else {}
        if mapping:
            return {
                "x": int(mapping["origin_x"]) + x * int(mapping["scale_x"]),
                "y": int(mapping["origin_y"]) + y * int(mapping["scale_y"]),
            }
        raise ChannelFailure(
            "VIEW_TRANSFORM_UNAVAILABLE",
            stage="DoProtocol.view_transform",
            detail="basis.view_id does not have a usable view-to-screen transform",
            retryable=True,
        )

    def _do_step_is_wait(self, step: Any) -> bool:
        return isinstance(step, int) or (isinstance(step, dict) and step.get("t") == "wait")

    def _safe_do_req(self, step: dict[str, Any]) -> dict[str, Any]:
        safe = dict(step)
        if safe.get("t") == "text" and "text" in safe:
            summary = key_text_summary(validate_key_text_stream(safe["text"]))
            safe.pop("text", None)
            safe["text_summary"] = summary
        return safe

    def _record_action_capture_window(self, windows: list[dict[str, Any]], *, input_started: float, step_i: int) -> None:
        """Record one /do action-capture window, merging adjacent inputs within 60s."""
        window_to = input_started + 60.0
        if windows and input_started <= float(windows[-1]["to_ts"]):
            windows[-1]["to_ts"] = max(float(windows[-1]["to_ts"]), window_to)
            windows[-1]["last_step_i"] = step_i
            return
        windows.append({"kind": "burst", "from_ts": input_started, "to_ts": window_to, "first_step_i": step_i, "last_step_i": step_i})

    def _format_action_capture_windows(self, windows: list[dict[str, Any]]) -> list[dict[str, str]]:
        formatted: list[dict[str, str]] = []
        for window in windows:
            first_step = int(window["first_step_i"])
            last_step = int(window["last_step_i"])
            reason = f"step_{first_step}_input" if first_step == last_step else f"steps_{first_step}-{last_step}_input_merged_within_60s"
            formatted.append(
                {
                    "kind": str(window["kind"]),
                    "from": self._format_hms_ms(float(window["from_ts"])),
                    "to": self._format_hms_ms(float(window["to_ts"])),
                    "reason": reason,
                }
            )
        return formatted

    def _format_hms_ms(self, timestamp: float) -> str:
        local = time.localtime(timestamp)
        millis = int((timestamp - int(timestamp)) * 1000)
        return time.strftime("%H:%M:%S", local) + f".{millis:03d}"

    def _public_readiness_fields(self) -> dict[str, Any]:
        readiness = {
            "schema": "agentsight_public_readiness_v1",
            "source": "embedded_local_gateway",
            "ok": True,
            "code": "READY",
            "message": "Local gateway is ready for this public request.",
            "service_status": "local_gateway_ready",
            "can_attempt_real_control": True,
            "control_blockers": [],
            "real_input_armed": True,
            "arm_required": True,
            "health_endpoint_internal": False,
            "host_input_sent": False,
            "host_sent_event_count": 0,
        }
        return {
            "code": readiness["code"],
            "readiness": readiness,
            "service_status": readiness["service_status"],
            "can_attempt_real_control": readiness["can_attempt_real_control"],
            "control_blockers": [],
        }

    def _public_boundary_facts(self) -> dict[str, bool]:
        return {
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def _query_visual_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        return query_visual_memory(
            query_type=str(payload.get("query_type")),
            observations=self.observations,
            frame_buffer=self.frame_buffer,
            sequence_id=payload.get("sequence_id"),
            change_event_id=payload.get("change_event_id"),
            max_entries=int(payload.get("max_entries", 8)),
            before_count=int(payload.get("before_count", 1)),
            after_count=int(payload.get("after_count", 1)),
            requested_time=payload.get("requested_time"),
        )

    def _observe_sequence(self, payload: dict[str, Any]) -> dict[str, Any]:
        baseline_ref = payload.get("baseline_observation_ref")
        if baseline_ref and baseline_ref not in self.observations:
            raise ChannelFailure(
                "BASELINE_OBSERVATION_UNAVAILABLE",
                stage="ProtocolGateway.observe_sequence",
                detail=f"unknown baseline observation: {baseline_ref}",
                retryable=False,
                requested_mode="sequence",
                requested_region=payload.get("region"),
            )

        channel = self.observation_registry.resolve(payload.get("channel_ref"))
        descriptor = channel.describe()
        if "sequence" not in descriptor.get("modes", []):
            raise ChannelFailure(
                "FRAME_SEQUENCE_UNAVAILABLE",
                stage="ProtocolGateway.observe_sequence",
                detail=f"channel does not support sequence mode: {channel.name}",
                retryable=False,
                channel_ref=channel.name,
                channel_type=channel.channel_type,
                implementation=descriptor.get("implementation"),
                requested_mode="sequence",
                requested_region=payload.get("region"),
            )

        sequence_id = f"seq-{uuid.uuid4().hex[:10]}"
        frame_count = int(payload.get("frame_count", 3))
        interval_ms = int(payload.get("interval_ms", 100))
        change_detection = bool(payload.get("change_detection", True))
        frame_mode = "region" if "region" in payload else "fullscreen"
        frame_payload = {
            "mode": frame_mode,
            "channel_ref": channel.name,
            **({"region": payload["region"]} if "region" in payload else {}),
        }
        started_at = time.time()
        frames: list[dict[str, Any]] = []

        for index in range(frame_count):
            try:
                frame = channel.capture(frame_payload, self.evidence)
            except ChannelFailure as exc:
                exc.channel_ref = exc.channel_ref or channel.name
                exc.channel_type = exc.channel_type or channel.channel_type
                exc.implementation = exc.implementation or descriptor.get("implementation")
                exc.requested_mode = "sequence"
                exc.requested_region = exc.requested_region or payload.get("region")
                raise
            self._enrich_observation_frame(frame, descriptor)
            frame["sequence_id"] = sequence_id
            frame["frame_index"] = index
            frame["previous_frame_ref"] = frames[-1]["observation_id"] if frames else baseline_ref
            frame["sequence_capture_mode"] = frame_mode
            frame["frame_buffer"] = self.frame_buffer.remember_frame(
                frame,
                attention_scope="sequence",
                sequence_id=sequence_id,
                frame_index=index,
                source="review_clip",
                event_id=payload.get("id"),
            )
            frame["frame_evidence_ref"] = self._append_event("observation_sequence_frame", frame)
            frames.append(frame)
            self._remember_runtime_observation(frame["observation_id"], frame)
            if index < frame_count - 1:
                time.sleep(interval_ms / 1000)

        ended_at = time.time()
        sequence_media = build_sequence_gif_artifact(
            sequence_id=sequence_id,
            frames=frames,
            interval_ms=interval_ms,
            evidence=self.evidence,
        )
        sequence = {
            "object_type": "ObservationFrameSequence",
            "sequence_id": sequence_id,
            "mode": "sequence",
            "frame_mode": frame_mode,
            "channel_ref": channel.name,
            "implementation": descriptor.get("implementation"),
            "source_kind": descriptor.get("source_kind"),
            "real_capture": descriptor.get("source_kind") == "software_screen_capture",
            "timestamp": started_at,
            "capture_started_at": started_at,
            "capture_ended_at": ended_at,
            "duration_ms": int((ended_at - started_at) * 1000),
            "requested_frame_count": frame_count,
            "actual_frame_count": len(frames),
            "interval_ms": interval_ms,
            "baseline_observation_ref": baseline_ref,
            "screen_region": frames[0].get("screen_region") or frames[0].get("region"),
            "coordinate_system": frames[0].get("coordinate_system"),
            "frames": frames,
            "frame_refs": [frame["observation_id"] for frame in frames],
            "media_refs": [
                {
                    "frame_ref": frame["observation_id"],
                    "media_ref": frame.get("media_ref"),
                    "media_sha256": frame.get("media_sha256"),
                    "media_size_bytes": frame.get("media_size_bytes"),
                }
                for frame in frames
                if frame.get("media_ref")
            ],
            "change_summary": self._sequence_change_summary(frames, enabled=change_detection),
            "sequence_media_status": sequence_media.get("status"),
            "sequence_media": sequence_media,
        }
        sequence["region_change_index"] = build_region_change_index(
            sequence=sequence,
            frames=frames,
            evidence=self.evidence,
            enabled=change_detection,
        )
        visual_evidence = build_visual_evidence_artifacts(
            sequence=sequence,
            frames=frames,
            evidence=self.evidence,
            evidence_request=payload.get("evidence_request"),
        )
        if visual_evidence is not None:
            sequence["visual_evidence"] = visual_evidence
        sequence["frame_buffer"] = self.frame_buffer.remember_sequence(sequence)
        self._remember_runtime_observation(sequence_id, sequence)
        self.last_observation_id = sequence_id
        return sequence

    def _sequence_change_summary(self, frames: list[dict[str, Any]], *, enabled: bool) -> dict[str, Any]:
        if not enabled:
            return {
                "object_type": "FrameChangeSummary",
                "method": "media_hash",
                "enabled": False,
                "changed": None,
                "not_verifiable_reason": "change_detection_disabled",
            }
        changed_indexes: list[int] = []
        comparisons = []
        for index in range(1, len(frames)):
            before = frames[index - 1]
            after = frames[index]
            hash_changed = before.get("media_sha256") != after.get("media_sha256")
            size_changed = before.get("media_size_bytes") != after.get("media_size_bytes")
            comparison = {
                "before_frame_ref": before.get("observation_id"),
                "after_frame_ref": after.get("observation_id"),
                "after_frame_index": index,
                "media_hash_changed": hash_changed,
                "media_size_changed": size_changed,
                "changed": bool(hash_changed or size_changed),
            }
            comparisons.append(comparison)
            if comparison["changed"]:
                changed_indexes.append(index)
        return {
            "object_type": "FrameChangeSummary",
            "method": "media_hash",
            "enabled": True,
            "frame_count": len(frames),
            "changed": bool(changed_indexes),
            "changed_frame_indexes": changed_indexes,
            "comparisons": comparisons,
        }

    def _enrich_observation_frame(self, frame: dict[str, Any], descriptor: dict[str, Any]) -> None:
        frame.setdefault("implementation", descriptor.get("implementation"))
        frame.setdefault("source_kind", descriptor.get("source_kind"))
        frame.setdefault("real_capture", descriptor.get("source_kind") == "software_screen_capture")
        if "capture_content_degenerate" not in frame:
            media_bytes = frame.get("_media_bytes")
            if not isinstance(media_bytes, (bytes, bytearray, memoryview)):
                media_path = frame.get("media_path_abs") or frame.get("raw_media_path_abs")
                try:
                    if media_path:
                        media_bytes = Path(str(media_path)).read_bytes()
                except Exception:
                    media_bytes = None
            frame.update(analyze_capture_image_bytes(media_bytes))

    def _with_response_hints(self, value: dict[str, Any]) -> dict[str, Any]:
        data = self._copy_dict(value)
        if data.get("object_type") == "ObservationFrame":
            self._attach_media_access(data)
        if data.get("object_type") == "ObservationFrameSequence":
            for frame in data.get("frames", []):
                if isinstance(frame, dict):
                    self._attach_media_access(frame)
            data["frame_media_access"] = [
                frame["media_access"]
                for frame in data.get("frames", [])
                if isinstance(frame, dict) and "media_access" in frame
            ]
            self._attach_visual_evidence_access(data)
            sequence_media = data.get("sequence_media")
            if isinstance(sequence_media, dict):
                sequence_media_access = self._media_access(sequence_media)
                if sequence_media_access:
                    data["sequence_media_access"] = {
                        **sequence_media_access,
                        "read_hint": "read media_path_abs to inspect the derived sequence GIF preview",
                    }
        return data

    def _copy_dict(self, value: dict[str, Any]) -> dict[str, Any]:
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).startswith("_"):
                continue
            if isinstance(item, dict):
                copied[key] = self._copy_dict(item)
            elif isinstance(item, list):
                copied[key] = [self._copy_dict(child) if isinstance(child, dict) else child for child in item]
            else:
                copied[key] = item
        return copied

    def _strip_internal_fields(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: self._strip_internal_fields(child)
                for key, child in value.items()
                if not str(key).startswith("_")
            }
        if isinstance(value, list):
            return [self._strip_internal_fields(child) for child in value]
        return value

    def _attach_media_access(self, frame: dict[str, Any]) -> None:
        media_access = self._media_access(frame)
        if media_access:
            frame["media_access"] = media_access

    def _attach_visual_evidence_access(self, sequence: dict[str, Any]) -> None:
        visual_evidence = sequence.get("visual_evidence")
        if not isinstance(visual_evidence, dict):
            return
        for artifact in visual_evidence.get("artifacts", []):
            if not isinstance(artifact, dict):
                continue
            media_access = self._media_access(artifact)
            if media_access:
                artifact["media_access"] = {
                    **media_access,
                    "read_hint": "read media_path_abs to inspect this visual evidence artifact",
                }
            before_media = artifact.get("before_media")
            if isinstance(before_media, dict):
                before_access = self._media_access(before_media)
                if before_access:
                    artifact["before_media_access"] = before_access
            after_media = artifact.get("after_media")
            if isinstance(after_media, dict):
                after_access = self._media_access(after_media)
                if after_access:
                    artifact["after_media_access"] = after_access

    def _media_access(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        media_ref = frame.get("media_ref")
        if not isinstance(media_ref, str):
            return None
        return {
            "object_type": "MediaAccess",
            "media_ref": media_ref,
            "media_path_abs": str((self.evidence.root / media_ref).resolve()),
            "media_mime": frame.get("media_mime"),
            "media_sha256": frame.get("media_sha256"),
            "media_size_bytes": frame.get("media_size_bytes"),
            "coordinate_system": frame.get("coordinate_system"),
            "screen_region": frame.get("screen_region") or frame.get("region"),
            "display_only": True,
            "canonical": False,
            "read_hint": "read media_path_abs to inspect visible pixels",
        }

    def _ai_usage_guide(self) -> dict[str, Any]:
        return {
            "object_type": "AIUsageGuide",
            "public_tools": list(PUBLIC_COMMAND_ORDER),
            "legacy_internal_tools": [
                name for name in COMMAND_ORDER if name not in PUBLIC_COMMAND_ORDER
            ],
            "workflow": [
                "screen",
                "look",
                "do",
                "look",
            ],
            "legacy_internal_workflow": [
                "observe",
                "query_visual_memory",
                "inspect_observation_media",
                "derive_candidates",
                "create_lease",
                "execute_input",
                "inspect_after_observation",
                "get_evidence_package",
                "read_replay",
                "verify_integrity",
            ],
            "preferred_real_observation_channel": "windows_software_observation",
            "recommended_ai_flow": [
                "ordinary public flow is screen -> look -> do -> look",
                "legacy/internal compatibility may still use observe when a diagnostic explicitly requires it",
                "ordinary indexed-frame review should use look time.near instead of legacy visual-memory queries",
                "inspect returned pixels externally; the tool does not OCR or judge UI meaning",
                "derive_candidates remains geometry-only legacy/internal assistance",
                "legacy/internal lease/execute_input remains available for diagnostics and audit flows",
                "evidence/replay/integrity remain internal or audit export, not the ordinary public path",
            ],
            "limited_batch_shortcut": {
                "legacy_internal_tool": "run_limited_batch",
                "role": "bounded orchestration over the legacy observe/lease/input/evidence compatibility path",
                "does_not_add_capabilities": True,
                "stops_on_first_failed_step": True,
                "suggested_mvp_steps": [
                    "observe_once",
                    "locate_visible_target",
                    "click_candidate_with_evidence or type_keys_with_evidence",
                    "wait_visible_change",
                    "build_evidence_package",
                ],
            },
            "observation_media": {
                "media_ref_scope": "session_relative",
                "media_path_abs_field": "media_access.media_path_abs",
                "hash_field": "media_sha256",
                "coordinate_fields": ["coordinate_system", "screen_region", "width", "height"],
            },
            "sequence_media": {
                "field": "sequence_media",
                "media_access_field": "sequence_media_access.media_path_abs",
                "role": "animation_preview",
                "visualization_only": True,
                "does_not_replace_frames": True,
            },
            "visual_memory_rule": {
                "current_stage": "p0n_storage_attention_summary",
                "frame_buffer_indexes_raw_evidence_frames": True,
                "frame_buffer_owns_raw_media": False,
                "frame_buffer_prune_deletes_raw_media": False,
                "bounded_by_ttl_entries_and_indexed_bytes": True,
                "sequence_region_change_index_available": True,
                "region_change_index_returns_images": False,
                "region_change_index_is_derived_metadata": True,
                "sequence_evidence_request_available": True,
                "visual_evidence_artifact_types": ["raw_frame", "raw_crop", "before_after", "diff_heatmap"],
                "raw_frame_and_raw_crop_can_be_canonical": True,
                "diff_heatmap_is_derived_review_only": True,
                "query_api_public": False,
                "query_api_legacy_internal": True,
                "ordinary_public_facade": "look",
                "query_tool": "query_visual_memory",
                "query_types": [
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
                ],
                "query_returns_images": False,
                "query_is_metadata_only": True,
                "query_performs_capture": False,
                "query_includes_ai_reading_summary": True,
                "query_summary_kind": "deterministic_metadata_only",
                "query_summary_freeform_language_generated": False,
                "query_summary_semantic_interpretation_in_tool": False,
                "query_summary_action_completion_judged": False,
                "event_window_query_available": True,
                "event_window_returns_frame_refs_only": True,
                "retention_status_query_available": True,
                "prune_unreferenced_buffer_query_is_dry_run_only": True,
                "prune_query_deletes_raw_media": False,
                "video_encoding_status": "not_implemented",
                "raw_frames_remain_canonical_evidence": True,
                "derived_review_artifacts_are_not_truth_source": True,
            },
            "visual_memory_workflow": {
                "stage": "p0n_storage_attention_summary",
                "ordinary_ai_steps": [
                    "screen to understand the virtual desktop",
                    "look fullscreen or focused region for a low-cost preview",
                    "look again to narrow attention before acting",
                    "do with a view basis when visible-pixel input is needed",
                    "look after the action to inspect visible pixel feedback",
                    "look time.near to inspect indexed frames around an approximate wall-clock time",
                    "use future look change/diff/clip expansions for ordinary visual-memory queries when available",
                ],
                "legacy_internal_steps": [
                    "observe fullscreen or bounded region for a low-cost preview",
                    "observe a bounded sequence over the caller-chosen region",
                    "legacy indexed metadata query path is retained only for compatibility diagnostics",
                    "public look time.near is the ordinary path for approximate wall-clock frame review",
                    "read ai_reading_summary as deterministic query metadata, not UI interpretation",
                    "legacy event-window and retention metadata queries remain compatibility-only diagnostics",
                    "read region_change_index structured metadata before requesting more images",
                    "request visual_evidence artifacts only for selected change events when needed",
                    "inspect response-layer media_access paths externally",
                    "decide UI semantics outside the tool",
                    "use existing visible-pixel mouse or keyboard control path when acting",
                    "read evidence package, replay, and integrity before reporting",
                ],
                "default_cost_posture": "metadata_first_then_artifacts_on_demand",
                "tool_does_not_choose_region_or_semantics": True,
                "public_query_tool": None,
                "legacy_internal_query_tool": "query_visual_memory",
                "ordinary_public_facade": "look",
                "raw_evidence_first": True,
                "derived_review_artifacts_not_truth_source": True,
            },
            "candidate_rule": {
                "candidate_method": "observation_geometry",
                "geometry_only": True,
                "candidate_click_is_host_side_manual_smoke_only": True,
                "no_ocr_or_window_semantics": True,
            },
            "coordinate_rule": {
                "mouse_click_requires_visible_pixel_coordinates": True,
                "real_input_mouse_click_must_be_inside_before_screen_region": True,
                "mouse_actions_require_visible_pixel_coordinates": True,
                "real_input_mouse_points_must_be_inside_before_screen_region": True,
                "drag_requires_start_and_end_inside_before_screen_region": True,
                "windows_virtual_desktop_coordinates_may_be_negative": True,
                "region_x_y_may_be_negative_on_left_or_upper_monitors": True,
                "sendinput_mouse_uses_virtual_desktop_absolute_coordinates": True,
                "coordinate_integrity_schema": "agentsight_p1c_coordinate_integrity_v1",
            },
            "pointer_control_rule": {
                "input_types": [
                    "mouse_move",
                    "mouse_click",
                    "mouse_double_click",
                    "mouse_button_down",
                    "mouse_button_up",
                    "mouse_drag",
                    "mouse_scroll",
                ],
                "buttons": ["left", "right", "middle"],
                "scroll_fields": ["wheel_delta", "horizontal_wheel_delta"],
                "dry_run_first": True,
                "semantic_action": False,
                "real_sendinput_requires_arming_consent_and_real_observation": True,
                "tool_does_not_assert_target_hit_drag_completed_or_scroll_effect": True,
            },
            "input_modes": {
                "dry_run_input": "validates flow without host input",
                "windows_software_input_disabled": "declared but disabled until host enablement",
                "manual_input_smoke": "host-side smoke only; not a public MCP tool",
            },
            "key_text_stream_rule": {
                "first_version_boundary": "keyboard_event_expansion_only",
                "max_text_chars": 256,
                "empty_text_allowed": False,
                "control_characters_allowed": False,
                "non_bmp_characters_allowed": False,
                "dry_run_first": True,
                "text_evidence_policy": "hash_only",
                "no_clipboard_or_command_sources": True,
                "no_file_clipboard_or_command_sources": True,
                "paste_must_be_user_visible_keyboard_shortcut": "use Ctrl+V as key events; do not set or read clipboard",
            },
            "keyboard_event_rule": {
                "p1b_boundary": "explicit_keyboard_events_only",
                "input_types": ["key_press", "key_chord", "key_down", "key_up"],
                "key_press_supported_sets": [
                    "letters A-Z",
                    "digits 0-9",
                    "arrows",
                    "Home/End/PageUp/PageDown",
                    "Escape/Backspace/Delete/Space/Enter/Tab",
                    "F1-F12",
                    "Ctrl/Alt/Shift/Win modifiers",
                ],
                "key_chord_modifiers": ["CTRL", "ALT", "SHIFT", "WIN"],
                "key_down_key_up_supported": True,
                "intentional_hold_requires_matching_key_up": True,
                "dry_run_first": True,
                "not_a_hotkey_or_paste_api": True,
                "clipboard_api_used": False,
                "file_source_used": False,
                "command_source_used": False,
                "real_sendinput_requires_arming_consent_and_real_observation": True,
            },
            "real_input_claim_rule": "claim host input only when host_input_executed is true and the backend reports host-sent events",
        }

    def _derive_candidates(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        observation_ref = payload.get("observation_ref")
        if observation_ref and observation_ref not in self.observations:
            return None
        if not observation_ref and not self.observations:
            return None
        if not observation_ref:
            observation_ref = next(iter(self.observations), None)
        observation = self.observations.get(observation_ref)
        if not observation:
            return None
        candidate_list = {
            "object_type": "VisualCandidateList",
            "source_observation_ref": observation_ref,
            "candidate_method": "observation_geometry",
            "semantics_used": False,
            "ocr_used": False,
            "window_semantics_used": False,
            "candidates": self._geometry_candidates(observation_ref, observation),
        }
        for candidate in candidate_list["candidates"]:
            self.candidates[candidate["candidate_id"]] = candidate
        self.last_candidate_list = candidate_list
        return candidate_list

    def _geometry_candidates(self, observation_ref: str, observation: dict[str, Any]) -> list[dict[str, Any]]:
        region = self._candidate_screen_region(observation)
        if not region:
            return []
        coordinate_system = observation.get("coordinate_system")
        if observation.get("object_type") == "ObservationFrameSequence":
            coordinate_system = observation.get("coordinate_system")
        x0 = int(region["x"])
        y0 = int(region["y"])
        width = int(region["width"])
        height = int(region["height"])
        points = [
            ("center", x0 + width // 2, y0 + height // 2),
            ("upper_left_quadrant", x0 + max(0, width // 4), y0 + max(0, height // 4)),
            ("upper_right_quadrant", x0 + max(0, (width * 3) // 4), y0 + max(0, height // 4)),
            ("lower_left_quadrant", x0 + max(0, width // 4), y0 + max(0, (height * 3) // 4)),
            ("lower_right_quadrant", x0 + max(0, (width * 3) // 4), y0 + max(0, (height * 3) // 4)),
        ]
        candidates: list[dict[str, Any]] = []
        for role, x, y in points:
            point = self._clamp_point(x, y, region)
            candidates.append(
                {
                    "object_type": "VisualCandidate",
                    "candidate_id": f"cand-{uuid.uuid4().hex[:10]}",
                    "candidate_kind": f"geometry_{role}",
                    "source_observation_ref": observation_ref,
                    "source_frame_refs": observation.get("frame_refs"),
                    "source_channel_ref": observation.get("channel_ref"),
                    "source_real_capture": observation.get("real_capture"),
                    "source_media_ref": observation.get("media_ref"),
                    "source_media_sha256": observation.get("media_sha256"),
                    "coordinate_system": coordinate_system,
                    "screen_region": region,
                    "click_point": point,
                    "region": self._candidate_region(point, region),
                    "confidence": 0.1,
                    "confidence_basis": "geometry_only",
                    "uncertainty_reason": "geometry_only_no_visual_or_window_semantics",
                    "semantics_used": False,
                    "ocr_used": False,
                    "window_semantics_used": False,
                }
            )
        return candidates

    def _candidate_screen_region(self, observation: dict[str, Any]) -> dict[str, int] | None:
        region = observation.get("screen_region") or observation.get("region")
        if not isinstance(region, dict):
            return None
        required = {"x", "y", "width", "height"}
        if not required.issubset(region):
            return None
        parsed = {key: int(region[key]) for key in required}
        if parsed["width"] <= 0 or parsed["height"] <= 0:
            return None
        return parsed

    def _clamp_point(self, x: int, y: int, region: dict[str, int]) -> dict[str, int]:
        return {
            "x": min(max(x, region["x"]), region["x"] + region["width"] - 1),
            "y": min(max(y, region["y"]), region["y"] + region["height"] - 1),
        }

    def _candidate_region(self, point: dict[str, int], screen_region: dict[str, int]) -> dict[str, int]:
        box_width = max(1, min(32, screen_region["width"] // 4 or 1))
        box_height = max(1, min(32, screen_region["height"] // 4 or 1))
        x = min(max(point["x"] - box_width // 2, screen_region["x"]), screen_region["x"] + screen_region["width"] - box_width)
        y = min(max(point["y"] - box_height // 2, screen_region["y"]), screen_region["y"] + screen_region["height"] - box_height)
        return {"x": x, "y": y, "width": box_width, "height": box_height}

    def _create_lease(self, payload: dict[str, Any]) -> dict[str, Any]:
        lease_id = f"lease-{uuid.uuid4().hex[:10]}"
        duration_ms = int(payload.get("duration_ms", 5000))
        input_channel_ref = payload.get("input_channel_ref") or self.input_registry.default_channel_ref
        input_channel = self.input_registry.resolve(input_channel_ref)
        input_channel_descriptor = input_channel.describe()
        gate = self._real_input_gate(payload, input_channel_descriptor)
        before_observation_ref = self._lease_before_observation_ref(payload, gate)
        after_observation_channel_ref = self._lease_after_observation_channel_ref(payload, gate)
        budget = payload.get("budget", {"max_input_events": 5})
        max_input_events = budget.get("max_input_events", 5) if isinstance(budget, dict) else 5
        if not isinstance(max_input_events, int) or max_input_events <= 0:
            max_input_events = 5
        lease = {
            "object_type": "InputLease",
            "lease_id": lease_id,
            "created_at": time.time(),
            "expires_at": time.time() + duration_ms / 1000,
            "scope": payload.get("scope", {"type": "screen"}),
            "budget": budget,
            "max_input_events": max_input_events,
            "used_input_events": 0,
            "input_channel_ref": input_channel_ref,
            "input_channel_status": input_channel_descriptor.get("status"),
            "input_mode": input_channel_descriptor.get("execution_mode"),
            "real_input": bool(input_channel_descriptor.get("real_input", False)),
            "real_input_gate": gate,
            "before_observation_ref": before_observation_ref,
            "after_observation_channel_ref": after_observation_channel_ref,
        }
        self.leases[lease_id] = lease
        return lease

    def _lease_before_observation_ref(self, payload: dict[str, Any], gate: dict[str, Any]) -> str | None:
        if gate.get("before_observation_ref"):
            return gate["before_observation_ref"]
        requested_ref = payload.get("before_observation_ref")
        if not requested_ref:
            return None
        if requested_ref not in self.observations:
            raise ChannelFailure(
                "FLOW_ORDER_VIOLATION",
                stage="InputLeaseManager.before_observation",
                detail="before_observation_ref must refer to a known observation",
                retryable=True,
                channel_type="input",
            )
        return requested_ref

    def _lease_after_observation_channel_ref(self, payload: dict[str, Any], gate: dict[str, Any]) -> str | None:
        if gate.get("after_observation_channel_ref"):
            return gate["after_observation_channel_ref"]
        requested_ref = payload.get("after_observation_channel_ref")
        if not requested_ref:
            return None
        channel = self.observation_registry.resolve(requested_ref)
        descriptor = channel.describe()
        if descriptor.get("status") != "available":
            raise ChannelFailure(
                "AFTER_OBSERVATION_CHANNEL_UNAVAILABLE",
                stage="InputLeaseManager.after_observation",
                detail="after_observation_channel_ref must refer to an available observation channel",
                retryable=False,
                channel_ref=requested_ref,
                channel_type="observation",
                implementation=descriptor.get("implementation"),
            )
        return requested_ref

    def _real_input_gate(self, payload: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
        gate_required = bool(descriptor.get("real_input", False)) and descriptor.get("status") == "available"
        gate = {
            "object_type": "RealInputGate",
            "required": gate_required,
            "state": "not_required",
            "arming_ref_source": "host_or_test_injected",
            "input_executed": False,
        }
        if not gate_required:
            return gate

        channel_ref = descriptor.get("name")
        implementation = descriptor.get("implementation")
        active_arming_ref = descriptor.get("active_arming_ref")
        arming_state = descriptor.get("arming_state", "not_armed")
        if arming_state != "armed" or not active_arming_ref:
            raise ChannelFailure(
                "REAL_INPUT_NOT_ARMED",
                stage="InputLeaseManager.real_input_gate",
                detail="available real input channel has no host-provided active arming",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )
        if not payload.get("arming_ref"):
            raise ChannelFailure(
                "REAL_INPUT_ARMING_REQUIRED",
                stage="InputLeaseManager.real_input_gate",
                detail="available real input channels require arming_ref",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )
        if payload.get("arming_ref") != active_arming_ref:
            raise ChannelFailure(
                "REAL_INPUT_ARMING_INVALID",
                stage="InputLeaseManager.real_input_gate",
                detail="arming_ref did not match active host arming",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )
        operator_consent_ref = payload.get("operator_consent_ref")
        active_consent_ref = descriptor.get("operator_consent_ref")
        if not operator_consent_ref or (active_consent_ref and operator_consent_ref != active_consent_ref):
            raise ChannelFailure(
                "REAL_INPUT_CONSENT_REQUIRED",
                stage="InputLeaseManager.real_input_gate",
                detail="available real input channels require matching operator_consent_ref",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )

        before_observation_ref = payload.get("before_observation_ref")
        before_observation = self._require_real_observation(
            before_observation_ref,
            channel_ref=channel_ref,
            implementation=implementation,
        )
        after_observation_channel_ref = payload.get("after_observation_channel_ref") or before_observation.get("channel_ref")
        after_channel = self.observation_registry.resolve(after_observation_channel_ref)
        after_descriptor = after_channel.describe()
        if after_descriptor.get("source_kind") != "software_screen_capture" or after_descriptor.get("status") != "available":
            raise ChannelFailure(
                "REAL_INPUT_AFTER_OBSERVATION_REQUIRED",
                stage="InputLeaseManager.real_input_gate",
                detail="after_observation_channel_ref must refer to an available real screen observation channel",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )

        return {
            **gate,
            "state": "armed",
            "arming_ref": active_arming_ref,
            "operator_consent_ref": operator_consent_ref,
            "before_observation_ref": before_observation_ref,
            "before_observation_channel_ref": before_observation.get("channel_ref"),
            "after_observation_channel_ref": after_observation_channel_ref,
            "arming_ref_verified": True,
        }

    def _require_real_observation(
        self,
        observation_ref: str | None,
        *,
        channel_ref: str | None,
        implementation: str | None,
    ) -> dict[str, Any]:
        if not observation_ref or observation_ref not in self.observations:
            raise ChannelFailure(
                "REAL_INPUT_REQUIRES_REAL_OBSERVATION",
                stage="InputLeaseManager.real_input_gate",
                detail="available real input channels require a known before_observation_ref",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )
        observation = self.observations[observation_ref]
        if observation.get("source_kind") != "software_screen_capture" or observation.get("real_capture") is not True:
            raise ChannelFailure(
                "REAL_INPUT_REQUIRES_REAL_OBSERVATION",
                stage="InputLeaseManager.real_input_gate",
                detail="before_observation_ref must be a real software screen observation",
                retryable=False,
                channel_ref=channel_ref,
                channel_type="input",
                implementation=implementation,
            )
        return observation

    def _execute_input(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.stopped:
            return self._failure("EXECUTION_STOPPED", stage="ExecutionControl", retryable=False)
        lease_id = payload.get("lease_id")
        lease = self.leases.get(lease_id)
        if not lease:
            return self._failure("LEASE_REQUIRED", stage="InputLeaseManager")
        if lease["expires_at"] < time.time():
            return self._failure("LEASE_EXPIRED", stage="InputLeaseManager")
        if int(lease.get("used_input_events", 0)) >= int(lease.get("max_input_events", 5)):
            return self._failure("INPUT_BUDGET_EXHAUSTED", stage="InputLeaseManager", retryable=False)

        requested_input_channel_ref = payload.get("input_channel_ref") or lease.get("input_channel_ref")
        if requested_input_channel_ref != lease.get("input_channel_ref"):
            return self._failure(
                "INPUT_CHANNEL_MISMATCH",
                stage="InputLeaseManager",
                retryable=False,
                input_executed=False,
                channel_ref=requested_input_channel_ref,
                channel_type="input",
            )

        input_channel = self.input_registry.resolve(requested_input_channel_ref)
        channel_descriptor = input_channel.describe()
        real_input_gate = lease.get("real_input_gate", {"required": False})
        if bool(channel_descriptor.get("real_input", False)) and channel_descriptor.get("status") == "available":
            if real_input_gate.get("state") != "armed":
                return self._failure(
                    "REAL_INPUT_ARMING_REQUIRED",
                    stage="InputLeaseManager.real_input_gate",
                    retryable=False,
                    input_executed=False,
                    channel_ref=requested_input_channel_ref,
                    channel_type="input",
                    implementation=channel_descriptor.get("implementation"),
                )
        skip_after_observation = bool(payload.get("skip_after_observation"))
        skip_legacy_evidence = bool(payload.get("_skip_legacy_evidence"))
        input_payload = {
            k: v
            for k, v in payload.items()
            if k not in {"simulate_evidence_prewrite_failure", "skip_after_observation", "after_observation_skip_reason", "_skip_legacy_evidence"}
        }
        evidence_payload = self._input_payload_for_evidence(input_payload, payload.get("input_type"))
        text_summary = self._input_text_summary(input_payload, payload.get("input_type"))
        keyboard_summary = self._input_keyboard_summary(input_payload, payload.get("input_type"))
        mouse_summary = self._input_mouse_summary(input_payload, payload.get("input_type"))
        after_observation_channel_ref = lease.get("after_observation_channel_ref")
        before_observation_ref = real_input_gate.get("before_observation_ref") or lease.get("before_observation_ref") or self.last_observation_id
        input_event = {
            "object_type": "InputEvent",
            "input_event_id": f"input-{uuid.uuid4().hex[:10]}",
            "lease_id": lease_id,
            "input_channel_ref": requested_input_channel_ref,
            "channel_ref": requested_input_channel_ref,
            "implementation": channel_descriptor.get("implementation"),
            "execution_mode": channel_descriptor.get("execution_mode"),
            "before_observation_ref": before_observation_ref,
            "after_observation_channel_ref": after_observation_channel_ref,
            "real_input_gate": real_input_gate,
            "input_type": payload["input_type"],
            "payload": evidence_payload,
            "requested_coordinates": self._requested_coordinates(payload),
            "requested_coordinate_points": self._requested_coordinate_points(payload, payload.get("input_type")),
            "button_or_key": payload.get("button"),
            **text_summary,
            **keyboard_summary,
            **mouse_summary,
            "input_executed": False,
        }
        backend_input_event = {**input_event, "payload": input_payload}
        before_summary = self._before_observation_summary(real_input_gate)
        if before_summary:
            input_event["before_observation_summary"] = before_summary
            backend_input_event["before_observation_summary"] = before_summary
            coordinate_integrity = self._coordinate_integrity_summary(
                backend_input_event,
                before_summary=before_summary,
                channel_descriptor=channel_descriptor,
            )
            if coordinate_integrity:
                input_event["coordinate_integrity"] = coordinate_integrity
                backend_input_event["coordinate_integrity"] = coordinate_integrity
            coordinate_failure = self._validate_real_input_coordinates(
                backend_input_event,
                before_summary=before_summary,
                channel_descriptor=channel_descriptor,
                requested_input_channel_ref=requested_input_channel_ref,
            )
            if coordinate_failure:
                return coordinate_failure

        if payload.get("simulate_evidence_prewrite_failure") and not skip_legacy_evidence:
            return self._failure(
                "EVIDENCE_RECORD_FAILED",
                stage="EvidenceReplayService.prepare_input_entry",
                evidence_incomplete=True,
            )

        if skip_legacy_evidence:
            input_event["prewrite_evidence_ref"] = None
            input_event["legacy_evidence_prewrite_skipped"] = True
            backend_input_event["prewrite_evidence_ref"] = None
            backend_input_event["legacy_evidence_prewrite_skipped"] = True
        else:
            try:
                prewrite_ref = self.evidence.prepare_input_entry(input_event)
            except OSError as exc:
                return self._failure(
                    "EVIDENCE_RECORD_FAILED",
                    stage="EvidenceReplayService.prepare_input_entry",
                    evidence_incomplete=True,
                    detail=str(exc),
                )

            input_event["prewrite_evidence_ref"] = prewrite_ref
            backend_input_event["prewrite_evidence_ref"] = prewrite_ref
        try:
            input_result = input_channel.execute(backend_input_event)
        except ChannelFailure as exc:
            input_event["input_failure"] = {
                "failure_code": exc.failure_code,
                "stage": exc.stage,
                "detail": exc.detail,
                "channel_ref": exc.channel_ref,
            }
            failure_event_ref = None if skip_legacy_evidence else self._append_event("input_failed", input_event)
            return self._failure(
                exc.failure_code,
                stage=exc.stage,
                retryable=exc.retryable,
                detail=exc.detail,
                input_executed=False,
                channel_ref=exc.channel_ref or requested_input_channel_ref,
                channel_type=exc.channel_type or "input",
                implementation=exc.implementation or channel_descriptor.get("implementation"),
            )
        input_event.update(input_result)
        lease["used_input_events"] = int(lease.get("used_input_events", 0)) + 1
        if skip_after_observation:
            input_event["after_observation_skipped"] = True
            input_event["after_observation_skip_reason"] = str(
                payload.get("after_observation_skip_reason") or "caller_requested_no_legacy_after_observation"
            )
        event_ref = None if skip_legacy_evidence else self._append_event("input", input_event)
        if skip_after_observation:
            return {
                "ok": True,
                "data": input_event,
                "evidence_ref": event_ref,
                "after_observation": None,
                "after_observation_skipped": True,
                "next_allowed_commands": self._next_allowed_commands("input"),
            }
        after_payload = {"mode": "after_action", "after_action_ref": input_event["input_event_id"]}
        if after_observation_channel_ref:
            after_payload["channel_ref"] = after_observation_channel_ref
        try:
            after_frame = self._observe(after_payload)
        except ChannelFailure as exc:
            input_event["after_observation_failure"] = {
                "failure_code": exc.failure_code,
                "stage": exc.stage,
                "detail": exc.detail,
                "channel_ref": exc.channel_ref,
            }
            if not skip_legacy_evidence:
                self._append_event("input_after_observation_failed", input_event)
            return self._failure(
                "AFTER_OBSERVATION_FAILED",
                stage=exc.stage,
                retryable=exc.retryable,
                detail=exc.detail,
                input_executed=bool(input_event.get("input_executed")),
                channel_ref=exc.channel_ref,
                channel_type=exc.channel_type,
                implementation=exc.implementation,
                requested_mode=exc.requested_mode,
                requested_region=exc.requested_region,
            )
        input_event["after_observation_ref"] = after_frame.get("observation_id")
        if not skip_legacy_evidence:
            self._append_event("observation_after_input", after_frame)
        return {
            "ok": True,
            "data": input_event,
            "evidence_ref": event_ref,
            "after_observation": self._with_response_hints(after_frame),
            "next_allowed_commands": self._next_allowed_commands("input"),
        }

    def _stop(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.stopped = True
        return {
            "object_type": "ExecutionControl",
            "state": "stopped",
            "reason": payload.get("reason", "requested"),
            "stopped_input": True,
            "released_inputs": True,
        }

    def _before_observation_summary(self, real_input_gate: dict[str, Any]) -> dict[str, Any] | None:
        before_ref = real_input_gate.get("before_observation_ref")
        if not before_ref:
            return None
        before = self.observations.get(before_ref)
        if not before:
            return None
        return {
            "observation_id": before.get("observation_id"),
            "channel_ref": before.get("channel_ref"),
            "source_kind": before.get("source_kind"),
            "real_capture": before.get("real_capture"),
            "width": before.get("width"),
            "height": before.get("height"),
            "screen_region": before.get("screen_region") or before.get("region"),
            "coordinate_system": before.get("coordinate_system"),
            "monitor_index": before.get("monitor_index"),
        }

    def _validate_real_input_coordinates(
        self,
        input_event: dict[str, Any],
        *,
        before_summary: dict[str, Any],
        channel_descriptor: dict[str, Any],
        requested_input_channel_ref: str,
    ) -> dict[str, Any] | None:
        input_type = input_event.get("input_type")
        if not is_mouse_action(input_type):
            return None
        if input_event.get("real_input_gate", {}).get("state") != "armed":
            return None
        payload = input_event.get("payload", {})
        region = before_summary.get("screen_region")
        try:
            points = mouse_action_points(input_type, payload)
        except ValueError as exc:
            return self._failure(
                "INPUT_COORDINATE_OUT_OF_SCOPE",
                stage="InputLeaseManager.coordinate_scope",
                retryable=False,
                detail=str(exc),
                input_executed=False,
                channel_ref=requested_input_channel_ref,
                channel_type="input",
                implementation=channel_descriptor.get("implementation"),
            )
        if not points or not isinstance(region, dict):
            return self._failure(
                "INPUT_COORDINATE_OUT_OF_SCOPE",
                stage="InputLeaseManager.coordinate_scope",
                retryable=False,
                detail=f"{input_type} coordinates require a known before observation screen_region",
                input_executed=False,
                channel_ref=requested_input_channel_ref,
                channel_type="input",
                implementation=channel_descriptor.get("implementation"),
            )
        left = int(region.get("x", 0))
        top = int(region.get("y", 0))
        width = int(region.get("width", 0))
        height = int(region.get("height", 0))
        out_of_scope_points = [
            point
            for point in points
            if width <= 0
            or height <= 0
            or int(point["x"]) < left
            or int(point["y"]) < top
            or int(point["x"]) >= left + width
            or int(point["y"]) >= top + height
        ]
        if out_of_scope_points:
            return self._failure(
                "INPUT_COORDINATE_OUT_OF_SCOPE",
                stage="InputLeaseManager.coordinate_scope",
                retryable=False,
                detail=(
                    f"{input_type} coordinates are outside the before observation screen_region; "
                    f"out_of_scope_points={out_of_scope_points}"
                ),
                input_executed=False,
                channel_ref=requested_input_channel_ref,
                channel_type="input",
                implementation=channel_descriptor.get("implementation"),
                requested_region=region,
            )
        return None

    def _coordinate_integrity_summary(
        self,
        input_event: dict[str, Any],
        *,
        before_summary: dict[str, Any],
        channel_descriptor: dict[str, Any],
    ) -> dict[str, Any] | None:
        input_type = input_event.get("input_type")
        if not is_mouse_action(input_type):
            return None
        payload = input_event.get("payload", {})
        region = before_summary.get("screen_region")
        try:
            points = mouse_action_points(input_type, payload)
        except ValueError:
            points = []
        inside = bool(points) and isinstance(region, dict) and all(self._point_inside_region(point, region) for point in points)
        before_space = before_summary.get("coordinate_system")
        supported_spaces = channel_descriptor.get("supported_mouse_coordinate_systems") or []
        return {
            "schema": "agentsight_p1c_coordinate_integrity_v1",
            "before_observation_ref": before_summary.get("observation_id"),
            "before_coordinate_system": before_space,
            "before_screen_region": region,
            "input_channel_ref": input_event.get("input_channel_ref"),
            "input_coordinate_system": channel_descriptor.get("mouse_coordinate_system"),
            "input_supported_coordinate_systems": supported_spaces,
            "coordinate_system_reported_compatible": before_space in supported_spaces if before_space else False,
            "coordinate_points": points,
            "scope_checked_against_before_screen_region": True,
            "all_points_inside_before_screen_region": inside,
            "negative_virtual_coordinates_supported": bool(channel_descriptor.get("supports_negative_virtual_coordinates")),
            "coordinate_transform_performed_by_tool": False,
            "tool_generated_coordinates": False,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def _point_inside_region(self, point: dict[str, Any], region: Any) -> bool:
        if not isinstance(region, dict):
            return False
        try:
            left = int(region["x"])
            top = int(region["y"])
            width = int(region["width"])
            height = int(region["height"])
            x = int(point["x"])
            y = int(point["y"])
        except (KeyError, TypeError, ValueError):
            return False
        return width > 0 and height > 0 and left <= x < left + width and top <= y < top + height

    def _requested_coordinates(self, payload: dict[str, Any]) -> dict[str, int] | None:
        if "x" not in payload and "y" not in payload:
            return None
        x = payload.get("x")
        y = payload.get("y")
        if isinstance(x, int) and isinstance(y, int):
            return {"x": x, "y": y}
        return None

    def _requested_coordinate_points(self, payload: dict[str, Any], input_type: str | None) -> list[dict[str, Any]] | None:
        if not is_mouse_action(input_type):
            return None
        try:
            return mouse_action_points(input_type, payload)
        except ValueError:
            return None

    def _input_text_summary(self, payload: dict[str, Any], input_type: str | None) -> dict[str, Any]:
        if input_type != "key_text_stream":
            return {"text_length": None}
        text = validate_key_text_stream(payload.get("text"))
        return key_text_summary(text)

    def _input_keyboard_summary(self, payload: dict[str, Any], input_type: str | None) -> dict[str, Any]:
        if not is_keyboard_action(input_type):
            return {}
        return keyboard_action_summary(input_type, payload)

    def _input_mouse_summary(self, payload: dict[str, Any], input_type: str | None) -> dict[str, Any]:
        if not is_mouse_action(input_type):
            return {}
        return mouse_action_summary(input_type, payload)

    def _input_payload_for_evidence(self, payload: dict[str, Any], input_type: str | None) -> dict[str, Any]:
        if is_mouse_action(input_type):
            return mouse_action_summary(input_type, payload)
        if is_keyboard_action(input_type):
            return keyboard_action_summary(input_type, payload)
        if input_type != "key_text_stream":
            return payload
        text = validate_key_text_stream(payload.get("text"))
        summary = key_text_summary(text)
        return {
            "input_type": "key_text_stream",
            **summary,
        }

    def _run_limited_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        started_at = time.time()
        steps = payload.get("steps", [])
        max_steps = payload.get("max_steps", len(steps) if steps else 0)
        max_duration_ms = payload.get("max_duration_ms", 5000)
        max_input_events = payload.get("max_input_events", 1)
        response_detail = payload.get("response_detail", "summary")
        limit_failure = self._batch_limit_failure(
            steps=steps,
            max_steps=max_steps,
            max_duration_ms=max_duration_ms,
            max_input_events=max_input_events,
        )
        if limit_failure:
            return limit_failure

        input_events_used = 0
        step_results: list[dict[str, Any]] = []
        batch_status = "completed"
        stopped_reason = None
        deadline = started_at + int(max_duration_ms) / 1000

        for index, step in enumerate(steps):
            if time.time() > deadline:
                batch_status = "stopped_on_limit"
                stopped_reason = "max_duration_ms_exceeded"
                break

            step_type = step["step_type"]
            step_payload = step.get("payload") or {}
            result = self._run_limited_batch_step(step_type, step_payload, max_input_events=max_input_events)
            result["index"] = index
            result["step_type"] = step_type
            step_results.append(result)

            if result.get("input_event_used"):
                input_events_used += 1
            if input_events_used >= int(max_input_events):
                remaining_has_input = any(
                    item.get("step_type")
                    in {"click_candidate_with_evidence", "type_keys_with_evidence"}
                    for item in steps[index + 1 :]
                )
                if remaining_has_input:
                    batch_status = "stopped_on_limit"
                    stopped_reason = "max_input_events_exhausted"
                    break
            if result.get("status") != "ok":
                batch_status = "stopped_on_failure"
                stopped_reason = result.get("failure_code") or "step_failed"
                break
            if self.stopped:
                batch_status = "stopped"
                stopped_reason = "safe_stop"
                break

        ended_at = time.time()
        ai_summary = self._limited_batch_ai_summary(step_results, batch_status=batch_status)
        steps_for_response = (
            step_results if response_detail == "full" else self._batch_step_summaries(step_results)
        )
        return {
            "object_type": "LimitedBatchResult",
            "schema": "limited_batch_result_v1",
            "batch_status": batch_status,
            "completed": batch_status == "completed",
            "stopped_reason": stopped_reason,
            "step_count_requested": len(steps),
            "step_count_run": len(step_results),
            "max_steps": int(max_steps),
            "max_duration_ms": int(max_duration_ms),
            "duration_ms": int((ended_at - started_at) * 1000),
            "max_input_events": int(max_input_events),
            "input_events_used": input_events_used,
            "response_detail": response_detail,
            "full_step_responses_omitted": response_detail != "full",
            "steps": steps_for_response,
            "ai_summary": ai_summary,
            "batch_policy": {
                "bounded": True,
                "legacy_internal_compatibility_orchestration_only": True,
                "ordinary_public_screen_look_do_flow": False,
                "stops_on_first_failed_step": True,
                "no_window_semantics": True,
                "no_ocr": True,
                "no_command_line": True,
                "no_business_result_evaluation": True,
                "no_clipboard_api": True,
                "application_effect_unverified": True,
                "does_not_upgrade_visual_feedback_to_task_success": True,
            },
            "ordinary_ai_progress": (
                "A historical compatibility batch can still run a bounded legacy/internal sequence over visible pixels, "
                "geometry-only candidates, lease/input, visual feedback, and audit receipts. "
                "Ordinary AI should use the public screen -> look -> do -> look flow instead."
            ),
        }

    def _batch_limit_failure(
        self,
        *,
        steps: list[Any],
        max_steps: Any,
        max_duration_ms: Any,
        max_input_events: Any,
    ) -> dict[str, Any] | None:
        if not steps:
            return self._batch_rejected("BATCH_STEPS_REQUIRED", "steps must contain at least one batch step")
        if not isinstance(max_steps, int) or max_steps < 0:
            return self._batch_rejected("BATCH_LIMIT_INVALID", "max_steps must be a non-negative integer")
        if len(steps) > max_steps:
            return self._batch_rejected("BATCH_STEP_LIMIT_EXCEEDED", "steps exceeded max_steps")
        if not isinstance(max_duration_ms, int) or max_duration_ms <= 0 or max_duration_ms > 10_000:
            return self._batch_rejected("BATCH_LIMIT_INVALID", "max_duration_ms must be 1..10000")
        if not isinstance(max_input_events, int) or max_input_events < 0 or max_input_events > 5:
            return self._batch_rejected("BATCH_LIMIT_INVALID", "max_input_events must be 0..5")
        return None

    def _batch_rejected(self, failure_code: str, detail: str) -> dict[str, Any]:
        return {
            "object_type": "LimitedBatchResult",
            "schema": "limited_batch_result_v1",
            "batch_status": "rejected",
            "completed": False,
            "failure_code": failure_code,
            "detail": detail,
            "response_detail": "summary",
            "full_step_responses_omitted": True,
            "steps": [],
            "batch_policy": {
                "bounded": True,
                "legacy_internal_compatibility_orchestration_only": True,
                "ordinary_public_screen_look_do_flow": False,
                "stops_on_first_failed_step": True,
            },
        }

    def _batch_step_summaries(self, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summaries: list[dict[str, Any]] = []
        for step in steps:
            summary: dict[str, Any] = {
                "object_type": "LimitedBatchStepSummary",
                "index": step.get("index"),
                "step_type": step.get("step_type"),
                "status": step.get("status"),
                "input_event_used": step.get("input_event_used"),
                "public_tool_calls": [
                    self._batch_call_summary(call) for call in step.get("public_tool_calls", [])
                ],
            }
            for field in ("failure_code", "detail"):
                if field in step:
                    summary[field] = step[field]
            summaries.append(summary)
        return summaries

    def _batch_call_summary(self, call: dict[str, Any]) -> dict[str, Any]:
        response = call.get("response") if isinstance(call.get("response"), dict) else {}
        failure = response.get("failure") if isinstance(response.get("failure"), dict) else {}
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        summary: dict[str, Any] = {
            "tool_name": call.get("tool_name"),
            "ok": bool(response.get("ok")),
            "object_type": data.get("object_type"),
            "evidence_ref": response.get("evidence_ref"),
        }
        if "media_ref" in data:
            summary["media_ref"] = data.get("media_ref")
        if "sequence_media_status" in data:
            summary["sequence_media_status"] = data.get("sequence_media_status")
        if "channel_ref" in data:
            summary["channel_ref"] = data.get("channel_ref")
        if "real_capture" in data:
            summary["real_capture"] = data.get("real_capture")
        if "input_channel_ref" in data:
            summary["input_channel_ref"] = data.get("input_channel_ref")
        if "input_type" in data:
            summary["input_type"] = data.get("input_type")
        if "host_input_executed" in data:
            summary["host_input_executed"] = data.get("host_input_executed")
            summary["host_input_sent"] = bool(data.get("host_input_executed"))
        if "host_sent_event_count" in data or "sent_event_count" in data:
            summary["host_sent_event_count"] = data.get("host_sent_event_count", data.get("sent_event_count"))
        real_input_gate = data.get("real_input_gate") if isinstance(data.get("real_input_gate"), dict) else {}
        if real_input_gate:
            summary["real_input_gate_state"] = real_input_gate.get("state")
        if failure:
            summary["failure_code"] = failure.get("failure_code")
            summary["detail"] = failure.get("detail")
            summary["input_executed"] = failure.get("input_executed")
        return summary

    def _limited_batch_ai_summary(self, steps: list[dict[str, Any]], *, batch_status: str) -> dict[str, Any]:
        observation = self._batch_summary_observation(steps)
        input_summary = self._batch_summary_input(steps)
        feedback = self._batch_summary_feedback(steps)
        evidence = self._batch_summary_evidence(steps)
        return {
            "object_type": "LimitedBatchAISummary",
            "schema": "limited_batch_ai_summary_v1",
            "batch_status": batch_status,
            "read_this_first": True,
            "observation": observation,
            "input": input_summary,
            "feedback": feedback,
            "evidence": evidence,
            "boundaries": {
                "legacy_internal_compatibility_flow": True,
                "ordinary_public_screen_look_do_flow": False,
                "no_window_semantics": True,
                "no_ocr": True,
                "no_clipboard_api": True,
                "no_file_input": True,
                "no_command_line": True,
                "business_result_evaluated": False,
                "application_effect_unverified": True,
                "visual_feedback_is_not_business_success": True,
                "integrity_is_evidence_consistency_only": True,
            },
            "safe_report_lines": self._batch_safe_report_lines(
                batch_status=batch_status,
                observation=observation,
                input_summary=input_summary,
                feedback=feedback,
                evidence=evidence,
            ),
        }

    def _batch_summary_observation(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        observe = self._first_success_data(steps, tool_name="observe", object_type="ObservationFrame")
        if not observe:
            return {"available": False}
        media_access = observe.get("media_access") if isinstance(observe.get("media_access"), dict) else {}
        return {
            "available": True,
            "channel_ref": observe.get("channel_ref"),
            "source_kind": observe.get("source_kind"),
            "real_capture": observe.get("real_capture"),
            "observation_ref": observe.get("observation_id"),
            "media_ref": observe.get("media_ref"),
            "media_path_abs": media_access.get("media_path_abs"),
            "coordinate_system": observe.get("coordinate_system"),
            "screen_region": observe.get("screen_region") or observe.get("region"),
        }

    def _batch_summary_input(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        execute = self._first_success_data(steps, tool_name="execute_input", object_type="InputEvent")
        if not execute:
            return {"attempted": False}
        return {
            "attempted": True,
            "input_event_ref": execute.get("input_event_id"),
            "input_channel_ref": execute.get("channel_ref") or execute.get("input_channel_ref"),
            "input_type": execute.get("input_type"),
            "execution_mode": execute.get("execution_mode"),
            "input_executed": execute.get("input_executed"),
            "host_input_executed": execute.get("host_input_executed"),
            "host_input_sent": bool(execute.get("host_input_executed")),
            "host_sent_event_count": execute.get("host_sent_event_count", execute.get("sent_event_count")),
            "sent_event_count": execute.get("sent_event_count"),
            "real_input_gate": execute.get("real_input_gate"),
            "released_inputs": execute.get("released_inputs"),
            "release_result": execute.get("release_result"),
            "after_observation_ref": execute.get("after_observation_ref"),
            "application_effect_unverified": True,
        }

    def _batch_summary_feedback(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        sequence = self._first_success_data(steps, tool_name="observe", object_type="ObservationFrameSequence")
        if not sequence:
            return {"sequence_captured": False}
        sequence_media = sequence.get("sequence_media") if isinstance(sequence.get("sequence_media"), dict) else {}
        media_access = sequence.get("sequence_media_access") if isinstance(sequence.get("sequence_media_access"), dict) else {}
        return {
            "sequence_captured": True,
            "sequence_id": sequence.get("sequence_id"),
            "channel_ref": sequence.get("channel_ref"),
            "real_capture": sequence.get("real_capture"),
            "actual_frame_count": sequence.get("actual_frame_count"),
            "sequence_media_status": sequence.get("sequence_media_status"),
            "sequence_media_ref": sequence_media.get("media_ref"),
            "sequence_media_path_abs": media_access.get("media_path_abs"),
            "visualization_only": True,
            "application_effect_unverified": True,
            "change_summary": sequence.get("change_summary"),
        }

    def _batch_summary_evidence(self, steps: list[dict[str, Any]]) -> dict[str, Any]:
        package = self._first_success_data(steps, tool_name="get_evidence_package", object_type="EvidencePackage")
        replay = self._first_success_data(steps, tool_name="read_replay", object_type="ReplayIndex")
        integrity = self._first_success_data(steps, tool_name="verify_integrity", object_type="IntegrityManifest")
        return {
            "package_ok": bool(package),
            "replay_read_only": bool(replay and replay.get("read_only")),
            "integrity_ok": bool(integrity and integrity.get("ok")),
            "integrity_means_evidence_consistency_only": True,
        }

    def _batch_safe_report_lines(
        self,
        *,
        batch_status: str,
        observation: dict[str, Any],
        input_summary: dict[str, Any],
        feedback: dict[str, Any],
        evidence: dict[str, Any],
    ) -> list[str]:
        return [
            "I used the legacy/internal run_limited_batch compatibility flow, not the ordinary public screen/look/do flow.",
            (
                "Observation: "
                f"channel={observation.get('channel_ref')}, real_capture={observation.get('real_capture')}, "
                f"media_ref={observation.get('media_ref')}, coordinate_system={observation.get('coordinate_system')}, "
                f"screen_region={observation.get('screen_region')}."
            ),
            (
                "Input: "
                f"input_channel={input_summary.get('input_channel_ref')}, input_type={input_summary.get('input_type')}, "
                f"host_input_executed={input_summary.get('host_input_executed')}, "
                f"host_input_sent={input_summary.get('host_input_sent')}, "
                f"host_sent_event_count={input_summary.get('host_sent_event_count')}."
            ),
            (
                "Feedback: "
                f"sequence_captured={feedback.get('sequence_captured')}, "
                f"sequence_media_status={feedback.get('sequence_media_status')}, "
                "visual feedback is not application or business success."
            ),
            (
                "Evidence: "
                f"package_ok={evidence.get('package_ok')}, replay_read_only={evidence.get('replay_read_only')}, "
                f"integrity_ok={evidence.get('integrity_ok')}."
            ),
            (
                "Boundary: business_result_evaluated=False, application_effect_unverified=True; "
                "I cannot claim paste success, UI semantic state, target-app completion, or business completion."
            ),
        ]

    def _first_success_data(
        self,
        steps: list[dict[str, Any]],
        *,
        tool_name: str,
        object_type: str,
    ) -> dict[str, Any] | None:
        for step in steps:
            for call in step.get("public_tool_calls", []):
                if call.get("tool_name") != tool_name:
                    continue
                response = call.get("response", {})
                data = response.get("data") if response.get("ok") else None
                if isinstance(data, dict) and data.get("object_type") == object_type:
                    return data
        return None

    def _run_limited_batch_step(
        self,
        step_type: str,
        step_payload: dict[str, Any],
        *,
        max_input_events: int,
    ) -> dict[str, Any]:
        if step_type == "observe_once":
            response = self.handle({"command": "observe", "payload": dict(step_payload or {"mode": "fullscreen"})})
            return self._batch_single_response("observe", response)
        if step_type == "locate_visible_target":
            response = self.handle({"command": "derive_candidates", "payload": dict(step_payload)})
            return self._batch_single_response("derive_candidates", response)
        if step_type == "click_candidate_with_evidence":
            return self._batch_click_candidate(step_payload, max_input_events=max_input_events)
        if step_type == "type_keys_with_evidence":
            return self._batch_type_keys(step_payload, max_input_events=max_input_events)
        if step_type == "wait_visible_change":
            return self._batch_wait_visible_change(step_payload)
        if step_type == "build_evidence_package":
            return self._batch_build_evidence()
        if step_type == "safe_stop":
            response = self.handle({"command": "stop", "payload": dict(step_payload)})
            return self._batch_single_response("stop", response)
        return {
            "object_type": "LimitedBatchStepResult",
            "status": "failed",
            "failure_code": "BATCH_STEP_NOT_IMPLEMENTED",
            "detail": f"step_type {step_type!r} is not implemented in limited batch MVP",
            "public_tool_calls": [],
        }

    def _batch_click_candidate(self, step_payload: dict[str, Any], *, max_input_events: int) -> dict[str, Any]:
        if max_input_events <= 0:
            return self._batch_step_failure("BATCH_INPUT_LIMIT_EXHAUSTED", "max_input_events is zero")
        candidate = self._batch_selected_candidate(step_payload)
        if not candidate:
            return self._batch_step_failure("BATCH_CANDIDATE_UNAVAILABLE", "no candidate or coordinates available")
        point = candidate["click_point"]
        calls = []
        lease_id = step_payload.get("lease_id")
        if not isinstance(lease_id, str):
            lease_payload = self._batch_lease_payload(
                step_payload,
                default_before_observation_ref=candidate.get("source_observation_ref"),
            )
            lease_response = self.handle(
                {
                    "command": "create_lease",
                    "payload": lease_payload,
                }
            )
            calls.append({"tool_name": "create_lease", "response": lease_response})
            if not lease_response.get("ok"):
                return self._batch_multi_response(calls, input_event_used=False)
            lease_id = lease_response["data"]["lease_id"]
        execute_payload = {
            "lease_id": lease_id,
            "input_type": "mouse_click",
            "x": point["x"],
            "y": point["y"],
            "button": step_payload.get("button", "left"),
        }
        execute_response = self.handle({"command": "execute_input", "payload": execute_payload})
        calls.append({"tool_name": "execute_input", "response": execute_response})
        return self._batch_multi_response(calls, input_event_used=bool(execute_response.get("ok")))

    def _batch_type_keys(self, step_payload: dict[str, Any], *, max_input_events: int) -> dict[str, Any]:
        if max_input_events <= 0:
            return self._batch_step_failure("BATCH_INPUT_LIMIT_EXHAUSTED", "max_input_events is zero")
        calls = []
        lease_id = step_payload.get("lease_id")
        if not isinstance(lease_id, str):
            lease_payload = self._batch_lease_payload(
                step_payload,
                default_before_observation_ref=self.last_observation_id,
            )
            lease_response = self.handle(
                {
                    "command": "create_lease",
                    "payload": lease_payload,
                }
            )
            calls.append({"tool_name": "create_lease", "response": lease_response})
            if not lease_response.get("ok"):
                return self._batch_multi_response(calls, input_event_used=False)
            lease_id = lease_response["data"]["lease_id"]
        input_type = step_payload.get("input_type", "key_text_stream")
        execute_payload: dict[str, Any] = {
            "lease_id": lease_id,
            "input_type": input_type,
        }
        if input_type == "key_text_stream":
            execute_payload["text"] = step_payload.get("text")
        elif input_type in {"key_press", "key_chord"}:
            execute_payload["key"] = step_payload.get("key")
            if input_type == "key_chord":
                execute_payload["modifiers"] = step_payload.get("modifiers", [])
        else:
            return self._batch_step_failure("BATCH_KEYBOARD_INPUT_UNSUPPORTED", f"unsupported keyboard batch input_type {input_type!r}")
        execute_response = self.handle(
            {
                "command": "execute_input",
                "payload": execute_payload,
            }
        )
        calls.append({"tool_name": "execute_input", "response": execute_response})
        return self._batch_multi_response(calls, input_event_used=bool(execute_response.get("ok")))

    def _batch_lease_payload(
        self,
        step_payload: dict[str, Any],
        *,
        default_before_observation_ref: str | None,
    ) -> dict[str, Any]:
        lease_payload = {
            "duration_ms": 10_000,
            "budget": {"max_input_events": 1},
        }
        before_ref = step_payload.get("before_observation_ref") or default_before_observation_ref
        if isinstance(before_ref, str):
            lease_payload["before_observation_ref"] = before_ref
        for field in (
            "input_channel_ref",
            "after_observation_channel_ref",
            "arming_ref",
            "operator_consent_ref",
        ):
            if isinstance(step_payload.get(field), str):
                lease_payload[field] = step_payload[field]
        return lease_payload

    def _batch_wait_visible_change(self, step_payload: dict[str, Any]) -> dict[str, Any]:
        duration_ms = step_payload.get("duration_ms", 100)
        interval_ms = max(50, min(250, int(duration_ms) if isinstance(duration_ms, int) else 100))
        observe_payload: dict[str, Any] = {"mode": "sequence", "frame_count": 2, "interval_ms": interval_ms}
        if isinstance(step_payload.get("channel_ref"), str):
            observe_payload["channel_ref"] = step_payload["channel_ref"]
        baseline_ref = step_payload.get("observation_ref") or self.last_observation_id
        if baseline_ref:
            observe_payload["baseline_observation_ref"] = baseline_ref
        response = self.handle({"command": "observe", "payload": observe_payload})
        return self._batch_single_response("observe", response)

    def _batch_build_evidence(self) -> dict[str, Any]:
        calls = []
        for tool_name in ("get_evidence_package", "read_replay", "verify_integrity"):
            response = self.handle({"command": tool_name, "payload": {}})
            calls.append({"tool_name": tool_name, "response": response})
            if not response.get("ok"):
                break
        return self._batch_multi_response(calls, input_event_used=False)

    def _batch_selected_candidate(self, step_payload: dict[str, Any]) -> dict[str, Any] | None:
        if isinstance(step_payload.get("x"), int) and isinstance(step_payload.get("y"), int):
            return {
                "candidate_id": step_payload.get("candidate_id", "explicit_coordinates"),
                "source_observation_ref": self.last_observation_id,
                "click_point": {"x": step_payload["x"], "y": step_payload["y"]},
            }
        candidate_id = step_payload.get("candidate_id")
        if isinstance(candidate_id, str) and candidate_id in self.candidates:
            return self.candidates[candidate_id]
        candidates = self.last_candidate_list.get("candidates", []) if self.last_candidate_list else []
        if not candidates:
            return None
        return next((item for item in candidates if item.get("candidate_kind") == "geometry_center"), candidates[0])

    def _batch_single_response(self, tool_name: str, response: dict[str, Any]) -> dict[str, Any]:
        return self._batch_multi_response([{"tool_name": tool_name, "response": response}], input_event_used=False)

    def _batch_multi_response(self, calls: list[dict[str, Any]], *, input_event_used: bool) -> dict[str, Any]:
        failed = next((call for call in calls if not call["response"].get("ok")), None)
        if failed:
            failure = failed["response"].get("failure", {})
            return {
                "object_type": "LimitedBatchStepResult",
                "status": "failed",
                "failure_code": failure.get("failure_code", "BATCH_CHILD_TOOL_FAILED"),
                "detail": failure.get("detail"),
                "public_tool_calls": calls,
                "input_event_used": input_event_used,
            }
        return {
            "object_type": "LimitedBatchStepResult",
            "status": "ok",
            "public_tool_calls": calls,
            "input_event_used": input_event_used,
        }

    def _batch_step_failure(self, failure_code: str, detail: str) -> dict[str, Any]:
        return {
            "object_type": "LimitedBatchStepResult",
            "status": "failed",
            "failure_code": failure_code,
            "detail": detail,
            "public_tool_calls": [],
            "input_event_used": False,
        }
