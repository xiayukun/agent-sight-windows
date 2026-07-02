from __future__ import annotations

from pathlib import Path
from typing import Any

from agentsight.adapters.session import SessionAdapter
from agentsight.channels.base import InputChannel, ObservationChannel


class AgentSightClient:
    """AI user-facing local adapter.

    Tests and manual validation should use this adapter instead of importing
    internal services directly.
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
    ) -> None:
        self._session = SessionAdapter(
            runs_dir,
            adapter_ref="local_client",
            observation_channel=observation_channel,
            observation_channels=observation_channels,
            default_observation_channel_ref=default_observation_channel_ref,
            input_channel=input_channel,
            input_channels=input_channels,
            default_input_channel_ref=default_input_channel_ref,
        )

    @property
    def session_id(self) -> str:
        return self._session.session_id

    def call(self, command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._session.call(command, payload)
