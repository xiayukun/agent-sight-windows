from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ai_control.adapters.mcp import MCPStdioAdapter
from ai_control.channels.key_text import validate_key_text_stream
from ai_control.channels.keyboard_events import keyboard_action_summary
from ai_control.diagnostics.input_smoke import (
    DEFAULT_OBSERVATION_CHANNEL_REF,
    apply_manual_report_profile,
    build_manual_windows_input_adapter,
    invalid_action_report,
    not_armed_report,
    run_manual_input_smoke,
)


ARMING_FLAG = "AI_CONTROL_REAL_INPUT_SMOKE"
ARMING_REF = "AI_CONTROL_REAL_INPUT_ARMING_REF"
CONSENT_REF = "AI_CONTROL_REAL_INPUT_CONSENT_REF"
ACTION = "AI_CONTROL_INPUT_SMOKE_ACTION"
ACTION_X = "AI_CONTROL_INPUT_SMOKE_X"
ACTION_Y = "AI_CONTROL_INPUT_SMOKE_Y"
ACTION_BUTTON = "AI_CONTROL_INPUT_SMOKE_BUTTON"
ACTION_TEXT = "AI_CONTROL_INPUT_SMOKE_TEXT"
ACTION_KEY = "AI_CONTROL_INPUT_SMOKE_KEY"
ACTION_MODIFIERS = "AI_CONTROL_INPUT_SMOKE_MODIFIERS"
OBSERVATION_CHANNEL = "AI_CONTROL_INPUT_SMOKE_OBSERVATION_CHANNEL"
REGION_X = "AI_CONTROL_INPUT_SMOKE_REGION_X"
REGION_Y = "AI_CONTROL_INPUT_SMOKE_REGION_Y"
REGION_WIDTH = "AI_CONTROL_INPUT_SMOKE_REGION_WIDTH"
REGION_HEIGHT = "AI_CONTROL_INPUT_SMOKE_REGION_HEIGHT"
POST_CAPTURE = "AI_CONTROL_POST_ACTION_CAPTURE"
POST_CAPTURE_FRAME_COUNT = "AI_CONTROL_POST_ACTION_FRAME_COUNT"
POST_CAPTURE_INTERVAL_MS = "AI_CONTROL_POST_ACTION_INTERVAL_MS"
POST_CAPTURE_DELAY_MS = "AI_CONTROL_POST_ACTION_DELAY_MS"
POST_CAPTURE_DURATION_MS = "AI_CONTROL_POST_ACTION_DURATION_MS"
POST_CAPTURE_MEDIA_KIND = "AI_CONTROL_POST_ACTION_MEDIA_KIND"
POST_CAPTURE_REGION_X = "AI_CONTROL_POST_ACTION_REGION_X"
POST_CAPTURE_REGION_Y = "AI_CONTROL_POST_ACTION_REGION_Y"
POST_CAPTURE_REGION_WIDTH = "AI_CONTROL_POST_ACTION_REGION_WIDTH"
POST_CAPTURE_REGION_HEIGHT = "AI_CONTROL_POST_ACTION_REGION_HEIGHT"
REPORT_PROFILE = "AI_CONTROL_REPORT_PROFILE"
RUNS_DIR = "AI_CONTROL_RUNS_DIR"


