# Visual Memory And Attention System

[中文](visual-memory-and-attention.md) | English

AgentSight for Windows should not remain only a "screenshot -> click -> screenshot" helper, and it should not force AI agents to watch a livestream like humans. The better shape is:

```text
traceable visual memory + attention toolbox
```

## Core Idea

- The tool observes the real Windows GUI continuously or on demand.
- The AI does not need a full-screen high-resolution image every time.
- The tool records pixels, time, regions, hashes, and evidence paths.
- The AI starts with a low-cost overview, then narrows space and time.
- Raw evidence stays separate from derived review artifacts.

## Recommended Observation Flow

1. `/screen`: coordinates, readiness, and `screen_frame_index`.
2. `/look`: low-cost full-screen or broad-region preview, usually with `scale_down`.
3. Choose region: caller AI chooses region or `view_id` from pixels.
4. High-detail local image: request detail only for the focused area.
5. `/do`: execute human-equivalent input.
6. Post frames: inspect post-observe frames, diff, or receipt.
7. Time lookup: use `time.near` to query indexed frames around an approximate time.

## Current Minimum Capability

- Frame index records `/screen`, `/look`, `/do` post frames, and review clip frames.
- Each record includes `frame_id`, `captured_at_iso`, `captured_at_monotonic_ms`, `raw_media_path_abs`, `raw_or_derived`, `cursor_mode`, `region`, `view_id`, `event_id`, `width`, `height`, `sha256`, `capture_content_degenerate`, and `source`.
- `look time.near` returns before / after / nearest indexed frames for an approximate time.
- The tool reports pixel facts, time, and evidence paths only; it does not OCR or judge business semantics.

## Later Direction

- P-CONFIG-RECORDING-POLICY: tray recording configuration center, unified around `%LOCALAPPDATA%\AgentSight\tray-config.jsonc` for user-adjustable idle capture, action pre/post frames, post-action FPS / duration, and retention days only; timeline is always enabled and operation logs are always saved, not user toggles;
- P0-F: short-term frame buffer / ring buffer / post-action clips;
- P0-G: region change index;
- P0-H: on-demand frame, crop, before/after, diff heatmap retrieval;
- P0-I: Skill/MCP flow for ordinary AI use of the visual memory toolbox;
- P0-J: near-realtime visual loop.

## Non-Goals

No OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, shell/cmd GUI substitute, business-success judgment, causality judgment, or target-hit judgment.
