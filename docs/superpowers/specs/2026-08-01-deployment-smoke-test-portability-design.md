# Deployment Smoke Test Portability Design

## Context

The single-node Docker deployment is healthy on Windows with the WSL 2 backend,
but `deploy/smoke_test.py --deep` does not complete through its public command.
Three test-harness defects were observed during live acceptance:

1. the Windows host decodes Docker's UTF-8 output with the GBK locale;
2. the internal script process starts from `/app/deploy`, so the project-level
   `app` package is not importable without an explicit module path;
3. retrieval succeeds, but the assertion expects the display name
   `deployment-smoke.txt` while the implementation reports the generated
   document ID filename.

The application, Qdrant retrieval, and configured LLM were independently
verified to work. This change is limited to making the public smoke-test command
measure those capabilities correctly on Windows and Linux.

## Chosen Design

Keep the existing host-orchestrator and internal-runner structure.

- `_run_command()` will decode captured subprocess output explicitly as UTF-8
  and replace isolated invalid bytes. Docker CLI output is UTF-8, and replacing
  a malformed diagnostic byte is preferable to losing the real command error
  to a host-locale `UnicodeDecodeError`.
- The Compose `exec` invocation for the private `--inside-deep` mode will pass
  `PYTHONPATH=/app`. This keeps the path adjustment local to the child process
  and avoids mutating `sys.path` globally for every smoke-test mode.
- Each deep run will generate a unique marker, write it into the temporary
  document, search for that marker, and require the marker to be present in the
  retrieval output. The test will not depend on presentation-layer filename
  formatting.
- The LLM question will reference the same marker. Existing failure-message
  handling and cleanup behavior remain unchanged.

## Alternatives Considered

### Add the project root to `sys.path`

This would make direct script execution work, but it changes import behavior for
all modes and hides the fact that only the private container child process needs
the extra path. It is broader than necessary.

### Convert the deployment scripts into an importable package

Running the deep test with `python -m ...` would provide normal package import
semantics, but it requires package-structure changes for a small operational
script. That restructuring is outside this fix.

## Error Handling and Cleanup

The existing nonzero-exit handling, secret sanitization, temporary user logout,
Qdrant document cleanup, and temporary-directory removal remain authoritative.
UTF-8 replacement applies only to captured diagnostics and must not alter exit
codes or secret redaction.

## Verification

Automated tests will verify that:

- subprocess execution requests UTF-8 decoding with replacement;
- the deep child command receives `PYTHONPATH=/app`;
- the marker check accepts retrieved content regardless of displayed filename;
- existing environment parsing and Compose status parsing continue to pass.

Live acceptance will rebuild the application image and run the unchanged public
command from Windows:

```powershell
python deploy/smoke_test.py --env-file deploy/.env --deep
```

Success requires healthy app and Qdrant services, HTTP readiness, writable
persistent storage, local source imports, temporary document import, Qdrant
retrieval of the unique marker, one configured LLM answer, successful cleanup,
and a zero exit status.

## Non-goals

- Changing application retrieval or source-display behavior.
- Enabling Neo4j or changing deployment topology.
- Refactoring the smoke test into a general test framework.
- Changing secrets, user data, or Docker Desktop settings.
