# Screen / Look / Do Public Protocol

更新时间：2026-06-20

定位：本文件是开发者协议说明，不是普通 AI 的默认操作手册。普通 AI 使用 AgentSight 时，应优先读取 `src/ai_control/adapters/skill/SKILL.md`，并遵循 `read discovery -> /screen -> /look -> /do -> /look` 的短链路。

## Goal

AgentSight for Windows is moving the ordinary AI-facing workflow from the historical
`observe -> mouse/input -> evidence/replay/integrity` surface to a smaller
public model:

```text
screen -> look -> do -> look
```

The old evidence, replay, integrity, observe, mouse, input, visual-memory, and
batch paths remain available internally for compatibility, diagnostics, and
audit export. They are no longer the recommended ordinary AI public flow.

## Installable Product Constraint

This protocol must be designed as the public surface of a future installable
Windows product, not as a source-tree-only demo.

Current allowed modes:

```text
source mode:
  py -m ai_control.session_supervisor run

packaged mode:
  AIControlSessionSupervisor.exe run

install mode:
  AIControlInstaller.exe
    -> registers AIControlSessionSupervisor for the current user
    -> starts AIControlSessionSupervisor.exe
    -> supervisor starts/monitors AIControlHostAgent.exe
    -> supervisor starts/monitors AIControlTrayGui.exe
    -> Host Agent exposes discovery_v2 and /screen /look /do
```

The only long-term self-start entry is `AIControlSessionSupervisor`. New public
protocol work must not add new recommended self-start entries for the Host
Agent watchdog or Tray GUI watchdog. Those split watchdogs are legacy
compatibility paths.

State, discovery, reports, stop markers, visual-store outputs, and evidence
remain under `%LOCALAPPDATA%\ai-control` or another explicit user data root.
Final users must not need to know the source checkout path, set
`PYTHONPATH=src`, or run `py -m ...`. Source mode may keep those conveniences
for development, but packaged/frozen mode must use adjacent executables in the
install directory.

This stage still does not introduce a Windows Service, SYSTEM process, Session
0 bridge, `WTSGetActiveConsoleSessionId`, `CreateProcessAsUser`, driver, or UAC
secure-desktop control. Those remain future optional service-layer work, not a
shortcut for the `screen/look/do` public protocol.

## Boundary

The protocol remains human-equivalent:

- pixels in;
- mouse/keyboard events out;
- no OCR;
- no clipboard;
- no DOM;
- no accessibility tree;
- no window semantics;
- no hidden application API;
- no command-line GUI substitute;
- no target-hit, causality, delivery, task, or business-success judgment.

## Visual Attention And Time Lookup

The public protocol should be used as an attention funnel, not as a constant
full-resolution livestream:

1. call `screen` to learn desktop coordinates and readiness; when capture is
   available, this also writes a `source="screen"` frame index entry without
   returning image bytes;
2. call `look` on a low-cost full-screen or broad region preview with
   `scale_down`;
3. narrow to a `view_id` or region;
4. call `look` again for a higher-detail crop;
5. call `do`;
6. inspect operation-aligned post frames or call `look` / `look q=diff`;
7. if the AI only knows an approximate moment, call `look q=frame` with
   `time.near` to retrieve indexed frames nearest that time.
8. if the AI first needs a cheap history summary, call `look q=changes` to
   query existing Segment metadata before exporting crops or diff artifacts;
9. when the AI needs a historical before/after diff for a time window, call
   `look q=diff mode=timeline` for metadata or
   `mode=timeline_with_artifacts` with a small `max_artifacts` for derived
   diff heatmaps;
10. when the AI needs a short review animation for a time window, call
    `look q=clip` with `max_artifacts=0` for metadata or `max_artifacts=1` for
    one derived review GIF.

Example approximate time lookup:

```json
{
  "v": "V1",
  "id": "look-around-2240",
  "q": "frame",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 1920, "h": 1080},
  "scale_down": 4,
  "time": {"near": "22:40:00"}
}
```

