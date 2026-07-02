# 用户指南

中文 | [English](user-guide.en.md)

## 适合谁

AgentSight for Windows 面向需要真实 Windows GUI 控制能力的 AI agent：computer-use agent、本地桌面操作 agent、需要复核证据的自动化实验。

它不是远程桌面，不是 OCR 工具，也不是后台业务 API。AI 通过它看到的是屏幕像素，发出的动作是鼠标键盘式输入。

## 安装

普通用户从 GitHub Release 下载：

```text
AgentSightSetup-1.0.0-windows-x64.exe
```

本地构建时对应：

```text
dist\AgentSightSetup.exe
```

运行安装器后，AgentSight 会安装到当前用户目录：

```text
%LOCALAPPDATA%\AgentSight
```

安装器会注册当前用户自启动，入口是 `AgentSightSupervisor`。Supervisor 再拉起 Host Agent 和 Tray GUI。

## 把 AgentSight 接给 AI

安装器会生成：

```text
%LOCALAPPDATA%\AgentSight\ai-install
```

请把这几个文件交给你的 AI 客户端读取：

- `mcp.json`：MCP stdio 配置，server 名称 `agentsight`。
- `SKILL.md`：普通 AI 调用 AgentSight 的边界和步骤。
- `README_FOR_AI.md`：接入说明。
- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`：可直接复制给 AI 的短提示。

接入后，普通 AI 只使用 `screen`、`look`、`do` 三个 MCP tools。

## AI 公共链路

```text
read discovery -> screen -> look -> do -> look
```

- `screen`：返回虚拟屏幕坐标、readiness、blocker 和帧索引事实。
- `look`：返回屏幕像素，可用 `scale_down`、region 和 `view_id` 聚焦。
- `do`：基于当前 `view_id` 发送人类等价输入。
- `look time.near`：按近似时间回看已有索引帧，不重新截图。
- `look q="changes"` / `q="diff"` / `q="clip"`：读取已有 Segment 的变化摘要、差分或派生审阅片段。

如果 readiness 报告锁屏、UAC secure desktop、operator pause、emergency stop、caller lock、截图不可用或输入不可用，AI 应停止并报告 blocker。

## 托盘控制面

Tray GUI 是给人类看的控制面，不是 AI 的语义通道。它提供：

- ready / paused / emergency / blocked / discovery_missing / unknown 状态图标；
- 暂停 AI 控制；
- 允许 AI 控制；
- 紧急停止和清除紧急停止；
- 采集与保留设置；
- 打开时间线；
- 查看操作日志；
- 停止 AgentSight；
- 语言：跟随系统、中文、English。

语言设置保存在：

```text
%LOCALAPPDATA%\AgentSight\tray-settings.json
```

采集与保留策略保存在：

```text
%LOCALAPPDATA%\AgentSight\tray-config.jsonc
```

设置窗只调整用户需要关心的策略，例如 idle FPS、操作前后帧、操作后 FPS / 时长、保留天数和磁盘空间。MKV 容器、帧索引和编码细节是内部实现。

## 证据和隐私

默认 canonical evidence 是：

```text
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.mkv
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.frames.jsonl
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\*.manifest.json
```

operation log、MKV、帧索引和 manifest 记录像素与事件事实。Qt 时间线预览、diff heatmap、GIF、截图 cache 都是 derived review artifacts。

证据可能包含真实屏幕内容。发布、上传或发给别人前先脱敏。不要把 runs、截图、视频、Chrome profile、token、私有路径或本地 evidence 上传到 GitHub。

## 边界

AgentSight 不提供 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API、shell/cmd GUI 替代、目标命中判断、因果判断或业务成功判断。AI 可以基于返回像素做自己的外部视觉判断，但不能说这些语义是 AgentSight 直接提供的。
