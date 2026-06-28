# 更新日志

中文 | [English](CHANGELOG.en.md)

## Unreleased

- 公开品牌整理为 `AgentSight for Windows`，保留 `ai_control` / `ai-control` 兼容运行名。
- 发布文档调整为中文主文档 + 英文镜像结构。
- Tray GUI 增加中英文菜单和 tooltip，语言设置持久化到 `%LOCALAPPDATA%\ai-control\tray-settings.json`。
- Tray 图标改为透明背景彩色 `AI` 字母，不再使用方块或圆形底色。
- Release workflow 支持 `v*` tag 和手动触发，执行测试、构建 exe、生成 SHA256 并上传 artifacts。
- 视觉记忆文档强调 `scale_down`、region、crop、`view_id`、`time.near` 是注意力工具箱的一部分。
