from __future__ import annotations

from typing import Any, Iterable

from ai_control.channels.base import ChannelFailure, InputChannel, ObservationChannel


class ObservationChannelRegistry:
    def __init__(self, channels: Iterable[ObservationChannel], *, default_channel_ref: str | None = None) -> None:
        self._channels = {channel.name: channel for channel in channels}
        if not self._channels:
            raise ValueError("at least one observation channel is required")
        self.default_channel_ref = default_channel_ref or next(iter(self._channels))

    def describe_all(self) -> list[dict[str, Any]]:
        return [channel.describe() for channel in self._channels.values()]

    def resolve(self, channel_ref: str | None = None) -> ObservationChannel:
        selected_ref = channel_ref or self.default_channel_ref
        channel = self._channels.get(selected_ref)
        if not channel:
            raise ChannelFailure(
                "OBSERVATION_CHANNEL_UNAVAILABLE",
                stage="ObservationChannelRegistry.resolve",
                detail=f"unknown observation channel: {selected_ref}",
                retryable=False,
                channel_ref=selected_ref,
                channel_type="observation",
            )
        return channel

    def refs(self) -> list[str]:
        return list(self._channels)


class InputChannelRegistry:
    def __init__(self, channels: Iterable[InputChannel], *, default_channel_ref: str | None = None) -> None:
        self._channels = {channel.name: channel for channel in channels}
        if not self._channels:
            raise ValueError("at least one input channel is required")
        self.default_channel_ref = default_channel_ref or next(iter(self._channels))

    def describe_all(self) -> list[dict[str, Any]]:
        return [channel.describe() for channel in self._channels.values()]

    def resolve(self, channel_ref: str | None = None) -> InputChannel:
        selected_ref = channel_ref or self.default_channel_ref
        channel = self._channels.get(selected_ref)
        if not channel:
            raise ChannelFailure(
                "INPUT_CHANNEL_UNAVAILABLE",
                stage="InputChannelRegistry.resolve",
                detail=f"unknown input channel: {selected_ref}",
                retryable=False,
                channel_ref=selected_ref,
                channel_type="input",
            )
        return channel

    def refs(self) -> list[str]:
        return list(self._channels)
