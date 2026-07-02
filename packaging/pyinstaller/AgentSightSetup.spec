# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

ROOT = Path(SPECPATH).parents[1]
DIST = ROOT / "dist"
PAYLOAD_EXES = [
    "AgentSightSupervisor.exe",
    "AgentSightHostAgent.exe",
    "AgentSightTray.exe",
    "AgentSightTrayCli.exe",
    "AgentSightTimelineViewer.exe",
    "AgentSightMcpServer.exe",
    "AgentSightHostAgentScenarios.exe",
    "AgentSightHostAgentInstaller.exe",
]
PAYLOAD_DATAS = [(str(DIST / name), "agentsight_payload") for name in PAYLOAD_EXES if (DIST / name).exists()]
SKILL = ROOT / "src" / "agentsight" / "adapters" / "skill" / "SKILL.md"
if SKILL.exists():
    PAYLOAD_DATAS.append((str(SKILL), "agentsight_payload"))

a = Analysis(
    [str(ROOT / "src" / "agentsight" / "installer.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=PAYLOAD_DATAS,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="AgentSightSetup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # AgentSightSetup embeds the other onefile exes as installer payload. UPX can
    # produce a setup bootloader that segfaults before Python starts when the
    # nested payload archive is this large, so keep compression disabled here.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
