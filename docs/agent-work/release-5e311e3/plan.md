# Release validation for 5e311e3

Status: active. User authorized recovery-fix commit, exact-commit candidate build,
isolated upgrade/rollback validation, then a new release operation only after gates pass.

- [ ] Commit DNS compatibility, its tests and completed recovery record separately.
- [ ] Build candidate from clean 5e311e3; verify image provenance and offline startup/upgrade/rollback.
- [ ] Evaluate compatibility with maintenance-aware release tooling. Create a fresh operation only if all gates pass; never reuse the recovered operation.

Production remains pinned to the recovered images during verification. Runtime
configuration, credentials, operation receipts and backups are excluded from Git.
Later UI/startup commits are deliberately outside this exact-source candidate.
