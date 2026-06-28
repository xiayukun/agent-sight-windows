# AgentSight for Windows 本地 Agent 指南

本文件是本仓库给未来 Codex / Claude / 其它 AI coding agent 的本地项目宪法。新会话接手时，先读本文件，再读 `README.md`、`src/ai_control/adapters/skill/SKILL.md` 和相关阶段文档。

重要维护规则：凡是项目哲学、产品边界、公开调用链、运行架构、发布策略、审核方式、目录清理策略发生变化，都必须同步更新本文件。不要只把新规则留在聊天里。

## 0. 当前接手优先级（2026-06-28）

新会话或新 agent 接手时，先读：

1. 本文件；
2. `docs/HERMES_ONBOARDING.md`；
3. `README.md`；
4. `src/ai_control/adapters/skill/SKILL.md`；
5. 与当前任务直接相关的源码和测试。

如果旧文档中出现冲突，以以下当前状态为准：

- 当前默认运行态 canonical storage 是 MKV VFR：`runs_host_agent/segments/*.mkv` + `.frames.jsonl` + `.manifest.json` + operation log；旧 `.agseg` 自定义格式仅作为历史/legacy 背景，除非用户明确要求迁移或清理。
- 当前时间线/日志查看器是原生 PySide6/Qt `AgentSightTimelineViewer`；旧网页 timeline/log viewer、批量 PNG preview、HTML review bundle 不再是默认主线。
- 时间线播放倍率必须按真实时间轴 `timestamp_ms`，不是按 MKV 内部固定帧率或 frame index。
- 点击柱子/拖动进度条/上一帧/下一帧/加载前后帧都必须暂停播放；播放中选中柱应自动滚入可视区。
- 点击柱子的跳转路径是 `frame -> segment_path -> playback_pts_ms`；跨 MKV 段时要切换对应视频段再 seek。
- 往前加载更多帧后要保持同一帧身份，而不是保持旧 index。
- 项目边界优先于实现便利：不引入 OCR、clipboard、DOM、accessibility tree、window semantics、shell 替代 GUI、业务成功/因果/目标命中判断。

## 1. 项目身份

公开产品名：`AgentSight for Windows`

历史兼容名：`AI-Control` / `ai-control` / `ai_control`

推荐 GitHub 仓库名：`agent-sight-windows`

一句话介绍：

```text
A pixel-grounded observe-and-act host for Windows AI agents.
```

中文定位：

AgentSight for Windows 是一个给 AI Agent 使用的 Windows 像素级观察与人类等价操作宿主。它让 Agent 通过真实屏幕像素观察，通过鼠标键盘行动，并保留可复核证据。

当前兼容约束：

- Python 包名暂时保留 `ai_control`。
- console scripts 暂时保留 `ai-control-*`。
- 用户数据目录暂时保留 `%LOCALAPPDATA%\ai-control`。
- 当前本地工作区暂时保留 `C:\git\其他\ai-control`。
- 不要在活跃 Codex 会话中直接把本地目录改名为 `agent-sight-windows`。

## 2. 核心哲学

AgentSight 的目标不是做“万能 Windows 自动化 API”，而是做 AI 可用的、像人类一样看和动的 Windows GUI 宿主。

核心原则：

- 人类能看到的，AI 才能通过本工具看到：真实屏幕像素、时间、坐标、证据路径。
- 人类能操作的，AI 才能通过本工具操作：鼠标、键盘、等待。
- 工具不替 AI 理解 UI，不替 AI 判断业务结果。
- 工具必须诚实记录自己实际看到了什么、发送了什么、没做到什么。
- 证据链的作用是支持外部审核，不是自证成功。

最重要的反过度声明原则：

- `integrity_ok=true` 只表示证据结构一致，不表示业务成功。
- `host_sent_event_count>0` 只表示输入事件被发送或插入，不表示点中了目标。
- 像素变化只表示像素变化，不表示输入导致变化。
- 工具内不要出现正向 `causal_loop_ok`、`business_success=true`、`target_hit=true` 这类字段。

## 3. 硬边界

除非用户在未来明确改变项目边界，否则以下能力不能作为产品能力加入：

- OCR；
- clipboard 读取或写入；
- DOM；
- accessibility tree；
- window semantics；
- 隐藏应用 API；
- cmd / shell 作为 GUI 替代；
- 后台业务 API；
- 目标命中判断；
- 因果判断；
- 业务成功判断；
- 绕过软件限制的语义化操作；
- 让人类看不到过程的黑盒业务跳转。

允许做的事情：

- 返回真实屏幕像素或图片路径；
- 返回坐标、时间、区域、hash、退化帧检测、证据路径；
- 返回 readiness / blocker；
- 在授权、前台、桌面状态允许时发送鼠标/键盘式输入；
- 记录 receipt / evidence / replay / integrity；
- 生成 derived review artifact，例如 cursor overlay、diff heatmap、GIF / video review clip，但必须标明 derived review only。

