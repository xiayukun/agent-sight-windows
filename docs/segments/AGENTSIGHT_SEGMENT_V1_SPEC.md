# AgentSight Segment v1 Spec

中文 | English: not yet mirrored

本文件定义 AgentSight 视频化视觉记忆系统的第一版 canonical segment 格式。它是 P-F0 的交付物，用于后续 P-F1 Segment Writer / Reader、P-F3 segment-aware frame index、P-F5 Timeline Player 和 P-F8 Retention / Prune。

当前实现状态：

- P-F0：Segment v1 schema、manifest helper、index helper 已实现。
- P-F1：`agentsight.segments.SegmentWriter` / `SegmentReader` MVP 已实现，可写 keyframe、P-frame delta crop、no-change frame，并按 `frame_id` 还原完整帧与校验 hash。
- P-F2 第一切片：`ProtocolGateway` 已把公共 `/screen`、`/look` 和 `/do` 后置观察产生的 raw evidence frames 同步写入当前 session 的 Segment，并在 frame buffer entry 中返回 `segment_frame` / `segment_restore_ref`。
- P-F3 第一切片：`time.near` 查询会透出 `segment_id`、`segment_frame_id`、`segment_restore_ref`、`segment_frame_status` 等字段，调用方可用 `SegmentReader` 按时间查询结果还原 raw frame；查询本身仍不重新截图。新 Segment frame 带有 `screen_region` / `coordinate_system` 时，Host Agent 可把公共屏幕区域 `r` 映射为段内 stored-frame crop；当前可映射坐标系包括 `virtual_screen_pixels` 和 Windows capture 运行态常见的 `monitor_pixels`；旧帧缺元数据时必须返回 stored-frame caveat。
- P-F4 第一切片：`post_observe.sampled_frames` 会透出 `segment_frame`；公共 operation log 会提取 `frame_refs` / `segment_frame_refs`；timeline attachment 可优先按 `segment_frame_id` 把日志挂回帧。
- P-F5/P-F6 第一切片：timeline model 会扫描 Segment manifests，用 `SegmentReader` 按需生成 derived restored preview cache；operation log viewer/model 可通过 `segment_frame_id` 与这些预览帧互相跳转。
- P-F7：提供 Segment frame crop 和 before/after diff heatmap 的按需导出，支持 `.agseg` 与 legacy directory Segment；diff 可限定区域，也可跨 `.agseg` bucket 比较同尺寸帧；这些 artifact 全部标为 `derived_review_only`。
- P-F8：提供 Segment retention/prune dry-run 与应用；过期但被 operation log 引用的 Segment 会被 pinned，只有过期且未引用的 Segment 目录或 `.agseg` 文件会被删除并写 prune report。
- P-G 第一切片：提供 metadata-first change index，可按已有 Segment 相邻帧返回变化比例、变化 bbox、delta_ms 和 frame refs，不导出图片。
- P-G 第二切片：public `/look q="changes"` 与 MCP `look` 可返回同类 metadata-only 变化索引；`src.type="screen"` 时 `r` 使用虚拟屏幕坐标，`src.type="view"` 时先按父 view transform 映射为虚拟屏幕区域，再通过 Segment `screen_region` 元数据映射到段内局部区域，继续保持不截图、不导出媒体、不发送输入、不判断业务。
- P-F3 补强：提供全局 Segment frame index 构建与 `query_segment_frames_near_time`，可跨 Segment 返回 before/after/nearest 及 `segment_restore_ref`；该 index 是 metadata，不是 canonical evidence。
- P-F2 补强：`ProtocolGateway.capture_idle_frame(policy, now_ms)` 可按 tray-config idle FPS 执行一次 tick；Host Agent 会启动轻量用户态 idle capture loop 调度该 tick；`SegmentFrameRecorder` 支持按 `daily_segment_boundary_local_time` 的本地日边界轮转 Segment。
- Host Agent HTTP visual observe 与 public `/look` 会在顶层响应中透出 `segment_frame`（若本次捕获已写入 Segment），让调用方不必从内部 `observation` 中挖 restore ref。
- P-F9 第一切片：新增 `.agseg` 单文件二进制 Segment 容器 MVP。它保持 keyframe + P-frame delta crop 语义，把 blob、manifest、index 写入一个二进制文件，并提供 reader 解码整帧、区域、缩放和模糊审阅图。
- P-F10 第一切片：`SegmentFrameRecorder` 默认写 `.agseg`；目录式 writer 保留为 `storage_format="proto_directory"` legacy 迁移路径；timeline model 可扫描 `.agseg` 并按需解码 derived restored preview。
- 尚未实现长期 ring buffer、完整 Windows 原生播放器 UI 或真实 UI 终验。

## 1. 目标

AgentSight Segment v1 的目标是把真实 Windows GUI 像素观察保存为可追溯、可 seek、可还原、可清理的时间线片段。

