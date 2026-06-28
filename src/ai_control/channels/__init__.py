"""Observation and input channel implementations."""

from ai_control.channels.mock import MockInputChannel, MockObservationChannel
from ai_control.channels.dry_run_input import DryRunInputChannel
from ai_control.channels.mss_capture import MssObservationChannel
from ai_control.channels.pillow_imagegrab import PillowImageGrabObservationChannel
from ai_control.channels.windows_capture import WindowsCaptureObservationChannel
from ai_control.channels.windows_software import WindowsSoftwareInputChannel, WindowsSoftwareObservationChannel
from ai_control.channels.defaults import (
    DEFAULT_INPUT_CHANNEL_REF,
    DEFAULT_OBSERVATION_CHANNEL_REF,
    default_input_channels,
    default_observation_channels,
)

__all__ = [
    "DEFAULT_INPUT_CHANNEL_REF",
    "DEFAULT_OBSERVATION_CHANNEL_REF",
    "DryRunInputChannel",
    "MockInputChannel",
    "MockObservationChannel",
    "MssObservationChannel",
    "PillowImageGrabObservationChannel",
    "WindowsCaptureObservationChannel",
    "WindowsSoftwareInputChannel",
    "WindowsSoftwareObservationChannel",
    "default_input_channels",
    "default_observation_channels",
]
