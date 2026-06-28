# 视觉记忆与注意力系统

中文 | [English](visual-memory-and-attention.en.md)

AgentSight for Windows 不应该只是“截图 -> 点击 -> 再截图”的工具，也不应该强迫 AI 像人类一样盯着实时直播。更适合 AI 的形态是：

```text
可追溯视觉记忆 + 注意力工具箱
```

## 核心思想

- 工具持续或按需观察真实 Windows GUI。
- AI 不必每次都读取全屏高清图。
- 工具记录像素、时间、区域、哈希和证据路径。
- AI 先看低成本概览，再逐步缩小时间和空间范围。
- raw evidence 与 derived review artifacts 分离。

## 推荐观察流程

1. `/screen`：获取坐标、readiness，并写入 `screen_frame_index`。
2. `/look`：低成本全屏或大区域预览，通常使用 `scale_down`。
3. 选择区域：调用方 AI 基于像素自行选择 region 或 `view_id`。
4. 高清局部图：只对关注区域请求更多细节。
5. `/do`：执行人类等价输入。
6. 后置帧：读取 post-observe 连续帧、diff 或 receipt。
7. 时间回看：使用 `time.near` 查询某个近似时间附近的索引帧。

## 当前最小能力

- frame index 记录 `/screen`、`/look`、`/do` 后置帧和 review clip 帧；
- 每条记录包含 `frame_id`、`captured_at_iso`、`captured_at_monotonic_ms`、`raw_media_path_abs`、`raw_or_derived`、`cursor_mode`、`region`、`view_id`、`event_id`、`width`、`height`、`sha256`、`capture_content_degenerate`、`source`；
- `look time.near` 支持按近似时间返回 before / after / nearest 索引帧；
- 工具只返回像素事实、时间和证据路径，不做 OCR 或业务语义判断。

## 后续方向

- P-CONFIG-RECORDING-POLICY：托盘录制配置中心，统一 `%LOCALAPPDATA%\ai-control\tray-config.jsonc`，只配置用户可调的 idle capture、动作前后帧、操作后 FPS / 持续时间 / 最大帧数，以及保留天数；timeline 必须启用，operation log 必须保存，不作为用户开关；
- P0-F：短时 frame buffer / ring buffer / 操作后短视频；
- P0-G：区域变化索引；
- P0-H：按需取 frame、crop、before/after、diff heatmap；
- P0-I：普通 AI 使用视觉记忆工具箱的 Skill/MCP 流程；
- P0-J：准实时视觉循环。

## 不做什么

不做 OCR、clipboard、DOM、accessibility tree、window semantics、隐藏应用 API、cmd/shell GUI 替代、业务成功判断、因果判断或目标命中判断。