Segment v1 采用：

- 内部编码：keyframe + P-frame delta crop；
- 外层包装：目录式 proto-segment 兼容路径 + `.agseg` 单文件二进制容器主线；
- 帧率模型：可变帧率，每帧都有真实时间戳；
- 证据模型：segment raw frame data 是 canonical evidence；MP4、GIF、WebM、overlay、diff heatmap 等只允许作为 derived review artifact。

Segment v1 不做：

- H.264 / x264 / OpenH264；
- B-frame；
- 音频；
- 硬件编码；
- OCR；
- clipboard；
- DOM；
- accessibility tree；
- window semantics；
- 业务成功、因果或目标命中判断。

## 2. 为什么不用 H.264 做 Canonical Evidence

H.264 适合人类观看和高压缩率，但不适合作为本项目第一版 canonical evidence：

- H.264 编码会引入复杂的 GOP、B-frame、环路滤波、色彩空间、量化和实现差异，逐帧像素还原与 hash 证明成本高。
- AgentSight 的核心不是影视播放，而是“某个时间、某个区域、某个输入事件附近发生了什么像素事实”。
- 工具必须能用简单、可审计的 reader 还原任意帧，并验证 manifest 记录的 full-frame hash。
- 对 AI 来说，按需 seek、crop、diff、overlay 比连续播放压缩视频更重要。

因此 H.264 / MP4 / WebM 只能作为 derived review export，不进入 integrity 的真相源。

## 3. 为什么不长期一帧一 PNG

一帧一 PNG 容易实现，但不适合作为长期视觉记忆形态：

- idle 观察和 `/do` 后高频帧会快速生成大量文件。
- 文件系统目录会被截图碎片淹没，清理、索引、迁移和完整性校验成本高。
- 很多 GUI 帧只有局部变化，用全量 PNG 保存浪费空间。
- 后续需要基于 segment 粒度做 retention、pin、prune 和 operation log 关联。

Segment v1 早期使用目录式 proto-segment 方便开发；从 P-F9 开始，`.agseg` 是长期主线，目录式 Segment 保留兼容和迁移参考。

## 4. 单文件 `.agseg` 容器布局

`.agseg` 是 AgentSight 后续长期视觉记忆的主线容器。第一切片目标不是压缩率极致，而是做到：

- 单个二进制文件承载一个时间段内的 raw frame data；
- keyframe + P-frame delta crop；
- 每帧可按 `frame_id` 解码成完整 PNG 像素；
- 可按区域导出 crop、缩放图、模糊图；
- manifest / index / blob hash 自洽；
- 不依赖旧的一帧一 PNG 文件夹作为 canonical source。

推荐轮转粒度：

- 小规模开发 / 调试：可使用临时 runs 目录，但二进制主线仍按 bucket 文件写入；
- 正式用户态采集：按小时或按天在 runs 根目录 `segments/` 下生成一个稳定 `.agseg`，例如 `agentsight-YYYYMMDD.agseg` 或 `agentsight-YYYYMMDD-HH.agseg`；`visual-*` 和 evidence `session-*` 目录不决定长期段文件名；
- 后续可按大小阈值强制切段，避免单文件过大。

文件结构：

```text
AGSEGv1\0
u32 header_json_length_le
header_json
blob bytes...
manifest_json
u64 manifest_json_length_le
AGSEGFTR
```

`header_json` 是启动时可读的轻量头：

```json
{
  "schema": "agentsight_binary_segment_header_v1",
  "segment_schema": "agentsight_segment_v1",
  "segment_id": "seg-20260621-1000",
  "container_model": "single_file_agseg_v1",
  "codec_model": "keyframe_plus_pframe_delta_crop",
  "created_at_iso": "2026-06-21T10:00:00.000+08:00"
}
```

footer 前的 `manifest_json` 包含 Segment v1 manifest、frame index 和 blob table。Reader 从文件尾部读取 footer，得到 manifest 长度，然后 seek 到 manifest；再根据 blob table 的 offset / length 读取 keyframe 或 delta crop。

blob table 示例：

```json
{
  "blob_id": "b000001",
  "role": "delta_crop",
  "offset": 1234,
  "length": 456,
  "sha256": "...",
  "encoding": "png"
}
```

第一版 blob 编码仍使用 PNG，这是为了保证实现可审计、跨机器可复现。PNG blob 被封装在 `.agseg` 内部，不再散落成每帧一个文件。未来可新增更高效的内部编码，但必须保持 reader 可验证 restored full-frame hash。

`.agseg` reader 必须至少支持：

