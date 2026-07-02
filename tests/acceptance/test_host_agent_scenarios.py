from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agentsight.host_agent import scenarios
from agentsight.host_agent.service_state import OK_ACTIVE_DEFAULT_DESKTOP, build_service_state


class HostAgentScenariosTest(unittest.TestCase):
    def _service_state_with_sensitive_markers(self, *, now_ms: int = 1234) -> dict:
        service_state = build_service_state(OK_ACTIVE_DEFAULT_DESKTOP, now_ms=now_ms)
        service_state["token"] = "SHOULD_NOT_SURVIVE"
        service_state["auth_header"] = "Bearer SHOULD_NOT_SURVIVE"
        service_state["nested"] = {
            "authorization": "Bearer SHOULD_NOT_SURVIVE",
            "message": "service-state included Bearer SHOULD_NOT_SURVIVE",
        }
        return service_state

    def test_discovery_missing_report_redacts_nested_sensitive_agent_reports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True)
                (agent_dir / "last-agent-report.json").write_text(
                    json.dumps(
                        {
                            "pid": -1,
                            "token": "SHOULD_NOT_SURVIVE",
                            "auth_header": "Bearer SHOULD_NOT_SURVIVE",
                            "nested": {
                                "authorization": "Bearer SHOULD_NOT_SURVIVE",
                                "message": "failure included Bearer SHOULD_NOT_SURVIVE",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                (agent_dir / "last-installer-report.json").write_text(
                    json.dumps(
                        {
                            "token": "SHOULD_NOT_SURVIVE",
                            "nested": {
                                "authorization": "Bearer SHOULD_NOT_SURVIVE",
                                "message": "installer included Bearer SHOULD_NOT_SURVIVE",
                            },
                        }
                    ),
                    encoding="utf-8",
                )
                (agent_dir / "service-state.json").write_text(
                    json.dumps(self._service_state_with_sensitive_markers()),
                    encoding="utf-8",
                )

                report = scenarios.run_scenarios(
                    discovery_path=agent_dir / "missing-host-agent.json",
                    scenario="health",
                    runs_dir=str(Path(temp_dir) / "runs"),
                    wait_seconds=0.0,
                )

        report_text = json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["exit_code"], 4)
        self.assertEqual(report["scenario_status"], "host_agent_discovery_missing")
        self.assertNotIn("SHOULD_NOT_SURVIVE", report_text)
        self.assertEqual(report["agent_report"]["token"], "<redacted>")
        self.assertEqual(report["agent_report"]["auth_header"], "Bearer ***")
        self.assertEqual(report["agent_report"]["nested"]["authorization"], "Bearer ***")
        self.assertIn("Bearer ***", report["agent_report"]["nested"]["message"])
        self.assertEqual(report["service_state_read"]["data"]["token"], "<redacted>")
        self.assertEqual(report["service_state_read"]["data"]["auth_header"], "Bearer ***")
        self.assertEqual(report["readiness"]["service_state_read"]["data"]["nested"]["authorization"], "Bearer ***")

    def test_health_scenario_report_redacts_top_level_service_state_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True)
                discovery_path = agent_dir / "host-agent.json"
                discovery_path.write_text(
                    json.dumps(
                        {
                            "schema": "discovery_v2",
                            "url": "http://127.0.0.1:8765",
                            "health_url": "http://127.0.0.1:8765/health",
                            "token": "secret-token",
                        }
                    ),
                    encoding="utf-8",
                )
                (agent_dir / "service-state.json").write_text(
                    json.dumps(self._service_state_with_sensitive_markers()),
                    encoding="utf-8",
                )
                health = {
                    "service_status": OK_ACTIVE_DEFAULT_DESKTOP,
                    "can_attempt_real_control": True,
                    "control_blockers": [],
                    "service_state": self._service_state_with_sensitive_markers(now_ms=5678),
                }
                with mock.patch("agentsight.host_agent.scenarios._request_json", return_value={"status": 200, "data": health}):
                    report = scenarios.run_scenarios(
                        discovery_path=discovery_path,
                        scenario="health",
                        runs_dir=str(Path(temp_dir) / "runs"),
                        wait_seconds=0.0,
                    )

        report_text = json.dumps(report, ensure_ascii=False)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["scenario_status"], "host_agent_ready")
        self.assertNotIn("SHOULD_NOT_SURVIVE", report_text)
        self.assertEqual(report["service_state_read"]["data"]["token"], "<redacted>")
        self.assertEqual(report["service_state_read"]["data"]["auth_header"], "Bearer ***")
        self.assertEqual(report["service_state_read"]["data"]["nested"]["authorization"], "Bearer ***")
        self.assertIn("Bearer ***", report["service_state_read"]["data"]["nested"]["message"])

    def test_health_scenario_accepts_embedded_health_service_state_when_state_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
            with mock.patch.dict("os.environ", env, clear=False):
                agent_dir = local / "AgentSight"
                agent_dir.mkdir(parents=True)
                discovery_path = agent_dir / "host-agent.json"
                discovery_path.write_text(
                    json.dumps(
                        {
                            "schema": "discovery_v2",
                            "url": "http://127.0.0.1:8765",
                            "health_url": "http://127.0.0.1:8765/health",
                            "token": "secret-token",
                        }
                    ),
                    encoding="utf-8",
                )
                health = {
                    "service_status": OK_ACTIVE_DEFAULT_DESKTOP,
                    "can_attempt_real_control": True,
                    "control_blockers": [],
                    "service_state": build_service_state(OK_ACTIVE_DEFAULT_DESKTOP, now_ms=1234),
                }
                with mock.patch("agentsight.host_agent.scenarios._request_json", return_value={"status": 200, "data": health}):
                    report = scenarios.run_scenarios(
                        discovery_path=discovery_path,
                        scenario="health",
                        runs_dir=str(Path(temp_dir) / "runs"),
                        wait_seconds=0.0,
                    )

        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["scenario_status"], "host_agent_ready")
        self.assertTrue(report["readiness"]["can_attempt_real_control"])
        self.assertEqual(report["readiness"]["service_state_source"], "health_embedded_service_state")
        self.assertFalse(report["readiness"]["readiness_checks"]["service_state_read"])


if __name__ == "__main__":
    unittest.main()