边界解释：

- 视觉模型对截图做语义理解，是调用方 AI 的外部判断，不是工具提供 OCR 或窗口语义。
- 工具可以做“退化帧/黑帧/近单色检测”，因为这是证据质量检测，不是业务语义判断。
- Tray GUI 是人类控制面，不是 AI 语义通道。

## 4. 公共 AI 调用链

普通 AI 面向 Host Agent 的公开链路是：

```text
read discovery -> /screen -> /look -> /do -> /look
```

MCP public tools 只应暴露：

```text
screen
look
do
```

不要把 `/health` 加回普通 AI 的必经调用链。`/health` 仍可作为 Tray GUI、Supervisor、installer、diagnostics 的内部/诊断接口。

每个 `/screen`、`/look`、`/do` 响应都应内嵌 readiness：

- `code`
- `readiness.schema=ai_control_public_readiness_v1`
- `service_status`
- `can_attempt_real_control`
- `control_blockers`

如果 readiness 不允许真实控制，工具应诚实返回 blocker，而不是尝试绕过。

使用技巧边界：

- AI 可以像人类一样通过 AgentSight 发送快捷键，例如 `Win+E`、`Win+D`、`Alt+Tab`、`Ctrl+L`、`Ctrl+F`、`Ctrl+A`、`Ctrl+V`。
- AI 可以把多个稳定目标的移动/点击合并到一个 `/do` 序列中，减少调用次数；但 UI 会动画、滚动、重排、弹层时应拆分并重新 `/look`。
- 普通 `/look q="frame"` 默认把派生审阅图作为 MCP image content 返回，不再默认把该 view 导出成硬盘 PNG；canonical evidence 当前是 MKV VFR segment、`.frames.jsonl` frame index、operation log 和 view record。
- 普通 `/screen`、`/look`、`/do` 捕获帧默认走内存 frame payload -> MKV recorder，不应先写 `session-*/media/*.png|*.bmp`。`session-*` 目录不应作为 Host Agent 启动或普通 public 调用副产物；只有显式导出复审包、legacy evidence/replay 诊断或兼容命令才可以创建。
- 每次 `/look` 必须生成 `view_id` 与 view record。view record 至少记录源 frame/Segment restore ref、屏幕 region、源 frame / Segment 内实际 decoded region、输出尺寸、scale、blur/cursor 模式、坐标系、`view_pixels -> virtual_screen_pixels` transform、request id 和 operation-log linkage。`requested_screen_region` 是虚拟屏幕坐标；`actual_decoded_region` 是源帧/Segment stored-frame 坐标，供按需预览解码使用，不能混用。
- `/look src.type="view"` 的 `r` 是父 view 图像像素，父 view 必须是当前屏幕 view、必须有 transform 且 `r` 必须落在父 view 边界内；历史 Segment review view 要以 `VIEW_NOT_CURRENT_SCREEN_BASIS` 失败，缺 transform 要以 `VIEW_TRANSFORM_UNAVAILABLE` 失败，越界要以 `VIEW_REGION_OUT_OF_BOUNDS` 失败，不能静默裁剪、扩展或猜测。
- `/do basis.view_id` 仍支持在 `seq` 中用 view-local `move x/y`；也可以用 `basis.point={x,y}` 传入一个 view-local 点，由工具按 view record transform 反算为 virtual screen 坐标，作为第一个 click-like step 的当前鼠标点。view_id 缺失、过期、transform/尺寸不可用或点位越界时必须失败，不能猜坐标。
- `/look src.type="view"` 必须按父 view record transform 把 view-local region 映射回 virtual screen region，并为新结果生成新的 view record。
- 长文本可以拆成多个 `text` 步骤连续输入；如果调用方在 AgentSight 外部自行准备剪贴板，再用 AgentSight 发送 `Ctrl+V`，必须把剪贴板准备声明为外部动作，不能说 AgentSight 读写了 clipboard。
- 调用方可以用自身具备的外部窗口/OS 信息辅助定位，但必须声明为外部上下文，不能说 AgentSight 读取了窗口语义。
- 任何外部语义、剪贴板、命令行、窗口清单都不能用来让 AgentSight 自称目标命中、因果成立或业务成功。

## 5. 视觉记忆与注意力工具箱

截图成本控制不是“省事”，而是产品核心：让 AI 使用注意力，而不是每次全屏高清。

推荐 AI 使用流程：

1. `/screen` 获取坐标、readiness，并写入 `screen_frame_index`。
2. `/look` 获取低成本全屏或大区域预览，通常使用 `scale_down`。
3. AI 自己根据像素选择 region 或 `view_id`。
4. 对选中区域请求高清 crop。
5. `/do` 发送人类等价输入。
6. 如果 `/do` 没有显式 `post_observe`，Host Agent HTTP 路径可以依据托盘录制策略自动注入 bounded 动作后观察窗口；这只是协议事实，不是成功/因果判断。
7. 查看 post-observe 连续帧、diff、receipt。
8. 必要时用 `time.near` 回看近似时间附近的索引帧。

