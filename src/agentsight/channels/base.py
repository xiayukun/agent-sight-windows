from __future__ import annotations

from typing import Any, Protocol

from agentsight.evidence.store import EvidenceReplayService


class ChannelFailure(RuntimeError):
    def __init__(
        self,
        failure_code: str,
        *,
        stage: str,
        detail: str | None = None,
        retryable: bool = True,
        channel_ref: str | None = None,
        channel_type: str | None = None,
        implementation: str | None = None,
        requested_mode: str | None = None,
        requested_region: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or failure_code)
        self.failure_code = failure_code
        self.stage = stage
        self.detail = detail
        self.retryable = retryable
        self.channel_ref = channel_ref
        self.channel_type = channel_type
        self.implementation = implementation
        self.requested_mode = requested_mode
        self.requested_region = requested_region


class ObservationChannel(Protocol):
    name: str
    channel_type: str

    def describe(self) -> dict[str, Any]:
        ...

    def capture(self, payload: dict[str, Any], evidence: EvidenceReplayService) -> dict[str, Any]:
        ...


class InputChannel(Protocol):
    name: str
    channel_type: str

    def describe(self) -> dict[str, Any]:
        ...

    def execute(self, input_event: dict[str, Any]) -> dict[str, Any]:
        ...
