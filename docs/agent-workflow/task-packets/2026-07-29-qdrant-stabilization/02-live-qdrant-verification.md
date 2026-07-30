---
id: "qdrant-stabilization-02"
title: "Verify the live Qdrant lifecycle"
status: "done"
parallel-safe: false
depends-on: ["qdrant-stabilization-01"]
base-commit: "614f84e9d01179ce1272281f77e15550c1dcd764"
owner: "Codex"
---

# Task Packet: Verify the live Qdrant lifecycle

## Goal

A repeatable PowerShell command downloads and runs official Qdrant v1.18.2 on
loopback, executes real vector-store and RAG isolation/lifecycle tests without
skips, and always stops the process it started.

## Non-goals

- Docker, WSL, system service registration, Qdrant Cloud, or production auth.
- Payload indexes; those belong to packet 03.
- Changes to core vector-store or RAG implementation.

## Delivery context

Fake-client tests cannot prove compatibility with Qdrant's actual filter,
payload, selector, and response models. Windows has no Docker in this
environment, but Qdrant v1.18.2 publishes an official Windows x86-64 binary.

## Relevant files and current interfaces

- `tests/integration/test_qdrant_document_scope.py:9` — opt-in URL.
- `tests/integration/test_qdrant_document_scope.py:16` — real `MatchAny` test.
- `tests/integration/test_qdrant_document_scope.py:65` — real vector-store contract.
- `hello_agents/memory/rag/qdrant_pipeline.py:28` — pipeline constructor.
- `hello_agents/memory/rag/contracts.py:7` — `DocumentSegment`.
- `.gitignore:12` — `.runtime/` is ignored.
- `requirements.txt:6` — `qdrant-client==1.18.0`.
- Existing README changes must be preserved; append only focused instructions.

## Prerequisites

### Packet dependencies

- `qdrant-stabilization-01` must be `done`.

### Repository/base state

- Base commit plus the dirty worktree and completed packet 01.

### External prerequisites

- Network access to the official GitHub release.
- Permission to install `qdrant-client==1.18.0`, explicitly granted by user.
- Port `127.0.0.1:6333` must be free.

## Explicit change boundary

### Allowed files

- Create: `scripts/run_qdrant_integration.ps1`
- Modify/Test: `tests/integration/test_qdrant_document_scope.py`
- Modify: `README.md`
- Update handoff only: this packet file

### Allowed behavior changes

- Add live RAG tests and a test-only runtime launcher.
- Add reproduction documentation.

### Forbidden changes

- Do not edit `hello_agents/` implementation files.
- Do not edit requirements or `.gitignore`.
- Do not touch Neo4j, UI, assistants, or app files.
- Do not install Docker/WSL or register a persistent service.
- Do not stop a process the runner did not start.

## Interface contract

### Consumes

- Environment: `QDRANT_TEST_URL`, optional `QDRANT_TEST_API_KEY`.
- `QdrantRAGPipeline(..., qdrant_client=client)`.
- `replace_document(document_id, list[DocumentSegment])`.
- `search`, `stats`, `delete_document`, and `clear`.

### Produces

- `scripts/run_qdrant_integration.ps1` with no required parameters.
- Live test coverage for two namespaces and separate documents.

### Invariants

- Runtime files stay under ignored `.runtime/`.
- Test collections use random names and are deleted in `finally`.
- Qdrant listens only on local machine for this test.

## Required behavior

- Download the exact v1.18.2 Windows asset only when missing.
- Verify the server reports v1.18.2 before tests.
- Execute every live test without skip.
- Preserve namespace B through namespace A replacement/deletion.
- Stop and wait for the owned process on success or failure.

## Implementation guidance

Use `Start-Process -WindowStyle Hidden -PassThru`, redirect stdout and stderr
to distinct files, and retain the returned process object. Use `try/finally`
around health polling and pytest. Resolve paths from `$PSScriptRoot`, not the
caller's current directory. Do not delete `.runtime` after the run because the
download should be reusable.

## Acceptance criteria

- [ ] Active `qdrant-client` version is exactly 1.18.0.
- [ ] Server health reports Qdrant 1.18.2.
- [ ] All live tests pass with no skips.
- [ ] Full RAG namespace/document lifecycle is covered.
- [ ] Owned server process is stopped and `.runtime` is untracked.

## Test and verification commands

```powershell
.\venv\Scripts\python.exe -m pip install qdrant-client==1.18.0
.\venv\Scripts\python.exe -c "from importlib.metadata import version; assert version('qdrant-client') == '1.18.0'; print(version('qdrant-client'))"
powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1
```

Expected: client `1.18.0`, server `1.18.2`, all live tests pass, cleanup passes.

```powershell
.\venv\Scripts\python.exe -m pytest tests/memory/test_qdrant_store.py tests/memory/test_semantic_vector_store_protocol.py tests/memory/test_semantic_fallback.py tests/memory/rag tests/tools/test_rag_tool_backend_contract.py -q --basetemp=.runtime/pytest-qdrant-focused
```

Expected: all focused fake-client and contract tests pass.

## Stop conditions

Stop and report `blocked` if port 6333 is already in use, the official asset
cannot be downloaded, the server reports another version, or live behavior
conflicts with current interfaces. Do not substitute embedded Qdrant.

## Implementation handoff

- Packet: `qdrant-stabilization-02`
- Status: `done`
- Delivered:
  - reproducible official Qdrant v1.18.2 Windows launcher and full live RAG
    namespace/document lifecycle verification.
- Files changed:
  - `scripts/run_qdrant_integration.ps1` — download, health check, test, and
    owned-process cleanup.
  - `tests/integration/test_qdrant_document_scope.py` — live RAG lifecycle test.
  - `README.md` — pinned Windows live-test command.
- Interfaces added or changed:
  - added `scripts/run_qdrant_integration.ps1`;
  - public Python interfaces unchanged.
- Acceptance evidence:
  - [x] client version exactly 1.18.0 — importlib metadata assertion.
  - [x] server reported 1.18.2 — launcher health output.
  - [x] live tests ran without skips — 3 passed.
  - [x] namespace B survived namespace A replacement/deletion — live test.
  - [x] process stopped and `.runtime` untracked — launcher output and git status.
- Verification:
  - client version command — PASS, `1.18.0`.
  - `powershell -ExecutionPolicy Bypass -File scripts/run_qdrant_integration.ps1` — PASS twice, 3 passed each run; PIDs 26976 and 24684 stopped.
  - focused fake/contract suite — PASS, 71 passed.
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - none
- Residual risks/follow-ups:
  - service is intentionally loopback-only and unauthenticated for temporary tests.
- Commit:
  - `not committed`
