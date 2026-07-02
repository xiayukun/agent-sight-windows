---
name: agentsight
description: Use this skill when an AI agent needs to inspect AgentSight for Windows readiness and control a Windows GUI through the public /screen, /look, and /do pixel-and-input protocol.
---

# AgentSight for Windows

AgentSight for Windows is a pixel-grounded observe-and-act host for Windows AI agents. It lets an AI read visible pixels and send human-equivalent mouse/keyboard events through a local Host Agent, while keeping UI interpretation outside the tool.

Use the current public flow:

```text
read discovery -> /screen -> /look -> /do -> /look
```

The public GUI control endpoints are `/screen`, `/look`, and `/do`. Each public response includes embedded readiness/status fields, so ordinary AI callers do not need a separate `/health` preflight before GUI work.
The short GUI-control subflow is `screen -> look -> do -> look`.

Entry selection:

1. If AgentSight MCP public tools `screen`, `look`, and `do` are available in
   your environment, prefer those tools. The MCP adapter handles discovery,
   auth, and caller identity. Do not call legacy MCP tools.
2. If MCP tools are not available and you are making direct Host Agent HTTP
   calls, read discovery and call only `api.screen`, `api.look`, and `api.do`.
3. Do not mix MCP and direct HTTP in one attempt unless debugging is explicitly
   requested by the operator.

Use the installed/running Host Agent first. Ordinary AI GUI work should not
depend on the source checkout, `PYTHONPATH=src`, `py -m ...`, or temporary
terminal startup commands. Source commands, first-use doctor, capture smoke, and
installer commands are for development, installation, diagnostics, or human
maintenance only.

## Product Boundary

AgentSight may:

- report visible screen pixels and view metadata;
- report readiness/blockers;
- send real mouse/keyboard-style input when the Host Agent is ready and authorized;
- record paths, event counts, timing facts, and pixel-change facts.

AgentSight must not:

- OCR text;
- read or write clipboard;
- read DOM;
- read accessibility tree;
- read window semantics;
- call hidden application APIs;
- use shell/cmd as a GUI substitute;
- judge business success;
- judge causality;
- judge that a target was hit.

All UI interpretation remains the caller AI's external visual judgment over returned pixels.
The tool does not OCR. The caller AI may visually inspect returned images,
including visible text-like pixels, but must describe that as its own external
visual review, not as AgentSight returning text or UI semantics.

## Host Readiness And Diagnostics

When the host is new or capture/input status is unknown, treat diagnostics as
environment confirmation, not as the ordinary GUI-control workflow.

Ordinary priority:

1. Read `%LOCALAPPDATA%\AgentSight\host-agent.json`.
2. Use its `url`, bearer `token`, and `api.screen` / `api.look` / `api.do`.
3. Call `/screen` and read embedded readiness.
4. If `/screen` reports a readiness blocker, stop and report that blocker.
5. Only run first-use doctor, capture smoke, release readiness, installers, or
   source-mode commands when the operator or developer explicitly asks for
   diagnostics, installation, or maintenance.

Diagnostic reports describe current host capability status only. They do not
prove that any GUI task succeeded.

## Blocked Control Quick Path

If discovery is missing/stale, `/screen` is unreachable, or any public response
reports `OPERATOR_PAUSED`, `EMERGENCY_STOP_ACTIVE`, `DISCOVERY_STALE`,
`HOST_AGENT_UNREACHABLE`, caller lock, HTTP `401/403/409/423`, or any readiness
blocker:

- stop ordinary GUI work immediately;
- do not attempt `/look` or `/do` after a blocker that prevents readiness;
- do not click Tray controls to allow, clear emergency stop, start, stop, or
  otherwise restore AgentSight for yourself;
- do not call `/health`, legacy/internal routes, shell commands, installers, or
  source-mode commands unless the operator explicitly asks for diagnostics or
  maintenance;
- report the blocker, endpoint attempted, HTTP status if any, and whether input
  was sent;
- ask the human operator or maintainer to restore readiness when required.

After a blocker clears, retry only through the public chain and the same stable
caller identity. Do not operate Tray to repair the human control surface unless
the operator explicitly asks for Tray maintenance.

## Visual Memory And Attention Discipline

Do not default to "full-screen high-resolution screenshot every time." Use the tool as an attention system:

1. Start with `/screen` and a coarse `/look` over the full desktop, usually with `scale_down`.
2. Select a region or `view_id` by your own visual reasoning.
3. Request a high-detail crop only for that region.
4. After `/do`, inspect returned post-observe anchors or call `/look` for the relevant region.
5. When you need to inspect a moment by approximate wall-clock time, use `/look q=frame` with `time.near`.

When capture is available, `/screen` also writes a `screen_frame_index` metadata entry for the visual-memory timeline. Treat it as an indexed raw frame reference, not as tool-side UI interpretation.

`scale_down`, region/crop, and `view_id` are attention controls. Time lookup returns indexed raw frame paths and timing facts; it does not infer what the UI means or whether an action succeeded.

## Coordinate Discipline

Keep these coordinate spaces separate:

- `/screen` reports virtual desktop pixels. Monitor origins may be negative.
- `/look src.type="screen"` takes `r` in virtual desktop pixels.
- `/look src.type="view"` takes `r` in the parent view image pixels you received, after that parent view's `scale_down`.
- `/do basis.view_id` takes mouse `move x/y` in that basis view image pixels. `/do basis.point` can also provide one view-local point up front, letting a click use that point without a separate move step.

