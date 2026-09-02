# Windows Deployment File-Hash Module Loading Design

## Problem

The complete test suite currently reports 21 failures in
`tests/deploy/test_windows_backup.py` and
`tests/deploy/test_windows_release.py`. The first reproducible failure occurs
when a deployment entry-point script calls `Get-FileHash`: Windows PowerShell
5.1 reports that the command is unavailable even though an independent clean
PowerShell session can resolve it from `Microsoft.PowerShell.Utility`.

The subsequent backup, restore, release-gate, and rollback failures are
cascades from checksum creation or validation stopping before those workflows
reach their intended stages.

## Constraints

- Preserve compatibility with Windows PowerShell 5.1 and PowerShell 7.
- Preserve the existing SHA-256 sidecar format and validation order.
- Do not change backup retention, archive validation, restore swapping,
  release gates, or rollback behavior.
- Do not add a new runtime dependency.
- Keep the fix limited to deployment entry points that directly invoke
  `Get-FileHash` and their deployment contract tests.

## Chosen Design

Each deployment entry-point script that directly calls `Get-FileHash` will
explicitly import its owning built-in module during startup:

```powershell
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
```

The import will be placed after strict error handling is enabled and before
the repository-local deployment modules are imported. This makes the command
dependency deterministic instead of relying on PowerShell module autoloading.
If the required built-in module is genuinely unavailable, the script will
fail immediately before any deployment mutation.

The affected entry points are:

- `deploy/windows/Backup-Deployment.ps1`
- `deploy/windows/Restore-Deployment.ps1`
- `deploy/windows/Invoke-RestoreDrill.ps1`
- `deploy/windows/Update-Deployment.ps1`

The calls to `Get-FileHash` themselves remain unchanged. This preserves the
existing static contract and avoids duplicating module-qualified command names
throughout the scripts.

## Alternatives Rejected

### Module-qualify every call

Using `Microsoft.PowerShell.Utility\Get-FileHash` at every call site also
avoids ambiguous command lookup, but spreads the dependency across all hash
operations and changes more lines without improving startup failure behavior.

### Replace `Get-FileHash` with .NET SHA-256 code

Direct use of `System.Security.Cryptography.SHA256` would avoid the PowerShell
module, but requires stream lifecycle and hexadecimal conversion code. That
increases the security-review surface for no change in required behavior.

## Testing

Deployment contract tests will assert that every entry point which invokes
`Get-FileHash` explicitly imports `Microsoft.PowerShell.Utility` with
`-ErrorAction Stop`.

Verification proceeds in increasing scope:

1. Run the isolated backup/restore round-trip that currently fails.
2. Run `tests/deploy/test_windows_backup.py` and
   `tests/deploy/test_windows_release.py`.
3. Run all deployment tests under `tests/deploy`.
4. Run the complete suite with the repository virtual environment and a fresh
   repository-local `--basetemp` directory.

Success means all 21 previously failing tests pass and the full suite has no
new failures. Tests requiring unconfigured live services or unavailable host
capabilities may remain skipped for their existing documented reasons.

## Non-Goals

- Refactoring checksum operations into a new shared abstraction.
- Changing the backup archive or checksum formats.
- Modifying CI PowerShell selection.
- Fixing unrelated dirty-worktree files or historical test artifacts.