- `restore_frame(frame_id)`：解码完整帧并校验 `full_frame_sha256`；
- `restore_frame_to_png(frame_id, output_path)`：导出可打开的 PNG；
- `restore_region(frame_id, region, scale_down=1, blur_radius=0)`：按需导出区域视图；
- before/after diff heatmap 可通过 review helper / decoder CLI 按需导出；同 Segment 两帧可以用单一路径，跨日/小时 bucket 时可比较两个同尺寸 `.agseg` 帧；可选 `region` / `x-y-w-h` 生成局部 diff，局部 diff 的 `changed_bbox` 使用导出图内坐标并通过 `diff_coordinate_basis="region_local_px"` 标注；
- 区域、缩放、模糊、diff heatmap 输出必须标记为 `derived_review_only`，不能替代 `.agseg` raw data。

`.agseg` 不改变公共 AI 调用链。P-F10 起，Host Agent 的 Segment recorder 默认写入 `.agseg`，但 `/screen`、`/look`、`/do` 语义不变。

Host Agent public `/look` 默认复用进程内 `visual-default` session，因此同一运行态下的连续观察会追加到同一个日/小时 `.agseg` 文件。只有调用方显式传入 `visual_session_id` 时，才隔离到其它 session / Segment recorder。

`BinarySegmentWriter.open_or_create` 必须能打开已有 `.agseg`，读取 embedded manifest/footer，还原最后一帧作为下一帧的 P-frame 基线，再覆盖旧 footer 继续追加 blob。重启 Host Agent 或创建新的 EvidenceReplayService 不应覆盖同一 bucket 文件。

写入并发约束：`SegmentFrameRecorder.record_frame` 必须把 “读取 raw media -> add_frame -> flush footer” 作为一个串行事务。Host Agent idle capture loop、public `/look` 和 `/do` 后置观察可能同时写入同一个稳定 bucket `.agseg`；实现必须避免并发写 blob/footer，否则 reader 可能出现 blob hash mismatch。

## 4A. Proto-Segment 目录布局

第一版目录布局：

```text
segment-{segment_id}/
  manifest.json
  index.json
  keyframes/
    k000000.png
    k000120.png
  deltas/
    d000001.png
    d000002.png
  thumbnails/
    t000001.webp
  derived/
    preview.gif
    diff-f000121.png
```

要求：

- `manifest.json` 是 segment 的权威元数据。
- `index.json` 是 reader/seek 快速索引，可由 manifest 重建。
- `keyframes/` 与 `deltas/` 中的数据用于 canonical restore。
- `thumbnails/` 和 `derived/` 默认不是 canonical evidence。
- 未来单文件容器应保持相同 manifest/index/blob-ref 语义。

## 5. Manifest Schema

Schema 名称：

```text
agentsight_segment_v1
```

最小 manifest：

```json
{
  "object_type": "AgentSightSegmentManifest",
  "schema": "agentsight_segment_v1",
  "segment_id": "seg-20260621-000001",
  "container_model": "proto_directory_now_single_file_later",
  "codec_model": "keyframe_plus_pframe_delta_crop",
  "frame_rate_model": "variable_timestamped_frames",
  "raw_frames_are_canonical_evidence": true,
  "derived_review_video_is_canonical": false,
  "b_frames_used": false,
  "h264_used": false,
  "started_at_iso": "2026-06-21T10:00:00.000+08:00",
  "ended_at_iso": "2026-06-21T10:01:00.000+08:00",
  "frame_count": 0,
  "keyframe_count": 0,
  "pframe_delta_count": 0,
  "pframe_no_change_count": 0,
  "frames": [],
  "integrity": {
    "manifest_hash_algorithm": "sha256",
    "frame_hash_algorithm": "sha256",
    "hash_scope": "restored_full_frame_pixels_or_canonical_png_bytes"
  },
  "boundary": {
    "ocr_used": false,
    "clipboard_used": false,
    "accessibility_tree_used": false,
    "dom_used": false,
    "window_semantics_used": false,
    "business_success_judged": false
  }
}
```

## 6. Frame Record Schema

每帧必须有真实时间戳和 restore 所需信息。

坐标元数据规则：

- 当捕获来源提供真实屏幕区域时，frame record 应保存 `screen_region`，字段使用 `x/y/w/h`，表示该 stored frame 对应的虚拟屏幕区域。
- 当坐标空间明确时，frame record 应保存 `coordinate_system`。当前公共虚拟屏幕路径使用 `virtual_screen_pixels`；Windows capture 运行态可能保存 `monitor_pixels`，但只要同时有 `screen_region`，仍可按该屏幕区域映射。
- 这两个字段让历史 `/look q="frame" time.near` 能把请求中的公共屏幕区域 `r` 映射成 `.agseg` 内部 stored-frame crop。缺失时 reader/Host Agent 必须诚实退回到 `stored_frame_px`，并返回 caveat。
- 如果请求区域与带坐标元数据的历史帧 `screen_region` 完全不相交，Host Agent 不得把该虚拟屏幕区域当作 stored-frame 坐标裁剪；应记录 `decode_skipped_no_overlap` 并尝试其它候选帧，或在无候选可用时返回解码失败。

