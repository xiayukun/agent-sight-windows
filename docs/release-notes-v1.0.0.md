# AgentSight for Windows v1.0.0 发布说明草稿

中文 | [English](release-notes-v1.0.0.en.md)

AgentSight for Windows v1.0.0 是第一次正式上线版本。这个版本把公开产品名、Python 包、命令、exe、安装目录、MCP 和 Skill 统一到 AgentSight / agentsight，不把旧实验名作为公开入口继续带出去。

## 下载

推荐下载：

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

普通用户只需要下载 `AgentSightSetup-1.0.0-windows-x64.exe`。Host Agent、Tray、Supervisor、MCP server 和 Timeline viewer 会包含在安装器里。

## 安装后会发生什么

- 安装到 `%LOCALAPPDATA%\AgentSight`。
- 注册当前用户自启动，入口是 `AgentSightSupervisor`。
- 启动 Host Agent 和 Tray GUI。
- 生成 `%LOCALAPPDATA%\AgentSight\ai-install`。
- `ai-install` 内包含 `mcp.json`、`SKILL.md`、`README_FOR_AI.md`、`AGENTSIGHT_AI_INSTALL_PROMPT.txt`。
- 默认只绑定 `127.0.0.1`，不开放公网端口。

## 本版重点

- AI 公共链路固定为 `read discovery -> screen -> look -> do -> look`。
- MCP public tools 只有 `screen`、`look`、`do`。
- `screen` / `look` / `do` 响应内嵌 readiness 和 blocker，不要求普通 AI 先调 `/health`。
- `look` 支持 `scale_down`、region、crop 和 `view_id`，适合按注意力逐步聚焦。
- `do` 基于当前 view 发送人类等价鼠标/键盘输入。
- 证据主线使用 MKV VFR、`.frames.jsonl`、manifest 和 operation log。
- Tray GUI 提供暂停、允许、紧急停止、采集与保留设置、时间线和操作日志入口。
- 安装器生成 MCP + Skill 资料包，让用户可以把 AgentSight 接入任意支持 MCP / Skill 的 AI 客户端。

## 边界

AgentSight 只记录和执行像素/输入层面的事实。它不提供：

- OCR；
- clipboard 读写；
- DOM；
- accessibility tree；
- window semantics；
- hidden app API；
- shell/cmd GUI 替代；
- 目标命中判断；
- 因果判断；
- 业务成功判断。

`host_sent_event_count > 0` 不等于点中了目标；`integrity_ok = true` 不等于任务成功；像素变化也不等于输入导致了变化。

## 已知限制

- 当前是当前用户态 Session Supervisor，不是 Windows Service。
- 锁屏、UAC secure desktop、操作者暂停、紧急停止、caller lock、截图不可用或输入不可用会作为 blocker 返回。
- 证据可能包含真实屏幕内容，发布或分享前必须脱敏。
- 旧 runs / evidence 不会在安装时自动删除。

## 发布前确认

- [ ] `py -m unittest discover tests` 通过。
- [ ] `py tools/build_host_agent_exe.py` 生成安装器。
- [ ] `AgentSightSetup.exe` 实际安装并生成 `ai-install`。
- [ ] SHA256 checksums 已生成。
- [ ] Release asset 不包含 runs、截图、视频、Chrome profile、token、私有路径或本地 evidence。
