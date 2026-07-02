from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from agentsight.segments.manifest import boundary_facts


MKV_CONTAINER_MODEL = "mkv_vfr_ffmpeg_v1"


class MkvSegmentWriter:
    def __init__(self, path: Path, *, segment_id: str, width: int, height: int) -> None:
        self.path = path
        self.segment_id = segment_id
        self.width = int(width)
        self.height = int(height)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path = self.path.with_suffix(".frames.jsonl")
        self.manifest_path = self.path.with_suffix(".manifest.json")
        self.ffmpeg_path = _find_ffmpeg()
        self.started_at_ms = int(time.time() * 1000)
        self.frames: list[dict[str, Any]] = []
        self._physical_frame_count = 0
        self._hash_index: dict[str, dict[str, Any]] = {}
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stdout_error: BaseException | None = None
        self._closed = False

    def _start_ffmpeg(self) -> subprocess.Popen[bytes]:
        command = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgra",
            "-video_size",
            f"{self.width}x{self.height}",
            "-i",
            "pipe:0",
            "-an",
            "-c:v",
            "ffv1",
            "-level",
            "3",
            "-g",
            "1",
            "-fps_mode",
            "vfr",
            "-f",
            "matroska",
            "pipe:1",
        ]
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **_hidden_subprocess_kwargs(),
        )

    def add_frame(
        self,
        bgra_bytes: bytes,
        *,
        captured_at_ms: int,
        timestamp_iso: str,
        source: str,
        event_id: str | None,
        cursor_mode: str,
        capture_content_degenerate: bool,
        screen_region: dict[str, int] | None,
        coordinate_system: str | None,
    ) -> dict[str, Any]:
        if self._closed:
            raise RuntimeError("mkv_segment_writer_closed")
        expected = self.width * self.height * 4
        if len(bgra_bytes) != expected:
            raise ValueError(f"unexpected BGRA byte size: {len(bgra_bytes)} != {expected}")
        logical_frame_index = len(self.frames)
        logical_frame_id = f"f{logical_frame_index:06d}"
        frame_hash = hashlib.sha256(bgra_bytes).hexdigest()
        duplicate_of = self._hash_index.get(frame_hash)
        logical_duplicate = duplicate_of is not None
        if logical_duplicate:
            physical_frame_index = int(duplicate_of["physical_frame_index"])
            physical_frame_id = str(duplicate_of["physical_frame_id"])
            duplicate_of_frame_id = str(duplicate_of["logical_frame_id"])
        else:
            physical_frame_index = self._physical_frame_count
            physical_frame_id = f"p{physical_frame_index:06d}"
            duplicate_of_frame_id = None
        restore_ref = {
            "storage_format": "mkv_vfr",
            "segment_path": str(self.path.resolve()),
            "index_path": str(self.index_path.resolve()),
            "frame_id": logical_frame_id,
            "logical_frame_id": logical_frame_id,
            "logical_frame_index": logical_frame_index,
            "physical_frame_id": physical_frame_id,
            "physical_frame_index": physical_frame_index,
            "duplicate_of_frame_id": duplicate_of_frame_id,
            "logical_duplicate": logical_duplicate,
            "timestamp_ms": int(captured_at_ms),
        }
        record = {
            "object_type": "AgentSightMkvFrame",
            "schema": "agentsight_mkv_segment_v1",
            "frame_id": logical_frame_id,
            "frame_index": logical_frame_index,
            "logical_frame_id": logical_frame_id,
            "logical_frame_index": logical_frame_index,
            "physical_frame_id": physical_frame_id,
            "physical_frame_index": physical_frame_index,
            "duplicate_of_frame_id": duplicate_of_frame_id,
            "logical_duplicate": logical_duplicate,
            "frame_hash_sha256": frame_hash,
            "frame_kind": "vfr_frame",
            "timestamp_ms": int(captured_at_ms),
            "timestamp_iso": timestamp_iso,
            "pts_ms": max(0, int(captured_at_ms) - self.started_at_ms),
            "playback_pts_ms": physical_frame_index * 40,
            "logical_playback_pts_ms": logical_frame_index * 40,
            "playback_time_basis": "ffmpeg_default_25fps_physical_frame_index",
            "logical_time_basis": "frame_index_with_timestamp_ms",
            "source": source,
            "event_id": event_id,
            "cursor_mode": cursor_mode,
            "capture_content_degenerate": bool(capture_content_degenerate),
            "screen_region": screen_region,
            "coordinate_system": coordinate_system,
            "width": self.width,
            "height": self.height,
            "storage_format": "mkv_vfr",
            "segment_id": self.segment_id,
            "segment_path": str(self.path.resolve()),
            "restore_ref": restore_ref,
            "boundary": boundary_facts(),
        }
        if not logical_duplicate:
            self._write_frame_payload(bgra_bytes)
            self._physical_frame_count += 1
            self._hash_index[frame_hash] = {
                "logical_frame_id": logical_frame_id,
                "physical_frame_id": physical_frame_id,
                "physical_frame_index": physical_frame_index,
            }
        self.frames.append(record)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if len(self.frames) == 1 or len(self.frames) % 25 == 0:
            self._write_manifest(finalized=False)
        return record

    def _write_frame_payload(self, bgra_bytes: bytes) -> None:
        process = self._ensure_process()
        if self._stdout_error is not None:
            raise RuntimeError(f"ffmpeg_mkv_stdout_copy_failed:{self._stdout_error}") from self._stdout_error
        if process.poll() is not None:
            raise RuntimeError(f"ffmpeg_mkv_writer_exited:{process.returncode}")
        if process.stdin is None:
            raise RuntimeError("ffmpeg_stdin_unavailable")
        try:
            process.stdin.write(bgra_bytes)
            process.stdin.flush()
        except Exception:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            self._process = None
            raise

    def _ensure_process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            self.path.write_bytes(b"")
            self._stdout_error = None
            self._process = self._start_ffmpeg()
            self._start_stdout_copy_thread(self._process)
        return self._process

    def _start_stdout_copy_thread(self, process: subprocess.Popen[bytes]) -> None:
        stdout = getattr(process, "stdout", None)
        if stdout is None:
            return

        def copy_stdout() -> None:
            try:
                while True:
                    chunk = stdout.read(1024 * 1024)
                    if not chunk:
                        break
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("ab") as fh:
                        fh.write(chunk)
            except BaseException as exc:  # pragma: no cover - surfaced by close/add_frame
                self._stdout_error = exc
            finally:
                try:
                    stdout.close()
                except Exception:
                    pass

        self._stdout_thread = threading.Thread(target=copy_stdout, name=f"mkv-writer-{self.segment_id}", daemon=True)
        self._stdout_thread.start()

    def _write_manifest(self, *, finalized: bool) -> None:
        payload = {
            "object_type": "AgentSightMkvSegmentManifest",
            "schema": "agentsight_mkv_segment_v1",
            "segment_id": self.segment_id,
            "storage_format": "mkv_vfr",
            "container_model": MKV_CONTAINER_MODEL,
            "segment_path": str(self.path.resolve()),
            "index_path": str(self.index_path.resolve()),
            "width": self.width,
            "height": self.height,
            "frame_count": len(self.frames),
            "logical_frame_count": len(self.frames),
            "physical_frame_count": self._physical_frame_count,
            "started_at_ms": self.started_at_ms,
            "finalized": finalized,
            "raw_frames_are_canonical_evidence": True,
            "derived_review_video_is_canonical": False,
            "boundary": boundary_facts(),
        }
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def close(self) -> None:
        if self._closed:
            return
        process = self._process
        self._process = None
        if process is not None:
            try:
                if process.stdin is not None and not process.stdin.closed:
                    process.stdin.close()
            except BrokenPipeError:
                pass
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            if self._stdout_thread is not None:
                self._stdout_thread.join(timeout=10)
                self._stdout_thread = None
            if self._stdout_error is not None:
                raise RuntimeError(f"ffmpeg_mkv_stdout_copy_failed:{self._stdout_error}") from self._stdout_error
            if process.returncode != 0:
                raise RuntimeError(f"ffmpeg_mkv_writer_failed:{process.returncode}")
        self._closed = True
        self._write_manifest(finalized=True)

    def __enter__(self) -> "MkvSegmentWriter":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass

    @property
    def manifest(self) -> dict[str, Any]:
        return {
            "segment_id": self.segment_id,
            "storage_format": "mkv_vfr",
            "container_model": MKV_CONTAINER_MODEL,
            "segment_path": str(self.path.resolve()),
            "index_path": str(self.index_path.resolve()),
            "frames": list(self.frames),
            "frame_count": len(self.frames),
            "logical_frame_count": len(self.frames),
            "physical_frame_count": self._physical_frame_count,
        }