```json
{
  "frame_id": "f000123",
  "timestamp_iso": "2026-06-21T10:00:12.300+08:00",
  "timestamp_monotonic_ms": 123456,
  "frame_index": 123,
  "frame_kind": "pframe_delta",
  "source": "post_do",
  "event_id": "do-abc",
  "nearest_keyframe_id": "f000120",
  "previous_frame_id": "f000122",
  "delta_bbox": {"x": 100, "y": 200, "w": 300, "h": 120},
  "keyframe_blob_ref": null,
  "delta_blob_ref": "deltas/d000123.png",
  "full_frame_sha256": "sha256...",
  "restore_chain_sha256": "sha256...",
  "raw_or_derived": "raw",
  "cursor_mode": "none",
  "capture_content_degenerate": false,
  "width": 1920,
  "height": 1080,
  "screen_region": {"x": 0, "y": 0, "w": 1920, "h": 1080},
  "coordinate_system": "virtual_screen_pixels",
  "tool_asserts_business_success": false,
  "tool_asserts_causality": false,
  "tool_asserts_target_hit": false
}
```

允许的 `frame_kind`：

- `keyframe`：完整帧。
- `pframe_delta`：相对上一帧或最近还原帧的局部 delta crop。
- `pframe_no_change`：无像素变化，复用上一帧。

允许的 `source`：

- `idle`
- `pre_do`
- `post_do`
- `screen`
- `look`
- `manual_import`
- `diagnostic`

## 7. Keyframe Record

Keyframe 必须包含：

- `frame_kind="keyframe"`；
- `keyframe_blob_ref`；
- `nearest_keyframe_id` 等于自身或为空；
- `delta_blob_ref=null`；
- `delta_bbox=null`；
- `full_frame_sha256`。

写入 keyframe 的条件：

- segment 第一帧；
- delta 变化区域超过阈值；
- keyframe interval 到达；
- 分辨率或像素格式变化；
- restore chain 过长；
- writer 无法安全生成 delta。

## 8. P-frame Delta Record

P-frame delta crop 必须包含：

- `frame_kind="pframe_delta"`；
- `previous_frame_id`；
- `nearest_keyframe_id`；
- `delta_bbox`；
- `delta_blob_ref`；
- `full_frame_sha256`。

Delta 的语义：

1. reader 先还原 `previous_frame_id`；
2. 读取 `delta_blob_ref`；
3. 将 delta crop 覆盖到 `delta_bbox`；
4. 得到当前完整帧；
5. 计算 hash 并与 `full_frame_sha256` 比对。

`pframe_no_change` 不需要 delta blob，但仍必须有 `full_frame_sha256`，通常与上一帧相同。

## 9. Segment-Level Index

`index.json` 面向 seek 和 lookup：

```json
{
  "schema": "agentsight_segment_index_v1",
  "segment_id": "seg-20260621-000001",
  "frame_count": 120,
  "time_index": [
    {"timestamp_monotonic_ms": 123000, "frame_id": "f000000"},
    {"timestamp_monotonic_ms": 123100, "frame_id": "f000001"}
  ],
  "keyframes": [
    {"frame_id": "f000000", "timestamp_monotonic_ms": 123000},
    {"frame_id": "f000120", "timestamp_monotonic_ms": 135000}
  ],
  "events": [
    {"event_id": "do-abc", "frame_ids": ["f000120", "f000121"]}
  ]
}
```

`index.json` 可以缓存，若损坏，可由 `manifest.json.frames` 重建。

全局 Segment frame index 扫描多个 Segment 时，遇到不可读 `.agseg` 或 legacy manifest 不应中断整个查询，也不应静默吞掉。实现必须返回：

- `skipped_segment_count`；
- `skipped_segments[]`，至少包含 `path_abs`、`storage_format`、`status="skipped"`、`error_type`、`detail` 和边界否定字段。

`query_segment_frames_near_time` 返回的 near-time 结果也必须透传 `skipped_segment_count` 和 `skipped_segments`，因为调用方通常只看到查询结果，而不是原始 index。`query_segment_decoder_near_time` 和 `agentsight-segment-decoder near` 作为本地审计/维护入口，也必须保留这些字段。这里的 skipped 只说明该段未进入本次 metadata index，不说明段内业务事实，也不执行截图或输入。