Do not add monitor origins or multiply by `scale_down` when using a returned
`view.id` as the next `/look` source or `/do` basis. The Host Agent stores the
view-to-screen mapping and applies the scale internally.
View-local points must stay inside the returned view image bounds. If a point
is outside the view or the transform/dimensions are missing, `/do` must fail
with a blocker such as `VIEW_POINT_OUT_OF_BOUNDS`,
`VIEW_TRANSFORM_UNAVAILABLE`, or `VIEW_DIMENSIONS_UNAVAILABLE`; do not guess a
screen coordinate.
Likewise, `/look src.type="view"` requires a current-screen parent view with a
stored transform, and the requested region must stay inside that parent view
image. Historical Segment review views are not current screen bases and fail
with `VIEW_NOT_CURRENT_SCREEN_BASIS`; missing transform fails with
`VIEW_TRANSFORM_UNAVAILABLE`; out-of-bounds regions fail with
`VIEW_REGION_OUT_OF_BOUNDS` rather than silently expanding or guessing.

Example: if a full virtual-desktop `/look` uses `scale_down=4`, and the target
region appears at `x=80,y=50,w=100,h=40` in the returned image, the next
focused `/look src=view` should use exactly that parent-view rectangle. If the
focused crop is `scale_down=1` and the click point is `x=40,y=20` inside that
crop, `/do` should either move to exactly `x=40,y=20` with `coord="view"` or
set `"basis": {"view_id": "...", "point": {"x": 40, "y": 20}}` before a click.

For multi-monitor desktops, first use `/screen` bounds for the initial
`src=screen` capture. If the virtual desktop starts at `x=-1280`, a full
desktop look should include that negative origin rather than silently capturing
only the primary monitor.

## 1. Discovery

Read the Host Agent discovery file:

```text
%LOCALAPPDATA%\AgentSight\host-agent.json
```

Expected current shape:

```json
{
  "schema": "discovery_v2",
  "url": "http://127.0.0.1:8765",
  "token": "secret",
  "api": {
    "screen": "/screen",
    "look": "/look",
    "do": "/do"
  },
  "health_url": "http://127.0.0.1:8765/health"
}
```

Use the bearer token from discovery for Host Agent HTTP calls. For direct Host
Agent HTTP calls, send the same stable caller identity in `X-AgentSight-Caller`
for the whole task or conversation. Do not generate a new caller id on each
request, do not impersonate another caller, and do not put `caller_id` into
public `/look` or `/do` JSON bodies.

Direct HTTP header shape:

```text
Authorization: Bearer <token from discovery>
X-AgentSight-Caller: <stable caller id>
```

`token`, `Authorization`, and `caller_id` in the JSON body do not replace these
headers.

Ordinary callers should ignore `health_url` unless the operator explicitly asks
for diagnostics or maintenance.

Discovery may contain legacy URLs, startup health snapshots, installer facts,
or compatibility fields. For ordinary GUI work, extract only `url`, `token`,
and `api.screen` / `api.look` / `api.do`; treat the rest as diagnostic context,
not as extra tools.

If discovery is missing or stale, report that AgentSight is not ready. Do not bypass it with shell commands, clipboard, OCR, DOM, accessibility, window APIs, or hidden application APIs.

## 2. Embedded Readiness

Do not add `/health` as a required ordinary-AI step before GUI work.

Every `/screen`, `/look`, and `/do` response includes:

- top-level `code`;
- `readiness.schema="agentsight_public_readiness_v1"`;
- `readiness.ok`;
- `readiness.code`;
- `readiness.service_status`;
- `readiness.can_attempt_real_control`;
- `readiness.control_blockers`;
- `host_input_sent` / `host_sent_event_count` where relevant.

Common readiness codes include:

- `READY`;
- `HOST_AGENT_NOT_ARMED`;
- `HOST_AGENT_NOT_READY`;
- `DESKTOP_LOCKED`;
- `SECURE_DESKTOP_ACTIVE`;
- `UAC_SECURE_DESKTOP_ACTIVE`;
- `OPERATOR_PAUSED`;
- `EMERGENCY_STOP_ACTIVE`;
- `DISCOVERY_STALE`;
- `CAPTURE_UNAVAILABLE`;
- `INPUT_UNAVAILABLE`;
- `NOT_IN_ACTIVE_SESSION`.

Caller-lock responses may arrive as HTTP `423` with a `caller_lock.failure_code`
such as `CALLER_LOCK_HELD_BY_OTHER_AI`. Treat HTTP `409` / `423` as blockers
unless fresh public readiness data proves otherwise.

If the Host Agent process is not reachable at all, the caller-side client should report `HOST_AGENT_UNREACHABLE`; the server cannot return that code because there is no HTTP response.

Rules:

- Treat `discovery.health_url` and `/health` as internal/diagnostic readiness surfaces for Tray GUI, Supervisor, installers, and debugging, not as the ordinary public GUI-control chain.
- If any public response has `readiness.ok=false`, stop and report `readiness.code` and blockers.
- Locked desktops, UAC/secure desktop, emergency stop, operator pause, stale discovery, and caller-lock conflicts are blockers, not reasons to invent a background bypass.
- Read-only calls such as `/screen` may still be blocked by caller lock or
  control-plane policy.

## 3. Screen

Use `/screen` to read the virtual desktop coordinate system and monitor layout.

```json
{"v":"V1","id":"screen-1","op":"screen"}
```

`/screen` is read-only from the caller's perspective. It returns virtual screen dimensions and monitor facts, including possible negative coordinates when a monitor is left of or above the primary display. When capture is available, it also writes a `screen_frame_index` entry for the visual-memory timeline, but it does not return image bytes or judge UI meaning. It does not send input, OCR, inspect window semantics, read clipboard, or call hidden APIs.