def build_manual_input_smoke_report(
    adapter: MCPStdioAdapter | None = None,
    *,
    runs_dir: str | Path | None = None,
    env: dict[str, str] | None = None,
    backend: Any | None = None,
    observation_channels: list[Any] | None = None,
    default_observation_channel_ref: str | None = None,
) -> dict[str, Any]:
    active_env = env if env is not None else os.environ
    active_runs_dir = Path(runs_dir or active_env.get(RUNS_DIR, "runs_input_smoke"))
    report_profile_or_error = _report_profile_from_env(active_env)
    if "error" in report_profile_or_error:
        return invalid_action_report(runs_dir=active_runs_dir, detail=report_profile_or_error["error"])
    report_profile = report_profile_or_error["profile"]
    present = [key for key in [ARMING_FLAG, ARMING_REF, CONSENT_REF] if active_env.get(key)]
    if active_env.get(ARMING_FLAG) != "armed" or not active_env.get(ARMING_REF) or not active_env.get(CONSENT_REF):
        return apply_manual_report_profile(
            not_armed_report(runs_dir=active_runs_dir, env_keys_present=present),
            report_profile,
        )

    action_or_error = _action_from_env(active_env)
    if "error" in action_or_error:
        return apply_manual_report_profile(
            invalid_action_report(
                runs_dir=active_runs_dir,
                detail=action_or_error["error"],
                failure_code=_invalid_report_failure_code(action_or_error["error"]),
            ),
            report_profile,
        )
    observation_or_error = _observation_request_from_env(active_env)
    if "error" in observation_or_error:
        return apply_manual_report_profile(
            invalid_action_report(
                runs_dir=active_runs_dir,
                detail=observation_or_error["error"],
                failure_code=_invalid_report_failure_code(observation_or_error["error"]),
            ),
            report_profile,
        )
    post_capture_or_error = _post_action_capture_policy_from_env(active_env)
    if "error" in post_capture_or_error:
        return apply_manual_report_profile(
            invalid_action_report(
                runs_dir=active_runs_dir,
                detail=post_capture_or_error["error"],
                failure_code=_invalid_report_failure_code(post_capture_or_error["error"]),
            ),
            report_profile,
        )

    observation_channel_ref = active_env.get(OBSERVATION_CHANNEL) or default_observation_channel_ref or DEFAULT_OBSERVATION_CHANNEL_REF
    active_adapter = adapter or build_manual_windows_input_adapter(
        runs_dir=active_runs_dir,
        arming_ref=active_env[ARMING_REF],
        operator_consent_ref=active_env[CONSENT_REF],
        backend=backend,
        observation_channels=observation_channels,
        default_observation_channel_ref=observation_channel_ref if observation_channels is not None else None,
    )
    report = run_manual_input_smoke(
        active_adapter,
        runs_dir=active_runs_dir,
        arming_ref=active_env[ARMING_REF],
        operator_consent_ref=active_env[CONSENT_REF],
        action=action_or_error,
        observation_channel_ref=observation_channel_ref,
        observation_request=observation_or_error,
        post_action_capture_policy=post_capture_or_error,
    )
    return apply_manual_report_profile(report, report_profile)


def main() -> int:
    report = build_manual_input_smoke_report()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report["exit_code"])


def _action_from_env(env: dict[str, str]) -> dict[str, Any]:
    action = env.get(ACTION, "wait")
    if action == "wait":
        return {"input_type": "wait", "duration_ms": 1}
    if action == "candidate_click":
        button = env.get(ACTION_BUTTON, "left")
        if button not in {"left", "right"}:
            return {"error": "candidate_click button must be left or right"}
        return {"input_type": "candidate_click", "button": button}
    if action == "key_text_stream":
        try:
            text = validate_key_text_stream(env.get(ACTION_TEXT))
        except ValueError as exc:
            return {"error": str(exc)}
        return {"input_type": "key_text_stream", "text": text}
    if action in {"key_press", "key_down", "key_up"}:
        payload = {"input_type": action, "key": env.get(ACTION_KEY)}
        try:
            keyboard_action_summary(action, payload)
        except ValueError as exc:
            return {"error": str(exc)}
        return payload
    if action == "key_chord":
        modifiers = [item.strip() for item in env.get(ACTION_MODIFIERS, "").split("+") if item.strip()]
        payload = {"input_type": "key_chord", "modifiers": modifiers, "key": env.get(ACTION_KEY)}
        try:
            keyboard_action_summary("key_chord", payload)
        except ValueError as exc:
            return {"error": str(exc)}
        return payload
    if action != "mouse_click":
        return {"error": f"unsupported action: {action!r}"}
    try:
        x = int(env.get(ACTION_X, ""))
        y = int(env.get(ACTION_Y, ""))
    except ValueError:
        return {"error": "mouse_click requires integer AI_CONTROL_INPUT_SMOKE_X/Y"}
    button = env.get(ACTION_BUTTON, "left")
    if button not in {"left", "right"}:
        return {"error": "mouse_click button must be left or right"}
    return {"input_type": "mouse_click", "x": x, "y": y, "button": button}