`query_segment_change_index` 和 `agentsight-segment-decoder changes` 是 metadata-first 变化索引入口。它扫描已有 Segment 相邻帧，返回 `changed_pixel_count`、`changed_pixel_ratio`、`changed_bbox`、`delta_ms`、before/after frame refs、skipped segments 和 decode errors；它不导出图片、不截图、不发送输入、不判断目标命中、因果或业务成功。可选 `region` / `x-y-w-h` 限定区域，区域变化的 `changed_bbox` 使用局部坐标，并以 `diff_coordinate_basis="region_local_px"` 标注。可选 `min_changed_pixel_ratio` / `--min-changed-pixel-ratio` 会把连续 changed pairs 聚合为 `change_runs`，每个 run 返回 start/end time、duration、pair count、peak ratio 和 bbox union。可选 `start_time` / `end_time` 或 CLI `--from-time` / `--to-time` 限定查询窗口；窗口过滤以 after frame timestamp 作为变化可见时间，因此不会要求窗口起点之前的 baseline frame 被丢弃。

Public `/look q="changes"` 是同一能力的普通 AI 入口。它返回 `type="changes"`、`mode="segment_metadata_change_index"` 和嵌套 `changes` 报告，并显式标注 `no_capture_performed=true`、`no_media_exported=true`、`raw_media_returned=false`、`derived_review_artifact_returned=false`。当前第一版支持 `src.type="screen"` 和 `src.type="view"`：screen 请求的 `r` 是虚拟屏幕区域；view 请求的 `r` 是父 view 像素区域，会先按 stored view transform 映射为虚拟屏幕 `screen_region`。随后查询通过 indexed Segment `screen_region` 元数据映射到 decoded Segment frame 的局部区域；缺少可映射元数据、相邻帧 screen region 不一致或无重叠时，该 frame pair 会进入 decode errors，而不是把屏幕坐标静默解释为 stored-frame 坐标。响应会带 `coordinate_caveat`；这使它适合先做廉价变化摘要，不适合被解释为实时屏幕定位、目标命中、因果成立或业务成功。

## 10. Time Seek 规则

输入：近似时间 `t`。

输出：

- `before`：时间小于等于 `t` 的最近帧；
- `after`：时间大于等于 `t` 的最近帧；
- `nearest`：绝对时间差最小的帧。

Host Agent 的 `/look q="frame" time.near` 可以优先尝试 `nearest`，再尝试 `before` / `after` / selected frames。若某个候选帧因旧坏段或 blob hash mismatch 解码失败，必须在 `decode_errors` 中留痕；若其它候选帧可解码，`decoded_review.selected_segment_frame` 必须标明实际采用的 Segment frame。这个回退只说明有可审阅的历史像素帧，不说明业务成功、因果成立或目标命中。

规则：

- seek 只查询已有 index，不触发新截图。
- 如果没有附近帧，返回 not found。
- 返回 frame restore ref，而不是必须立即导出 PNG。
- 调用方需要看图时，再调用 reader 还原目标帧或区域。

## 11. Frame Restore 规则

还原任意 frame：

1. 读取 manifest。
2. 找到目标 frame record。
3. 如果是 keyframe，直接读取 keyframe blob。
4. 如果是 pframe，找到最近 keyframe。
5. 从 keyframe 开始按 frame_index 顺序应用 delta/no-change 到目标帧。
6. 计算还原完整帧 hash。
7. hash 必须等于目标 frame 的 `full_frame_sha256`。

Reader 可以缓存中间还原结果，但缓存不是 canonical evidence。

## 12. Integrity / Hash 规则

必须支持：

- 每个 keyframe blob hash；
- 每个 delta blob hash；
- 每个 restored full frame hash；
- manifest hash；
- 可选 restore chain hash。

`integrity_ok=true` 只能说明 segment 结构与 hash 自洽，不表示业务成功、因果成立或目标命中。

## 13. Retention / Prune 规则

第一版按 segment 粒度清理：

- 超过 `retention_days` 的 segment 可进入候选。
- 被 evidence / receipt / replay / operation log 引用的 segment 不能删除。
- derived review artifact 可删除并按需重建。
- 删除必须写 prune report。
- 删除后 index 不得留下悬空引用。

本阶段不实现删除，只定义规则。

## 14. Canonical Evidence 与 Derived Review 边界

Canonical evidence：

- manifest；
- index；
- keyframe blobs；
- delta blobs；
- frame records；
- hash/integrity records；
- operation log 中引用 frame 的关系。

Derived review only：

- GIF；
- MP4 / WebM；
- cursor overlay；
- diff heatmap；
- annotated image；
- preview thumbnail；
- 临时 restored PNG cache；
- timeline player 导出的审阅片段。

Derived artifact 可以帮助人类或 AI 审阅，但不能替代 raw segment 作为证据真相源。

## 15. 与现有 Timeline Segment MVP 的关系

旧 `agentsight.tray.viewers.write_timeline_segment` 网页 review-bundle 路径已删除；历史说明如下：

- derived review bundle；
- 不拥有采集链路；
- 不作为 canonical evidence；
- 不执行 retention；
- 不替代 Segment v1 writer/reader。

