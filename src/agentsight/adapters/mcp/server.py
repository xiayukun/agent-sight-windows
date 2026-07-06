from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from agentsight.adapters.session import SessionAdapter
from agentsight.channels.base import InputChannel, ObservationChannel
from agentsight.protocol.schemas import MAX_POST_OBSERVE_FRAME_COUNT, PAYLOAD_FIELDS, PUBLIC_COMMAND_ORDER
from agentsight.visual_memory.frame_buffer import FrameBufferConfig


MCP_TOOL_NAMES = PUBLIC_COMMAND_ORDER


def public_tool_rejection(name: object) -> dict[str, Any]:
    return {
        "ok": False,
        "error": "mcp_tool_not_public",
        "tool": name,
        "allowed_tools": list(MCP_TOOL_NAMES),
        "detail": "public MCP calls are limited to screen/look/do; legacy/internal commands are not callable through this public adapter mode",
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": {
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        },
    }


def _field_schema(field: str) -> dict[str, Any]:
    if field == "max_entries":
        return {"type": "integer", "minimum": 1, "maximum": 32, "default": 8}
    if field == "v":
        return {"type": "string", "enum": ["V1"], "default": "V1"}
    if field == "id":
        return {"type": "string"}
    if field == "op":
        return {"type": "string", "enum": ["screen", "look", "do"]}
    if field == "q":
        return {"type": "string", "enum": ["frame", "diff", "changes", "clip"]}
    if field == "src":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "enum": ["screen", "view"]},
                "t": {"type": "string", "default": "latest"},
                "view_id": {"type": "string"},
            },
        }
    if field == "r":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["x", "y", "w", "h"],
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "w": {"type": "integer", "minimum": 1},
                "h": {"type": "integer", "minimum": 1},
            },
        }
    if field == "scale_down":
        return {"type": "integer", "minimum": 1, "maximum": 32}
    if field == "image_response":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["inline_lowres", "none"], "default": "inline_lowres"},
                "max_edge": {"type": "integer", "minimum": 1, "maximum": 1024, "default": 512},
            },
            "description": "Optional HTTP JSON fallback review image request. Returned image is derived_review_only and not canonical evidence.",
        }
    if field == "basis":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["view_id"],
            "properties": {
                "view_id": {"type": "string"},
                "point": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["x", "y"],
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                    },
                    "description": "Optional point in the basis view image pixels. When present, do may click/use this point without a prior move step.",
                },
            },
        }
    if field == "post_observe":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "delay_ms": {"type": "integer", "minimum": 0, "maximum": 5000, "default": 0},
                "frame_count": {"type": "integer", "minimum": 1, "maximum": MAX_POST_OBSERVE_FRAME_COUNT, "default": 3},
                "interval_ms": {"type": "integer", "minimum": 0, "maximum": 2000, "default": 150},
                "stable_threshold": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.001},
                "stable_frame_count": {"type": "integer", "minimum": 1, "maximum": 5, "default": 2},
                "stop_when_stable": {"type": "boolean", "default": False},
            },
        }
    if field == "seq":
        return {
            "type": "array",
            "minItems": 1,
            "items": {
                "oneOf": [
                    {"type": "integer", "minimum": 0},
                    {"type": "object", "additionalProperties": True},
                ]
            },
        }
    if field == "time":
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "from": {"type": "string"},
                "to": {"type": "string"},
                "near": {"type": "string"},
                "at": {"type": "string"},
                "requested_time": {"type": "string"},
                "tolerance_ms": {"type": "integer", "minimum": 0},
            },
        }
    if field == "mode":
        return {"type": "string", "enum": ["fullscreen", "region", "after_action", "sequence", "endpoints", "timeline", "timeline_with_artifacts"]}
    if field == "max_artifacts":
        return {"type": "integer", "minimum": 0, "maximum": 5, "default": 0}
    if field == "max_pairs":
        return {"type": "integer", "minimum": 1, "maximum": 10000, "default": 128}
    if field == "max_frames":
        return {"type": "integer", "minimum": 1, "maximum": 240, "default": 32}
    if field == "min_changed_pixel_ratio":
        return {"type": "number", "minimum": 0, "maximum": 1, "default": 0}
    if field in {"before_count", "after_count"}:
        return {"type": "integer", "minimum": 0, "maximum": 5, "default": 1}
    if field in {
        "duration_ms",
        "frame_count",
        "interval_ms",
        "x",
        "y",
        "to_x",
        "to_y",
        "wheel_delta",
        "vertical_wheel_delta",
        "horizontal_wheel_delta",
        "max_steps",
        "max_duration_ms",
        "max_input_events",
    }:
        return {"type": "integer"}
    if field == "query_type":
        return {
            "type": "string",
            "enum": [
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
        }
    if field in {"change_detection", "reexecute", "simulate_evidence_prewrite_failure"}:
        return {"type": "boolean"}
    if field == "response_detail":
        return {"type": "string", "enum": ["summary", "full"], "default": "summary"}
    if field == "requested_time":
        return {"type": "string", "description": "Wall-clock, ISO timestamp, epoch seconds, or HH:MM[:SS] time for nearby frame lookup."}
    if field in {"steps", "modifiers", "artifact_types"}:
        return {"type": "array"}
    if field == "evidence_request":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["artifact_types"],
            "properties": {
                "source": {"type": "string", "enum": ["change_events"], "default": "change_events"},
                "artifact_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["raw_frame", "raw_crop", "before_after", "diff_heatmap"]},
                    "minItems": 1,
                },
                "max_artifacts": {"type": "integer", "minimum": 1, "maximum": 5, "default": 1},
            },
        }
    if field in {"region", "scope", "budget"}:
        if field == "region":
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["x", "y", "width", "height"],
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "width": {"type": "integer", "minimum": 1},
                    "height": {"type": "integer", "minimum": 1},
                },
            }
        return {"type": "object", "additionalProperties": False}
    return {"type": "string"}


