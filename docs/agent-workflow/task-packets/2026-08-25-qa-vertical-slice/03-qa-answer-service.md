---
id: "qa-vertical-slice-03"
title: "Build request-local QA service"
status: "done"
parallel-safe: false
depends-on: ["qa-vertical-slice-02"]
base-commit: "6b1548972cc3819d45c89edf0939931d80c4d362"
owner: "unassigned"
---

# Task Packet: Build request-local QA service

## Goal

Deliver the synchronous QA core: a request-local RAG result adapter, complete-turn context builder, user-scoped `QaService`, retry/recovery behavior and content-free telemetry, with no new JSON question writes or shared last-result reads.

## Non-goals

- No background summary/deletion/Memory worker, HTTP route or React UI.
- No alternate RAG stack, model provider or prompt framework.
- No removal of deprecated direct-Python summary compatibility.

## Delivery context

The existing RAG tool can answer across fixed document IDs, but callers read shared `_last_action_data`, which is unsafe under concurrency, and the assistant persists flat JSON questions. The accepted architecture keeps retrieval keyed only by the current question; bounded conversation context is added to the final answer prompt, preventing history from polluting retrieval.

## Relevant files and current interfaces

- `hello_agents/tools/builtin/rag_tool.py:405-413` — `execute_result(action, **kwargs)` resets/reads shared last-action state and is the request-local conversion seam.
- `hello_agents/tools/builtin/rag_tool.py:674-799` — result/source envelopes are currently accumulated into `_last_action_data`.
- `assistants/pdf_learning_assistant.py:473-507` — summary compatibility methods must remain callable for one release.
- `tests/tools/test_rag_tool_multi_document.py:176,226-227` — source/compare contracts currently assert `_last_action_data`; keep legacy behavior while adding a request-local return path.
- `tests/test_assistant_user_isolation.py` — user-runtime isolation regression boundary.
- Existing changes to preserve: packets 01–02.

## Prerequisites

### Packet dependencies

- `qa-vertical-slice-02` must be `done`.

### Repository/base state

- Base commit plus prior packet handoffs/commits.
- `QaRepository` implements atomic pending turns and conditional transitions.

### External prerequisites

- none; tests inject deterministic fake engines.

## Explicit change boundary

### Allowed files

- Modify: `hello_agents/tools/builtin/rag_tool.py`
- Modify: `assistants/pdf_learning_assistant.py`
- Create: `app/qa_answer_engine.py`
- Create: `app/qa_context.py`
- Create: `app/qa_observability.py`
- Create: `app/qa_service.py`
- Create/Test: `tests/test_qa_answer_engine.py`
- Create/Test: `tests/test_qa_context.py`
- Create/Test: `tests/test_qa_observability.py`
- Create/Test: `tests/test_qa_service.py`
- Test: `tests/tools/test_rag_tool_multi_document.py`
- Test: `tests/tools/test_rag_tool_graph.py`
- Test: `tests/assistants/test_pdf_learning_assistant_multi_document.py`
- Test: `tests/test_history_repository.py`
- Test: `tests/test_assistant_user_isolation.py`

### Allowed behavior changes

- Add request-local result propagation and switch ordinary generation away from flat question-history persistence.

### Forbidden changes

- Do not delete or extend `app/summary_tasks.py` or remove its assistant methods.
- Do not put model/RAG work inside SQLite transactions.
- Do not use rendered history as the retrieval query, expose raw prompts/errors, or log content/document paths.
- No API/UI/lifecycle wiring.

## Interface contract

### Consumes

- Packet 02 conversation/message/source records and conditional repository methods.
- `UserRuntimeRegistry.get_session()` and runtime `rag_tool`.
- Existing RAG actions/modes `auto`, `joint`, `compare`, `summary`.

### Produces

- `QaAnswerRequest(question, conversation_context, document_ids, mode, limit, structured_output)`.
- `QaAnswerResult(answer, sources, comparison, comparison_format, graph_sources)` and typed `QaEngineError(code,retryable)`.
- `RagQaAnswerEngine.answer(runtime, request)` using `execute_result()` only.
- `QaContextBuilder.build(...)` with rolling summary plus most recent complete turns under token budget.
- `QaService` create/list/get/ask/retry methods and a small content-free metrics/trace port.

### Invariants

- Retrieval sees only `request.question`; `conversation_context` appears only in final answer prompts and is bounded.
- Context includes only completed user+assistant pairs grouped by `turn_id`; truncation removes oldest whole turns.
- Model work occurs after the pending transaction and before conditional completion.
- Deletion/cancellation/version loss discards late answers; duplicate requests execute the engine once.
- Sources are sanitized immutable drafts before persistence.

## Required behavior

