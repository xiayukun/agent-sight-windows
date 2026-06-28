# 用户指南

中文 | [English](user-guide.en.md)

## 适合谁

AgentSight for Windows 面向需要真实 Windows GUI 控制能力的 AI Agent。它不是远程桌面，不是 OCR 工具，也不是后台业务 API。

## 第一次使用

开发态：

```powershell
$env:PYTHONPATH = "src"
py -m ai_control.session_supervisor run --host 127.0.0.1 --port 8765 --arm-real-input
```

打包态目标：

```text
AIControlSessionSupervisor.exe run
```

## AI 公共链路

```text
read discovery -> /screen -> /look -> /do -> /look
```

- `/screen`：返回坐标、readiness、`screen_frame_index`。
- `/look`：返回低成本全屏或局部视图，使用 `scale_down`、region、`view_id` 聚焦。
- `/do`：基于 `view_id` 执行人类等价输入。
- `/look time.near`：按近似时间回看附近索引帧。

## 托盘控制面

Tray GUI 是给人类看的控制面：

- ready / paused / emergency / blocked / discovery_missing / unknown 状态图标；
- 右键菜单；
- 暂停 AI 控制；
- 允许 AI 控制；
- 紧急停止；
- 采集与保留设置；
- 打开时间线；
- 查看操作日志；
- 停止 AgentSight；
- 语言：跟随系统、中文、English。

语言设置保存在：

```text
%LOCALAPPDATA%\ai-control\tray-settings.json
```

录制/时间线策略保存在：

```text
%LOCALAPPDATA%\ai-control\tray-config.jsonc
```

`采集与保留设置` 会打开现代可滚动 Windows 设置窗，按当前托盘语言显示，并把保存结果写入 `tray-config.jsonc`。配置只保留用户需要调的采集策略和保留天数；平时采集使用 FPS，最低 0.1 FPS（10 秒 1 帧）；`post_observe_defaults` 不再作为用户配置，`/do` 后置观察直接按操作后 FPS、持续时间和最大帧数执行。托盘菜单不再暴露零散录制策略开关，所有采集与保留项都集中在设置窗里，不弹确认框，不发送鼠标键盘输入，也不判断任何业务结果。运行时 Segment 默认写入 `.agseg` 单文件二进制存储，采用 keyframe + P-frame delta crop；`.agseg` raw data、manifest 和 hash 是 canonical evidence。`打开时间线` / `查看操作日志` 会启动原生 PySide6/Qt `AgentSightTimelineViewer`，默认只读取 `.agseg` 索引和 operation log，选中帧时直接在内存中解码显示；不默认生成 HTML、PNG、GIF 或 review bundle。Qt 预览和显式 look-preview cache 都是人类回看的 derived review artifact，不代表工具判断目标命中、因果成立或业务成功。

## 证据和隐私

证据可能包含真实屏幕内容。分享或发布前必须脱敏。


