# AgentSight 1.0.0 Public Architecture

[中文](agentsight-1.0.0-public-architecture.md) | English

This document fixes the public naming, installer, MCP / Skill, installation layout, and release artifact architecture for the first formal AgentSight release. It follows the PM decision recorded on the kanban board: version 1.0.0 ships without public historical aliases; product name, package name, commands, executables, directories, MCP, and Skill use AgentSight / agentsight.

## 1. Decision Summary

- First formal version: `1.0.0`; Git tag: `v1.0.0`.
- GitHub repository: `git@github.com:xiayukun/agent-sight-windows.git`.
- Public product name: `AgentSight for Windows`.
- Python distribution: `agentsight`.
- Python import package: `agentsight`.
- Command prefix: `agentsight-*`; historical commands must not be published as 1.0.0 public entry points.
- Main installer: `AgentSightSetup.exe`.
- User-level install directory: `%LOCALAPPDATA%\AgentSight`.
- Default binding: `127.0.0.1` only; no public port by default.
- Runtime shape: current-user Session Supervisor; no Windows Service, SYSTEM process, driver, or UAC secure desktop control.
- Ordinary AI public chain: `read discovery -> screen -> look -> do -> look`; MCP public tools expose only `screen`, `look`, and `do`.
- Evidence semantics: MKV / frame index / operation log record pixel and event facts only; they do not prove target hit, causality, or business success.

## 2. Naming Matrix

| Layer | 1.0.0 Target | Migration Point |
| --- | --- | --- |
| Repository | `agent-sight-windows` | Local working directory may stay as-is; GitHub remote points to the new repository. |
| Python distribution | `agentsight` | `pyproject.toml [project].name` is unified as `agentsight`; version is `1.0.0`. |
| Python import package | `agentsight` | Source package is unified at `src/agentsight`; imports use `agentsight.*`. |
| Console scripts | `agentsight-*` | Public console scripts use only the `agentsight-*` prefix. |
| MCP server | `agentsight-mcp` / `AgentSightMcp.exe` | Tool names stay `screen`, `look`, `do`; the installer provides `AgentSightMcp.exe` as the AI-facing config entry while the internal build may keep `AgentSightMcpServer.exe` as a compatibility copy. |
| Host Agent | `AgentSightHostAgent.exe` | Migrate current `AgentSightHostAgent.exe` specs, tests, and release workflow. |
| Supervisor | `AgentSightSupervisor.exe` | Migrate current `AgentSightSupervisor.exe`; still the only long-term autostart entry. |
| Tray GUI | `AgentSightTray.exe` | Consolidate public Tray entry naming. |
| Timeline viewer | `AgentSightTimelineViewer.exe` | Already aligned; keep it. |
| Diagnostics | `agentsight-doctor` / `AgentSightDoctor.exe` | Consolidate first-use doctor, capture smoke, and release readiness. |
| Segment decoder | `agentsight-segment-decoder` | Reads existing MKV / index / derived review data only; no capture, input, or success judgment. |
| Data directory | `%LOCALAPPDATA%\AgentSight` | Migrate default agent dir, discovery, state, caller lock, tray config, and runs paths. |
| Discovery | `%LOCALAPPDATA%\AgentSight\host-agent.json` | Suggested schema: `agentsight_discovery_v1`; expose only url, token, public api paths, and diagnostic health URL. |
| Skill | `ai-install\AgentSight\SKILL.md` | Migrate the packaged usage Skill to AgentSight naming and ship it inside the installer. |

## 3. Install Layout

Recommended layout:

```text
%LOCALAPPDATA%\AgentSight\
  app\1.0.0\
    AgentSightSupervisor.exe
    AgentSightHostAgent.exe
    AgentSightTray.exe
    AgentSightTimelineViewer.exe
    AgentSightMcp.exe
    AgentSightMcpServer.exe
    AgentSightDoctor.exe
    agentsight-package-metadata.json
  current\
    ... current runnable copy ...
  data\
    host-agent.json
    service-state.json
    session-supervisor-state.json
    operator-control-policy.json
    caller-lock.json
    tray-settings.json
    tray-config.jsonc
    runs_host_agent\segments\*.mkv
    runs_host_agent\segments\*.frames.jsonl
    runs_host_agent\segments\*.manifest.json
  ai-install\
    AGENTSIGHT_AI_INSTALL_PROMPT.txt
    mcp.json
    SKILL.md
    README_FOR_AI.md
    mcp.config.example.json
    PROMPT_FOR_AI.md
    README.md
    agentsight\SKILL.md
  logs\
  uninstall\AgentSightUninstall.exe
```

Rules:

- `app\<version>` is program payload; `data` is runtime state and evidence; `ai-install` is the handoff package for any AI client.
- Implement `current` as a copied directory for 1.0.0 to avoid symlink permission issues.
- Register only `AgentSightSupervisor` for autostart.
- Uninstall removes autostart and program files by default, but keeps evidence unless the user explicitly chooses deletion.
- Do not require admin rights or write to Program Files.

## 4. AgentSightSetup.exe Behavior

`AgentSightSetup.exe` is the only recommended 1.0.0 download entry.

Main flow:

