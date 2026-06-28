# Contributing

[中文](CONTRIBUTING.md) | English

AgentSight for Windows is still research/MVP software. Contributions should stay scoped to one engineering milestone and preserve the pixel-grounded observe, human-equivalent input, and auditable-evidence boundary.

## Local Development

```powershell
$env:PYTHONPATH = "src"
py -m unittest discover tests
```

Optional capture/input dependencies are not part of the base install. Install packaging dependencies only when building executables:

```powershell
py -m pip install -e ".[packaging-exe]"
py tools/build_host_agent_exe.py
```

## Engineering Rules

- Do not add OCR, clipboard, DOM, accessibility tree, window semantics, hidden app APIs, shell/cmd GUI substitutes, or business-success judgment.
- Do not change the Host Agent input/capture core unless the current milestone explicitly requires it.
- Keep raw evidence separate from cursor overlays, diff heatmaps, and annotated review artifacts.
- Tray GUI is a human-visible control surface, not an AI semantic channel.
- Do not commit `runs*`, `dist/`, `build/`, local caches, screenshot evidence, or tokens.
- Do not rename the active `ai-control` workspace directory directly; public branding should move first through docs.

## Pull Request Checklist

- [ ] The change preserves project boundaries.
- [ ] Tests cover new behavior and failure paths.
- [ ] README / Skill / user guide are updated when AI or user workflow changes.
- [ ] No local evidence, build artifacts, or private paths are committed.
- [ ] Security-sensitive changes call out token, localhost, operator-control, emergency-stop, and real-input impacts.