当前 frame index 最小要求：

每次 `/screen`、`/look`、`/do` 后置连续帧、review clip 帧写入索引。记录至少包含：

```json
{
  "frame_id": "...",
  "captured_at_iso": "...",
  "captured_at_monotonic_ms": 123456,
  "media_path_abs": "...",
  "raw_or_derived": "raw",
  "cursor_mode": "none|native|overlay",
  "region": null,
  "view_id": "...",
  "event_id": "...",
  "width": 1920,
  "height": 1080,
  "sha256": "...",
  "capture_content_degenerate": false,
  "source": "screen|look|do_after_frame|review_clip",
  "screen_region": {"x": 0, "y": 0, "w": 1920, "h": 1080},
  "coordinate_system": "virtual_screen_pixels"
}
```

`time.near` 语义：

- AI 可以请求“某个近似时间附近的帧”。
- 如果没有精确帧，返回最近前帧、最近后帧、nearest。
- 返回 requested_time、actual_frame_time、delta_ms、before/after/nearest、raw_media_path_abs、cursor/derived 标记、退化帧标记、capture source、evidence/receipt/replay 引用。
- 该查询只查已有索引，不额外截图，不判断 UI 意义。
- Public `/look q="frame" time.near` 可以基于 `.agseg` 解码 nearest frame 的审阅图，返回 `decoded_review` / `historical_view` 元数据和 transient MCP image content；Gateway/MCP 路径会先查内存 frame buffer，未命中时回退到 Segment decoder；这是历史帧 review artifact，不是当前可操作 view，也不是默认持久 PNG。
- 如果历史 Segment review image 被返回，响应可以同时返回 `view_record`，其 `view_id` 与 `historical_view.id` 一致，并标记 `view_role="historical_segment_review"`、`view_is_current_action_basis=false`；该 record 只用于 operation log 追溯和按需 look-preview materialize，不能作为 `/do` action basis；若被传给 `/do`，必须以 `VIEW_NOT_ACTION_BASIS` 失败，不能猜当前屏幕坐标。
- 当 Segment frame 带有 `screen_region`，且 `coordinate_system` 是可映射的屏幕像素系（当前包括 `virtual_screen_pixels` 与 Windows capture 运行态常见的 `monitor_pixels`）时，`time.near` 请求中的 `r` 会按屏幕区域坐标解释，并映射成 `.agseg` 内部 stored-frame crop；返回中应包含 `decoded_review.decode_region_basis`、`source_coordinate_system` 和 `requested_screen_region`，说明映射依据。
- 如果请求 `r` 与某个有坐标元数据的历史 frame `screen_region` 完全不相交，不能把 `r` 退回解释为 stored-frame 坐标；该候选必须以 `decode_skipped_no_overlap` 留痕并尝试其它候选，或在没有可用候选时诚实失败。
- 旧 Segment frame 或 legacy 数据缺少 `screen_region` 元数据时，`time.near` 只能把 `r` 退回解释为 `stored_frame_px`，并必须返回 caveat；调用方不能把这种退回当成实时虚拟屏幕坐标。
- 如果 nearest/before/after 中某个候选 Segment frame 解码失败（例如旧坏段 blob hash mismatch），public adapter 可以尝试其它候选帧；失败尝试必须进入 `decode_errors`，成功返回的 `decoded_review.selected_segment_frame` 必须说明实际解码的是哪一帧。
- 全局 Segment frame index 扫描 `.agseg` / legacy manifest 时，遇到不可读或损坏段不能静默消失；必须在 `skipped_segments` / `skipped_segment_count` 里留痕，同时继续索引其它可读段。`query_segment_frames_near_time` 和 public `/look q="frame" time.near` 的 `frames_near_time` 也应透传这些 skipped 信息，让调用方知道本次回看不是完整覆盖所有段。
- `ai-control-segment-decoder near` / `query_segment_decoder_near_time` 同样必须透出 skipped 信息；本地 CLI 是审计和维护工具，不应比 HTTP public response 少报告证据缺口。
- `query_segment_change_index` / `ai-control-segment-decoder changes` 提供 metadata-first 变化索引：扫描已有 Segment 相邻帧，返回 `changed_pixel_count`、`changed_pixel_ratio`、`changed_bbox`、`delta_ms`、frame refs 和 skipped/decode errors；它不导出图片、不截图、不发送输入、不判断业务。CLI 默认可用 `x/y/w/h` 限定 stored-frame 区域，区域变化的 `changed_bbox` 使用 `region_local_px`。可用 `min_changed_pixel_ratio` / `--min-changed-pixel-ratio` 把连续 changed pairs 聚合为 `change_runs`，每个 run 返回起止时间、持续时间、pair 数、峰值变化比例和 bbox union。可用 `start_time` / `end_time` 或 CLI `--from-time` / `--to-time` 限定窗口；窗口过滤以 after frame 时间作为变化可见时间。Public `/look q="changes"` 也可返回同类 metadata-only 变化索引，响应标记 `no_capture_performed=true` / `no_media_exported=true`；`src.type="screen"` 时 `r` 是虚拟屏幕区域，`src.type="view"` 时 `r` 先按父 view transform 映射为虚拟屏幕区域，再通过 indexed Segment `screen_region` 映射到段内局部区域；缺元数据、不一致或不重叠的 frame pair 必须进入 decode errors，不应当作实时截图、目标命中、因果或业务成功。
- Public `/look q="diff"` 有两类模式：`mode=endpoints` 比较已有 view baseline 与最新同区域截图，会捕获最新像素；`mode=timeline` / `mode=timeline_with_artifacts` 只读取已有 Segment frames，不重新截图。timeline 模式返回 `diffs` metadata；`timeline_with_artifacts` 在 `max_artifacts>0` 时导出少量 derived diff heatmap。`src.type="screen"` 的 `r` 是虚拟屏幕区域；`src.type="view"` 的 `r` 先通过父 view 映射到虚拟屏幕区域，再通过 Segment `screen_region` / `coordinate_system` 映射到 stored-frame 局部区域。缺坐标元数据、不重叠、解码或导出失败必须进入 response errors，不能声明目标命中、因果成立或业务成功。
- Public `/look q="clip"` 只读取已有 Segment frames，不重新截图。它返回 `clip.frames` metadata；当 `max_artifacts=1` 时导出一个 `review_clip_gif` 派生审阅动画。`max_frames` 控制最多抽样帧数，`scale_down` 控制导出 GIF 尺寸。GIF 是 derived review only，不是 canonical evidence，不承诺精确实时播放节奏，不能声明目标命中、因果成立或业务成功。

