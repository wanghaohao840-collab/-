"""Authenticated, tenant-scoped isolated import admission and published reads."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import PurePath

from app.distributed_import_sessions import (
    DistributedImportSessions, SessionLockUnavailable, UntrustedSessionError,
)
from app.import_models import ImportBatchSummary
from app.import_publication_evidence import (
    PublicationEvidenceError, UnresolvedPublicationGate,
)
from app.import_repository import PostgresImportTaskRepository
from app.import_uploads import ImportLimitError, ImportUpload, validate_suffix
from app.isolated_import_runtime import (
    IsolatedImportConfigurationError, IsolatedImportRuntime,
)
from app.object_store import ObjectRef
from app.postgres_document_objects import PostgresDocumentObjectRepository, _identity
from app.postgres_history_document_witnesses import (
    PostgresHistoryDocumentWitnessRepository, document_evidence,
)
from app.postgres_import_artifacts import InvalidImportUpload, PostgresImportArtifactService
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.session import InvalidCsrfTokenError, InvalidSessionError
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.index_identity import IndexIdentity


class DistributedImportError(RuntimeError):
    """Only stable public code, status, and retry advice cross this facade."""

    def __init__(self, code: str, status_code: int, retryable: bool):
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True)
class PublishedImportDocument:
    document_id: str
    filename: str
    suffix: str
    size_bytes: int
    content: bytes = field(repr=False)


class _DocumentMissing(Exception):
    pass


_READBACK_SQL = """with requested as (
    select %s::text as user_id,%s::text as namespace,
           %s::text as index_key,%s::text as document_id)
select
    (select to_jsonb(u) from users u where u.id=r.user_id) as account,
    (select to_jsonb(i) from vector_indexes i where i.tenant_id=r.user_id
       and i.vector_kind='rag' and i.namespace=r.namespace
       and i.index_key=r.index_key) as rag_index,
    (select to_jsonb(h) from vector_heads h where h.tenant_id=r.user_id
       and h.vector_kind='rag' and h.namespace=r.namespace
       and h.index_key=r.index_key) as rag_head,
    (select to_jsonb(g) from vector_generations g join vector_heads h
       on h.tenant_id=g.tenant_id and h.vector_kind=g.vector_kind
       and h.namespace=g.namespace and h.index_key=g.index_key
       and h.last_generation_id=g.generation_id
       where h.tenant_id=r.user_id and h.vector_kind='rag'
       and h.namespace=r.namespace and h.index_key=r.index_key) as rag_receipt,
    (select to_jsonb(w) from history_document_witnesses w join vector_heads h
       on h.tenant_id=w.tenant_id and h.vector_kind=w.vector_kind
       and h.namespace=w.namespace and h.index_key=w.index_key
       and h.revision=w.head_revision
       where h.tenant_id=r.user_id and h.vector_kind='rag'
       and h.namespace=r.namespace and h.index_key=r.index_key) as witness,
    (select to_jsonb(s) from user_snapshots s where s.user_id=r.user_id
       and s.kind='history') as history,
    (select coalesce(jsonb_agg(to_jsonb(o) order by o.document_id), '[]'::jsonb)
       from document_objects o where o.user_id=r.user_id) as references,
    (select coalesce(jsonb_agg(to_jsonb(f) order by f.id), '[]'::jsonb)
       from qa_deletion_fences f where f.user_id=r.user_id
       and f.target_type='document' and f.target_id=r.document_id) as fences
