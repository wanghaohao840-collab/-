# QA Reconnect and Long-History Pagination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/qa` load the newest bounded history first, expose older conversations/messages on demand, and recover an active durable summary after reload or conversation switching.

**Architecture:** Preserve the existing oldest-first repository read for context/report workers and add a separate recent-message cursor read for the product REST API. Add a user-and-conversation-scoped active-summary discovery resource, consume both cursors with bounded TanStack infinite queries, and derive UI busy/recovery state from server resources rather than browser storage or component memory.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic, SQLite, pytest, React 18, TypeScript, TanStack Query v5, Vitest/Testing Library, Playwright.

## Global Constraints

- Work from `D:\python_self_agent\.worktrees\document-library-vertical-slice` on `codex/qa-vertical-slice`; design base is commit `5204551` and corrective packet base is `7f3b91f`.
- Read `PROJECT_KNOWLEDGE.md` before implementation; current code, tests, configuration, and runtime behavior remain authoritative.
- Preserve `QaRepository.list_messages()` oldest-first semantics for context, report, migration, and worker consumers.
- Do not change database tables, indexes, task statuses, resource IDs, authentication/session behavior, deletion fencing, RAG/Memory boundaries, dependencies, lockfiles, Penpot source, or the visual token system.
- Product message pages contain at most 50 rows; conversation pages contain at most 20 rows. Loading more is explicit and bounded, never eager or unbounded.
- Every repository/API read remains user-scoped; no lease owner, Memory ID, path, prompt, raw exception, or internal worker detail may enter a DTO.
- Browser storage is not a source of truth. Reload recovery must use the active-summary REST resource and the existing durable job resource.
- Keep query roots `['qa','conversations']` and `['qa','messages',conversationId]`, keep active polling at 1500 ms, and keep new API calls abortable.
- Do not intercept Playwright routes, add a production fake flag, or synchronize browser tests with fixed sleeps alone.
- Existing eight QA visual baselines must not be updated. Any visible snapshot difference is a stop-and-review condition.
- Use `D:\python_self_agent\venv\Scripts\python.exe` for Python commands and put pytest temporary files under `.runtime`.

---

## File Map

- `app/qa_repository.py` — owns old and recent message cursor reads; the two methods intentionally have different traversal semantics.
- `app/qa_job_repository.py` — owns user/conversation-scoped active-job discovery and hides worker lease fields.
- `app/qa_service.py` — authenticates product reads and keeps repository details out of routes.
- `api/schemas/qa.py` — defines the additive `{job: QaJobResponse | null}` envelope.
- `api/routes/qa.py` — exposes recent message paging and active-summary discovery under the existing route flag/auth rules.
- `web/src/features/qa/types.ts` — mirrors the additive active-summary envelope.
- `web/src/features/qa/api.ts` — encodes path segments/cursors and keeps `AbortSignal` propagation.
- `web/src/features/qa/queries.ts` — owns bounded infinite queries, flatten/deduplicate order, active-summary discovery, and terminal reconciliation.
- `web/src/components/QaWorkspace/QaWorkspace.tsx` — renders accessible explicit load controls.
- `web/src/pages/QaPage.tsx` — resolves selected-conversation task identity and derives truthful action mutual exclusion.
- `web/e2e/qa-runtime.py` — keeps summaries active long enough for observable reload, only in the isolated E2E process.
- `web/e2e/qa.spec.ts` — proves active-summary reload recovery against the real server.
- `README.md`, `docs/product-ui/README.md`, packet 08, and the final review — record the delivered API/UI behavior and acceptance evidence.

### Task 1: Add a Separate Recent-Message Repository Read

**Files:**
- Modify: `app/qa_repository.py` beside `list_messages()`
- Test: `tests/test_qa_repository.py` beside `test_cursor_pagination_uses_id_tiebreaker`

**Interfaces:**
- Consumes: existing `decode_cursor(cursor) -> tuple[str, str]`, `encode_cursor(timestamp, id) -> str`, `_validated_limit(limit, maximum=200)`, `_not_fenced_clause(table)`, and `_message_from_row(conn, row)`.
- Produces: `QaRepository.list_recent_messages(user_id: str, conversation_id: str, *, cursor: str | None = None, limit: int = 50) -> QaMessagePage`.
- Preserves: `QaRepository.list_messages(...)` exactly as the oldest-first internal traversal.

- [ ] **Step 1: Write failing repository tests for newest-page selection, chronological rendering, equal-timestamp boundaries, isolation, and the old traversal**

Add this test to `tests/test_qa_repository.py`:

```python
def test_recent_message_pages_start_newest_and_page_older_without_overlap(
    repository,
) -> None:
    created = repository.create_conversation(OWNER, documents())
    expected_ids: list[str] = []
    for index in range(3):
        pending = repository.create_pending_turn(
            OWNER,
            created.id,
            f"问题 {index}",
            "auto",
            f"request-{index}",
            now=f"2026-08-26T10:00:0{index}Z",
        )
        assert repository.complete_turn(
            OWNER,
            pending.assistant_message.id,
            pending.assistant_message.version,
            f"回答 {index}",
            (),
            "none",
            None,
            now=f"2026-08-26T10:00:0{index}Z",
        )
        expected_ids.extend(
            (pending.user_message.id, pending.assistant_message.id)
        )

    with connect(repository.db_path) as conn:
        conn.execute(
            "update qa_messages set created_at = '2026-08-26T10:00:00Z' "
            "where conversation_id = ?",
            (created.id,),
        )
    expected_ids = sorted(expected_ids)

    first = repository.list_recent_messages(OWNER, created.id, limit=2)
    second = repository.list_recent_messages(
        OWNER, created.id, cursor=first.next_cursor, limit=2
    )
    third = repository.list_recent_messages(
        OWNER, created.id, cursor=second.next_cursor, limit=2
    )

    assert [item.id for item in first.items] == expected_ids[-2:]
    assert [item.id for item in second.items] == expected_ids[-4:-2]
    assert [item.id for item in third.items] == expected_ids[:2]
    assert first.next_cursor is not None
    assert second.next_cursor is not None
    assert third.next_cursor is None
    assert len({item.id for page in (first, second, third) for item in page.items}) == 6
    assert repository.list_recent_messages(OTHER, created.id).items == ()
    assert [item.id for item in repository.list_messages(OWNER, created.id).items] == expected_ids


def test_recent_first_page_contains_latest_answer_beyond_200_messages(
    repository,
) -> None:
    created = repository.create_conversation(OWNER, documents())
    first_assistant_id = ""
    latest_assistant_id = ""
    for index in range(101):
        timestamp = (
            f"2026-08-26T{10 + index // 60:02d}:{index % 60:02d}:00Z"
        )
        pending = repository.create_pending_turn(
            OWNER,
            created.id,
            f"问题 {index}",
            "auto",
            f"long-history-{index}",
            now=timestamp,
        )
        assert repository.complete_turn(
            OWNER,
            pending.assistant_message.id,
            pending.assistant_message.version,
            f"回答 {index}",
            (),
            "none",
            None,
            now=timestamp,
        )
        if index == 0:
            first_assistant_id = pending.assistant_message.id
        latest_assistant_id = pending.assistant_message.id

    page = repository.list_recent_messages(OWNER, created.id, limit=50)
    ids = {item.id for item in page.items}
    assert len(page.items) == 50
    assert latest_assistant_id in ids
    assert first_assistant_id not in ids
    assert page.next_cursor is not None
```

