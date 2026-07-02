# AgentSight for Windows v1.0.0 release notes draft

[中文](release-notes-v1.0.0.md) | English

AgentSight for Windows v1.0.0 is the first formal release. This release aligns the public product name, Python package, commands, executables, install directory, MCP, and Skill around AgentSight / agentsight, without carrying old experiment names as public entry points.

## Downloads

Recommended downloads:

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

Most users only need `AgentSightSetup-1.0.0-windows-x64.exe`. Host Agent, Tray, Supervisor, MCP server, and Timeline viewer are bundled inside the installer.

## What installation does

- Installs into `%LOCALAPPDATA%\AgentSight`.
- Registers current-user autostart for `AgentSightSupervisor`.
- Starts the Host Agent and Tray GUI.
- Writes `%LOCALAPPDATA%\AgentSight\ai-install`.
- The `ai-install` package includes `mcp.json`, `SKILL.md`, `README_FOR_AI.md`, and `AGENTSIGHT_AI_INSTALL_PROMPT.txt`.
- Binds to `127.0.0.1` by default; no public port is opened.

## Highlights

- Public AI flow is `read discovery -> screen -> look -> do -> look`.
- Public MCP tools are only `screen`, `look`, and `do`.
- `screen` / `look` / `do` responses embed readiness and blockers, so ordinary AI callers do not need `/health` first.
- `look` supports `scale_down`, regions, crops, and `view_id` for attention-style focus.
- `do` sends human-equivalent mouse and keyboard input from a current view.
- Evidence storage uses MKV VFR, `.frames.jsonl`, manifests, and operation logs.
- Tray GUI provides pause, allow, emergency stop, capture/retention settings, timeline, and operation-log review.
- The installer writes MCP + Skill handoff files so users can connect AgentSight to any compatible AI client.

## Boundary

AgentSight records and executes pixel/input facts. It does not provide:

- OCR;
- clipboard read/write;
- DOM;
- accessibility tree;
- window semantics;
- hidden app APIs;
- shell/cmd as a GUI substitute;
- target-hit judgment;
- causality judgment;
- business-success judgment.

`host_sent_event_count > 0` does not mean the target was hit. `integrity_ok = true` does not mean the task succeeded. Pixel change does not prove that input caused the change.

## Known limits

- Current runtime uses a current-user Session Supervisor, not a Windows Service.
- Lock screen, UAC secure desktop, operator pause, emergency stop, caller lock, capture failure, and input failure are returned as blockers.
- Evidence may contain real screen content. Redact before publishing or sharing.
- Old runs / evidence are not deleted automatically during install.

## Pre-release checks

- [ ] `py -m unittest discover tests` passes.
- [ ] `py tools/build_host_agent_exe.py` produces the setup executable.
- [ ] `AgentSightSetup.exe` installs and writes `ai-install`.
- [ ] SHA256 checksums are generated.
- [ ] Release assets do not include runs, screenshots, videos, browser profiles, tokens, private paths, or local evidence.
