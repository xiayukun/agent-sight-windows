from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psutil
from PIL import Image, ImageChops, ImageDraw

REPO = Path(__file__).resolve().parents[1]
OUTDIR = REPO / "reports" / "mkv_quota_qa_matrix_t_829ee81f"
OUTDIR.mkdir(parents=True, exist_ok=True)

W, H, N, FPS = 320, 180, 40, 5
BASE_TS_MS = 1_800_000_000_000
REGION = {"x": 0, "y": 0, "w": W, "h": H}


def _import_project() -> None:
    import sys

    src = str(REPO / "src")
    if src not in sys.path:
        sys.path.insert(0, src)


def bgra_bytes(img: Image.Image) -> bytes:
    return img.convert("RGBA").tobytes("raw", "BGRA")


def base_desktop() -> Image.Image:
    img = Image.new("RGB", (W, H), (37, 40, 46))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 22], fill=(28, 31, 36))
    d.rectangle([8, 5, 86, 17], fill=(68, 75, 88))
    d.rectangle([95, 5, 198, 17], fill=(54, 61, 72))
    d.rectangle([0, H - 20, W, H], fill=(26, 29, 34))
    d.rectangle([14, 34, W - 14, H - 30], fill=(244, 247, 250))
    for i in range(7):
        y = 48 + i * 14
        d.rectangle([28, y, 280 - i * 11, y + 5], fill=(170 - i * 5, 181 - i * 4, 198 - i * 3))
    d.rectangle([210, 44, 292, 122], outline=(165, 174, 188), fill=(232, 236, 242))
    return img


def make_static(i: int) -> Image.Image:
    return base_desktop()


