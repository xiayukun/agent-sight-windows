# AgentSight 1.0.0 公共架构方案

中文 | [English](agentsight-1.0.0-public-architecture.en.md)

本文固化 AgentSight 首次正式上线版本的公共命名、安装器、MCP / Skill、安装目录和 release 产物架构。结论基于看板父卡 PM 约束：1.0.0 首发不保留公开历史包袱，公开产品、包名、命令、exe、目录、MCP 和 Skill 全部统一为 AgentSight / agentsight。

## 1. 决策摘要

- 首发版本号：`1.0.0`，Git tag 使用 `v1.0.0`。
- GitHub 仓库：`git@github.com:xiayukun/agent-sight-windows.git`。
- 公开产品名：`AgentSight for Windows`。
- Python distribution：`agentsight`。
- Python import package：`agentsight`。
- 命令前缀：`agentsight-*`，不得发布历史命令作为 1.0.0 公开入口。
- 主安装器：`AgentSightSetup.exe`。
- 用户级安装目录：`%LOCALAPPDATA%\AgentSight`。
- 默认监听：只绑定 `127.0.0.1`，不默认开放公网端口。
- 运行形态：仍是当前用户态 Session Supervisor，不做 Windows Service、SYSTEM、driver 或 UAC secure desktop 控制。
- 普通 AI 公共链路：`read discovery -> screen -> look -> do -> look`；MCP public tools 只暴露 `screen`、`look`、`do`。
- 证据语义：MKV / frame index / operation log 只证明像素与事件事实，不证明目标命中、因果成立或业务成功。

## 2. 命名矩阵

| 层级 | 1.0.0 目标 | 当前来源 / 迁移点 |
| --- | --- | --- |
| 仓库 | `agent-sight-windows` | 当前本地目录可暂不重命名，GitHub remote 指向新仓库。 |
| Python distribution | `agentsight` | `pyproject.toml [project].name` 已统一为 `agentsight`，版本为 `1.0.0`。 |
| Python import package | `agentsight` | 源码包已统一到 `src/agentsight`，全仓导入使用 `agentsight.*`。 |
| Console scripts | `agentsight-*` | 公开 console scripts 仅使用 `agentsight-*` 前缀。 |
| MCP server | `agentsight-mcp` / `AgentSightMcp.exe` | MCP tool names 保持 `screen`、`look`、`do`；安装包提供 `AgentSightMcp.exe` 作为 AI 配置入口，内部构建仍可保留 `AgentSightMcpServer.exe` 兼容副本。 |
| Host Agent | `AgentSightHostAgent.exe` | 当前 `AgentSightHostAgent.exe` spec、测试和 release workflow 迁移。 |
| Supervisor | `AgentSightSupervisor.exe` | 当前 `AgentSightSupervisor.exe` 迁移，仍是唯一长期自启入口。 |
| Tray GUI | `AgentSightTray.exe` | 人类控制面入口统一为 `AgentSightTray.exe`，内部可保留 CLI 子命令但不作为首屏 release 入口。 |
| Timeline viewer | `AgentSightTimelineViewer.exe` | 已基本符合命名，保留。 |
| Diagnostics | `agentsight-doctor` / `AgentSightDoctor.exe` | first-use doctor、capture smoke、release readiness 合并为诊断入口，减少 release asset 数量。 |
| Segment decoder | `agentsight-segment-decoder` | 只读本地 MKV / index / derived review，不截图、不发送输入、不判断业务。 |
| 数据目录 | `%LOCALAPPDATA%\AgentSight` | `default_agent_dir()`、discovery、service state、caller lock、tray config、runs 路径统一迁移。 |
| Discovery | `%LOCALAPPDATA%\AgentSight\host-agent.json` | schema 建议改为 `agentsight_discovery_v1`；只暴露 `url`、`token`、`api.screen/look/do` 和诊断 health URL。 |
| Skill | `ai-install\AgentSight\SKILL.md` | 当前 `src/agentsight/adapters/skill/SKILL.md` 迁移为 AgentSight 名称并打包到安装产物。 |

## 3. 安装目录与运行布局

推荐安装布局：

```text
%LOCALAPPDATA%\AgentSight\
  app\
    1.0.0\
      AgentSightSupervisor.exe
      AgentSightHostAgent.exe
      AgentSightTray.exe
      AgentSightTimelineViewer.exe
      AgentSightMcp.exe
      AgentSightMcpServer.exe
      AgentSightDoctor.exe
      agentsight-package-metadata.json
  current\
    ... 指向或复制当前版本 app\1.0.0 的可执行文件 ...
  data\
    host-agent.json
    service-state.json
    session-supervisor-state.json
    operator-control-policy.json
    caller-lock.json
    tray-settings.json
    tray-config.jsonc
    runs_host_agent\segments\*.mkv
    runs_host_agent\segments\*.frames.jsonl
    runs_host_agent\segments\*.manifest.json
  ai-install\
    AGENTSIGHT_AI_INSTALL_PROMPT.txt
    mcp.json
    SKILL.md
    README_FOR_AI.md
    mcp.config.example.json
    PROMPT_FOR_AI.md
    README.md
    agentsight\SKILL.md
  logs\
  uninstall\
    AgentSightUninstall.exe
```