Segment v1 是后续 P-F1 开始实现的 canonical storage 主线。

## 16. P-F1 最小实现边界

P-F1 只应实现：

- segment writer；
- segment reader；
- keyframe + pframe delta crop；
- restore by frame_id；
- hash 校验；
- 聚焦测试。

P-F1 不应接入 Host Agent 主链路，不应开始 Windows Service，不应实现 H.264，不应改变公共 `/screen` / `/look` / `/do` 语义。

P-F1 当前 Python API：

```python
from agentsight.segments import SegmentReader, SegmentWriter

writer = SegmentWriter.create("segment-demo", segment_id="seg-demo")
writer.add_frame(image, timestamp_iso="2026-06-21T00:00:00Z", timestamp_monotonic_ms=1000, source="idle")
manifest = writer.close()

image, report = SegmentReader("segment-demo").restore_frame("f000000")
assert report["hash_ok"]
```

`SegmentWriter` 使用 RGBA 像素 hash 作为 full-frame integrity scope，delta 判断基于 RGB 差分 bbox；保存的 keyframe/delta blob 是 PNG，但 MP4/GIF/WebM 仍只能作为 derived review export。

## 17. P-F2 第一切片：公共采集帧写入 Segment

P-F2 当前已完成的最小接入：

- Gateway 初始化 `SegmentFrameRecorder`；
- `_observe` 已经捕获并写入 evidence store 的 raw PNG 会同步写入当前 bucket Segment；
- `/look` 的 source 记录为 `look`；
- `/screen` 的 source 记录为 `screen`；
- `/do` 后置观察帧的 source 从旧 frame buffer 名称 `do_after_frame` 映射为 Segment v1 的 `post_do`；
- frame buffer entry 增加 `segment_frame`、`segment_id`、`segment_frame_id`、`segment_restore_ref`；
- `SegmentReader` 可以根据 `segment_restore_ref.frame_id` 还原完整 raw frame。

P-F2 仍保持的边界：

- Host Agent HTTP 层仍负责根据 `tray-config.jsonc` 注入默认 `post_observe`；
- Host Agent 已有轻量用户态 idle capture loop，用于按 tray-config 调度 `ProtocolGateway.capture_idle_frame`；
- 已按 `daily_segment_boundary_local_time` 支持本地日边界轮转 Segment；retention/prune 仍按独立清理阶段执行；
- time.near 的 segment-aware 解码/restore ref 优先逻辑属于 P-F3+，不改变 P-F2 的 capture 写入职责。

## 18. P-F3 第一切片：Time.near 返回 Segment Restore Ref

P-F3 当前已完成的最小接入：

- frame buffer entry 保留 `segment_frame`、`segment_id`、`segment_frame_id`、`segment_restore_ref`；
- `frames_near_time` 的 `before_frame` / `after_frame` / `nearest_frame` 会返回这些 segment 字段；
- `look(q="frame", time.near=...)` 不重新截图，只返回已有 frame index 中的 segment restore ref；
- 调用方需要看图时，可以用 `SegmentReader(segment_restore_ref.segment_path).restore_frame(segment_restore_ref.frame_id)` 还原 raw frame。
- Host Agent visual observe 和 public `/look` 顶层会返回 `segment_frame`（如果本次帧已写入 Segment），这是协议事实，不代表业务成功或目标命中。

P-F3 当前补强：

- 全局 Segment frame index 可扫描 `.agseg` 与 legacy manifest；
- `look(q="frame", time.near=...)` 可基于 Segment decoder 生成 `decoded_review` / `historical_view`，不重新截图；
- 新帧带 `screen_region` / `coordinate_system` 时，公共请求 `r` 会从屏幕坐标映射到段内 crop；旧帧缺元数据时才退回 `stored_frame_px` caveat；
- 解码失败或 no-overlap 候选必须写入 `decode_errors`，并可尝试其它 nearby candidate；
- 这仍然不判断业务成功、因果或目标命中。

## 19. P-F4 第一切片：Operation Log 与 Segment Frame 绑定

P-F4 当前已完成的最小接入：

- `post_observe.sampled_frames` 返回 `segment_frame`、`segment_frame_id`、`segment_restore_ref`；
- `public_operation_log_entry` 会从 response 中提取：
  - `frame_refs.post_action`；
  - `frame_refs.looked_frames`；
  - `segment_frame_refs`；
- operation log 仍保留 redacted request / response JSON；
- timeline attachment 现在优先按 `segment_frame_id` 匹配，其次按 media path，再按 nearest timestamp；
- 日志不会声称业务成功、目标命中或因果成立。

这仍然不是完整 P-F4/P-F6：

- 本地 MCP/Gateway 还没有直接写 tray operation log；
- timeline/log viewer 仍主要扫描现有 media 文件，尚未变成完整 segment-native UI；
- 日志和 segment 的双向关系已有数据字段，但还没有完整的用户态 Windows UI。

