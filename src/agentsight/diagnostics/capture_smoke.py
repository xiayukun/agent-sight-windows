from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from agentsight.adapters.mcp import MCPStdioAdapter


REGION = {"x": 0, "y": 0, "width": 64, "height": 64}
SEQUENCE_FRAME_COUNT = 2
SEQUENCE_INTERVAL_MS = 50


def run_post_install_capture_smoke(adapter: MCPStdioAdapter, *, runs_dir: str | Path) -> dict[str, Any]:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    transcript: list[dict[str, Any]] = []
    capabilities = adapter.call_tool("get_capabilities", {"probe_mode": "passive"})
    transcript.append(_compact_response("get_capabilities_passive_probe", capabilities))
    diagnostics = capabilities["data"]["capture_diagnostics"] if capabilities.get("ok") else {}
    channels = diagnostics.get("channels", [])
    real_channels = [
        channel
        for channel in channels
        if channel.get("channel_ref") != "mock_screen"
        and channel.get("source_kind") == "software_screen_capture"
        and channel.get("status") == "available"
    ]

    attempted_channels: list[dict[str, Any]] = []
    successful_channels: list[str] = []
    failed_channels: list[dict[str, Any]] = []

    for channel in real_channels:
        channel_ref = channel["channel_ref"]
        supports_sequence = bool(channel.get("supports_sequence") or "sequence" in channel.get("modes", []))
        channel_attempt = {
            "channel_ref": channel_ref,
            "supports_sequence": supports_sequence,
            "region": None,
            "fullscreen": None,
            "sequence": None,
        }
        region_result = adapter.call_tool("observe", {"mode": "region", "region": REGION, "channel_ref": channel_ref})
        region_summary = _summarize_observe("observe_region", region_result, adapter, runs_dir)
        transcript.append(region_summary)
        channel_attempt["region"] = region_summary

        fullscreen_result = adapter.call_tool("observe", {"mode": "fullscreen", "channel_ref": channel_ref})
        fullscreen_summary = _summarize_observe("observe_fullscreen", fullscreen_result, adapter, runs_dir)
        transcript.append(fullscreen_summary)
        channel_attempt["fullscreen"] = fullscreen_summary

        sequence_summary: dict[str, Any] | None = None
        if supports_sequence:
            sequence_result = adapter.call_tool(
                "observe",
                {
                    "mode": "sequence",
                    "frame_count": SEQUENCE_FRAME_COUNT,
                    "interval_ms": SEQUENCE_INTERVAL_MS,
                    "change_detection": True,
                    "channel_ref": channel_ref,
                },
            )
            sequence_summary = _summarize_observe("observe_sequence", sequence_result, adapter, runs_dir)
            transcript.append(sequence_summary)
            channel_attempt["sequence"] = sequence_summary

        attempted_channels.append(channel_attempt)

        sequence_ok = sequence_summary is None or bool(sequence_summary.get("media_valid"))
        if region_summary.get("media_valid") and fullscreen_summary.get("media_valid") and sequence_ok:
            successful_channels.append(channel_ref)
        else:
            failed_channels.append(
                {
                    "channel_ref": channel_ref,
                    "region_ok": bool(region_summary.get("media_valid")),
                    "fullscreen_ok": bool(fullscreen_summary.get("media_valid")),
                    "sequence_ok": sequence_ok,
                    "region_failure_code": region_summary.get("failure_code"),
                    "region_failure_detail": region_summary.get("failure_detail"),
                    "fullscreen_failure_code": fullscreen_summary.get("failure_code"),
                    "fullscreen_failure_detail": fullscreen_summary.get("failure_detail"),
                    "sequence_failure_code": sequence_summary.get("failure_code") if sequence_summary else None,
                    "sequence_failure_detail": sequence_summary.get("failure_detail") if sequence_summary else None,
                }
            )

    package = adapter.call_tool("get_evidence_package")
    replay = adapter.call_tool("read_replay")
    integrity = adapter.call_tool("verify_integrity")
    transcript.append(_compact_response("get_evidence_package", package))
    transcript.append(_compact_response("read_replay", replay))
    transcript.append(_compact_response("verify_integrity", integrity))

    integrity_ok = bool(integrity.get("ok") and integrity.get("data", {}).get("ok"))
    if successful_channels and integrity_ok:
        smoke_status = "real_capture_succeeded"
        exit_code = 0
    elif real_channels:
        smoke_status = "real_capture_failed"
        exit_code = 3
    else:
        smoke_status = "real_capture_not_ready"
        exit_code = 2
    if successful_channels and not integrity_ok:
        smoke_status = "integrity_failed"
        exit_code = 4
    result_attribution, recommended_next = _classify_result(
        smoke_status,
        real_channels=real_channels,
        successful_channels=successful_channels,
    )

    return {
        "object_type": "PostInstallCaptureSmokeReport",
        "smoke_status": smoke_status,
        "exit_code": exit_code,
        "result_attribution": result_attribution,
        "recommended_next": recommended_next,
        "started_at": started_at,
        "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": adapter.session_id,
        "runs_dir": str(runs_dir),
        "diagnostics_ref": capabilities.get("evidence_ref"),
        "real_capture_available": diagnostics.get("real_capture_available", False),
        "available_real_channels": diagnostics.get("available_real_channels", []),
        "attempted_channels": attempted_channels,
        "successful_channels": successful_channels,
        "failed_channels": failed_channels,
        "mock_not_tested": True,
        "install_executed": False,
        "input_executed": False,
        "background_action_executed": False,
        "evidence_package_ok": bool(package.get("ok")),
        "replay_read_only": bool(replay.get("ok") and replay.get("data", {}).get("read_only")),
        "integrity_ok": integrity_ok,
        "transcript": transcript,
    }