1. Extract embedded payload into `%LOCALAPPDATA%\AgentSight\app\1.0.0`.
2. Refresh `%LOCALAPPDATA%\AgentSight\current`.
3. Write default data/config files.
4. Register HKCU Run Key `AgentSight` pointing to `current\AgentSightSupervisor.exe run --host 127.0.0.1 --port 8765 --arm-real-input`.
5. Start Supervisor; Supervisor starts Host Agent and Tray.
6. Wait for discovery/readiness and write `data\last-install-report.json`.
7. Generate the `ai-install` package.
8. Show a completion window with status, install path, autostart state, and copyable AI instructions.

## 5. MCP and Skill Delivery

`ai-install` is not a runtime semantic channel; it is a local installation handoff package.

Recommended files:

- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`: short prompt for the user to paste into any AI client.
- `mcp.json`: preferred stdio MCP config; server name is `agentsight`, command points to the absolute `AgentSightMcp.exe` path, and no token is stored.
- `SKILL.md`: authoritative ordinary-AI usage guide.
- `README_FOR_AI.md`: AI-facing install notes for merging MCP config, installing the Skill, and using only `screen` / `look` / `do`.
- `mcp.config.example.json`, `PROMPT_FOR_AI.md`, `README.md`, and `agentsight/SKILL.md`: compatibility copies.

The Skill must preserve boundaries: no OCR, clipboard, DOM, accessibility tree, window semantics, hidden app API, shell-as-GUI-substitute, target-hit judgment, causality judgment, or business-success judgment.

## 6. Release Assets

Recommended 1.0.0 assets:

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

Optional advanced asset, not the main download:

```text
AgentSight-1.0.0-windows-x64-portable.zip
```

Do not put every internal executable on the GitHub Release front page. Host, Tray, Supervisor, MCP server, and diagnostics should be bundled inside the setup exe.

GitHub About:

```text
Local Windows host for AI agents: screen pixels in, mouse and keyboard out, with audit logs and replayable evidence. Built for computer-use workflows, not hidden app APIs.
```

Topics:

```text
windows, windows-ai, ai-agents, computer-use, gui-automation, desktop-automation, mcp, model-context-protocol, pixel-grounded, screen-observation, mouse-keyboard, human-input, audit-trail, replay, evidence, local-first, python, pyinstaller, agent-tools, windows-gui
```

## 7. Implementation Path

This task only defines architecture. Downstream engineering cards should implement in small chunks:

1. Protect current worktree state; do not reset or delete unknown changes.
2. Mechanically rename package/imports/scripts/specs/tests to AgentSight / agentsight.
3. Implement setup exe, user-level install layout, autostart, uninstall, and ai-install generation.
4. Migrate MCP / Skill / README / release docs.
5. Update release workflow to build the setup exe, run tests before release, and generate SHA256.

## 8. Test Matrix

| Target | Check | Expected Result |
| --- | --- | --- |
| Import | `python -c "import agentsight; print(agentsight.__version__)"` | Prints `1.0.0`. |
| Public protocol | `python -m unittest tests.acceptance.test_p3a_screen_look_do_protocol` | `screen/look/do` readiness and boundaries remain intact. |
| Supervisor | `python -m unittest tests.acceptance.test_p1x_session_supervisor` | Autostart, status, stop, and uninstall semantics are correct. |
| Tray | `python -m unittest tests.acceptance.test_p1g_tray_gui_control_surface` | Pause, allow, emergency stop, settings, and timeline entries still work. |
| Packaging | `python -m unittest tests.acceptance.test_packaging_round7` | Specs, expected outputs, wrappers, and asset names are aligned. |
| MKV storage | `python -m unittest tests.acceptance.test_mkv_segment_storage tests.acceptance.test_pf2_idle_capture_and_rotation` | Canonical storage stays MKV VFR; no PNG/GIF mainline regression. |
| Installer dry run | Temporary `%LOCALAPPDATA%` override | Creates app/current/data/ai-install; Run Key points to Supervisor; host binds to 127.0.0.1. |
| Public old-name gate | Search public docs, scripts, specs, workflow assets | No old names remain as public entry points. |
| Release | GitHub Actions release job | Tests pass before setup exe and checksums are published. |

## 9. Risks and Protections

- Large rename risk: do mechanical rename first, behavior changes second, and run focused tests after each slice.
- Dirty worktree risk: every downstream card records `git status --short` and avoids reset/delete.
- Running AgentSight risk: installer/autostart work only affects AgentSight processes, not Hermes or gateway.
- Data migration risk: 1.0.0 does not silently delete old `%LOCALAPPDATA%\AgentSight`; detection should be informational.
- Evidence leakage risk: release workflow must not upload runs, screenshots, videos, browser profiles, tokens, or local evidence.
- Naming leakage risk: Git history may contain old names, but 1.0.0 public entry points, README, release assets, and ai-install must not present old names as the main path.

## 10. Rollback

- Before release: stop the current engineering card and keep the diff for review; do not use `git reset --hard`.
- Failed local install: run `AgentSightUninstall.exe` or `AgentSightSupervisor.exe uninstall`; keep `data` by default.
- Failed release: do not publish or withdraw the `v1.0.0` release asset; fix and rerun workflow.
- Old-name leak: treat as a release blocker, rebuild setup, and regenerate SHA256.
