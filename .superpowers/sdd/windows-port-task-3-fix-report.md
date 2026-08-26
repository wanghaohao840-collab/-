# Windows port Task 3 corrective-fix report

## Scope

Corrective work resumed from the interrupted uncommitted patch on top of
`74e8906 feat: adapt deployment upgrades to unified app`.

The committed correction:

- holds the existing exclusive operations lock for the Update-Deployment
  upgrade and rollback window;
- passes the live lock only to same-process Backup-Deployment and
  Restore-Deployment invocations, so the nested calls do not self-deadlock;
- validates that a supplied inherited handle is the live, exclusive
  `operations.lock` handle before accepting it, while keeping that validation
  script-local (not a newly exported bypass);
- retags old images and force-recreates their containers before Restore-
  Deployment restores data after any candidate has been attempted;
- fails the rollback with high priority and does not restore data if old-image
  recreation cannot be established;
- extends both deployment-workflow path filters with `.dockerignore`, `app/**`,
  `assistants/**`, `hello_agents/**`, and `ui/**`.

## Files changed

- `.github/workflows/deployment.yml`
- `deploy/windows/Backup-Deployment.ps1`
- `deploy/windows/Restore-Deployment.ps1`
- `deploy/windows/Update-Deployment.ps1`
- `tests/deploy/test_windows_backup.py`
- `tests/deploy/test_windows_release.py`

## RED evidence

An intermediate implementation put the inherited-lock assertion into
`Operations.Common.psm1` and exported it. The existing exact-public-command
contract rejected that addition:

```text
FAILED tests/deploy/test_windows_operations.py::test_common_module_exports_exact_public_command_set
1 failed, 168 passed, 4 skipped
```

The helper was moved into the backup and restore scripts, where it is private
to the handoff protocol. This preserves the Operations.Common public contract
and avoids exposing an unauthenticated lock-skip command.

A separate full-suite attempt then encountered a Windows `Rename-Item` access
denied error in the pre-existing `test_backup_restore_round_trip_uses_fake_docker_and_retains_rollback` filesystem harness. The identical test passed in a fresh temporary root immediately afterward, and the subsequent full suite passed. No relevant code changed between that failed attempt and its successful rerun.

## GREEN evidence

Focused corrective regressions:

```text
D:\python_self_agent\venv\Scripts\python.exe -m pytest \
  tests/deploy/test_windows_operations.py::test_common_module_exports_exact_public_command_set \
  tests/deploy/test_windows_backup.py::test_backup_accepts_only_the_updates_live_exclusive_lock \
  tests/deploy/test_windows_backup.py::test_backup_rejects_a_forged_inherited_lock \
  tests/deploy/test_windows_release.py::test_overlapping_update_fails_closed_before_release_commands \
  tests/deploy/test_windows_release.py::test_candidate_health_failure_recreates_old_images_before_restoring_data \
  tests/deploy/test_windows_release.py::test_old_image_recreation_failure_stops_before_data_restore \
  tests/deploy/test_windows_release.py::test_deployment_workflow_is_pinned_and_offline_from_secrets \
  -q --basetemp=.pytest-tmp-port-release-fix-green-2

7 passed in 6.08s
```

Required full deployment suite:

```text
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy -q \
  --basetemp=.pytest-tmp-port-release-fix-final-3

169 passed, 4 skipped in 76.91s (0:01:16)
```

`git diff --check` completed with exit code 0.

## Self-review

- Update takes the same `operations.lock` used by backup, restore, and restore
  drill before upgrade checks or release commands, and releases it only in the
  outer `finally` block after success or compensation.
- Backup and restore normally acquire that same lock themselves. Their optional
  inherited handle is accepted only if it names the expected lock file, is live
  and read/write, and an independent exclusive-open probe confirms the lock is
  held. The nested scripts do not dispose a caller-owned handle.
- On candidate health or smoke failure, recovery retags the previous image IDs,
  force-recreates `app` and `qdrant`, confirms both are running, then restores
  data and verifies health/smoke. Recreation failure prevents data restore and
  produces high-priority compensation failure reporting.
- The candidate-health harness asserts the old images are present at restore,
  backup data is restored, and the final recovery smoke uses the old images.
- Workflow contract assertions check all five new paths occur in both pull
  request and main-push filters.

## Concerns

No remaining code or test concern. The one transient `Rename-Item` access
denied failure noted above may indicate a host-level Windows file-handle or
antivirus race in that existing integration harness; it did not reproduce in
the immediate fresh-root rerun or final full deployment suite.
