# 发布检查清单

中文 | [English](release-checklist.en.md)

## 发布前

- [ ] 确认 README.md / README.en.md 已更新。
- [ ] 确认 CHANGELOG.md / CHANGELOG.en.md 已更新。
- [ ] 确认 release notes 中文主文档和英文镜像已准备。
- [ ] 清理本地 `runs*`、证据目录、截图缓存、`dist/`、`build/`。
- [ ] 确认 Run Key / Startup 指向 packaged `AIControlSessionSupervisor.exe`，不是失效源码路径。
- [ ] 确认不在当前活跃 Codex 工作区中直接重命名 `C:\git\其他\ai-control`。

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
dist\AIControlSessionSupervisor.exe
dist\AIControlHostAgent.exe
dist\AIControlTrayGui.exe
dist\AIControlInstaller.exe
```

## 本地 smoke

- [ ] `AIControlSessionSupervisor.exe status` 可返回状态。
- [ ] Host Agent discovery 可读。
- [ ] `/screen -> /look -> /do -> /look` 仍可用。
- [ ] Tray 图标可见，右键菜单可打开。
- [ ] 语言菜单可在跟随系统、中文、English 间切换。
- [ ] emergency stop 后真实控制被阻断。

## GitHub Release

- [ ] 创建 `v*` tag。
- [ ] Release workflow 测试通过。
- [ ] workflow 上传 exe 和 `SHA256SUMS.txt`。
- [ ] release notes 中文在上，英文在下或有链接。
- [ ] 不在测试失败时发布。