def _compact_response(step: str, response: dict[str, Any]) -> dict[str, Any]:
    if response.get("ok"):
        return {
            "step": step,
            "ok": True,
            "object_type": response.get("data", {}).get("object_type"),
            "evidence_ref": response.get("evidence_ref"),
        }
    failure = response.get("failure", {})
    return {
        "step": step,
        "ok": False,
        "failure_code": failure.get("failure_code"),
        "failure_detail": failure.get("detail"),
        "channel_ref": failure.get("channel_ref"),
        "evidence_ref": failure.get("evidence_ref"),
    }


def _summarize_observe(
    step: str,
    response: dict[str, Any],
    adapter: MCPStdioAdapter,
    runs_dir: str | Path,
) -> dict[str, Any]:
    summary = _compact_response(step, response)
    if not response.get("ok"):
        failure = response.get("failure", {})
        summary.update(
            {
                "implementation": failure.get("implementation"),
                "requested_mode": failure.get("requested_mode"),
                "requested_region": failure.get("requested_region"),
                "media_valid": False,
            }
        )
        return summary

    data = response["data"]
    if data.get("object_type") == "ObservationFrameSequence":
        return _summarize_sequence(summary, data, adapter, runs_dir)

    media = _summarize_media(data, adapter, runs_dir, expected_region=REGION if data.get("mode") == "region" else None)
    summary.update(media)
    return summary


def _summarize_sequence(
    summary: dict[str, Any],
    data: dict[str, Any],
    adapter: MCPStdioAdapter,
    runs_dir: str | Path,
) -> dict[str, Any]:
    frame_summaries = [
        _summarize_media(frame, adapter, runs_dir, expected_region=REGION if frame.get("mode") == "region" else None)
        for frame in data.get("frames", [])
    ]
    frame_count_valid = (
        data.get("requested_frame_count") == SEQUENCE_FRAME_COUNT
        and data.get("actual_frame_count") == SEQUENCE_FRAME_COUNT
        and len(frame_summaries) == SEQUENCE_FRAME_COUNT
    )
    frames_valid = all(frame.get("media_valid") for frame in frame_summaries)
    sequence_valid = (
        data.get("channel_ref") != "mock_screen"
        and data.get("real_capture") is True
        and frame_count_valid
        and frames_valid
    )
    summary.update(
        {
            "channel_ref": data.get("channel_ref"),
            "implementation": data.get("implementation"),
            "source_kind": data.get("source_kind"),
            "real_capture": data.get("real_capture"),
            "requested_frame_count": data.get("requested_frame_count"),
            "actual_frame_count": data.get("actual_frame_count"),
            "interval_ms": data.get("interval_ms"),
            "frame_count_valid": frame_count_valid,
            "frame_media_valid": frames_valid,
            "media_valid": bool(sequence_valid),
            "frame_summaries": frame_summaries,
            "change_summary": data.get("change_summary"),
        }
    )
    return summary


def _summarize_media(
    data: dict[str, Any],
    adapter: MCPStdioAdapter,
    runs_dir: str | Path,
    *,
    expected_region: dict[str, int] | None = None,
) -> dict[str, Any]:
    media_ref = data.get("media_ref")
    if not isinstance(media_ref, str):
        return {
            "channel_ref": data.get("channel_ref"),
            "media_ref": media_ref,
            "media_exists": False,
            "media_size_bytes": data.get("media_size_bytes"),
            "media_sha256_matches": False,
            "media_mime": data.get("media_mime"),
            "width": data.get("width"),
            "height": data.get("height"),
            "media_valid": False,
        }
    media_path = Path(runs_dir) / adapter.session_id / media_ref
    exists = media_path.exists()
    media_bytes = media_path.read_bytes() if exists else b""
    media_sha256 = hashlib.sha256(media_bytes).hexdigest() if exists else None
    media_valid = (
        exists
        and len(media_bytes) == data.get("media_size_bytes")
        and media_sha256 == data.get("media_sha256")
        and data.get("channel_ref") != "mock_screen"
        and data.get("media_mime") in {"image/png", "image/bmp"}
        and data.get("width", 0) > 0
        and data.get("height", 0) > 0
    )
    if expected_region:
        media_valid = (
            media_valid
            and data.get("width") == expected_region["width"]
            and data.get("height") == expected_region["height"]
        )
    return {
        "channel_ref": data.get("channel_ref"),
        "media_ref": media_ref,
        "media_exists": exists,
        "media_size_bytes": data.get("media_size_bytes"),
        "media_sha256_matches": media_sha256 == data.get("media_sha256"),
        "media_mime": data.get("media_mime"),
        "width": data.get("width"),
        "height": data.get("height"),
        "media_valid": bool(media_valid),
    }


def _classify_result(
    smoke_status: str,
    *,
    real_channels: list[dict[str, Any]],
    successful_channels: list[str],
) -> tuple[str, str]:
    if smoke_status == "integrity_failed":
        return "evidence_integrity_failed", "inspect_evidence_store"
    if successful_channels:
        return "real_capture_verified", "continue_to_next_development_gate"
    if real_channels:
        return "host_graphics_capture_blocked", "start_wgc_dxgi_capture_spike"
    return "dependency_missing_or_not_ready", "request_optional_dependency_install"
