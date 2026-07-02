# Release Notes Template

[中文](release-notes-template.md) | English

Copy this template to `docs/release-notes-vX.Y.Z.en.md`; the Chinese primary file is `docs/release-notes-vX.Y.Z.md`. The GitHub Release body should put Chinese first and English below it, or link to the English mirror.

## AgentSight for Windows vX.Y.Z

One sentence: AgentSight for Windows is a local Windows Host Agent that lets AI agents observe real screen pixels, act through mouse and keyboard input, and leave reviewable evidence.

## Downloads

Recommended downloads:

```text
AgentSightSetup-X.Y.Z-windows-x64.exe
AgentSightSetup-X.Y.Z-windows-x64.sha256.txt
SHA256SUMS.txt
```

Most users should download `AgentSightSetup.exe`. Host Agent, Tray, Supervisor, MCP server, and Timeline viewer are bundled inside the installer and should not be scattered across the release front page as primary assets.

## What installation does

- Installs into `%LOCALAPPDATA%\AgentSight`.
- Registers current-user autostart for `AgentSightSupervisor` only.
- Starts the Host Agent and Tray GUI.
- Writes `%LOCALAPPDATA%\AgentSight\ai-install`, including `mcp.json`, `SKILL.md`, `README_FOR_AI.md`, and a copyable prompt.
- Binds to `127.0.0.1` by default.

## Highlights

- Public GUI-control flow: `screen -> look -> do -> look`.
- Public MCP tools: `screen`, `look`, `do`.
- Visual attention tools: `scale_down`, region, crop, `view_id`.
- Visual memory: `time.near`, change summaries, timeline diffs, review clips.
- Evidence chain: MKV VFR, `.frames.jsonl`, manifests, operation logs.
- Human control surface: Tray pause / allow / emergency stop / timeline / operation log.

## Boundary

AgentSight does not provide OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, shell/cmd as a GUI substitute, target-hit judgment, causality judgment, or business-success judgment.

## Known limits

- Current runtime uses a current-user Session Supervisor, not a Windows Service.
- Lock screen, UAC secure desktop, operator pause, emergency stop, caller lock, capture failure, and input failure are returned as blockers.
- Evidence may contain real screen content. Redact before publishing or sharing.

## Pre-release checks

- [ ] Full tests pass.
- [ ] Release workflow tests first, then builds.
- [ ] SHA256 checksums are generated.
- [ ] The `ai-install` package includes MCP config, Skill, and README.
- [ ] Local runs, screenshots, videos, browser profiles, tokens, private paths, and evidence are not included in release assets or source.