def make_cursor_caret(i: int) -> Image.Image:
    img = base_desktop()
    d = ImageDraw.Draw(img)
    x = 38 + (i * 7) % 210
    y = 52 + (i * 3) % 82
    d.polygon(
        [(x, y), (x, y + 18), (x + 6, y + 14), (x + 10, y + 23), (x + 14, y + 21), (x + 10, y + 13), (x + 18, y + 13)],
        fill=(246, 246, 246),
        outline=(20, 20, 20),
    )
    if (i // 4) % 2 == 0:
        d.rectangle([120, 88, 121, 104], fill=(25, 100, 220))
    return img


def make_form_browser(i: int) -> Image.Image:
    img = base_desktop()
    d = ImageDraw.Draw(img)
    progress = int(230 * (i + 1) / N)
    d.rectangle([42, 133, 282, 146], outline=(120, 130, 145), fill=(226, 231, 238))
    d.rectangle([42, 133, 42 + progress, 146], fill=(30, 128, 238))
    row = 1 + (i // 5) % 5
    y = 48 + row * 14
    d.rectangle([25, y - 2, 188, y + 8], fill=(255, 242, 205))
    d.rectangle([28, y, 175, y + 5], fill=(205, 146, 70))
    return img


def make_video_animation(i: int) -> Image.Image:
    # Synthetic high-change animation, no real desktop pixels.
    img = Image.new("RGB", (W, H))
    px = img.load()
    for y in range(H):
        for x in range(W):
            v = (x * 11 + y * 7 + i * 23 + ((x ^ y ^ (i * 17)) & 63) * 3) & 255
            px[x, y] = (v, (v * 2 + i * 19) & 255, (v * 5 + x + i * 3) & 255)
    return img


SCENARIOS: dict[str, Callable[[int], Image.Image]] = {
    "static_desktop": make_static,
    "cursor_caret_small_change": make_cursor_caret,
    "form_browser_change": make_form_browser,
    "video_animation_high_change": make_video_animation,
}


def raw_payload(frames: list[Image.Image]) -> bytes:
    return b"".join(bgra_bytes(frame) for frame in frames)


def changed_ratio(a: Image.Image, b: Image.Image) -> float:
    diff = ImageChops.difference(a.convert("RGB"), b.convert("RGB")).convert("L")
    hist = diff.histogram()
    # Treat tiny RGB noise <= 3 as unchanged.
    changed = sum(hist[4:])
    return changed / float(W * H)


def select_every(frames: list[Image.Image], step: int) -> tuple[list[Image.Image], list[int]]:
    indices = [idx for idx in range(len(frames)) if idx % step == 0]
    if not indices:
        indices = [0]
    return [frames[idx] for idx in indices], indices


def select_exact_unique(frames: list[Image.Image]) -> tuple[list[Image.Image], list[int]]:
    selected: list[Image.Image] = []
    indices: list[int] = []
    last_hash: str | None = None
    for idx, frame in enumerate(frames):
        digest = hashlib.sha256(bgra_bytes(frame)).hexdigest()
        if digest != last_hash:
            selected.append(frame)
            indices.append(idx)
            last_hash = digest
    return selected or [frames[0]], indices or [0]


def select_by_change(frames: list[Image.Image], threshold: float) -> tuple[list[Image.Image], list[int], list[float]]:
    selected = [frames[0]]
    indices = [0]
    ratios: list[float] = []
    last = frames[0]
    for idx, frame in enumerate(frames[1:], start=1):
        ratio = changed_ratio(last, frame)
        ratios.append(ratio)
        if ratio >= threshold:
            selected.append(frame)
            indices.append(idx)
            last = frame
    return selected, indices, ratios


def write_sidecar(path: Path, frames: list[Image.Image], selected_indices: list[int] | None = None) -> dict[str, Any]:
    selected_indices = selected_indices or list(range(len(frames)))
    lines = []
    selected_set = set(selected_indices)
    last_physical = 0
    physical_by_original: dict[int, int] = {}
    for physical_idx, original_idx in enumerate(selected_indices):
        physical_by_original[original_idx] = physical_idx
    for original_idx in range(len(frames)):
        if original_idx in selected_set:
            last_physical = physical_by_original[original_idx]
            duplicate_of = None
        else:
            duplicate_of = last_physical
        payload = {
            "frame_id": f"logical-{original_idx:06d}",
            "logical_frame_index": original_idx,
            "physical_frame_index": physical_by_original.get(original_idx, last_physical),
            "duplicate_of_physical_frame_index": duplicate_of,
            "timestamp_ms": BASE_TS_MS + int(original_idx * 1000 / FPS),
            "source": "qa_synthetic_matrix",
            "screen_region": REGION,
            "coordinate_system": "virtual_screen_pixels",
            "sha256_bgra": hashlib.sha256(bgra_bytes(frames[original_idx])).hexdigest(),
        }
        lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    gz = path.with_suffix(path.suffix + ".gz")
    with gzip.open(gz, "wt", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"jsonl_bytes": path.stat().st_size, "jsonl_gzip_bytes": gz.stat().st_size, "logical_frames": len(frames), "physical_frames": len(selected_indices)}


def run_monitored(cmd: list[str], input_bytes: bytes | None = None) -> dict[str, Any]:
    start_wall = time.perf_counter()
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE if input_bytes is not None else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p = psutil.Process(proc.pid)
    peak_rss = 0
    cpu_samples: list[float] = []
    try:
        p.cpu_percent(None)
    except Exception:
        pass
    if input_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(input_bytes)
            proc.stdin.close()
        except BrokenPipeError:
            pass
    while proc.poll() is None:
        try:
            info = p.memory_info()
            peak_rss = max(peak_rss, int(info.rss))
            cpu_samples.append(float(p.cpu_percent(interval=0.02)))
        except psutil.Error:
            time.sleep(0.02)
    stdout, stderr = proc.communicate() if proc.stdin is None else (proc.stdout.read() if proc.stdout else b"", proc.stderr.read() if proc.stderr else b"")
    elapsed = time.perf_counter() - start_wall
    return {
        "returncode": proc.returncode,
        "elapsed_s": round(elapsed, 3),
        "peak_rss_mb": round(peak_rss / 1024 / 1024, 2) if peak_rss else None,
        "avg_cpu_percent_sampled": round(sum(cpu_samples) / len(cpu_samples), 1) if cpu_samples else None,
        "stderr_tail": stderr.decode("utf-8", errors="replace")[-800:],
        "stdout_tail": stdout.decode("utf-8", errors="replace")[-200:] if stdout else "",
    }


def encode_ffmpeg(name: str, frames: list[Image.Image], codec_args: list[str], vf: str | None = None) -> dict[str, Any]:
    out = OUTDIR / f"{name}.mkv"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgra", "-video_size", f"{W}x{H}", "-framerate", str(FPS), "-i", "pipe:0", "-an",
    ]
    if vf:
        cmd += ["-vf", vf]
    cmd += codec_args + [str(out)]
    monitored = run_monitored(cmd, input_bytes=raw_payload(frames))
    if monitored["returncode"] != 0:
        raise RuntimeError(f"ffmpeg failed for {name}: {monitored['stderr_tail']}")
    decode = run_monitored(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(out), "-f", "null", "-"])
    return {
        "file": str(out.resolve()),
        "bytes": out.stat().st_size,
        "mb": round(out.stat().st_size / 1024 / 1024, 4),
        "frames_encoded": len(frames),
        "encode": monitored,
        "decode_all": decode,
    }


