# Release validation for 5e311e3

Status: blocked by candidate maintenance gate. User authorized recovery-fix commit, exact-commit candidate build,
isolated upgrade/rollback validation, then a new release operation only after gates pass.

- [x] Commit DNS compatibility, its tests and completed recovery record separately (`9a5fd74`).
- [x] Build candidate from clean 5e311e3; exercise isolated schema upgrade, application startup and paired rollback (see progress for scope).
- [x] Evaluate compatibility with maintenance-aware release tooling: FAILED; maintenance request still allows application writes.
- [ ] After fixing and validating a separately identified candidate, create a fresh release operation and arrange production deployment; never reuse the recovered operation.

Production remains pinned to the recovered images during verification. Runtime
configuration, credentials, operation receipts and backups are excluded from Git.
Later UI/startup commits are deliberately outside this exact-source candidate.

The exact 5e311e3 image is not eligible for production release. A correction will
require a new source revision; it must not be represented as the original candidate.