from requested r"""


def _unavailable() -> DistributedImportError:
    return DistributedImportError("dependency_unavailable", 503, True)


def _effective_fence(rows: list[dict]) -> bool:
    return any(row["status"] in ("queued", "running") or
               (row["status"] == "failed" and row["attempt_count"] < 3)
               for row in rows)


def _record(row: dict, user_id: str, document_id: str) -> tuple[dict, object]:
    account = row["account"]
    if account is None or account["status"] != "active":
        raise _unavailable()
    history = row["history"]
    if history is None or history["user_id"] != user_id or history["kind"] != "history":
        raise _unavailable()
    evidence = document_evidence(user_id, history["payload"])
    selected = [item for item in history["payload"]["documents"]
                if item["document_id"] == document_id]
    if not selected or _effective_fence(row["fences"]):
        raise _DocumentMissing()
    if len(selected) != 1:
        raise _unavailable()
    return selected[0], evidence


def _match_pairing(row: dict, scope: VectorScope, pairing, evidence) -> None:
    head, index, receipt, witness = (row["rag_head"], row["rag_index"],
                                      row["rag_receipt"], row["witness"])
    if (not head or not index or not receipt or not witness
            or pairing.expected_count <= 0 or pairing.head.state != "published"
            or index["identity"] != scope.identity.to_dict()
            or index["index_revision"] != pairing.head.index_revision
            or (head["revision"], head["generation_id"], head["last_generation_id"],
                head["index_revision"], head["snapshot_version"]) !=
               (pairing.head.revision, str(pairing.head.generation_id),
                str(pairing.last_generation_id), pairing.head.index_revision,
                pairing.head.snapshot_version)
            or (receipt["generation_id"], receipt["state"], receipt["expected_count"],
                receipt["content_digest"], receipt["publication_revision"],
                receipt["index_revision"], receipt["publication_snapshot_version"]) !=
               (str(pairing.last_generation_id), "published", pairing.expected_count,
                pairing.content_digest, pairing.receipt_revision,
                pairing.receipt_index_revision, pairing.receipt_snapshot_version)
            or receipt["published_at"] is None
            or (witness["head_revision"], witness["last_generation_id"],
                witness["document_count"], witness["documents_sha256"],
                witness["index_revision"], witness["publication_snapshot_version"]) !=
               (pairing.head.revision, str(pairing.last_generation_id),
                pairing.witness_count, pairing.witness_digest,
                pairing.receipt_index_revision, pairing.receipt_snapshot_version)
            or not pairing.has_witness):
        raise _unavailable()
    PostgresHistoryDocumentWitnessRepository.check_document_pairing(
        pairing, evidence, row["history"]["version"])


def _references(row: dict, evidence, store, user_id: str,
                document_id: str, record: dict) -> ObjectRef:
    refs = row["references"]
    if len(refs) != evidence.count or {item["document_id"] for item in refs} != evidence.ids:
        raise _unavailable()
    hashes = dict(evidence.records)
    for item in refs:
        if (item["user_id"] != user_id or item["bucket"] != store.bucket
                or item["history_record_sha256"] != hashes[item["document_id"]]):
            raise _unavailable()
    chosen = next(item for item in refs if item["document_id"] == document_id)
    if chosen["history_record_sha256"] != PostgresDocumentObjectRepository._record_hash(record):
        raise _unavailable()
    ref = ObjectRef(chosen["object_key"], chosen["sha256"], chosen["size_bytes"],
                    chosen["version_id"])
    _identity(user_id, document_id, ref)
    return ref


def _filename(record: dict) -> tuple[str, str]:
    raw = record.get("document_name") or record.get("original_name")
    if not isinstance(raw, str) or not raw or any(ord(char) < 32 for char in raw):
        raise _unavailable()
    name = raw.replace("\\", "/").rsplit("/", 1)[-1]
    if not name or name in (".", ".."):
        raise _unavailable()
    suffix = record.get("file_suffix") or PurePath(name).suffix
    if not isinstance(suffix, str) or validate_suffix(suffix) != suffix.lower():
        raise _unavailable()
    return name, suffix.lower()


class DistributedImportService:
    def __init__(self, runtime: IsolatedImportRuntime,
                 sessions: DistributedImportSessions):
        if (not isinstance(runtime, IsolatedImportRuntime) or runtime.role != "api"
                or not isinstance(sessions, DistributedImportSessions)
                or sessions.runtime is not runtime
                or sessions.repository.database is not runtime.database):
            raise ValueError("isolated_api_composition_required")
        self.runtime = runtime
        self.sessions = sessions
        self.repository = PostgresImportTaskRepository(runtime.database)

    def _session(self, token: str | None, csrf_token: str | None = None,
                 *, mutation: bool = False):
        try:
            return (self.sessions.validate_csrf(token, csrf_token) if mutation
                    else self.sessions.get_session(token))
        except InvalidSessionError:
            raise DistributedImportError("invalid_session", 401, False) from None
        except (InvalidCsrfTokenError, UntrustedSessionError):
            raise DistributedImportError("forbidden", 403, False) from None
        except Exception:
            raise _unavailable() from None

    def _gate_current(self, user_id: str) -> bool:
        with self.runtime.database.transaction() as cursor:
            return cursor.execute("""select 1 from user_publication_gates
                where user_id=%s and status='unresolved' limit 1""",
                (user_id,)).fetchone() is not None

    def submit_uploads(self, token: str | None, csrf_token: str | None,
                       uploads: list[ImportUpload]) -> ImportBatchSummary:
        session = self._session(token, csrf_token, mutation=True)
        stamp = None
        try:
            stamp = self.runtime.prepare_admission(session.user_id)
            if stamp is None:
                raise _unavailable()
            store, _ = self.runtime.storage_clients()
            artifacts = PostgresImportArtifactService(self.runtime.database, store)

            def recheck(cursor, user_id):
                self.sessions.recheck_csrf_in_transaction(
                    cursor, token, csrf_token, expected_user_id=session.user_id)
                self.runtime.recheck_admission(cursor, user_id, stamp)

            try:
                return artifacts.submit_uploads(session.user_id, uploads,
                                                admission_recheck=recheck)
            except UnresolvedPublicationGate:
                # Artifact transaction has rolled back and released its user lock.
                try:
                    if self._gate_current(session.user_id):
                        raise DistributedImportError("needs_reconciliation", 409, False)
                except DistributedImportError:
                    raise
                except Exception:
                    pass
                raise _unavailable() from None
        except DistributedImportError:
            raise
        except InvalidSessionError:
            raise DistributedImportError("invalid_session", 401, False) from None
        except (InvalidCsrfTokenError, UntrustedSessionError):
            raise DistributedImportError("forbidden", 403, False) from None
        except ImportLimitError as error:
            raise DistributedImportError(error.code, error.status_code, False) from None
        except IsolatedImportConfigurationError:
            raise _unavailable() from None
        except InvalidImportUpload:
            raise DistributedImportError("invalid_upload", 400, False) from None
        except PublicationEvidenceError:
            raise _unavailable() from None
        except ValueError:
            raise _unavailable() from None
        except (FileNotFoundError, SessionLockUnavailable):
            raise _unavailable() from None
        except Exception:
            raise _unavailable() from None
        finally:
            if stamp is not None:
                self.runtime.discard_admission(stamp)

    def list_batches(self, token: str | None, *, limit: int = 50):
        session = self._session(token)
        try:
            return self.repository.list_batches(session.user_id, limit=limit)
        except Exception:
            raise _unavailable() from None

    def get_batch(self, token: str | None, batch_id: str):
        session = self._session(token)
        try:
            result = self.repository.get_batch(session.user_id, batch_id)
        except Exception:
            raise _unavailable() from None
        if result is None:
            raise DistributedImportError("not_found", 404, False)
        return result

    def get_task(self, token: str | None, task_id: str):
        session = self._session(token)
        try:
            result = self.repository.get_task(session.user_id, task_id)
        except Exception:
            raise _unavailable() from None
        if result is None:
            raise DistributedImportError("not_found", 404, False)
        return result

    def _capture_readback(self, scope: VectorScope, document_id: str) -> dict:
        with self.runtime.database.transaction() as cursor:
            return cursor.execute(_READBACK_SQL, (scope.tenant_id, scope.namespace,
                scope.index_key, document_id)).fetchone()

    def read_published_document(self, token: str | None,
                                document_id: str) -> PublishedImportDocument:
        session = self._session(token)
        try:
            store, raw = self.runtime.storage_clients()
            identity = IndexIdentity("qdrant", self.runtime.settings.rag_collection,
                                     self.runtime.settings.rag_profile)
            scope = VectorScope(session.user_id, "rag", f"pdf_{session.user_id}", identity)
            witness = PostgresHistoryDocumentWitnessRepository(self.runtime.database, store)
            vectors = VectorGenerationService(PostgresVectorGenerationAuthority(
                self.runtime.database), raw)
            for attempt in range(2):
                before = self._capture_readback(scope, document_id)
                try:
                    record, evidence = _record(before, session.user_id, document_id)
                except _DocumentMissing:
                    if attempt == 0:
                        raise DistributedImportError("not_found", 404, False) from None
                    raise _unavailable() from None
                pairing = witness.read_current(scope)
                _match_pairing(before, scope, pairing, evidence)
                ref = _references(before, evidence, store, session.user_id,
                                  document_id, record)
                retained = witness.verify_retained_references(scope, evidence)
                fixed = tuple((item["document_id"], item["bucket"], item["object_key"],
                               item["version_id"], item["sha256"], item["size_bytes"],
                               item["history_record_sha256"]) for item in before["references"])
                if set(retained) != set(fixed):
                    raise _unavailable()
                content = store.read_verified(session.user_id, ref)
                if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != ref.sha256:
                    raise _unavailable()
                view = vectors.read_view(scope)
                if view.head != pairing.head:
                    raise _unavailable()
                points = view.scroll(identity.physical_collection,
                    expected_manifest=(pairing.expected_count, pairing.content_digest))
                if not any(point.payload.get("document_id") == document_id and
                           point.payload.get("rag_namespace") == scope.namespace
                           for point in points):
                    raise _unavailable()
                after = self._capture_readback(scope, document_id)
                if before != after:
                    continue
                final_record, final_evidence = _record(after, session.user_id, document_id)
                _match_pairing(after, scope, pairing, final_evidence)
                final_ref = _references(after, final_evidence, store, session.user_id,
                                        document_id, final_record)
                if final_ref != ref or final_record != record:
                    raise _unavailable()
                name, suffix = _filename(final_record)
                return PublishedImportDocument(document_id, name, suffix,
                                               final_ref.size_bytes, content)
            raise _unavailable()
        except DistributedImportError:
            raise
        except Exception:
            raise _unavailable() from None
