# AgentSight historical failure regression checklist

This checklist captures the historical failures that must stay covered by repeatable tests or explicit semi-automated acceptance records. Do not use a real `%LOCALAPPDATA%\\AgentSight\\runs*` evidence directory for destructive quota/retention checks; use isolated temp roots or mocks only.

## Automated acceptance coverage

1. Health / service-state fallback
   - `tests/acceptance/test_host_agent_scenarios.py::HostAgentScenariosTest::test_health_scenario_accepts_embedded_health_service_state_when_state_file_is_missing`
   - Purpose: `/health` can report `ok_active_default_desktop` even when `service-state.json` is missing; the packaged/source scenario must use embedded health service state instead of misreporting `host_agent_not_ready`.

2. `post_observe` units and upper bounds
   - `tests/acceptance/test_historical_failure_regressions.py::AgentSightHistoricalFailureRegressionsTest::test_recording_policy_post_observe_uses_seconds_to_frames_and_clamps_to_bounded_window`
   - `tests/acceptance/test_historical_failure_regressions.py::AgentSightHistoricalFailureRegressionsTest::test_explicit_post_observe_and_scale_down_boundaries_fail_honestly`
   - Purpose: tray/HTTP defaults use seconds × FPS, explicit `post_observe.frame_count` remains frames, and 0/negative/over-limit values fail instead of becoming unbounded recordings.

3. Recording defaults and isolated retention/quota
   - `tests/acceptance/test_p1g_tray_gui_control_surface.py::P1GTrayGuiControlSurfaceTest::test_tray_recording_config_defaults_are_written_as_user_visible_config`
   - `tests/acceptance/test_p1g_tray_gui_control_surface.py::P1GTrayGuiControlSurfaceTest::test_recording_settings_dialog_model_follows_language_and_boundaries`
   - `tests/acceptance/test_p1g_tray_gui_control_surface.py::P1GTrayGuiControlSurfaceTest::test_storage_quota_prunes_old_mkv_and_matching_operation_logs`
   - Purpose: default idle/action capture policy, retention days, 5GB-style quota, and quota pruning are validated in isolated temp roots and do not delete real evidence.

4. 60-second post-action recording and repeated-action behavior
   - `tests/acceptance/test_historical_failure_regressions.py::AgentSightHistoricalFailureRegressionsTest::test_repeated_actions_get_independent_bounded_sixty_second_observe_windows`
   - Purpose: a 60s policy becomes a bounded 600-frame / 100ms interval window at 10 FPS; each new action receives its own bounded post-observe window instead of relying on unobservable assumptions.

5. Build/restart parity for source and dist
   - Source-level tests: `tests/acceptance/test_host_agent_scenarios.py` and related protocol tests.
   - Dist-level semi-automated gate below must be run after fixes affecting packaged entry points.

6. Public `/screen`, `/look`, `/do` side effects
   - `tests/acceptance/test_historical_failure_regressions.py::AgentSightHistoricalFailureRegressionsTest::test_public_screen_look_do_do_not_create_legacy_session_media_directories_by_default`
   - Purpose: ordinary public calls should not default-write legacy `session-*` PNG/BMP/GIF media directories; canonical evidence remains MKV + `.frames.jsonl` + `.manifest.json` + operation log.

7. Negative/boundary honest failures
   - Existing coverage in `tests/acceptance/test_p3a_screen_look_do_protocol.py` for readiness blockers, invalid/historical view basis, missing transforms, out-of-bounds regions/points, and no-coordinate guessing.
   - `tests/acceptance/test_historical_failure_regressions.py::AgentSightHistoricalFailureRegressionsTest::test_explicit_post_observe_and_scale_down_boundaries_fail_honestly` adds scale/down and post-observe numeric boundaries.

## Semi-automated dist acceptance gate

Run these only when validating packaged artifacts. They must not stop Hermes or Hermes Gateway; if restart is needed, restart AgentSight only.

```bash
cd '/c/git/家里/AgentSight'
uv run pytest tests/acceptance/test_host_agent_scenarios.py tests/acceptance/test_historical_failure_regressions.py tests/acceptance/test_p1g_tray_gui_control_surface.py::P1GTrayGuiControlSurfaceTest::test_storage_quota_prunes_old_mkv_and_matching_operation_logs tests/acceptance/test_pf2_segment_capture_integration.py::PF2SegmentCaptureIntegrationTest::test_public_look_and_do_post_observe_frames_are_written_to_canonical_segment -q
python tools/build_host_agent_exe.py
./dist/AgentSightHostAgentScenarios.exe --scenario health --wait-seconds 60
```

Expected evidence to save in the Kanban/task report:

- exact command output and exit codes;
- `dist/AgentSightHostAgentScenarios.exe --scenario health` report showing `scenario_status=host_agent_ready`, `service_status=ok_active_default_desktop`, and valid service-state schema/source;
- paths to any new isolated temp evidence used by the tests, if retained intentionally;
- confirmation that real historical `runs/evidence` was not deleted;
- for real 60s/manual operation recording checks, include frames sidecar path, operation log path, segment timestamps, and whether the second action created an independent bounded window or product design intentionally changed that behavior.