## 4. Look

Use `/look` to capture pixels. Always provide `scale_down`.

Coarse screen look:

```json
{
  "v": "V1",
  "id": "look-1",
  "op": "look",
  "q": "frame",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 1920, "h": 1080},
  "scale_down": 4
}
```

Focused look from a prior view:

```json
{
  "v": "V1",
  "id": "look-2",
  "op": "look",
  "q": "frame",
  "src": {"type": "view", "view_id": "v_ab12"},
  "r": {"x": 120, "y": 80, "w": 300, "h": 180},
  "scale_down": 1
}
```

Rules:

- `src.type=screen` / `src.type="screen"` means `r` is in virtual screen pixels.
- `src.type=view` / `src.type="view"` means `r` is in parent view pixels.
- The Host Agent stores the view-to-screen mapping. Use returned `view.id` as the basis for later `look` and `do` calls.
- Ordinary public `look q="frame"` returns the pixels as transient MCP image content, not as a default derived PNG file on disk.
- `look` returns `view.id`, dimensions, scale, and `view_record` mapping facts. The `view_record` is the traceable index used for child looks, `/do` coordinate conversion, and future on-demand previews.
- In `view_record`, `requested_screen_region` is in virtual screen pixels; `actual_decoded_region` is in the source frame / Segment stored-frame pixels used to regenerate an on-demand preview. Do not mix these coordinate spaces.
- Canonical storage is the MKV VFR Segment (`*.mkv`) plus `.frames.jsonl` frame index, operation log, and view records. Ordinary public `screen` / `look` / `do` captures should flow from memory frame payloads directly into MKV; they should not create `session-*/media/*.png|*.bmp` or legacy evidence objects by default. The returned image content is a derived review image for this response only.
- Operation logs may include `look_preview_refs` for `/look` calls. These are metadata descriptors only: `view_id`, source Segment restore ref, region, scale, blur, cursor mode, and transform facts. They are not embedded images and are not proof of UI meaning.
- Current local timeline/log viewers do not load every AI `/look` crop by default. If a human explicitly asks to see the image an AI saw, tooling can regenerate a derived review cache image from the Segment restore ref and `actual_decoded_region` by explicit log/preview index. That cache is temporary/regenerable and not canonical evidence. If `actual_decoded_region` is missing, do not fall back to `requested_screen_region`; report that the preview cannot be materialized.
- Do not assume requested `r.w` / `r.h` equals returned `view.w` / `view.h`.
  The returned view dimensions are authoritative, especially after parent-view
  mapping or scaling. Use coordinates in the returned image you actually
  inspected.
- A `view_id` preserves mapping to a screen region, not a guarantee that the
  same application still occupies that region. Opening review images, switching
  focus, taskbar clicks, notifications, or another window can cover the target.
  Before acting from a view after any foreground change, fresh `/look` and
  verify the target app/region is still visible.
- `look q="diff"` compares pixels and returns pixel-change facts such as changed count/ratio and changed bbox. Optional diff heatmaps are derived review artifacts only.
- `look` does not return OCR, UI labels, button state, target hit, causality, or business success.
- Do not ask for per-request cursor capture in the public flow unless the schema explicitly exposes it. Raw evidence and derived review artifacts must stay distinct.

For ordinary endpoint diffs, use a prior `view_id` as the baseline:

```json
{
  "v": "V1",
  "id": "look-diff-1",
  "op": "look",
  "q": "diff",
  "src": {"type": "view", "view_id": "v_ab12"},
  "r": {"x": 0, "y": 0, "w": 300, "h": 180},
  "scale_down": 1,
  "max_artifacts": 1
}
```

This compares the prior view region against latest pixels for the same mapped
screen region. `summary.changed`, `changed_pixel_ratio`, or a diff heatmap only
prove pixel differences. They do not prove the input caused the change, the
target was hit, or the business task succeeded. Diff heatmaps are derived
review images, not canonical raw evidence.

For historical Segment diffs, use timeline mode. This reads existing `.agseg`
/ legacy Segment frames only; it does not capture the live screen:

```json
{
  "v": "V1",
  "id": "look-diff-timeline-1",
  "op": "look",
  "q": "diff",
  "mode": "timeline_with_artifacts",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 800, "h": 500},
  "scale_down": 1,
  "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:05Z"},
  "max_artifacts": 1
}
```

Use `mode="timeline"` when you only want metadata. Use
`mode="timeline_with_artifacts"` with a small `max_artifacts` when you need a
derived diff heatmap for review. `src.type="view"` is also allowed; then `r` is
first mapped through the parent view into virtual screen pixels and then into
indexed Segment frame coordinates. Missing Segment coordinate metadata,
non-overlap, or decode failures are reported as decode/artifact errors rather
than treated as target hit or success.

For a short historical review animation, use `look q="clip"`. This also reads
existing Segment frames only:

```json
{
  "v": "V1",
  "id": "look-clip-1",
  "op": "look",
  "q": "clip",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 800, "h": 500},
  "scale_down": 2,
  "time": {"from": "2026-06-21T10:00:00Z", "to": "2026-06-21T10:00:10Z"},
  "max_frames": 32,
  "max_artifacts": 1
}
```

Use `max_artifacts=0` for frame refs / metadata only. Use `max_artifacts=1`
when you need one derived review GIF. The GIF is a convenience animation over
variable-timestamped Segment frames; it is not canonical evidence, does not
preserve exact real-time cadence, and does not prove target hit, causality, or
business success.

### Time-near lookup

When you only know an approximate time such as "around 22:40", do not assume
you can hit an exact frame timestamp. After `/screen` readiness is OK, query
the existing frame index:

```json
{
  "v": "V1",
  "id": "look-near-time-1",
  "op": "look",
  "q": "frame",
  "src": {"type": "screen", "t": "latest"},
  "r": {"x": 0, "y": 0, "w": 1920, "h": 1080},
  "scale_down": 4,
  "time": {"near": "22:40:00"}
}
```

This is metadata-first review only when the response explicitly returns
near-time fields such as requested time, actual frame time, `delta_ms`,
before/after/nearest relation, `raw_media_path_abs`, `cursor_mode`,
`raw_or_derived`, `capture_content_degenerate`, capture source, and
evidence/receipt/replay references. Public adapters may also return
`decoded_review` / `historical_view` metadata plus transient MCP image content
generated from `.agseg` when no in-memory frame satisfies the query; that image
is a derived historical review artifact, not a current action basis, and it is
not a default persistent PNG or canonical evidence.
When a historical Segment review image is returned, the response may also
include a `view_record` whose `view_id` matches `historical_view.id`. Use that
record for operation-log traceability and on-demand preview regeneration only.
It must have `view_role="historical_segment_review"` and
`view_is_current_action_basis=false`; do not pass that historical `view_id` to
`/do` as an action basis. The Host Agent must reject that misuse with
`VIEW_NOT_ACTION_BASIS` instead of guessing current-screen coordinates.

For `.agseg` frames that carry `screen_region` and a mappable screen-pixel
`coordinate_system` such as `virtual_screen_pixels` or Windows capture
`monitor_pixels`, the request `r` is interpreted as a screen region and mapped
into the stored historical frame crop. The response should expose this with
`r.unit="virtual_screen_px"` plus
`decoded_review.decode_region_basis` and
`decoded_review.requested_screen_region`; `decode_region_basis` also reports
the source coordinate system. For older frames or legacy data that lack this
metadata, the Host Agent must fall back to `r.unit="stored_frame_px"` and
return a caveat; do not treat that fallback as live virtual-screen coordinates.
`time.near` should not take a fresh screenshot.

If a nearby Segment frame cannot be decoded, the public adapter may try another
nearby candidate. Check `decode_errors` for skipped frames and
`decoded_review.selected_segment_frame` for the frame actually returned. This
fallback is only evidence handling; it is not success, causality, or target-hit
judgment.
If `decode_errors` includes `status="decode_skipped_no_overlap"`, the requested
screen region did not intersect that historical frame's recorded
`screen_region`; do not reinterpret it as a stored-frame crop or live
screen coordinate. The adapter may continue to another nearby candidate, but
if every candidate is skipped this way, report that no usable historical review
frame was found for the requested region.
Also check `frames_near_time.skipped_segment_count` and
`frames_near_time.skipped_segments`. A nonzero count means one or more Segment
files/manifests could not be indexed for this review, so the time lookup is
partial even if a nearby frame was returned.
If the response looks like an ordinary latest `/look` result and does not
include near-time metadata, treat `time.near` as unsupported or ignored for
that request; do not use it as review evidence. If no nearby indexed frame
exists, report not found. Finding a nearby frame is not task success.

Use these rules for approximate-time review:

- Prefer raw, non-degenerate frames for visual observation.
- Treat `raw_or_derived!="raw"` as review-only context, not canonical evidence.
- Treat `capture_content_degenerate=true` as "frame exists but is not a usable
  visual reference."
- Inspect before/after/nearest metadata; do not assume nearest is best if it is
  derived, degenerate, or far from the requested time.
- If no `/do` receipt or `post_observe` covers the action window, do not claim
  target hit, causality, submit success, delivery, task completion, or business
  success.
- If only a time such as `22:40:00` is provided, state the date/timezone/session
  assumption you used. If ambiguous, report that ambiguity rather than
  pretending the time is exact.

Safe wording:

```text
I found nearby indexed frames at ...
Usable raw evidence: ...
Derived or degenerate review-only evidence: ...
No receipt/post_observe covers the requested action window.
I cannot confirm success or causality; I can only report these visual/protocol facts.
```

## 5. Do

Use `/do` to send human-equivalent input. The basis must be a view returned by `/look`.

```json
{
  "v": "V1",
  "id": "act-1",
  "op": "do",
  "basis": {"view_id": "v_ab12"},
  "seq": [
    {"t": "move", "x": 25, "y": 25, "coord": "view", "move": "instant"},
    100,
    {"t": "click", "b": "left"}
  ]
}
```

Rules:

- Do not send naked screen coordinates in ordinary use.
- `move` is the only mouse step that accepts `x`/`y`.
- `click`, `dblclick`, mouse `down`, mouse `up`, and `wheel` use the current mouse position; move first.
- If `basis.point` is present, the tool converts that view-local point through the stored `view_record.transform` and treats it as the current mouse point for the first click-like step.
- `text` sends real keyboard events. It is not clipboard, paste, file input, command execution, OCR, window text, or semantic text input.
- `key`, `chord`, keyboard `down`, and keyboard `up` are explicit keyboard events.
- Numbers in `seq` are waits in milliseconds.
- If a step fails after earlier input, the tool reports `status=partial`. It cannot roll back real GUI input.
- Read event counts as input accounting only. They do not prove target hit or business success.
- For `/do`, prefer the operation's own `status`, `input.sent`,
  `input.host_event_count`, step results, and receipt/evidence refs when
  reporting input facts. Embedded readiness fields are readiness facts for that
  response and do not replace the `/do` input accounting.

### Partial input before submit

Treat `status=partial` as a stop sign, especially after text input and before
any commit/send/submit action.