## 20. P-F5/P-F6 第一切片：Timeline / Operation Log 读取 Segment 数据

P-F5/P-F6 当前已完成的最小接入：

- `build_timeline_model` 会扫描 `%LOCALAPPDATA%\AgentSight\runs_host_agent\**\segments\segment-*\manifest.json` 与 `runs_host_agent/**/segments/*.agseg`；
- 对每个 Segment frame，使用对应 reader 生成本地 restored preview cache；
- restored preview 是 `derived_review_restored_segment_frame`，不是 canonical evidence；
- frame model 保留 `canonical_source=agentsight_segment_v1`、`segment_id`、`segment_frame_id`、`segment_restore_ref`；
- operation log attachment 会优先按 `segment_frame_id` 关联到 segment frame，再回退到 media path 或 nearest timestamp。

这仍然不是完整播放器：

- 时间线 UI 已切换为原生 PySide6/Qt viewer；
- 还没有 Windows 原生播放器窗口；
- 还没有播放速度控制和完整拖拽体验的真实 GUI 验收；
- restored preview cache 可以删除并按需重建，不是 evidence truth source。

## 21. P-F7：On-demand Review Artifact

P-F7 当前已实现：

- `export_segment_frame_crop(segment_path_or_dir, frame_id, region)`；
- `export_segment_frame_diff(segment_path_or_dir, before_frame_id, after_frame_id)`；
- `query_segment_timeline_diff(root, time.from/time.to, region)` 可在已有 Segment
  frame 上生成 metadata-first 时间线 diff，并按需导出少量 derived diff heatmap；
- `query_segment_review_clip(root, time.from/time.to, region)` 可在已有 Segment
  frame 上返回 clip frame refs，并按需导出一个 derived review GIF；
- 输入可以是 `.agseg` 单文件，也可以是 legacy directory Segment；
- 输出写入 Segment 的 `derived/` 目录；
- 报告字段明确：
  - `raw_or_derived=derived_review_only`；
  - `artifact_is_canonical_evidence=false`；
  - `canonical_evidence_source=agentsight_segment_v1`；
  - 不判断业务成功、因果或目标命中。

Public `/look q="diff"` 现在有两条语义：

- `mode=endpoints`：比较一个已有 view baseline 与最新同区域截图，会重新捕获最新像素；
- `mode=timeline` / `mode=timeline_with_artifacts`：只读取已有 Segment
  frames，不重新截图。`timeline` 返回 metadata-only；`timeline_with_artifacts`
  在 `max_artifacts>0` 时导出 derived review diff heatmap。

当 public timeline diff 使用 `src.type="screen"` 时，`r` 是虚拟屏幕区域；使用
`src.type="view"` 时，`r` 先按父 view 映射到虚拟屏幕区域，再通过 Segment
frame 的 `screen_region` / `coordinate_system` 映射到 stored-frame 局部区域。
缺少坐标元数据、不重叠或解码失败必须留在 `decode_errors` /
`artifact_errors` 中，不能退化成目标命中、因果或业务成功判断。

Public `/look q="clip"` 读取同一批 Segment index，不重新截图。它返回
`clip.frames` 作为 frame refs / metadata；当 `max_artifacts=1` 时导出一个
`review_clip_gif`，标记为 `derived_review_only`、
`artifact_is_canonical_evidence=false`。该 GIF 是变量时间戳 raw Segment
frames 的便利动画，第一版不承诺精确 real-time cadence，也不能作为业务成功、
因果或目标命中证据。

## 22. P-F8：Retention / Prune

P-F8 当前已实现：

- `plan_segment_prune(root, retention_days, now_iso, operation_logs)`；
- `apply_segment_prune_plan(plan)`；
- operation log 中出现的 `segment_frame_refs.segment_id` 或 `restore_ref.segment_path` 会 pin 对应 Segment；
- 未被 pin 且超过 `retention_days` 的 Segment 会进入 `would_delete`；
- apply 阶段只删除计划中的过期未引用 Segment 目录或 `.agseg` 文件，并写 `segment-prune-report.json`。

注意：

- 删除 Segment 目录或 `.agseg` 文件会删除其中 canonical raw keyframe/delta blobs，因此必须只对未引用、过期的 Segment 执行；
- derived restored preview / crop / diff 可随时删除并按需重建；
- prune report 不是业务判断，只是文件生命周期记录。

## 23. Global Segment Frame Index

当前实现：

- `build_global_segment_frame_index(root)` 扫描 `segments/segment-*/manifest.json` 与 `segments/*.agseg`；
- `query_segment_frames_near_time(index, requested_time)` 返回 before / after / nearest；
- 每个结果包含 `segment_restore_ref`，可交给 `SegmentReader` 还原；
- restore ref 的 `storage_format` 决定使用 `BinarySegmentReader` 还是 legacy `SegmentReader`；
- 不可读或损坏段进入 `skipped_segments` / `skipped_segment_count`，查询继续处理其它段；
- 精确命中同一帧时，before/after 选择集必须去重；
- 查询不重新截图、不发送输入、不判断业务。

