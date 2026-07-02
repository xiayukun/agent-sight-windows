# 更新日志

中文 | [English](CHANGELOG.en.md)

## Unreleased

- 公开品牌整理为 `AgentSight for Windows`；首个正式版本按 `1.0.0` 发布，公开包名、命令、exe、MCP 和 Skill 统一使用 AgentSight / agentsight。
- README 首屏改为面向 GitHub 发布：加入 `AgentSightSetup.exe` 下载入口、MCP + Skill 接入、Windows 安全边界、证据链/回放/视觉记忆说明。
- 仓库 About / Topics 推荐文案更新为更自然的 GitHub 文案，覆盖 Windows AI agent、computer-use、MCP、pixel-grounded、evidence、replay 等关键词。
- 新增 `docs/release-notes-v1.0.0.md` / `.en.md` 发布说明草稿。
- 发布检查清单更新为 1.0.0 安装器主线，强调 release 首屏推荐 setup exe 和 checksum，不上传 runs、截图、视频、token 或本地 evidence。
- Tray GUI 增加中英文菜单和 tooltip，语言设置持久化到 `%LOCALAPPDATA%\AgentSight\tray-settings.json`。
- Release workflow 支持 `v*` tag 和手动触发，执行测试、构建 exe、生成 SHA256 并上传 artifacts。
- 视觉记忆文档强调 `scale_down`、region、crop、`view_id`、`time.near`、change summary、diff 和 review clip 是注意力/复核工具箱的一部分。
