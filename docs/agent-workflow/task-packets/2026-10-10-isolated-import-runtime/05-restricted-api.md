---
id: "2026-10-10-isolated-import-runtime-05"
title: "Restricted authenticated isolated import API"
status: "done"
parallel-safe: false
depends-on: ["2026-10-10-isolated-import-runtime-04", "2026-10-10-isolated-import-runtime-04a", "2026-10-10-isolated-import-runtime-04b"]
base-commit: "9bd700e39a2dfe802af3be6a9d893a3dad2c6875"
owner: "GPT-6 Sol High"
---

# Task Packet: restricted isolated import HTTP composition

## Goal and non-goals

Expose only the approved authenticated import endpoints through a separate FastAPI factory backed by one API-role isolated runtime and the accepted real provider probe. Sessions persist across two app instances; tenant-scoped upload/status and proof-verified binary readback use existing facades. The factory never boots ordinary ApplicationServices, a local session registry or an embedded Worker.

No registration, retry/cancel, QA, notes, learning, reports, deletion, search, legacy mounting, initializer, migration, new dependency, production change or actual PID restart. Two in-process ASGI apps do not establish Task06 independent-process acceptance. Existing production modules remain unchanged.

## Delivery context and verified interfaces

Tasks01–04 and04a/04b are accepted. Source commit66772996f77badb1e12ca12298e45e040582b2ce and progress-only descendant9bd700e are independently remote-verified on codex/distributed-cutover-vertical-slice.433unique passed cases do not establish HTTP or actual PID behavior.

- app/isolated_import_runtime.py:164,250,667: IsolatedImportSettings.from_env('api'); open_isolated_runtime(settings,*,provider_probe=None), validates dedicated PG/schema and returns API-role runtime. Constructor/API factory must not perform resource I/O. Runtime.close:521 performs idempotent close attempts but suppresses resource errors and has no duration bound.
- app/isolated_import_provider.py:141: open_isolated_providers(settings,*,env=None,rag_transport=None,episode_transport=None) returns bundle with probe(), active_threads and close(*,timeout_seconds)->bool. Real SimpleEmbedding384D or exact configured BGE-M3 is used. Only typed IsolatedProviderConfigurationError permits provider-unavailable composition; never synthesize successful ProviderReadiness.
- app/distributed_import_sessions.py:26–58: DistributedImportSessions(runtime). login(username,password)->str token, get_session(token)->SharedSession, validate_csrf(token,csrf)->SharedSession, logout(token). Only the server-resolved trusted user is authority. Login requires get_session(token) for SessionResponse; token itself is not a session object.
- app/distributed_import_service.py:178–350: DistributedImportService(runtime,sessions) requires same runtime/database. submit_uploads(token,csrf,uploads), list_batches(token,*,limit=50), get_batch(token,batch_id), get_task(token,task_id), read_published_document(token,document_id). DistributedImportError has code/status_code/retryable. PublishedImportDocument exposes document_id, filename, suffix, size_bytes and verified content bytes; physical references/proof are private.
- api/config.py:8: ApiConfig.from_environment(), cookie zhiyan_session, HttpOnly, SameSite=lax, path '/', Secure controlled by APP_COOKIE_SECURE. api/schemas/auth.py Credentials/SessionResponse are reusable without mounting auth router. Credentials.password.get_secret_value() only feeds facade login.
- api/schemas/imports.py:28–102: ImportTaskResponse, ImportBatchResponse, import_batch_response and existing private _task_response are safe converters. Explicitly reuse _task_response in this restricted module; no shared converter edit.
- Existing api/routes/auth.py, api/routes/imports.py and api/app.py mount excluded/local behavior; do not include their routers or dependencies.

## Prerequisites and explicit change boundary

Read PROJECT_KNOWLEDGE.md and docs/agent-workflow/README.md. Fixed interpreter D:\python_self_agent\venv\Scripts\python.exe. Existing compatible dedicated PG/S3/target-Q integration fixture and trusted-account baselines must be reused by new tests with unique generated schema/bucket/collections. Source-Q stays exited. No local fallback.

Sol High implementation owns only CREATE api/isolated_import_app.py and CREATE tests/integration/test_isolated_import_api.py. Root owns packet/REVIEW/source-plan/design/progress and ignored evidence runners. No other source/test edits, new public constructor options, dependencies or Worker lifecycle imports. Tests may monkeypatch factory-local openers using existing signatures. Implementation needing another file is a reality conflict.

