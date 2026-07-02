from __future__ import annotations

from agentsight.channels.dry_run_input import DryRunInputChannel
from agentsight.channels.mock import MockObservationChannel
from agentsight.channels.mock import MockInputChannel
from agentsight.channels.mss_capture import MssObservationChannel
from agentsight.channels.pillow_imagegrab import PillowImageGrabObservationChannel
from agentsight.channels.windows_capture import WindowsCaptureObservationChannel
from agentsight.channels.windows_software import WindowsSoftwareInputChannel, WindowsSoftwareObservationChannel


DEFAULT_OBSERVATION_CHANNEL_REF = "mock_screen"
DEFAULT_INPUT_CHANNEL_REF = "mock_input"


def default_observation_channels() -> list[object]:
    return [
        MockObservationChannel(),
        WindowsSoftwareObservationChannel(),
        MssObservationChannel(),
        PillowImageGrabObservationChannel(),
        WindowsCaptureObservationChannel(),
    ]


def default_input_channels() -> list[object]:
    return [
        MockInputChannel(),
        DryRunInputChannel(),
        WindowsSoftwareInputChannel(),
    ]