def decode_mkv_frame_to_image(restore_ref: dict[str, Any]) -> tuple[Image.Image, dict[str, Any]]:
    segment_path = Path(str(restore_ref.get("segment_path") or ""))
    frame_id = str(restore_ref.get("frame_id") or "")
    index_path = Path(str(restore_ref.get("index_path") or segment_path.with_suffix(".frames.jsonl")))
    frame = _read_frame(index_path, frame_id)
    logical_frame_index = int(frame.get("logical_frame_index", frame.get("frame_index") or 0))
    physical_frame_index = int(frame.get("physical_frame_index", frame.get("frame_index") or 0))
    command = [
        _find_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(segment_path),
        "-vf",
        f"select=eq(n\\,{physical_frame_index})",
        "-vsync",
        "0",
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "pipe:1",
    ]
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        **_hidden_subprocess_kwargs(),
    )
    if result.returncode != 0 or not result.stdout:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace") or "ffmpeg_decode_failed")
    import io

    image = Image.open(io.BytesIO(result.stdout)).convert("RGBA")
    decoded_hash = hashlib.sha256(image.tobytes("raw", "BGRA")).hexdigest()
    expected_hash = frame.get("frame_hash_sha256")
    hash_ok = decoded_hash == expected_hash if expected_hash else None
    return image, {
        "status": "decoded",
        "storage_format": "mkv_vfr",
        "canonical_evidence_source": MKV_CONTAINER_MODEL,
        "frame_id": frame_id,
        "frame_index": logical_frame_index,
        "logical_frame_id": frame.get("logical_frame_id", frame_id),
        "logical_frame_index": logical_frame_index,
        "physical_frame_id": frame.get("physical_frame_id", frame_id),
        "physical_frame_index": physical_frame_index,
        "decoded_physical_frame_id": frame.get("physical_frame_id", frame_id),
        "duplicate_of_frame_id": frame.get("duplicate_of_frame_id"),
        "logical_duplicate": bool(frame.get("logical_duplicate")),
        "hash_ok": hash_ok,
        "decoded_frame_hash_sha256": decoded_hash,
        "expected_frame_hash_sha256": expected_hash,
        "pts_ms": frame.get("pts_ms"),
        "playback_pts_ms": frame.get("playback_pts_ms"),
        "playback_time_basis": frame.get("playback_time_basis"),
        "segment_path": str(segment_path.resolve()),
        "file_written": False,
        "tool_asserts_business_success": False,
        "tool_asserts_target_hit": False,
        "tool_asserts_causality": False,
        "host_input_sent": False,
        "host_sent_event_count": 0,
        "boundary": boundary_facts(),
    }


