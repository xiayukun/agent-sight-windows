# User Guide

[中文](user-guide.md) | English

## Who It Is For

AgentSight for Windows is for AI agents that need real Windows GUI control. It is not remote desktop, OCR software, or a hidden business API.

## First Use

Source mode:

```powershell
$env:PYTHONPATH = "src"
py -m ai_control.session_supervisor run --host 127.0.0.1 --port 8765 --arm-real-input
```

Packaged target:

```text
AIControlSessionSupervisor.exe run
```

## Public AI Flow

```text
read discovery -> /screen -> /look -> /do -> /look
```

- `/screen`: coordinates, readiness, and `screen_frame_index`.
- `/look`: low-cost full-screen or regional views with `scale_down`, region, and `view_id`.
- `/do`: human-equivalent input based on `view_id`.
- `/look time.near`: retrieve indexed frames around an approximate time.

## Tray Control Surface

Tray GUI is a human-visible control surface:

- ready / paused / emergency / blocked / discovery_missing / unknown status icon;
- context menu;
- pause AI control;
- allow AI control;
- emergency stop;
- open recording settings;
- open timeline;
- view operation log;
- stop AgentSight;
- language: Follow System, Chinese, English.

Language settings are stored in:

```text
%LOCALAPPDATA%\ai-control\tray-settings.json
```

Recording/timeline policy is stored in:

```text
%LOCALAPPDATA%\ai-control\tray-config.jsonc
```

`Capture & Retention Settings` opens a modern scrollable Windows settings dialog in the current tray language and saves changes to `tray-config.jsonc`. The config keeps only user-adjustable capture policy and retention days; idle capture is expressed as FPS with a minimum of 0.1 FPS (one frame per 10 seconds); `post_observe_defaults` is no longer a user setting, and `/do` post-action observation follows the configured post-action FPS, duration, and max-frame policy directly. Runtime Segment recording defaults to `.agseg` single-file binary storage with keyframes and P-frame delta crops; `.agseg` raw data, manifest, and hashes are canonical evidence. `Open Timeline` / `View Operation Log` launch the native PySide6/Qt `AgentSightTimelineViewer`, which reads `.agseg` indexes and the operation log, then decodes the selected frame in memory. It does not generate HTML, PNG, GIF, or review bundles by default. Qt previews and explicit look-preview caches are human-review derived artifacts and do not mean the tool judged target hit, causality, or business success.

## Evidence And Privacy

Evidence may contain real screen content. Redact before sharing or publishing.