The response is metadata-first and capture-free: it returns nearest indexed
raw frame paths, requested/actual timestamps, `delta_ms`, before/after/nearest
relations, degenerate-frame flags, capture source, and evidence/receipt/replay
references when available. It does not perform OCR, read semantics, or judge
success. If the selected historical Segment frame must be visually inspected,
the public response returns a derived `decoded_review` / `historical_view`
metadata block plus transient MCP image content by default; it does not default
to writing a persistent `review_media_path_abs` PNG.
When that historical review image is returned, the response may also include a
`view_record` whose `view_id` equals `historical_view.id`. This record exists
for operation-log traceability and explicit on-demand preview regeneration. It
must be marked `view_role="historical_segment_review"` and
`view_is_current_action_basis=false`; callers must not use this historical
`view_id` as a `/do` action basis. Public `/do` must fail with
`VIEW_NOT_ACTION_BASIS` if such a review-only view is supplied.

Timeline diff example:

```json
{
  "v": "V1",
  "id": "look-window-diff",
  "q": "diff",
  "mode": "timeline_with_artifacts",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 800, "h": 500},
  "scale_down": 1,
  "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:05Z"},
  "max_artifacts": 1
}
```

Timeline diff is also capture-free. It queries existing Segment frames, maps
screen/view `r` through indexed `screen_region` metadata, returns pixel-change
metadata in `diffs`, and only exports derived review artifacts when requested
with `timeline_with_artifacts`. Missing Segment metadata, non-overlap, or
decode/export problems are reported as errors in the response. None of these
fields prove target hit, causality, delivery, task completion, or business
success.

Review clip example:

```json
{
  "v": "V1",
  "id": "look-window-clip",
  "q": "clip",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 800, "h": 500},
  "scale_down": 2,
  "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:10Z"},
  "max_frames": 32,
  "max_artifacts": 1
}
```

`look q=clip` is capture-free and Segment-backed. It returns selected frame
refs in `clip.frames`; when `max_artifacts=1`, it exports a single derived
review GIF. The GIF is a convenience visualization over variable-timestamped
Segment frames, not canonical evidence and not an exact real-time video. The
tool still does not judge target hit, causality, delivery, task completion, or
business success.

## Public Tools

The MCP public tool list is:

```text
screen
look
do
```

In code, this is `PUBLIC_COMMAND_ORDER`. The broader `COMMAND_ORDER` still
contains legacy/internal commands so older adapters and diagnostics do not have
to be deleted in the same slice.

## Discovery

Host Agent discovery stays at:

```text
%LOCALAPPDATA%\ai-control\host-agent.json
```

Current public shape:

```json
{
  "schema": "discovery_v2",
  "url": "http://127.0.0.1:38127",
  "token": "secret",
  "pid": 12345,
  "api": {
    "screen": "/screen",
    "look": "/look",
    "do": "/do"
  },
  "files": {
    "service_state": "%LOCALAPPDATA%/ai-control/service-state.json",
    "session_supervisor_state": "%LOCALAPPDATA%/ai-control/session-supervisor-state.json",
    "tray_config": "%LOCALAPPDATA%/ai-control/tray-config.jsonc"
  }
}
```

Legacy URL fields such as `observe_url`, `mouse_url`, and `input_url` may still
be present for old clients.

`health_url` may also be present for Tray GUI, Session Supervisor, installers,
and diagnostics. It is not part of the ordinary AI control chain.

## Embedded Readiness

The ordinary public chain is:

```text
read discovery -> /screen -> /look -> /do -> /look
```

Ordinary AI callers should not add a mandatory `/health` preflight. Instead,
each public `/screen`, `/look`, and `/do` response carries the same readiness
surface:

```json
{
  "code": "READY",
  "readiness": {
    "schema": "ai_control_public_readiness_v1",
    "ok": true,
    "code": "READY",
    "service_status": "ok_active_default_desktop",
    "can_attempt_real_control": true,
    "control_blockers": []
  }
}
```

