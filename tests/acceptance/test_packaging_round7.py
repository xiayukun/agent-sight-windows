from __future__ import annotations

import importlib
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from ai_control.adapters.mcp import MCPStdioAdapter, MCP_TOOL_NAMES
from ai_control.channels.mock import MockObservationChannel
from ai_control.diagnostics import capture as capture_diagnostics
from ai_control.diagnostics import first_use_doctor
from ai_control.diagnostics import input as input_diagnostics
from ai_control.diagnostics import p0_real_input_closed_loop
from ai_control.diagnostics.capture_cli import build_capture_diagnostics_report
from ai_control.diagnostics.capture_smoke_cli import build_capture_smoke_report
from ai_control.host_agent import interactive_task


ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
PYINSTALLER_DIR = ROOT / "packaging" / "pyinstaller"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
RELEASE_DOC_PAIRS = [
    (ROOT / "README.md", ROOT / "README.en.md"),
    (ROOT / "CHANGELOG.md", ROOT / "CHANGELOG.en.md"),
    (ROOT / "CONTRIBUTING.md", ROOT / "CONTRIBUTING.en.md"),
    (ROOT / "SECURITY.md", ROOT / "SECURITY.en.md"),
    (ROOT / "PRIVACY.md", ROOT / "PRIVACY.en.md"),
    (ROOT / "THIRD-PARTY-NOTICES.md", ROOT / "THIRD-PARTY-NOTICES.en.md"),
    (ROOT / "MAINTAINERS.md", ROOT / "MAINTAINERS.en.md"),
    (ROOT / "docs" / "release-checklist.md", ROOT / "docs" / "release-checklist.en.md"),
    (ROOT / "docs" / "release-notes-template.md", ROOT / "docs" / "release-notes-template.en.md"),
    (ROOT / "docs" / "github-launch-checklist.md", ROOT / "docs" / "github-launch-checklist.en.md"),
    (ROOT / "docs" / "repository-profile.md", ROOT / "docs" / "repository-profile.en.md"),
    (ROOT / "docs" / "user-guide.md", ROOT / "docs" / "user-guide.en.md"),
    (ROOT / "docs" / "visual-memory-and-attention.md", ROOT / "docs" / "visual-memory-and-attention.en.md"),
    (ROOT / "docs" / "branding-and-workspace-migration.md", ROOT / "docs" / "branding-and-workspace-migration.en.md"),
]


def pyproject() -> dict[str, object]:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def split_entrypoint(target: str) -> tuple[str, str]:
    module_name, _, function_name = target.partition(":")
    return module_name, function_name