边界：

- 全局 index 是可重建 metadata；
- raw Segment manifest/keyframe/delta blobs 才是 canonical evidence；
- index 完整不代表业务成功。

## 24. Idle Capture Tick And Daily Segment Rotation

当前实现：

- `ProtocolGateway.capture_idle_frame(policy, now_ms)` 是可被后台循环调用的 tick 方法；
- Host Agent 启动时会创建轻量 idle capture loop，周期读取 `tray-config.jsonc`，在策略允许时调用该 tick；
- 当 `continuous_recording_enabled=true` 且 `recording.idle_capture.enabled=true` 时，tick 会按 `idle_capture.fps` 判断是否需要采集；
- 采集到的帧按 `source=idle` 写入 Segment；
- `SegmentFrameRecorder` 会按帧时间戳和 `daily_segment_boundary_local_time` 计算本地日桶，跨桶时自动轮转到新的 Segment；字符串 ISO 时间戳也必须参与分桶。

注意：

- 这仍不是完整长期 ring buffer；它只是用户态 Host Agent 内的轻量采集循环；
- tick 不发送输入，不做 OCR/window semantics/business 判断；
- idle FPS 最低支持 0.1，即 10 秒一帧。

## 25. P-F9：Single-File `.agseg` Binary Segment

P-F9 第一切片实现边界：

- 新增 `BinarySegmentWriter` / `BinarySegmentReader`；
- 输出一个 `.agseg` 单文件；
- 文件内部保存 keyframe PNG blob、P-frame delta crop PNG blob、manifest/index/blob table；
- 支持按 `frame_id` 还原完整帧并校验 RGBA hash；
- 支持导出完整 PNG；
- 支持按区域取图、缩放和模糊，输出标记为 `derived_review_only`；
- 保持所有边界否定字段；
- 不接入 Windows Service；
- 不改变 `/screen` / `/look` / `/do` 公共协议；
- 不删除旧目录式 Segment，也不自动清理用户旧数据。

P-F10 第一切片当前实现：

- `SegmentFrameRecorder` 默认 `storage_format="binary_agseg"`；
- 仍可显式使用 `storage_format="proto_directory"` 保留旧目录式写入；
- `record_frame` 返回 `storage_format`、稳定 `.agseg` 路径和包含 storage_format 的 `restore_ref`；
- writer 每帧后 flush footer，活跃 `.agseg` 可被 `BinarySegmentReader` 打开；
- writer 可通过 `open_or_create` 打开已有 `.agseg`，从最后一帧继续追加 P-frame，重启后不覆盖同一 bucket 文件；
- timeline model 扫描 `runs_host_agent/**/segments/*.agseg`，解码 restored preview cache，preview 仍是 `derived_review_restored_segment_frame`；
- 按天轮转沿用现有 bucket，另支持 `segment_bucket_granularity="hourly"` 的小时桶命名；默认二进制文件名为 `agentsight-{bucket}.agseg`；
- 全局 Segment frame index 扫描 `.agseg` 并返回带 `storage_format="binary_agseg"` 的 restore ref；
- retention/prune 能按 `.agseg` 文件粒度清理过期未引用 Segment，并保护 operation log 引用的 `.agseg`；
- `agentsight.segments.decoder` 提供解码器式接口：按 restore ref 导出整帧 PNG、导出区域/缩放/模糊 PNG、导出 before/after diff heatmap、按时间查询最近帧、按相邻帧查询 metadata-only 变化摘要；`agentsight-segment-decoder` 是对应本地 CLI。`diff` 子命令可用 `--segment-path` 比较同段两帧，或用 `--before-segment-path` / `--after-segment-path` 比较跨 bucket 两帧；可附加 `--x/--y/--w/--h` 限定比较区域；`changes` 子命令返回变化摘要和可选连续变化段 `change_runs`，不导出媒体；
- Public `/look q="frame" time.near` 可用 Segment decoder 为 nearest frame 生成 `decoded_review` / `historical_view`，不重新截图、不发送输入；Gateway/MCP 路径会先查内存 frame buffer，未命中时回退到 Segment decoder；当 Segment frame 有可映射的 `screen_region` / `coordinate_system` 元数据时，公共请求 `r` 按屏幕区域坐标解释并映射为段内裁剪区域；旧帧缺元数据时才退回 `stored_frame_px` caveat；
- 公共 `/screen` / `/look` / `/do` 不新增语义判断。

P-F10 后续切片才考虑：

- 将目录式 proto-segment 标为 legacy storage。


