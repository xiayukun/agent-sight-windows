# 发布说明模板

中文 | [English](release-notes-template.en.md)

复制本模板为 `docs/release-notes-vX.Y.Z.md`，英文镜像使用 `docs/release-notes-vX.Y.Z.en.md`。

## 亮点

- AgentSight for Windows 提供 pixel-grounded observe-and-act Windows Host Agent。
- 公共 GUI 控制流：`screen -> look -> do -> look`。
- 视觉记忆 + 注意力工具箱：`scale_down`、region、`view_id`、`time.near`。
- 证据链：evidence / replay / integrity。
- Tray GUI：状态图标、右键菜单、暂停/允许、紧急停止、语言切换。

## 下载

- `AIControlSessionSupervisor.exe`
- `AIControlHostAgent.exe`
- `AIControlTrayGui.exe`
- `AIControlInstaller.exe`
- `SHA256SUMS.txt`

## 边界

不提供 OCR、clipboard、DOM、accessibility tree、window semantics、隐藏应用 API、cmd/shell GUI 替代、业务成功判断、因果判断或目标命中判断。

## 已知限制

- 当前仍是用户态 Session Supervisor 架构，不是 Windows Service。
- 锁屏、UAC secure desktop、权限不足会被识别为 blocker。
- Python 包名和命令仍保留历史 `ai_control` / `ai-control`。

## 发布前检查

- [ ] 全量测试通过。
- [ ] Release workflow 构建 artifacts。
- [ ] SHA256 checksums 已生成。
- [ ] 截图证据和本地缓存未进入源码。
