# Hermes 接手指南

中文 | English: not yet mirrored

本文件用于让 Hermes 或其它新 coding agent 快速接手 AgentSight for Windows。它只记录项目基本情况、当前主线、开发规范和最近已知上下文；具体 API 细节仍以 `AGENTS.md`、`README.md`、`src/agentsight/adapters/skill/SKILL.md` 和源码为准。

## 1. 项目一句话

AgentSight for Windows 是一个给 AI agent 使用的 Windows 像素级 observe-and-act 宿主：AI 通过真实屏幕像素观察，通过人类等价鼠标键盘输入行动，并保留可复核的事件、帧索引和证据链。

历史兼容名仍然存在：Python 包名是 `agentsight`，部分 exe / 数据目录仍使用 `AgentSight` / `agentsight`。不要在当前工作区直接重命名 `C:\git\其他\AgentSight`。

## 2. 必须遵守的边界

工具只能做：

- 返回真实 GUI 像素、区域、坐标、时间、readiness、blocker、事件数、帧引用；
- 在 Host Agent ready 且操作者允许时发送鼠标/键盘式输入；
- 记录 operation log、MKV 帧索引、manifest、derived review 预览。

工具不能做：

- OCR；
- clipboard 读写；
- DOM；
- accessibility tree；
- window semantics；
- cmd/shell 作为 GUI 替代；
- hidden app API；
- 目标命中判断；
- 因果判断；
- 业务成功判断。

任何“看起来点中了/发送了/完成了”的判断都只能是调用方 AI 或人类基于像素的外部判断，不能由 AgentSight 工具字段自证。

## 3. 当前公共调用链

普通 AI 只走：

```text
read discovery -> /screen -> /look -> /do -> /look
```

MCP public tools 只应暴露 `screen`、`look`、`do`。不要把 `/health` 恢复成普通 AI 的必经预检；`/health` 只给 Tray、Supervisor、installer 和诊断使用。

每个 `/screen`、`/look`、`/do` 都应内嵌 readiness，例如 `code`、`service_status`、`can_attempt_real_control`、`control_blockers`。遇到锁屏、UAC secure desktop、operator pause、emergency stop、caller lock、capture/input unavailable 等 blocker 时，必须停止并报告。

## 4. 当前运行架构

当前推荐用户态架构：

```text
HKCU Run key / Startup
  -> AgentSightSupervisor
       -> AgentSightHostAgent
       -> AgentSightTray
```

职责：

- `AgentSightSupervisor`：当前登录用户会话中的生命周期管理者。
- `AgentSightHostAgent`：提供 `/screen`、`/look`、`/do`、截图、输入、readiness 和记录。
- `AgentSightTray`：人类可见控制面，含状态、暂停/允许、紧急停止、采集与保留设置、打开时间线。
- `AgentSightTimelineViewer`：原生 PySide6/Qt 时间线窗口。

当前不要做 Windows Service、SYSTEM、driver、UAC secure desktop 控制、`WTSGetActiveConsoleSessionId + CreateProcessAsUser`。

## 5. 当前存储主线

当前 canonical storage 是 MKV VFR，不是 `.agseg`。

默认运行态应主要写：

```text
%LOCALAPPDATA%\AgentSight\runs_host_agent\segments\
  agentsight-YYYYMMDD-HH-001.mkv
  agentsight-YYYYMMDD-HH-001.frames.jsonl
  agentsight-YYYYMMDD-HH-001.manifest.json
```

说明：

- 一个“段组”通常是 1 个 `.mkv` + 1 个 `.frames.jsonl` + 1 个 `.manifest.json`。
- 正常同一小时、同一分辨率、同一 Host 运行态应尽量只生成一个 MKV 段组；异常重启、旧残留、分辨率变化、避免覆盖旧文件时可以出现 `002`、`003` 后缀。
- 普通 `/screen`、`/look`、`/do` 不应默认写 `session-*/media/*.png|*.bmp|*.gif`。
- HTML 时间线、批量 PNG preview、旧 `.agseg` 自定义格式不是当前默认运行态主线。
- Qt 预览、diff heatmap、GIF、显式 cache 都是 derived review only，不是 canonical evidence。

如文档中出现 `.agseg` 作为主线的旧描述，以本节和当前源码为准；`.agseg` 相关内容只当历史/legacy 背景处理，除非用户明确要求迁移或清理。