时间线/日志预览策略：

- 时间线 UI 默认展示整屏历史帧和 look 标识/元数据，例如 view_id、region、scale、blur、caller/request id，不应默认加载每次 AI `/look` 的派生图。
- 操作日志默认线性展示 route/op、request/response 摘要、view_id、frame/Segment refs、region/scale/blur、事件数和 readiness/blocker，不应自动贴图。`look_preview_refs` 使用 `agentsight_look_preview_descriptor_v1` 描述“AI 看了哪里、怎么看”，不包含 image bytes 或默认 PNG 路径。
- 用户点击“查看这次 AI 看到的图”、选中某一帧或某个 look 记录时，才根据 view record 从 `.agseg` / raw frame 动态生成预览。预览必须使用 `actual_decoded_region` 作为源 frame / Segment crop；如果缺失，必须阻断并报告，不能退回用 `requested_screen_region` 猜 Segment 坐标。时间线/日志 UI 当前是原生 PySide6/Qt：选中帧时优先在内存中解码显示；显式按 operation-log index / preview index materialize 才写 `agent-sight-look-preview-cache`。命令入口是 `ai-control-tray look-preview materialize --log-index N --preview-index M`，它只读 operation log 和 Segment，不截图、不发送输入、不作为新的普通 AI 视觉接口。

视频化存储主线：

