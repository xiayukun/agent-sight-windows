from __future__ import annotations

import importlib
import io
import json
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from agentsight.adapters.mcp import MCPStdioAdapter, MCP_TOOL_NAMES
from agentsight.channels.mock import MockObservationChannel
from agentsight.diagnostics import capture as capture_diagnostics
from agentsight.diagnostics import first_use_doctor
from agentsight.diagnostics import input as input_diagnostics
from agentsight.diagnostics import p0_real_input_closed_loop
from agentsight.diagnostics.capture_cli import build_capture_diagnostics_report
from agentsight.diagnostics.capture_smoke_cli import build_capture_smoke_report
from agentsight.host_agent import interactive_task
from agentsight import installer


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
            with mock.patch("agentsight.runtime_platform.os.name", "nt"):
                self.assertEqual(capture_diagnostics._platform_system_label(), "Windows")
                self.assertEqual(input_diagnostics._platform_system_label(), "Windows")
                self.assertEqual(first_use_doctor._platform_system_label(), "Windows")
                status = first_use_doctor._python_status({"AGENTSIGHT_DOCTOR_PYTHON_EXECUTABLE": "C:\\Python\\python.exe"})
                self.assertTrue(p0_real_input_closed_loop._is_windows())
                self.assertEqual(p0_real_input_closed_loop._platform_system_label(), "Windows")

        self.assertEqual(status["recommended_launcher"], "py")

    def test_platform_system_probe_is_centralized(self) -> None:
        offenders = []
        allowed = ROOT / "src" / "AgentSight" / "runtime_platform.py"
        for path in (ROOT / "src" / "AgentSight").rglob("*.py"):
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
        self.assertEqual(package_find["include"], ["agentsight*"])

        package_data = setuptools_config["package-data"]  # type: ignore[index]
        self.assertEqual(package_data["agentsight.adapters.skill"], ["SKILL.md"])

    def test_capture_dependencies_are_optional_only(self) -> None:
        project = pyproject()["project"]  # type: ignore[index]

        base_dependencies = project.get("dependencies", [])  # type: ignore[attr-defined]
        self.assertEqual(base_dependencies, [])

        optional = project["optional-dependencies"]  # type: ignore[index]
        self.assertEqual(optional["capture-mss"], ["mss"])
        self.assertEqual(optional["capture-pillow"], ["Pillow"])
        self.assertEqual(optional["capture-windows-capture"], ["windows-capture"])
        self.assertEqual(optional["windows-capture"], ["mss", "Pillow", "windows-capture"])
        self.assertEqual(optional["packaging-exe"], ["pyinstaller", "PySide6"])

    def test_project_scripts_are_fixed_package_entrypoints(self) -> None:
        scripts = pyproject()["project"]["scripts"]  # type: ignore[index]
        expected = {
            "agentsight-cli": "agentsight.adapters.local_cli:main",
            "agentsight-mcp": "agentsight.adapters.mcp.server:main",
            "agentsight-capture-diagnostics": "agentsight.diagnostics.capture_cli:main",
            "agentsight-capture-probe": "agentsight.diagnostics.capture_cli:main",
            "agentsight-capture-smoke": "agentsight.diagnostics.capture_smoke_cli:main",
            "agentsight-first-use-doctor": "agentsight.diagnostics.first_use_doctor_cli:main",
            "agentsight-input-smoke": "agentsight.diagnostics.input_smoke_cli:main",
            "agentsight-p0-real-input-smoke": "agentsight.diagnostics.p0_real_input_closed_loop_cli:main",
            "agentsight-attention-sync-proof": "agentsight.diagnostics.attention_sync_value:main",
            "agentsight-release-readiness": "agentsight.diagnostics.release_readiness:main",
            "agentsight-installer": "agentsight.installer:main",
            "agentsight-host-agent": "agentsight.host_agent.server:main",
            "agentsight-host-agent-scenarios": "agentsight.host_agent.scenarios:main",
            "agentsight-host-agent-installer": "agentsight.host_agent.installer:main",
            "agentsight-session-supervisor": "agentsight.session_supervisor:main",
            "agentsight-segment-decoder": "agentsight.segments.decoder_cli:main",
            "agentsight-tray": "agentsight.tray.cli:main",
            "agentsight-tray-gui": "agentsight.tray.gui:main",
        }
        self.assertEqual(scripts, expected)

        for target in scripts.values():
            module_name, function_name = split_entrypoint(target)
            self.assertTrue(module_name.startswith("agentsight."))
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
        agent_spec = PYINSTALLER_DIR / "AgentSightHostAgent.spec"
        installer_spec = PYINSTALLER_DIR / "AgentSightHostAgentInstaller.spec"
        scenarios_spec = PYINSTALLER_DIR / "AgentSightHostAgentScenarios.spec"
        supervisor_spec = PYINSTALLER_DIR / "AgentSightSupervisor.spec"
        product_installer_spec = PYINSTALLER_DIR / "AgentSightSetup.spec"
        mcp_spec = PYINSTALLER_DIR / "AgentSightMcpServer.spec"
        tray_spec = PYINSTALLER_DIR / "AgentSightTray.spec"
        tray_gui_spec = PYINSTALLER_DIR / "AgentSightTrayCli.spec"
        timeline_viewer_spec = PYINSTALLER_DIR / "AgentSightTimelineViewer.spec"
        build_script = ROOT / "tools" / "build_host_agent_exe.py"

        self.assertTrue(agent_spec.exists())
        self.assertTrue(installer_spec.exists())
        self.assertTrue(scenarios_spec.exists())
        self.assertTrue(supervisor_spec.exists())
        self.assertTrue(product_installer_spec.exists())
        self.assertTrue(mcp_spec.exists())
        self.assertTrue(tray_spec.exists())
        self.assertTrue(tray_gui_spec.exists())
        self.assertTrue(timeline_viewer_spec.exists())
        self.assertTrue(build_script.exists())
        self.assertIn("AgentSightHostAgent", agent_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightHostAgentInstaller", installer_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightHostAgentScenarios", scenarios_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightSupervisor", supervisor_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightSetup", product_installer_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightMcpServer", mcp_spec.read_text(encoding="utf-8"))
        self.assertIn("agentsight_payload", product_installer_spec.read_text(encoding="utf-8"))
        self.assertIn("upx=False", product_installer_spec.read_text(encoding="utf-8"))
        self.assertNotIn("upx=True", product_installer_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTray", tray_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTrayCli", tray_gui_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTimelineViewer", timeline_viewer_spec.read_text(encoding="utf-8"))
        self.assertIn("AgentSightSetup.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightSupervisor.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightMcpServer.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightHostAgentScenarios.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTray.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTrayCli.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("AgentSightTimelineViewer.exe", build_script.read_text(encoding="utf-8"))
        self.assertIn("INSTALL_AGENTSIGHT.cmd", build_script.read_text(encoding="utf-8"))
        self.assertIn("INSTALL_AGENTSIGHT_HOST_AGENT.cmd", build_script.read_text(encoding="utf-8"))
        self.assertIn("--scenario health --wait-seconds 60", build_script.read_text(encoding="utf-8"))
        self.assertIn("pyinstaller_missing", build_script.read_text(encoding="utf-8"))

    def test_schtasks_helper_times_out_instead_of_hanging_installer(self) -> None:
        with mock.patch("agentsight.host_agent.interactive_task.shutil.which", return_value="schtasks.exe"):
            with mock.patch("agentsight.host_agent.interactive_task.run_process") as run:
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

        self.assertIn("INSTALL_AGENTSIGHT.cmd", build_script)
        self.assertIn("UNINSTALL_AGENTSIGHT.cmd", build_script)
        self.assertIn("AgentSightSetup.exe", build_script)
        self.assertIn("AgentSightSupervisor.exe", build_script)
        self.assertIn("AgentSightHostAgent.exe", build_script)
        self.assertIn("AgentSightMcpServer.exe", build_script)
        self.assertIn("AgentSightTray.exe", build_script)
        self.assertIn("--start-now --arm-real-input --wait-seconds 60", build_script)
        self.assertIn("INSTALL_AGENTSIGHT_HOST_AGENT.cmd", build_script)
        self.assertIn("Legacy: installing and starting AgentSight Host Agent split watchdog", build_script)

    def test_long_lived_onefile_specs_use_stable_runtime_tmpdir(self) -> None:
        stable_runtime_tmpdir = r'runtime_tmpdir=r"%LOCALAPPDATA%\\AgentSight\\pyinstaller-runtime"'
        for name in ["AgentSightHostAgent.spec", "AgentSightSupervisor.spec", "AgentSightTray.spec"]:
            spec_text = (PYINSTALLER_DIR / name).read_text(encoding="utf-8")
            self.assertIn(stable_runtime_tmpdir, spec_text)

    def test_agentsight_setup_self_extracts_payload_and_writes_ai_install_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            payload.mkdir()
            for name in ["AgentSightSetup.exe", *installer.PAYLOAD_EXE_NAMES]:
                (payload / name).write_text(f"fake {name}\n", encoding="utf-8")
            env = {
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "APPDATA": str(root / "Roaming"),
            }
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.installer._install_with_packaged_supervisor") as install_supervisor:
                    install_supervisor.return_value = {
                        "object_type": "AgentSightPackagedCommandReport",
                        "exit_code": 0,
                        "json": {"install_status": "installed", "run_key": {"run_key_name": "AgentSight"}},
                    }
                    with mock.patch("agentsight.installer.product_status") as status:
                        status.return_value = {"object_type": "AgentSightSetupStatus", "exit_code": 0}
                        report = installer.install_agentsight(
                            payload_dir=payload,
                            version="1.0.0",
                            start_now=False,
                            arm_real_input=True,
                            show_prompt=False,
                        )

            install_root = Path(report["install_root"])
            current_dir = install_root / "current"
            ai_install_dir = install_root / "ai-install"
            mcp_config = json.loads((ai_install_dir / "mcp.json").read_text(encoding="utf-8"))
            legacy_mcp_config = json.loads((ai_install_dir / "mcp.config.example.json").read_text(encoding="utf-8"))
            skill_text = (ai_install_dir / "SKILL.md").read_text(encoding="utf-8")
            observed = {
                "setup_exists": (current_dir / "AgentSightSetup.exe").exists(),
                "supervisor_exists": (current_dir / "AgentSightSupervisor.exe").exists(),
                "mcp_exists": (current_dir / "AgentSightMcpServer.exe").exists(),
                "mcp_public_exists": (current_dir / "AgentSightMcp.exe").exists(),
                "skill_exists": (ai_install_dir / "SKILL.md").exists(),
                "nested_skill_exists": (ai_install_dir / "agentsight" / "SKILL.md").exists(),
                "readme_for_ai_exists": (ai_install_dir / "README_FOR_AI.md").exists(),
                "prompt_exists": (ai_install_dir / "AGENTSIGHT_AI_INSTALL_PROMPT.txt").exists(),
                "legacy_mcp_exists": (ai_install_dir / "mcp.config.example.json").exists(),
                "legacy_prompt_exists": (ai_install_dir / "PROMPT_FOR_AI.md").exists(),
                "legacy_readme_exists": (ai_install_dir / "README.md").exists(),
                "uninstall_exists": (install_root / "uninstall" / "UNINSTALL_AGENTSIGHT.cmd").exists(),
                "mcp_command": mcp_config["mcpServers"]["agentsight"]["command"],
                "legacy_mcp_command": legacy_mcp_config["mcpServers"]["agentsight"]["command"],
                "current_mcp_command": str(current_dir / "AgentSightMcp.exe"),
                "mcp_env": mcp_config["mcpServers"]["agentsight"].get("env"),
                "skill_text": skill_text,
                "readme_text": (ai_install_dir / "README_FOR_AI.md").read_text(encoding="utf-8"),
                "prompt_text": (ai_install_dir / "AGENTSIGHT_AI_INSTALL_PROMPT.txt").read_text(encoding="utf-8"),
            }

        self.assertEqual(report["install_status"], "installed")
        self.assertEqual(report["version"], "1.0.0")
        self.assertEqual(report["registered_startup_components"], ["AgentSight"])
        self.assertFalse(report["public_port_opened"])
        self.assertFalse(report["prompt_shown"])
        self.assertTrue(observed["setup_exists"])
        self.assertTrue(observed["supervisor_exists"])
        self.assertTrue(observed["mcp_exists"])
        self.assertTrue(observed["mcp_public_exists"])
        self.assertTrue(observed["skill_exists"])
        self.assertTrue(observed["nested_skill_exists"])
        self.assertTrue(observed["readme_for_ai_exists"])
        self.assertTrue(observed["prompt_exists"])
        self.assertTrue(observed["legacy_mcp_exists"])
        self.assertTrue(observed["legacy_prompt_exists"])
        self.assertTrue(observed["legacy_readme_exists"])
        self.assertTrue(observed["uninstall_exists"])
        self.assertEqual(observed["mcp_command"], observed["current_mcp_command"])
        self.assertEqual(observed["legacy_mcp_command"], observed["current_mcp_command"])
        self.assertEqual(observed["mcp_env"], {})
        self.assertEqual(report["mcp_public_alias"]["status"], "written")
        self.assertIn("screen", observed["skill_text"])
        self.assertIn("look", observed["skill_text"])
        self.assertIn("do", observed["skill_text"])
        self.assertIn("mcp.json", observed["readme_text"])
        self.assertIn("SKILL.md", observed["prompt_text"])
        self.assertIn("AgentSightMcp.exe", observed["readme_text"])
        self.assertNotIn("Bearer ", json.dumps(mcp_config, ensure_ascii=False))
        self.assertNotIn("secret", json.dumps(mcp_config, ensure_ascii=False).lower())
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_setup_install_report_records_visible_progress_and_copyable_prompt_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            payload.mkdir()
            for name in ["AgentSightSetup.exe", *installer.PAYLOAD_EXE_NAMES]:
                (payload / name).write_text(f"fake {name}\n", encoding="utf-8")
            env = {
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "APPDATA": str(root / "Roaming"),
            }
            visible_progress = io.StringIO()

            def fake_copy_payload(*args: object, **kwargs: object) -> dict[str, object]:
                self.assertIn("Copying packaged AgentSight files", visible_progress.getvalue())
                return {"copy_status": "copied"}

            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.installer._install_with_packaged_supervisor", return_value={"exit_code": 0}):
                    with mock.patch("agentsight.installer.product_status", return_value={"object_type": "AgentSightSetupStatus", "exit_code": 0}):
                        with mock.patch("agentsight.installer._copy_payload", side_effect=fake_copy_payload):
                            report = installer.install_agentsight(
                                payload_dir=payload,
                                version="1.0.1",
                                start_now=False,
                                arm_real_input=True,
                                show_prompt=False,
                                progress_stream=visible_progress,
                            )

        self.assertEqual(report["progress"]["status"], "visible")
        visible_output = visible_progress.getvalue()
        self.assertIn("[AgentSight Setup] Preparing install directories", visible_output)
        self.assertIn("[AgentSight Setup] Copying packaged AgentSight files", visible_output)
        self.assertIn("[AgentSight Setup] Registering startup and starting AgentSight", visible_output)
        self.assertIn("[AgentSight Setup] AgentSight setup complete", visible_output)
        self.assertEqual(report["progress"]["visible_output"], "console")
        self.assertEqual(
            [event["key"] for event in report["progress"]["events"]],
            [
                "prepare_install_dirs",
                "stop_existing_supervisor",
                "copy_payload",
                "write_runtime_files",
                "write_ai_install",
                "write_uninstall_entry",
                "install_startup",
                "write_report",
                "complete",
            ],
        )
        prompt_ui = report["completion_prompt_ui"]
        self.assertEqual(prompt_ui["ui"], "copyable_win32_dialog")
        self.assertTrue(prompt_ui["copy_button"])
        self.assertTrue(prompt_ui["readonly_multiline_text"])
        self.assertTrue(prompt_ui["selectable_text"])
        self.assertFalse(prompt_ui["legacy_message_box"])

    def test_setup_completion_prompt_contract_reflects_actual_prompt_ui_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            payload.mkdir()
            for name in ["AgentSightSetup.exe", *installer.PAYLOAD_EXE_NAMES]:
                (payload / name).write_text(f"fake {name}\n", encoding="utf-8")
            env = {
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "APPDATA": str(root / "Roaming"),
            }
            fallback_ui = {
                "ui": "legacy_message_box",
                "copy_button": False,
                "readonly_multiline_text": False,
                "selectable_text": False,
                "legacy_message_box": True,
            }
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.installer._install_with_packaged_supervisor", return_value={"exit_code": 0}):
                    with mock.patch("agentsight.installer.product_status", return_value={"object_type": "AgentSightSetupStatus", "exit_code": 0}):
                        with mock.patch(
                            "agentsight.installer._show_install_prompt",
                            return_value={"shown": True, "ui_contract": fallback_ui},
                        ):
                            report = installer.install_agentsight(
                                payload_dir=payload,
                                version="1.0.1",
                                start_now=False,
                                arm_real_input=True,
                                show_prompt=True,
                                progress_stream=io.StringIO(),
                            )

        self.assertTrue(report["prompt_shown"])
        self.assertEqual(report["completion_prompt_ui"], fallback_ui)

    def test_ai_install_docs_explain_when_to_use_and_when_not_to_use_agentsight(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "AgentSight"
            readme = installer._ai_install_readme(install_root, install_root / "current")
        skill = (ROOT / "src" / "agentsight" / "adapters" / "skill" / "SKILL.md").read_text(encoding="utf-8")

        for text in [readme, skill]:
            self.assertIn("When to use AgentSight", text)
            self.assertIn("When not to use AgentSight", text)
            self.assertIn("screen monitoring", text)
            self.assertIn("timeline", text)
            self.assertIn("direct API", text)
            self.assertIn("do not use AgentSight as a shell substitute", text)

    def test_ai_handoff_docs_include_five_request_happy_path_quickstart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = Path(temp_dir) / "AgentSight"
            readme = installer._ai_install_readme(install_root, install_root / "current")
        skill = (ROOT / "src" / "agentsight" / "adapters" / "skill" / "SKILL.md").read_text(encoding="utf-8")

        for text in [readme, skill]:
            self.assertIn("Five-request happy path", text)
            self.assertIn("screen -> look -> do -> look", text)
            self.assertIn("read discovery", text)
            self.assertIn('"op": "screen"', text)
            self.assertIn('"op": "look"', text)
            self.assertIn('"op": "do"', text)
            self.assertIn('"t": "click"', text)
            self.assertIn('"t": "text"', text)
            self.assertIn('"t": "key"', text)
            self.assertIn('"basis": {"view_id": "<view.id from look-1>"}', text)
            self.assertIn("Even pure keyboard input still requires basis.view_id", text)
            quickstart = text.split("Five-request happy path", 1)[1].split("Product Boundary", 1)[0]
            self.assertNotIn("business_success", quickstart)
            self.assertNotIn("target_hit", quickstart)
            self.assertNotIn("causal_loop_ok", quickstart)

    def test_setup_stops_running_current_supervisor_before_copying_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            payload = root / "payload"
            payload.mkdir()
            for name in ["AgentSightSetup.exe", *installer.PAYLOAD_EXE_NAMES]:
                (payload / name).write_text(f"new {name}\n", encoding="utf-8")
            env = {
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "APPDATA": str(root / "Roaming"),
            }
            current_dir = Path(env["LOCALAPPDATA"]) / "AgentSight" / "current"
            current_dir.mkdir(parents=True)
            supervisor_exe = current_dir / "AgentSightSupervisor.exe"
            supervisor_exe.write_text("old running supervisor\n", encoding="utf-8")
            running_status = {
                "object_type": "AgentSightPackagedCommandReport",
                "exit_code": 0,
                "json": {
                    "state": {
                        "supervisor_status": "running",
                        "process_identity": {"executable": str(supervisor_exe)},
                    },
                    "single_instance": {
                        "lock_status": "active",
                        "active": True,
                        "owner_process_identity": {"executable": str(supervisor_exe)},
                    },
                },
            }
            stop_report = {"object_type": "AgentSightPackagedCommandReport", "exit_code": 0, "json": {"stop_requested": True}}
            stopped_status = {"object_type": "AgentSightPackagedCommandReport", "exit_code": 0, "json": {"single_instance": {"active": False}}}

            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.installer._run_supervisor_command", side_effect=[running_status, stop_report, stopped_status]) as run_supervisor:
                    with mock.patch("agentsight.installer._install_with_packaged_supervisor", return_value={"exit_code": 0}):
                        with mock.patch("agentsight.installer.product_status", return_value={"object_type": "AgentSightSetupStatus", "exit_code": 0}):
                            report = installer.install_agentsight(
                                payload_dir=payload,
                                version="1.0.0",
                                start_now=False,
                                arm_real_input=True,
                                show_prompt=False,
                            )
                            supervisor_text_after_install = supervisor_exe.read_text(encoding="utf-8")

        self.assertTrue(report["pre_existing_supervisor"]["stop_attempted"])
        self.assertEqual(report["payload_copy"]["copy_status"], "copied")
        self.assertEqual(supervisor_text_after_install, "new AgentSightSupervisor.exe\n")
        self.assertEqual(run_supervisor.call_args_list[1].args[0][0], "stop")

    def test_payload_cleanup_sweeps_current_install_dir_processes_not_reported_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current_dir = Path(temp_dir) / "current"
            current_dir.mkdir()
            with mock.patch("agentsight.installer._find_current_dir_payload_process_ids", return_value=[111, 222]):
                with mock.patch("agentsight.installer._process_running", return_value=True):
                    with mock.patch("agentsight.installer._terminate_process_for_upgrade") as terminate:
                        terminate.side_effect = lambda pid, reason: {
                            "pid": pid,
                            "force_attempted": True,
                            "force_status": "terminated",
                            "reason": reason,
                        }

                        cleanup = installer._cleanup_reported_payload_processes(
                            current_dir=current_dir,
                            reports=[],
                            reason="packaged_supervisor_stopped_before_payload_copy",
                        )

        self.assertTrue(cleanup["attempted"])
        self.assertEqual(cleanup["pids"], [111, 222])
        self.assertEqual([call.args[0] for call in terminate.call_args_list], [111, 222])
        self.assertFalse(cleanup["host_input_sent"])
        self.assertEqual(cleanup["host_sent_event_count"], 0)

    def test_agentsight_setup_cli_accepts_dist_wrapper_install_flags(self) -> None:
        with mock.patch("agentsight.installer.install_agentsight") as install_agentsight:
            install_agentsight.return_value = {"exit_code": 0, "install_status": "installed"}

            exit_code = installer.main(["install", "--no-gui", "--start-now", "--arm-real-input", "--wait-seconds", "60"])

        self.assertEqual(exit_code, 0)
        _, kwargs = install_agentsight.call_args
        self.assertTrue(kwargs["start_now"])
        self.assertTrue(kwargs["arm_real_input"])
        self.assertEqual(kwargs["wait_seconds"], 60.0)
        self.assertFalse(kwargs["show_prompt"])

    def test_setup_status_output_keeps_nested_supervisor_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            env = {
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "APPDATA": str(root / "Roaming"),
            }
            current_dir = Path(env["LOCALAPPDATA"]) / "AgentSight" / "current"
            current_dir.mkdir(parents=True)
            (current_dir / "AgentSightSupervisor.exe").write_text("fake supervisor\n", encoding="utf-8")
            supervisor_status = {
                "object_type": "AgentSightPackagedCommandReport",
                "exit_code": 0,
                "json": {"state": {"supervisor_status": "running"}},
            }

            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.installer._run_supervisor_command", return_value=supervisor_status) as run_supervisor:
                    report = installer.product_status(output=str(root / "status.json"))

        self.assertEqual(report["supervisor"]["json"]["state"]["supervisor_status"], "running")
        self.assertIsNone(run_supervisor.call_args.kwargs["output"])

    def test_packaged_setup_installs_then_detaches_supervisor_run_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current_dir = Path(temp_dir)
            (current_dir / "AgentSightSupervisor.exe").write_text("fake supervisor\n", encoding="utf-8")
            install_report = {"object_type": "AgentSightPackagedCommandReport", "exit_code": 0}
            start_report = {"object_type": "AgentSightPackagedSupervisorStartReport", "exit_code": 0, "start_status": "started"}

            with mock.patch("agentsight.installer._run_packaged_command", return_value=install_report) as run_packaged:
                with mock.patch("agentsight.installer._start_packaged_supervisor_detached", return_value=start_report) as start_supervisor:
                    report = installer._install_with_packaged_supervisor(
                        current_dir=current_dir,
                        host="127.0.0.1",
                        port=8765,
                        runs_dir="runs_host_agent",
                        agent_exe=None,
                        tray_gui_exe=None,
                        start_method="auto",
                        start_now=True,
                        arm_real_input=True,
                        wait_seconds=60.0,
                    )

        install_command = run_packaged.call_args.args[0]
        self.assertIn("install", install_command)
        self.assertIn("--arm-real-input", install_command)
        self.assertNotIn("--start-now", install_command)
        start_supervisor.assert_called_once()
        _, start_kwargs = start_supervisor.call_args
        self.assertEqual(start_kwargs["host"], "127.0.0.1")
        self.assertEqual(start_kwargs["port"], 8765)
        self.assertEqual(start_kwargs["runs_dir"], "runs_host_agent")
        self.assertEqual(start_kwargs["agent_exe"], current_dir / "AgentSightHostAgent.exe")
        self.assertEqual(start_kwargs["tray_gui_exe"], current_dir / "AgentSightTray.exe")
        self.assertTrue(start_kwargs["arm_real_input"])
        self.assertEqual(start_kwargs["wait_seconds"], 60.0)
        self.assertEqual(report["start_after_install"], start_report)
        self.assertEqual(report["exit_code"], 0)

    def test_detached_packaged_supervisor_start_launches_run_loop_not_start_helper(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current_dir = Path(temp_dir)
            supervisor_exe = current_dir / "AgentSightSupervisor.exe"
            agent_exe = current_dir / "AgentSightHostAgent.exe"
            tray_exe = current_dir / "AgentSightTray.exe"
            supervisor_exe.write_text("fake supervisor\n", encoding="utf-8")
            process = mock.Mock(pid=12345)

            with mock.patch("agentsight.installer.Popen", return_value=process) as popen:
                launch = installer._launch_packaged_supervisor_run(
                    current_dir=current_dir,
                    host="127.0.0.1",
                    port=8765,
                    runs_dir="runs_host_agent",
                    agent_exe=agent_exe,
                    tray_gui_exe=tray_exe,
                    arm_real_input=True,
                )

        command = popen.call_args.args[0]
        self.assertEqual(command[0], str(supervisor_exe))
        self.assertEqual(command[1], "run")
        self.assertNotIn("start", command)
        self.assertIn("--arm-real-input", command)
        self.assertTrue(launch["started"])
        self.assertEqual(launch["pid"], 12345)

    def test_start_after_install_ignores_running_supervisor_from_different_install_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            current_dir = Path(temp_dir) / "current"
            other_dir = Path(temp_dir) / "other"
            current_dir.mkdir(parents=True)
            other_dir.mkdir(parents=True)
            for name in ["AgentSightSupervisor.exe", "AgentSightHostAgent.exe", "AgentSightTray.exe"]:
                (current_dir / name).write_text("fake\n", encoding="utf-8")

            other_status = {
                "object_type": "AgentSightPackagedCommandReport",
                "exit_code": 0,
                "json": {
                    "state": {
                        "supervisor_status": "running",
                        "process_identity": {"executable": str(other_dir / "AgentSightSupervisor.exe")},
                    },
                    "single_instance": {
                        "lock_status": "active",
                        "active": True,
                        "owner_process_identity": {"executable": str(other_dir / "AgentSightSupervisor.exe")},
                    },
                },
            }

            with mock.patch("agentsight.installer._run_supervisor_command", return_value=other_status):
                with mock.patch("agentsight.installer._launch_packaged_supervisor_run") as launch:
                    with mock.patch("agentsight.installer._wait_for_packaged_supervisor_state", return_value=True):
                        launch.return_value = {"started": True, "command": [str(current_dir / "AgentSightSupervisor.exe"), "run"]}
                        report = installer._start_packaged_supervisor_detached(
                            current_dir=current_dir,
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs_host_agent",
                            agent_exe=current_dir / "AgentSightHostAgent.exe",
                            tray_gui_exe=current_dir / "AgentSightTray.exe",
                            arm_real_input=True,
                            wait_seconds=1,
                        )

        launch.assert_called_once()
        self.assertEqual(report["launch_strategy"], "detached_run_loop")
        self.assertEqual(report["start_status"], "started")
        self.assertEqual(report["exit_code"], 0)

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
        self.assertIn("python -m unittest discover tests", text)
        self.assertIn("python tools/build_host_agent_exe.py", text)
        self.assertIn('python -m pip install -e ".[packaging-exe,capture-pillow]"', text)
        self.assertIn("SHA256SUMS.txt", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("gh release create", text)
        self.assertLess(text.index("Run tests"), text.index("Build dist executables"))
        self.assertLess(text.index("Build dist executables"), text.index("Create or update GitHub release"))

    def test_release_workflow_publishes_versioned_setup_only_as_release_assets(self) -> None:
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        release_step = text[text.index("Create or update GitHub release") :]

        self.assertIn("RELEASE_VERSION=$($tag.TrimStart('v'))", text)
        self.assertIn("AgentSightSetup-$env:RELEASE_VERSION-windows-x64.exe", text)
        self.assertIn("AgentSightSetup-$env:RELEASE_VERSION-windows-x64.sha256.txt", text)
        self.assertIn(".\\release_assets", text)
        self.assertIn(".\\dist\\AgentSightSetup.exe", text)
        self.assertIn("SHA256SUMS.txt", release_step)
        self.assertNotIn(".\\dist\\AgentSightSetup.exe", release_step)
        for internal_asset in [
            ".\\dist\\AgentSightSupervisor.exe",
            ".\\dist\\AgentSightHostAgent.exe",
            ".\\dist\\AgentSightTray.exe",
            ".\\dist\\AgentSightMcpServer.exe",
            ".\\dist\\AgentSightHostAgentInstaller.exe",
            ".\\dist\\AgentSightHostAgentScenarios.exe",
            ".\\dist\\INSTALL_AGENTSIGHT.cmd",
            ".\\dist\\UNINSTALL_AGENTSIGHT.cmd",
        ]:
            self.assertNotIn(internal_asset, release_step)
        self.assertNotIn("portable.zip", release_step.lower())

    def test_release_workflow_checks_target_github_repository_before_publishing(self) -> None:
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("EXPECTED_RELEASE_REPOSITORY: xiayukun/agent-sight-windows", text)
        self.assertIn("GITHUB_REPOSITORY", text)
        self.assertIn("unexpected GitHub release repository", text)

    def test_release_workflow_uses_chinese_release_notes_only_without_duplicate_body_heading(self) -> None:
        text = RELEASE_WORKFLOW.read_text(encoding="utf-8")
        resolve_step = text[text.index("Resolve release notes") : text.index("Upload workflow artifact")]
        notes_101 = ROOT / "docs" / "release-notes-v1.0.1.md"
        notes_101_en = ROOT / "docs" / "release-notes-v1.0.1.en.md"
        notes_text = notes_101.read_text(encoding="utf-8")
        first_content_line = next(line for line in notes_text.splitlines() if line.strip())

        self.assertEqual(first_content_line, "中文")
        self.assertFalse(notes_101_en.exists())
        self.assertNotIn("release-notes-combined.md", resolve_step)
        self.assertNotIn("release-notes-template.en.md", resolve_step)
        self.assertNotIn("Add-Content", resolve_step)
        self.assertIn("release-notes-body.md", resolve_step)
        self.assertIn("RELEASE_NOTES=release-notes-body.md", resolve_step)

    def test_readme_and_repository_profile_include_monitoring_keywords_without_window_semantics(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_en = (ROOT / "README.en.md").read_text(encoding="utf-8")
        profile = (ROOT / "docs" / "repository-profile.md").read_text(encoding="utf-8")

        self.assertIn("时间线设置", readme)
        self.assertIn("屏幕监视器", readme)
        self.assertIn("MKV VFR 视频存储", readme)
        self.assertIn("timeline settings", readme_en)
        self.assertIn("screen monitor", readme_en)
        self.assertIn("MKV VFR video storage", readme_en)
        self.assertIn("screen-monitoring", profile)
        self.assertIn("mkv", profile)
        self.assertNotIn("window-semantics", profile.lower())

    def test_gitignore_blocks_local_runtime_and_qa_private_artifacts(self) -> None:
        ignored = set((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

        self.assertIn("*.log", ignored)
        self.assertIn("tmp_*/", ignored)
        self.assertIn("last-agent-report.json", ignored)

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
        self.assertIn("AgentSight", readme)
        self.assertIn("Windows AI agent", readme)
        self.assertIn("computer use", readme)
        self.assertIn("pixel-grounded control", readme)
        self.assertIn("视觉记忆系统", readme)

    def test_user_guides_describe_mkv_vfr_as_canonical_and_review_outputs_as_derived(self) -> None:
        zh = (ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
        en = (ROOT / "docs" / "user-guide.en.md").read_text(encoding="utf-8")

        self.assertIn(".mkv", zh)
        self.assertIn(".frames.jsonl", zh)
        self.assertIn(".manifest.json", zh)
        self.assertIn("canonical evidence", zh)
        self.assertIn("derived review artifact", zh)
        self.assertNotIn("基于已有 raw evidence 的派生重建包", zh)
        self.assertIn(".mkv", en)
        self.assertIn(".frames.jsonl", en)
        self.assertIn(".manifest.json", en)
        self.assertIn("canonical evidence", en)
        self.assertIn("human-review derived artifacts", en)
        self.assertNotIn("derived reconstruction package over existing raw evidence", en)

    def test_public_docs_separate_timeline_review_bundle_from_canonical_mkv_vfr(self) -> None:
        docs = [
            ROOT / "README.md",
            ROOT / "README.en.md",
            ROOT / "AGENTS.md",
            ROOT / "docs" / "user-guide.md",
            ROOT / "docs" / "user-guide.en.md",
        ]

        for path in docs:
            text = path.read_text(encoding="utf-8")
            self.assertIn(".mkv", text, f"{path} must describe canonical MKV Segment storage")
            self.assertIn(".frames.jsonl", text, f"{path} must describe canonical frame indexes")
            self.assertIn("canonical evidence", text, f"{path} must name canonical evidence")
            self.assertNotIn("timestamped keyframe/P-frame delta segment manifest", text)
            self.assertNotIn("derived reconstruction package", text)
            self.assertNotIn("timestamped keyframe/P-frame delta review bundle", text)

        self.assertIn("PySide6/Qt", (ROOT / "README.md").read_text(encoding="utf-8"))
        self.assertIn("PySide6/Qt", (ROOT / "README.en.md").read_text(encoding="utf-8"))
        self.assertIn("PySide6/Qt", (ROOT / "AGENTS.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