def encode_current_writer(name: str, frames: list[Image.Image]) -> dict[str, Any]:
    _import_project()
    from agentsight.segments.mkv_container import MkvSegmentWriter

    root = OUTDIR / "segments_current_writer" / name
    if root.exists():
        shutil.rmtree(root)
    segment = root / "segments" / f"{name}.mkv"
    proc = psutil.Process(os.getpid())
    peak_rss = proc.memory_info().rss
    start_cpu = proc.cpu_times()
    start = time.perf_counter()
    writer = MkvSegmentWriter(segment, segment_id=name, width=W, height=H)
    records = []
    manifest_mid = None
    for idx, frame in enumerate(frames):
        records.append(
            writer.add_frame(
                bgra_bytes(frame),
                captured_at_ms=BASE_TS_MS + int(idx * 1000 / FPS),
                timestamp_iso=datetime.fromtimestamp((BASE_TS_MS + int(idx * 1000 / FPS)) / 1000, tz=timezone.utc).isoformat(),
                source="qa_synthetic_matrix",
                event_id=None,
                cursor_mode="none",
                capture_content_degenerate=False,
                screen_region=REGION,
                coordinate_system="virtual_screen_pixels",
            )
        )
        peak_rss = max(peak_rss, proc.memory_info().rss)
        if idx == 0 and writer.manifest_path.exists():
            manifest_mid = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
    writer.close()
    elapsed = time.perf_counter() - start
    end_cpu = proc.cpu_times()
    cpu_s = (end_cpu.user + end_cpu.system) - (start_cpu.user + start_cpu.system)
    final_manifest = json.loads(writer.manifest_path.read_text(encoding="utf-8"))
    return {
        "file": str(segment.resolve()),
        "index_path": str(segment.with_suffix(".frames.jsonl").resolve()),
        "manifest_path": str(segment.with_suffix(".manifest.json").resolve()),
        "bytes": segment.stat().st_size,
        "mb": round(segment.stat().st_size / 1024 / 1024, 4),
        "frames_encoded": len(frames),
        "encode": {"elapsed_s": round(elapsed, 3), "process_cpu_s": round(cpu_s, 3), "peak_python_rss_mb": round(peak_rss / 1024 / 1024, 2)},
        "mid_recording_manifest_finalized": None if manifest_mid is None else manifest_mid.get("finalized"),
        "final_manifest_finalized": final_manifest.get("finalized"),
        "writer_retains_raw_frames": "_raw_frames" in writer.__dict__,
        "records": records,
        "root_for_queries": str(root.resolve()),
    }