def _observation_request_from_env(env: dict[str, str]) -> dict[str, Any]:
    region_keys = [REGION_X, REGION_Y, REGION_WIDTH, REGION_HEIGHT]
    present = [key for key in region_keys if env.get(key) is not None]
    if not present:
        return {"mode": "fullscreen"}
    if len(present) != len(region_keys):
        return {"error": "region observation requires all AI_CONTROL_INPUT_SMOKE_REGION_X/Y/WIDTH/HEIGHT values"}
    try:
        region = {
            "x": int(env[REGION_X]),
            "y": int(env[REGION_Y]),
            "width": int(env[REGION_WIDTH]),
            "height": int(env[REGION_HEIGHT]),
        }
    except ValueError:
        return {"error": "region observation requires integer AI_CONTROL_INPUT_SMOKE_REGION_X/Y/WIDTH/HEIGHT"}
    if region["width"] <= 0 or region["height"] <= 0:
        return {"error": "region observation requires positive width/height"}
    return {"mode": "region", "region": region}


def _post_action_capture_policy_from_env(env: dict[str, str]) -> dict[str, Any]:
    mode = env.get(POST_CAPTURE, "sequence").strip().lower()
    if mode in {"0", "false", "off", "none", "disabled"}:
        return {
            "enabled": False,
            "schema": "manual_post_action_capture_policy_v1",
            "requested": {"enabled": False, "mode": mode},
        }
    if mode not in {"sequence", "gif", "frames"}:
        return {"error": f"unsupported post-action capture mode: {mode!r}"}

    media_kind_requested = _env_has_value(env, POST_CAPTURE_MEDIA_KIND)
    media_kind = env.get(POST_CAPTURE_MEDIA_KIND, "gif" if mode in {"sequence", "gif"} else "frames").strip().lower()
    if media_kind == "video":
        return {"error": "post-action video capture is not supported in this version"}
    if media_kind not in {"frames", "gif"}:
        return {"error": f"unsupported post-action media kind: {media_kind!r}"}

    frame_count_requested = _env_has_value(env, POST_CAPTURE_FRAME_COUNT)
    interval_requested = _env_has_value(env, POST_CAPTURE_INTERVAL_MS)
    delay_requested = _env_has_value(env, POST_CAPTURE_DELAY_MS)
    duration_requested = _env_has_value(env, POST_CAPTURE_DURATION_MS)
    try:
        interval_ms = _optional_int(env, POST_CAPTURE_INTERVAL_MS, 100)
        delay_ms = _optional_int(env, POST_CAPTURE_DELAY_MS, 0)
        duration_ms = _optional_int(env, POST_CAPTURE_DURATION_MS, 0)
        frame_count = _optional_int(env, POST_CAPTURE_FRAME_COUNT, 0)
    except ValueError as exc:
        return {"error": str(exc)}

    if interval_ms < 50 or interval_ms > 250:
        return {"error": "post-action interval must be between 50 and 250 ms"}
    if delay_ms < 0 or delay_ms > 5000:
        return {"error": "post-action delay must be between 0 and 5000 ms"}
    if duration_ms < 0 or duration_ms > 1000:
        return {"error": "post-action duration must be between 0 and 1000 ms"}
    if frame_count == 0:
        frame_count = max(2, int(duration_ms / interval_ms) + 1) if duration_ms else 2
    if frame_count < 2 or frame_count > 5:
        return {"error": "post-action frame count must be between 2 and 5"}
    if (frame_count - 1) * interval_ms > 1000:
        return {"error": "post-action sequence total duration must be <= 1000 ms"}
    duration_derivation = (
        "explicit_frame_count"
        if frame_count_requested
        else "duration_interval_bounded"
        if duration_requested
        else "default_frame_count"
    )

    policy: dict[str, Any] = {
        "enabled": True,
        "schema": "manual_post_action_capture_policy_v1",
        "mode": "sequence",
        "frame_count": frame_count,
        "interval_ms": interval_ms,
        "delay_ms": delay_ms,
        "duration_ms": duration_ms,
        "effective_duration_ms": (frame_count - 1) * interval_ms,
        "duration_derivation": duration_derivation,
        "media_kind": media_kind,
        "video_requested": False,
        "video_supported": False,
        "min_frame_count": 2,
        "max_frame_count": 5,
        "min_interval_ms": 50,
        "max_interval_ms": 250,
        "max_total_capture_duration_ms": 1000,
        "max_delay_ms": 5000,
        "continuous_recording": False,
        "visualization_only": True,
        "application_effect_unverified": True,
        "requested": {
            "enabled": True,
            "mode": mode,
            "media_kind": media_kind if media_kind_requested else None,
            "frame_count": frame_count if frame_count_requested else None,
            "interval_ms": interval_ms if interval_requested else None,
            "delay_ms": delay_ms if delay_requested else None,
            "duration_ms": duration_ms if duration_requested else None,
        },
    }
    region_or_error = _post_action_region_from_env(env)
    if "error" in region_or_error:
        return region_or_error
    if region_or_error:
        policy["region"] = region_or_error
        policy["requested"]["region"] = region_or_error
    return policy


