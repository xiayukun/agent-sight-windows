# AgentSight for Windows

中文 | [English](README.en.md)

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
![Boundary](https://img.shields.io/badge/boundary-pixel--grounded-green)

AgentSight for Windows 给 AI agent 一个本地 Windows 宿主：它看真实屏幕像素，用鼠标和键盘像人一样操作，并把过程留在可复核的 evidence / replay / integrity 证据链里。

它适合 computer-use agent、computer use 工作流、Windows AI agent、本地 GUI 自动化实验、需要审计记录的桌面操作流。它提供 pixel-grounded control 和视觉记忆系统，但不把 Windows 伪装成一组后台 API，也不替 AI 判断页面含义。

> A pixel-grounded observe-and-act host for Windows AI agents.

## 下载

当前修正版按 `1.0.1` 发布。GitHub Release 首屏只推荐下载安装器：

```text
AgentSightSetup-1.0.1-windows-x64.exe
```

如果你是在本地构建产物里使用，文件名通常是：

```text
dist\AgentSightSetup.exe
```

双击 `AgentSightSetup.exe` 后，安装器会在当前用户目录下完成这些事：

1. 解压到 `%LOCALAPPDATA%\AgentSight`。
2. 注册当前用户自启动，只启动 `AgentSightSupervisor`。
3. 拉起 Host Agent 和 Tray GUI。
4. 生成给 AI 客户端使用的 `ai-install` 资料包。
5. 弹出一段可复制提示，让你交给任意 AI 客户端继续接入 MCP 和 Skill。

不需要管理员权限。默认只绑定 `127.0.0.1`，不开放公网端口。

## 接入 AI：MCP + Skill

安装完成后，资料在：

```text
%LOCALAPPDATA%\AgentSight\ai-install
```

里面最重要的是：

- `mcp.json`：给支持 MCP 的 AI 客户端合并配置；server 名称是 `agentsight`，命令指向本机 `AgentSightMcp.exe`。
- `SKILL.md`：普通 AI 使用 AgentSight 的操作手册，写清楚只走 `screen`、`look`、`do`。
- `README_FOR_AI.md`：给接入 AI 的短说明。
- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`：你可以直接复制给 AI，让它自己读取上面的文件并完成接入。

接入后，普通 AI 的公开链路只有这一条：

```text
read discovery -> screen -> look -> do -> look
```

MCP public tools 也只暴露：

```text
screen
look
do
```

`/health`、诊断命令和内部工具留给 Tray、Supervisor、安装器和维护流程，不是普通 AI 做 GUI 任务的前置步骤。

## 它做什么

- 返回真实 Windows GUI 像素、坐标、时间、readiness 和 blocker。
- 用 `scale_down`、region、crop、`view_id` 帮 AI 逐步聚焦，而不是每次都抓全屏高清图。
- 在操作者允许、Host Agent ready、桌面状态允许时发送人类等价鼠标/键盘输入。
- 记录 operation log、MKV VFR 视频段、`.frames.jsonl` 帧索引和 `.manifest.json` 清单。
- 支持 `time.near`、变化摘要、diff、review clip、时间线设置等视觉记忆/复核工具。
- Tray GUI 给人类保留暂停、允许、紧急停止、采集与保留设置、屏幕监视器、时间线和操作日志入口。

## 它不做什么

AgentSight 的边界很硬。它不提供：

- OCR；
- clipboard 读写；
- DOM；
- accessibility tree；
- window semantics；
- hidden app API；
- 用 shell/cmd 替代 GUI 操作；
- 目标命中判断；
- 因果判断；
- 业务成功判断。

`host_sent_event_count > 0` 只说明事件被发送或插入，不说明点中了目标。`integrity_ok = true` 只说明证据结构一致，不说明任务成功。像素变化也只说明像素变了，不说明这次输入导致了变化。

## 证据、回放和视觉记忆

当前 canonical storage 是 MKV VFR 视频存储：

```text
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.mkv
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.frames.jsonl
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.manifest.json
```

这些文件记录的是像素和事件事实，供人类或外部审核复核。时间线和操作日志由原生 PySide6/Qt `AgentSightTimelineViewer` 打开，默认读取 MKV sidecar frame index，并在选中帧时按需解码到内存。diff heatmap、GIF、截图预览和显式 cache 都是 derived review artifacts，不是 canonical evidence。

## 当前运行形态

AgentSight 1.0.0 采用当前用户态架构：

```text
HKCU Run Key / Startup
  -> AgentSightSupervisor
       -> AgentSightHostAgent
       -> AgentSightTray
```

暂不做 Windows Service、SYSTEM 进程、driver、UAC secure desktop 控制或公网服务。锁屏、UAC secure desktop、操作者暂停、紧急停止、caller lock、截图或输入不可用时，工具会返回 blocker。

## 开发

源码开发态：

```powershell
$env:PYTHONPATH = "src"
py -m agentsight.session_supervisor run --host 127.0.0.1 --port 8765 --arm-real-input
```

测试：

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

构建当前 PyInstaller packaged layout：

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

## 文档

- [用户指南](docs/user-guide.md)
- [AgentSight 1.0.0 公共架构方案](docs/agentsight-1.0.0-public-architecture.md)
- [仓库 About / Topics 建议](docs/repository-profile.md)
- [发布检查清单](docs/release-checklist.md)
- [v1.0.1 发布说明](docs/release-notes-v1.0.1.md)
- [Screen / Look / Do 协议](docs/SCREEN_LOOK_DO_PROTOCOL.md)
- [视觉记忆与注意力系统](docs/visual-memory-and-attention.md)
- [Hermes / 新 Agent 接手指南](docs/HERMES_ONBOARDING.md)
