from __future__ import annotations

import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agentsight.host_agent.server as host_server
import agentsight.host_agent.service_state as service_state
import agentsight.session_supervisor as supervisor
import agentsight.tray.actions as tray_actions
import agentsight.tray.gui as tray_gui


class _Proc:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class P1XSessionSupervisorTest(unittest.TestCase):
    def test_tray_window_process_probe_avoids_platform_system(self) -> None:
        with mock.patch("platform.system", side_effect=AssertionError("platform.system must not be called")):
            with mock.patch("agentsight.session_supervisor._is_windows", return_value=False):
                self.assertIsNone(supervisor._tray_window_process_id())

    def test_supervisor_once_starts_host_and_tray_independently(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {"watchdog_probe_status": "discovery_missing", "restart_recommended": True}
                    with mock.patch("agentsight.session_supervisor._tray_window_present", side_effect=[False, True]):
                        with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                            start_child.side_effect = [
                                ({"action": "start_host_agent", "child_pid": 111}, _Proc(111)),
                                ({"action": "start_tray_gui", "child_pid": 222}, _Proc(222)),
                            ]

                            exit_code = supervisor.run_session_supervisor(
                                host="127.0.0.1",
                                port=8765,
                                runs_dir="runs",
                                repo_root=Path(temp_dir),
                                python_command="py",
                                agent_exe=None,
                                tray_gui_exe=None,
                                arm_real_input=True,
                                interval_seconds=0.5,
                                once=True,
                                output=str(output),
                            )

                state = _read_state()

        self.assertEqual(exit_code, 0)
        self.assertEqual(start_child.call_count, 2)
        self.assertEqual(state["supervisor_status"], "running")
        self.assertEqual(state["host_agent"]["component_status"], "restarting")
        self.assertEqual(state["host_agent"]["last_action"]["action"], "start_host_agent")
        self.assertEqual(state["tray_gui"]["component_status"], "visible")
        self.assertEqual(state["tray_gui"]["last_action"]["action"], "start_tray_gui")
        self.assertEqual(state["process_identity"]["role"], "session_supervisor")
        self.assertIsInstance(state["process_identity"]["parent_pid"], int)
        self.assertEqual(state["single_instance"]["lock_status"], "active")
        self.assertIsInstance(state["single_instance"]["owner_parent_pid"], int)
        self.assertTrue(state["single_instance"]["owned_by_current_process"])
        self.assertFalse(state["host_input_sent"])
        self.assertEqual(state["host_sent_event_count"], 0)
        self.assertFalse(state["boundary"]["clipboard_used"])
        self.assertFalse(state["boundary"]["business_success_judged"])

    def test_supervisor_run_exits_when_active_lock_is_owned_by_other_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_session_supervisor_lock_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_session_supervisor_lock_file().write_text(
                    json.dumps(
                        {
                            "object_type": "AgentSightSupervisorLock",
                            "schema": supervisor.SESSION_SUPERVISOR_SCHEMA,
                            "lock_status": "active",
                            "owner_pid": 99999,
                            "acquired_at_ms": 123,
                            "host_input_sent": False,
                            "host_sent_event_count": 0,
                        }
                    ),
                    encoding="utf-8",
                )
                output = Path(temp_dir) / "already-running.json"
                with mock.patch("agentsight.session_supervisor._process_running", return_value=True):
                    with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                        exit_code = supervisor.run_session_supervisor(
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs",
                            repo_root=Path(temp_dir),
                            python_command="py",
                            agent_exe=None,
                            tray_gui_exe=None,
                            arm_real_input=True,
                            interval_seconds=0.5,
                            once=True,
                            output=str(output),
                        )
                report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        start_child.assert_not_called()
        self.assertEqual(report["supervisor_status"], "already_running")
        self.assertFalse(report["new_supervisor_started"])
        self.assertEqual(report["single_instance"]["active_supervisor_pid"], 99999)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_supervisor_run_reclaims_stale_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_session_supervisor_lock_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_session_supervisor_lock_file().write_text(
                    json.dumps(
                        {
                            "object_type": "AgentSightSupervisorLock",
                            "schema": supervisor.SESSION_SUPERVISOR_SCHEMA,
                            "lock_status": "active",
                            "owner_pid": 99998,
                            "acquired_at_ms": 123,
                        }
                    ),
                    encoding="utf-8",
                )
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._process_running", side_effect=lambda pid: False if pid == 99998 else True):
                    with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                        probe.return_value = {"watchdog_probe_status": "agent_responding", "restart_recommended": False}
                        with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                            with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                                exit_code = supervisor.run_session_supervisor(
                                    host="127.0.0.1",
                                    port=8765,
                                    runs_dir="runs",
                                    repo_root=Path(temp_dir),
                                    python_command="py",
                                    agent_exe=None,
                                    tray_gui_exe=None,
                                    arm_real_input=True,
                                    interval_seconds=0.5,
                                    once=True,
                                    output=str(output),
                                )
                state = _read_state()
                released_lock = json.loads(supervisor.default_session_supervisor_lock_file().read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        start_child.assert_not_called()
        self.assertEqual(state["single_instance"]["previous_lock_status"], "stale")
        self.assertEqual(state["supervisor_status"], "running")
        self.assertEqual(released_lock["lock_status"], "released")

    def test_unified_supervisor_writes_service_state_for_health_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                health = {
                    "service_status": service_state.OK_ACTIVE_DEFAULT_DESKTOP,
                    "can_attempt_real_control": True,
                    "control_blockers": [],
                }
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {
                        "watchdog_probe_status": "agent_responding",
                        "restart_recommended": False,
                        "discovery": {"pid": 1234, "process_session_id": 1, "active_console_session_id": 1},
                        "agent_running": True,
                        "health": health,
                    }
                    with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                        supervisor.run_session_supervisor(
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs",
                            repo_root=Path(temp_dir),
                            python_command="py",
                            agent_exe=None,
                            tray_gui_exe=None,
                            arm_real_input=True,
                            interval_seconds=0.5,
                            once=True,
                            output=str(output),
                        )

                state = _read_state()
                service_state_payload = json.loads(host_server.default_service_state_file().read_text(encoding="utf-8"))

        self.assertEqual(state["service_state_write"]["written"], True)
        self.assertEqual(state["service_state"]["service_status"], service_state.OK_ACTIVE_DEFAULT_DESKTOP)
        self.assertEqual(service_state_payload["service_status"], service_state.OK_ACTIVE_DEFAULT_DESKTOP)
        self.assertEqual(service_state_payload["state_artifact_role"], "unified_session_supervisor_projection")
        service_state.validate_service_state(service_state_payload)

    def test_host_restart_does_not_restart_visible_tray(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {"watchdog_probe_status": "health_unreachable", "restart_recommended": True}
                    with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                        with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                            start_child.return_value = ({"action": "start_host_agent", "child_pid": 333}, _Proc(333))

                            supervisor.run_session_supervisor(
                                host="127.0.0.1",
                                port=8765,
                                runs_dir="runs",
                                repo_root=Path(temp_dir),
                                python_command="py",
                                agent_exe=None,
                                tray_gui_exe=None,
                                arm_real_input=True,
                                interval_seconds=0.5,
                                once=True,
                                output=str(output),
                            )

                state = _read_state()

        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["host_agent"]["last_action"]["action"], "start_host_agent")
        self.assertEqual(state["tray_gui"]["last_action"]["action"], "none")
        self.assertEqual(state["tray_gui"]["backend_status"], "backend_unavailable_or_restarting")

    def test_health_unreachable_live_discovery_pid_suppresses_new_host_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {
                        "watchdog_probe_status": "health_unreachable",
                        "restart_recommended": True,
                        "agent_running": True,
                        "discovery": {"pid": 3333, "health_url": "http://127.0.0.1:8765/health"},
                        "health": {"request_failed": True, "error": "Remote end closed connection without response"},
                    }
                    with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                        with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                            supervisor.run_session_supervisor(
                                host="127.0.0.1",
                                port=8765,
                                runs_dir="runs",
                                repo_root=Path(temp_dir),
                                python_command="py",
                                agent_exe=None,
                                tray_gui_exe=None,
                                arm_real_input=True,
                                interval_seconds=0.5,
                                once=True,
                                output=str(output),
                            )

                state = _read_state()

        start_child.assert_not_called()
        action = state["host_agent"]["last_action"]
        self.assertEqual(action["action"], "host_agent_restart_suppressed")
        self.assertEqual(action["reason"], "existing_host_agent_unreachable_pid_still_running")
        self.assertEqual(action["existing_pid"], 3333)
        self.assertEqual(state["host_agent"]["component_status"], "unavailable")
        self.assertEqual(state["tray_gui"]["backend_status"], "backend_unavailable_or_restarting")

    def test_watchdog_probe_treats_alive_non_host_pid_as_stale_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                host_server.default_discovery_file().parent.mkdir(parents=True, exist_ok=True)
                host_server.default_discovery_file().write_text(
                    json.dumps(
                        {
                            "object_type": "AgentSightHostAgentDiscovery",
                            "schema": "discovery_v2",
                            "pid": os.getpid(),
                            "url": "http://127.0.0.1:8765",
                            "process_identity": {
                                "role": "host_agent",
                                "executable": str(Path(temp_dir) / "AgentSightHostAgent.exe"),
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                probe = host_server._watchdog_probe(
                    host_server.default_discovery_file(),
                    suppress_when_unified_supervisor_enabled=False,
                    respect_watchdog_stop_file=False,
                )

        self.assertEqual(probe["watchdog_probe_status"], "stale_discovery_pid_not_running")
        self.assertEqual(probe["stale_discovery_reason"], "pid_not_running_or_not_host_agent")
        self.assertFalse(probe["agent_running"])
        self.assertTrue(probe["restart_recommended"])

    def test_host_agent_server_uses_exclusive_port_binding(self) -> None:
        self.assertFalse(host_server._AgentSightThreadingHTTPServer.allow_reuse_address)

    def test_bind_server_falls_back_when_requested_port_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            handler = host_server._handler_class(runs_dir=temp_dir, arm_real_input=False, token="token")
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                busy_port = int(listener.getsockname()[1])
                server = host_server._bind_server("127.0.0.1", busy_port, handler)
                try:
                    self.assertNotEqual(server.server_address[1], busy_port)
                    self.assertIsInstance(server, host_server._AgentSightThreadingHTTPServer)
                finally:
                    server.server_close()

    def test_tray_restart_does_not_restart_responding_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {"watchdog_probe_status": "agent_responding", "restart_recommended": False}
                    with mock.patch("agentsight.session_supervisor._tray_window_present", side_effect=[False, True]):
                        with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                            start_child.return_value = ({"action": "start_tray_gui", "child_pid": 444}, _Proc(444))

                            supervisor.run_session_supervisor(
                                host="127.0.0.1",
                                port=8765,
                                runs_dir="runs",
                                repo_root=Path(temp_dir),
                                python_command="py",
                                agent_exe=None,
                                tray_gui_exe=None,
                                arm_real_input=True,
                                interval_seconds=0.5,
                                once=True,
                                output=str(output),
                            )

                state = _read_state()

        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["host_agent"]["component_status"], "running")
        self.assertEqual(state["host_agent"]["last_action"]["action"], "none")
        self.assertEqual(state["tray_gui"]["last_action"]["action"], "start_tray_gui")
        self.assertEqual(state["tray_gui"]["backend_status"], "available")

    def test_emergency_stop_blocks_host_restart_but_keeps_tray_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                supervisor.default_emergency_stop_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_emergency_stop_file().write_text("emergency\n", encoding="utf-8")
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    with mock.patch("agentsight.session_supervisor._tray_window_present", side_effect=[False, True]):
                        with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                            start_child.return_value = ({"action": "start_tray_gui", "child_pid": 555}, _Proc(555))

                            supervisor.run_session_supervisor(
                                host="127.0.0.1",
                                port=8765,
                                runs_dir="runs",
                                repo_root=Path(temp_dir),
                                python_command="py",
                                agent_exe=None,
                                tray_gui_exe=None,
                                arm_real_input=True,
                                interval_seconds=0.5,
                                once=True,
                                output=str(output),
                            )

                state = _read_state()

        probe.assert_not_called()
        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["supervisor_status"], "emergency_stopped")
        self.assertEqual(state["host_agent"]["component_status"], "blocked_by_control_plane")
        self.assertEqual(state["host_agent"]["last_action"]["action"], "none")
        self.assertEqual(state["tray_gui"]["last_action"]["action"], "start_tray_gui")
        self.assertEqual(state["tray_gui"]["backend_status"], "emergency_stopped")

    def test_stop_writes_marker_and_requests_host_and_tray_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.session_supervisor.request_host_agent_shutdown") as host_shutdown:
                    host_shutdown.return_value = {"shutdown_attempted": True, "shutdown_status": "requested"}
                    with mock.patch("agentsight.session_supervisor._request_tray_window_close") as tray_close:
                        tray_close.return_value = {"close_requested": True}
                        with mock.patch("agentsight.session_supervisor._ensure_host_agent_stopped") as host_process:
                            host_process.return_value = {"component": "host_agent", "status": "stopped"}
                            with mock.patch("agentsight.session_supervisor._ensure_tray_gui_closed") as tray_process:
                                tray_process.return_value = {"component": "tray_gui", "status": "closed"}
                                with mock.patch("agentsight.session_supervisor._ensure_supervisor_lock_released") as supervisor_process:
                                    supervisor_process.return_value = {"component": "session_supervisor", "status": "released"}

                                    report = supervisor.stop_session_supervisor(reason="operator_requested_stop_agentsight")

                stop_payload = json.loads(supervisor.default_session_supervisor_stop_file().read_text(encoding="utf-8"))

        self.assertTrue(report["stop_requested"])
        self.assertEqual(report["stop_kind"], "full_agentsight_shutdown")
        self.assertEqual(stop_payload["reason"], "operator_requested_stop_agentsight")
        self.assertEqual(stop_payload["stop_kind"], "full_agentsight_shutdown")
        self.assertFalse(stop_payload["semantics"]["emergency_stop"])
        self.assertFalse(stop_payload["semantics"]["operator_pause"])
        self.assertTrue(stop_payload["semantics"]["full_shutdown"])
        self.assertEqual(report["host_agent_shutdown"]["shutdown_status"], "requested")
        self.assertEqual(report["host_agent_process"]["status"], "stopped")
        self.assertTrue(report["tray_close"]["close_requested"])
        self.assertEqual(report["tray_process"]["status"], "closed")
        self.assertEqual(report["supervisor_process"]["status"], "released")
        self.assertFalse(report["tool_asserts_business_success"])
        self.assertFalse(report["tool_asserts_causality"])
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_stop_forces_host_agent_when_graceful_shutdown_times_out(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                discovery = {"pid": 12345}
                shutdown = {"shutdown_attempted": True, "shutdown_status": "requested"}
                with mock.patch("agentsight.session_supervisor._wait_until_process_exits", return_value=False):
                    with mock.patch("agentsight.session_supervisor._terminate_process") as terminate:
                        terminate.return_value = {"force_attempted": True, "force_status": "signal_sent", "pid": 12345}
                        with mock.patch("agentsight.session_supervisor._process_running", return_value=False):
                            report = supervisor._ensure_host_agent_stopped(
                                discovery=discovery,
                                shutdown=shutdown,
                                wait_seconds=0.0,
                                force_after_timeout=True,
                            )

        terminate.assert_called_once_with(12345, reason="host_agent_stop_timeout")
        self.assertEqual(report["component"], "host_agent")
        self.assertFalse(report["exited_gracefully"])
        self.assertEqual(report["force"]["force_status"], "signal_sent")
        self.assertEqual(report["status"], "stopped")
        self.assertFalse(report["host_input_sent"])

    def test_stop_releases_stale_lock_after_owner_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_session_supervisor_lock_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_session_supervisor_lock_file().write_text(
                    json.dumps(
                        {
                            "object_type": "AgentSightSupervisorLock",
                            "schema": supervisor.SESSION_SUPERVISOR_SCHEMA,
                            "lock_status": "active",
                            "owner_pid": 24680,
                            "acquired_at_ms": 123,
                        }
                    ),
                    encoding="utf-8",
                )
                with mock.patch("agentsight.session_supervisor._process_running", return_value=False):
                    report = supervisor._release_lock_if_owner_not_running(reason="unit_test_stale_lock")
                released = json.loads(supervisor.default_session_supervisor_lock_file().read_text(encoding="utf-8"))

        self.assertTrue(report["release_attempted"])
        self.assertEqual(report["release_status"], "released")
        self.assertEqual(released["lock_status"], "released")
        self.assertEqual(released["release_reason"], "unit_test_stale_lock")
        self.assertFalse(released["host_input_sent"])

    def test_tray_stop_action_delegates_to_session_supervisor_full_shutdown(self) -> None:
        with mock.patch("agentsight.session_supervisor.stop_session_supervisor") as stop:
            stop.return_value = {
                "object_type": "AgentSightSupervisorStopReport",
                "stop_kind": "full_agentsight_shutdown",
                "host_agent_process": {"status": "stopped"},
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": {"clipboard_used": False, "business_success_judged": False},
            }
            report = tray_actions.stop_agentsight("operator_requested_from_test")

        stop.assert_called_once_with(reason="operator_requested_from_test", wait_seconds=2.0, force_after_timeout=True)
        self.assertEqual(report["control_action"], "stop_agentsight")
        self.assertTrue(report["stop_semantics"]["full_shutdown"])
        self.assertFalse(report["stop_semantics"]["emergency_stop"])
        self.assertFalse(report["stop_semantics"]["operator_pause"])
        self.assertFalse(report["tool_asserts_business_success"])
        self.assertFalse(report["boundary"]["clipboard_used"])

    def test_tray_description_uses_stop_agentsight_as_only_human_shutdown(self) -> None:
        description = tray_gui.build_tray_gui_description()
        menu_keys = {item["key"] for item in description["menu_items"]}

        self.assertIn("stop_agentsight", menu_keys)
        self.assertNotIn("exit_tray_only", menu_keys)
        self.assertTrue(description["controls"]["stop_agentsight_full_shutdown"])
        self.assertFalse(description["controls"]["exit_tray_process_only"])
        self.assertEqual(
            description["stop_semantics"]["exit_tray_only"],
            "removed from human menu because the Session Supervisor intentionally restarts the tray",
        )
        self.assertFalse(description["host_input_sent"])
        self.assertFalse(description["boundary"]["window_semantics_used"])

    def test_physical_emergency_hotkey_keeps_tray_visible_control_plane(self) -> None:
        class _FakeUser32:
            def __init__(self) -> None:
                self.post_quit_called = False

            def PostQuitMessage(self, code: int) -> None:
                self.post_quit_called = True

        app = tray_gui.Win32TrayApp.__new__(tray_gui.Win32TrayApp)
        app.user32 = _FakeUser32()
        with mock.patch("agentsight.tray.gui.emergency_stop") as emergency:
            emergency.return_value = {
                "emergency_stop_status": "active",
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "boundary": {"clipboard_used": False},
            }
            report = app._emergency_stop_from_physical_hotkey("physical_hotkey_triggered")

        emergency.assert_called_once_with("physical_hotkey_triggered")
        self.assertEqual(report["emergency_stop_status"], "active")
        self.assertFalse(app.user32.post_quit_called)

    def test_install_writes_supervisor_launcher_instead_of_split_watchdogs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.session_supervisor._install_run_key") as install_run_key:
                    install_run_key.return_value = {
                        "install_status": "installed",
                        "run_key_name": "AgentSight",
                        "run_key_path": "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                    }
                    with mock.patch("agentsight.session_supervisor._install_onlogon_task") as install_task:
                        install_task.return_value = {"task_name": "AgentSightSupervisorOnLogon", "install_status": "installed"}

                        report = supervisor.install_session_supervisor(
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs",
                            repo_root=Path(temp_dir),
                            python_command="py",
                            agent_exe=None,
                            tray_gui_exe=None,
                            arm_real_input=True,
                            start_now=False,
                            start_method="auto",
                            wait_seconds=0.0,
                        )

                command = Path(report["supervisor_command"]).read_text(encoding="utf-8")

        self.assertIn("-m agentsight.session_supervisor run", command)
        self.assertEqual(report["resolved_start_method"], "run_key")
        self.assertEqual(report["run_key"]["install_status"], "installed")
        install_run_key.assert_called_once()
        install_task.assert_not_called()
        self.assertIn("--arm-real-input", command)
        self.assertNotIn("agentsight.host_agent.server --watchdog", command)
        self.assertNotIn("agentsight.tray.gui watchdog", command)
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)

    def test_install_startup_vbs_mode_skips_onlogon_task_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.session_supervisor._install_onlogon_task") as install_task:
                    report = supervisor.install_session_supervisor(
                        host="127.0.0.1",
                        port=8765,
                        runs_dir="runs",
                        repo_root=Path(temp_dir),
                        python_command="py",
                        agent_exe=None,
                        tray_gui_exe=None,
                        arm_real_input=True,
                        start_now=False,
                        start_method="startup_vbs",
                        wait_seconds=0.0,
                    )
                    progress = json.loads(supervisor.default_session_supervisor_install_progress_file().read_text(encoding="utf-8"))

        install_task.assert_not_called()
        self.assertEqual(report["onlogon_task"]["install_status"], "skipped_by_start_method")
        self.assertEqual(report["onlogon_task"]["reason"], "startup_vbs_selected")
        self.assertEqual(report["registered_startup_components"], ["AgentSight"])
        self.assertEqual(progress["stage"], "reports_written")
        self.assertFalse(progress["host_input_sent"])
        self.assertFalse(report["host_input_sent"])

    def test_start_run_key_uses_registered_command_line_before_cmd_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_session_supervisor_command_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_session_supervisor_command_file().write_text("@echo off\n", encoding="utf-8")
                with mock.patch("agentsight.session_supervisor._read_run_key") as read_run_key:
                    read_run_key.return_value = {"exists": True, "command": "AgentSightSupervisor.exe run"}
                    with mock.patch("agentsight.session_supervisor._start_via_command_line") as start_line:
                        start_line.return_value = {"start_method_used": "run_key", "started": True, "pid": 888}
                        with mock.patch("agentsight.session_supervisor._start_via_command_file") as start_file:
                            with mock.patch("agentsight.session_supervisor._wait_for_state_update", return_value=False):
                                report = supervisor.start_installed_session_supervisor(start_method="run_key", wait_seconds=0.0)

        start_line.assert_called_once_with("AgentSightSupervisor.exe run", source="run_key")
        start_file.assert_not_called()
        self.assertEqual(report["launcher"]["start_method_used"], "run_key")
        self.assertEqual(report["launcher"]["pid"], 888)
        self.assertFalse(report["host_input_sent"])

    def test_start_installed_supervisor_does_not_launch_when_lock_active(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_session_supervisor_command_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_session_supervisor_command_file().write_text("@echo off\n", encoding="utf-8")
                supervisor.default_session_supervisor_lock_file().write_text(
                    json.dumps(
                        {
                            "object_type": "AgentSightSupervisorLock",
                            "schema": supervisor.SESSION_SUPERVISOR_SCHEMA,
                            "lock_status": "active",
                            "owner_pid": 99997,
                            "acquired_at_ms": 456,
                        }
                    ),
                    encoding="utf-8",
                )
                with mock.patch("agentsight.session_supervisor._process_running", return_value=True):
                    with mock.patch("agentsight.session_supervisor._start_hidden") as start_hidden:
                        report = supervisor.start_installed_session_supervisor(start_method="run_key", wait_seconds=0.0)

        start_hidden.assert_not_called()
        self.assertEqual(report["start_status"], "already_running")
        self.assertEqual(report["process_identity"]["role"], "session_supervisor_start")
        self.assertIsInstance(report["process_identity"]["parent_pid"], int)
        self.assertEqual(report["single_instance"]["owner_pid"], 99997)
        self.assertEqual(report["launcher"]["reason"], "active_supervisor_lock")
        self.assertFalse(report["host_input_sent"])

    def test_start_child_process_reports_launcher_pid_for_process_model_audit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            command = [supervisor.sys.executable, "-c", "pass"]
            action, process = supervisor._start_child_process(command, cwd=Path(temp_dir), action_name="start_test_child")
            if process:
                process.wait(timeout=10)

        self.assertEqual(action["action"], "start_test_child")
        self.assertIsInstance(action["child_pid"], int)
        self.assertEqual(action["launcher_pid"], supervisor.os.getpid())
        self.assertIsInstance(action["launcher_parent_pid"], int)
        self.assertEqual(action["command"], command)

    def test_frozen_defaults_use_install_dir_and_adjacent_exes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = Path(temp_dir) / "dist"
            dist.mkdir()
            supervisor_exe = dist / "AgentSightSupervisor.exe"
            agent_exe = dist / "AgentSightHostAgent.exe"
            tray_exe = dist / "AgentSightTray.exe"
            supervisor_exe.write_text("", encoding="utf-8")
            agent_exe.write_text("", encoding="utf-8")
            tray_exe.write_text("", encoding="utf-8")

            with mock.patch.object(supervisor.sys, "frozen", True, create=True):
                with mock.patch.object(supervisor.sys, "executable", str(supervisor_exe)):
                    self.assertEqual(supervisor._default_runtime_root(), dist)
                    self.assertEqual(supervisor._default_host_agent_exe_arg(), str(agent_exe))
                    self.assertEqual(supervisor._default_tray_gui_exe_arg(), str(tray_exe))
                    command = supervisor._session_supervisor_command(
                        host="127.0.0.1",
                        port=8765,
                        runs_dir="runs",
                        repo_root=dist,
                        python_command="py",
                        agent_exe=agent_exe,
                        tray_gui_exe=tray_exe,
                        arm_real_input=True,
                    )

        self.assertIn("AgentSightSupervisor.exe run", command)
        self.assertIn("--agent-exe", command)
        self.assertIn("AgentSightHostAgent.exe", command)
        self.assertIn("AgentSightTray.exe", command)
        self.assertNotIn("PYTHONPATH", command)
        self.assertNotIn("-m agentsight.session_supervisor", command)
        self.assertNotIn("C:\\git\\其他\\AgentSight", command)

    def test_frozen_installer_install_command_targets_adjacent_session_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dist = Path(temp_dir) / "dist"
            dist.mkdir()
            installer_exe = dist / "AgentSightSetup.exe"
            supervisor_exe = dist / "AgentSightSupervisor.exe"
            agent_exe = dist / "AgentSightHostAgent.exe"
            tray_exe = dist / "AgentSightTray.exe"
            installer_exe.write_text("", encoding="utf-8")
            supervisor_exe.write_text("", encoding="utf-8")
            agent_exe.write_text("", encoding="utf-8")
            tray_exe.write_text("", encoding="utf-8")

            with mock.patch.object(supervisor.sys, "frozen", True, create=True):
                with mock.patch.object(supervisor.sys, "executable", str(installer_exe)):
                    command = supervisor._session_supervisor_command(
                        host="127.0.0.1",
                        port=8765,
                        runs_dir="runs",
                        repo_root=dist,
                        python_command="py",
                        agent_exe=agent_exe,
                        tray_gui_exe=tray_exe,
                        arm_real_input=True,
                    )

        self.assertIn("AgentSightSupervisor.exe run", command)
        self.assertIn(str(supervisor_exe), command)
        self.assertNotIn("AgentSightSetup.exe run", command)
        self.assertIn("--agent-exe", command)
        self.assertIn("AgentSightHostAgent.exe", command)
        self.assertIn("AgentSightTray.exe", command)
        self.assertNotIn("PYTHONPATH", command)

    def test_install_report_writes_unified_marker_for_legacy_watchdog_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                with mock.patch("agentsight.session_supervisor._install_run_key") as install_run_key:
                    install_run_key.return_value = {
                        "install_status": "installed",
                        "run_key_name": "AgentSight",
                        "run_key_path": "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run",
                    }
                    with mock.patch("agentsight.session_supervisor._install_onlogon_task") as install_task:
                        install_task.return_value = {"task_name": "AgentSightSupervisorOnLogon", "install_status": "installed"}
                        report = supervisor.install_session_supervisor(
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs",
                            repo_root=Path(temp_dir),
                            python_command="py",
                            agent_exe=None,
                            tray_gui_exe=None,
                            arm_real_input=True,
                            start_now=False,
                            start_method="auto",
                            wait_seconds=0.0,
                        )
                marker = json.loads(supervisor.default_unified_supervisor_enabled_file().read_text(encoding="utf-8"))
                install_report_exists = Path(report["install_report_file"]).exists()

        self.assertEqual(report["install_layout"], "source_tree")
        self.assertEqual(report["self_start_entry"], "AgentSight")
        self.assertTrue(report["self_start_entry_is_unified_supervisor"])
        self.assertEqual(report["registered_startup_components"], ["AgentSight"])
        self.assertEqual(report["resolved_start_method"], "run_key")
        self.assertEqual(report["run_key"]["install_status"], "installed")
        install_run_key.assert_called_once()
        install_task.assert_not_called()
        self.assertFalse(report["packaged_layout"]["final_user_requires_pythonpath"])
        self.assertEqual(report["packaged_layout"]["frozen_mode_entry"], "AgentSightSupervisor.exe run")
        self.assertFalse(report["legacy_split_watchdogs_recommended"])
        self.assertEqual(report["legacy_split_watchdogs"]["host_agent_watchdog"], "legacy_compatibility_only")
        self.assertTrue(install_report_exists)
        self.assertTrue(report["unified_supervisor_enabled"]["enabled"])
        self.assertTrue(marker["legacy_split_watchdogs_should_not_restart_children"])
        self.assertFalse(marker["host_input_sent"])

    def test_uninstall_marks_discovery_stale_and_writes_uninstall_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_discovery_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_discovery_file().write_text(
                    json.dumps(
                        {
                            "object_type": "AgentSightHostAgentDiscovery",
                            "schema": "discovery_v2",
                            "pid": 12345,
                            "token": "secret-token",
                            "url": "http://127.0.0.1:8765",
                        }
                    ),
                    encoding="utf-8",
                )
                supervisor.default_unified_supervisor_enabled_file().write_text("enabled\n", encoding="utf-8")
                with mock.patch("agentsight.session_supervisor._delete_run_key") as delete_run_key:
                    delete_run_key.return_value = {"delete_status": "deleted", "run_key_name": "AgentSight"}
                    with mock.patch("agentsight.session_supervisor._delete_onlogon_task") as delete_task:
                        delete_task.return_value = {"task_name": "AgentSightSupervisorOnLogon", "delete_status": "deleted"}
                        with mock.patch("agentsight.session_supervisor.request_host_agent_shutdown") as host_shutdown:
                            host_shutdown.return_value = {"shutdown_attempted": True, "shutdown_status": "requested"}
                            with mock.patch("agentsight.session_supervisor._request_tray_window_close") as tray_close:
                                tray_close.return_value = {"close_requested": True}
                                report = supervisor.uninstall_session_supervisor(stop_running=True)

                stale_discovery = json.loads(supervisor.default_discovery_file().read_text(encoding="utf-8"))
                uninstall_report_exists = Path(report["uninstall_report_file"]).exists()

        self.assertEqual(report["uninstall_status"], "removed")
        self.assertTrue(report["uninstall_keeps_evidence"])
        self.assertEqual(report["run_key"]["delete_status"], "deleted")
        self.assertEqual(report["discovery_stale"]["status"], "marked_stale")
        self.assertTrue(uninstall_report_exists)
        self.assertEqual(stale_discovery["object_type"], "AgentSightHostAgentDiscoveryStaleMarker")
        self.assertTrue(stale_discovery["stale"])
        self.assertEqual(stale_discovery["previous"]["token"], "<redacted>")
        self.assertFalse(stale_discovery["host_input_sent"])
        self.assertEqual(stale_discovery["host_sent_event_count"], 0)

    def test_legacy_host_watchdog_does_not_restart_when_unified_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_unified_supervisor_enabled_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_unified_supervisor_enabled_file().write_text("enabled\n", encoding="utf-8")

                probe = host_server._watchdog_probe(host_server.default_discovery_file())

        self.assertEqual(probe["watchdog_probe_status"], "unified_session_supervisor_enabled")
        self.assertFalse(probe["restart_recommended"])

    def test_session_supervisor_starts_host_even_when_unified_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_unified_supervisor_enabled_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_unified_supervisor_enabled_file().write_text("enabled\n", encoding="utf-8")
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                    with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                        start_child.return_value = ({"action": "start_host_agent", "child_pid": 777}, _Proc(777))

                        exit_code = supervisor.run_session_supervisor(
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs",
                            repo_root=Path(temp_dir),
                            python_command="py",
                            agent_exe=None,
                            tray_gui_exe=None,
                            arm_real_input=True,
                            interval_seconds=0.5,
                            once=True,
                            output=str(output),
                        )

                state = _read_state()

        self.assertEqual(exit_code, 0)
        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["host_agent"]["last_action"]["action"], "start_host_agent")
        self.assertEqual(state["host_agent"]["component_status"], "restarting")
        self.assertNotEqual(state["host_agent"]["probe"]["watchdog_probe_status"], "unified_session_supervisor_enabled")
        self.assertFalse(state["host_input_sent"])

    def test_session_supervisor_ignores_legacy_host_watchdog_stop_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_host_watchdog_stop_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_host_watchdog_stop_file().write_text("legacy stop\n", encoding="utf-8")
                output = Path(temp_dir) / "supervisor.json"
                with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                    with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                        start_child.return_value = ({"action": "start_host_agent", "child_pid": 778}, _Proc(778))

                        exit_code = supervisor.run_session_supervisor(
                            host="127.0.0.1",
                            port=8765,
                            runs_dir="runs",
                            repo_root=Path(temp_dir),
                            python_command="py",
                            agent_exe=None,
                            tray_gui_exe=None,
                            arm_real_input=True,
                            interval_seconds=0.5,
                            once=True,
                            output=str(output),
                        )

                legacy_probe = host_server._watchdog_probe(host_server.default_discovery_file())
                state = _read_state()

        self.assertEqual(legacy_probe["watchdog_probe_status"], "stop_requested")
        self.assertEqual(exit_code, 0)
        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["host_agent"]["last_action"]["action"], "start_host_agent")
        self.assertNotEqual(state["host_agent"]["probe"]["watchdog_probe_status"], "stop_requested")
        self.assertFalse(state["host_input_sent"])

    def test_legacy_tray_watchdog_exits_when_unified_marker_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            output = Path(temp_dir) / "tray-watchdog.json"
            with mock.patch.dict("os.environ", env, clear=False):
                supervisor.default_unified_supervisor_enabled_file().parent.mkdir(parents=True, exist_ok=True)
                supervisor.default_unified_supervisor_enabled_file().write_text("enabled\n", encoding="utf-8")
                with mock.patch("agentsight.tray.gui._tray_window_present", return_value=True):
                    with mock.patch("agentsight.tray.gui._start_tray_gui_child") as start_child:
                        exit_code = tray_gui.run_tray_gui_watchdog(interval_seconds=0.5, once=True, output=str(output))
                report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(exit_code, 0)
        self.assertEqual(report["watchdog_status"], "stopped_by_unified_session_supervisor")
        start_child.assert_not_called()
        self.assertFalse(report["host_input_sent"])

    def test_active_non_console_session_is_not_blocked_when_raw_health_ready(self) -> None:
        classification = service_state.classify_host_agent_health(
            session={
                "process_id": 123,
                "process_session_id": 2,
                "active_console_session_id": 1,
                "process_session_connect_state": {"state_name": "WTSActive"},
                "process_session_is_wts_active": True,
            },
            station={"window_station_name": "WinSta0"},
            input_desktop={"desktop_name": "Default"},
            foreground_window={"title": "Codex"},
            cursor_probe={"ok": True},
            capture_probe={"ok": True},
            raw_can_attempt_real_control=True,
            raw_control_blockers=[],
        )

        self.assertEqual(classification["service_status"], service_state.OK_ACTIVE_DEFAULT_DESKTOP)
        self.assertTrue(classification["can_attempt_real_control"])
        self.assertIn("process_session_not_active_console_session", classification["signals"])

    def test_active_non_console_session_reports_underlying_health_when_raw_health_not_ready(self) -> None:
        classification = service_state.classify_host_agent_health(
            session={
                "process_id": 123,
                "process_session_id": 2,
                "active_console_session_id": 1,
                "process_session_connect_state": {"query_ok": True, "state_name": "WTSActive"},
                "process_session_is_wts_active": True,
            },
            station={"window_station_name": "WinSta0"},
            input_desktop={"desktop_name": "Default"},
            foreground_window={"title": "Codex"},
            cursor_probe={"ok": False},
            capture_probe={"ok": False},
            raw_can_attempt_real_control=False,
            raw_control_blockers=["cursor_position_unavailable", "screen_capture_unavailable"],
        )

        self.assertEqual(classification["service_status"], "child_unhealthy")
        self.assertFalse(classification["can_attempt_real_control"])
        self.assertIn("process_session_not_active_console_session", classification["signals"])
        self.assertIn("process_session_is_active_visible_session", classification["signals"])
        self.assertIn("process_session_is_wts_active", classification["signals"])
        self.assertIn("raw_host_agent_health_not_ready", classification["signals"])

    def test_disconnected_non_console_session_remains_session_disconnected(self) -> None:
        classification = service_state.classify_host_agent_health(
            session={
                "process_id": 123,
                "process_session_id": 2,
                "active_console_session_id": 1,
                "process_session_connect_state": {"query_ok": True, "state_name": "WTSDisconnected"},
                "process_session_is_wts_active": False,
            },
            station={"window_station_name": "WinSta0"},
            input_desktop={"desktop_name": "Default"},
            foreground_window={"title": "Codex"},
            cursor_probe={"ok": False},
            capture_probe={"ok": False},
            raw_can_attempt_real_control=False,
            raw_control_blockers=["cursor_position_unavailable", "screen_capture_unavailable"],
        )

        self.assertEqual(classification["service_status"], "session_disconnected")
        self.assertIn("session_connect_state=WTSDisconnected", classification["signals"])

    def test_unverified_non_console_session_uses_underlying_health_not_console_mislabel(self) -> None:
        classification = service_state.classify_host_agent_health(
            session={
                "process_id": 123,
                "process_session_id": 2,
                "active_console_session_id": 1,
                "process_session_connect_state": {"query_ok": False, "error": "wts_query_failed"},
                "process_session_is_wts_active": False,
            },
            station={"window_station_name": "WinSta0"},
            input_desktop={"desktop_name": "Default"},
            foreground_window={"title": "Codex"},
            cursor_probe={"ok": False},
            capture_probe={"ok": False},
            raw_can_attempt_real_control=False,
            raw_control_blockers=["process_not_in_active_interactive_session", "cursor_position_unavailable"],
        )

        self.assertEqual(classification["service_status"], "child_unhealthy")
        self.assertIn("process_session_not_active_console_session", classification["signals"])
        self.assertIn("active_visible_session_query_failed", classification["signals"])

    def test_supervisor_records_non_active_session_blocker_instead_of_restart_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                health = _non_active_session_health()
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {
                        "watchdog_probe_status": "agent_responding",
                        "restart_recommended": False,
                        "discovery": {"pid": 1234, "process_session_id": 1, "active_console_session_id": 2},
                        "agent_running": True,
                        "health": health,
                    }
                    with mock.patch("agentsight.session_supervisor._current_process_session_report") as session_report:
                        session_report.return_value = {
                            "query_ok": True,
                            "process_session_id": 1,
                            "active_console_session_id": 2,
                            "process_is_active_console_session": False,
                        }
                        with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                            with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                                supervisor.run_session_supervisor(
                                    host="127.0.0.1",
                                    port=8765,
                                    runs_dir="runs",
                                    repo_root=Path(temp_dir),
                                    python_command="py",
                                    agent_exe=None,
                                    tray_gui_exe=None,
                                    arm_real_input=True,
                                    interval_seconds=0.5,
                                    once=True,
                                    output=str(output),
                                )

                state = _read_state()

        start_child.assert_not_called()
        self.assertEqual(state["service_state"]["service_status"], "host_agent_not_active_console_session")
        action = state["host_agent"]["last_action"]
        self.assertEqual(action["action"], "host_agent_restart_blocked_supervisor_not_active_console_session")
        self.assertEqual(action["supervisor_session"]["process_session_id"], 1)
        self.assertEqual(action["supervisor_session"]["active_console_session_id"], 2)
        self.assertEqual(state["host_agent"]["component_status"], "blocked_by_session_mismatch")

    def test_supervisor_does_not_restart_active_visible_rdp_host_for_raw_health_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                health = _active_visible_child_unhealthy_health()
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {
                        "watchdog_probe_status": "agent_responding",
                        "restart_recommended": False,
                        "discovery": {"pid": 1234, "process_session_id": 1, "active_console_session_id": 2},
                        "agent_running": True,
                        "health": health,
                    }
                    with mock.patch("agentsight.session_supervisor.request_host_agent_shutdown") as shutdown:
                        with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                            with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                                supervisor.run_session_supervisor(
                                    host="127.0.0.1",
                                    port=8765,
                                    runs_dir="runs",
                                    repo_root=Path(temp_dir),
                                    python_command="py",
                                    agent_exe=None,
                                    tray_gui_exe=None,
                                    arm_real_input=True,
                                    interval_seconds=0.5,
                                    once=True,
                                    output=str(output),
                                )

                state = _read_state()

        shutdown.assert_not_called()
        start_child.assert_not_called()
        self.assertEqual(state["service_state"]["service_status"], "child_unhealthy")
        self.assertEqual(state["host_agent"]["last_action"], {"action": "none"})
        self.assertEqual(state["host_agent"]["component_status"], "running")

    def test_supervisor_restarts_non_active_host_when_supervisor_is_active_console(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                health = _non_active_session_health()
                discovery = {
                    "pid": 1234,
                    "process_session_id": 1,
                    "active_console_session_id": 2,
                    "shutdown_url": "http://127.0.0.1:8765/shutdown",
                    "token": "secret",
                }
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {
                        "watchdog_probe_status": "agent_responding",
                        "restart_recommended": False,
                        "discovery": discovery,
                        "agent_running": True,
                        "health": health,
                    }
                    with mock.patch("agentsight.session_supervisor._current_process_session_report") as session_report:
                        session_report.return_value = {
                            "query_ok": True,
                            "process_session_id": 2,
                            "active_console_session_id": 2,
                            "process_is_active_console_session": True,
                        }
                        with mock.patch("agentsight.session_supervisor.request_host_agent_shutdown") as shutdown:
                            shutdown.return_value = {"shutdown_attempted": True, "shutdown_status": "requested"}
                            with mock.patch("agentsight.session_supervisor._ensure_host_agent_stopped") as stopped:
                                stopped.return_value = {"component": "host_agent", "status": "stopped", "running_after_stop_attempt": False}
                                with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                                    with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                                        start_child.return_value = ({"action": "start_host_agent", "child_pid": 5678}, _Proc(5678))
                                        supervisor.run_session_supervisor(
                                            host="127.0.0.1",
                                            port=8765,
                                            runs_dir="runs",
                                            repo_root=Path(temp_dir),
                                            python_command="py",
                                            agent_exe=None,
                                            tray_gui_exe=None,
                                            arm_real_input=True,
                                            interval_seconds=0.5,
                                            once=True,
                                            output=str(output),
                                        )

                state = _read_state()

        shutdown.assert_called_once_with(discovery)
        stopped.assert_called_once()
        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["host_agent"]["last_action"]["action"], "start_host_agent")
        self.assertEqual(state["host_agent"]["last_action"]["pre_restart"]["restart_prepare_status"], "stopped")

    def test_supervisor_restarts_non_active_host_when_supervisor_is_active_rdp_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = _temp_env(temp_dir)
            with mock.patch.dict("os.environ", env, clear=False):
                output = Path(temp_dir) / "supervisor.json"
                health = _non_active_session_health()
                discovery = {
                    "pid": 1234,
                    "process_session_id": 1,
                    "active_console_session_id": 2,
                    "shutdown_url": "http://127.0.0.1:8765/shutdown",
                    "token": "secret",
                }
                with mock.patch("agentsight.session_supervisor._watchdog_probe") as probe:
                    probe.return_value = {
                        "watchdog_probe_status": "agent_responding",
                        "restart_recommended": False,
                        "discovery": discovery,
                        "agent_running": True,
                        "health": health,
                    }
                    with mock.patch("agentsight.session_supervisor._current_process_session_report") as session_report:
                        session_report.return_value = {
                            "query_ok": True,
                            "process_session_id": 1,
                            "active_console_session_id": 2,
                            "process_is_active_console_session": False,
                            "process_session_connect_state": {"query_ok": True, "state_name": "WTSActive"},
                            "process_session_is_wts_active": True,
                            "process_is_active_visible_session": True,
                        }
                        with mock.patch("agentsight.session_supervisor.request_host_agent_shutdown") as shutdown:
                            shutdown.return_value = {"shutdown_attempted": True, "shutdown_status": "requested"}
                            with mock.patch("agentsight.session_supervisor._ensure_host_agent_stopped") as stopped:
                                stopped.return_value = {"component": "host_agent", "status": "stopped", "running_after_stop_attempt": False}
                                with mock.patch("agentsight.session_supervisor._tray_window_present", return_value=True):
                                    with mock.patch("agentsight.session_supervisor._start_child_process") as start_child:
                                        start_child.return_value = ({"action": "start_host_agent", "child_pid": 5678}, _Proc(5678))
                                        supervisor.run_session_supervisor(
                                            host="127.0.0.1",
                                            port=8765,
                                            runs_dir="runs",
                                            repo_root=Path(temp_dir),
                                            python_command="py",
                                            agent_exe=None,
                                            tray_gui_exe=None,
                                            arm_real_input=True,
                                            interval_seconds=0.5,
                                            once=True,
                                            output=str(output),
                                        )

                state = _read_state()

        shutdown.assert_called_once_with(discovery)
        stopped.assert_called_once()
        self.assertEqual(start_child.call_count, 1)
        self.assertEqual(state["host_agent"]["last_action"]["action"], "start_host_agent")
        self.assertEqual(state["host_agent"]["last_action"]["pre_restart"]["restart_prepare_status"], "stopped")


def _temp_env(temp_dir: str) -> dict[str, str]:
    return {
        "LOCALAPPDATA": str(Path(temp_dir) / "LocalAppData"),
        "APPDATA": str(Path(temp_dir) / "Roaming"),
    }


def _read_state() -> dict[str, object]:
    return json.loads(supervisor.default_session_supervisor_state_file().read_text(encoding="utf-8"))


def _non_active_session_health() -> dict[str, object]:
    return {
        "service_status": "host_agent_not_active_console_session",
        "can_attempt_real_control": False,
        "control_blockers": ["cursor_position_unavailable", "screen_capture_unavailable", "host_agent_not_active_console_session"],
        "session": {
            "process_id": 1234,
            "process_session_id": 1,
            "active_console_session_id": 2,
            "process_is_active_console_session": False,
            "process_session_connect_state": {"state_name": "WTSActive"},
        },
        "service_state": {
            "last_error": {
                "classification": {
                    "signals": [
                        "process_session_not_active_console_session",
                        "process_session_is_active_non_console_session",
                        "raw_host_agent_health_not_ready_in_non_console_session",
                    ]
                }
            }
        },
        "raw_can_attempt_real_control": False,
        "raw_control_blockers": ["cursor_position_unavailable", "screen_capture_unavailable"],
    }


def _active_visible_child_unhealthy_health() -> dict[str, object]:
    return {
        "service_status": "child_unhealthy",
        "can_attempt_real_control": False,
        "control_blockers": ["cursor_position_unavailable", "screen_capture_unavailable", "child_unhealthy"],
        "session": {
            "process_id": 1234,
            "process_session_id": 1,
            "active_console_session_id": 2,
            "process_is_active_console_session": False,
            "process_session_connect_state": {"query_ok": True, "state_name": "WTSActive"},
            "process_session_is_wts_active": True,
            "process_is_active_visible_session": True,
        },
        "service_state": {
            "last_error": {
                "classification": {
                    "signals": [
                        "process_session_not_active_console_session",
                        "process_session_is_active_visible_session",
                        "process_session_is_wts_active",
                        "raw_host_agent_health_not_ready",
                    ]
                }
            }
        },
        "raw_can_attempt_real_control": False,
        "raw_control_blockers": ["cursor_position_unavailable", "screen_capture_unavailable"],
    }


if __name__ == "__main__":
    unittest.main()