Preserve15 existing dirty paths: app/coordination.py, app/document_library.py, app/history.py, app/import_models.py, app/import_worker.py, app/memory_repository.py, app/qa_deletion.py, app/qa_persistence.py, app/qa_repository.py, app/recovery.py, migrations/versions/20260926_01_business_schema.sql, migrations/versions/20260928_09_import_leases.py, tests/conftest.py, tests/test_import_worker.py, tests/test_user_mutation_coordination.py. All accepted Task01–04 source inputs are frozen. No implementer staging, commits or push; root performs scoped delivery after final review.

## Exact interface and HTTP contract

Produce create_isolated_import_app(settings: IsolatedImportSettings | None = None)->FastAPI. Resolve/validate API-role settings at factory time without openers/I/O; refuse worker role with a code-only configuration error. Lifespan constructs resources and facades. App state may expose the owned lifecycle status for tests and process evidence, but never public credentials or raw resources in HTTP responses.

Registered routes/methods are exactly:

| Path | Methods | Result |
|---|---|---|
| /healthz | GET | 200 {"status":"ok"} for started accepting app; liveness only, no tenant/provider readiness claim |
| /api/v1/auth/login | POST | SessionResponse and opaque token cookie |
| /api/v1/auth/session | GET | SessionResponse from persisted trusted session |
| /api/v1/auth/logout | POST | validate CSRF, logout original token,204 and matching cookie deletion |
| /api/v1/imports | POST | files multipart,202 ImportBatchResponse |
| /api/v1/imports | GET | batch DTO list, bounded limit1–100 default50 |
| /api/v1/imports/tasks/{task_id} | GET | safe ImportTaskResponse |
| /api/v1/imports/{batch_id} | GET | safe ImportBatchResponse |
| /api/v1/documents/{document_id} | GET | exact verified binary bytes |

Disable openapi_url/docs_url/redoc_url and redirect_slashes. No additional HEAD/OPTIONS/CORS aliases or business routes; unsupported methods405, unmatched paths404. Internal app.openapi() may exist; tests enumerate actual app.routes and also disabled docs URLs. Place explicit task route before dynamic batch route. Do not manually serve raw storage paths.

Read token from configured cookie and X-CSRF-Token header. Pass original strings to every facade; never trust query/header/body user_id. Login uses credentials, not CSRF. Authenticated logout/upload require CSRF before logout deletion, facade-owned upload stream.read and import/business PG/S3 mutations. Existing session lookup/expiry refresh is allowed before CSRF comparison; tests must not install a blanket DB-write bomb that rejects this producer contract. FastAPI multipart parsing may consume network bytes/spool first, so no pre-parser claim. Facade repeats atomic cursor-only session/admission checks.

Upload field must be optional files:list[UploadFile]|None=File(None), and uploads are built from files or [] then passed to the facade. With valid session/CSRF and ready admission, absent file field yields400 import_no_files; unavailable admission retains existing503 precedence before file validation. Empty/unsupported/oversize uploads retain existing facade400/413 codes. Malformed framework fields map to sanitized422 invalid_request; never return validation input/password or exception str. Public error payload is {"detail":{"code":stable_code,"retryable":boolean}}. Preserve known DistributedImportError codes/status/retryable, including dependency_unavailable; auth failures401 invalid_credentials, invalid session401 invalid_session, CSRF/untrusted403 forbidden, unexpected dependency failure503 isolated_import_unavailable retryabletrue. Framework HTTP404/405 and malformed multipart errors also use the sanitized envelope with stable not_found/method_not_allowed/invalid_request codes while retaining their status. Never leak SQL/endpoints/secret passwords/source text in error bodies.

Provider configuration typed refusal keeps auth/status/readback available by composing runtime with provider_probe=None; ordinary submit fails existing503 admission. No API recovery loop. Unexpected provider construction failures or runtime/schema/configuration errors fail startup with code-only error and clean owned partial resources. A successful provider bundle supplies its actual bound probe to the same runtime shared by both facades. No global runtime/provider cache; instances have independent owners.

Binary readback uses facade verified content only, suffix-to-safe MIME mapping (pdf/text/markdown/docx, otherwise octet-stream), Cache-Control:no-store, Content-Disposition:attachment with safe ASCII fallback and RFC5987 UTF8 filename*. Strip/control-safe the ASCII fallback and encode Unicode/quotes/CRLF without injecting headers. Verify exact body size/content. Unknown/other-tenant/deleted documents404, inconsistent evidence/dependency503 as facade specifies. Never expose index identity, object key/version, receipt or full dataclass repr.

## Lifecycle and concurrency contract

One local lifecycle owner per app controls opening, request borrowing, shutdown and eventual resource release using short locks/conditions. No new owner lock spans I/O or joins; frozen runtime internal locks remain unchanged. No new shared/global owner executor and no daemon threads masking leaked work; FastAPI's managed thread pool remains allowed for synchronous handlers. Do not copy/import the entire Worker lifecycle; implement the minimal API-local owner within the one new module.