Decision rule:

1. Stop the current multi-step plan. Do not continue with the remaining steps.
2. Report which step failed if available, `host_input_sent`,
   `host_sent_event_count`, receipt/evidence paths, and any post-observe facts.
3. Do not press `Enter`, reuse old coordinates, replay the same `/do`, or click
   a commit/send/submit target from the stale view.
4. If readiness is still OK, a fresh `/look` may be used only to observe the
   current pixels and gather evidence.
5. If the field content, focus, overlay state, or commit target is visually
   uncertain, stop and report uncertainty. Do not use OCR, clipboard, DOM,
   accessibility, window text, shell commands, or hidden APIs to repair it.
6. A follow-up `/do` for commit/send/submit is allowed only when the operator
   explicitly wants continuation and fresh pixels show the target is
   unobstructed. Use a new current `view_id` and bounded `post_observe`.

Safe wording:

```text
Partial real input occurred. The tool cannot roll it back.
I can report sent-event counts, failed step metadata, and current pixels.
I cannot confirm the text was fully replaced, the submit target was hit, the
message was delivered, or the task succeeded.
```

### Replacing text in a field

Do not trust existing keyboard focus. Use this conservative recipe:

1. Fresh `/look`, then focused crop of the intended field.
2. If the field is obscured, too small, or confusable with nearby highlighted
   UI, stop and report uncertainty.
3. `/do`: click inside the field, wait briefly, then use human-equivalent keys
   such as `Ctrl+A` before `text`.
4. Add `post_observe`, then inspect fresh pixels for the field region.

Example sequence:

```json
[
  {"t": "move", "x": 80, "y": 20, "coord": "view"},
  {"t": "click", "b": "left"},
  100,
  {"t": "chord", "modifiers": ["CTRL"], "key": "A"},
  100,
  {"t": "text", "text": "replacement text"}
]
```

Prefer AgentSight `text` for ordinary typing. If an external,
operator-approved tool outside AgentSight has already prepared known clipboard
contents, you may use AgentSight only to press the human-equivalent paste chord
such as `Ctrl+V`. In that case, report that clipboard preparation happened
outside AgentSight; AgentSight did not read or write clipboard and only sent
the keyboard chord. Do not paste unknown clipboard content. Do not use OCR,
DOM, accessibility, window text APIs, file input, or command-line shortcuts as
if they were AgentSight capabilities.

For CJK, emoji, IME, or other composed/non-ASCII text, report uncertainty
unless post-look pixels visibly support the result. A `partial` status or
unexpected candidate/composition UI means you should stop and report the
protocol facts; do not keep appending corrective text.

### Combining AgentSight With External Capabilities

AgentSight is only the pixel-and-human-input window. A capable caller AI may
have other environment tools, such as its own visual model, normal computer-use
abilities, or external window-inspection helpers. These can be useful, but keep
the accounting separate:

- Use AgentSight `/screen` and `/look` as the pixel truth for what the tool saw.
- Use your external visual or semantic reasoning to decide where to focus, but
  do not say AgentSight OCRed text, read a window title, inspected DOM, or used
  accessibility.
- If an external tool reports window semantics, compare that claim against
  AgentSight pixels before sending input. Prefer the pixel view when they
  disagree.
- If an external shell or command prepares data, disclose it as external
  preparation. Do not use shell/cmd as a substitute for the GUI action the
  operator asked AgentSight to perform.
- If an external tool prepares clipboard contents, AgentSight may only press
  the human paste chord; it still does not read or write clipboard. Never paste
  unknown clipboard content.
- If an external computer-use tool moves windows or changes app state, say so
  separately and do not count it as AgentSight host input.
- Never use external DOM, accessibility, hidden app APIs, or business APIs to
  claim that an AgentSight GUI action succeeded.

Useful human-equivalent shortcuts through `/do`:

- `Win+E` can open File Explorer.
- `Win+D` can show the desktop.
- `Ctrl+L` can focus a visible address/search field in apps where that is a
  normal user shortcut.
- `Alt+Tab`, `Esc`, `Enter`, arrow keys, `Tab`, `Shift+Tab`, `Ctrl+A`, and
  `Ctrl+V` are keyboard chords when sent by AgentSight. Use them only when they
  are visible, authorized, and appropriate for the task.
- For long text, split it into several bounded `text` steps inside one `/do`
  sequence when the protocol or host has text-length limits. After the sequence,
  use `/look` or bounded `post_observe`; do not assume the full text landed.
- For repeated clicking, one `/do` may include multiple `move` + `click` pairs
  when all target positions are based on the same fresh view and are expected to
  remain stable. If the UI can animate, scroll, reflow, or open overlays between
  clicks, split the work and take a fresh `/look`.

## 6. Post-Observe For Animation

When the UI may animate, load slowly, or settle after input, add bounded `post_observe` to the same `/do` request. If you omit `post_observe`, the installed Host Agent applies the operator's tray action-capture policy when operation capture and post-action frames are enabled. With the current default policy, 1 FPS for 10 seconds produces 10 requested post-action frames. Treat `recording_policy.applied_default_post_observe=true` as a protocol fact only; it is not a success or causality claim.

```json
{
  "v": "V1",
  "id": "act-2",
  "op": "do",
  "basis": {"view_id": "v_ab12"},
  "seq": [
    {"t": "move", "x": 25, "y": 25, "coord": "view"},
    {"t": "click", "b": "left"}
  ],
  "post_observe": {
    "delay_ms": 300,
    "frame_count": 6,
    "interval_ms": 150,
    "stable_threshold": 0.001,
    "stable_frame_count": 2,
    "stop_when_stable": true
  }
}
```

