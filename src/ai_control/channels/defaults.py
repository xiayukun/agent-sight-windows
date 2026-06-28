from __future__ import annotations

from ai_control.channels.dry_run_input import DryRunInputChannel
from ai_control.channels.mock import MockObservationChannel
from ai_control.channels.mock import MockInputChannel
from ai_control.channels.mss_capture import MssObservationChannel
from ai_control.channels.pillow_imagegrab import PillowImageGrabObservationChannel
from ai_control.channels.windows_capture import WindowsCaptureObservationChannel
from ai_control.channels.windows_software import WindowsSoftwareInputChannel, WindowsSoftwareObservationChannel


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
