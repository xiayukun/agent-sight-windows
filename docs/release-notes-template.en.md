# Release Notes Template

[中文](release-notes-template.md) | English

Copy this template to `docs/release-notes-vX.Y.Z.en.md`; the Chinese primary file is `docs/release-notes-vX.Y.Z.md`.

## Highlights

- AgentSight for Windows provides a pixel-grounded observe-and-act Windows Host Agent.
- Public GUI-control flow: `screen -> look -> do -> look`.
- Visual memory + attention toolbox: `scale_down`, region, `view_id`, `time.near`.
- Evidence chain: evidence / replay / integrity.
- Tray GUI: status icon, context menu, pause/allow, emergency stop, language switching.

## Downloads

- `AIControlSessionSupervisor.exe`
- `AIControlHostAgent.exe`
- `AIControlTrayGui.exe`
- `AIControlInstaller.exe`
- `SHA256SUMS.txt`

## Boundary

No OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, shell/cmd GUI substitute, business-success judgment, causality judgment, or target-hit judgment.

## Known Limits

- Current runtime uses a user-mode Session Supervisor, not a Windows Service.
- Lock screen, UAC secure desktop, and permission blockers are reported as blockers.
- Python package and commands still keep the historical `ai_control` / `ai-control` names.

## Pre-Release Checks

- [ ] Full tests pass.
- [ ] Release workflow builds artifacts.
- [ ] SHA256 checksums are generated.
- [ ] Screenshot evidence and local caches are not included in source.
