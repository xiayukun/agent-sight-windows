# Branding And Workspace Migration

[中文](branding-and-workspace-migration.md) | English

## Public Name

Public product name: `AgentSight for Windows`

Recommended repository name:

```text
agent-sight-windows
```

Tagline:

```text
A pixel-grounded observe-and-act host for Windows AI agents.
```

## Current Compatibility Names

To avoid breaking current entrypoints, these names remain for now:

- Python package: `agentsight`
- console scripts: `agentsight-*`
- local data directory: `%LOCALAPPDATA%\AgentSight`
- current workspace: `C:\git\其他\AgentSight`
- historical exe names: `AgentSight*.exe`

## Do Not Rename The Active Workspace Directly

Do not directly rename the active Codex workspace from:

```text
C:\git\其他\AgentSight
```

to:

```text
C:\git\其他\agent-sight-windows
```

Reasons:

- the current Codex thread and automations may be bound to the old path;
- Supervisor, Run Key, Startup, discovery, and dist paths may reference the old directory;
- a direct rename can leave stale processes and startup entries behind.

## Recommended Migration Steps

1. Finish public docs and release style first.
2. Evaluate migration scope for package name, exe name, and GitHub repo name.
3. Stop Supervisor / Host Agent / Tray GUI.
4. Back up or commit current work.
5. Migrate the directory in a new session or new workspace.
6. Update startup entries, Run Key, dist paths, discovery, docs, and workflow.
7. Rebuild the packaged layout.
8. Verify `/screen -> /look -> /do -> /look`, Tray GUI, and release workflow.

Recommendation: keep the current local path until the final controlled migration before GitHub publication.
