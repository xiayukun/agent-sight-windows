"""Observation and input channel implementations."""

from agentsight.channels.mock import MockInputChannel, MockObservationChannel
from agentsight.channels.dry_run_input import DryRunInputChannel
from agentsight.channels.mss_capture import MssObservationChannel
from agentsight.channels.pillow_imagegrab import PillowImageGrabObservationChannel
from agentsight.channels.windows_capture import WindowsCaptureObservationChannel
from agentsight.channels.windows_software import WindowsSoftwareInputChannel, WindowsSoftwareObservationChannel
from agentsight.channels.defaults import (
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
