# 仓库配置建议

中文 | [English](repository-profile.en.md)

## 仓库名

```text
agent-sight-windows
```

## GitHub About 描述

推荐使用这一版，信息密度够高，也不像关键词堆砌：

```text
Local Windows host for AI agents: screen pixels in, mouse and keyboard out, with audit logs, replayable evidence, MCP, and a visible tray control surface.
```

更短版本：

```text
Pixel-grounded Windows host for AI agents, with MCP, human-equivalent input, audit logs, replay, and visual memory.
```

中文备用文案（不建议放进 About，可用于发布页或介绍）：

```text
AgentSight for Windows 让 AI 通过真实屏幕像素观察 Windows，用鼠标键盘行动，并留下可复核的日志、回放和证据链。
```

## Topics

GitHub Topics 最多 20 个。推荐这一组：

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
audit-trail
replay
evidence
local-first
python
pyinstaller
agent-tools
windows-gui
```

逗号分隔版本：

```text
windows, windows-ai, ai-agents, computer-use, gui-automation, desktop-automation, mcp, model-context-protocol, pixel-grounded, screen-observation, mouse-keyboard, human-input, audit-trail, replay, evidence, local-first, python, pyinstaller, agent-tools, windows-gui
```

## README 首屏要传达什么

首屏不需要夸大。读者需要快速知道四件事：

1. 这是给 Windows AI agent / computer-use agent 的本地宿主。
2. AI 看到的是真实屏幕像素，动作是鼠标键盘式输入。
3. AgentSight 有 MCP + Skill 接入路径，安装器会生成 `ai-install` 包。
4. 它保留 evidence / replay / integrity / visual memory，但不做 OCR、DOM、accessibility、window semantics 或业务成功判断。

## Release 首屏资产建议

首屏推荐一个安装器和 checksum，不把内部 exe 摊开给普通用户：

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

Host Agent、Tray、Supervisor、MCP server、Timeline viewer 应打包进安装器。高级用户需要 portable zip 时可以作为补充资产，但不要放在第一推荐位置。