def iter_mkv_frames(root: Path, *, max_frames: int | None = None) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    if not root.exists():
        return frames
    remaining = max_frames if max_frames and max_frames > 0 else None
    for index_path in sorted(root.rglob("segments/*.frames.jsonl"), key=lambda path: path.stat().st_mtime, reverse=True):
        lines = index_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                record["index_path"] = str(index_path.resolve())
                record.setdefault("segment_path", str(_segment_path_from_index(index_path).resolve()))
                frames.append(record)
                if remaining is not None:
                    remaining -= 1
                    if remaining <= 0:
                        frames.sort(key=lambda item: int(item.get("timestamp_ms") or 0))
                        return frames
    frames.sort(key=lambda item: int(item.get("timestamp_ms") or 0))
    return frames


def _read_frame(index_path: Path, frame_id: str) -> dict[str, Any]:
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if isinstance(record, dict) and record.get("frame_id") == frame_id:
            return record
    raise KeyError(f"frame not found in MKV index: {frame_id}")


def _segment_path_from_index(index_path: Path) -> Path:
    name = index_path.name
    if name.endswith(".frames.jsonl"):
        return index_path.with_name(name[: -len(".frames.jsonl")] + ".mkv")
    return index_path.with_suffix(".mkv")


def _find_ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    if local:
        package_root = Path(local) / "Microsoft" / "WinGet" / "Packages"
        for path in package_root.glob("Gyan.FFmpeg*_8wekyb3d8bbwe/ffmpeg-*/bin/ffmpeg.exe"):
            if path.exists():
                return str(path)
    raise RuntimeError("ffmpeg_not_found")


def _hidden_subprocess_kwargs() -> dict[str, Any]:
    startupinfo = None
    if os.name == "nt":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }


def timestamp_iso(value_ms: int) -> str:
    return datetime.fromtimestamp(value_ms / 1000.0).astimezone().isoformat(timespec="milliseconds")
