# 发布检查清单

中文 | [English](release-checklist.en.md)

## 发布前

- [ ] 确认版本号是 `1.0.0`，tag 使用 `v1.0.0`。
- [ ] 确认 README.md / README.en.md 已更新。
- [ ] 确认 CHANGELOG.md / CHANGELOG.en.md 已更新。
- [ ] 确认 `docs/release-notes-v1.0.0.md` 和英文镜像已准备。
- [ ] 确认 [repository-profile.md](repository-profile.md) 中的 About / Topics 已用于 GitHub 仓库。
- [ ] 确认目标 GitHub 仓库是 `git@github.com:xiayukun/agent-sight-windows.git` / `xiayukun/agent-sight-windows`，不要把 tag 或 release 推到本地内部 remote。
- [ ] 清理本地 `runs*`、证据目录、截图缓存、`dist/`、`build/`、Chrome profile、token 和私有路径。
- [ ] 确认 Run Key / Startup 指向 packaged `AgentSightSupervisor.exe`，不是失效源码路径。
- [ ] 确认不在当前活跃工作区里 reset、删除或覆盖未知未提交改动。

## 测试

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

## 构建

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

预期核心制品：

```text
dist\AgentSightSetup.exe
dist\AgentSightSupervisor.exe
dist\AgentSightHostAgent.exe
dist\AgentSightTray.exe
dist\AgentSightMcp.exe
```

Release 首屏推荐资产：

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

## 本地 smoke

- [ ] `AgentSightSetup.exe install --no-gui --start-now --arm-real-input` 可安装。
- [ ] `%LOCALAPPDATA%\AgentSight\ai-install` 包含 `mcp.json`、`SKILL.md`、`README_FOR_AI.md`、`AGENTSIGHT_AI_INSTALL_PROMPT.txt`。
- [ ] Host Agent discovery 可读。
- [ ] `screen -> look -> do -> look` 仍可用。
- [ ] Tray 图标可见，右键菜单可打开。
- [ ] 语言菜单可在跟随系统、中文、English 间切换。
- [ ] emergency stop 后真实控制被阻断。

## GitHub Release

- [ ] 创建 `v1.0.0` tag。
- [ ] Release workflow 的 `GITHUB_REPOSITORY` 检查通过，目标仓库为 `xiayukun/agent-sight-windows`。
- [ ] Release workflow 先测试，测试失败不发布。
- [ ] workflow 只把 versioned setup exe、对应单文件 sha256 和 `SHA256SUMS.txt` 作为 GitHub Release 首屏资产；内部 Host/Tray/Supervisor/MCP exe 仅保留在 CI artifact 或 setup 内。
- [ ] release notes 中文在上，英文在下或链接英文镜像。
- [ ] Release assets 不包含 runs、截图、视频、Chrome profile、token、私有路径或本地 evidence。