- 旧的 `agent-sight-review-video.gif`、网页 timeline/log viewer、目录式 restored preview 和 `.agseg` 自定义格式不再作为当前默认运行态主线。
- 当前主线是 MKV VFR：`runs_host_agent/segments/*.mkv` 是 canonical video storage；同名 `.frames.jsonl` 是帧索引；同名 `.manifest.json` 是小型清单。
- 默认运行态 canonical storage 只应包含 MKV、frame index、operation log、frame buffer / service state / discovery / quota report 等必要小型 JSON。普通捕获中间 PNG/BMP/GIF 不是 canonical storage，也不应默认产生。
- `SegmentFrameRecorder` 默认写 `storage_format="mkv_vfr"`，内部通过 FFmpeg 写 Matroska/MKV；每帧都有 `frame_id`、timestamp、`pts_ms`、screen region、coordinate system 和 restore ref。
- 二进制主线路径应是 runs 根目录下的稳定 bucket 文件，例如 `.../runs_host_agent/segments/agentsight-YYYYMMDD.agseg` 或 `agentsight-YYYYMMDD-HH.agseg`；`visual-*` 和 evidence `session-*` 目录只保存当次 evidence media / replay，不应决定长期视频段文件名。
- `.agseg` 内部第一版 blob 仍可用 PNG 编码，但这些 PNG blob 必须封装在单个二进制文件内，不再以“一帧一个 PNG 文件”的方式作为 canonical storage。
- `BinarySegmentReader` 应像解码器一样提供按 `frame_id` 还原整帧、导出 PNG、按区域取图、缩放和模糊审阅图。
- `ai_control.segments.decoder` / `ai-control-segment-decoder` 是本地解码器接口，可按 restore ref 或时间索引导出 PNG / region / before-after diff heatmap；diff 支持同一 Segment 内两帧，也支持跨 `.agseg` bucket 的同尺寸两帧，还可用 `x/y/w/h` 限定比较区域；区域 diff 的 `changed_bbox` 是导出局部图内坐标，必须结合 `region` 解读；它只读 Segment 文件，不截图、不发送输入、不判断业务。
- change index 是 metadata-only 摘要，不是 derived image；它帮助 AI 先判断哪段/哪块区域值得取图或导出 diff heatmap。
- `export_segment_frame_crop` / `export_segment_frame_diff` 可读取 `.agseg` 或 legacy directory Segment 并输出 derived review crop / diff heatmap；输出不是 canonical evidence。
- 运行时 `segment_frame.restore_ref` 对 `.agseg` 应包含 `storage_format="binary_agseg"`、`segment_path` 和 `frame_id`。
- Segment frame manifest 记录应在可获得时保存 `screen_region` 与 `coordinate_system`。这让历史帧裁剪可以从公共虚拟屏幕坐标映射到段内存储坐标；缺失时仍必须诚实退回到 stored-frame 坐标 caveat。
- `SegmentFrameRecorder.record_frame` 必须把 “读取 raw media -> add_frame -> flush footer” 当成一个串行事务；idle capture loop 与 public `/look` / `/do` 后置观察可能同时写同一个 bucket `.agseg`，不能并发写 blob/footer。
- Host Agent public `/look` 默认复用进程内 `visual-default` session，让同一运行态下的帧追加到同一个日/小时 `.agseg`；只有调用方显式传入 `visual_session_id` 时才隔离到其它 session。`BinarySegmentWriter.open_or_create` 必须能打开已有 `.agseg` 并从最后一帧继续写 P-frame，避免重启后覆盖或重新建 session 文件。
- Timeline model 应扫描 `runs_host_agent/**/segments/*.frames.jsonl` 并按需解码对应 `.mkv` 帧；这些 preview 不是新的 canonical evidence。
- 整帧解码成功只证明 MKV 与索引可读取，不证明业务成功、因果成立或目标命中。
- 区域、缩放、模糊、overlay、diff、GIF、Qt 预览和显式 cache 都是 derived review only；canonical evidence 是 MKV video data、frame index 和 operation log。
- 不要在没有用户明确要求时删除旧 evidence/runs 数据；旧数据可以在后续迁移或清理阶段按 retention 策略处理。

## 6. 运行架构

当前推荐用户态架构：

```text
Startup / Run Key
  -> AIControlSessionSupervisor
       -> AIControlHostAgent
       -> AIControlTrayGui
```

职责：

- `AIControlSessionSupervisor`：当前登录用户会话中的生命周期管理者，启动和监控 Host Agent 与 Tray GUI。
- `AIControlHostAgent`：提供 `/screen`、`/look`、`/do`、证据、readiness、真实输入和截图链路。
- `AIControlTrayGui`：人类可见控制面，提供状态、暂停、允许、紧急停止、停止 AgentSight、录制/时间线配置入口、语言切换。

当前不做：

- Windows Service；
- SYSTEM 进程；
- Session 0 bridge；
- `WTSGetActiveConsoleSessionId + CreateProcessAsUser`；
- driver；
- UAC secure desktop 控制。

未来可以研究服务层，但不能在当前 MVP 中把它当捷径。

自启规则：

- 长期推荐自启入口只能是 Session Supervisor。
- 不要再把 Host Agent watchdog 和 Tray watchdog 作为长期推荐入口。
- legacy split watchdog 可保留兼容，但文档中应标为 legacy。

## 7. Tray GUI 规则

Tray GUI 是人类可见控制面，不是 AI 视觉/语义/业务通道。

当前能力：

- 状态：`ready`、`paused`、`emergency`、`blocked`、`discovery_missing`、`unknown`。
- 右键菜单：状态、暂停 AI 控制、允许 AI 控制、紧急停止、清除紧急停止、采集与保留设置、打开时间线、查看操作日志、停止 AgentSight。
- 语言：跟随系统、中文、English。
- 语言设置文件：`%LOCALAPPDATA%\ai-control\tray-settings.json`。
- 录制/时间线配置文件：`%LOCALAPPDATA%\ai-control\tray-config.jsonc`。
- tooltip 国际化，例如 `AI-Control: Ready` / `AI-Control: 可用`。
- `采集与保留设置` 使用现代可滚动 Windows 设置窗，按当前默认语言显示，集中设置平时 FPS、动作采集、动作后 FPS / 时长 / 最大帧数、保留天数、最大保留空间和至少保留磁盘空间，保存后写入 `tray-config.jsonc`；`tray-config.jsonc` 仍是 source of truth。
- 时间线和操作日志当前由原生 PySide6/Qt `AgentSightTimelineViewer` 打开，不再生成网页查看器。
- `打开时间线` / `查看操作日志` 会启动原生 Qt 查看器：默认只加载 MKV frame index 和 operation log，选中帧时才把视频帧解码到内存显示；不默认生成 HTML、PNG、GIF 或 review bundle。Qt 预览是 derived review only。
- `仅退出托盘` 不再出现在人类菜单中，因为 Session Supervisor 会自动拉起托盘；完整停止应使用 `停止 AgentSight`。