def _report_profile_from_env(env: dict[str, str]) -> dict[str, str]:
    profile = env.get(REPORT_PROFILE, "full").strip().lower()
    if profile not in {"full", "compact"}:
        return {"error": f"unsupported manual smoke report profile: {profile!r}"}
    return {"profile": profile}


def _invalid_report_failure_code(detail: str) -> str:
    lowered = detail.lower()
    if "video capture is not supported" in lowered:
        return "POST_ACTION_VIDEO_UNSUPPORTED"
    if "post-action" in lowered:
        return "POST_ACTION_CAPTURE_POLICY_INVALID"
    if "region observation" in lowered:
        return "OBSERVATION_REQUEST_INVALID"
    return "MANUAL_INPUT_ACTION_INVALID"


def _post_action_region_from_env(env: dict[str, str]) -> dict[str, int] | dict[str, str]:
    region_keys = [POST_CAPTURE_REGION_X, POST_CAPTURE_REGION_Y, POST_CAPTURE_REGION_WIDTH, POST_CAPTURE_REGION_HEIGHT]
    present = [key for key in region_keys if env.get(key) is not None]
    if not present:
        return {}
    if len(present) != len(region_keys):
        return {"error": "post-action capture region requires all X/Y/WIDTH/HEIGHT values"}
    try:
        region = {
            "x": int(env[POST_CAPTURE_REGION_X]),
            "y": int(env[POST_CAPTURE_REGION_Y]),
            "width": int(env[POST_CAPTURE_REGION_WIDTH]),
            "height": int(env[POST_CAPTURE_REGION_HEIGHT]),
        }
    except ValueError:
        return {"error": "post-action capture region requires integer X/Y/WIDTH/HEIGHT"}
    if region["width"] <= 0 or region["height"] <= 0:
        return {"error": "post-action capture region requires positive width/height"}
    return region


def _optional_int(env: dict[str, str], key: str, default: int) -> int:
    value = env.get(key)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc


def _env_has_value(env: dict[str, str], key: str) -> bool:
    value = env.get(key)
    return value is not None and value != ""


if __name__ == "__main__":
    raise SystemExit(main())
