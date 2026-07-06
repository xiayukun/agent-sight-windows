中文

AgentSight for Windows v1.0.1 是安装体验和发布说明修正版。GitHub Release 标题已经包含版本号，本文件不再以重复的一级标题开头，避免 Release 正文首屏重复显示标题。

## 下载

推荐下载：

```text
AgentSightSetup-1.0.1-windows-x64.exe
AgentSightSetup-1.0.1-windows-x64.sha256.txt
SHA256SUMS.txt
```

普通用户只需要下载 `AgentSightSetup-1.0.1-windows-x64.exe`。Host Agent、Tray、Supervisor、MCP server 和 Timeline viewer 会包含在安装器里。

## 本版重点

- 安装器记录可见进度事件，便于用户和维护者确认安装阶段。
- 安装完成提示改为可选择、可复制的多行文本窗口，方便把提示交给 AI 客户端完成 MCP + Skill 接入。
- `ai-install/README_FOR_AI.md` 和内置 Skill 补充什么时候该用 AgentSight、什么时候应优先用 direct API 或其它工具。
- README 与仓库 Topics 补充屏幕监视器、时间线设置和 MKV VFR 视频存储关键词。
- GitHub Release 正文只使用中文主发布说明，避免中英文拼接导致正文首屏重复。

## 边界

AgentSight 仍然只提供像素观察、人类等价输入和证据记录。它不提供 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API、shell/cmd GUI 替代、目标命中判断、因果判断或业务成功判断。

## 发布前确认

- [ ] `py -m unittest discover tests` 通过。
- [ ] `py tools/build_host_agent_exe.py` 生成安装器。
- [ ] `AgentSightSetup.exe` 实际安装并生成 `ai-install`。
- [ ] SHA256 checksums 已生成。
- [ ] Release asset 不包含 runs、截图、视频、Chrome profile、token、私有路径或本地 evidence。
