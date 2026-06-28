from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any

from ai_control.evidence.store import EvidenceReplayService, canonical_json


@dataclass(frozen=True)
class FrameBufferConfig:
    ttl_seconds: int = 300
    max_entries: int = 128
    max_indexed_media_bytes: int = 50_000_000
    max_entries_per_region: int = 48

    def normalized(self) -> "FrameBufferConfig":
        return FrameBufferConfig(
            ttl_seconds=max(1, int(self.ttl_seconds)),
            max_entries=max(1, int(self.max_entries)),
            max_indexed_media_bytes=max(1, int(self.max_indexed_media_bytes)),
            max_entries_per_region=max(1, int(self.max_entries_per_region)),
        )


class VisualFrameBuffer:
    """Short-term bounded index over canonical segment-backed frames.

    The buffer does not own or delete raw media. Raw frames stay in MKV
    Segment storage; this index only keeps a bounded attention window of frame
    metadata.
    """

    schema = "ai_control_p0f_frame_buffer_v1"
    retention_schema = "ai_control_p0l_visual_memory_retention_v1"
    retention_class_schema = "ai_control_p0m_visual_memory_retention_class_projection_v1"
    storage_attention_summary_schema = "ai_control_p0n_visual_memory_storage_attention_summary_v1"

    def __init__(self, evidence: EvidenceReplayService, config: FrameBufferConfig | None = None) -> None:
        self.evidence = evidence
        self.config = (config or FrameBufferConfig()).normalized()
        self.entries: list[dict[str, Any]] = []
        self.index_dir = self.evidence.runs_dir / "visual_memory"
        self.index_path = self.index_dir / "frame_buffer_index.json"
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._write_index(pruned=[])

    def remember_frame(
        self,
        frame: dict[str, Any],
        *,
        attention_scope: str,
        sequence_id: str | None = None,
        frame_index: int | None = None,
        source: str | None = None,
        event_id: str | None = None,
        view_id: str | None = None,
        cursor_mode: str | None = None,
        raw_or_derived: str = "raw",
    ) -> dict[str, Any]:
        now = time.time()
        captured_at = frame.get("captured_at") or frame.get("timestamp") or now
        captured_at_float = float(captured_at) if isinstance(captured_at, (int, float)) else now
        media_ref = frame.get("media_ref")
        media_path_abs = str((self.evidence.root / media_ref).resolve()) if isinstance(media_ref, str) else frame.get("media_path_abs")
        indexed_media_size = int(frame.get("media_size_bytes") or 0)
        if not media_ref and not media_path_abs and isinstance(frame.get("segment_frame"), dict):
            indexed_media_size = 0
        entry = {
            "object_type": "VisualFrameBufferEntry",
            "schema": self.schema,
            "buffer_entry_id": f"fb-{frame.get('observation_id')}",
            "frame_id": frame.get("observation_id"),
            "observation_ref": frame.get("observation_id"),
            "sequence_id": sequence_id,
            "frame_index": frame_index,
            "attention_scope": attention_scope,
            "captured_at": captured_at_float,
            "captured_at_iso": _timestamp_iso(captured_at_float),
            "captured_at_monotonic_ms": int(frame.get("captured_at_monotonic_ms") or time.monotonic() * 1000),
            "indexed_at": now,
            "expires_at": now + self.config.ttl_seconds,
            "media_ref": media_ref,
            "media_path_abs": media_path_abs,
            "raw_media_path_abs": media_path_abs if raw_or_derived == "raw" else None,
            "media_sha256": frame.get("media_sha256"),
            "sha256": frame.get("media_sha256"),
            "media_size_bytes": indexed_media_size,
            "width": frame.get("width"),
            "height": frame.get("height"),
            "region": frame.get("screen_region") or frame.get("region"),
            "screen_region": frame.get("screen_region") or frame.get("region"),
            "coordinate_system": frame.get("coordinate_system"),
            "channel_ref": frame.get("channel_ref"),
            "capture_source": frame.get("channel_ref"),
            "source_kind": frame.get("source_kind"),
            "source": source or _frame_source_from_attention(attention_scope),
            "event_id": event_id,
            "view_id": view_id,
            "raw_or_derived": raw_or_derived,
            "cursor_mode": cursor_mode or "none",
            "capture_content_degenerate": bool(frame.get("capture_content_degenerate")),
            "receipt_ref": frame.get("receipt_ref"),
            "evidence_ref": frame.get("frame_evidence_ref") or frame.get("evidence_ref"),
            "replay_ref": frame.get("replay_ref"),
            "real_capture": frame.get("real_capture"),
            "raw_canonical": True,
            "indexed": True,
            "not_indexed_reason": None,
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "derived_review_artifact": False,
            "segment_frame": frame.get("segment_frame") if isinstance(frame.get("segment_frame"), dict) else None,
            "segment_id": (frame.get("segment_frame") or {}).get("segment_id") if isinstance(frame.get("segment_frame"), dict) else None,
            "segment_frame_id": (frame.get("segment_frame") or {}).get("frame_id") if isinstance(frame.get("segment_frame"), dict) else None,
            "segment_restore_ref": (frame.get("segment_frame") or {}).get("restore_ref") if isinstance(frame.get("segment_frame"), dict) else None,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }
        self.entries.append(entry)
        pruned = self.prune(now=now)
        self._write_index(pruned=pruned)
        if not any(item.get("buffer_entry_id") == entry["buffer_entry_id"] for item in self.entries):
            reason = next(
                (
                    record.get("reason")
                    for record in pruned
                    if record.get("buffer_entry_id") == entry["buffer_entry_id"]
                ),
                "pruned_from_frame_buffer_index",
            )
            return self._not_indexed_ref(entry, reason)
        return self._entry_ref(entry)

    def update_entry_metadata(self, observation_ref: str | None, **metadata: Any) -> dict[str, Any] | None:
        if not observation_ref:
            return None
        for entry in self.entries:
            if entry.get("observation_ref") != observation_ref:
                continue
            for key, value in metadata.items():
                if value is not None:
                    entry[key] = value
            self._write_index(pruned=[])
            return self._entry_ref(entry)
        return None

    def remember_sequence(self, sequence: dict[str, Any]) -> dict[str, Any]:
        frame_refs = list(sequence.get("frame_refs") or [])
        not_indexed_frame_refs = [
            frame.get("observation_id")
            for frame in sequence.get("frames", [])
            if isinstance(frame, dict) and isinstance(frame.get("frame_buffer"), dict) and frame["frame_buffer"].get("indexed") is False
        ]
        return {
            "object_type": "VisualFrameBufferSequenceSummary",
            "schema": self.schema,
            "sequence_id": sequence.get("sequence_id"),
            "frame_refs": frame_refs,
            "frame_buffer_entry_refs": [
                entry["buffer_entry_id"] for entry in self.entries if entry.get("observation_ref") in set(frame_refs)
            ],
            "indexed_frame_count": len([entry for entry in self.entries if entry.get("observation_ref") in set(frame_refs)]),
            "not_indexed_frame_refs": not_indexed_frame_refs,
            "index_path_abs": str(self.index_path.resolve()),
            "bounded": True,
            "raw_canonical": True,
            "index_canonical": False,
            "index_integrity_truth_source": False,
            "raw_frames_are_integrity_truth_source": True,
            "derived_review_artifact": False,
        }

    def status(self) -> dict[str, Any]:
        return {
            "object_type": "VisualFrameBufferStatus",
            "schema": self.schema,
            "enabled": True,
            "bounded": True,
            "config": asdict(self.config),
            "entry_count": len(self.entries),
            "indexed_media_bytes": self._indexed_media_bytes(self.entries),
            "indexed_media_bytes_within_config": self._indexed_media_bytes(self.entries) <= self.config.max_indexed_media_bytes,
            "index_path_abs": str(self.index_path.resolve()),
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "canonical_raw_evidence_not_deleted_by_buffer": True,
            "index_canonical": False,
            "index_integrity_truth_source": False,
            "raw_frames_are_integrity_truth_source": True,
            "derived_review_artifact": False,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def retention_status(self, *, now: float | None = None) -> dict[str, Any]:
        plan = self.plan_prune(now=now)
        return {
            "object_type": "VisualFrameBufferRetentionStatus",
            "schema": self.retention_schema,
            "policy_role": "metadata_only_short_term_attention_retention",
            "config": asdict(self.config),
            "entry_count": len(self.entries),
            "indexed_media_bytes": self._indexed_media_bytes(self.entries),
            "indexed_media_bytes_within_config": self._indexed_media_bytes(self.entries) <= self.config.max_indexed_media_bytes,
            "expired_entry_count": plan["reason_counts"].get("ttl_expired", 0),
            "would_prune_entry_count": plan["would_prune_entry_count"],
            "would_keep_entry_count": plan["would_keep_entry_count"],
            "reason_counts": plan["reason_counts"],
            "prune_query_type": "prune_unreferenced_buffer",
            "prune_query_is_dry_run_only": True,
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "canonical_raw_evidence_not_deleted_by_buffer": True,
            "index_canonical": False,
            "index_integrity_truth_source": False,
            "raw_frames_are_integrity_truth_source": True,
            "receipt_evidence_references_inspected": False,
            "derived_review_artifact": False,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def plan_prune(self, *, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        original = [dict(entry) for entry in self.entries]
        kept: list[dict[str, Any]] = []
        pruned: list[dict[str, Any]] = []
        for entry in original:
            if float(entry.get("expires_at") or 0) < now:
                pruned.append(self._prune_record(entry, "ttl_expired"))
            else:
                kept.append(entry)

        kept, by_region_pruned = self._prune_per_region(kept)
        pruned.extend(by_region_pruned)

        kept.sort(key=lambda item: float(item.get("indexed_at") or 0), reverse=True)
        while len(kept) > self.config.max_entries:
            pruned.append(self._prune_record(kept.pop(), "max_entries_exceeded"))
        while self._indexed_media_bytes(kept) > self.config.max_indexed_media_bytes and kept:
            target = self._bytes_budget_prune_target(kept)
            kept.remove(target)
            pruned.append(self._prune_record(target, "max_indexed_media_bytes_exceeded"))
        kept.sort(key=lambda item: float(item.get("indexed_at") or 0))
        return {
            "object_type": "VisualFrameBufferPrunePlan",
            "schema": self.retention_schema,
            "query_type": "prune_unreferenced_buffer",
            "dry_run_only": True,
            "prune_applied": False,
            "entry_count_before": len(self.entries),
            "would_keep_entry_count": len(kept),
            "would_prune_entry_count": len(pruned),
            "would_keep_entry_refs": [entry.get("buffer_entry_id") for entry in kept],
            "would_prune": pruned,
            "reason_counts": self._reason_counts(pruned),
            "scope": "frame_buffer_index_only",
            "unreferenced_scope": "short_term_attention_index_entries_only",
            "raw_media_deleted": False,
            "canonical_evidence_deleted": False,
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "canonical_raw_evidence_not_deleted_by_buffer": True,
            "receipt_evidence_references_inspected": False,
            "index_integrity_truth_source": False,
            "raw_frames_are_integrity_truth_source": True,
            "metadata_only": True,
            "no_capture_performed": True,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def retention_class_projection(self, *, max_entries: int = 8, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        limit = max(1, min(int(max_entries), 32))
        entries = sorted(self.entries, key=lambda item: float(item.get("indexed_at") or 0), reverse=True)[:limit]
        projected = [self._retention_class_entry(entry, now=now) for entry in entries]
        return {
            "object_type": "VisualFrameBufferRetentionClassProjection",
            "schema": self.retention_class_schema,
            "projection_role": "metadata_only_retention_class_projection",
            "max_entries": limit,
            "entry_count": len(projected),
            "total_frame_buffer_entry_count": len(self.entries),
            "retention_class_counts": self._class_counts(projected),
            "entries": projected,
            "classification_basis": [
                "sequence_id_present",
                "attention_scope",
                "expires_at_vs_query_time",
            ],
            "classification_limitations": {
                "receipt_evidence_references_inspected": False,
                "replay_references_inspected": False,
                "semantic_importance_judged": False,
                "pin_state_written": False,
                "raw_media_deleted": False,
            },
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "canonical_raw_evidence_not_deleted_by_buffer": True,
            "metadata_only": True,
            "no_capture_performed": True,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def storage_attention_summary(self, *, max_entries: int = 8, now: float | None = None) -> dict[str, Any]:
        now = now or time.time()
        retention = self.retention_status(now=now)
        classes = self.retention_class_projection(max_entries=max_entries, now=now)
        plan = self.plan_prune(now=now)
        return {
            "object_type": "VisualFrameBufferStorageAttentionSummary",
            "schema": self.storage_attention_summary_schema,
            "summary_role": "metadata_only_storage_attention_summary",
            "entry_count": len(self.entries),
            "indexed_media_bytes": self._indexed_media_bytes(self.entries),
            "indexed_media_bytes_within_config": retention.get("indexed_media_bytes_within_config"),
            "expired_entry_count": retention.get("expired_entry_count"),
            "retention_class_counts": classes.get("retention_class_counts"),
            "would_prune_entry_count": plan.get("would_prune_entry_count"),
            "would_keep_entry_count": plan.get("would_keep_entry_count"),
            "prune_reason_counts": plan.get("reason_counts"),
            "sample_entry_count": classes.get("entry_count"),
            "sample_entries": classes.get("entries"),
            "fact_sources": {
                "retention_status": "frame_buffer.retention_status",
                "retention_class_projection": "frame_buffer.retention_class_projection",
                "prune_plan": "frame_buffer.plan_prune",
            },
            "decision_fields": {
                "tool_recommends_delete": False,
                "tool_recommends_keep": False,
                "semantic_importance_judged": False,
                "business_success_judged": False,
            },
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "canonical_raw_evidence_not_deleted_by_buffer": True,
            "dry_run_only": True,
            "prune_applied": False,
            "metadata_only": True,
            "no_capture_performed": True,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def recent_entries(self, *, max_entries: int = 8) -> list[dict[str, Any]]:
        limit = max(1, min(int(max_entries), 32))
        entries = sorted(self.entries, key=lambda item: float(item.get("indexed_at") or 0), reverse=True)
        return [self._entry_ref(entry) for entry in entries[:limit]]

    def frames_near_time(self, requested_time: Any) -> dict[str, Any]:
        parsed = _parse_requested_time(requested_time, now=time.time())
        if parsed is None:
            return {
                "object_type": "VisualFrameBufferFramesNearTime",
                "schema": "ai_control_p0_time_near_frame_query_v1",
                "query_status": "invalid_time",
                "requested_time": requested_time,
                "frame_count": 0,
                "frames": [],
                "metadata_only": False,
                "media_paths_returned": False,
                "no_capture_performed": True,
                "host_input_sent": False,
                "host_sent_event_count": 0,
                "ocr_used": False,
                "clipboard_used": False,
                "accessibility_tree_used": False,
                "dom_used": False,
                "window_semantics_used": False,
                "business_success_judged": False,
            }
        candidates = [entry for entry in self.entries if isinstance(entry.get("captured_at"), (int, float))]
        before = max((entry for entry in candidates if float(entry["captured_at"]) <= parsed), key=lambda item: float(item["captured_at"]), default=None)
        after = min((entry for entry in candidates if float(entry["captured_at"]) >= parsed), key=lambda item: float(item["captured_at"]), default=None)
        nearest = min(candidates, key=lambda item: abs(float(item["captured_at"]) - parsed), default=None)
        selected: list[dict[str, Any]] = []
        for relation, entry in (("before", before), ("after", after)):
            if entry is not None and not any(item.get("buffer_entry_id") == entry.get("buffer_entry_id") for item in selected):
                selected.append({**entry, "_relation": relation})
        if not selected and nearest is not None:
            selected.append({**nearest, "_relation": "nearest"})
        frames = [self._near_time_frame(entry, requested_epoch=parsed, relation=str(entry.get("_relation"))) for entry in selected]
        nearest_frame = self._near_time_frame(nearest, requested_epoch=parsed, relation="nearest") if nearest else None
        return {
            "object_type": "VisualFrameBufferFramesNearTime",
            "schema": "ai_control_p0_time_near_frame_query_v1",
            "query_status": "generated" if frames else "not_found",
            "requested_time": requested_time,
            "requested_time_epoch": parsed,
            "requested_time_iso": _timestamp_iso(parsed),
            "frame_count": len(frames),
            "frames": frames,
            "before_frame": next((frame for frame in frames if frame.get("relation") == "before"), None),
            "after_frame": next((frame for frame in frames if frame.get("relation") == "after"), None),
            "nearest_frame": nearest_frame,
            "exact_match": bool(nearest_frame and nearest_frame.get("delta_ms") == 0),
            "metadata_only": False,
            "media_paths_returned": bool(frames),
            "image_bytes_returned": False,
            "no_capture_performed": True,
            "tool_does_not_choose_frame_semantically": True,
            "host_input_sent": False,
            "host_sent_event_count": 0,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }

    def prune(self, *, now: float | None = None) -> list[dict[str, Any]]:
        now = now or time.time()
        kept: list[dict[str, Any]] = []
        pruned: list[dict[str, Any]] = []
        for entry in self.entries:
            if float(entry.get("expires_at") or 0) < now:
                pruned.append(self._prune_record(entry, "ttl_expired"))
            else:
                kept.append(entry)

        kept, by_region_pruned = self._prune_per_region(kept)
        pruned.extend(by_region_pruned)

        kept.sort(key=lambda item: float(item.get("indexed_at") or 0), reverse=True)
        while len(kept) > self.config.max_entries:
            pruned.append(self._prune_record(kept.pop(), "max_entries_exceeded"))
        while self._indexed_media_bytes(kept) > self.config.max_indexed_media_bytes and kept:
            target = self._bytes_budget_prune_target(kept)
            kept.remove(target)
            pruned.append(self._prune_record(target, "max_indexed_media_bytes_exceeded"))
        kept.sort(key=lambda item: float(item.get("indexed_at") or 0))
        self.entries = kept
        return pruned

    def _prune_per_region(self, entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for entry in entries:
            grouped.setdefault(self._region_key(entry), []).append(entry)
        kept: list[dict[str, Any]] = []
        pruned: list[dict[str, Any]] = []
        for group in grouped.values():
            group.sort(key=lambda item: float(item.get("indexed_at") or 0), reverse=True)
            kept.extend(group[: self.config.max_entries_per_region])
            pruned.extend(self._prune_record(item, "max_entries_per_region_exceeded") for item in group[self.config.max_entries_per_region :])
        return kept, pruned

    def _write_index(self, *, pruned: list[dict[str, Any]]) -> None:
        index = {
            "object_type": "VisualFrameBufferIndex",
            "schema": self.schema,
            "bounded": True,
            "config": asdict(self.config),
            "entry_count": len(self.entries),
            "indexed_media_bytes": self._indexed_media_bytes(self.entries),
            "indexed_media_bytes_within_config": self._indexed_media_bytes(self.entries) <= self.config.max_indexed_media_bytes,
            "entries": [self._entry_ref(entry) for entry in self.entries],
            "last_pruned": pruned,
            "raw_media_owned_by": "evidence_store",
            "buffer_owns_raw_media": False,
            "prune_deletes_raw_media": False,
            "canonical_raw_evidence_not_deleted_by_buffer": True,
            "index_canonical": False,
            "index_integrity_truth_source": False,
            "raw_frames_are_integrity_truth_source": True,
            "derived_review_artifact": False,
            "ocr_used": False,
            "clipboard_used": False,
            "accessibility_tree_used": False,
            "dom_used": False,
            "window_semantics_used": False,
            "business_success_judged": False,
        }
        self.index_path.write_text(canonical_json(index), encoding="utf-8")

    def _entry_ref(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            key: entry.get(key)
            for key in [
                "object_type",
                "schema",
                "buffer_entry_id",
                "observation_ref",
                "sequence_id",
                "frame_index",
                "frame_id",
                "attention_scope",
                "captured_at",
                "captured_at_iso",
                "captured_at_monotonic_ms",
                "indexed_at",
                "expires_at",
                "media_ref",
                "media_path_abs",
                "raw_media_path_abs",
                "media_sha256",
                "sha256",
                "media_size_bytes",
                "width",
                "height",
                "region",
                "screen_region",
                "coordinate_system",
                "channel_ref",
                "capture_source",
                "source_kind",
                "source",
                "event_id",
                "view_id",
                "raw_or_derived",
                "cursor_mode",
                "capture_content_degenerate",
                "receipt_ref",
                "evidence_ref",
                "replay_ref",
                "real_capture",
                "raw_canonical",
                "indexed",
                "not_indexed_reason",
                "raw_media_owned_by",
                "buffer_owns_raw_media",
                "prune_deletes_raw_media",
                "derived_review_artifact",
                "segment_frame",
                "segment_id",
                "segment_frame_id",
                "segment_restore_ref",
                "ocr_used",
                "clipboard_used",
                "accessibility_tree_used",
                "dom_used",
                "window_semantics_used",
                "business_success_judged",
            ]
        }

    def _not_indexed_ref(self, entry: dict[str, Any], reason: str) -> dict[str, Any]:
        result = self._entry_ref(entry)
        result.update(
            {
                "indexed": False,
                "not_indexed_reason": reason,
                "pruned_from_index": True,
                "raw_media_deleted": False,
                "raw_media_owned_by": "evidence_store",
            }
        )
        return result

    def _prune_record(self, entry: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "buffer_entry_id": entry.get("buffer_entry_id"),
            "observation_ref": entry.get("observation_ref"),
            "media_size_bytes": entry.get("media_size_bytes"),
            "reason": reason,
            "raw_media_deleted": False,
            "raw_media_owned_by": "evidence_store",
        }

    def _bytes_budget_prune_target(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        oversized = [
            entry
            for entry in entries
            if int(entry.get("media_size_bytes") or 0) > self.config.max_indexed_media_bytes
        ]
        candidates = oversized or entries
        return min(candidates, key=lambda item: float(item.get("indexed_at") or 0))

    def _near_time_frame(self, entry: dict[str, Any] | None, *, requested_epoch: float, relation: str) -> dict[str, Any] | None:
        if entry is None:
            return None
        captured_at = float(entry.get("captured_at") or 0)
        return {
            "frame_id": entry.get("frame_id") or entry.get("observation_ref"),
            "buffer_entry_id": entry.get("buffer_entry_id"),
            "requested_time": _timestamp_iso(requested_epoch),
            "requested_time_epoch": requested_epoch,
            "actual_frame_time": captured_at,
            "actual_frame_time_iso": entry.get("captured_at_iso") or _timestamp_iso(captured_at),
            "delta_ms": int(round((captured_at - requested_epoch) * 1000)),
            "relation": relation,
            "before_after_nearest": relation,
            "raw_media_path_abs": entry.get("raw_media_path_abs") or entry.get("media_path_abs"),
            "media_path_abs": entry.get("media_path_abs"),
            "raw_or_derived": entry.get("raw_or_derived"),
            "cursor_mode": entry.get("cursor_mode"),
            "derived_review_artifact": bool(entry.get("derived_review_artifact")),
            "capture_content_degenerate": bool(entry.get("capture_content_degenerate")),
            "capture_source": entry.get("capture_source"),
            "source": entry.get("source"),
            "region": entry.get("region"),
            "view_id": entry.get("view_id"),
            "event_id": entry.get("event_id"),
            "segment_id": entry.get("segment_id"),
            "segment_frame_id": entry.get("segment_frame_id"),
            "segment_restore_ref": entry.get("segment_restore_ref"),
            "segment_frame": entry.get("segment_frame"),
            "segment_source": (entry.get("segment_frame") or {}).get("source") if isinstance(entry.get("segment_frame"), dict) else None,
            "segment_raw_or_derived": (entry.get("segment_frame") or {}).get("raw_or_derived") if isinstance(entry.get("segment_frame"), dict) else None,
            "segment_frame_status": (entry.get("segment_frame") or {}).get("status") if isinstance(entry.get("segment_frame"), dict) else None,
            "width": entry.get("width"),
            "height": entry.get("height"),
            "sha256": entry.get("sha256"),
            "media_sha256": entry.get("media_sha256"),
            "media_ref": entry.get("media_ref"),
            "evidence_ref": entry.get("evidence_ref"),
            "receipt_ref": entry.get("receipt_ref"),
            "replay_ref": entry.get("replay_ref"),
            "business_success_judged": False,
            "tool_asserts_business_success": False,
        }

    def _region_key(self, entry: dict[str, Any]) -> str:
        return json.dumps(
            {
                "screen_region": entry.get("screen_region"),
                "coordinate_system": entry.get("coordinate_system"),
                "channel_ref": entry.get("channel_ref"),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    def _indexed_media_bytes(self, entries: list[dict[str, Any]]) -> int:
        return sum(int(entry.get("media_size_bytes") or 0) for entry in entries)

    def _reason_counts(self, records: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in records:
            reason = str(record.get("reason") or "unknown")
            counts[reason] = counts.get(reason, 0) + 1
        return counts

    def _retention_class_entry(self, entry: dict[str, Any], *, now: float) -> dict[str, Any]:
        retention_class, basis = self._retention_class_for(entry)
        expires_at = entry.get("expires_at")
        expired = isinstance(expires_at, (int, float)) and float(expires_at) < now
        return {
            "object_type": "VisualFrameBufferRetentionClassEntry",
            "schema": self.retention_class_schema,
            "buffer_entry_id": entry.get("buffer_entry_id"),
            "observation_ref": entry.get("observation_ref"),
            "sequence_id": entry.get("sequence_id"),
            "frame_index": entry.get("frame_index"),
            "attention_scope": entry.get("attention_scope"),
            "retention_class": retention_class,
            "retention_basis": basis,
            "expired_by_ttl": bool(expired),
            "captured_at": entry.get("captured_at"),
            "indexed_at": entry.get("indexed_at"),
            "expires_at": entry.get("expires_at"),
            "media_ref": entry.get("media_ref"),
            "media_sha256": entry.get("media_sha256"),
            "media_size_bytes": entry.get("media_size_bytes"),
            "screen_region": entry.get("screen_region"),
            "coordinate_system": entry.get("coordinate_system"),
            "raw_canonical": True,
            "pinned": False,
            "pin_state_written": False,
            "receipt_evidence_references_inspected": False,
            "semantic_importance_judged": False,
            "raw_media_deleted": False,
            "derived_review_artifact": False,
        }

    def _retention_class_for(self, entry: dict[str, Any]) -> tuple[str, list[str]]:
        basis: list[str] = []
        if entry.get("sequence_id"):
            basis.append("sequence_id_present")
            return "sequence_referenced", basis
        attention_scope = str(entry.get("attention_scope") or "")
        if attention_scope:
            basis.append(f"attention_scope={attention_scope}")
            return f"attention_scope_{attention_scope}", basis
        basis.append("no_sequence_or_attention_scope")
        return "ephemeral_unreferenced", basis

    def _class_counts(self, entries: list[dict[str, Any]]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in entries:
            retention_class = str(entry.get("retention_class") or "unknown")
            counts[retention_class] = counts.get(retention_class, 0) + 1
        return counts


def _timestamp_iso(timestamp: float) -> str:
    return datetime.fromtimestamp(float(timestamp)).astimezone().isoformat(timespec="milliseconds")


def _parse_requested_time(value: Any, *, now: float) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        pass
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            clock = datetime.strptime(text, fmt).time()
        except ValueError:
            continue
        base = datetime.fromtimestamp(now).astimezone()
        combined = datetime.combine(base.date(), datetime_time(clock.hour, clock.minute, clock.second, clock.microsecond), tzinfo=base.tzinfo)
        return combined.timestamp()
    return None


def _frame_source_from_attention(attention_scope: str) -> str:
    return {
        "look": "look",
        "do_after_frame": "do_after_frame",
        "sequence": "review_clip",
        "observe": "observe",
    }.get(attention_scope, attention_scope or "unknown")