规则：

- `app\<version>` 是只读程序目录；`data` 是运行状态和证据目录；`ai-install` 是给用户复制给任意 AI 的安装说明包。
- `current` 可用复制目录实现，避免首版引入 symlink 权限问题；后续再评估 junction/symlink。
- 自启只注册 `AgentSightSupervisor`，指向 `current\AgentSightSupervisor.exe run ...`。
- 卸载默认移除 Run Key / Startup / 程序文件，保留 `data\runs_host_agent` 证据；只有用户明确选择时才清理证据。
- 安装器不得写入 Program Files，不要求管理员权限。

## 4. AgentSightSetup.exe 行为

`AgentSightSetup.exe` 是 1.0.0 唯一推荐下载入口。

主流程：

1. 解压内嵌 payload 到 `%LOCALAPPDATA%\AgentSight\app\1.0.0`。
2. 写入或刷新 `%LOCALAPPDATA%\AgentSight\current`。
3. 生成默认 `data\tray-config.jsonc`、`data\operator-control-policy.json` 和必要小状态目录。
4. 注册 HKCU Run Key：名称 `AgentSight`，命令指向 `current\AgentSightSupervisor.exe run --host 127.0.0.1 --port 8765 --arm-real-input`。
5. 启动 Supervisor；Supervisor 拉起 Host Agent 和 Tray。
6. 等待 discovery / readiness，写安装报告到 `data\last-install-report.json`。
7. 生成 `ai-install` 包。
8. 弹出安装完成窗口，显示状态、安装目录、是否已注册自启、以及“复制给 AI”的提示语。

安装完成窗口的复制提示应短、具体、可执行，例如：

```text
AgentSight for Windows 已安装在本机。请读取下面目录中的 mcp.json、SKILL.md 和 README_FOR_AI.md，然后把 AgentSight 作为本地 MCP 工具接入当前 AI 客户端。接入后只使用 screen、look、do 三个工具，通过真实屏幕像素观察，用鼠标键盘行动，并保留证据记录；不要使用 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API 或 shell 作为 GUI 替代。

安装资料目录：%LOCALAPPDATA%\AgentSight\ai-install
请先读：README_FOR_AI.md
```

## 5. MCP 与 Skill 交付

`ai-install` 不是运行时语义通道，只是给用户和任意 AI 客户端的安装资料包。

建议文件：

- `AGENTSIGHT_AI_INSTALL_PROMPT.txt`：用户复制给任意 AI 的短提示，要求 AI 读取同目录配置与 Skill。
- `mcp.json`：首选 stdio MCP 配置，server 名称 `agentsight`，command 指向 `AgentSightMcp.exe` 绝对路径，不含 token。
- `SKILL.md`：普通 AI 使用 AgentSight 的权威操作手册。
- `README_FOR_AI.md`：给接入 AI 的安装说明：如何合并 MCP、如何安装 Skill、只使用 `screen` / `look` / `do`。
- `mcp.config.example.json`、`PROMPT_FOR_AI.md`、`README.md`、`agentsight/SKILL.md`：兼容副本。

Skill 必须保留边界：

- 不做 OCR、clipboard、DOM、accessibility tree、window semantics、hidden app API、shell 替代 GUI。
- `host_sent_event_count>0` 只说明事件被发送或插入，不说明点中目标。
- `integrity_ok=true` 只说明证据结构一致，不说明业务成功。
- 像素变化只说明像素变化，不说明输入导致变化。

## 6. Release asset 形态

1.0.0 release 推荐上传：

```text
AgentSightSetup-1.0.0-windows-x64.exe
AgentSightSetup-1.0.0-windows-x64.sha256.txt
SHA256SUMS.txt
```

可选上传一个面向高级用户的 zip，但不作为首屏推荐：

```text
AgentSight-1.0.0-windows-x64-portable.zip
```

不建议把 Host、Tray、Supervisor、MCP server 等内部 exe 全部作为 GitHub Release 首屏资产。它们应打包进 `AgentSightSetup.exe`，避免用户误点单个内部组件。

GitHub About 建议：

```text
Local Windows host for AI agents: screen pixels in, mouse and keyboard out, with audit logs and replayable evidence. Built for computer-use workflows, not hidden app APIs.
```

Topics：

```text
windows, windows-ai, ai-agents, computer-use, gui-automation, desktop-automation, mcp, model-context-protocol, pixel-grounded, screen-observation, mouse-keyboard, human-input, audit-trail, replay, evidence, local-first, python, pyinstaller, agent-tools, windows-gui
```

## 7. 迁移实施路径

本阶段只产出架构；后续工程卡按以下顺序实现，每块应能独立验证，且单卡不超过 2 天。

### A. 保护与基线

- 记录 `git status --short`，确认当前已有 modified/untracked。
- 不执行 `git reset`、批量删除或强制清理。
- 对每个工程卡，只修改验收范围内文件；如需要生成 build/dist，必须确保不把产物当源码交付。

### B. 机械命名迁移