`post_observe` returns metadata over the same view-backed screen region:

- sampled frame refs;
- segment refs such as `segment_frame`, `segment_frame_id`, and
  `segment_restore_ref` when Segment recording is available;
- changed frame indexes;
- changed pixel ratio;
- changed bbox;
- simple stability status;
- bounded early-stop facts.

It is not livestreaming, OCR, semantic change detection, target-hit proof, causality proof, or business-success proof. `stop_when_stable=true` is still bounded by `frame_count`.

Current public schema accepts bounded `post_observe.frame_count` values from 1
through 1000. For longer windows, prefer a larger `interval_ms`, a delayed
follow-up `/look`, or an explicit near-time frame query if that response returns
the expected near-time metadata.

After reading `post_observe`, call `/look` again if you need fresh pixels, a crop, or a diff.

When Segment recording is available, Host Agent visual observe and public
`/look` may also expose a top-level `segment_frame`. Treat it as a restore
reference to canonical pixel evidence, not as proof that the UI target was hit
or that the task succeeded.

Prefer `post_observe` when menus animate, pages load with delay, clicked state
changes gradually, or you need several frames around an input event. Use the
returned frame/view facts to decide whether another `/look` is needed.

Delayed overlays are common. For autocomplete, search suggestions, context
menus, dropdowns, and toast-like UI, do not click the next target after only one
immediate after frame. Use bounded `post_observe` that covers the expected
popup delay, then inspect fresh pixels for the target region. Pixel stability
only means pixels stabilized; it does not mean the target is uncovered,
enabled, hit, or safe. If a popup-like visual layer appears over the target,
use only human-equivalent input such as wait, `Escape`, or clicking a visually
safe blank area, then `/look` again before clicking the target.

Autocomplete/search suggestion pattern:

1. `/look` a region that includes both the input field and the area where
   suggestions may appear.
2. `/do` click the field, use `Ctrl+A` if replacing text, then send `text`.
   Add `post_observe` long enough to cover the expected popup delay.
3. Treat post-observe as change/stability metadata. Call a fresh `/look` over
   the input and suggestions region before choosing a suggestion.
4. Use the fresh suggestion-list `view_id` for the click. Do not click a
   predicted suggestion position from the old pre-input view.

Dropdown/menu pattern:

1. Crop a region large enough to include the control, likely menu direction,
   and the next target you may need after the menu closes.
2. Open the dropdown/menu with human-equivalent input.
3. If the menu is not visible, is clipped by the crop, or appears unstable,
   do not use old coordinates. Rebuild the basis with a fresh `/look` from the
   current screen over a larger region.
4. Select from the fresh menu pixels, then `/look` again to confirm the control
   value or menu-closed state before clicking a follow-up Apply/Save target.

Destructive modal pattern:

1. Open the modal with a current `view_id`, preferably with bounded
   `post_observe`.
2. After the overlay/modal appears, discard the old background basis and call
   fresh `/look` over the modal.
3. Crop until the safe action and destructive action are visually distinct.
4. If the safe action is not clearly separated from the red/destructive action,
   stop and report uncertainty. Never click a destructive modal from stale
   coordinates.

If the possible overlay covers a destructive or committing target, prefer to
stop and report uncertainty. Retry only after fresh pixels show the target
region is visually unobstructed.

When entering text into a field, remember that `text` sends keyboard events at
the current caret. It may append to existing contents. If replacement is
required, first obtain fresh pixels and use human-equivalent keys such as
`Ctrl+A` or `Backspace`; do not use clipboard, file input, DOM, accessibility,
or window text APIs.

## 7. Dense Pages, Scrolling, And Built-In Search

For dense tables, dashboards, long pages, side drawers, and repeated similar
buttons, use an attention ladder instead of repeated full-resolution desktop
captures:

1. Coarse full-screen `/look` to locate major regions.
2. Focused crop of the relevant panel or table.
3. Fresh crop after every scroll, drag, tab switch, drawer open, or text input.
4. `/do` only against a current `view_id`; do not reuse old coordinates after
   the content moved.
5. Post-observe or a follow-up `/look` after each input that may move content.

For scrollable tables or panes, distinguish page scrolling from inner-container
scrolling by observing whether the target pane rows change while the outer page
layout stays put. If wheel input is too coarse, inconsistent, or affects the
wrong surface, use visible scrollbar-thumb dragging as human-equivalent input,
then fresh `/look` before clicking any row action.

Using an application's visible search field, filter field, or `Ctrl+F` is
allowed only as human-equivalent keyboard/mouse input. It is not allowed to
read DOM, accessibility, clipboard, window text, or hidden APIs. Always verify
the resulting pixels before acting on a row, button, or drawer.

Examples of human-equivalent navigation steps:

```json
{"t": "chord", "modifiers": ["CTRL"], "key": "A"}
```

```json
{"t": "key", "key": "PAGE_DOWN"}
```

```json
{"t": "wheel", "dy": -600}
```

Other human-equivalent shortcuts are allowed when they are visible user actions:

- `Win+E` to open File Explorer;
- `Win+D` to show the desktop;
- `Alt+Tab` to switch applications when the target is visually selected;
- `Alt+F4` to close the foreground window;
- `Ctrl+L` or an application's visible search/address shortcut when that is a
  normal user path.

Always take a fresh `/look` after these shortcuts before acting on the new
screen state.

For sliders and draggable controls, use a focused crop that includes the
handle, track, current value feedback, and any Save/Apply target. Drag from the
current handle position toward the desired range, then fresh `/look` to inspect
the new value before saving. If the value is still outside the acceptable
range, adjust from the new `view_id`; do not claim success from drag events
alone.