Common blocked codes are `HOST_AGENT_NOT_ARMED`, `HOST_AGENT_NOT_READY`,
`DESKTOP_LOCKED`, `SECURE_DESKTOP_ACTIVE`, `UAC_SECURE_DESKTOP_ACTIVE`,
`OPERATOR_PAUSED`, `EMERGENCY_STOP_ACTIVE`, `DISCOVERY_STALE`,
`CAPTURE_UNAVAILABLE`, `INPUT_UNAVAILABLE`, and `NOT_IN_ACTIVE_SESSION`.

If the Host Agent is unreachable, the caller-side client should report
`HOST_AGENT_UNREACHABLE`. The server cannot return that code when no HTTP
response exists.

`/health` remains an internal/diagnostic endpoint. It is useful for lifecycle
components and debugging, but it should not lengthen the ordinary AI public
workflow.

## `screen`

Purpose: report the Windows virtual desktop coordinate space and monitor layout.

Request:

```json
{"v": "V1", "op": "screen"}
```

Return:

```json
{
  "v": "V1",
  "ok": true,
  "virtual": {"x": 0, "y": 0, "w": 2560, "h": 1440},
  "monitors": [{"id": "m1", "primary": true, "x": 0, "y": 0, "w": 2560, "h": 1440}]
}
```

`screen` is read-only layout plus embedded readiness. It is not a separate
health endpoint and is not a business-success or target-hit judgment.

## `look`

Purpose: get pixels from the current or cached visual stream and create a
`view_id` that can be used for further focus or action.

Current implementation:

- supports `q="frame"`;
- supports `q="diff"` for endpoint comparison against a prior view;
- supports `q="changes"` for metadata-only Segment change summaries;
- uses existing observation/capture internally;
- records a view index in memory;
- returns ordinary `q="frame"` pixels as transient MCP image content rather than default derived PNG files;
- requires `scale_down`;
- supports `src.type="screen"` and `src.type="view"`.

Screen look:

```json
{
  "v": "V1",
  "id": "look-1",
  "op": "look",
  "q": "frame",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 2500, "h": 1400},
  "scale_down": 5
}
```

View look:

```json
{
  "v": "V1",
  "id": "look-2",
  "op": "look",
  "q": "frame",
  "src": {"type": "view", "view_id": "v_ab12"},
  "r": {"x": 100, "y": 120, "w": 10, "h": 10},
  "scale_down": 1
}
```

When `src.type=screen`, `r` is in virtual screen pixels. When
`src.type=view`, `r` is in parent view image pixels. A view-sourced `r` must
fit within the parent view image, and the parent view must be a current-screen
view with a stored transform. Historical Segment review views fail with
`VIEW_NOT_CURRENT_SCREEN_BASIS`, missing transforms fail with
`VIEW_TRANSFORM_UNAVAILABLE`, missing dimensions fail with
`VIEW_DIMENSIONS_UNAVAILABLE`, and out-of-bounds regions fail with
`VIEW_REGION_OUT_OF_BOUNDS` instead of silent clamping or guessing. The internal
view index stores:

- `view_id`;
- `parent_view_id`;
- `screen_rect`;
- `source_rect_in_parent`;
- `scale_down`;
- source timestamp;
- source observation/frame reference;
- source Segment restore ref when available;
- requested virtual-screen region and actual decoded source-frame / Segment
  region as separate fields;
- output image size;
- `transform.view_pixels_to_virtual_screen_pixels`;
- `raw_or_derived="derived_review_only"`.

The returned image is not canonical evidence. Canonical replay is based on raw
captures / `.agseg` Segment data, operation logs, and view records. Timeline and
operation-log UIs should show look metadata by default and generate preview
images only on demand. If a UI must materialize a file to show a preview, that
file is cache / derived review only and can be regenerated.