- [ ] **Step 2: Run the new test and verify the missing API fails**

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py::test_recent_message_pages_start_newest_and_page_older_without_overlap --basetemp=.runtime/pytest-qa-08-repository-red
```

Expected: FAIL with `AttributeError: 'QaRepository' object has no attribute 'list_recent_messages'`.

- [ ] **Step 3: Implement the independent recent-page cursor read**

Add this method immediately after `list_messages()` in `app/qa_repository.py`:

```python
    def list_recent_messages(
        self,
        user_id: str,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> QaMessagePage:
        page_size = _validated_limit(limit, maximum=200)
        params: list[object] = [user_id, conversation_id]
        cursor_clause = ""
        if cursor:
            timestamp, message_id = decode_cursor(cursor)
            cursor_clause = (
                "and (created_at < ? or (created_at = ? and id < ?))"
            )
            params.extend((timestamp, timestamp, message_id))
        params.append(page_size + 1)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                select * from qa_messages
                where user_id = ? and conversation_id = ?
                  and exists (
                      select 1 from qa_conversations
                      where qa_conversations.id = qa_messages.conversation_id
                        and qa_conversations.user_id = qa_messages.user_id
                        and {_not_fenced_clause('qa_conversations')}
                  )
                  {cursor_clause}
                order by created_at desc, id desc
                limit ?
                """,
                params,
            ).fetchall()
            has_more = len(rows) > page_size
            page_rows = rows[:page_size]
            items = tuple(
                self._message_from_row(conn, row)
                for row in reversed(page_rows)
            )
        next_cursor = None
        if has_more and page_rows:
            oldest = page_rows[-1]
            next_cursor = encode_cursor(oldest["created_at"], oldest["id"])
        return QaMessagePage(items, next_cursor)
```

Do not edit the existing `list_messages()` SQL or cursor direction.

- [ ] **Step 4: Run the focused repository suite**

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py --basetemp=.runtime/pytest-qa-08-repository
```

Expected: every test passes, including both recent and oldest-first traversal assertions.

- [ ] **Step 5: Commit the repository slice**

```powershell
git add app/qa_repository.py tests/test_qa_repository.py
git commit -m "feat: page recent QA messages"
```

### Task 2: Expose Recent Messages and Active Summary Discovery

**Files:**
- Modify: `app/qa_job_repository.py` beside `get()`
- Modify: `app/qa_service.py` in `list_messages()` and beside `get_job()`
- Modify: `api/schemas/qa.py` after `QaJobResponse` and beside `job_response()`
- Modify: `api/routes/qa.py` imports and summary routes
- Test: `tests/test_qa_job_repository.py`
- Test: `tests/test_qa_service.py`
- Test: `tests/api/test_qa_routes.py`

**Interfaces:**
- Consumes: `QaRepository.list_recent_messages(...)` from Task 1, `QaJob`, `job_response(job)`, `_require_conversation(user_id, conversation_id)`, existing QA route flag and authenticated GET dependency.
- Produces: `QaJobRepository.get_active_for_conversation(user_id: str, conversation_id: str) -> QaJob | None`, `QaService.get_active_job(session_token: str, conversation_id: str) -> QaJob | None`, `QaActiveJobResponse`, and `GET /api/v1/qa/conversations/{conversation_id}/summary-jobs/active`.

- [ ] **Step 1: Write failing job-repository tests for active, terminal, foreign-user, and fenced states**

Append to `tests/test_qa_job_repository.py`:

```python
def test_active_job_discovery_is_user_conversation_and_fence_scoped(
    repositories,
) -> None:
    qa, jobs = repositories
    created = conversation(qa)
    enqueued = jobs.create_summary_turn_and_job(
        OWNER, created.id, "总结", "client-active", now=iso(NOW)
    )

    assert jobs.get_active_for_conversation(OWNER, created.id).id == enqueued.job.id
    assert jobs.get_active_for_conversation(OTHER, created.id) is None

    cancelled = jobs.request_cancel(OWNER, enqueued.job.id, now=iso(NOW))
    assert cancelled.status == "cancelled"
    assert jobs.get_active_for_conversation(OWNER, created.id) is None

    second = jobs.create_summary_turn_and_job(
        OWNER, created.id, "再次总结", "client-fenced", now=iso(NOW)
    )
    with connect(qa.db_path) as conn:
        conn.execute(
            """
            insert into qa_deletion_fences (
                id, user_id, target_type, target_id, status, stage,
                created_at, updated_at
            ) values ('fence', ?, 'conversation', ?, 'queued', 'fenced', ?, ?)
            """,
            (OWNER, created.id, iso(NOW), iso(NOW)),
        )
    assert jobs.get_active_for_conversation(OWNER, created.id) is None
    assert jobs.get(OWNER, second.job.id) is not None
```

- [ ] **Step 2: Write failing service tests proving product reads switch semantics and ownership is checked before discovery**

In `tests/test_qa_service.py`, import `Mock` with `from unittest.mock import Mock`, then extend `test_summary_service_methods_are_user_scoped_and_notify_worker` before cancellation with:

```python
    active = service.get_active_job(TOKEN, conversation.id)
    assert active is not None
    assert active.id == job.id
    with pytest.raises(QaNotFoundError):
        service.get_active_job("other-token", conversation.id)
```

Also add this isolated dispatch assertion:

```python
def test_product_message_listing_uses_recent_repository_read(
    service_parts, monkeypatch
) -> None:
    service = make_service(service_parts)
    _, repository, _sessions, _library, _telemetry = service_parts
    conversation = service.create_conversation(TOKEN, ["doc-1"])
    recent = repository.list_recent_messages
    recent_spy = Mock(wraps=recent)
    monkeypatch.setattr(repository, "list_recent_messages", recent_spy)

    service.list_messages(TOKEN, conversation.id, limit=7)

    recent_spy.assert_called_once_with(
        OWNER, conversation.id, cursor=None, limit=7
    )
```

- [ ] **Step 3: Write failing route tests for `{job:null}`, active recovery, and safe cross-user 404**

Extend `test_summary_and_deletion_status_resources_are_reconnect_safe` in `tests/api/test_qa_routes.py`:

```python
    active_url = (
        f"/api/v1/qa/conversations/{conversation['conversation_id']}"
        "/summary-jobs/active"
    )
    active = qa_parts.client.get(active_url)
    assert active.status_code == 200
    assert active.json()["job"]["job_id"] == job_id
    assert_safe(active.text)

    cancelled = qa_parts.client.post(
        f"/api/v1/qa/jobs/{job_id}/cancel", headers=csrf(qa_parts)
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert qa_parts.client.get(active_url).json() == {"job": None}

    qa_parts.client.cookies.set(COOKIE, qa_parts.other_token)
    hidden = qa_parts.client.get(active_url)
    assert hidden.status_code == 404
    assert_safe(hidden.text)
```

Import `quote` with `from urllib.parse import quote`, then add this independent route test:

```python
def test_message_route_starts_newest_and_pages_toward_older_history(
    qa_parts,
) -> None:
    conversation = create_conversation(qa_parts)
    session = qa_parts.services.session_registry.get_session(qa_parts.owner_token)
    user_id = str(session.user_id)
    repository = qa_parts.services.qa_service.repository
    turn_ids: list[set[str]] = []
    for index in range(3):
        timestamp = f"2026-08-27T10:00:0{index}Z"
        pending = repository.create_pending_turn(
            user_id,
            conversation["conversation_id"],
            f"问题 {index}",
            "auto",
            f"route-page-{index}",
            now=timestamp,
        )
        assert repository.complete_turn(
            user_id,
            pending.assistant_message.id,
            pending.assistant_message.version,
            f"回答 {index}",
            (),
            "none",
            None,
            now=timestamp,
        )
        turn_ids.append({pending.user_message.id, pending.assistant_message.id})

    url = f"/api/v1/qa/conversations/{conversation['conversation_id']}/messages"
    first = qa_parts.client.get(f"{url}?limit=2")
    assert first.status_code == 200
    first_page = first.json()
    assert {item["message_id"] for item in first_page["items"]} == turn_ids[2]
    assert first_page["next_cursor"] is not None

    second = qa_parts.client.get(
        f"{url}?limit=2&cursor={quote(first_page['next_cursor'], safe='')}"
    )
    assert second.status_code == 200
    assert {item["message_id"] for item in second.json()["items"]} == turn_ids[1]
    assert_safe(first.text)
    assert_safe(second.text)
```

- [ ] **Step 4: Run all new backend tests and verify they fail before implementation**

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_job_repository.py::test_active_job_discovery_is_user_conversation_and_fence_scoped tests/test_qa_service.py::test_product_message_listing_uses_recent_repository_read tests/api/test_qa_routes.py::test_summary_and_deletion_status_resources_are_reconnect_safe --basetemp=.runtime/pytest-qa-08-api-red
```

Expected: FAIL on missing `get_active_for_conversation`, missing `get_active_job`, and/or the absent `/summary-jobs/active` route.

- [ ] **Step 5: Implement active-job repository discovery**

Add immediately after `QaJobRepository.get()` in `app/qa_job_repository.py`:

```python
    def get_active_for_conversation(
        self, user_id: str, conversation_id: str
    ) -> QaJob | None:
        with connect(self.db_path) as conn:
            row = conn.execute(
                f"""
                select qa_jobs.*
                from qa_jobs
                join qa_conversations
                  on qa_conversations.id = qa_jobs.conversation_id
                 and qa_conversations.user_id = qa_jobs.user_id
                where qa_jobs.user_id = ?
                  and qa_jobs.conversation_id = ?
                  and qa_jobs.status in ('queued', 'running')
                  and {_not_fenced_clause('qa_conversations')}
                order by qa_jobs.created_at desc, qa_jobs.id desc
                limit 1
                """,
                (user_id, conversation_id),
            ).fetchone()
            return _job_from_row(row) if row is not None else None
```

- [ ] **Step 6: Switch the product service to recent messages and add authenticated active discovery**

Change `QaService.list_messages()` in `app/qa_service.py` to:

```python
        return self.repository.list_recent_messages(
            str(session.user_id), conversation_id, cursor=cursor, limit=limit
        )
```

Add beside `get_job()`:

```python
    def get_active_job(self, session_token: str, conversation_id: str):
        if self.job_repository is None:
            raise RuntimeError("QA summary workers are not configured")
        session = self.session_registry.get_session(session_token)
        user_id = str(session.user_id)
        self._require_conversation(user_id, conversation_id)
        return self.job_repository.get_active_for_conversation(
            user_id, conversation_id
        )
```

- [ ] **Step 7: Add the safe response envelope and route**

In `api/schemas/qa.py`, add after `QaJobResponse`:

```python
class QaActiveJobResponse(BaseModel):
    job: QaJobResponse | None
```

Add after `job_response()`:

```python
def active_job_response(job: QaJob | None) -> QaActiveJobResponse:
    return QaActiveJobResponse(job=job_response(job) if job is not None else None)
```

Import both names in `api/routes/qa.py`, then add before the summary POST route:

```python
@router.get(
    "/conversations/{conversation_id}/summary-jobs/active",
    response_model=QaActiveJobResponse,
)
def get_active_summary_job(
    conversation_id: UUID,
    request: Request,
    _session: Annotated[UserSession, Depends(get_current_session)],
    service: Annotated[QaService, Depends(get_qa_service)],
):
    if disabled := _disabled(request):
        return disabled
    try:
        return active_job_response(
            service.get_active_job(
                get_session_token(request), str(conversation_id)
            )
        )
    except Exception as error:
        return _domain_error(error)
```

- [ ] **Step 8: Run the complete focused backend gate**

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_qa_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-qa-08-backend
```

Expected: all tests pass; response DTO tests show no internal job fields; foreign/missing/fenced conversations return the existing safe 404 envelope.

- [ ] **Step 9: Commit the backend product API**

```powershell
git add app/qa_job_repository.py app/qa_service.py api/schemas/qa.py api/routes/qa.py tests/test_qa_job_repository.py tests/test_qa_service.py tests/api/test_qa_routes.py
git commit -m "feat: discover active QA summaries"
```

### Task 3: Consume Both Cursors with Bounded Infinite Queries

**Files:**
- Modify: `web/src/features/qa/types.ts`
- Modify: `web/src/features/qa/api.ts`
- Modify: `web/src/features/qa/queries.ts`
- Test: `web/src/features/qa/api.test.ts`
- Test: `web/src/features/qa/queries.test.tsx`

**Interfaces:**
- Consumes: backend `QaConversationPage`, `QaMessagePage`, and `{job: QaJob | null}`; TanStack Query v5 `useInfiniteQuery` and `InfiniteData`.
- Produces: `QaActiveJob`, `qaActiveSummaryKey(id)`, `useQaActiveSummary(id)`, and infinite hooks returning `items`, `hasNextPage`, `fetchNextPage`, and `isFetchingNextPage` while keeping existing root keys.

- [ ] **Step 1: Write API-client tests for exact limits, encoded cursors, encoded IDs, and active discovery**

Extend imports and add this test to `web/src/features/qa/api.test.ts`:

```typescript
import {
  getQaActiveSummary,
  listQaConversations,
  listQaMessages,
} from "./api";

it("encodes bounded pagination and active-summary resource paths", async () => {
  const request = vi.fn().mockResolvedValue({ items: [], next_cursor: null })
    as unknown as AuthContextValue["request"];
  const signal = new AbortController().signal;

  await listQaConversations(request, "time+/older==", signal);
  await listQaMessages(request, "conversation /一", "message+/older==", signal);
  await getQaActiveSummary(request, "conversation /一", signal);

  expect(request).toHaveBeenNthCalledWith(
    1,
    "/api/v1/qa/conversations?limit=20&cursor=time%2B%2Folder%3D%3D",
    { signal },
  );
  expect(request).toHaveBeenNthCalledWith(
    2,
    "/api/v1/qa/conversations/conversation%20%2F%E4%B8%80/messages?limit=50&cursor=message%2B%2Folder%3D%3D",
    { signal },
  );
  expect(request).toHaveBeenNthCalledWith(
    3,
    "/api/v1/qa/conversations/conversation%20%2F%E4%B8%80/summary-jobs/active",
    { signal },
  );
});
```

- [ ] **Step 2: Write infinite-query tests for page order, deduplication, cursor forwarding, and pending polling**

In `web/src/features/qa/queries.test.tsx`, import `useQaConversations`, `useQaMessages`, `useQaActiveSummary`, and `qaActiveSummaryKey`. Add fixture factories with unique `message_id`/`conversation_id`, then add:

```typescript
it("loads recent messages first and prepends older pages without duplicates", async () => {
  const staleDuplicate = { ...message("m-3"), content: "stale-copy" };
  vi.spyOn(qaApi, "listQaMessages")
    .mockResolvedValueOnce({ items: [message("m-3"), message("m-4")], next_cursor: "older-2" })
    .mockResolvedValueOnce({ items: [message("m-1"), message("m-2"), staleDuplicate], next_cursor: null });
  const { wrapper } = harness();
  const { result } = renderHook(() => useQaMessages("conversation-1"), { wrapper });

  await waitFor(() => expect(result.current.items.map((item) => item.message_id)).toEqual(["m-3", "m-4"]));
  await act(() => result.current.fetchNextPage());

  await waitFor(() => expect(result.current.items.map((item) => item.message_id)).toEqual(["m-1", "m-2", "m-3", "m-4"]));
  expect(result.current.items.find((item) => item.message_id === "m-3")?.content).toBe("m-3");
  expect(qaApi.listQaMessages).toHaveBeenNthCalledWith(2, expect.anything(), "conversation-1", "older-2", expect.any(AbortSignal));
  expect(result.current.hasNextPage).toBe(false);
});

it("keeps polling when any loaded message page contains pending work", async () => {
  vi.useFakeTimers();
  const list = vi.spyOn(qaApi, "listQaMessages").mockResolvedValue({
    items: [{ ...pendingMessage, message_id: "pending-latest" }],
    next_cursor: null,
  });
  const { wrapper } = harness();
  renderHook(() => useQaMessages("conversation-1"), { wrapper });
  await vi.advanceTimersByTimeAsync(1500);
  expect(list.mock.calls.length).toBeGreaterThanOrEqual(2);
  vi.useRealTimers();
});

it("discovers the active summary by selected conversation", async () => {
  vi.spyOn(qaApi, "getQaActiveSummary").mockResolvedValue({ job: completedJob });
  const { wrapper } = harness();
  const { result } = renderHook(() => useQaActiveSummary("conversation-1"), { wrapper });
  await waitFor(() => expect(result.current.data?.job?.job_id).toBe("job-1"));
});
```

Add these exact local factories before the tests:

```typescript
const message = (id: string): QaMessage => ({
  ...pendingMessage,
  message_id: id,
  status: "completed",
  content: id,
  completed_at: "now",
});

const conversation = (id: string) => ({
  conversation_id: id,
  title: id,
  origin: "product" as const,
  rolling_summary: "",
  summary_version: 0,
  created_at: "now",
  updated_at: "now",
  last_message_at: "now",
  documents: [],
});
```

Add the parallel conversation assertion:

```typescript
it("keeps conversations recent-first while loading older cursor pages", async () => {
  vi.spyOn(qaApi, "listQaConversations")
    .mockResolvedValueOnce({
      items: [conversation("c-4"), conversation("c-3")],
      next_cursor: "older-conversations",
    })
    .mockResolvedValueOnce({
      items: [conversation("c-3"), conversation("c-2")],
      next_cursor: null,
    });
  const { wrapper } = harness();
  const { result } = renderHook(() => useQaConversations(), { wrapper });

  await waitFor(() => expect(result.current.items.map((item) => item.conversation_id)).toEqual(["c-4", "c-3"]));
  await act(() => result.current.fetchNextPage());

  await waitFor(() => expect(result.current.items.map((item) => item.conversation_id)).toEqual(["c-4", "c-3", "c-2"]));
  expect(qaApi.listQaConversations).toHaveBeenNthCalledWith(
    2,
    expect.anything(),
    "older-conversations",
    expect.any(AbortSignal),
  );
});
```

- [ ] **Step 3: Run the new frontend data tests and verify they fail**

Run:

```powershell
Push-Location web
npm exec vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx
Pop-Location
```

Expected: FAIL because the API signatures, active type/hook, infinite `items`, and `fetchNextPage` do not exist.

- [ ] **Step 4: Add the active response type and cursor-aware API functions**

Add to `web/src/features/qa/types.ts`:

```typescript
export type QaActiveJob = { job: QaJob | null };
```

In `web/src/features/qa/api.ts`, import `QaActiveJob` and add these helpers:

```typescript
const segment = (value: string) => encodeURIComponent(value);
const pageQuery = (limit: number, cursor?: string | null) => {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set("cursor", cursor);
  return params.toString();
};
```

Replace the bounded reads and add discovery:

```typescript
export const listQaConversations = (
  request: AuthRequest,
  cursor?: string | null,
  signal?: AbortSignal,
) => request<QaConversationPage>(
  `/api/v1/qa/conversations?${pageQuery(20, cursor)}`,
  { signal },
);

export const listQaMessages = (
  request: AuthRequest,
  id: string,
  cursor?: string | null,
  signal?: AbortSignal,
) => request<QaMessagePage>(
  `/api/v1/qa/conversations/${segment(id)}/messages?${pageQuery(50, cursor)}`,
  { signal },
);

export const getQaActiveSummary = (
  request: AuthRequest,
  id: string,
  signal?: AbortSignal,
) => request<QaActiveJob>(
  `/api/v1/qa/conversations/${segment(id)}/summary-jobs/active`,
  { signal },
);
```

Use `segment(id)` in the other QA API functions touched by later code so no new path interpolation bypasses encoding.

- [ ] **Step 5: Implement stable flattening and bounded infinite hooks**

Change imports in `web/src/features/qa/queries.ts`:

```typescript
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { InfiniteData } from "@tanstack/react-query";
import type { QaConversation, QaConversationPage, QaDeletion, QaJob, QaMessage, QaMessagePage, QaMode } from "./types";
```

Add the key and pure flatten helpers:

```typescript
export const qaActiveSummaryKey = (id: string) => ["qa", "active-summary", id] as const;

function uniqueById<T>(items: T[], id: (item: T) => string): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const value = id(item);
    if (seen.has(value)) return false;
    seen.add(value);
    return true;
  });
}

export function flattenQaConversations(
  data?: InfiniteData<QaConversationPage>,
): QaConversation[] {
  return uniqueById(
    data?.pages.flatMap((page) => page.items) ?? [],
    (item) => item.conversation_id,
  );
}

export function flattenQaMessages(
  data?: InfiniteData<QaMessagePage>,
): QaMessage[] {
  const newestCopies = new Map<string, QaMessage>();
  for (const page of data?.pages ?? []) {
    for (const item of page.items) {
      if (!newestCopies.has(item.message_id)) {
        newestCopies.set(item.message_id, item);
      }
    }
  }
  return uniqueById(
    [...(data?.pages ?? [])].reverse().flatMap((page) => page.items),
    (item) => item.message_id,
  ).map((item) => newestCopies.get(item.message_id)!);
}
```

Replace the conversation/message hooks and add discovery:

```typescript
export function useQaConversations(enabled = true) {
  const { request } = useAuth();
  const query = useInfiniteQuery({
    queryKey: QA_CONVERSATIONS_KEY,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.listQaConversations(request, pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled,
  });
  return { ...query, items: flattenQaConversations(query.data) };
}

export function useQaMessages(id?: string) {
  const { request } = useAuth();
  const query = useInfiniteQuery({
    queryKey: qaMessagesKey(id ?? ""),
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) => api.listQaMessages(request, id!, pageParam, signal),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: Boolean(id),
    refetchInterval: (current) => current.state.data?.pages
      .some((page) => page.items.some((item) => item.status === "pending"))
      ? 1500
      : false,
  });
  return { ...query, items: flattenQaMessages(query.data) };
}

export function useQaActiveSummary(id?: string) {
  const { request } = useAuth();
  return useQuery({
    queryKey: qaActiveSummaryKey(id ?? ""),
    queryFn: ({ signal }) => api.getQaActiveSummary(request, id!, signal),
    enabled: Boolean(id),
  });
}
```

In `useQaJob`, add terminal invalidation:

```typescript
      void client.invalidateQueries({ queryKey: qaActiveSummaryKey(job.conversation_id) });
```

In `summarize.onSuccess`, seed both resources:

```typescript
onSuccess: (job) => {
  client.setQueryData(qaJobKey(job.job_id), job);
  client.setQueryData(qaActiveSummaryKey(job.conversation_id), { job });
},
```

- [ ] **Step 6: Run data-layer tests, type checking, and lint**

Run:

```powershell
Push-Location web
npm exec vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx
npm run typecheck
npm run lint
Pop-Location
```

Expected: all commands exit 0; TypeScript confirms cursor types and `InfiniteData` flattening are consistent.

- [ ] **Step 7: Commit the bounded query layer**

```powershell
git add web/src/features/qa/types.ts web/src/features/qa/api.ts web/src/features/qa/queries.ts web/src/features/qa/api.test.ts web/src/features/qa/queries.test.tsx
git commit -m "feat: page QA workspace queries"
```

### Task 4: Restore Summary State and Render Explicit Load Controls

**Files:**
- Modify: `web/src/components/QaWorkspace/QaWorkspace.tsx`
- Test: `web/src/components/QaWorkspace/QaWorkspace.test.tsx`
- Modify: `web/src/pages/QaPage.tsx`
- Test: `web/src/pages/QaPage.test.tsx`
- Preserve: `web/src/styles/qa.css`; the existing button and panel primitives provide the required layout and states.

**Interfaces:**
- Consumes: Task 3 hooks with flattened `items`, `hasNextPage`, `fetchNextPage`, `isFetchingNextPage`; `useQaActiveSummary(id)`; existing `useQaJob(id)`.
- Produces: accessible “加载更多对话” and “加载更早消息” controls; current job identity scoped to the selected conversation; `busy` covering ask, summary submit, pending messages, and queued/running summaries.

- [ ] **Step 1: Write component tests for explicit bounded loading controls**

Extend `web/src/components/QaWorkspace/QaWorkspace.test.tsx` imports to include `ConversationList`, then add:

```typescript
it("exposes explicit disabled loading controls only when another page exists", async () => {
  const loadConversations = vi.fn();
  const loadMessages = vi.fn();
  const { rerender } = render(<>
    <ConversationList
      items={[]}
      onSelect={vi.fn()}
      onNew={vi.fn()}
      hasMore
      loadingMore={false}
      onLoadMore={loadConversations}
    />
    <MessageList
      messages={[failed]}
      onSources={vi.fn()}
      onRetry={vi.fn()}
      hasOlder
      loadingOlder
      onLoadOlder={loadMessages}
    />
  </>);

  await userEvent.click(screen.getByRole("button", { name: "加载更多对话" }));
  expect(loadConversations).toHaveBeenCalledOnce();
  expect(screen.getByRole("button", { name: "正在加载更早消息…" })).toBeDisabled();

  rerender(<MessageList messages={[failed]} onSources={vi.fn()} onRetry={vi.fn()} />);
  expect(screen.queryByRole("button", { name: /加载更早消息/ })).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Write page tests for reload recovery, busy mutual exclusion, and terminal release**

Import `waitFor` and `QaJob` (`import type { QaJob } from "../features/qa/types";`), then add a reusable job factory:

```typescript
const runningJob = (jobId: string, conversationId: string): QaJob => ({
  job_id: jobId,
  conversation_id: conversationId,
  input_message_id: "input-active",
  assistant_message_id: "assistant-active",
  status: "running" as const,
  stage: "summarizing",
  progress: 40,
  cancel_requested_at: null,
  attempt_count: 1,
  max_attempts: 3,
  safe_error_code: null,
  trace_id: null,
  created_at: "now",
  started_at: "now",
  finished_at: null,
  updated_at: "running",
});
```

Change `renderPage()` to accept this final options object:

```typescript
type RenderPageOptions = {
  activeJob?: QaJob | null;
  jobResult?: QaJob;
  conversations?: (typeof conversation)[];
};

function renderPage(
  enabled: boolean,
  path = "/qa",
  messages: unknown[] = [],
  options: RenderPageOptions = {},
) {
  const conversationItems = options.conversations ?? [conversation];
```

Keep the existing handler branches and replace/add these GET branches before mutation branches:

```typescript
    if (url === "/api/v1/qa/conversations?limit=20") {
      return Promise.resolve(response({ items: conversationItems, next_cursor: null }));
    }
    if (url.includes("/messages?limit=50")) {
      return Promise.resolve(response({ items: messages, next_cursor: null }));
    }
    if (url.endsWith("/summary-jobs/active") && (init?.method ?? "GET") === "GET") {
      const selected = conversationItems.find((item) => url.includes(item.conversation_id));
      const active = options.activeJob?.conversation_id === selected?.conversation_id
        ? options.activeJob
        : null;
      return Promise.resolve(response({ job: active }));
    }
    if (options.jobResult && url === `/api/v1/qa/jobs/${options.jobResult.job_id}`) {
      return Promise.resolve(response(options.jobResult));
    }
```

Add the reload-state unit assertion:

```typescript
it("recovers an active summary after reload and blocks conflicting actions", async () => {
  const active = runningJob("job-active", conversation.conversation_id);
  renderPage(
    true,
    `/qa?conversation=${conversation.conversation_id}`,
    [],
    { activeJob: active, jobResult: active },
  );

  expect(await screen.findByText(/学习摘要 · 生成中/)).toBeVisible();
  expect(screen.getByRole("button", { name: "取消生成" })).toBeVisible();
  expect(screen.getByLabelText("向这些文档提问")).toBeDisabled();
  expect(screen.getByRole("button", { name: "生成摘要" })).toBeDisabled();
});
```

Add terminal reconciliation and conversation-switch assertions:

```typescript
it("reconciles recovered summary completion and releases actions", async () => {
  const active = runningJob("job-active", conversation.conversation_id);
  const completed = {
    ...active,
    status: "completed" as const,
    stage: "completed",
    progress: 100,
    finished_at: "done",
    updated_at: "done",
  };
  renderPage(
    true,
    `/qa?conversation=${conversation.conversation_id}`,
    [],
    { activeJob: active, jobResult: completed },
  );

  const composer = await screen.findByLabelText("向这些文档提问");
  await waitFor(() => expect(composer).toBeEnabled());
  await waitFor(() => expect(
    fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/summary-jobs/active")).length,
  ).toBeGreaterThanOrEqual(2));
});

it("does not carry a just-created summary identity into another conversation", async () => {
  const second = {
    ...conversation,
    conversation_id: "22222222-2222-4222-8222-222222222222",
    title: "第二个对话",
  };
  renderPage(
    true,
    `/qa?conversation=${conversation.conversation_id}`,
    [],
    { conversations: [conversation, second] },
  );
  await screen.findByRole("heading", { name: "智能问答" });
  await userEvent.click(screen.getByRole("button", { name: "生成摘要" }));
  await userEvent.click(screen.getByRole("button", { name: "对话" }));
  await userEvent.click(screen.getByRole("button", { name: /第二个对话/ }));

  await waitFor(() => expect(screen.queryByText(/学习摘要/)).not.toBeInTheDocument());
});
```

- [ ] **Step 3: Run component/page tests and verify the missing props/recovery logic fail**

Run:

```powershell
Push-Location web
npm exec vitest run src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx
Pop-Location
```

Expected: FAIL because load-control props and active-summary recovery are not implemented.

- [ ] **Step 4: Add accessible load-control props and rendering**

Change `ConversationList` to accept optional pagination props:

```typescript
type ConversationListProps = {
  items: QaConversation[];
  selectedId?: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDeleteSelected?: () => void;
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
};
```

After its `<ol>`/empty state, render:

```tsx
{hasMore && onLoadMore ? (
  <Button hierarchy="secondary" disabled={loadingMore} onClick={onLoadMore}>
    {loadingMore ? "正在加载更多对话…" : "加载更多对话"}
  </Button>
) : null}
```

Change `MessageList` to accept `busy?: boolean`, `hasOlder?: boolean`, `loadingOlder?: boolean`, and `onLoadOlder?: () => void`. Set the failed-message retry button to:

```tsx
<Button hierarchy="secondary" disabled={busy} onClick={() => onRetry(message)}>
  重试回答
</Button>
```

Before the ordered message list, render:

```tsx
{hasOlder && onLoadOlder ? (
  <Button hierarchy="secondary" disabled={loadingOlder} onClick={onLoadOlder}>
    {loadingOlder ? "正在加载更早消息…" : "加载更早消息"}
  </Button>
) : null}
```

Wrap the button and `<ol>` in a fragment. Keep message `key={message.message_id}` and chronological rendering unchanged. Do not add CSS: reuse the existing `Button` and thread layout primitives.

- [ ] **Step 5: Derive selected-conversation job identity from server discovery**

In `QaPage.tsx`, import `useQaActiveSummary`, replace the string state with:

```typescript
const [startedSummary, setStartedSummary] = useState<{
  conversationId: string;
  jobId: string;
}>();
const activeSummary = useQaActiveSummary(enabled ? selectedId : undefined);
const startedJobId = startedSummary?.conversationId === selectedId
  ? startedSummary.jobId
  : undefined;
const summaryJobId = startedJobId ?? activeSummary.data?.job?.job_id;
const job = useQaJob(summaryJobId);

useEffect(() => {
  setStartedSummary(undefined);
}, [selectedId]);
```

Remove the old `useState<string>()` and old `useQaJob` declaration. Resolve current task state and busy state after data loading:

```typescript
const currentJob = job.data ?? activeSummary.data?.job;
const summaryActive = currentJob?.status === "queued" || currentJob?.status === "running";
const busy = mutations.ask.isPending
  || mutations.summarize.isPending
  || currentMessages.some((item) => item.status === "pending")
  || summaryActive;
```

Update summary creation and rendering:

```tsx
<Button
  hierarchy="secondary"
  disabled={busy}
  onClick={() => mutations.summarize.mutate(
    {
      conversationId: selectedId,
      instruction: "",
      clientRequestId: newClientRequestId(),
    },
    {
      onSuccess: (value) => setStartedSummary({
        conversationId: selectedId,
        jobId: value.job_id,
      }),
    },
  )}
>
  生成摘要
</Button>

<SummaryStatus
  job={currentJob}
  onCancel={() => summaryJobId && mutations.cancel.mutate(summaryJobId)}
/>
```

Use `conversations.items` and `messages.items`, and pass pagination props in both desktop and drawer conversation lists plus the thread message list:

```tsx
hasMore={Boolean(conversations.hasNextPage)}
loadingMore={conversations.isFetchingNextPage}
onLoadMore={() => void conversations.fetchNextPage()}
```

```tsx
hasOlder={Boolean(messages.hasNextPage)}
loadingOlder={messages.isFetchingNextPage}
onLoadOlder={() => void messages.fetchNextPage()}
```

Include `activeSummary.error` in `queryError`. Keep switching conversations and cancellation available while `busy`; only ask/retry/summary creation are mutually excluded. Pass `busy={busy}` to `MessageList` so a pending/active task cannot create another retry turn.

- [ ] **Step 6: Run the focused UI gate and inspect for accidental visual changes**

Run:

```powershell
Push-Location web
npm exec vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx
npm run typecheck
npm run lint
npm run build
Pop-Location
git diff --check
```

Expected: all commands exit 0; no visual snapshot file is modified.

- [ ] **Step 7: Commit the recovery UI**

```powershell
git add web/src/components/QaWorkspace/QaWorkspace.tsx web/src/components/QaWorkspace/QaWorkspace.test.tsx web/src/pages/QaPage.tsx web/src/pages/QaPage.test.tsx
git commit -m "feat: restore active QA summary state"
```

### Task 5: Prove Real-Server Reload Recovery and Close the Corrective Review

**Files:**
- Modify: `web/e2e/qa-runtime.py`
- Modify: `web/e2e/qa.spec.ts`
- Modify: `README.md`
- Modify: `docs/product-ui/README.md`
- Modify: `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/08-qa-reconnect-pagination.md`
- Modify: `docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/FINAL_INTEGRATION_REVIEW.md`

**Interfaces:**
- Consumes: real FastAPI server, SQLite durable job state, active-summary GET, existing Playwright fixtures, all focused acceptance behavior from Tasks 1–4.
- Produces: deterministic real-server reload evidence, complete verification counts, packet 08 status `done`, and final review result `accepted` only when every required gate passes.

- [ ] **Step 1: Write the real-server reload assertion before extending test runtime duration**

In the first test in `web/e2e/qa.spec.ts`, replace the current optional cancel block with:

```typescript
  await page.getByRole("button", { name: "生成摘要" }).click();
  const summary = page.locator(".qa-summary-status");
  await expect(summary).toContainText("生成中", { timeout: 15_000 });
  await expect(summary.getByRole("button", { name: "取消生成" })).toBeVisible();

  await page.reload();

  await expect(summary).toContainText("生成中", { timeout: 15_000 });
  await expect(summary.getByRole("button", { name: "取消生成" })).toBeVisible();
  await expect(page.getByLabel("向这些文档提问")).toBeDisabled();
  await expect(page.getByRole("button", { name: "生成摘要" })).toBeDisabled();
  await summary.getByRole("button", { name: "取消生成" }).click();
  await expect(summary).toContainText(/已取消|已完成/, { timeout: 15_000 });
```

Do not add `page.route`, local/session storage, or `waitForTimeout`.

- [ ] **Step 2: Run the real-server QA test and verify the existing four-step adapter is too short**

Run:

```powershell
Push-Location web
npx playwright test e2e/qa.spec.ts --project=desktop --grep "real server preserves" --workers=1
Pop-Location
```

Expected before the adapter change: FAIL because the summary may complete before reload can observe a durable active state. If it already passes repeatedly, retain the current adapter and proceed; do not slow all answers without evidence.

- [ ] **Step 3: Make only summary-mode E2E work long enough for observable reload**

Replace the progress block in `web/e2e/qa-runtime.py` with:

```python
        if callable(progress_callback):
            total = 20 if request.mode == "summary" else 5
            for completed in range(1, total):
                progress_callback("summarizing", completed, total, "safe")
                time.sleep(0.08)
```

This remains isolated behind `web/e2e/qa-runtime.py`; production application creation and engines are unchanged. Cancellation continues to be observed by the worker between progress callbacks.

- [ ] **Step 4: Run the full QA real-server matrix**

Run:

```powershell
Push-Location web
npx playwright test e2e/qa.spec.ts --workers=1
Pop-Location
```

Expected: all QA tests pass across desktop, tablet, and mobile; zero QA skips; the reload assertion observes active progress/cancel from the server. Confirm `git status --short` shows no modified snapshot PNG.

- [ ] **Step 5: Update product documentation with exact delivered behavior**

In `README.md` and `docs/product-ui/README.md`, add a concise QA persistence note containing these statements:

```markdown
- 对话列表每次读取 20 条，消息首屏读取最新 50 条；用户可显式加载更多对话或更早消息。
- 摘要任务由服务端持久化；刷新或重新进入对话后，客户端通过活动任务资源恢复进度与取消入口。
- 浏览器存储不是任务或历史事实源；现有 1500 ms 轮询可在后续替换为 SSE/WebSocket，而资源 ID 与恢复读取保持兼容。
```

Do not claim SSE, WebSocket, distributed queues, or multi-device push has been implemented.

- [ ] **Step 6: Run focused and complete acceptance gates**

Run from the worktree root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/test_qa_repository.py tests/test_qa_job_repository.py tests/test_qa_service.py tests/api/test_qa_routes.py --basetemp=.runtime/pytest-qa-08-focused
Push-Location web
npm exec vitest run src/features/qa/api.test.ts src/features/qa/queries.test.tsx src/components/QaWorkspace/QaWorkspace.test.tsx src/pages/QaPage.test.tsx
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/qa.spec.ts --workers=1
Pop-Location
git diff --check
```

Then run the complete repository gate:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q --basetemp=.runtime/pytest-qa-08-full
Push-Location web
npm test -- --run
npm run test:e2e
npm audit --audit-level=moderate
Pop-Location
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
git diff --check
git status --short
```

Expected: all commands exit 0; QA Playwright has no skip; only the already documented optional/project-conditional skips may remain; npm audit reports 0 moderate-or-higher advisories; pip check reports no broken requirements; no snapshot or out-of-scope file appears.

- [ ] **Step 7: Record exact evidence and accept the final review only if every gate is green**

Replace packet 08’s `Implementation handoff` with:

```markdown
## Implementation handoff

- Packet/status: `qa-vertical-slice-08` / `done`
- Delivered interfaces: recent message cursor paging, active summary discovery, bounded infinite queries, explicit history controls, reload recovery and active-task mutual exclusion.
- Verification: record the exact pass/skip counts and command exits from Step 6; do not estimate or copy earlier counts.
- Scope: no schema, dependency, snapshot, browser-storage, production-fake, auth, deletion, RAG/Memory or internal oldest-first traversal change.
- Deviations/residual risks: record `none` if there are none; otherwise describe the concrete evidence and whether it blocks acceptance.
- Commits: list the Task 1–5 commit hashes.
```

Update `FINAL_INTEGRATION_REVIEW.md` from `changes-required` to `accepted` only after rechecking both original P1 findings against code and Step 6 evidence. The review must explicitly state that (1) >200-message conversations start at the newest page and load older pages without overlap, and (2) an active summary survives browser reload through server discovery with truthful busy/cancel state.

- [ ] **Step 8: Commit acceptance evidence and inspect the final range**

```powershell
git add web/e2e/qa-runtime.py web/e2e/qa.spec.ts README.md docs/product-ui/README.md docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/08-qa-reconnect-pagination.md docs/agent-workflow/task-packets/2026-08-25-qa-vertical-slice/FINAL_INTEGRATION_REVIEW.md
git commit -m "test: prove QA reconnect recovery"
git log --oneline 5204551..HEAD
git diff --stat 5204551..HEAD
git status --short --branch
```

Expected: the range contains only the five planned slices and documentation evidence; the worktree is clean; the final review is `accepted`.

---

## Stop Conditions

Stop and report the concrete blocker instead of broadening scope if any step requires a schema migration, changes internal oldest-first paging, exposes worker internals, stores job/history state in the browser, changes visual baselines, needs route interception, adds a production test path, or touches files outside packet 08’s allowed boundary. Do not mark packet 08 `done` or the final review `accepted` while a required gate is failing.