For small controls such as checkboxes, tabs, and compact buttons, crop with
enough margin to include the label, current state, neighboring controls, and the
target boundary. Click conservatively inside the visible control or label area,
then fresh `/look` before using the changed state.

Drag, such as moving a scrollbar thumb, is a sequence:

```json
[
  {"t": "move", "x": 40, "y": 180, "coord": "view"},
  {"t": "down", "b": "left"},
  {"t": "move", "x": 180, "y": 180, "coord": "view", "move": "linear", "ms": 300},
  {"t": "up", "b": "left"}
]
```

Do not report that a table row, drawer button, or search result was found by
the tool. Say that you visually inspected returned pixels and then sent
human-equivalent input.

## 8. Evidence, Replay, Integrity, And Review Artifacts

Use evidence fields as accounting, not as success claims:

- `host_input_sent=true`: host input was attempted/sent, not that it landed on
  the intended target.
- `host_sent_event_count>0`: event accounting only, not click success.
- `receipt`: a recorded operation attempt, not business completion.
- `replay`: material for external review, not proof that the task worked.
- `integrity_ok=true`: evidence structure/hash consistency, not target hit,
  causality, or business success.
- raw frames/crops: canonical visual evidence if non-degenerate.
- AgentSight Segment refs (`segment_restore_ref`) identify canonical raw frames
  that can be restored by the host/tooling; restored preview images, crops, and
  heatmaps are derived review artifacts.
- Segment crop/diff review helpers and `agentsight-segment-decoder diff` may
  read `.agseg` or legacy directory Segments. Diff can compare two frames in
  one Segment or two same-sized frames across `.agseg` buckets. Diff can also
  be limited to a region; then `changed_bbox` is local to the exported region
  image, not full-screen coordinates. Its output is still a derived review
  artifact and not target-hit, causality, or business-success evidence.
- `agentsight-segment-decoder changes` is metadata-first: it scans existing
  indexed Segment frames and reports adjacent-frame `changed_pixel_ratio`,
  `changed_bbox`, `delta_ms`, frame refs, and skipped/decode errors without
  exporting images. Use it before asking for crops or diff heatmaps when you
  only need to know where/when pixels changed. Region-limited change summaries
  use region-local bbox coordinates. Use `--min-changed-pixel-ratio` to group
  consecutive changed pairs into `change_runs` with start/end time, duration,
  pair count, peak ratio, and bbox union. Use `--from-time` / `--to-time` to
  restrict the query window; change pairs are filtered by the after-frame
  timestamp, meaning "when the changed pixels became visible in the indexed
  sequence."
- Public `/look q="changes"` exposes the same idea through the ordinary
  screen/look/do surface. It returns `type="changes"` with a nested
  `changes` report, `no_capture_performed=true`, and `no_media_exported=true`.
  Use it when you want a cheap timeline/region summary before requesting
  actual frame crops or diff heatmaps. The current first slice applies `r` to
  existing Segment frames. With `src.type="screen"`, `r` is already a virtual
  screen region. With `src.type="view"`, `r` is first mapped from parent-view
  pixels into the corresponding virtual screen region using the stored view
  transform. The change query then maps that virtual screen region through
  indexed `screen_region` metadata when available. Frames without mappable
  screen metadata are skipped with decode errors instead of being silently
  reinterpreted. Do not treat it as live-screen observation, target-hit proof,
  causality, or business success.
- cursor overlays, diff heatmaps, annotated images, GIF/video review clips:
  derived review artifacts only.

When evidence looks complete but the target may have been covered, the page may
auto-refresh, or a clock/hover state may change pixels, report only protocol
facts and visual uncertainty. Do not say "clicked successfully", "submitted",
"delivered", "caused", or "completed" unless an external reviewer makes that
judgment outside the tool.

## 9. Public MCP Surface

In public MCP stdio mode, the public tools are:

- `screen`
- `look`
- `do`

The MCP adapter rejects non-public tool calls before they reach the session gateway. `/health` is a Host Agent HTTP diagnostic endpoint for internal readiness consumers, not a public GUI-control MCP tool and not a required ordinary-AI preflight step.

## 10. Legacy/Internal/Compatibility Surface

Legacy/internal tools and routes may still exist for compatibility, diagnostics, old demos, or audit export:

- `/observe`
- `/click`
- `/mouse`
- `/input`
- `/p0`
- `observe`
- `query_visual_memory`
- `derive_candidates`
- `create_lease`
- `execute_input`
- `run_limited_batch`
- `get_evidence_package`
- `read_replay`
- `verify_integrity`

Do not use them as the ordinary AI workflow unless the operator or developer explicitly asks for legacy debugging. They do not grant OCR, clipboard, DOM, accessibility, window semantics, hidden APIs, target-hit judgment, causality judgment, or business-success judgment.

Never fall back to `observe`, `query_visual_memory`, `derive_candidates`,
`create_lease`, `execute_input`, `run_limited_batch`, `/click`, `/mouse`,
`/input`, or `/health` as an ordinary preflight just because the public path is
blocked. Legacy/internal paths are for compatibility, diagnostics, audit export,
or developer tests.

## 11. Tray And Supervisor Facts

The current preferred resident product shape is:

```text
HKCU Run key: AgentSightSupervisor
  -> AgentSightSupervisor
       -> AgentSightHostAgent
       -> AgentSightTray
```

`AgentSightSupervisor` is the preferred current-user lifecycle manager. Legacy split Host Agent watchdog and Tray watchdog paths may remain for compatibility, but they are not the recommended long-term entry.
Those split watchdogs are legacy compatibility paths. Future packaged installs should use `AgentSightSetup.exe` or the unified supervisor entry rather than registering separate long-running Host Agent and Tray watchdogs.