Public operation-log entries for `/look` may include `look_preview_refs` with
schema `agentsight_look_preview_descriptor_v1`. A descriptor records the
`view_id`, source frame/Segment restore ref, `actual_decoded_region`,
`requested_screen_region`, output size, scale, blur, cursor mode, transform,
and cache policy. It intentionally does not include image bytes or a default
PNG path. The native Qt operation-log viewer renders these descriptors as
metadata. A separate explicit materialization step can decode the referenced
Segment into a temporary derived review cache image by operation-log index and
preview index; that cache is not canonical evidence and may be pruned or
regenerated. Materialization must use
`actual_decoded_region`, not `requested_screen_region`; if the decoded region
is missing, the preview request must be blocked rather than guessing a Segment
crop from screen coordinates.
Historical `time.near` reviews follow the same descriptor path when they return
a `view_record`, but those descriptors remain review-only and do not make the
historical view a valid `/do` basis.

The ordinary AI should use the returned `view.id`, not recompute the transform.

Change summary look:

```json
{
  "v": "V1",
  "id": "changes-1",
  "op": "look",
  "q": "changes",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 2500, "h": 1400},
  "scale_down": 1,
  "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:05:00Z"},
  "max_pairs": 128,
  "min_changed_pixel_ratio": 0.001
}
```

`look q="changes"` reads existing Segment files and returns metadata such as
`changed_pixel_ratio`, `changed_bbox`, adjacent frame refs, and `change_runs`.
It does not capture the current screen, does not export images, does not send
input, and does not judge target hit, causality, or business success. In the
current slice supports both `src.type="screen"` and `src.type="view"`. For
`screen`, `r` is a virtual screen region. For `view`, `r` is first mapped from
parent-view pixels through the stored view transform to a virtual screen
region; the response also returns that mapped `screen_region`. The change query
then maps the virtual screen region through indexed Segment `screen_region`
metadata when available. Frames without mappable screen metadata, mismatched
regions, or no overlap are skipped with decode errors rather than silently
reinterpreted as stored-frame coordinates. Callers must not treat this as a
live-screen observation.

## `do`

Purpose: execute a sequence of human-equivalent mouse/keyboard actions against a
view basis.

Example:

```json
{
  "v": "V1",
  "id": "act-1",
  "op": "do",
  "basis": {"view_id": "v_cd34"},
  "seq": [
    {"t": "move", "x": 25, "y": 25, "coord": "view", "move": "instant"},
    100,
    {"t": "click", "b": "left"}
  ]
}
```

Point basis shortcut:

```json
{
  "v": "V1",
  "id": "act-1-click-point",
  "op": "do",
  "basis": {"view_id": "v_cd34", "point": {"x": 25, "y": 25}},
  "seq": [
    {"t": "click", "b": "left"}
  ]
}
```

Rules:

- `basis` must be a view in the ordinary public flow.
- `move` is the only mouse action that accepts `x`/`y`.
- `click`, `dblclick`, mouse `down`, mouse `up`, and `wheel` use the current
  mouse position; move first.
- If `basis.point` is present, the host maps that view-local point through the
  stored view transform and uses it as the current mouse point. If the view or
  transform is missing, the request must fail rather than guessing coordinates.
- View-local points must stay inside the returned view image bounds. Missing
  dimensions, missing transforms, or out-of-bounds points fail with explicit
  blockers such as `VIEW_DIMENSIONS_UNAVAILABLE`,
  `VIEW_TRANSFORM_UNAVAILABLE`, or `VIEW_POINT_OUT_OF_BOUNDS`.
- `text` is keyboard input, not clipboard, file input, command execution, OCR,
  or semantic text API.
- `key`, `chord`, keyboard `down`, and keyboard `up` are explicit key events.
- numeric sequence entries are wait milliseconds.
- partial failures are reported as `status="partial"` and cannot roll back
  already-sent GUI input.

The response includes:

- status;
- start/end time;
- step list;
- input host event count;
- anchors;
- capture windows.

It does not include business success, target-hit, or causality claims.

### Optional `post_observe`

Use `post_observe` when the GUI may animate, debounce, or populate results after
the input event. The tool waits and samples the same view-backed screen region,
then returns structured pixel-change/stability metadata first.

