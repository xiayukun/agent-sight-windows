# Third-Party Notices

[中文](THIRD-PARTY-NOTICES.md) | English

AgentSight for Windows does not require capture or packaging dependencies in the base package. Optional dependencies include:

- `mss`: candidate cross-platform screen capture;
- `Pillow`: image processing, test PNG generation, and some review artifacts;
- `windows-capture`: Windows Graphics Capture path;
- `PyInstaller`: local executable packaging.

Before release, re-check `pyproject.toml`, the pinned build environment, and the third-party components actually included in release artifacts.

This project read LinkShelf / ServicePilot for release-documentation and workflow style only; implementation code was not copied.
