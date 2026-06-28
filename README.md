# AgentSight for Windows

中文 | [English](README.en.md)

![Platform](https://img.shields.io/badge/platform-Windows-blue)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)
![Boundary](https://img.shields.io/badge/boundary-pixel--grounded-green)

AgentSight for Windows 是一个给 AI Agent 使用的 Windows 像素级观察与人类等价操作宿主。它让 Windows AI agent / computer use agent 通过真实屏幕像素观察，通过鼠标键盘行动，并保留 evidence / replay / integrity 证据链。

一句话介绍：

> A pixel-grounded observe-and-act host for Windows AI agents.

历史兼容名：当前 Python 包、命令、数据目录和部分 UI 文案仍使用 `ai_control`、`ai-control` 或 `AI-Control`。公开产品名采用 `AgentSight for Windows`，仓库名推荐 `agent-sight-windows`；不要在当前活跃工作区中直接重命名本地目录。

## 关键词

AgentSight for Windows, AI-Control, Windows AI agent, computer use, GUI automation, pixel-grounded control, observe and act, human-equivalent input, evidence / replay / integrity, visual memory, attention toolbox, Windows Host Agent, AI 控制, Windows 图形界面控制, 像素级观察, 人类等价鼠标键盘操作, 证据链, 回放, 视觉记忆系统。

## 产品边界

AgentSight 只做两件事：

- 像人一样看：返回真实 Windows GUI 像素、坐标、时间、证据路径和视觉记忆索引。
- 像人一样动：在 Host Agent 就绪且操作者允许时，发送鼠标/键盘式输入。

AgentSight 不做：

- OCR；
- clipboard 读写；
- DOM / accessibility tree / window semantics；
- cmd/shell 作为 GUI 替代；
- 隐藏应用 API；
- 目标命中、因果成立、业务成功判断。

所有 UI 语义仍由调用方 AI 基于返回像素自行判断。

## 当前形态

当前项目仍是 research/MVP，但已经具备可演示的主链路：

- 用户态 `AIControlSessionSupervisor` 管理 Host Agent 与 Tray GUI 生命周期。
- Host Agent 暴露 `screen -> look -> do -> look` 公共 GUI 控制流。
- `/screen`、`/look`、`/do` 返回内嵌 readiness，不要求普通 AI 先调用 `/health`。
- `/look` 支持 `scale_down`、region、`view_id`，用于视觉注意力逐步聚焦。
- `/do` 使用 `basis.view_id`，鼠标移动和点击拆分，避免隐藏坐标跳跃。
- `/do` 未显式传 `post_observe` 时，Host Agent 可按托盘录制策略自动应用 bounded 动作后观察窗口。
- 真实输入、截图、证据、回放、完整性报告均保持可复核。
- visual memory / attention toolbox 支持按近似时间 `time.near` 查询附近索引帧，也支持通过 public `/look q="changes"` 查询已有 Segment 的 metadata-only 变化摘要，通过 `/look q="diff" mode="timeline"` / `timeline_with_artifacts` 对已有 Segment 时间窗做按需差分审阅，并通过 `/look q="clip"` 导出派生审阅 GIF。
- Tray GUI 是人类可见控制面，支持状态图标、右键菜单、暂停/允许、紧急停止、语言切换，以及采集与保留设置入口。
- 托盘录制配置使用 `%LOCALAPPDATA%\ai-control\tray-config.jsonc`，只表达用户需要调的采集策略和保留天数，例如 idle FPS、动作前后帧、操作后 FPS、操作后持续时间。时间线必须启用，操作日志必须保存，不再作为用户开关。
- 托盘里的采集与保留设置使用现代可滚动 Windows 设置窗，按当前默认语言显示并写入 `tray-config.jsonc`；时间线和操作日志现在由原生 PySide6/Qt `AgentSightTimelineViewer` 打开，直接读取 MKV sidecar frame index 并按选中帧在内存中解码预览；默认不生成 HTML、PNG、GIF 或 timestamped review bundle。MKV + `.frames.jsonl` + operation log 是当前 canonical storage，Qt 预览只是 derived review only。完整 ring buffer / 长期视频归档仍是后续方向。
- `/look` 返回的图片默认是本次响应里的 MCP image content；operation log 只记录 `look_preview_refs` 元数据。需要人工复核某次 AI 实际看到的局部图时，可显式运行 `ai-control-tray look-preview materialize --log-index N --preview-index M` 生成可清理的 derived review cache；这不是 canonical evidence，也不是新的 AI 视觉接口。
- 视频化存储主线已改为 MKV VFR：canonical 使用 `runs_host_agent/segments/*.mkv`，每个视频旁边写入 `.frames.jsonl` 帧索引和 `.manifest.json` 小型清单。公共 `/screen`、`/look`、`/do` 后置观察 raw frames 会写入 MKV，Host Agent visual observe 与 public `/look` 会透出 `segment_frame`，operation log 能提取 `segment_frame_refs`，时间线模型能读取 MKV 索引并按需用 FFmpeg 解码选中帧。旧 `.agseg` 自定义格式不再作为默认运行态存储。

## AI 推荐使用流程

不要每次都请求全屏高清图。推荐让 AI 像使用注意力一样逐步聚焦：

1. 读取 discovery。
2. 调用 `/screen` 获取坐标、readiness，并写入 `screen_frame_index`。
3. 调用 `/look` 获取低成本全屏或大区域预览，使用 `scale_down`。
4. 基于像素自行选择区域，再用 `view_id` 或 crop 请求高清局部图。
5. 调用 `/do` 执行人类等价鼠标/键盘动作。
6. 查看后置连续帧、diff 或 receipt。
7. 必要时用 `time.near` 回看某个近似时间点附近的帧，或用 `/look q="changes"` / `/look q="diff" mode="timeline"` / `/look q="clip"` 先看已有 Segment 的变化摘要、按需差分图和派生审阅片段。

## 安装与运行方向

当前不急着做 Windows Service 或完整 MSI/NSIS 安装器。推荐架构是：

```text
Startup / Run Key
  -> AIControlSessionSupervisor
       -> AIControlHostAgent
       -> AIControlTrayGui
```

开发态仍可使用源码入口：

```powershell
$env:PYTHONPATH = "src"
py -m ai_control.session_supervisor run --host 127.0.0.1 --port 8765 --arm-real-input
```

未来打包态使用：

```text
AIControlSessionSupervisor.exe
AIControlHostAgent.exe
AIControlTrayGui.exe
AIControlInstaller.exe
```

自启入口只能注册 Supervisor，Host Agent 和 Tray GUI 不再作为长期独立自启入口。

## 文档

- [用户指南](docs/user-guide.md)
- [Hermes / 新 Agent 接手指南](docs/HERMES_ONBOARDING.md)
- [发布检查清单](docs/release-checklist.md)
- [仓库配置建议](docs/repository-profile.md)
- [Screen / Look / Do 协议](docs/SCREEN_LOOK_DO_PROTOCOL.md)
- [视觉记忆与注意力系统愿景](docs/visual-memory-and-attention.md)
- [品牌与工作区迁移](docs/branding-and-workspace-migration.md)

## 开发验证

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

构建当前 PyInstaller packaged layout：

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

## 发布状态

GitHub Actions 将在 `v*` tag 或手动触发时运行测试、构建 Windows exe、生成 SHA256 checksums 并上传 release artifacts。发布说明以中文为主，英文附在下方或链接到英文镜像。

