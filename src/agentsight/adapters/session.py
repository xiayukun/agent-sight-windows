from __future__ import annotations

from pathlib import Path
from typing import Any

from agentsight.channels.base import InputChannel, ObservationChannel
from agentsight.gateway import ProtocolGateway
from agentsight.visual_memory.frame_buffer import FrameBufferConfig


class SessionAdapter:
    """Persistent AI-facing session over one ProtocolGateway."""

    def __init__(
        self,
        runs_dir: str | Path = "runs",
        *,
        adapter_ref: str = "session",
        observation_channel: ObservationChannel | None = None,
        observation_channels: list[ObservationChannel] | None = None,
        default_observation_channel_ref: str | None = None,
        input_channel: InputChannel | None = None,
        input_channels: list[InputChannel] | None = None,
        default_input_channel_ref: str | None = None,
        frame_buffer_config: FrameBufferConfig | None = None,
    ) -> None:
        self.gateway = ProtocolGateway(
            runs_dir,
            adapter_ref=adapter_ref,
            observation_channel=observation_channel,
            observation_channels=observation_channels,
            default_observation_channel_ref=default_observation_channel_ref,
            input_channel=input_channel,
            input_channels=input_channels,
            default_input_channel_ref=default_input_channel_ref,
            frame_buffer_config=frame_buffer_config,
        )
        self.transcript: list[dict[str, Any]] = []

    @property
    def session_id(self) -> str:
        return self.gateway.evidence.session_id

    def call(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.call_raw({"command": command, "payload": payload or {}})

    def call_raw(self, request: dict[str, Any]) -> dict[str, Any]:
        response = self.gateway.handle(request)
        self.transcript.append({"request": request, "response": response})
        return response

    def close(self) -> None:
        self.gateway.close()

    def __enter__(self) -> "SessionAdapter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
