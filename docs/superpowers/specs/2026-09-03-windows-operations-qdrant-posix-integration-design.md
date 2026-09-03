# Windows Operations and Qdrant POSIX Integration Design

**Date:** 2026-09-03  
**Branch:** `codex/notes-vertical-slice`  
**Status:** Approved

## Objective

Integrate the already-developed Windows operations capabilities into the current
Notes branch, migrate Qdrant persistence away from a Windows bind mount to a
Docker-managed Linux/WSL2 POSIX volume, and leave the deployment ready for the
operator to configure a real LLM service before the deep smoke test runs.

The integrated result must support continued product development rather than a
one-off local demo. Backups and restore drills therefore treat the Qdrant named
volume as first-class durable state, scheduled tasks must ultimately target a
stable deployment checkout, and every destructive transition must retain a
verified rollback path.

## Scope

### Included

- Windows login recovery.
- Five-minute deployment health checks and bounded self-healing.
- Daily consistent cold backups.
- Monthly isolated restore drills.
- Operator-triggered secure updates with rollback.
- Migration of Qdrant data to a stable Docker named volume stored by the Linux
  Docker Desktop/WSL2 backend.
- Backup, restore, restore-drill, and update support for the named volume.
- Tests, operational documentation, and local runtime verification.
- Opening the ignored deployment environment file for the operator after the
  operations and storage work passes acceptance.

### Deferred

- Installing production scheduled tasks while the branch still runs from the
  temporary `release-document-library` worktree.
- Executing `smoke_test.py --deep` before the operator supplies real LLM
  credentials and confirms that configuration is complete.
- Moving Qdrant to a remote Linux host or introducing a multi-node Qdrant
  cluster. The named-volume contract is intended to keep that later migration
  possible without changing application APIs.

## Existing State

- The current Compose topology contains required `app` and `qdrant` services
  plus optional `neo4j` under the `graph` profile.
- App and Qdrant currently use host bind mounts below `DEPLOY_DATA_ROOT`.
- The Qdrant bind mount is on NTFS. Qdrant reports that this filesystem cannot
  provide its required persistence guarantees.
- A completed Windows operations implementation exists on
  `codex/port-windows-ops-main`, but its branch base predates the Notes branch
  and its backup model assumes that all durable state is a host directory.
- Two disabled scheduled tasks remain on the host and point at the stable
  repository root. They are not evidence that the full operations suite is
  installed or healthy.
- Docker Desktop uses the Linux/WSL2 backend, but the daemon may be stopped
  while the desktop application is not running.

## Integration Strategy

Use a selective port instead of merging or cherry-picking the historical
operations branch wholesale. Preserve the final hardened behavior of its
PowerShell modules and scripts, but reconcile each file with current Compose,
health, application, and data-layout contracts.

This avoids reintroducing obsolete application code while retaining the tested
operational behavior and its audit history. New or revised tests must cover the
named-volume path as well as the existing Windows safety boundaries.

## Stable Deployment Boundary

Implementation and verification occur in the current Notes worktree. Formal
scheduled-task installation does not occur there. After the Notes branch is
integrated into `D:\python_self_agent`, the installer is run from that stable
repository root and replaces the stale disabled tasks.

The installer must continue to preflight:

- administrator privileges;
- Docker, Compose, archive, and security-scanning commands;
- Docker daemon availability;
- a private active network profile;
- ownership of the application port;
- environment, state, data, and backup paths that do not overlap;
- task definitions fully materialized in memory before any system mutation.

## Qdrant Storage Contract

The Compose service mounts a named volume at `/qdrant/storage`. The stable
default name is `zhiyan_qdrant_data`, configurable through
`QDRANT_VOLUME_NAME`. App and optional Neo4j data remain under
`DEPLOY_DATA_ROOT` for this phase.

The volume is managed by Docker Desktop's Linux/WSL2 storage backend. The
application continues to address Qdrant through `http://qdrant:6333`; no
application API or repository abstraction changes are required.

The migration must never delete the original NTFS directory. Its sequence is:

1. Verify the daemon, Compose configuration, source directory, destination
   volume name, and non-overlapping backup paths.
2. Record running services and stop the deployment consistently.
3. Create a checksummed pre-migration backup of the legacy Qdrant directory.
4. Create an empty destination named volume and import the source through a
   local, already-built deployment image with both paths explicitly mounted.
5. Start Qdrant against the named volume and wait for readiness.
6. Compare source and destination collection metadata and point counts when a
   readable source service is available; always validate the target health and
   expected collection inventory.
7. Start the prior service set and execute the deployment smoke test.
8. On failure, stop the target deployment and retain the source directory,
   migration backup, failed target volume, and diagnostic state for rollback.

