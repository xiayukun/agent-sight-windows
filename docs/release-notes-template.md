# 发布说明模板

中文 | [English](release-notes-template.en.md)

复制本模板为 `docs/release-notes-vX.Y.Z.md`。GitHub Release 正文使用中文主发布说明；如需英文镜像，作为文档链接提供，不在 workflow 中自动拼接进 Release 正文。

## AgentSight for Windows vX.Y.Z

一句话：AgentSight for Windows 是一个本地 Windows Host Agent，让 AI 通过真实屏幕像素观察，用鼠标键盘行动，并留下可复核的证据链。

## 下载

推荐下载：

```text
AgentSightSetup-X.Y.Z-windows-x64.exe
AgentSightSetup-X.Y.Z-windows-x64.sha256.txt
SHA256SUMS.txt
```

普通用户优先下载 `AgentSightSetup.exe`。Host Agent、Tray、Supervisor、MCP server 和 Timeline viewer 会打包在安装器里，不建议作为首屏单独资产分发。

## 安装后会发生什么

- 安装到 `%LOCALAPPDATA%\AgentSight`。
- 注册当前用户自启动，只启动 `AgentSightSupervisor`。
- 启动 Host Agent 和 Tray GUI。
- 生成 `%LOCALAPPDATA%\AgentSight\ai-install`，里面包含 `mcp.json`、`SKILL.md`、`README_FOR_AI.md` 和可复制提示。
- 默认只绑定 `127.0.0.1`。

## 本版重点

- 公共 GUI 控制流：`screen -> look -> do -> look`。
- MCP public tools：`screen`、`look`、`do`。
- 视觉注意力工具：`scale_down`、region、crop、`view_id`。
- 视觉记忆：`time.near`、change summary、timeline diff、review clip。
- 证据链：MKV VFR、`.frames.jsonl`、manifest、operation log。
- 人类控制面：Tray pause / allow / emergency stop / timeline / operation log。

## 边界

AgentSight 不提供 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API、shell/cmd GUI 替代、目标命中判断、因果判断或业务成功判断。

## 已知限制

- 当前是当前用户态 Session Supervisor，不是 Windows Service。
- 锁屏、UAC secure desktop、操作者暂停、紧急停止、caller lock、截图或输入不可用都会作为 blocker 返回。
- 证据可能包含真实屏幕内容，发布或分享前必须脱敏。

## 发布前检查

- [ ] 全量测试通过。
- [ ] Release workflow 先测试、再构建。
- [ ] SHA256 checksums 已生成。
- [ ] `ai-install` 包含 MCP 配置、Skill 和 README。
- [ ] 本地 runs、截图、视频、Chrome profile、token、私有路径和 evidence 没有进入 release 或源码。
