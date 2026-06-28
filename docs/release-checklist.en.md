# Release Checklist

[中文](release-checklist.md) | English

## Before Release

- [ ] README.md / README.en.md are updated.
- [ ] CHANGELOG.md / CHANGELOG.en.md are updated.
- [ ] Chinese release notes and English mirror are prepared.
- [ ] Local `runs*`, evidence directories, screenshot caches, `dist/`, and `build/` are cleaned.
- [ ] Run Key / Startup points to packaged `AIControlSessionSupervisor.exe`, not a stale source path.
- [ ] Do not rename the active `C:\git\其他\ai-control` Codex workspace directly.

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
dist\AIControlSessionSupervisor.exe
dist\AIControlHostAgent.exe
dist\AIControlTrayGui.exe
dist\AIControlInstaller.exe
```

## Local Smoke

- [ ] `AIControlSessionSupervisor.exe status` returns state.
- [ ] Host Agent discovery is readable.
- [ ] `/screen -> /look -> /do -> /look` still works.
- [ ] Tray icon is visible and context menu opens.
- [ ] Language menu switches between Follow System, Chinese, and English.
- [ ] Emergency stop blocks real control.

## GitHub Release

- [ ] Create a `v*` tag.
- [ ] Release workflow passes tests.
- [ ] Workflow uploads executables and `SHA256SUMS.txt`.
- [ ] Release notes are Chinese-first with English below or linked.
- [ ] Do not publish when tests fail.