## 6. 当前时间线 Viewer 状态

时间线已从网页改为原生 PySide6/Qt `AgentSightTimelineViewer`。

最近修过的重点：

- 初始进入时会选最后一帧并 prime 一下 Qt 视频画面，避免只加载不渲染。
- 播放倍率按真实时间轴 `timestamp_ms` 计算，不按 MKV 内部 25fps 或帧序号计算。
  - 10 秒 1 帧时，1x 应等约 10 秒再切下一帧。
  - 1 秒 10 帧时，1x 应按 10 FPS 推进。
- 滑条和柱图都应使用真实时间轴。
- 点击柱子或拖动进度条必须暂停播放。
- 播放过程中，选中柱要自动滚入可视区域。
- 初始化最后一帧的异步 seek/prime 回调可能覆盖用户点击；当前用 `_seek_generation` 防止旧回调生效。
- 往前加载 500 帧后必须保持同一帧身份，而不是保持旧 index；例如原来 `400/500` 对应的同一帧，加载后应接近 `900/1000`。
- 点击柱子跳转路径应是 `frame -> segment_path -> playback_pts_ms`，而不是只按全局 index 猜。

如继续改时间线，优先读：

- `src/agentsight/tray/timeline_viewer.py`
- `src/agentsight/tray/viewers.py`
- `src/agentsight/segments/mkv_container.py`
- `src/agentsight/segments/recorder.py`

## 7. 开发与验证

常用验证：

```powershell
$env:PYTHONPATH = "src"
py -B -m compileall -q src\AgentSight
py -B -m unittest tests.acceptance.test_mkv_segment_storage
py -B -m unittest tests.acceptance.test_p3a_screen_look_do_protocol
py -B -m unittest tests.acceptance.test_p1g_tray_gui_control_surface
```

重打包时间线：

```powershell
py -m PyInstaller --noconfirm packaging\pyinstaller\AgentSightTimelineViewer.spec
```

重打包 Host/Tray/Timeline：

```powershell
py -m PyInstaller --noconfirm packaging\pyinstaller\AgentSightHostAgent.spec
py -m PyInstaller --noconfirm packaging\pyinstaller\AgentSightTray.spec
py -m PyInstaller --noconfirm packaging\pyinstaller\AgentSightTimelineViewer.spec
```

不要把 `build/`、`dist/`、`runs*`、本地 evidence/cache 提交或当成源码文档。

## 8. 本地缓存与清理

运行数据在：

```text
%LOCALAPPDATA%\AgentSight
```

可保留的小状态文件通常包括：

- `host-agent.json`
- `session-supervisor-state.json`
- `service-state.json`
- `operator-control-policy.json`
- `tray-settings.json`
- `tray-config.jsonc`
- `unified-session-supervisor.enabled`

大型缓存/证据主要是：

- `runs_host_agent/segments/*.mkv`
- `runs_host_agent/segments/*.frames.jsonl`
- `runs_host_agent/segments/*.manifest.json`
- 旧 `runs_*` / preview cache / evidence package。

清理前先停 Supervisor / Host Agent / Tray GUI，避免删正在写入的文件。PyInstaller `_MEI*` 临时目录异常堆积也可能撑爆 C 盘，需要单独清理 `%TEMP%\_MEI*`。

## 9. 当前用户偏好

- 不再启动审核 subagent。
- 不再写 `docs/reviews` 审核文件。
- 阶段性开发可以内部小步，但对外按较大的工程交付块汇报。
- 输出尽量简洁。
- 用户更愿意亲自做 GUI 功能验收；除非用户明确要求，不要主动长时间操作电脑。
- 如果需要用户处理系统弹窗、重启、锁屏、UAC 等，会阻断时先说明。

## 10. Hermes 接手建议

接手顺序：

1. 读 `AGENTS.md`。
2. 读本文件。
3. 读 `README.md`。
4. 读 `src/agentsight/adapters/skill/SKILL.md` 中 public `/screen` / `/look` / `/do` 和 Tray/Timeline 相关部分。
5. 针对具体任务再读相关源码，不要一开始全仓库漫游。

遇到文档冲突时：

- 当前 MKV VFR 主线优先于旧 `.agseg` 主线描述。
- public `screen/look/do` 优先于 legacy/internal tool。
- 项目边界优先于“方便实现”。
- 真实像素和事件事实优先于工具内成功判断。