Activation is successful only after health and inventory checks pass. The old
directory remains a documented rollback artifact until the operator retires it
in a later maintenance window.

## Backup Format

Each daily backup is a self-describing bundle containing:

- host-backed application data;
- optional Neo4j data when configured;
- a Qdrant volume archive;
- a manifest containing creation time, Git revision, Compose project,
  `QDRANT_VOLUME_NAME`, prior running services, and image identities;
- SHA-256 checksums for every payload and the final bundle.

A single global operations lock serializes backup, restore, restore drill, and
update. The backup records the exact running service set, stops Compose before
capturing mutable state, and restarts only the services that were running before
the operation. Cleanup or restart failures are surfaced rather than silently
discarded.

Volume export and import use an explicitly selected local deployment image,
read-only source mounts where applicable, quoted Docker arguments, and staging
directories constrained below validated roots. The workflow must not depend on
accessing Docker Desktop's internal virtual-disk path from Windows.

## Restore and Rollback

Production restore validates the bundle and all nested checksums before stopping
services. Host data is extracted into a staging directory. Qdrant data is first
restored into a staging volume. The current production state is captured as a
rollback bundle or rollback volume before replacement.

After activation, Compose health and the application smoke test determine
success. A failure restores both host data and Qdrant state, restarts the prior
service set, records a high-priority status, and retains failed staging data for
inspection.

No archive member may be absolute, escape through `..`, or introduce symbolic
or hard links. Restore targets must remain below their declared allowed roots.

## Windows Operations

### Login recovery

An interactive-user logon task starts Docker Desktop when necessary, waits for
the Linux daemon within a bounded deadline, starts the configured Compose
services, and waits for health. Failure is logged and notified without exposing
secrets.

### Five-minute health check

The health task checks daemon availability, Compose service state, container
health, and the public `/healthz` endpoint. It may perform a bounded Compose
recovery for stopped or unhealthy services. Repeated notifications are
rate-limited, while status and logs retain the latest actionable failure.

### Daily cold backup

The daily task runs at 03:00 with wake-to-run enabled and invokes the consistent
bundle workflow described above.

### Monthly isolated restore drill

The monthly task runs at 04:00 on the first Sunday. It restores the newest valid
backup into a unique temporary Compose project, temporary host-data root,
temporary Qdrant volume, and non-production host port. It runs health and smoke
checks, writes an auditable result, then removes only validated temporary
resources. Production data and the production volume are never mounted
writable by the drill.

### Secure update and rollback

Updates remain operator-triggered. The workflow acquires the global lock,
verifies repository and deployment state, creates a fresh consistent backup,
records current image identities, performs the requested source/image update,
builds and scans candidate images, starts them, and runs health and smoke
checks. A failed gate restores images and data from recorded state. Rollback
failure is reported as high priority and never masked by the original update
error.

## Security and Observability

- Secrets are read from the ignored environment file and redacted from logs,
  status, errors, and notifications.
- The installer restricts the environment file ACL and configures only a
  Private/LocalSubnet firewall rule for the application port.
- Health and operations state use bounded logs and structured status output.
- Docker image and Git identities are included in backup and update records.
- Resource names include validated prefixes; scripts refuse ambiguous or broad
  deletion targets.
- Scheduled operations use `IgnoreNew` and the shared lock to prevent overlap.

## Verification

Acceptance for phases one and two requires:

1. PowerShell static parsing and focused Pester-style/pytest contract tests for
   all Windows scripts and common modules.
2. `docker compose config` resolving the named volume and current profiles.
3. A migration dry run plus a real migration of the current Qdrant state.
4. Qdrant readiness and collection/point inventory verification.
5. Application shallow smoke success after migration.
6. Creation and checksum verification of a real cold backup.
7. A successful isolated restore drill using that backup.
8. Exercised update failure/rollback paths without changing the production Git
   checkout.
9. Installer validation in non-mutating mode while still in the worktree.
10. No installation of formal scheduled tasks until integration into
    `D:\python_self_agent`.

After these pass, Codex opens
`D:\python_self_agent\.worktrees\release-document-library\deploy\.env` for
the operator. The deep smoke test remains blocked until the operator supplies a
real endpoint, API key, and model and explicitly confirms completion.

## Future Evolution

The storage and backup manifest identify Qdrant by logical volume name and
payload type rather than Docker Desktop's internal path. A later move to a Linux
host can therefore import the same verified volume archive into a local named
volume or a mounted POSIX block device. Likewise, future multi-node Qdrant
replication may replace cold volume export with supported collection snapshots
without changing the application-facing vector-store interface or the backup
manifest's role as the system-of-record index.