Startup uses one retained non-daemon opening operation observed within provider_timeout_seconds, covering provider construction and runtime open. A timeout marks owner closing and raises a sanitized startup failure; a late opener must not publish app state or admit requests. Its result/exception remains accounted; resources successfully acquired before/after timeout remain owned and are closed exactly once after the opener drains. Runtime open must finish schema validation before accepting traffic. No replacement opener after timeout; a blocked factory/open does not become cancelled merely because observer timed out.

Every resource-using handler takes a borrower inside its executing synchronous handler thread under the owner gate only while accepting, holds it through facade work and DTO/binary response construction, releases in that same thread's finally on success/failure. Do not place its only borrower lifetime in an async dependency whose cancellation can precede completion of thread work. Closing rejects new borrowers with sanitized503; already borrowed work may complete under existing database authority. HTTP health remains pure liveness and must not access closing resources. Cancellation of a request observer cannot remove the active borrower before the synchronous call actually returns.

On first shutdown signal, mark closing and record one absolute stop_wait_seconds deadline. One retained cleanup operation waits for opening/request borrowers and actual provider probe threads to drain before provider close and runtime.close, each called once. An unbounded request/open/close is retained, never closed underneath a borrower. Lifespan observes this same deadline without blocking the ASGI event loop in an unbounded join. Repeated cleanup/partial-startup/finally paths cannot spawn another closer or reset the deadline. Late operation completion owns eventual cleanup. Report bounded observation as clean/unclean with active operations and code-only status in app.state; clean proves returned provider close success, runtime.close return and drained operations, not suppressed underlying runtime resource-close errors. An unclean lifespan return does not guarantee PID termination while non-daemon threads remain. No claim/cancel/heartbeat/recovery embedded here.

Each owned opening/cleanup operation stores result or exception and completes ownership transfer/retention before setting its completion signal in the executing function's finally. Async observer completion is not operation completion. Cleanup's prerequisite set excludes itself and startup/shutdown observers waiting for cleanup; no self-join or recursive observer wait. Once opener/requests/actual probes drain, provider close failure/exception records unclean but cannot prevent the once-only runtime close attempt. Every close attempt occurs outside owner locks.

Startup/lifespan observe completion via nonblocking async waits/polling: no synchronous Event.wait, Condition.wait or Thread.join on the ASGI loop, even within a nominal finite budget. Timeout/cancellation does not cancel/forget the retained operation or release resources. The first closing transition, including startup cancellation/timeout/partial failure, sets one absolute stop deadline; repeated observers use only remaining time, while eventual cleanup may continue past it without closing underneath live work. Tests must prove event-loop ticker progress during blocked opening/slow close, cancellation during a barrier-blocked synchronous handler, opener late success/exception, provider-close exception followed by runtime-close attempt, cleanup finishing without waiting on itself or its observer, and exactly-once closure after all barriers release.

## Implementation guidance and acceptance criteria

1. Sol verifies current interfaces and writes focused missing-factory RED tests before creating production file. Root freezes baseline/runner, grants solepytest lane and verifies genuine missing-module failure. No source until reviewed RED.
2. Implement two owned files; use accepted facades/converters, small lifecycle owner, stable error mappings. Compile/static diff only until root gives each pytest stage GO. Astra reviews exact candidate bytes and lifecycle barriers before final execution.
3. Unit/controlled tests: exact route/method table, excluded endpoints/docs/HEAD/OPTIONS/slashes404/405, role/no-I/O factory, config/error secrecy, cookie flags, CSRF import-business read/write/deletion bombs while allowing session refresh, absent files HTTP400 with ready admission, provider typed refusal/status independence, safe download headers/body, request failure finally-release. Deterministic barriers cover startup timeout/late success/late exception/partial failure, request cancellation/spanning close, rejection after closing, live probe, slow close, async ticker progress, provider close exception with runtime attempt, completion-signal ordering/no self-wait, repeated shutdown and one total deadline/late cleanup exactly once. Every test releases blockers in finally and joins actual test threads.
4. Real dedicated services: two API app instances share persisted cookie/session and cross-instance logout; trusted/other/untrusted tenants, real submit text/PDF, immutable queued task/source rows/status DTO, confirmed gate409 and baseline/provider503. Publish using accepted Worker/factory or existing publication helper, then proof-verified exact document bytes via HTTP. Missing/changed evidence or S3 bytes fail closed. Any helper is labeled fixture preparation, not independent PID evidence. No synthetic available probe in real acceptance.
5. All executed stages actual pytest/wrapper0, no failure/error/skip; raw IDs/XML/log/beforeafter/current source bindings; unit and real subsets union full new file. Register exact created/removed schema/bucket/collection identities and client close attempts in a secret-free ledger. No live-absence claim without a separate actual query.
6. Root verifies final existing facade/local API/bootstrap regression, accepted source/protected bytes unchanged. Astra independently checks auth+CSRF admission/readback/lifecycle and SPEC/QUALITY PASS beforedone. Scoped root Git blob/test-byte binding and remote-ref verification are separate delivery gates.

