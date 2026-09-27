# Isolated prerequisite procedure

This procedure covers verified component operations only. It cannot start a distributed application or authorize production migration. The complete business/Worker/rollback gates remain in the main plan.

## Durable evidence and order

1. Verify one stopped-write archive pair with `deploy/inventory_paired_backup.py`. Retain archive names, both hashes and the SQLite/file inventory in ignored local evidence. Preserve the source and its journals exactly; do not run writers on it.
2. Restore the pair into a task-owned isolated environment with `deploy/verify_isolated_restore.py`, specifying immutable image identities. Check exact Qdrant collection counts and source data hashes. This old-system restoration is distinct from the required distributed application rollback.
3. Create a new isolated PostgreSQL schema prefixed `cutover_` in a task-owned database. Set the Alembic `DATABASE_URL` search path to that schema and run revision `20260926_01` explicitly. The relational migration helper validates that frozen revision and its 25 business tables, not an arbitrary future head.
4. Set `CUTOVER_TEST_DATABASE_URL` to the isolated database. Run `deploy/migrate_relational_isolated.py` with `--source`, `--expected-sha256`, `--target-schema`, `--mode dry-run`, and `--evidence`; repeat with `apply`, repeat `apply`, then `verify`, retaining separate output files. A nonempty differing target is a hard failure and is never cleared automatically. Additional expansion revisions run only after this baseline copy and verification.
5. Create a dedicated versioned S3 bucket in the isolated service. Set `CUTOVER_TEST_S3_ENDPOINT`, `CUTOVER_TEST_S3_BUCKET`, `CUTOVER_TEST_S3_ACCESS_KEY`, and `CUTOVER_TEST_S3_SECRET_KEY` in the command environment. Never put credentials into a tracked file or command report.
6. Run `python deploy/migrate_files_isolated.py SOURCE_ROOT dry-run MANIFEST`, then `apply`, repeat `apply`, and `verify`. `SOURCE_ROOT` contains `users/`; `MANIFEST` and its sidecars must be outside the frozen source. Dry-run requires no S3 configuration and creates no manifest. Apply writes the sorted source manifest before uploading and checkpoints every verified object version. Preserve that manifest for retries; changing its source or target identity is rejected.

Use the project virtual environment for each Python command. Paths and connection settings must name the isolated resources, and local ephemeral ports must be re-read after Docker restarts. The current CLI file migration covers only document/report bytes. History, Memory, import staging, RAG registry, PostgreSQL object references and application publication require their own later migration steps.

## Failure handling

- Keep a failed target isolated and retain its evidence. Never truncate a nonempty schema or replace the source to make verification pass.
- A partial file upload resumes using the existing manifest; completed entries are read by saved VersionId before remaining writes. Uncheckpointed uploads are recovered through the immutable conditional-put/readback contract.
- An abandoned `MANIFEST.lock` is not broken by age. Verify the controller is no longer running, preserve the manifest, and remove only that exact lock before retry. Never remove a live controller's lock.
- If the bucket/endpoint changes, first verify target identity and retained versions through a deliberate recovery procedure. Editing a manifest to bypass that check is not part of this procedure.
- Do not use component counts or an old-system image restore as evidence that the distributed API, Worker or application rollback passed.