录制/时间线配置目标：

- 平时低频观察使用 FPS 表达，例如默认 1 FPS，最低 0.1 FPS（10 秒 1 帧）；
- `/do` 前后可以按策略记录动作前帧、动作后帧、短时间高频帧；
- 配置应只包括用户需要调的采集策略和保留策略：idle FPS、action capture、retention days、max storage、minimum free disk 等。MKV 容器、编码器、轮转细节是内部实现，不作为第一版用户配置项。
- `post_observe_defaults` 不再作为用户配置；`/do` 后置观察必须直接按 action capture 的帧率、时长和最大帧数执行，例如 10 FPS × 10 秒 = 100 帧。
- timeline 和 operation log 不再作为用户开关；时间线必须启用，操作日志必须保存。
- 配置文件表达策略；当前有原生 Qt timeline/log viewer，但还没有完整 ring buffer / 长期视频归档；
- raw evidence 与 derived review artifacts 仍必须分离。

视频化存储主线：

- 从 P-F0 开始，canonical storage 路线固定为 AgentSight Segment v1；
- 内部编码模型是 keyframe + P-frame delta crop，暂不做 B-frame；
- 外层先使用目录式 proto-segment，schema / manifest / index / reader-writer 语义按未来单文件容器设计；
- 规范文件是 `docs/segments/AGENTSIGHT_SEGMENT_V1_SPEC.md`；
- `src/ai_control/segments/` 只放 segment schema/helper/writer/reader 相关代码；
- P-F1 当前已有 `SegmentWriter` / `SegmentReader` MVP：能写 keyframe、P-frame delta crop、no-change frame，并按 `frame_id` 从最近 keyframe + delta 链还原完整帧、校验 full-frame hash；
- P-F2 第一切片已接入 `ProtocolGateway`：公共 `/screen`、`/look` 和 `/do` 后置观察产生的 raw frames 会写入当前 bucket Segment，frame buffer entry 会带 `segment_frame`、`segment_id`、`segment_frame_id`、`segment_restore_ref`；
- P-F3 第一切片已让 `time.near` 查询返回 `segment_id`、`segment_frame_id`、`segment_restore_ref`、`segment_frame_status` 等字段；查询仍不重新截图，调用方应按 `restore_ref.storage_format` 选择 `BinarySegmentReader` 或 legacy `SegmentReader` 还原目标 raw frame；新帧带有 `screen_region` / `coordinate_system` 元数据时，Host Agent 可把公共虚拟屏幕 `r` 映射为段内 stored-frame crop；老帧缺元数据时保留 stored-frame caveat；
- P-F4 第一切片已让 `post_observe.sampled_frames` 透出 `segment_frame`，公共 operation log 可提取 `frame_refs` / `segment_frame_refs`，timeline attachment 可优先按 `segment_frame_id` 关联日志和帧；
- P-F5/P-F6 第一切片已让 timeline model 扫描 Segment manifest / `.agseg`，用对应 reader 生成 `derived_review_restored_segment_frame` 预览，并让 operation log 通过 `segment_frame_id` 与这些帧互相绑定；
- P-F7 已提供 Segment frame crop 与 before/after diff heatmap 的按需导出，支持 `.agseg` 与 legacy directory Segment，全部是 `derived_review_only`；`decode_segment_diff_to_png` 和 `ai-control-segment-decoder diff` 是面向解码器/CLI 的同一能力入口，CLI 可用 `--segment-path` 比较同段两帧，或用 `--before-segment-path` / `--after-segment-path` 比较跨 bucket 两帧；`diff` 还支持 `--x/--y/--w/--h` 生成区域限定 heatmap；
- P-G 第一切片已提供 metadata-first change index，可用 `ai-control-segment-decoder changes --root ...` 汇总相邻 Segment 帧的变化比例、变化 bbox 和连续变化段 `change_runs`；这是后续区域变化索引的雏形，不输出媒体；
- P-G 第二切片已把 metadata-first change index 暴露到 public `/look q="changes"` 与 MCP `look`，普通 AI 可以用虚拟屏幕区域先查已有 Segment 的变化摘要，再决定是否用 `/look q="frame" time.near` 或 segment decoder 导出 crop/diff；该入口不捕获、不导出媒体、不发送输入、不判断业务；
- P-F8 已提供 Segment retention/prune 计划与应用：operation log 引用的 Segment 会 pinned，过期未引用目录式 Segment 或 `.agseg` 文件可删除并写 prune report；
- 全局 Segment frame index 已可扫描多个 Segment manifest / `.agseg` 并执行 time.near 查询，返回 before/after/nearest 和 `segment_restore_ref`；它是可重建 metadata，不是 canonical evidence；
- `ProtocolGateway.capture_idle_frame(policy, now_ms)` 已支持按 tray-config idle FPS 做一次 idle capture tick；Host Agent 已有轻量用户态 idle capture loop 调度该 tick；`SegmentFrameRecorder` 默认写稳定 MKV VFR segment，并由内部策略处理短段轮转；ISO 字符串时间戳必须进入 frame index，不要退回“当前时间碰巧分桶”。
- Host Agent visual observe 与 public `/look` 会在顶层响应透出 `segment_frame`，方便调用方拿到 canonical Segment restore ref；这仍不代表业务成功、因果成立或目标命中；
- P-F2 到 P-F10 仍未完成完整 ring buffer、完整 Windows 原生播放器 UI 或真实 GUI 验收；
- 旧 `tray.viewers.write_timeline_segment` / 网页 review bundle 已删除；canonical 继续走 `.agseg` Segment；
- MP4 / H.264 / GIF / WebM 只能作为 derived review export，不能作为 canonical evidence；当前 canonical video container 是 MKV。

