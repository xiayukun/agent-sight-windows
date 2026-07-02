# User Guide

[中文](user-guide.md) | English

## Who it is for

AgentSight for Windows is for AI agents that need real Windows GUI control: computer-use agents, local desktop agents, and automation experiments where reviewable evidence matters.

It is not remote desktop software, OCR software, or a hidden business API. The agent sees screen pixels and sends mouse/keyboard-style input.

## Install

Download from the GitHub Release page:

```text
AgentSightSetup-1.0.0-windows-x64.exe
```

In a local build directory, the equivalent file is:

```text
dist\AgentSightSetup.exe
```

The installer writes AgentSight into the current user's local app directory:

```text
%LOCALAPPDATA%\AgentSight
```

It registers current-user autostart for `AgentSightSupervisor`. The Supervisor starts the Host Agent and Tray GUI.

## Connect AgentSight to an AI client

The installer writes:

```text
%LOCALAPPDATA%\AgentSight\ai-install
```

Point your AI client at these files:

- `mcp.json`: MCP stdio config; server name is `agentsight`.
- `SKILL.md`: usage rules and boundaries for ordinary AI callers.
- `README_FOR_AI.md`: setup notes.
- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`: a short prompt you can paste into an AI client.

After setup, ordinary AI callers use only the `screen`, `look`, and `do` MCP tools.

## Public AI flow

```text
read discovery -> screen -> look -> do -> look
```

- `screen`: virtual-screen coordinates, readiness, blockers, and frame-index facts.
- `look`: screen pixels, with `scale_down`, regions, and `view_id` for attention-style focus.
- `do`: human-equivalent input based on a current `view_id`.
- `look time.near`: review already-indexed frames near an approximate time; it does not take a new screenshot.
- `look q="changes"` / `q="diff"` / `q="clip"`: read existing Segment change summaries, diffs, or derived review clips.

If readiness reports lock screen, UAC secure desktop, operator pause, emergency stop, caller lock, capture failure, or input failure, the AI should stop and report the blocker.

## Tray control surface

The Tray GUI is a human-visible control surface, not an AI semantic channel. It provides:

- ready / paused / emergency / blocked / discovery_missing / unknown status icon;
- pause AI control;
- allow AI control;
- emergency stop and clear emergency stop;
- capture and retention settings;
- open timeline;
- view operation log;
- stop AgentSight;
- language: Follow System, Chinese, English.

Language settings are stored in:

```text
%LOCALAPPDATA%\AgentSight\tray-settings.json
```

Capture and retention policy is stored in:

```text
%LOCALAPPDATA%\AgentSight\tray-config.jsonc
```

The settings window keeps user-facing policy only: idle FPS, action pre/post frames, post-action FPS / duration, retention days, and disk-space limits. MKV container details, frame indexes, and encoder behavior are internal implementation details.

## Evidence and privacy

Current canonical evidence is:

```text
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.mkv
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.frames.jsonl
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.manifest.json
```

Operation logs, MKV files, frame indexes, and manifests record pixel and event facts. Qt timeline previews, diff heatmaps, GIFs, and screenshot caches are human-review derived artifacts / derived review artifact outputs, not canonical evidence.

Evidence may contain real screen content. Redact before publishing or sharing. Do not upload runs, screenshots, videos, browser profiles, tokens, private paths, or local evidence to GitHub.

## Boundary

AgentSight does not provide OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, shell/cmd as a GUI substitute, target-hit judgment, causality judgment, or business-success judgment. The caller AI may make its own external visual judgment from returned pixels, but it must not claim that AgentSight supplied those semantics directly.
