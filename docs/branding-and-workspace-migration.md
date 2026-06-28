# 品牌与工作区迁移

中文 | [English](branding-and-workspace-migration.en.md)

## 公开名称

公开产品名：`AgentSight for Windows`

推荐仓库名：

```text
agent-sight-windows
```

一句话介绍：

```text
A pixel-grounded observe-and-act host for Windows AI agents.
```

## 当前兼容名

为了不破坏当前入口，以下名称暂时保留：

- Python 包：`ai_control`
- console scripts：`ai-control-*`
- 本地数据目录：`%LOCALAPPDATA%\ai-control`
- 当前工作区：`C:\git\其他\ai-control`
- 部分 exe 历史名：`AIControl*.exe`

## 不要直接改当前工作区目录

不要在当前活跃 Codex 工作区中直接把：

```text
C:\git\其他\ai-control
```

改成：

```text
C:\git\其他\agent-sight-windows
```

原因：

- 当前 Codex 会话和自动化可能绑定旧路径；
- Supervisor、Run Key、Startup、discovery、dist 路径可能引用旧目录；
- 直接改名可能留下旧进程和旧自启项。

## 推荐迁移步骤

1. 先完成公开文档和发布风格。
2. 评估 package 名、exe 名、GitHub repo 名的迁移范围。
3. 停止 Supervisor / Host Agent / Tray GUI。
4. 备份或提交当前工作。
5. 在新会话或新工作区中迁移目录。
6. 更新自启项、Run Key、dist 路径、discovery、文档、workflow。
7. 重建 packaged layout。
8. 验证 `/screen -> /look -> /do -> /look`、Tray GUI、release workflow。

建议：保持当前本地路径到 GitHub 发布前最后阶段，再做受控迁移。