```json
{
  "v": "V1",
  "id": "act-with-post-observe",
  "op": "do",
  "basis": {"view_id": "v_cd34"},
  "seq": [
    {"t": "move", "x": 25, "y": 25, "coord": "view", "move": "instant"},
    {"t": "click", "b": "left"}
  ],
  "post_observe": {
    "delay_ms": 300,
    "frame_count": 4,
    "interval_ms": 150,
    "stable_threshold": 0.001,
    "stable_frame_count": 2,
    "stop_when_stable": true
  }
}
```

Bounds:

- `delay_ms`: 0..5000;
- `frame_count`: 1..1000;
- `interval_ms`: 0..2000;
- `stable_threshold`: 0..1;
- `stable_frame_count`: 1..5;
- `stop_when_stable`: boolean, default `false`.

The returned `post_observe` block is metadata-only. It reports sampled frame
refs, comparison counts, changed pixel ratios, changed frame indexes, largest
changed bounding box, and whether recent post frames are stable by the requested
pixel threshold. It does not return raw image paths in the public payload, does
not create a new MCP tool, and does not assert target hit, semantic change,
causality, delivery, task completion, or business success. Raw frames remain the
canonical evidence in the Host Agent run directory; derived review artifacts are
separate.

When `stop_when_stable=true`, the tool may stop sampling before `frame_count`
only after the recent post-frame comparisons satisfy `stable_frame_count` and
`stable_threshold`. This is still bounded by `frame_count`; it is not an
unbounded wait, a livestream, OCR, UI semantics, or success detection. The
summary reports `stopped_early` and `sampling_stop_reason` such as
`stable_window_reached` or `max_frame_count_reached`.

Host Agent timing note: when a `do` request includes `post_observe`, the Host
Agent skips its legacy per-step after-observation capture and lets
`post_observe` be the first post-input sampling window. Per-step results expose
`post_observe_fast_path=true` and `after_observation_skipped=true`. Requests
without `post_observe` keep the legacy per-step after-observation behavior.

## Cursor Capture

Cursor inclusion is a tray/user configuration concern. New public `look`
requests do not expose per-request cursor flags. Raw frames/video remain
canonical evidence; cursor overlays and annotated images are derived review
artifacts only.

## Legacy Handling

Legacy/internal names:

- `observe`;
- `query_visual_memory`;
- `derive_candidates`;
- `create_lease`;
- `execute_input`;
- `run_limited_batch`;
- `get_evidence_package`;
- `read_replay`;
- `verify_integrity`;
- Host Agent `/observe`, `/click`, `/mouse`, `/input`.

These are retained for compatibility, diagnostics, and audit, but ordinary AI
Skill/MCP guidance should not use them as the main flow.

## Current Slice vs Later Work

Current implementation includes:

- protocol schema;
- MCP public surface;
- gateway `screen/look/do`;
- Host Agent discovery and `/screen` `/look` `/do` entrypoints;
- in-memory `view_id` mapping;
- chained view-to-screen coordinate conversion;
- `do` action array with move/click separation;
- optional `do.post_observe` bounded post-action frame sampling and metadata
  summary;
- `/look` MCP image content by default, with traceable view records instead of
  default persistent PNG exports;
- `time.near` indexed-frame review through `/look`;
- `.agseg` Segment recording with keyframe + P-frame delta crop storage;
- operation-log entries that omit transient MCP image payloads and keep
  regenerable preview refs;
- timeline/log derived review viewers for human review;
- tray capture and retention settings for idle FPS, action pre/post capture,
  post-action FPS/duration/max frames, retention days, daily boundary, and
  Segment bucket granularity.

Later slices should implement:

- longer-running visual-memory ring buffer retention and pruning hardening;
- older web review surfaces; current tray launches a native PySide6/Qt timeline/log viewer;
- richer region-change indexes and metadata-first queries through the public
  `/look` facade;
- broader real-host functional testing guided by the Skill, with external AI
  semantics kept outside AgentSight.


