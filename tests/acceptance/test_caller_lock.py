from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from agentsight.caller_lock import (
    check_single_caller_lock,
    default_caller_lock_file,
    enforce_single_caller_lock,
    read_caller_lock,
)
import agentsight.host_agent.server as host_server
from agentsight.host_agent.server import _handler_class


class CallerLockPreflightTest(unittest.TestCase):
    def test_screen_preflight_reports_other_active_caller_without_refreshing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "caller-lock.json"
            acquired, status, _report = enforce_single_caller_lock(
                "qa-tester-t-min-repro-probe",
                request_path="/look",
                path=lock_path,
                now_ms=1_000,
                ttl_ms=10_000,
            )
            before = read_caller_lock(lock_path)

            allowed, status, report = check_single_caller_lock(
                "qa-tester-t-min-repro",
                request_path="/screen",
                path=lock_path,
                now_ms=2_000,
            )
            after = read_caller_lock(lock_path)

        self.assertTrue(acquired)
        self.assertEqual(status, 423)
        self.assertFalse(allowed)
        self.assertEqual(report["status"], "caller_lock_held_by_other_ai")
        self.assertEqual(report["failure_code"], "CALLER_LOCK_HELD_BY_OTHER_AI")
        self.assertFalse(report["host_input_sent"])
        self.assertEqual(report["host_sent_event_count"], 0)
        self.assertEqual(before, after)

    def test_screen_preflight_allows_same_or_stale_caller_without_acquiring_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "caller-lock.json"
            enforce_single_caller_lock(
                "qa-tester-t-min-repro",
                request_path="/look",
                path=lock_path,
                now_ms=1_000,
                ttl_ms=1_000,
            )
            before_same = read_caller_lock(lock_path)

            same_allowed, same_status, same_report = check_single_caller_lock(
                "qa-tester-t-min-repro",
                request_path="/screen",
                path=lock_path,
                now_ms=1_500,
            )
            after_same = read_caller_lock(lock_path)

            stale_allowed, stale_status, stale_report = check_single_caller_lock(
                "another-caller",
                request_path="/screen",
                path=lock_path,
                now_ms=2_001,
            )
            after_stale = read_caller_lock(lock_path)

        self.assertTrue(same_allowed)
        self.assertEqual(same_status, 200)
        self.assertEqual(same_report["status"], "caller_lock_owned_by_request_caller")
        self.assertFalse(same_report["lock_refreshed"])
        self.assertEqual(before_same, after_same)
        self.assertTrue(stale_allowed)
        self.assertEqual(stale_status, 200)
        self.assertEqual(stale_report["status"], "caller_lock_stale_available")
        self.assertFalse(stale_report["lock_refreshed"])
        self.assertEqual(after_same, after_stale)

    def test_http_screen_reports_caller_lock_conflict_before_ready_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_appdata = Path(temp_dir) / "LocalAppData"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}, clear=False):
                lock_path = default_caller_lock_file()
                enforce_single_caller_lock(
                    "qa-tester-t-min-repro-probe",
                    request_path="/look",
                    path=lock_path,
                    ttl_ms=60_000,
                )
                before = read_caller_lock(lock_path)

                handler = _handler_class(runs_dir=str(Path(temp_dir) / "runs"), arm_real_input=True, token="token")
                server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    payload = json.dumps({"v": "V1", "id": "min-repro-screen", "op": "screen"}).encode("utf-8")
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_address[1]}/screen",
                        data=payload,
                        method="POST",
                    )
                    request.add_header("authorization", "Bearer token")
                    request.add_header("content-type", "application/json")
                    request.add_header("x-agentsight-caller", "qa-tester-t-min-repro")
                    with self.assertRaises(urllib.error.HTTPError) as raised:
                        urllib.request.urlopen(request, timeout=5)
                    body = json.loads(raised.exception.read().decode("utf-8"))
                    after = read_caller_lock(lock_path)
                    http_status = raised.exception.code
                    raised.exception.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

        self.assertEqual(http_status, 423)
        self.assertEqual(body["status"], "caller_lock_held_by_other_ai")
        self.assertEqual(body["failure_code"], "CALLER_LOCK_HELD_BY_OTHER_AI")
        self.assertNotEqual(body.get("code"), "READY")
        self.assertFalse(body["host_input_sent"])
        self.assertEqual(body["host_sent_event_count"], 0)
        self.assertEqual(before, after)

    def test_http_health_returns_structured_json_when_health_builder_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_appdata = Path(temp_dir) / "LocalAppData"
            with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(local_appdata)}, clear=False):
                handler = _handler_class(runs_dir=str(Path(temp_dir) / "runs"), arm_real_input=True, token="token")
                server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{server.server_address[1]}/health",
                        method="GET",
                    )
                    request.add_header("authorization", "Bearer token")
                    with mock.patch(
                        "agentsight.host_agent.server.build_host_agent_health_report",
                        side_effect=RuntimeError("health builder exploded"),
                    ):
                        with self.assertRaises(urllib.error.HTTPError) as raised:
                            urllib.request.urlopen(request, timeout=5)
                    body = json.loads(raised.exception.read().decode("utf-8"))
                    http_status = raised.exception.code
                    raised.exception.close()
                    error_report = json.loads(host_server.default_agent_error_file().read_text(encoding="utf-8"))
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

        self.assertEqual(http_status, 500)
        self.assertEqual(body["object_type"], "AgentSightHostAgentErrorReport")
        self.assertEqual(body["request_path"], "/health")
        self.assertEqual(body["error_type"], "RuntimeError")
        self.assertIn("health builder exploded", body["error"])
        self.assertFalse(body["host_input_sent"])
        self.assertEqual(body["host_sent_event_count"], 0)
        self.assertEqual(error_report["request_path"], "/health")
        self.assertEqual(error_report["error_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