def tool_schema(command: str) -> dict[str, Any]:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {field: _field_schema(field) for field in sorted(PAYLOAD_FIELDS[command])},
    }
    if command == "look":
        schema["required"] = ["q", "src", "r", "scale_down"]
    elif command == "do":
        schema["required"] = ["basis", "seq"]
    return schema


class MCPStdioAdapter:
    """Minimal MCP-like stdio adapter over the shared session gateway.

    The project intentionally keeps this stdlib-only until the dependency
    policy is settled. The shape is MCP-compatible enough for local acceptance:
    list fixed tools, expose closed input schemas, and call one tool at a time.
    """

    def __init__(
        self,
        runs_dir: str | Path = "runs",
        *,
        observation_channel: ObservationChannel | None = None,
        observation_channels: list[ObservationChannel] | None = None,
        default_observation_channel_ref: str | None = None,
        input_channel: InputChannel | None = None,
        input_channels: list[InputChannel] | None = None,
        default_input_channel_ref: str | None = None,
        frame_buffer_config: FrameBufferConfig | None = None,
        enforce_public_tool_allowlist: bool = False,
    ) -> None:
        self.enforce_public_tool_allowlist = bool(enforce_public_tool_allowlist)
        self.session = SessionAdapter(
            runs_dir,
            adapter_ref="mcp_stdio",
            observation_channel=observation_channel,
            observation_channels=observation_channels,
            default_observation_channel_ref=default_observation_channel_ref,
            input_channel=input_channel,
            input_channels=input_channels,
            default_input_channel_ref=default_input_channel_ref,
            frame_buffer_config=frame_buffer_config,
        )

    @property
    def session_id(self) -> str:
        return self.session.session_id

    def list_tools(self) -> dict[str, Any]:
        return {
            "object_type": "MCPToolList",
            "tools": [
                {
                    "name": name,
                    "description": f"AgentSight protocol command: {name}",
                    "inputSchema": tool_schema(name),
                }
                for name in MCP_TOOL_NAMES
            ],
        }

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.enforce_public_tool_allowlist and name not in MCP_TOOL_NAMES:
            return public_tool_rejection(name)
        return self.session.call(name, arguments or {})

    def initialize(self) -> dict[str, Any]:
        public_tool_names = list(MCP_TOOL_NAMES)
        return {
            "ok": True,
            "data": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "agentsight", "title": "AgentSight for Windows"},
                "capabilities": {"tools": {"public_tool_names": public_tool_names}},
                "boundary": {
                    "ocr_used": False,
                    "clipboard_used": False,
                    "accessibility_tree_used": False,
                    "dom_used": False,
                    "window_semantics_used": False,
                    "business_success_judged": False,
                },
            },
        }

    def self_check(self) -> dict[str, Any]:
        public_tool_names = list(MCP_TOOL_NAMES)
        return {
            "ok": True,
            "data": {
                "mcp_server_name": "agentsight",
                "public_tool_names": public_tool_names,
                "expected_client_tool_names": [f"mcp__agentsight__{name}" for name in public_tool_names],
                "token_returned": False,
                "auth_material_returned": False,
                "boundary": {
                    "ocr_used": False,
                    "clipboard_used": False,
                    "accessibility_tree_used": False,
                    "dom_used": False,
                    "window_semantics_used": False,
                    "business_success_judged": False,
                },
            },
        }

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "MCPStdioAdapter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        if method == "initialize":
            return self.initialize()
        if method in {"agentsight/self_check", "self_check"}:
            return self.self_check()
        if method in {"tools/list", "list_tools"}:
            return {"ok": True, "data": self.list_tools()}
        if method in {"tools/call", "call_tool"}:
            params = message.get("params", {})
            return self.call_tool(params.get("name"), params.get("arguments", {}))
        if "tool" in message:
            return self.call_tool(message["tool"], message.get("arguments", {}))
        return {"ok": False, "error": "unsupported MCP adapter message"}