图标要求：

- 主体是透明背景彩色 `AS` 大写字母。
- 不允许方块背景、圆形底板、色块背景。
- ready：蓝/绿色，低频、克制动画。
- paused：黄色/琥珀色。
- emergency / blocked：红色静止。
- discovery_missing / unknown：灰色静止。
- 动画不能影响右键菜单响应。

## 8. 证据模型

raw evidence 与 derived review artifact 必须分离。

raw 可以作为 canonical evidence：

- 原始截图；
- 原始视频片段；
- 原始帧；
- 原始哈希和路径。

derived review only：

- cursor overlay；
- annotated image；
- diff heatmap；
- GIF / video review clip；
- 为方便人类或 AI 审阅生成的可视化。

Integrity / replay 必须清楚说明哪些是原始证据，哪些是派生审阅产物。

退化帧规则：

- 黑帧、近单色、透明、低信息量帧不能被当作有效观察参照。
- `capture_content_degenerate=true` 是证据质量事实，不是业务判断。

## 9. 安全与操作者控制

本工具本质上是本地操作者授权的真实 GUI 控制工具。它不是沙箱。

必须重视：

- bearer token；
- discovery 文件；
- localhost 绑定；
- stale discovery；
- caller lock；
- operator pause / allow；
- emergency stop；
- 锁屏；
- UAC / secure desktop；
- 证据截图泄露。

锁屏、UAC、secure desktop、非活动会话、权限不足时，应该返回 blocker，例如：

- `DESKTOP_LOCKED`
- `SECURE_DESKTOP_ACTIVE`
- `UAC_SECURE_DESKTOP_ACTIVE`
- `NOT_IN_ACTIVE_SESSION`
- `INPUT_UNAVAILABLE`
- `CAPTURE_UNAVAILABLE`
- `EMERGENCY_STOP_ACTIVE`
- `OPERATOR_PAUSED`

不要绕过这些 blocker。

## 10. 发布与文档风格

公开发布文档采用中文主文档 + 英文镜像：

- `README.md` / `README.en.md`
- `CHANGELOG.md` / `CHANGELOG.en.md`
- `CONTRIBUTING.md` / `CONTRIBUTING.en.md`
- `SECURITY.md` / `SECURITY.en.md`
- `PRIVACY.md` / `PRIVACY.en.md`
- `THIRD-PARTY-NOTICES.md` / `THIRD-PARTY-NOTICES.en.md`
- `MAINTAINERS.md` / `MAINTAINERS.en.md`
- `docs/*.md` / `docs/*.en.md` 发布相关文档。

每个双语文档顶部必须有语言切换链接。

文档分工：

- `src/ai_control/adapters/skill/SKILL.md` 是普通 AI 使用 AgentSight 的权威操作手册。凡是普通 AI 必须知道的调用链、readiness、`time.near`、`post_observe`、安全汇报模板、旧接口禁用规则，都应提炼进 Skill。
- `docs/SCREEN_LOOK_DO_PROTOCOL.md` 是开发者协议说明，可以保留更细的 schema、实现约束和兼容背景，但不要让它替代 Skill。
- `docs/user-guide.md` / `.en.md` 面向人类用户和发布页。
- `docs/release-*`、`docs/github-launch-*`、`docs/repository-profile*` 面向发布维护。
- 不要在 `docs` 中长期保留阶段包、本地审核包、describe JSON、临时发布草案、截图素材或 runs/evidence 输出。
- 如果某个 docs 文件只是重复 Skill 的普通 AI 操作说明，应删除或缩短为指向 Skill / README 的入口。

