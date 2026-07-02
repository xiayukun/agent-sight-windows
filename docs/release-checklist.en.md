# Release Checklist

[中文](release-checklist.md) | English

## Before release

- [ ] Confirm version `1.0.0`; tag is `v1.0.0`.
- [ ] README.md / README.en.md are updated.
- [ ] CHANGELOG.md / CHANGELOG.en.md are updated.
- [ ] `docs/release-notes-v1.0.0.md` and the English mirror are ready.
- [ ] The About / Topics from [repository-profile.en.md](repository-profile.en.md) are applied to the GitHub repository.
- [ ] Confirm the target GitHub repository is `git@github.com:xiayukun/agent-sight-windows.git` / `xiayukun/agent-sight-windows`; do not push tags or releases to the local internal remote.
- [ ] Local `runs*`, evidence directories, screenshot caches, `dist/`, `build/`, browser profiles, tokens, and private paths are cleaned before publishing.
- [ ] Run Key / Startup points to packaged `AgentSightSupervisor.exe`, not a stale source path.
- [ ] No reset, deletion, or overwrite of unknown uncommitted work.

## Tests

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

## Build

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

Expected core artifacts:

```text
dist\AgentSightSetup.exe
dist\AgentSightSupervisor.exe
dist\AgentSightHostAgent.exe
dist\AgentSightTray.exe
dist\AgentSightMcp.exe
```

Recommended release-front-page assets:

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

## Local smoke

- [ ] `AgentSightSetup.exe install --no-gui --start-now --arm-real-input` installs successfully.
- [ ] `%LOCALAPPDATA%\AgentSight\ai-install` contains `mcp.json`, `SKILL.md`, `README_FOR_AI.md`, and `AGENTSIGHT_AI_INSTALL_PROMPT.txt`.
- [ ] Host Agent discovery is readable.
- [ ] `screen -> look -> do -> look` still works.
- [ ] Tray icon is visible and the context menu opens.
- [ ] Language menu switches between Follow System, Chinese, and English.
- [ ] Emergency stop blocks real control.

## GitHub Release

- [ ] Create `v1.0.0` tag.
- [ ] The release workflow `GITHUB_REPOSITORY` guard passes for `xiayukun/agent-sight-windows`.
- [ ] Release workflow tests first; failed tests stop publication.
- [ ] The workflow publishes only the versioned setup exe, its per-file sha256 file, and `SHA256SUMS.txt` as GitHub Release front-page assets; internal Host/Tray/Supervisor/MCP executables remain inside the setup or CI artifact.
- [ ] Release notes put Chinese first and English below, or link to the English mirror.
- [ ] Release assets do not include runs, screenshots, videos, browser profiles, tokens, private paths, or local evidence.
