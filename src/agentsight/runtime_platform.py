from __future__ import annotations

import os
import platform
import sys


def is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def platform_system_label() -> str:
    return "Windows" if is_windows() else platform.system()
