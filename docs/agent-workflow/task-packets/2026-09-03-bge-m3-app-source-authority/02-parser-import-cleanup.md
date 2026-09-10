---
id: "bge-m3-app-source-authority-02"
title: "Remove obsolete parser import"
status: "done"
parallel-safe: false
depends-on: ["bge-m3-app-source-authority-01"]
base-commit: "a33b071"
owner: "Codex-inline"
---

# Corrective packet: Remove obsolete parser import

## Goal and non-goals

Remove the now-unused hashlib import left in source_json.py after the bounded reader moved to source_stream.py. No runtime behavior, interface, test or parser-limit changes.

## Verified context and prerequisite

Packet 01 is done. source_stream.py now owns hashlib/SHA reader receipt. source_json.py only reads reader.hash and no longer references its own hashlib import. Existing .worktrees/bge-m3-runtime-identity at a33b071 plus E3-A1/A2/A3 dirty work must be preserved.

## Owned-file boundary

Only hello_agents/memory/rag/source_json.py (one import removal), same-name source-authority plan listing, this packet, REVIEW.md and FINAL_INTEGRATION_REVIEW.md. No other source, test, dependency, configuration, production data or Git changes.

## Interface and implementation

All JsonChunkSource, shared parser aliases/limits, inventory and application authority interfaces are unchanged. Apply exactly:

```diff
 from datetime import datetime
-import hashlib
 import math
```

## Acceptance and verification

- [x] source_json.py no longer imports hashlib, while source_stream.py still does.
- [x] All prior focused and combined tests pass, compile/dependency/diff checks pass.

```powershell
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/test_rag_source_authority.py tests/memory/rag/test_source_inventory.py tests/memory/storage/test_qdrant_scan.py tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py -q --tb=short --junitxml=output/e3-a3-focused.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pytest tests/memory tests/test_auth_service.py tests/test_history_repository.py tests/test_user_storage.py tests/test_document_library_service.py tests/test_legacy_migration.py tests/test_legacy_migration_recovery.py tests/test_rag_source_authority.py -q --tb=short --junitxml=output/e3-a3-regression.xml
& 'D:/python_self_agent/venv/Scripts/python.exe' -m compileall -q app/rag_authority.py app/rag_inventory.py hello_agents/memory/rag/source_stream.py hello_agents/memory/rag/source_json.py hello_agents/memory/rag/source_inventory.py tests/test_rag_source_authority.py
& 'D:/python_self_agent/venv/Scripts/python.exe' -m pip check
git diff --check
```

The source plan's complete source_json.py listing must match the file after this one-line removal.

## Stop conditions and handoff

Stop if removing the import changes a used symbol or requires other file edits. Record actual tests/counts, interface invariance, allowed-files confirmation and not-committed status. Codex final integration review follows this packet; no fixes inside final review.

## Implementation handoff

- Packet: bge-m3-app-source-authority-02; status: done.
- Changed source: source_json.py removed only unused hashlib import. Updated exact plan listing and review/packet records.
- Interfaces: unchanged. Shared source_stream.py retains hashlib and reader.hash; original _Reader and _MAX_VALUE_BYTES seams preserved.
- Verification: all commands above passed. Focused 168 passed in 16.36s; combined 647 passed in 46.57s; no failures/errors/skips; groups overlap. Compile/pip/diff checks exit 0, no broken requirements.
- Scope: only owned files; no runtime/source/config/dependency/Git effects beyond the one-line import removal.
- Deviations: none. Residual production gates remain as packet 01/final review.
- Commit: not committed; not pushed.
