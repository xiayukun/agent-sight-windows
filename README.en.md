# AgentSight for Windows

[中文](README.md) | English

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
![Boundary](https://img.shields.io/badge/boundary-pixel--grounded-green)

AgentSight for Windows is a pixel-grounded observe-and-act host for Windows AI agents. It lets a Windows AI agent / computer-use agent observe real screen pixels, act through human-equivalent mouse and keyboard input, and keep evidence / replay / integrity records for external review.

Tagline:

> A pixel-grounded observe-and-act host for Windows AI agents.

Compatibility note: the current Python package, commands, data directory, and some UI labels still use the historical `ai_control`, `ai-control`, or `AI-Control` names. The public product name is `AgentSight for Windows`, and the recommended repository name is `agent-sight-windows`. Do not rename the active local workspace directly.

## Keywords

AgentSight for Windows, AI-Control, Windows AI agent, computer use, GUI automation, pixel-grounded control, observe and act, human-equivalent input, evidence / replay / integrity, visual memory, attention toolbox, Windows Host Agent, AI 控制, Windows 图形界面控制, 像素级观察, 人类等价鼠标键盘操作, 证据链, 回放, 视觉记忆系统。

## Boundary

AgentSight does two things:

- observe like a human: return real Windows GUI pixels, coordinates, time facts, evidence paths, and visual-memory indexes;
- act like a human: send mouse/keyboard-style input when the Host Agent is ready and the operator allows it.

AgentSight does not provide OCR, clipboard read/write, DOM, accessibility tree, window semantics, shell/cmd as a GUI substitute, hidden application APIs, target-hit judgment, causality judgment, or business-success judgment.

All UI interpretation remains the caller AI's external visual judgment over returned pixels.

## Current Shape

AgentSight is still research/MVP software, but the main path is demonstrable:

- a user-mode `AIControlSessionSupervisor` manages the Host Agent and Tray GUI;
- the Host Agent exposes the public GUI-control flow `screen -> look -> do -> look`;
- `/screen`, `/look`, and `/do` embed readiness fields, so ordinary AI callers do not need `/health` as a preflight;
- `/look` supports `scale_down`, region, and `view_id` for attention-style focusing;
- `/do` uses `basis.view_id`, and move/click are separate steps;
- when `/do` omits `post_observe`, the Host Agent can apply a bounded post-action observation window from the tray recording policy;
- input, screenshots, evidence, replay, and integrity remain externally reviewable;
- visual memory / attention toolbox supports `time.near` lookup for approximate time-based frame retrieval, public `/look q="changes"` metadata-only Segment change summaries, `/look q="diff" mode="timeline"` / `timeline_with_artifacts` for on-demand Segment-window diff review, and `/look q="clip"` for derived review GIFs;
- Tray GUI is a human control surface with status icon, context menu, pause/allow, emergency stop, language switching, and a capture/retention settings entry point.
- Tray recording configuration is centered on `%LOCALAPPDATA%\ai-control\tray-config.jsonc` and now contains only user-adjustable capture and retention policy: idle FPS, action pre/post frames, post-action FPS, post-action duration, and retention days. Timeline is always enabled and operation logs are always saved; they are not user toggles.
- The tray uses a modern scrollable Windows capture/retention settings dialog in the current tray language and saves to `tray-config.jsonc`. Timeline and operation-log review now use the native PySide6/Qt `AgentSightTimelineViewer`, which reads `.agseg` metadata and decodes the selected frame in memory. It no longer generates HTML, PNG, GIF, or timestamped review bundles by default. `.agseg` plus the operation log remain canonical evidence / canonical storage; Qt previews are human-review derived artifacts only. Full ring-buffer / long-term video archive remain later work.
- The video-storage track now has single-file `.agseg` Segment storage plus P-G/P-H visual-memory slices: canonical storage uses keyframe + P-frame delta crop; public `/screen`, `/look`, and `/do` post-observe raw frames are mirrored into Segments; Host Agent visual observe and public `/look` expose `segment_frame`; `time.near` returns `segment_restore_ref`; operation logs extract `segment_frame_refs`; the timeline model can read Segments and generate derived restored previews; on-demand crop/diff artifacts, metadata-only `changes`, timeline diff heatmaps, review clip GIFs, expired-unreferenced Segment pruning, the Host Agent lightweight idle capture loop, and daily/hourly Segment rotation are available. Long-term ring buffer storage and the complete player UI remain later work. See [AGENTSIGHT_SEGMENT_V1_SPEC.md](docs/segments/AGENTSIGHT_SEGMENT_V1_SPEC.md).

## Recommended AI Flow

Do not request full-screen high-resolution images every time. Use AgentSight as an attention toolbox:

1. read discovery;
2. call `/screen` for coordinates, readiness, and `screen_frame_index`;
3. call `/look` for a low-cost full-screen or broad-region preview with `scale_down`;
4. visually choose a region, then use `view_id` or crop for a high-detail local image;
5. call `/do` for human-equivalent mouse/keyboard actions;
6. inspect post-action frames, diff, or receipts;
7. use `time.near` when you need frames around an approximate wall-clock time, or `/look q="changes"` / `/look q="diff" mode="timeline"` / `/look q="clip"` when you first need Segment metadata summaries, on-demand diff review, or a derived review clip.

## Runtime Direction

The current stage does not jump to a Windows Service or full installer. The target user-mode architecture is:

```text
Startup / Run Key
  -> AIControlSessionSupervisor
       -> AIControlHostAgent
       -> AIControlTrayGui
```

Source-mode development can use:

```powershell
$env:PYTHONPATH = "src"
py -m ai_control.session_supervisor run --host 127.0.0.1 --port 8765 --arm-real-input
```

Packaged mode is expected to use adjacent executables:

```text
AIControlSessionSupervisor.exe
AIControlHostAgent.exe
AIControlTrayGui.exe
AIControlInstaller.exe
```

Only the Supervisor should be registered as the long-term startup entry.

## Docs

- [User guide](docs/user-guide.en.md)
- [Release checklist](docs/release-checklist.en.md)
- [Repository profile](docs/repository-profile.en.md)
- [Screen / Look / Do protocol](docs/SCREEN_LOOK_DO_PROTOCOL.md)
- [Visual memory and attention vision](docs/visual-memory-and-attention.en.md)
- [Branding and workspace migration](docs/branding-and-workspace-migration.en.md)

## Development

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

Build the current PyInstaller packaged layout:

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

## Release Status

GitHub Actions runs on `v*` tags or manual dispatch, executes tests, builds Windows executables, generates SHA256 checksums, and uploads release artifacts. Release notes are Chinese-first with English notes appended or linked.


