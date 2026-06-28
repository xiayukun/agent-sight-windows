from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from ai_control.segments.manifest import boundary_facts


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
        self._process = self._start_ffmpeg()

    def _start_ffmpeg(self) -> subprocess.Popen[bytes]:
        command = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-use_wallclock_as_timestamps",
            "1",
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
            str(self.path),
        ]
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
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
        if self._process.stdin is None or self._process.poll() is not None:
            raise RuntimeError("ffmpeg_mkv_writer_not_running")
        expected = self.width * self.height * 4
        if len(bgra_bytes) != expected:
            raise ValueError(f"unexpected BGRA byte size: {len(bgra_bytes)} != {expected}")
        self._process.stdin.write(bgra_bytes)
        self._process.stdin.flush()
        frame_index = len(self.frames)
        frame_id = f"f{frame_index:06d}"
        record = {
            "object_type": "AgentSightMkvFrame",
            "schema": "agentsight_mkv_segment_v1",
            "frame_id": frame_id,
            "frame_index": frame_index,
            "frame_kind": "vfr_frame",
            "timestamp_ms": int(captured_at_ms),
            "timestamp_iso": timestamp_iso,
            "pts_ms": max(0, int(captured_at_ms) - self.started_at_ms),
            "playback_pts_ms": frame_index * 40,
            "playback_time_basis": "ffmpeg_default_25fps_frame_index",
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
            "boundary": boundary_facts(),
        }
        self.frames.append(record)
        with self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        if len(self.frames) == 1 or len(self.frames) % 25 == 0:
            self._write_manifest(finalized=False)
        return record

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
            "started_at_ms": self.started_at_ms,
            "finalized": finalized,
            "raw_frames_are_canonical_evidence": True,
            "derived_review_video_is_canonical": False,
            "boundary": boundary_facts(),
        }
        self.manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def close(self) -> None:
        if self._process.poll() is None:
            if self._process.stdin is not None:
                self._process.stdin.close()
            self._process.wait(timeout=10)
        self._write_manifest(finalized=True)

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
        }


def decode_mkv_frame_to_image(restore_ref: dict[str, Any]) -> tuple[Image.Image, dict[str, Any]]:
    segment_path = Path(str(restore_ref.get("segment_path") or ""))
    frame_id = str(restore_ref.get("frame_id") or "")
    index_path = Path(str(restore_ref.get("index_path") or segment_path.with_suffix(".frames.jsonl")))
    frame = _read_frame(index_path, frame_id)
    frame_index = int(frame.get("frame_index") or 0)
    command = [
        _find_ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(segment_path),
        "-vf",
        f"select=eq(n\\,{frame_index})",
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
    return image, {
        "status": "decoded",
        "storage_format": "mkv_vfr",
        "canonical_evidence_source": MKV_CONTAINER_MODEL,
        "frame_id": frame_id,
        "frame_index": frame_index,
        "pts_ms": frame.get("pts_ms"),
        "playback_pts_ms": frame.get("playback_pts_ms"),
        "playback_time_basis": frame.get("playback_time_basis"),
        "segment_path": str(segment_path.resolve()),
        "file_written": False,
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
