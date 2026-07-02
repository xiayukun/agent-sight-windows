# GitHub 发布清单

中文 | [English](github-launch-checklist.en.md)

- [ ] 仓库名使用 `agent-sight-windows`。
- [ ] About 描述使用 [repository-profile.md](repository-profile.md) 推荐文案。
- [ ] Topics 使用 [repository-profile.md](repository-profile.md) 的 20 个推荐 topic。
- [ ] README.md 为中文主入口，README.en.md 为英文镜像。
- [ ] README 首屏包含 `AgentSightSetup.exe` 下载、MCP + Skill、Windows 安全边界、证据链/回放/视觉记忆。
- [ ] CHANGELOG / CONTRIBUTING / SECURITY / PRIVACY / THIRD-PARTY-NOTICES / MAINTAINERS 均有中英文版本。
- [ ] Release workflow 已启用，并且测试失败不发布。
- [ ] 默认分支保护要求测试通过。
- [ ] 首个 release notes 使用 `docs/release-notes-v1.0.0.md`。
- [ ] Release 首屏推荐 `AgentSightSetup-1.0.0-windows-x64.exe` 和 checksum，不把内部 exe 当普通用户主入口。
- [ ] 发布前清理本地 evidence、runs、token、截图、视频、Chrome profile 和个人路径。
- [ ] 确认 license 文件与用户选择一致。