class Round7PackagingTest(unittest.TestCase):
    def test_diagnostics_platform_helpers_avoid_wmi_backed_platform_system_probe_on_windows(self) -> None:
        with mock.patch("platform.system", side_effect=AssertionError("platform.system must not be called")):
            with mock.patch("ai_control.runtime_platform.os.name", "nt"):
                self.assertEqual(capture_diagnostics._platform_system_label(), "Windows")
                self.assertEqual(input_diagnostics._platform_system_label(), "Windows")
                self.assertEqual(first_use_doctor._platform_system_label(), "Windows")
                status = first_use_doctor._python_status({"AI_CONTROL_DOCTOR_PYTHON_EXECUTABLE": "C:\\Python\\python.exe"})
                self.assertTrue(p0_real_input_closed_loop._is_windows())
                self.assertEqual(p0_real_input_closed_loop._platform_system_label(), "Windows")

        self.assertEqual(status["recommended_launcher"], "py")

    def test_platform_system_probe_is_centralized(self) -> None:
        offenders = []
        allowed = ROOT / "src" / "ai_control" / "runtime_platform.py"
        for path in (ROOT / "src" / "ai_control").rglob("*.py"):
            if path == allowed:
                continue
            if "platform.system(" in path.read_text(encoding="utf-8"):
                offenders.append(path.relative_to(ROOT).as_posix())

        self.assertEqual(offenders, [])

    def test_pyproject_declares_build_system_and_src_package_discovery(self) -> None:
        data = pyproject()

        build_system = data["build-system"]  # type: ignore[index]
        self.assertEqual(build_system["build-backend"], "setuptools.build_meta")  # type: ignore[index]
        self.assertIn("setuptools>=68", build_system["requires"])  # type: ignore[index]

        setuptools_config = data["tool"]["setuptools"]  # type: ignore[index]
        self.assertEqual(setuptools_config["package-dir"], {"": "src"})  # type: ignore[index]
        package_find = setuptools_config["packages"]["find"]  # type: ignore[index]
        self.assertEqual(package_find["where"], ["src"])
        self.assertEqual(package_find["include"], ["ai_control*"])

        package_data = setuptools_config["package-data"]  # type: ignore[index]
        self.assertEqual(package_data["ai_control.adapters.skill"], ["SKILL.md"])

    def test_capture_dependencies_are_optional_only(self) -> None:
        project = pyproject()["project"]  # type: ignore[index]

        base_dependencies = project.get("dependencies", [])  # type: ignore[attr-defined]
        self.assertEqual(base_dependencies, [])

        optional = project["optional-dependencies"]  # type: ignore[index]
        self.assertEqual(optional["capture-mss"], ["mss"])
        self.assertEqual(optional["capture-pillow"], ["Pillow"])
        self.assertEqual(optional["capture-windows-capture"], ["windows-capture"])
        self.assertEqual(optional["windows-capture"], ["mss", "Pillow", "windows-capture"])
        self.assertEqual(optional["packaging-exe"], ["pyinstaller"])

    def test_project_scripts_are_fixed_package_entrypoints(self) -> None:
        scripts = pyproject()["project"]["scripts"]  # type: ignore[index]
        expected = {
            "ai-control-cli": "ai_control.adapters.local_cli:main",
            "ai-control-mcp": "ai_control.adapters.mcp.server:main",
            "ai-control-capture-diagnostics": "ai_control.diagnostics.capture_cli:main",
            "ai-control-capture-probe": "ai_control.diagnostics.capture_cli:main",
            "ai-control-capture-smoke": "ai_control.diagnostics.capture_smoke_cli:main",
            "ai-control-first-use-doctor": "ai_control.diagnostics.first_use_doctor_cli:main",
            "ai-control-input-smoke": "ai_control.diagnostics.input_smoke_cli:main",
            "ai-control-p0-real-input-smoke": "ai_control.diagnostics.p0_real_input_closed_loop_cli:main",
            "ai-control-attention-sync-proof": "ai_control.diagnostics.attention_sync_value:main",
            "ai-control-release-readiness": "ai_control.diagnostics.release_readiness:main",
            "ai-control-installer": "ai_control.installer:main",
            "ai-control-host-agent": "ai_control.host_agent.server:main",
            "ai-control-host-agent-scenarios": "ai_control.host_agent.scenarios:main",
            "ai-control-host-agent-installer": "ai_control.host_agent.installer:main",
            "ai-control-session-supervisor": "ai_control.session_supervisor:main",
            "ai-control-segment-decoder": "ai_control.segments.decoder_cli:main",
            "ai-control-tray": "ai_control.tray.cli:main",
            "ai-control-tray-gui": "ai_control.tray.gui:main",
        }
        self.assertEqual(scripts, expected)

        for target in scripts.values():
            module_name, function_name = split_entrypoint(target)
            self.assertTrue(module_name.startswith("ai_control."))
            self.assertNotIn(".examples", module_name)
            module = importlib.import_module(module_name)
            self.assertTrue(callable(getattr(module, function_name)))

    def test_script_modules_do_not_execute_install_or_shell(self) -> None:
        scripts = pyproject()["project"]["scripts"]  # type: ignore[index]
        forbidden = [
            "subprocess.run",
            "subprocess.Popen",
            "os.system",
            "Start-Process",
            "pip._internal",
            "ensurepip",
        ]

        for target in set(scripts.values()):
            module_name, _ = split_entrypoint(target)
            path = ROOT / "src" / Path(*module_name.split(".")).with_suffix(".py")
            text = path.read_text(encoding="utf-8")
            for marker in forbidden:
                self.assertNotIn(marker, text, f"{marker} found in {path}")

    def test_host_agent_exe_packaging_specs_are_present(self) -> None:
        agent_spec = PYINSTALLER_DIR / "AIControlHostAgent.spec"
        installer_spec = PYINSTALLER_DIR / "AIControlHostAgentInstaller.spec"
        scenarios_spec = PYINSTALLER_DIR / "AIControlHostAgentScenarios.spec"
        supervisor_spec = PYINSTALLER_DIR / "AIControlSessionSupervisor.spec"
        product_installer_spec = PYINSTALLER_DIR / "AIControlInstaller.spec"
        tray_spec = PYINSTALLER_DIR / "AIControlTray.spec"
        tray_gui_spec = PYINSTALLER_DIR / "AIControlTrayGui.spec"
        timeline_viewer_spec = PYINSTALLER_DIR / "AgentSightTimelineViewer.spec"
        build_script = ROOT / "tools" / "build_host_agent_exe.py"

        self.assertTrue(agent_spec.exists())
        self.assertTrue(installer_spec.exists())
        self.assertTrue(scenarios_spec.exists())
        self.assertTrue(supervisor_spec.exists())
        self.assertTrue(product_installer_spec.exists())
        self.assertTrue(tray_spec.exists())
        self.assertTrue(tray_gui_spec.exists())
        self.assertTrue(timeline_viewer_spec.exists())
        self.assertTrue(build_script.exists())
        self.assertIn("AIControlHostAgent", agent_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlHostAgentInstaller", installer_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlHostAgentScenarios", scenarios_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlSessionSupervisor", supervisor_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlInstaller", product_installer_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlTray", tray_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlTrayGui", tray_gui_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTimelineViewer", timeline_viewer_spec.read_text(encoding="utf-8"))
        self.assertIn("AIControlInstaller.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AIControlSessionSupervisor.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AIControlHostAgentScenarios.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AIControlTray.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AIControlTrayGui.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTimelineViewer.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("INSTALL_AI_CONTROL.cmd", build_script.read_text(encoding="utf-8"))
        self.assertIn("INSTALL_AI_CONTROL_HOST_AGENT.cmd", build_script.read_text(encoding="utf-8"))
        self.assertIn("--scenario health --wait-seconds 60", build_script.read_text(encoding="utf-8"))
        self.assertIn("pyinstaller_missing", build_script.read_text(encoding="utf-8"))

    def test_schtasks_helper_times_out_instead_of_hanging_installer(self) -> None:
        with mock.patch("ai_control.host_agent.interactive_task.shutil.which", return_value="schtasks.exe"):
            with mock.patch("ai_control.host_agent.interactive_task.run_process") as run:
                run.side_effect = interactive_task.TimeoutExpired(
                    cmd=["schtasks.exe", "/Create"],
                    timeout=10,
                    output="partial",
                    stderr="slow",
                )
                result = interactive_task.run_schtasks(["/Create"])

        self.assertEqual(result.returncode, 124)
        self.assertIn("timed out", result.stderr)
        self.assertEqual(result.stdout, "partial")

    def test_dist_wrappers_prefer_unified_supervisor_entry(self) -> None:
        build_script = (ROOT / "tools" / "build_host_agent_exe.py").read_text(encoding="utf-8")

        self.assertIn("INSTALL_AI_CONTROL.cmd", build_script)
        self.assertIn("UNINSTALL_AI_CONTROL.cmd", build_script)
        self.assertIn("AIControlInstaller.exe", build_script)
        self.assertIn("AIControlSessionSupervisor.exe", build_script)
        self.assertIn("AIControlHostAgent.exe", build_script)
        self.assertIn("AIControlTrayGui.exe", build_script)
        self.assertIn("--start-now --arm-real-input --wait-seconds 60", build_script)
        self.assertIn("INSTALL_AI_CONTROL_HOST_AGENT.cmd", build_script)
        self.assertIn("Legacy: installing and starting AI Control Host Agent split watchdog", build_script)

    def test_diagnostics_cli_report_uses_public_adapter_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[MockObservationChannel()],
                default_observation_channel_ref="mock_screen",
            )
            report = build_capture_diagnostics_report(adapter, runs_dir=temp_dir)

            self.assertEqual(report["object_type"], "CaptureDiagnosticsCliReport")
            self.assertTrue(report["diagnostics_ok"])
            self.assertFalse(report["install_executed"])
            self.assertFalse(report["input_executed"])
            self.assertFalse(report["background_action_executed"])
            self.assertTrue(report["evidence_package_ok"])
            self.assertTrue(report["replay_read_only"])
            self.assertTrue(report["integrity_ok"])

    def test_smoke_cli_report_does_not_count_mock_as_real_capture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[MockObservationChannel()],
                default_observation_channel_ref="mock_screen",
            )
            report = build_capture_smoke_report(adapter, runs_dir=temp_dir)

            self.assertEqual(report["smoke_status"], "real_capture_not_ready")
            self.assertEqual(report["exit_code"], 2)
            self.assertTrue(report["mock_not_tested"])
            self.assertFalse(report["install_executed"])
            self.assertFalse(report["input_executed"])
            self.assertFalse(report["background_action_executed"])
            self.assertTrue(report["integrity_ok"])

    def test_console_scripts_keep_public_mcp_surface_minimal(self) -> None:
        self.assertEqual(MCP_TOOL_NAMES, ("screen", "look", "do"))

    def test_release_workflow_builds_after_tests_and_uploads_checksums(self) -> None:
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("tags:", text)
        self.assertIn('"v*"', text)
        self.assertIn("workflow_dispatch", text)
        self.assertIn("runs-on: windows-latest", text)
        self.assertIn("py -m unittest discover tests", text)
        self.assertIn("py tools/build_host_agent_exe.py", text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("gh release create", text)
        self.assertLess(text.index("Run tests"), text.index("Build dist executables"))
        self.assertLess(text.index("Build dist executables"), text.index("Create or update GitHub release"))

    def test_release_docs_use_chinese_primary_and_english_mirrors(self) -> None:
        for zh, en in RELEASE_DOC_PAIRS:
            self.assertTrue(zh.exists(), f"missing Chinese document: {zh}")
            self.assertTrue(en.exists(), f"missing English document: {en}")
            zh_text = zh.read_text(encoding="utf-8")
            en_text = en.read_text(encoding="utf-8")
            self.assertIn("中文 | [English]", zh_text)
            self.assertIn("[中文]", en_text)
            self.assertIn("English", en_text)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("# AgentSight for Windows", readme)
        self.assertIn("AI-Control", readme)
        self.assertIn("Windows AI agent", readme)
        self.assertIn("computer use", readme)
        self.assertIn("pixel-grounded control", readme)
        self.assertIn("视觉记忆系统", readme)

    def test_user_guides_describe_agseg_as_canonical_and_review_outputs_as_derived(self) -> None:
        zh = (ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
        en = (ROOT / "docs" / "user-guide.en.md").read_text(encoding="utf-8")

        self.assertIn(".agseg", zh)
        self.assertIn("canonical evidence", zh)
        self.assertIn("derived review artifact", zh)
        self.assertNotIn("基于已有 raw evidence 的派生重建包", zh)
        self.assertIn(".agseg", en)
        self.assertIn("canonical evidence", en)
        self.assertIn("human-review derived artifacts", en)
        self.assertNotIn("derived reconstruction package over existing raw evidence", en)

    def test_public_docs_separate_timeline_review_bundle_from_canonical_agseg(self) -> None:
        docs = [
            ROOT / "README.md",
            ROOT / "README.en.md",
            ROOT / "AGENTS.md",
            ROOT / "docs" / "user-guide.md",
            ROOT / "docs" / "user-guide.en.md",
        ]

        for path in docs:
            text = path.read_text(encoding="utf-8")
            self.assertIn(".agseg", text, f"{path} must describe canonical Segment storage")
            self.assertIn("canonical evidence", text, f"{path} must name canonical evidence")
            self.assertNotIn("timestamped keyframe/P-frame delta segment manifest", text)
            self.assertNotIn("derived reconstruction package", text)
            self.assertNotIn("timestamped keyframe/P-frame delta review bundle", text)

        self.assertIn("PySide6/Qt", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("PySide6/Qt", (ROOT / "README.en.md").read_text(encoding="utf-8"))
        self.assertIn("PySide6/Qt", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

