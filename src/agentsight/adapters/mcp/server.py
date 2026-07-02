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

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "MCPStdioAdapter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def handle_message(self, message: dict[str, Any]) -> dict[str, Any]:
        method = message.get("method")
        if method in {"tools/list", "list_tools"}:
            return {"ok": True, "data": self.list_tools()}
        if method in {"tools/call", "call_tool"}:
            params = message.get("params", {})
            return self.call_tool(params.get("name"), params.get("arguments", {}))
        if "tool" in message:
            return self.call_tool(message["tool"], message.get("arguments", {}))
        return {"ok": False, "error": "unsupported MCP adapter message"}


def main() -> int:
    adapter = MCPStdioAdapter(enforce_public_tool_allowlist=True)
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"ok": False, "error": "empty input"}))
        return 1

    messages = [json.loads(line) for line in raw.splitlines() if line.strip()]
    responses = [adapter.handle_message(message) for message in messages]
    if len(responses) == 1:
        print(json.dumps(responses[0], ensure_ascii=False))
    else:
        for response in responses:
            print(json.dumps(response, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
