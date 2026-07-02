from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class EvidenceReplayService:
    def __init__(self, runs_dir: str | Path = "runs", session_id: str | None = None) -> None:
        self.runs_dir = Path(runs_dir)
        self.session_id = session_id or f"session-{uuid.uuid4().hex[:12]}"
        self.root = self.runs_dir / self.session_id
        self.media_dir = self.root / "media"
        self.objects_dir = self.root / "objects"
        self.replay_dir = self.root / "replay"
        self.timeline = self.root / "timeline.jsonl"
        self.manifest = self.root / "manifest.json"
        self.integrity = self.root / "integrity.json"
        self._prev_hash = ""
        self._entry_count = 0
        self.fail_next_append = False

    def _init_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        self.objects_dir.mkdir(parents=True, exist_ok=True)
        self.replay_dir.mkdir(parents=True, exist_ok=True)
        if not self.manifest.exists():
            from agentsight.protocol.schemas import schema_ref

            current_schema = schema_ref()
            self.manifest.write_text(
                canonical_json(
                    {
                        "session_id": self.session_id,
                        "capability_manifest_summary": "prototype-v1",
                        "boundary_declaration_summary": "human-equivalent-gui-only",
                        "schema_version": current_schema["schema_version"],
                        "schema_ref": current_schema,
                        "config_version": "default",
                    }
                ),
                encoding="utf-8",
            )

    def append(self, event_type: str, obj: dict[str, Any]) -> dict[str, Any]:
        if self.fail_next_append:
            self.fail_next_append = False
            raise OSError("simulated evidence append failure")

        self._init_dirs()
        self._entry_count += 1
        entry_id = f"ev-{self._entry_count:06d}"
        object_ref = f"objects/{entry_id}.json"
        (self.root / object_ref).write_text(canonical_json(obj), encoding="utf-8")
        object_hash = sha256_text(canonical_json(obj))
        entry_without_hash = {
            "entry_id": entry_id,
            "session_id": self.session_id,
            "request_id": obj.get("request_id", entry_id),
            "type": event_type,
            "timestamp": time.time(),
            "object_ref": object_ref,
            "media_ref": obj.get("media_ref"),
            "media_sha256": obj.get("media_sha256"),
            "media_size_bytes": obj.get("media_size_bytes"),
            "object_sha256": object_hash,
            "prev_hash": self._prev_hash,
            "mask_record": obj.get("mask_record"),
            "config_ref": obj.get("config_ref", {"config_version": "default"}),
            "decision_ref": obj.get("decision_ref"),
            "boundary_result": obj.get("boundary_result"),
            "adapter_ref": obj.get("adapter_ref"),
            "schema_ref": obj.get("schema_ref"),
            "gateway_route": obj.get("gateway_route"),
            "input_executed": obj.get("input_executed"),
        }
        entry_hash = sha256_text(canonical_json(entry_without_hash))
        entry = {**entry_without_hash, "sha256": entry_hash}
        with self.timeline.open("a", encoding="utf-8") as fh:
            fh.write(canonical_json(entry) + "\n")
        self._prev_hash = entry_hash
        return {"object_type": "EvidenceRef", "session_id": self.session_id, "entry_id": entry_id}

    def write_media_text(self, name: str, text: str) -> str:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        path = self.media_dir / name
        path.write_text(text, encoding="utf-8")
        return f"media/{name}"

    def media_bytes_record(self, data: bytes) -> dict[str, Any]:
        return {
            "_media_bytes": data,
            "media_sha256": sha256_bytes(data),
            "media_size_bytes": len(data),
            "media_storage": "memory_only_not_persisted",
            "media_ref": None,
        }

    def write_media_bytes(self, name: str, data: bytes) -> dict[str, Any]:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        path = self.media_dir / name
        path.write_bytes(data)
        return {
            "media_ref": f"media/{name}",
            "media_sha256": sha256_bytes(data),
            "media_size_bytes": len(data),
        }

    def prepare_input_entry(self, input_event: dict[str, Any]) -> dict[str, Any]:
        return self.append("pre_input", {"object_type": "InputEventPrewrite", **input_event})

    def package(self) -> dict[str, Any]:
        package = {
            "object_type": "EvidencePackage",
            "session_id": self.session_id,
            "manifest_ref": "manifest.json",
            "timeline_ref": "timeline.jsonl",
            "entry_count": self._entry_count,
            "last_hash": self._prev_hash,
        }
        self.append("evidence_package", package)
        return package

    def replay_index(self) -> dict[str, Any]:
        index = {
            "object_type": "ReplayIndex",
            "session_id": self.session_id,
            "timeline_ref": "timeline.jsonl",
            "read_only": True,
            "reexecute_allowed": False,
        }
        self._init_dirs()
        (self.replay_dir / "index.json").write_text(canonical_json(index), encoding="utf-8")
        self.append("replay_index", index)
        return index

    def verify_integrity(self) -> dict[str, Any]:
        prev = ""
        count = 0
        if self.timeline.exists():
            for line in self.timeline.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                expected_prev = entry["prev_hash"]
                if expected_prev != prev:
                    return {
                        "object_type": "IntegrityManifest",
                        "ok": False,
                        "reason": "prev_hash_mismatch",
                    }
                stored_hash = entry["sha256"]
                entry_without_hash = dict(entry)
                del entry_without_hash["sha256"]
                actual_hash = sha256_text(canonical_json(entry_without_hash))
                if actual_hash != stored_hash:
                    return {
                        "object_type": "IntegrityManifest",
                        "ok": False,
                        "reason": "entry_hash_mismatch",
                    }
                object_path = self.root / entry["object_ref"]
                if object_path.exists():
                    stored_object = json.loads(object_path.read_text(encoding="utf-8"))
                    media_check = self._verify_object_media(stored_object)
                    if media_check:
                        return media_check
                prev = stored_hash
                count += 1
        result = {"object_type": "IntegrityManifest", "ok": True, "entry_count": count, "last_hash": prev}
        self._init_dirs()
        self.integrity.write_text(canonical_json(result), encoding="utf-8")
        self.append("integrity", result)
        return result

    def _verify_object_media(self, value: Any) -> dict[str, Any] | None:
        for media in self._iter_media_items(value):
            media_ref = media["media_ref"]
            media_sha256 = media["media_sha256"]
            media_path = self.root / media_ref
            if not media_path.exists():
                return {
                    "object_type": "IntegrityManifest",
                    "ok": False,
                    "reason": "media_missing",
                    "media_ref": media_ref,
                }
            actual_media_hash = sha256_bytes(media_path.read_bytes())
            if actual_media_hash != media_sha256:
                return {
                    "object_type": "IntegrityManifest",
                    "ok": False,
                    "reason": "media_hash_mismatch",
                    "media_ref": media_ref,
                }
        return None

    def _iter_media_items(self, value: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if isinstance(value, dict):
            media_ref = value.get("media_ref")
            media_sha256 = value.get("media_sha256")
            if isinstance(media_ref, str) and isinstance(media_sha256, str):
                items.append({"media_ref": media_ref, "media_sha256": media_sha256})
            for child in value.values():
                items.extend(self._iter_media_items(child))
        elif isinstance(value, list):
            for child in value:
                items.extend(self._iter_media_items(child))
        return items
