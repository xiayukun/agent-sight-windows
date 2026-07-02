# Changelog

[中文](CHANGELOG.md) | English

## Unreleased

- Public branding is aligned around `AgentSight for Windows`; the first formal release is `1.0.0`, with public package, commands, executables, MCP, and Skill using AgentSight / agentsight.
- README first screen is rewritten for GitHub launch: `AgentSightSetup.exe` download, MCP + Skill setup, Windows safety boundaries, evidence/replay/visual memory.
- Repository About / Topics copy is updated with more natural GitHub wording while covering Windows AI agent, computer-use, MCP, pixel-grounded, evidence, and replay keywords.
- Added `docs/release-notes-v1.0.0.md` / `.en.md` release-notes drafts.
- Release checklist now follows the 1.0.0 setup-exe path and emphasizes setup exe + checksums as primary release assets, with no runs, screenshots, videos, tokens, or local evidence in release artifacts.
- Tray GUI adds Chinese/English menu and tooltip localization, persisted in `%LOCALAPPDATA%\AgentSight\tray-settings.json`.
- Release workflow supports `v*` tags and manual dispatch, runs tests, builds executables, creates SHA256 checksums, and uploads artifacts.
- Visual-memory docs frame `scale_down`, region, crop, `view_id`, `time.near`, change summaries, diffs, and review clips as the attention/review toolbox.
