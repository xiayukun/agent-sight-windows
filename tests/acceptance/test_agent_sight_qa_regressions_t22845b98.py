from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from agentsight.adapters.mcp import MCPStdioAdapter
from agentsight.storage_quota import apply_storage_quota
from tests.acceptance.test_p3a_screen_look_do_protocol import P3AInputChannel, P3ALookPngChannel


class AgentSightQARegressionsT22845B98Test(unittest.TestCase):
    def test_storage_quota_simulates_small_limit_pruning_without_touching_real_runs(self) -> None:
        """Simulate the user's '5g auto delete' concern in an isolated temp agentsight root."""
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                agent_dir = local / "AgentSight"
                derived = agent_dir / "agent-sight-look-preview-cache" / "preview.png"
                legacy = agent_dir / "runs_host_agent" / "session-legacy" / "media" / "frame.png"
                segment = agent_dir / "runs_host_agent" / "segments" / "agentsight-20260620-00.mkv"
                derived.parent.mkdir(parents=True, exist_ok=True)
                legacy.parent.mkdir(parents=True, exist_ok=True)
                segment.parent.mkdir(parents=True, exist_ok=True)
                derived.write_bytes(b"derived-cache")
                legacy.write_bytes(b"legacy-png")
                segment.write_bytes(b"fake-mkv-bytes")
                segment.with_suffix(".frames.jsonl").write_text(
                    json.dumps({"timestamp_iso": "2026-06-20T00:00:00+00:00", "timestamp_ms": 1781913600000}) + "\n",
                    encoding="utf-8",
                )
                segment.with_suffix(".manifest.json").write_text(
                    json.dumps({"frames": [{"timestamp_iso": "2026-06-20T00:00:00+00:00"}]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                log_path = agent_dir / "operation-log.jsonl"
                log_path.write_text(
                    json.dumps({"timestamp_ms": 1781913600000, "entry": {"route": "/look"}}, ensure_ascii=False) + "\n"
                    + json.dumps({"timestamp_ms": 1782000000000, "entry": {"route": "/screen"}}, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

                report = apply_storage_quota(root=agent_dir, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999})
                remaining_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

        self.assertEqual([item["reason"] for item in report["deleted"]], ["derived_review_cache", "legacy_session_png_evidence", "old_unpinned_segment"])
        self.assertGreaterEqual(report["deleted_count"], 3, report)
        self.assertFalse(segment.exists())
        self.assertFalse(segment.with_suffix(".frames.jsonl").exists())
        self.assertFalse(segment.with_suffix(".manifest.json").exists())
        self.assertTrue(report["operation_log_prune"]["pruned"])
        self.assertNotIn("/look", remaining_log)
        self.assertIn("/screen", remaining_log)
        self.assertFalse(report["host_input_sent"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_storage_quota_accepts_runtime_runs_host_agent_root(self) -> None:
        """Runtime recorder quota calls may pass the runs_host_agent root directly."""
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                agent_dir = local / "AgentSight"
                runs_root = agent_dir / "runs_host_agent"
                segment = runs_root / "segments" / "agentsight-runtime-old.mkv"
                segment.parent.mkdir(parents=True, exist_ok=True)
                segment.write_bytes(b"runtime-root-segment")
                segment.with_suffix(".frames.jsonl").write_text(
                    json.dumps({"timestamp_iso": "2026-06-20T00:00:00+00:00", "timestamp_ms": 1781913600000}) + "\n",
                    encoding="utf-8",
                )
                segment.with_suffix(".manifest.json").write_text(
                    json.dumps({"segment_id": segment.stem, "finalized": True, "frames": [{"timestamp_iso": "2026-06-20T00:00:00+00:00"}]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                log_path = agent_dir / "operation-log.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(
                    json.dumps({"timestamp_ms": 1781913600000, "entry": {"route": "/look", "note": "old segment gap should be pruned"}}, ensure_ascii=False) + "\n"
                    + json.dumps({"timestamp_ms": 1782000000000, "entry": {"route": "/screen"}}, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

                report = apply_storage_quota(root=runs_root, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999})
                remaining_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

        self.assertEqual(report["agent_dir"], str(agent_dir))
        self.assertEqual(report["runs_root"], str(runs_root))
        self.assertEqual([item["reason"] for item in report["deleted"]], ["old_unpinned_segment"])
        self.assertFalse(segment.exists())
        self.assertFalse(segment.with_suffix(".frames.jsonl").exists())
        self.assertFalse(segment.with_suffix(".manifest.json").exists())
        self.assertTrue(report["operation_log_prune"]["pruned"])
        self.assertNotIn("old segment gap should be pruned", remaining_log)
        self.assertIn("/screen", remaining_log)
        self.assertFalse(report["host_input_sent"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_storage_quota_protects_pinned_and_unfinalized_mkv_segments(self) -> None:
        """Quota apply must not delete pinned, unfinished, or open-writer MKV segments."""
        with tempfile.TemporaryDirectory() as temp_dir:
            local = Path(temp_dir) / "LocalAppData"
            roaming = Path(temp_dir) / "Roaming"
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}, clear=False):
                agent_dir = local / "AgentSight"
                segments_dir = agent_dir / "runs_host_agent" / "segments"
                old_unpinned = segments_dir / "agentsight-old-unpinned.mkv"
                pinned = segments_dir / "agentsight-pinned.mkv"
                unfinalized = segments_dir / "agentsight-unfinalized.mkv"
                for segment in (old_unpinned, pinned, unfinalized):
                    segment.parent.mkdir(parents=True, exist_ok=True)
                    segment.write_bytes((segment.stem + "-payload").encode("utf-8"))
                    segment.with_suffix(".frames.jsonl").write_text(
                        json.dumps({"frame_id": "f000000", "timestamp_iso": "2026-06-20T00:00:00+00:00", "timestamp_ms": 1781913600000}) + "\n",
                        encoding="utf-8",
                    )
                old_unpinned.with_suffix(".manifest.json").write_text(
                    json.dumps({"segment_id": old_unpinned.stem, "finalized": True, "frames": [{"timestamp_iso": "2026-06-20T00:00:00+00:00"}]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                pinned.with_suffix(".manifest.json").write_text(
                    json.dumps({"segment_id": pinned.stem, "finalized": True, "frames": [{"timestamp_iso": "2026-06-20T00:00:00+00:00"}]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                unfinalized.with_suffix(".manifest.json").write_text(
                    json.dumps({"segment_id": unfinalized.stem, "finalized": False, "frames": [{"timestamp_iso": "2026-06-20T00:00:00+00:00"}]}, ensure_ascii=False),
                    encoding="utf-8",
                )
                log_path = agent_dir / "operation-log.jsonl"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(
                    json.dumps(
                        {
                            "timestamp_ms": 1781913600000,
                            "entry": {
                                "route": "/look",
                                "segment_frame_refs": [
                                    {
                                        "segment_id": pinned.stem,
                                        "restore_ref": {"storage_format": "mkv_vfr", "segment_path": str(pinned), "frame_id": "f000000"},
                                    }
                                ],
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    + json.dumps({"timestamp_ms": 1781913600000, "entry": {"route": "/look", "note": "deleted segment gap should be pruned"}}, ensure_ascii=False)
                    + "\n"
                    + json.dumps({"timestamp_ms": 1782000000000, "entry": {"route": "/screen"}}, ensure_ascii=False)
                    + "\n",
                    encoding="utf-8",
                )

                dry_run = apply_storage_quota(root=agent_dir, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999}, dry_run=True)
                report = apply_storage_quota(root=agent_dir, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999})
                remaining_log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
                old_unpinned_exists_after = old_unpinned.exists()
                old_unpinned_index_exists_after = old_unpinned.with_suffix(".frames.jsonl").exists()
                old_unpinned_manifest_exists_after = old_unpinned.with_suffix(".manifest.json").exists()
                pinned_exists_after = pinned.exists()
                pinned_index_exists_after = pinned.with_suffix(".frames.jsonl").exists()
                pinned_manifest_exists_after = pinned.with_suffix(".manifest.json").exists()
                unfinalized_exists_after = unfinalized.exists()
                unfinalized_index_exists_after = unfinalized.with_suffix(".frames.jsonl").exists()
                unfinalized_manifest_exists_after = unfinalized.with_suffix(".manifest.json").exists()

        self.assertEqual([item["reason"] for item in dry_run["deleted"]], ["old_unpinned_segment"])
        self.assertEqual([item["reason"] for item in report["deleted"]], ["old_unpinned_segment"])
        self.assertFalse(old_unpinned_exists_after)
        self.assertFalse(old_unpinned_index_exists_after)
        self.assertFalse(old_unpinned_manifest_exists_after)
        self.assertTrue(pinned_exists_after)
        self.assertTrue(pinned_index_exists_after)
        self.assertTrue(pinned_manifest_exists_after)
        self.assertTrue(unfinalized_exists_after)
        self.assertTrue(unfinalized_index_exists_after)
        self.assertTrue(unfinalized_manifest_exists_after)
        self.assertIn("referenced_by_operation_log", {item["reason"] for item in report["protected"]})
        self.assertIn("unfinalized_or_open_writer_segment", {item["reason"] for item in report["protected"]})
        self.assertTrue(report["operation_log_prune"]["pruned"])
        self.assertGreaterEqual(report["operation_log_prune"]["protected_old_count"], 1)
        self.assertNotIn("agentsight-old-unpinned", remaining_log)
        self.assertIn("agentsight-pinned", remaining_log)
        self.assertIn("/screen", remaining_log)
        self.assertEqual(report["pruned_gap_report"]["protected_segment_count"], 2)
        self.assertFalse(report["host_input_sent"])
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_do_action_capture_window_is_sixty_seconds_and_merges_adjacent_steps(self) -> None:
        """Regression for the 60s post-action observation/continuation requirement.

        The response should describe one merged 60s action-capture window when a second
        input step occurs inside the first action window, instead of returning short
        independent 3s windows.
        """
        observation = P3ALookPngChannel()
        input_channel = P3AInputChannel()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MCPStdioAdapter(
                runs_dir=temp_dir,
                observation_channels=[observation],
                default_observation_channel_ref=observation.name,
                input_channels=[input_channel],
                default_input_channel_ref=input_channel.name,
            )
            look = adapter.call_tool(
                "look",
                {
                    "v": "V1",
                    "id": "qa-60s-window-look",
                    "q": "frame",
                    "src": {"type": "screen", "t": "latest"},
                    "r": {"x": 0, "y": 0, "w": 60, "h": 40},
                    "scale_down": 1,
                },
            )
            view_id = look["data"]["view"]["id"]
            action = adapter.call_tool(
                "do",
                {
                    "v": "V1",
                    "id": "qa-60s-window-do",
                    "basis": {"view_id": view_id},
                    "seq": [
                        {"t": "move", "x": 4, "y": 5, "coord": "view", "move": "instant"},
                        {"t": "wait", "ms": 1},
                        {"t": "move", "x": 6, "y": 7, "coord": "view", "move": "instant"},
                    ],
                    "post_observe": {"delay_ms": 0, "frame_count": 2, "interval_ms": 1},
                },
            )
            adapter.session.gateway.segment_recorder.close()

        self.assertTrue(action["ok"], action)
        windows = action["data"]["capture_windows"]
        self.assertEqual(len(windows), 1, windows)
        self.assertGreaterEqual(_window_duration_ms(windows[0]), 60_000, windows)
        self.assertEqual(windows[0]["kind"], "burst")
        self.assertFalse(action["data"]["tool_asserts_business_success"])
        self.assertFalse(action["data"]["boundary"]["window_semantics_used"])


def _window_duration_ms(window: dict[str, str]) -> int:
    return (_hms_ms_to_ms(window["to"]) - _hms_ms_to_ms(window["from"])) % (24 * 60 * 60 * 1000)


def _hms_ms_to_ms(value: str) -> int:
    parsed = time.strptime(value.split(".", 1)[0], "%H:%M:%S")
    millis = int(value.rsplit(".", 1)[1]) if "." in value else 0
    return ((parsed.tm_hour * 60 + parsed.tm_min) * 60 + parsed.tm_sec) * 1000 + millis


if __name__ == "__main__":
    unittest.main()