- `src/agentsight` -> `src/agentsight`。
- 全仓 import、entry points、schema object type、CLI 文案、测试引用迁移到 AgentSight / agentsight。
- 移除 `agentsight-*` console script 公开入口。
- 聚焦验证：`python -m unittest tests.acceptance.test_p3a_screen_look_do_protocol tests.acceptance.test_p1x_session_supervisor tests.acceptance.test_packaging_round7`。

### C. 安装器与目录迁移

- 引入 `AgentSightSetup.exe` 构建入口。
- 将默认数据目录改为 `%LOCALAPPDATA%\AgentSight`。
- HKCU Run Key 名称改为 `AgentSight`。
- 安装器生成 `ai-install` 包并弹出复制提示。
- 卸载默认保留证据目录。

### D. MCP / Skill / 文档迁移

- MCP server 入口统一为 `agentsight-mcp` / `AgentSightMcp.exe`；`AgentSightMcpServer.exe` 只作为内部构建/兼容副本。
- Skill frontmatter name 改为 `agentsight`。
- README、用户指南、release checklist、GitHub checklist、release notes 模板移除首屏历史别名叙述。
- 文案避免 unlock、seamless、revolutionary、empower、cutting-edge、robust、transform、ensure success 等营销腔。

### E. Release workflow

- release workflow 改为检查 `v1.0.0` / `v*` tag，构建 `AgentSightSetup-<tag>-windows-x64.exe`。
- 先测试，测试失败不发布。
- 生成 SHA256。
- Release notes 中文在上，英文在下或链接英文镜像。

## 8. 测试矩阵

| 目标 | 命令 / 检查 | 验收 |
| --- | --- | --- |
| Python 导入 | `python -c "import agentsight; print(agentsight.__version__)"` | 输出 `1.0.0`。 |
| 公共协议 | `python -m unittest tests.acceptance.test_p3a_screen_look_do_protocol` | `screen/look/do` readiness 与边界字段保持。 |
| Supervisor | `python -m unittest tests.acceptance.test_p1x_session_supervisor` | 自启命令、状态报告、stop/uninstall 语义正确。 |
| Tray | `python -m unittest tests.acceptance.test_p1g_tray_gui_control_surface` | 暂停、允许、急停、设置和 timeline 入口仍可用。 |
| Packaging | `python -m unittest tests.acceptance.test_packaging_round7` | spec、expected outputs 和 wrapper 文案统一。 |
| MKV storage | `python -m unittest tests.acceptance.test_mkv_segment_storage tests.acceptance.test_pf2_idle_capture_and_rotation` | MKV VFR canonical storage 不退回 PNG/GIF 主线。 |
| Installer dry run | 安装到临时 `%LOCALAPPDATA%` 覆盖目录 | 生成 app/current/data/ai-install，Run Key 命令正确，默认绑定 127.0.0.1。 |
| Public old-name gate | grep 公共文档、pyproject scripts、spec name、release workflow asset | 不再出现作为公开入口的 `agentsight` / `AgentSight` / `agentsight`。 |
| Release | GitHub Actions release job | 测试先过，再生成 setup exe 和 checksum。 |

## 9. 风险与保护策略

- 大规模包名迁移风险：先做机械 rename，再做行为改动；每轮跑聚焦测试，失败时只回退当前小改动，不 reset 全仓。
- 当前工作区已有大量未提交改动：所有后续卡必须先盘点状态，不能删除或覆盖未知文件。
- 运行中 AgentSight 风险：安装器和自启改动只影响 AgentSight 进程，不触碰 Hermes / gateway；涉及真实重启时记录命令和结果。
- 数据目录迁移风险：1.0.0 首发不承诺自动迁移旧 `%LOCALAPPDATA%\AgentSight`；如检测到旧目录，只提示用户存在旧数据，不自动删除。
- 证据泄露风险：release workflow 和 docs 明确禁止上传 runs、screenshots、video、Chrome profile、token、本地 evidence。
- 命名泄漏风险：允许源码历史里存在 Git 历史，不允许 1.0.0 工作树公开入口、README、release asset 和 ai-install 里继续把旧名作为主入口。

## 10. 回滚方案

- 未发布前：停止当前工程卡改动，保留 git diff 供审阅；不要 `git reset --hard`，由看板后续卡决定如何回退。
- 本机安装失败：运行 `AgentSightUninstall.exe` 或 `AgentSightSupervisor.exe uninstall` 移除 Run Key 和程序文件；默认保留 `data`。
- Release 失败：不创建或撤回 `v1.0.0` release asset；修复后重新跑 workflow。
- 发现旧名泄漏：作为 release blocker 修复，重新构建 setup 和 SHA256。

## 11. 后续卡交接重点

已有下游卡应按本方案执行：

- `t_d9f82671`：统一公开命名，移除首发历史别名。
- `t_a1a2177c`：实现 `AgentSightSetup.exe` 自解压安装器与用户级自启动。

如果后续发现 MCP / Skill 或 GitHub release 需要单独工程卡，应以本文件为父级验收依据拆分，不要把超过 2 天的范围塞进单卡。