Tray GUI is the human-visible control surface. Its status icon/menu reflects redacted readiness and control state such as `ready`, `paused`, `emergency`, `blocked`, `discovery_missing`, or `unknown`. The tray icon is a generated Win32/GDI transparent-background `AS` uppercase lettermark: only the colored glyphs are visible; ready uses subtle blue/green animation, paused uses amber, emergency/blocked use static red, and discovery/unknown use gray. The right-click menu and tooltip support Chinese/English with a persisted language setting in `%LOCALAPPDATA%\AgentSight\tray-settings.json`. Recording policy lives at `%LOCALAPPDATA%\AgentSight\tray-config.jsonc` and contains only user-adjustable capture/retention settings such as idle FPS, action pre/post-frame capture, post-action FPS, post-action duration, max post-action frames, retention days, max storage, and minimum free disk. MKV container, codec, and rotation details are internal implementation choices, not first-version user settings. Idle FPS accepts low-frequency values such as `0.1` (one frame every 10 seconds). Timeline is always enabled and operation logs are always saved; they are not user toggles. Runtime Segment recording now defaults to MKV VFR files in `runs_host_agent/segments/*.mkv`, with sidecar `.frames.jsonl` frame indexes and `.manifest.json` summaries. Timeline and operation-log review uses the native PySide6/Qt `AgentSightTimelineViewer`: it scans MKV frame indexes and operation logs, then decodes only the selected video frame in memory; it does not generate HTML or batch PNG/GIF previews on open. AI `/look` crops are not loaded by default in logs. MKV video data, frame index, and operation log are canonical storage; Qt previews and explicit cache files are derived review only. `tray-config.jsonc` remains the source of truth. Tray GUI does not capture pixels, send input, OCR, use clipboard, inspect DOM/accessibility/window semantics, or judge success.

Important stop meanings:

- `operator pause/allow`: policy only; does not exit processes.
- `emergency stop`: blocks real control and should keep the tray visible.
- `Stop AgentSight`: full shutdown of Host Agent, Tray GUI, and Supervisor.
- `Exit Tray Only`: removed from the human menu because the Session Supervisor
  intentionally restarts the tray.

## 12. Error Handling

If any public call returns `ok=false`, `blocked`, `failed`, `partial`, HTTP 401/403/409/423, or a readiness blocker:

1. Report the exact blocker and safe facts.
2. Preserve any evidence paths and event counts.
3. Do not retry by guessing coordinates unless fresh pixels justify it.
4. Do not bypass with shell/cmd, clipboard, OCR, DOM, accessibility tree, window semantics, or hidden APIs.

The safe summary should say what the tool actually observed or sent, and should not claim that the UI task succeeded.

The tool does not report target hit, button identity, OCR text, semantic UI state, semantic change, causality, delivery success, task success, or business success.

Caller lock conflict rule:

- If `/screen`, `/look`, or `/do` returns HTTP `423`, HTTP `409`, or a
  `caller_lock.failure_code` such as `CALLER_LOCK_HELD_BY_OTHER_AI`, stop the
  ordinary public flow immediately.
- Do not change `X-AgentSight-Caller` to steal, reuse, or impersonate another
  caller.
- Do not call `/health`, legacy/internal routes such as `create_lease`, shell
  commands, OCR, clipboard, DOM, accessibility, window semantics, or hidden APIs.
- Do not attempt `/do` after a lock conflict.
- Report endpoint attempted, HTTP status, `readiness.code` if present,
  `control_blockers` if present, caller-lock status/failure code if present,
  and that no input was sent.
- Next safe step: wait for the existing caller/operator to release the lock,
  then retry later with the same stable caller identity if still authorized.

Authentication and authorization rule:

- HTTP `401`: report that bearer token authentication failed or was missing.
- HTTP `403`: report authorization/caller identity failure or policy refusal.
- Do not move `token` or `caller_id` into the JSON body to fix it.
- Do not try `/health`, legacy routes, hidden APIs, shell commands, or token
  guessing. Wait for operator/maintainer correction.

If discovery exists but `/screen` cannot connect, report `HOST_AGENT_UNREACHABLE`
with the discovery URL and say no `/look` or `/do` was attempted. Do not call
`/health`, legacy routes, shell commands, or GUI substitutes to work around the
unreachable host unless the operator explicitly asks for diagnostics.

If a `/do` request times out, disconnects, or the client loses the response,
treat the outcome as unknown and possibly already applied. Do not replay the
same input sequence. If public readiness is still available, use a fresh
`/look` to inspect current pixels, report the timeout, and mark event counts for
that request as unknown unless a receipt was actually returned.

## 13. Safe Reporting Template

After a GUI attempt, report protocol facts and external visual observations only.
Use this shape:

```text
I used only the AgentSight public screen/look/do flow.
Screen: virtual=..., monitors=..., readiness=...
Look: view_id=..., image_content_returned=..., scale_down=..., transform=..., capture_content_degenerate=...
Do: basis.view_id=..., basis.point=..., screen_point=..., steps=..., host_input_sent=..., host_sent_event_count=..., status=...
Post observe / after look: frame_count=..., changed_pixel_ratio=..., changed_bbox=..., frame_refs/segment_refs=...
I can report protocol facts, input event counts, pixel changes, and media paths.
I cannot claim target hit, causality, delivery, correct app state, task completion, or business success.
```

If you include a visual statement, phrase it as external visual review over the
returned pixels, not as a tool assertion.