Release workflow 要求：

- tag `v*` 触发；
- `workflow_dispatch` 手动触发；
- Windows runner；
- 先测试，测试失败不发布；
- 构建 dist exe；
- 生成 SHA256 checksums；
- 上传 release artifacts；
- release notes 中文为主，英文附在下方或链接。

不要马上做大而全安装器。发布文档可以说明未来安装包方向，但当前主线仍是用户态 Session Supervisor。

## 11. 本地目录与缓存

当前用户数据目录：

```text
%LOCALAPPDATA%\ai-control
```

可保留的小型状态文件通常包括：

- `host-agent.json`
- `session-supervisor-state.json`
- `service-state.json`
- `operator-control-policy.json`
- `caller-lock.json`
- `unified-session-supervisor.enabled`
- `tray-settings.json`
- install / run report JSON

可清理的大型证据目录：

- `runs_*`
- 运行截图、GIF、视频片段、旧 evidence package。

清理前应先停止 Supervisor / Host Agent / Tray GUI，避免删正在写入的目录。

注意：如果 `dist` 被删除但 Run Key 仍指向 `dist\AIControlSessionSupervisor.exe`，重启/重新登录可能无法自启。后续涉及安装/启动时，要优先修复 packaged layout 或更新自启入口。

## 12. 开发流程

常用测试：

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

聚焦测试：

```powershell
py -B -m unittest tests.acceptance.test_p1g_tray_gui_control_surface
py -B -m unittest tests.acceptance.test_packaging_round7
py -B -m unittest tests.acceptance.test_p3a_screen_look_do_protocol
```

打包：

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

工程习惯：

- 先读现有代码和文档，再改。
- 小步实现，聚焦测试，再全量测试。
- 不要提交或保留多余 `dist/`、`build/`、`runs*`、本地证据。
- 手写文件改动用 patch。
- 搜索优先用 `rg`；大型搜索/测试/输出可通过 `rtk`。
- 当前没有 `.git` 时，不要假设 `git diff` 可用。
- Windows 平台判断统一走 `src/ai_control/runtime_platform.py` 的 `is_windows()` / `platform_system_label()`；不要在运行态、托盘、Supervisor、诊断入口中直接新增 `platform.system()`，避免 Windows 上触发慢速或 WMI 相关探测。

## 13. 审核规则

当前用户偏好：

- 后续步骤不要再启动审核 subagent。
- 阶段审核改为 Codex 本地自检：检查 diff / 关键代码路径 / 文档同步 / 边界红线 / 测试结果，并在最终汇报中给出证据。
- 不要写入 `docs/reviews` 或其它本地审核文件。
- 如果用户未来再次明确要求外部或 subagent 审核，再按新要求执行。

阶段完成后至少报告：

- 做了什么；
- 哪些测试通过；
- 是否有 caveat；
- 是否允许进入下一阶段。

## 14. 与用户协作规则

如果某一步依赖用户操作，例如：

- 重启；
- 注销/重新登录；
- 锁屏；
- UAC 弹窗；
- 手动同意系统权限；
- 微信/Claude/Cursor 前台 GUI 操作；

先明确告诉用户要做什么，等待用户确认。不要让目标卡在无人可见的系统提示上。

如果用户在外面，需要远程同意，先通过可用的人类可见渠道通知，再阻断等待。

## 15. 不要遗忘的项目历史结论

- P0 已证明真实 Windows GUI 闭环可成立：真实观察、真实输入、after 图、证据链、外部审核。
- P0-A/P0-B/P0-C/P0-D/P0-E 等阶段形成了当前 Host Agent、Supervisor、Tray GUI、视觉坐标闭环、审核结论习惯、frame index 等基础。
- 固定多角色长讨论模式已停止。现在按工程里程碑推进。
- Claude 不再作为实时阶段审核的必需环节；后续可由用户手动拿阶段产物给 Claude 回看。
- 当前主线是把工具产品化，而不是无限讨论边界。

## 16. 改动本文件的触发条件

以下情况必须更新 `AGENTS.md`：

- 项目公开名、仓库名、包名、exe 名迁移；
- public tools 或 public HTTP endpoints 变化；
- 边界允许/禁止项变化；
- Session Supervisor / Host Agent / Tray GUI 职责变化；
- 视觉记忆、frame index、time lookup、review artifact 语义变化；
- Tray GUI 菜单、图标、i18n、操作者控制语义变化；
- release workflow 或发布文档结构变化；
- 审核规则变化；
- 本地缓存/证据清理策略变化；
- 跨模块运行时 helper、平台判断、启动/诊断稳定性约束变化；
- 用户明确给出新的长期产品方向。

把这个文件当成项目长期记忆的一部分，而不是一次性说明。

