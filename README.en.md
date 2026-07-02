# AgentSight for Windows

[中文](README.md) | English

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
![Boundary](https://img.shields.io/badge/boundary-pixel--grounded-green)

AgentSight for Windows is a local Windows host for AI agents. It lets an agent look at real screen pixels, act through human-equivalent mouse and keyboard input, and leave an audit trail that a person can review later.

It is built for computer-use agents, Windows AI agents, local GUI automation experiments, and desktop workflows where evidence matters. It does not turn Windows into hidden app APIs, and it does not decide what the UI means for the agent.

> A pixel-grounded observe-and-act host for Windows AI agents.

## Download

The first formal release is `1.0.0`. On the GitHub Release page, the recommended front-door download is:

```text
AgentSightSetup-1.0.0-windows-x64.exe
```

In a local build directory, the setup file is usually named:

```text
dist\AgentSightSetup.exe
```

When you run `AgentSightSetup.exe`, it:

1. installs into `%LOCALAPPDATA%\AgentSight`;
2. registers current-user autostart for `AgentSightSupervisor` only;
3. starts the Host Agent and Tray GUI;
4. writes an `ai-install` handoff package for AI clients;
5. shows a copyable prompt that you can paste into any AI client to finish MCP + Skill setup.

It does not need administrator rights. By default it binds only to `127.0.0.1`; it does not expose a public port.

## Connect an AI client: MCP + Skill

After installation, the AI handoff package is here:

```text
%LOCALAPPDATA%\AgentSight\ai-install
```

The important files are:

- `mcp.json`: MCP configuration for clients that support it. The server name is `agentsight`; the command points to the local `AgentSightMcp.exe`.
- `SKILL.md`: the operating guide for ordinary AI callers. It keeps the public surface to `screen`, `look`, and `do`.
- `README_FOR_AI.md`: short setup notes for the AI client.
- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`: a prompt you can paste into an AI client so it can read the local files and finish setup.

Once connected, the ordinary public flow is:

```text
read discovery -> screen -> look -> do -> look
```

The public MCP tools are only:

```text
screen
look
do
```

`/health`, diagnostic commands, and internal tools are for Tray, Supervisor, installer, and maintenance flows. They are not an ordinary GUI-task preflight.

## What it does

- Returns real Windows GUI pixels, coordinates, timing facts, readiness, and blockers.
- Uses `scale_down`, regions, crops, and `view_id` so the agent can focus attention instead of taking a full-resolution desktop image every time.
- Sends human-equivalent mouse and keyboard input when the operator allows it and the Host Agent is ready.
- Records operation logs, MKV VFR video segments, `.frames.jsonl` frame indexes, and `.manifest.json` manifests.
- Provides visual-memory review helpers such as `time.near`, change summaries, diffs, and review clips.
- Keeps a visible Tray GUI for pause, allow, emergency stop, capture/retention settings, timeline, and operation log review.

## What it does not do

AgentSight has a hard boundary. It does not provide:

- OCR;
- clipboard read/write;
- DOM;
- accessibility tree;
- window semantics;
- hidden app APIs;
- shell/cmd as a substitute for GUI use;
- target-hit judgment;
- causality judgment;
- business-success judgment.

`host_sent_event_count > 0` means input events were sent or inserted. It does not mean the target was hit. `integrity_ok = true` means the evidence structure is consistent. It does not mean the task succeeded. Pixel change is only pixel change, not proof that the input caused it.

## Evidence, replay, and visual memory

Current canonical storage is MKV VFR:

```text
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.mkv
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.frames.jsonl
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.manifest.json
```

These files record pixel and event facts for external review. The native PySide6/Qt `AgentSightTimelineViewer` reads MKV sidecar frame indexes and decodes selected frames in memory. Diff heatmaps, GIFs, screenshot previews, and explicit caches are derived review artifacts, not canonical evidence.

## Runtime shape

AgentSight 1.0.0 uses a current-user runtime:

```text
HKCU Run Key / Startup
  -> AgentSightSupervisor
       -> AgentSightHostAgent
       -> AgentSightTray
```

It is not a Windows Service, SYSTEM process, driver, UAC secure desktop controller, or public network service. Lock screen, UAC secure desktop, operator pause, emergency stop, caller lock, capture failure, and input failure are reported as blockers.

## Development

Source-mode run:

```powershell
$env:PYTHONPATH = "src"
py -m agentsight.session_supervisor run --host 127.0.0.1 --port 8765 --arm-real-input
```

Tests:

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

Build the current PyInstaller packaged layout:

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

## Docs

- [User guide](docs/user-guide.en.md)
- [AgentSight 1.0.0 public architecture](docs/agentsight-1.0.0-public-architecture.en.md)
- [Repository About / Topics](docs/repository-profile.en.md)
- [Release checklist](docs/release-checklist.en.md)
- [v1.0.0 release notes draft](docs/release-notes-v1.0.0.en.md)
- [Screen / Look / Do protocol](docs/SCREEN_LOOK_DO_PROTOCOL.md)
- [Visual memory and attention](docs/visual-memory-and-attention.en.md)
- [Hermes onboarding guide](docs/HERMES_ONBOARDING.md)
