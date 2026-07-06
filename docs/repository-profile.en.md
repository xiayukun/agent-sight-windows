# Repository Profile

[中文](repository-profile.md) | English

## Repository name

```text
agent-sight-windows
```

## GitHub About description

Recommended version:

```text
Local Windows host for AI agents: screen pixels in, mouse and keyboard out, with audit logs, replayable evidence, MCP, and a visible tray control surface.
```

Shorter version:

```text
Pixel-grounded Windows host for AI agents, with MCP, human-equivalent input, audit logs, replay, and visual memory.
```

Chinese fallback copy for release pages or introductions:

```text
AgentSight for Windows 让 AI 通过真实屏幕像素观察 Windows，用鼠标键盘行动，并留下可复核的日志、回放和证据链。
```

## Topics

GitHub allows up to 20 topics. Use this set:

```text
windows
windows-ai
ai-agents
computer-use
gui-automation
desktop-automation
mcp
model-context-protocol
pixel-grounded
screen-observation
mouse-keyboard
human-input
screen-monitoring
mkv
audit-trail
evidence
local-first
python
pyinstaller
agent-tools
windows-gui
```

Comma-separated version:

```text
windows, windows-ai, ai-agents, computer-use, gui-automation, desktop-automation, mcp, model-context-protocol, pixel-grounded, screen-observation, mouse-keyboard, human-input, screen-monitoring, mkv, audit-trail, evidence, local-first, python, pyinstaller, agent-tools
```

## What the README first screen should say

The first screen should answer four questions quickly:

1. This is a local host for Windows AI agents and computer-use agents.
2. The agent observes real screen pixels and acts through mouse/keyboard-style input.
3. AgentSight has an MCP + Skill path; the installer writes an `ai-install` handoff package.
4. It records evidence / replay / integrity / visual memory, but it does not provide OCR, DOM, accessibility, window semantics, or business-success judgment.

## Recommended release assets

Keep the release front page simple:

```text
AgentSightSetup-1.0.1-windows-x64.exe
AgentSightSetup-1.0.1-windows-x64.sha256.txt
SHA256SUMS.txt
```

Host Agent, Tray, Supervisor, MCP server, and Timeline viewer should be bundled into the setup executable. A portable zip can be added for advanced users, but it should not be the primary download.
