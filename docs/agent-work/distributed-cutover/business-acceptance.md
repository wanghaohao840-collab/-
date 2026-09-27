# Isolated distributed business acceptance

Status: checklist only; no distributed runtime or business acceptance has passed yet.

Run against two isolated API processes and a separate Worker backed by the same migrated PostgreSQL, versioned S3 bucket and restored Qdrant. Record source revision, image digests, dependency identities and feature flags. Keep credentials, session cookies, source documents and full responses in ignored local evidence only. These are the existing API contracts inspected on 2026-09-27.

| Step | Product request | Required observation |
| --- | --- | --- |
| Authentication | Register, login and `GET /api/v1/auth/session` | Two separate users; one user's cookie and CSRF token work on both API processes. Logout invalidates the session on both. Invalid CSRF is rejected. |
| Migrated state | List documents, QA conversations, notes, learning plans and reports | Preserved IDs and content match the source manifest. Empty learning tables in the September 13 backup are explicitly recorded. |
| Import | Multipart `POST /api/v1/imports`; poll `GET /api/v1/imports/{batch_id}` | Independent Worker completes PDF and text fixtures with stable IDs, object references and tenant-scoped vectors. Terminating the submitting API does not lose the task. |
| Search | `POST /api/v1/search` with query and document IDs | Excerpt and locator identify the expected document/chunk/content hash. PDF page references survive migration and restart. |
| QA | Create `/api/v1/qa/conversations`, then post `/conversations/{id}/messages` | Completed answer and stored sources correspond to the selected document. Repeating the UUID `client_request_id` does not create another answer. Poll pending messages. |
| Source note | `POST /api/v1/notes` using a returned QA citation or search locator | Note preserves provenance and excerpt; replay is idempotent; stale `expected_version` updates fail. Projection runs in the independent Worker. |
| Learning | Create `/api/v1/learning/plans`; list tasks; patch `/api/v1/learning/tasks/{id}` | Plan uses the imported document. Completion persists, replay has no extra event and stale version fails. |
| Reports | Create/read `/api/v1/insights/reports` and download `/{report_id}/download` | Metadata references the retained object version. Downloaded bytes match the saved SHA-256 before and after restart. |
| Tenant isolation | Repeat reads, mutations, search and downloads using the other user's cookie | No body, bytes, citation, existence-sensitive metadata or task result crosses users. |
| Restart | Restart both APIs and Worker; repeat authenticated reads | State persists without process memory or durable local user files. |
| Deletion | Delete document/conversation; poll deletion where applicable | Cleanup completes; source-note deletion semantics match local contracts; a stale worker cannot republish deleted results. |

## Failure and rollback evidence

- Claim work concurrently, expire a lease and pause the original Worker. Replacement completes once; resuming the original cannot publish structured, object or vector state.
- Interrupt PostgreSQL and object storage. No local fallback or successful publication on failure is accepted. Record retry/reconcile results.
- Prove same-user writes serialize across APIs while unrelated users can progress. Record database time, lease identity and commit outcomes without session secrets.
- Build genuinely different compatible candidate and rollback images. On candidate create import, QA, note, learning and report writes; switch application image only, keeping all three stores. Rollback build must read and continue these writes and finish a queued task.
- Keep old single-node paired restore separate from distributed application rollback. Shared-store disaster recovery is a separate stopped-write exercise with a measured recovery point and cross-store checks.

Every row and scenario requires an actual recorded outcome. Synthetic model responses may make the journey deterministic, but must be labeled and cannot establish real-model quality. Production migration remains blocked until the entire isolated journey and distinct-image rollback pass and the user renews production authorization.
