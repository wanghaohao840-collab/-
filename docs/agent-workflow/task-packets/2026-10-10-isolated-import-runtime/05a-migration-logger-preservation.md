---
id: "2026-10-10-isolated-import-runtime-05a"
title: "Preserve application loggers during Alembic configuration"
status: "done"
parallel-safe: false
depends-on: ["2026-10-10-isolated-import-runtime-04"]
base-commit: "9bd700e39a2dfe802af3be6a9d893a3dad2c6875"
owner: "GPT-6 Sol High"
---

# Corrective packet: migration logger preservation

## Goal and verified conflict

Task05 regression n3 actually exited1:161tests160pass1failure0error/skip. Only tests/api/test_auth_routes.py::test_unexpected_error_returns_generic_envelope_and_logs_no_secrets failed: sanitized HTTP500 and secrecy assertions passed, but expected one captured ERROR record was absent. All122round bindings were unchanged. The eight-file regression does not import the new isolated API module, so its passing26new tests cannot substitute for this failed regression.

Existing tests/integration/test_distributed_import_facades.py:480 upgrades dedicated schema through Alembic. migrations/env.py:16 calls fileConfig(config.config_file_name), whose default disables existing named loggers. alembic.ini configures root/sqlalchemy/alembic, not api.errors. Existing api/errors.py:18,167 owns the module logger and safe request-id-only ERROR. Sol's independent fixed-venv child experiment without DB/pytest proves api.errors.disabled changes false→true with default fileConfig; false→false with disable_existing_loggers=False. Do not fix this by weakening caplog assertions or monkeypatching logger in the test.

## Scope and prerequisites

Read PROJECT_KNOWLEDGE and repository workflow. SolHigh may MODIFY only migrations/env.py: replace the one fileConfig call with fileConfig(config.config_file_name, disable_existing_loggers=False). No other production, migrations, tests, ini/log levels/handlers, SQL, schema or runtime change. This preserves existing application logger enabled state; Alembic still owns its configured root/console setup. This correction does not promise restoration of all root handlers or universal logging isolation.

Root owns05a/05/REVIEW/plan/progress and ignored runners/baselines. Existing Task05 source c137dd4701a931680d8f2ea1b9f990409184643e427b93d4b020b52a6fe745d4 and testbef21d76f4f5baa99d0eb515e274ab5c90f5cee4e6d453f5a94557659ad536ed stay frozen. Preserve15dirty paths listed in packet05; no Git staging/commit/push by implementer. CurrentHEAD9bd700e and exact failedXML16a34089f492713179f8c2a04fc22e39abc543acdab55913c29bcdc8dae4caec/log759097d62614f56cf87a46659a047f4caa77f5db595b3ebf5b2bd37dc54410fd remain historical evidence.

## Required behavior and implementation

After independent Astra contract approval and root assignment, apply the single keyword argument. Compile only; report exact rawSHA and diff. No new generic logging framework, global config workaround, exception logging with secrets, migration reordering or test omission. Root explicitly supersedes only this existing input in a new baseline/freeze and retains all old receipts. The original Task05 two-file interface/route/lifecycle contract is unchanged.

## Acceptance

- [x] Independent Astra approves exact correction, preserving logging configuration semantics and no schema/concurrency behavior change.
- [x] Actual same-ini child experiment imports api.errors before fileConfig, observes enabled after corrected call and exactly one safe ERROR emission for a sentinel request; no DB/service or original secret text printed. Static source syntax confirms migrations/env.py uses the corrected keyword.
- [x] Repeat final Task05 unit/real/full and unfiltered original eight-file regression under the corrected existing-input baseline with unique outputs. All actualpytest/wrapper0, zerofailure/error/skip, unchanged twoTask05candidates/15protected; derive counts/uniqueIDs from rawXML, no reconstructed160+focused arithmetic.
- [x] Unit and real disjoint union full; full and existing regression disjoint. Before/after/current input hashes and XML/log/wrapper binds verified. All previous failed regression and fixture records retained.
- [x] Sol handoff and final independent Astra SPEC/QUALITYPASS before05/05adone or scoped sourcecommit. No PID/main/production gate closed.

## Verification commands

Root-approved runner has fixed D:\python_self_agent\venv\Scripts\python.exe, dedicated4containeridentity/volume/health checks, child-only five fixturekeys, uniqueartifactpaths and solelane GO. Reruns use newnames, preserving earlier unitn3/realn4/fulln3/regressionn3:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py -k 'not real' --basetemp=.runtime/pytest-isolated-runtime-p5-unit-n4 --junitxml=.runtime/isolated-import-runtime/p5-unit-n4.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py -k real --basetemp=.runtime/pytest-isolated-runtime-p5-real-n5 --junitxml=.runtime/isolated-import-runtime/p5-real-n5.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py --basetemp=.runtime/pytest-isolated-runtime-p5-full-n4 --junitxml=.runtime/isolated-import-runtime/p5-full-n4.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py tests/api/test_auth_routes.py tests/api/test_import_routes.py tests/api/test_document_routes.py tests/api/test_mounts.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py tests/test_deployment_settings.py --basetemp=.runtime/pytest-isolated-runtime-p5-regression-n4 --junitxml=.runtime/isolated-import-runtime/p5-regression-n4.xml
```

## Stop conditions and handoff

Report reality conflict if logger cause is unproven, wider root-handler behavior must change, another source path is required or accepted input hashes drift. Do not implement outside scope. Report old/new migrations/env SHA, one-line diff, nonpytest experiment and every actualstage receipt, final baseline exception, unchanged source/protected hashes, limits and commit status. Root finalizes Git after acceptance only.

## Independent ready and assignment

## Final accepted handoff

Done: exact one fileConfig keyword, migrations/env.py SHAa1401c7e463256d7e76f7c146706473b0481ecd3e5002eeb2f3e40d81eec4454; no SQL/schema/authority changes. Independent sourcePASS p5-logging-source-astra-n2.md SHAa06a2e17c7d7e78552125b8e0fc5f6c9d95dd9125597eee57444000805b9c133 and noDB safeERROR experiment passed. Fresh unit22/real4/full26/regression161 allactual0/zeroFES; original auth assertion unchanged and passes. Final Task05/05a SPEC/QUALITYPASS SHA5b03ca5087cee41c58ac7036167104679805ddc4b91f22d140773d70ed0e3f13; combined delivery/lineage/limits in packet05. Root owns scoped Git chain; failed originals preserved.

Astra p5-logging-plan-astra-n1.md SHA9939129d0b4fca5bef3716df30f9516590401353b4dbf1558ea9fc5d6062b047 judged the draft SPEC READY / QUALITY READY without blocking findings. Root assigns SolHigh solely the one call correction. This enables unlisted application loggers under standard fileConfig semantics; it does not promise preservation of every prior disabled flag or root handler. Source review and fresh corrected113input stage results remain required.
