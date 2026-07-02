from __future__ import annotations

import unittest
from pathlib import Path

from agentsight.segments import (
    SEGMENT_INDEX_SCHEMA,
    SEGMENT_SCHEMA,
    build_empty_segment_index,
    build_empty_segment_manifest,
    build_frame_record,
    validate_segment_manifest,
)


class PF0AgentSightSegmentV1SpecTest(unittest.TestCase):
    def test_segment_spec_uses_current_review_bundle_language(self) -> None:
        spec = Path("docs/segments/AGENTSIGHT_SEGMENT_V1_SPEC.md").read_text(encoding="utf-8")

        self.assertIn("derived review bundle", spec)
        self.assertNotIn("derived reconstruction package", spec)

    def test_empty_manifest_declares_canonical_segment_boundaries(self) -> None:
        manifest = build_empty_segment_manifest(segment_id="seg-test", started_at_iso="2026-06-21T00:00:00Z")

        self.assertEqual(manifest["schema"], SEGMENT_SCHEMA)
        self.assertEqual(manifest["container_model"], "proto_directory_now_single_file_later")
        self.assertEqual(manifest["codec_model"], "keyframe_plus_pframe_delta_crop")
        self.assertEqual(manifest["frame_rate_model"], "variable_timestamped_frames")
        self.assertTrue(manifest["raw_frames_are_canonical_evidence"])
        self.assertFalse(manifest["derived_review_video_is_canonical"])
        self.assertFalse(manifest["h264_used"])
        self.assertFalse(manifest["b_frames_used"])
        self.assertFalse(manifest["tool_asserts_business_success"])
        self.assertFalse(manifest["boundary"]["clipboard_used"])

    def test_segment_index_is_time_and_keyframe_oriented(self) -> None:
        index = build_empty_segment_index(segment_id="seg-test")

        self.assertEqual(index["schema"], SEGMENT_INDEX_SCHEMA)
        self.assertEqual(index["segment_id"], "seg-test")
        self.assertEqual(index["time_index"], [])
        self.assertEqual(index["keyframes"], [])
        self.assertEqual(index["events"], [])

    def test_manifest_validation_accepts_keyframe_and_pframe_delta(self) -> None:
        manifest = build_empty_segment_manifest(segment_id="seg-test", started_at_iso="2026-06-21T00:00:00Z")
        manifest["frames"] = [
            build_frame_record(
                frame_id="f000000",
                timestamp_iso="2026-06-21T00:00:00Z",
                timestamp_monotonic_ms=1000,
                frame_index=0,
                frame_kind="keyframe",
                source="idle",
                width=16,
                height=16,
                full_frame_sha256="sha256-key",
                nearest_keyframe_id="f000000",
                keyframe_blob_ref="keyframes/k000000.png",
            ),
            build_frame_record(
                frame_id="f000001",
                timestamp_iso="2026-06-21T00:00:00.100Z",
                timestamp_monotonic_ms=1100,
                frame_index=1,
                frame_kind="pframe_delta",
                source="post_do",
                width=16,
                height=16,
                full_frame_sha256="sha256-p",
                event_id="do-1",
                nearest_keyframe_id="f000000",
                previous_frame_id="f000000",
                delta_bbox={"x": 2, "y": 3, "w": 1, "h": 1},
                delta_blob_ref="deltas/d000001.png",
            ),
        ]
        manifest["frame_count"] = 2
        manifest["keyframe_count"] = 1
        manifest["pframe_delta_count"] = 1

        report = validate_segment_manifest(manifest)

        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(report["frame_count"], 2)
        self.assertEqual(report["keyframe_count"], 1)
        self.assertEqual(report["pframe_delta_count"], 1)
        self.assertFalse(report["boundary"]["business_success_judged"])

    def test_manifest_validation_rejects_h264_or_derived_canonical_claims(self) -> None:
        manifest = build_empty_segment_manifest(segment_id="seg-test")
        manifest["h264_used"] = True
        manifest["derived_review_video_is_canonical"] = True

        report = validate_segment_manifest(manifest)

        self.assertFalse(report["valid"])
        self.assertIn("h264_must_not_be_canonical", report["errors"])
        self.assertIn("derived_review_video_marked_canonical", report["errors"])


if __name__ == "__main__":
    unittest.main()