def _jsonrpc_error_response(request_id: object, code: int, message: str, data: object | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _jsonrpc_result_response(request_id: object, result: object) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _legacy_adapter_response_to_jsonrpc(response: dict[str, Any]) -> object:
    if response.get("ok") is True and "data" in response:
        return response["data"]
    return response


def _handle_jsonrpc_message(adapter: MCPStdioAdapter, message: object) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _jsonrpc_error_response(None, -32600, "Invalid Request")

    request_id = message.get("id")
    is_notification = "id" not in message
    method = message.get("method")
    if not isinstance(method, str):
        if is_notification:
            return None
        return _jsonrpc_error_response(request_id, -32600, "Invalid Request")

    if method == "notifications/initialized":
        return None

    try:
        response = adapter.handle_message(message)
    except Exception as exc:  # pragma: no cover - defensive JSON-RPC boundary guard
        if is_notification:
            return None
        return _jsonrpc_error_response(request_id, -32603, "Internal error", {"detail": str(exc)})
    if response.get("ok") is False:
        if is_notification:
            return None
        return _jsonrpc_error_response(request_id, -32601, str(response.get("error", "Method not found")), response)
    if is_notification:
        return None
    return _jsonrpc_result_response(request_id, _legacy_adapter_response_to_jsonrpc(response))


def _is_jsonrpc_message(message: object) -> bool:
    return isinstance(message, dict) and (message.get("jsonrpc") == "2.0" or "id" in message)


def main() -> int:
    with MCPStdioAdapter(enforce_public_tool_allowlist=True) as adapter:
        for line in sys.stdin:
            raw = line.strip()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(
                    json.dumps(_jsonrpc_error_response(None, -32700, "Parse error", {"detail": str(exc)}), ensure_ascii=False),
                    flush=True,
                )
                continue

            if _is_jsonrpc_message(message):
                response = _handle_jsonrpc_message(adapter, message)
            elif isinstance(message, dict):
                response = adapter.handle_message(message)
            else:
                response = _jsonrpc_error_response(None, -32600, "Invalid Request")

            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