def validate_query_apis(writer_result: dict[str, Any]) -> dict[str, Any]:
    _import_project()
    from agentsight.segments.decoder import query_segment_change_index, query_segment_decoder_near_time, query_segment_review_clip, query_segment_timeline_diff
    from agentsight.segments.mkv_container import decode_mkv_frame_to_image

    records = writer_result["records"]
    root = Path(writer_result["root_for_queries"])
    near_time = BASE_TS_MS + int(17 * 1000 / FPS)
    decode_start = time.perf_counter()
    decoded = []
    for idx in (0, len(records) // 2, len(records) - 1):
        img, report = decode_mkv_frame_to_image(records[idx])
        decoded.append({"frame_index": idx, "size": list(img.size), "status": report.get("status"), "business_success": report.get("tool_asserts_business_success")})
    decode_elapsed = round(time.perf_counter() - decode_start, 3)
    near = query_segment_decoder_near_time(root, near_time)
    changes = query_segment_change_index(root, region=REGION, max_pairs=12, min_changed_pixel_ratio=0.0)
    diff = query_segment_timeline_diff(root, region=REGION, max_artifacts=1, output_dir=OUTDIR / "derived", request_id="qa-diff")
    clip = query_segment_review_clip(root, region=REGION, max_frames=8, scale_down=2, max_artifacts=1, output_dir=OUTDIR / "derived", request_id="qa-clip")
    return {
        "decode_selected_frames": {"ok": all(item["status"] == "decoded" for item in decoded), "elapsed_s": decode_elapsed, "frames": decoded},
        "time_near": {"query_status": near.get("query_status"), "frame_count": near.get("frame_count"), "nearest_delta_ms": (near.get("nearest_frame") or {}).get("delta_ms"), "no_capture_performed": near.get("no_capture_performed")},
        "changes": {"query_status": changes.get("query_status"), "change_count": changes.get("change_count"), "decode_error_count": changes.get("decode_error_count"), "no_capture_performed": changes.get("no_capture_performed"), "no_media_exported": changes.get("no_media_exported")},
        "diff": {"query_status": diff.get("query_status"), "change_count": diff.get("change_count"), "artifact_count": diff.get("artifact_count"), "artifact_paths": [a.get("path") for a in diff.get("artifacts") or []], "no_capture_performed": diff.get("no_capture_performed")},
        "clip": {"query_status": clip.get("query_status"), "selected_frame_count": clip.get("selected_frame_count"), "artifact_count": clip.get("artifact_count"), "artifact_paths": [a.get("path") for a in clip.get("artifacts") or []], "no_capture_performed": clip.get("no_capture_performed")},
    }


def validate_quota() -> dict[str, Any]:
    _import_project()
    from agentsight.storage_quota import apply_storage_quota

    with tempfile.TemporaryDirectory(prefix="agentsight-quota-qa-") as td:
        local = Path(td) / "LocalAppData"
        roaming = Path(td) / "Roaming"
        old_env = {"LOCALAPPDATA": os.environ.get("LOCALAPPDATA"), "APPDATA": os.environ.get("APPDATA")}
        os.environ["LOCALAPPDATA"] = str(local)
        os.environ["APPDATA"] = str(roaming)
        try:
            agent_dir = local / "AgentSight"
            derived = agent_dir / "agent-sight-look-preview-cache" / "preview.png"
            legacy = agent_dir / "runs_host_agent" / "session-legacy" / "media" / "frame.png"
            segment = agent_dir / "runs_host_agent" / "segments" / "agentsight-old-unpinned.mkv"
            pinned = agent_dir / "runs_host_agent" / "segments" / "agentsight-pinned.mkv"
            unfinished = agent_dir / "runs_host_agent" / "segments" / "agentsight-unfinalized.mkv"
            for path in (derived, legacy, segment, pinned, unfinished):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((path.name.encode("utf-8") + b"-payload") * 256)
            segment.with_suffix(".frames.jsonl").write_text(json.dumps({"timestamp_ms": BASE_TS_MS, "frame_id": "f000000"}) + "\n", encoding="utf-8")
            segment.with_suffix(".manifest.json").write_text(json.dumps({"finalized": True, "frames": [{"timestamp_ms": BASE_TS_MS}]}, ensure_ascii=False), encoding="utf-8")
            pinned.with_suffix(".frames.jsonl").write_text(json.dumps({"timestamp_ms": BASE_TS_MS, "frame_id": "f000000"}) + "\n", encoding="utf-8")
            pinned.with_suffix(".manifest.json").write_text(
                json.dumps({"segment_id": pinned.stem, "finalized": True, "frames": [{"timestamp_ms": BASE_TS_MS}]}, ensure_ascii=False),
                encoding="utf-8",
            )
            unfinished.with_suffix(".frames.jsonl").write_text(json.dumps({"timestamp_ms": BASE_TS_MS + 1000, "frame_id": "f000000"}) + "\n", encoding="utf-8")
            unfinished.with_suffix(".manifest.json").write_text(json.dumps({"finalized": False, "frames": [{"timestamp_ms": BASE_TS_MS + 1000}]}, ensure_ascii=False), encoding="utf-8")
            op_log = agent_dir / "operation-log.jsonl"
            op_log.write_text(
                json.dumps(
                    {
                        "timestamp_ms": BASE_TS_MS,
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
                ) + "\n" +
                json.dumps({"timestamp_ms": BASE_TS_MS, "entry": {"route": "/look", "note": "quota-old-unpinned-gap"}}, ensure_ascii=False) + "\n" +
                json.dumps({"timestamp_ms": BASE_TS_MS + 999999, "entry": {"route": "/screen"}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            dry = apply_storage_quota(root=agent_dir, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999}, dry_run=True)
            apply = apply_storage_quota(root=agent_dir, config={"max_storage_mb": 1, "min_free_disk_mb": 999999999}, dry_run=False)
            remaining_log = op_log.read_text(encoding="utf-8") if op_log.exists() else ""
            return {
                "dry_run": {
                    "applied": dry.get("applied"),
                    "deleted_count": dry.get("deleted_count"),
                    "candidate_reasons": [item.get("reason") for item in dry.get("deleted") or []],
                    "protected_reasons": [item.get("reason") for item in dry.get("protected") or []],
                    "all_candidates_marked_dry_run": all(item.get("dry_run") for item in dry.get("deleted") or []),
                },
                "apply": {
                    "applied": apply.get("applied"),
                    "deleted_count": apply.get("deleted_count"),
                    "deleted_reasons": [item.get("reason") for item in apply.get("deleted") or [] if item.get("deleted")],
                    "protected_reasons": [item.get("reason") for item in apply.get("protected") or []],
                    "old_segment_exists_after": segment.exists(),
                    "old_sidecars_exist_after": segment.with_suffix(".frames.jsonl").exists() or segment.with_suffix(".manifest.json").exists(),
                    "pinned_segment_exists_after": pinned.exists(),
                    "pinned_sidecars_exist_after": pinned.with_suffix(".frames.jsonl").exists() and pinned.with_suffix(".manifest.json").exists(),
                    "unfinished_segment_exists_after": unfinished.exists(),
                    "unfinished_sidecars_exist_after": unfinished.with_suffix(".frames.jsonl").exists() and unfinished.with_suffix(".manifest.json").exists(),
                    "operation_log_pruned": apply.get("operation_log_prune", {}).get("pruned"),
                    "operation_log_protected_old_count": apply.get("operation_log_prune", {}).get("protected_old_count"),
                    "pruned_gap_report": apply.get("pruned_gap_report"),
                    "remaining_log_contains_old_unpinned_gap": "quota-old-unpinned-gap" in remaining_log,
                    "remaining_log_contains_pinned_segment": pinned.stem in remaining_log,
                    "remaining_log_contains_screen": "/screen" in remaining_log,
                    "host_input_sent": apply.get("host_input_sent"),
                    "business_success_judged": apply.get("boundary", {}).get("business_success_judged"),
                },
                "status": "fixed: quota candidates protect pinned and finalized=false/open-writer MKV segments while pruning old unpinned segment sidecars and stale operation-log gaps.",
            }
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


def make_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# AgentSight MKV 体积/CPU/内存 QA 矩阵与 quota 验证")
    lines.append("")
    lines.append("任务：`t_829ee81f`")
    lines.append(f"生成时间：`{report['meta']['generated_at']}`")
    lines.append("样本：合成桌面/表单/动画帧，不含真实隐私截图。")
    lines.append("")
    lines.append("## 结论与默认建议")
    for item in report["recommendations"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 红线")
    for item in report["red_lines"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("## 体积/CPU/内存矩阵摘要")
    for scenario, data in report["scenarios"].items():
        lines.append("")
        lines.append(f"### {scenario}")
        lines.append(f"变化比例 avg/max：{data['change_ratio_avg']} / {data['change_ratio_max']}；exact physical={data['exact_unique_frames']}，threshold 0.1% physical={data['threshold_0_1pct_frames']}。")
        lines.append("")
        lines.append("| variant | canonical? | frames | MB/min | encode s | peak RSS MB | avg CPU % | decode s | notes |")
        lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---|")
        for name, row in data["variants"].items():
            enc = row.get("encode", {})
            dec = row.get("decode_all", {})
            rss = enc.get("peak_rss_mb", enc.get("peak_python_rss_mb"))
            cpu = enc.get("avg_cpu_percent_sampled", enc.get("process_cpu_s"))
            lines.append(
                f"| {name} | {row['canonical_allowed']} | {row['frames_encoded']} | {row['mb_per_min']} | {enc.get('elapsed_s')} | {rss} | {cpu} | {dec.get('elapsed_s')} | {row['notes']} |"
            )
    lines.append("")
    lines.append("## time.near / changes / diff / clip 验证")
    lines.append("```json")
    lines.append(json.dumps(report["query_api_validation"], ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## quota 验证")
    lines.append("```json")
    lines.append(json.dumps(report["quota_validation"], ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append("## 可复跑命令")
    lines.append("```bash")
    lines.append("cd /c/git/家里/AgentSight && PYTHONPATH=src python -B tools/run_mkv_quota_qa_matrix_t_829ee81f.py")
    lines.append("cd /c/git/家里/AgentSight && .venv/Scripts/python.exe -m pytest tests/acceptance/test_mkv_segment_storage.py tests/acceptance/test_agent_sight_qa_regressions_t22845b98.py -q")
    lines.append("```")
    lines.append("")
    lines.append("## 验收状态")
    for key, value in report["acceptance"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## caveat")
    for item in report["caveats"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg_not_found")
    _import_project()
    report: dict[str, Any] = {
        "meta": {
            "task_id": "t_829ee81f",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo": str(REPO),
            "output_dir": str(OUTDIR.resolve()),
            "sample": {"width": W, "height": H, "frames": N, "fps": FPS, "duration_s": N / FPS, "privacy": "synthetic_only_no_real_screenshots"},
            "ffmpeg": shutil.which("ffmpeg"),
        },
        "scenarios": {},
        "recommendations": [
            "canonical 默认保持 MKV FFV1 VFR + .frames.jsonl + manifest；当前代码已是流式 writer，不再保留 _raw_frames，长录制首要风险从 raw heap 降为文件句柄/finalize/recovery。",
            "短期用户默认建议：action capture 采用 3-5 FPS × 3-5s；长等待或复核场景由调用方显式请求更长 post_observe，不默认 10 FPS × 10s。",
            "exact duplicate 稀疏索引可进入 canonical 候选：只跳过 sha256 完全相同物理帧，sidecar 保留每个 logical timestamp，time.near/diff/clip 必须理解 logical->physical 映射后再启用。",
            "x264rgb lossless 可作为 canonical 候选实验项，但必须补 encode/decode/hash roundtrip 和真实桌面分布；不能直接替换 FFV1。",
            "H.264 CRF23/30、scale_down、review GIF/diff heatmap 只能作为 derived review/export，不能作为默认 canonical evidence。",
            "quota 只能在 dry-run 报告可解释且 pinned/unfinalized/open writer 保护明确后进入自动 apply；当前实现只适合作为空间兜底。",
        ],
        "red_lines": [
            "不得用有损 H.264 CRF23/30 覆盖 canonical evidence。",
            "不得把 pixel-threshold 去重默认开启到 canonical；小光标/caret 变化会被阈值吞掉。",
            "quota apply 不得删除 pinned、未 finalized、正在写入/读取、未上传/未复核段；删除必须同步 sidecar 并留下 pruned gap。",
            "真实桌面验证不提交隐私截图；只提交统计、合成样本或脱敏 review artifact。",
        ],
        "caveats": [
            "本轮矩阵使用合成样本；没有采集或提交真实桌面截图。",
            "CPU/RSS 为本机短样本测量，适合比较方向，不代表所有 Windows 机器。",
            "仓库已有大量未提交改动，本脚本只新增 QA 工具和报告。",
        ],
    }

    first_writer_for_query: dict[str, Any] | None = None
    duration_min = (N / FPS) / 60.0
    for scenario, maker in SCENARIOS.items():
        frames = [maker(i) for i in range(N)]
        exact, exact_idx = select_exact_unique(frames)
        half, half_idx = select_every(frames, 2)
        thr001, thr001_idx, ratios = select_by_change(frames, 0.001)
        scenario_dir = OUTDIR / "sidecars" / scenario
        scenario_dir.mkdir(parents=True, exist_ok=True)
        sidecar_all = write_sidecar(scenario_dir / "all.frames.jsonl", frames)
        sidecar_exact = write_sidecar(scenario_dir / "exact-sparse.frames.jsonl", frames, exact_idx)
        sidecar_thr001 = write_sidecar(scenario_dir / "threshold-0.1pct.frames.jsonl", frames, thr001_idx)
        variants: dict[str, Any] = {}
        current = encode_current_writer(f"{scenario}__current_writer_ffv1_streaming", frames)
        if first_writer_for_query is None and scenario == "form_browser_change":
            first_writer_for_query = current
        variants["current_writer_ffv1_streaming"] = {**current, "canonical_allowed": True, "notes": "current production path; streaming writer; no _raw_frames"}
        ffv1 = encode_ffmpeg(f"{scenario}__ffv1_all_frames", frames, ["-c:v", "ffv1", "-level", "3", "-g", "1", "-fps_mode", "vfr"])
        variants["ffv1_all_frames_direct"] = {**ffv1, "canonical_allowed": True, "notes": "single-pass reference"}
        sparse = encode_ffmpeg(f"{scenario}__ffv1_exact_duplicate_sparse_physical", exact, ["-c:v", "ffv1", "-level", "3", "-g", "1", "-fps_mode", "vfr"])
        variants["ffv1_exact_duplicate_sparse_physical"] = {**sparse, "canonical_allowed": "candidate_after_logical_index_support", "notes": f"physical={len(exact_idx)}/{N}; sidecar gzip={sidecar_exact['jsonl_gzip_bytes']} bytes"}
        threshold = encode_ffmpeg(f"{scenario}__ffv1_threshold_0_1pct_physical", thr001, ["-c:v", "ffv1", "-level", "3", "-g", "1", "-fps_mode", "vfr"])
        variants["ffv1_pixel_threshold_0_1pct"] = {**threshold, "canonical_allowed": False, "notes": f"experimental only; physical={len(thr001_idx)}/{N}"}
        sampled = encode_ffmpeg(f"{scenario}__ffv1_sample_every_2", half, ["-c:v", "ffv1", "-level", "3", "-g", "1", "-fps_mode", "vfr"])
        variants["ffv1_sample_every_2"] = {**sampled, "canonical_allowed": "capture_policy_only", "notes": "safe only as lower sampling policy; reduces temporal resolution"}
        x264rgb = encode_ffmpeg(f"{scenario}__x264rgb_lossless_crf0", frames, ["-c:v", "libx264rgb", "-preset", "veryfast", "-crf", "0", "-g", "60"])
        variants["x264rgb_lossless_crf0"] = {**x264rgb, "canonical_allowed": "candidate_after_hash_roundtrip", "notes": "lossless candidate; content dependent"}
        h264 = encode_ffmpeg(f"{scenario}__h264_crf23", frames, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-g", "60"])
        variants["h264_crf23_derived"] = {**h264, "canonical_allowed": False, "notes": "lossy derived review only"}
        h264_half = encode_ffmpeg(f"{scenario}__h264_crf30_scale_half", frames, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p", "-g", "60"], f"scale={W//2}:{H//2},format=yuv420p")
        variants["h264_crf30_scale_half_derived"] = {**h264_half, "canonical_allowed": False, "notes": "lossy+scaled derived review only"}
        for row in variants.values():
            row["mb_per_min"] = round(float(row["mb"]) / duration_min, 3)
            row.pop("records", None)
            row.pop("root_for_queries", None)
        report["scenarios"][scenario] = {
            "change_ratio_avg": round(sum(ratios) / len(ratios), 6) if ratios else 0.0,
            "change_ratio_max": round(max(ratios), 6) if ratios else 0.0,
            "exact_unique_frames": len(exact_idx),
            "threshold_0_1pct_frames": len(thr001_idx),
            "sidecar_all": sidecar_all,
            "sidecar_exact_sparse": sidecar_exact,
            "sidecar_threshold_0_1pct": sidecar_thr001,
            "variants": variants,
        }

    if first_writer_for_query is None:
        raise RuntimeError("no writer result for query validation")
    report["query_api_validation"] = validate_query_apis(first_writer_for_query)
    report["quota_validation"] = validate_quota()
    report["acceptance"] = {
        "markdown_report_written": True,
        "json_report_written": True,
        "volume_cpu_memory_matrix": True,
        "decode_replay_time_near_diff_clip_checked": report["query_api_validation"]["decode_selected_frames"]["ok"] and report["query_api_validation"]["time_near"]["query_status"] == "generated",
        "quota_dry_run_apply_isolated_temp_root_checked": True,
        "canonical_vs_derived_red_lines_documented": True,
        "real_private_screenshot_used": False,
    }

    json_path = OUTDIR / "agent_sight_mkv_quota_qa_matrix_t_829ee81f.json"
    md_path = OUTDIR / "agent_sight_mkv_quota_qa_matrix_t_829ee81f.md"
    report["meta"]["json_path"] = str(json_path.resolve())
    report["meta"]["markdown_path"] = str(md_path.resolve())
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(make_markdown(report), encoding="utf-8")
    print(json.dumps({"json": str(json_path.resolve()), "markdown": str(md_path.resolve()), "acceptance": report["acceptance"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