- Ordinary/joint/compare validates fixed scope and returns persisted messages with stable citations.
- Engine failure persists one safe code/trace and never raw exception text; retry is allowed only for current-user retryable failed messages.
- Unknown engine exceptions map to `QA_ENGINE_UNAVAILABLE`.
- Assistant/RAG product generation no longer appends ordinary questions to `history.json`; legacy direct callers retain compatible answer/summary shapes.

## Implementation guidance

Use request-local storage (return values or a scoped context variable) while preserving legacy `_last_action_data` tests. Pass current question to retrieval, optional context to answer prompt builders, and empty context for summary jobs. Estimate context conservatively, drop complete turns first, then rolling summary if still over budget. Persist no rendered prompt.

## Acceptance criteria

- [ ] Concurrent answer calls receive their own sources/comparison data without cross-request bleed.
- [ ] Tests prove retriever input is current question while LLM prompt contains bounded history.
- [ ] Duplicate idempotency and failure/retry paths persist one truthful outcome with safe telemetry.
- [ ] No ordinary product answer writes `history.json.questions`; deprecated summary APIs still pass compatibility tests.

## Test and verification commands

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_answer_engine.py tests/test_qa_context.py tests/test_qa_observability.py tests/test_qa_service.py tests/tools/test_rag_tool_multi_document.py tests/tools/test_rag_tool_graph.py tests/assistants/test_pdf_learning_assistant_multi_document.py tests/test_history_repository.py tests/test_assistant_user_isolation.py --basetemp=.runtime/pytest-qa-answer
git diff --check
```

Expected: all selected tests and diff check PASS.

## Stop conditions

Stop with a reality-conflict report if packet 02 is not done, result/source shapes differ from verified code, request-local behavior requires a public compatibility break, or changes outside allowed files are necessary.

## Implementation handoff

**Packet:** `qa-vertical-slice-03` — `done`

**Delivered result:** Added a request-local RAG result seam and the complete
synchronous QA core. Retrieval receives only the current question; bounded
conversation history is injected only into final ordinary/joint/compare prompts.
Answers, stable vector/graph source snapshots and comparison metadata are
returned through typed records. `QaService` now owns authenticated fixed-scope
conversation creation, synchronous ask/idempotency, safe failure persistence,
retry and content-free telemetry.

**Files and interfaces:**

- `hello_agents/tools/builtin/rag_tool.py` now stores action data/errors in
  per-execution `ContextVar`s while preserving legacy accessors and output.
- `assistants/pdf_learning_assistant.py` uses structured RAG execution and no
  longer appends new ordinary/summary questions to flat JSON history; its
  deprecated summary task methods remain callable.
- `app/qa_answer_engine.py` adds `QaAnswerRequest`, `QaAnswerResult`,
  `QaAnswerEngine`, `QaEngineError` and `RagQaAnswerEngine`.
- `app/qa_context.py` adds complete-turn-only, summary-aware bounded context.
- `app/qa_observability.py` adds an allowlisted content-free telemetry port and
  lock-protected implementation.
- `app/qa_service.py` adds user-scoped create/list/get/ask/retry operations,
  fixed-scope readiness revalidation and safe error/trace mapping.
- Four new QA test suites and the updated Assistant compatibility suite cover
  concurrency, prompts, budgets, isolation, failure and retry behavior.

**Acceptance evidence:**

- RED: the new answer/context/service modules were absent and test collection
  failed as expected.
- Request-local RAG/Assistant increment: `84 passed`.
- Exact final packet command: `98 passed in 54.51s`.
- `python -m compileall -q` passed for every changed/new Python module.
- `git diff --check` passed with only Windows line-ending normalization warnings.
- Search confirms `PDFLearningAssistant.ask()` no longer appends to
  `history["questions"]`; generic legacy repository helpers remain untouched.

**Scope confirmation:** No worker, HTTP route, React UI, application lifecycle,
alternate RAG stack or summary compatibility removal was introduced. Model work
runs after the pending transaction commits. Telemetry accepts no content,
prompt, excerpt or path parameter, and persisted failures contain only a stable
domain code plus opaque trace ID.

**Deviations:** Existing RAG failures use lowercase internal codes such as
`rag_connection`; the service maps these to stable uppercase QA domain codes so
retryability survives persistence without exposing internal/raw errors. The
approved plan's `list_context_messages()` call was satisfied by cursor-paging the
existing Packet 02 `list_messages()` API, avoiding an out-of-bound repository
change.

**Residual risks:** Durable summary workers, deletion fences, Memory linking,
legacy migration, API/lifecycle wiring and React consumption remain assigned to
later packets. Application composition must inject the model-aware token
estimator when it wires `QaContextBuilder`.

**Implementation commits:**

- `1966956` — `refactor: isolate QA answer generation`
- `15c8abe` — `feat: add synchronous QA service`