## Verification commands and handoff

Root-reviewed private runner injects only five fixture envkeys from exact dedicated containers into real-test children, removes those keys for unit/RED, and preserves unique run names. First RED select test_factory_contract_import on new file; expected missing api.isolated_import_app failure, not fixture failure. Fixed initial GREEN commands:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py -k 'not real' --basetemp=.runtime/pytest-isolated-runtime-p5-unit-n1 --junitxml=.runtime/isolated-import-runtime/p5-unit-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py -k real --basetemp=.runtime/pytest-isolated-runtime-p5-real-n1 --junitxml=.runtime/isolated-import-runtime/p5-real-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_isolated_import_api.py --basetemp=.runtime/pytest-isolated-runtime-p5-full-n1 --junitxml=.runtime/isolated-import-runtime/p5-full-n1.xml
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q tests/integration/test_distributed_import_facades.py tests/api/test_auth_routes.py tests/api/test_import_routes.py tests/api/test_document_routes.py tests/api/test_mounts.py tests/api/test_app_lifecycle.py tests/test_app_bootstrap.py tests/test_deployment_settings.py --basetemp=.runtime/pytest-isolated-runtime-p5-regression-n1 --junitxml=.runtime/isolated-import-runtime/p5-regression-n1.xml
```

No counts inferred from definitions or console progress. Retry uses fresh n2/etc, preserving failed originals. Runtime settings remain isolated; ApplicationServices ordinary distributed mode must still refuse. Solelane GO precedes tests and no self-started services. Stop/report reality conflict for changed interfaces, unavailable baselines, a required edit outside twofiles or unverifiable lifecycle guarantees.

Sol handoff: two source/test hashes, exact interfaces/route table/error shapes, commands/actual counts/IDs/XML/log/runner exits, generated-resource identities/cleanup outcomes, real versus controlled/mock limits, final protected input bindings, approved deviations, commit status and residual Task06–07/live-provider/main/production gates. Root finalizes packetdone only after exact-byte independent acceptance.

## Independent ready gate and assignment

## Final accepted handoff

Done after Task05/05a independent SPEC PASS / QUALITY PASS, no blocking findings. SourceSHA c137dd4701a931680d8f2ea1b9f990409184643e427b93d4b020b52a6fe745d4; testSHA bef21d76f4f5baa99d0eb515e274ab5c90f5cee4e6d453f5a94557659ad536ed. Separate05a adds only one logger keyword. Final unit n4 22pass, real n5 4pass, full n4 26pass, original eight-file regression n4 161pass, allactualpytest/wrapper0 and zeroFES. Unit/real disjoint union26; full/regression disjoint187unique. RED and failed rounds remain retained.

Delivery p5-delivery-root-n2.json SHAa3c7b8378459ef50e830e5687aad5e62c258b3f6c9e5cd574937280508c2b434 binds113existing+2newinputs,127round beforeaftercurrent identical at finalreview and15protectedsame. The three logger inputs entered only fresh rounds via explicit supplement; original122snapshots were not rewritten. Solhand p5-sol-handoff-n3.md SHA6aa010fd640df78efc546894f49b80f7d0a131103c34e1ddbfa7be49afaa7549; finalAstra p5-final-accepted-astra-n1.md SHA5b03ca5087cee41c58ac7036167104679805ddc4b91f22d140773d70ed0e3f13.

Resource19fixtures/247ledger rows paired, not liveabsence. Real PG/S3/Q and two ASGI apps establish in-process HTTP behavior; no independent PID/restart/live remote embedding/main/production acceptance. Runtime-close clean limitations remain. Root performs scoped three-source/six-governance Git delivery and testedblob/remote-ref checks separately; Task06–07 remain open.

Astra n2 report p5-plan-astra-n2.md SHA2567ed1b31beed5ff936f087c5cfb79b28921cfa5799e4e8b200a6a9baee1e6d5fb reviewed packet24515068ddb38f54de85f651277c27abc2a75aeefdc858a5afbd09ebbb7b637a and judged SPEC READY / QUALITY READY, closing R1–R3 with no blocking findings. Root records ready and assigns SolHigh first to test-only missing-factory RED preparation. Statusin_progress is assignment bookkeeping, not source/test acceptance. Root runner GO and verified genuine RED precede production-source authorization.
