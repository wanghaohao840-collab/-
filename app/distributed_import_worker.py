"""Bounded isolated import Worker; existing PG leases and publication own writes."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from threading import Event, Lock, Thread
import time

from app.import_document_preparation import ImportDocumentPreparationError, prepare_import_document
from app.import_memory_publication import ImportMemoryPublicationService
from app.import_publication_recovery import (
    PostgresImportPublicationRecoveryRepository, RecoveryOutcome,
)
from app.import_publication_worker_adapter import ImportPublicationWorkerAdapter
from app.isolated_import_provider import IsolatedImportProviders
from app.isolated_import_runtime import IsolatedImportRuntime
from app.postgres_import_artifacts import PostgresImportArtifactService
from app.postgres_import_leases import ImportAttempt, ImportLeaseLost, PostgresImportLeaseRepository
from app.postgres_vector_generations import PostgresVectorGenerationAuthority, VectorScope
from app.vector_generation_service import VectorGenerationService
from hello_agents.memory.rag.index_identity import IndexIdentity


@dataclass(frozen=True)
class WorkerExit:
    clean: bool
    reason: str
    ordinary_attempts: int
    recovery_attempts: int
    active_threads: int


@dataclass
class _Operation:
    thread: Thread | None = None
    abort: Event = field(default_factory=Event)
    result: object = None
    error: Exception | None = None
    deadline: float | None = None
    completed_at: float | None = None


class IsolatedImportWorker:
    def __init__(self, runtime: IsolatedImportRuntime,
                 providers: IsolatedImportProviders | None, *, worker_id: str):
        if (type(runtime) is not IsolatedImportRuntime or runtime.role != "worker"
                or not isinstance(worker_id, str) or not worker_id.strip()
                or len(runtime.settings.trusted_user_ids) != 2):
            raise ValueError("worker_configuration")
        if (providers is not None and
                (type(providers) is not IsolatedImportProviders
                 or providers.settings != runtime.settings
                 or providers.rag_runtime.profile != runtime.settings.rag_profile
                 or providers.episode_runtime.profile != runtime.settings.episode_profile)):
            raise ValueError("worker_provider_identity")
        self.runtime = runtime
        self.providers = providers
        self.worker_id = worker_id
        self.leases = PostgresImportLeaseRepository(runtime.database)
        self.recovery = PostgresImportPublicationRecoveryRepository(runtime.database)
        self._lock = Lock()
        self._ordinary_gate = Lock()
        self._stop = Event()
        self._external_stop: Event | None = None
        self._lanes: dict[str, _Operation] = {}
        self._close_deadline: float | None = None
        self._closed = False
        self._attempt_stage: str | None = None
        self._attempt_stage_deadline: float | None = None
        self._ordinary_count = 0
        self._recovery_count = 0
        self._last_disposition = "none"

    @property
    def recovery_only(self) -> bool:
        return self.providers is None

    def _active(self) -> tuple[Thread, ...]:
        with self._lock:
            lanes = tuple(op.thread for op in self._lanes.values())
        probes = self.providers.active_threads if self.providers is not None else ()
        unique = {id(thread): thread for thread in (*lanes, *probes)
                  if thread is not None and thread.is_alive()}
        return tuple(unique.values())

    def _start(self, lane: str, target, *, deadline: float | None = None,
               prerequisite: _Operation | None = None) -> _Operation | None:
        with self._lock:
            previous = self._lanes.get(lane)
            if (self._stopping_locked() or self._closed
                    or prerequisite is not None and not self._allowed_locked(
                        prerequisite.abort, prerequisite.deadline)
                    or prerequisite is not None and self.providers is not None
                    and self.providers.active_threads
                    or previous is not None and previous.thread is not None
                    and previous.thread.is_alive()):
                return None
            operation = _Operation(deadline=deadline)

            def run() -> None:
                try:
                    operation.result = target(operation.abort)
                except Exception as error:
                    operation.error = error
                finally:
                    operation.completed_at = time.monotonic()

            operation.thread = Thread(target=run, name=f"isolated-import-{lane}", daemon=False)
            self._lanes[lane] = operation
            operation.thread.start()
            return operation

    def _latch_stop_locked(self) -> None:
        if self._close_deadline is None:
            self._close_deadline = time.monotonic() + self.runtime.settings.stop_wait_seconds
        self._stop.set()
        for operation in self._lanes.values():
            operation.abort.set()

    def _stopping_locked(self) -> bool:
        if self._external_stop is not None and self._external_stop.is_set():
            self._latch_stop_locked()
        return self._stop.is_set()

    def _stopping(self) -> bool:
        with self._lock:
            return self._stopping_locked()

    def _allowed_locked(self, abort: Event | None = None,
                        deadline: float | None = None) -> bool:
        return (not self._stopping_locked() and (abort is None or not abort.is_set())
                and (deadline is None or time.monotonic() < deadline))

    def _allowed(self, abort: Event | None = None, deadline: float | None = None) -> bool:
        with self._lock:
            return self._allowed_locked(abort, deadline)

    def _admit(self, abort: Event, deadline: float | None = None) -> bool:
        # Close takes the same short lock. This is the start boundary for one
        # external operation; an admitted call may finish after stop.
        with self._lock:
            return self._allowed_locked(abort, deadline)

    def _observe(self, operation: _Operation) -> bool:
        assert operation.thread is not None
        assert operation.deadline is not None
        deadline = operation.deadline
        while operation.thread.is_alive():
            remaining = deadline - time.monotonic()
            if self._stopping() or remaining <= 0:
                operation.abort.set()
                return False
            operation.thread.join(min(0.05, remaining))
        return (not self._stopping() and operation.completed_at is not None
                and operation.completed_at <= deadline
                and time.monotonic() <= deadline)

    def _preflight(self, abort: Event, deadline: float) -> ImportAttempt | None:
        assert self.providers is not None
        ready: set[str] = set()
        for user_id in sorted(self.runtime.settings.trusted_user_ids):
            if self.providers.active_threads or not self._admit(abort, deadline):
                return None
            if self.runtime.ordinary_ready(user_id):
                ready.add(user_id)
        if (not ready or self.providers.active_threads
                or not self._admit(abort, deadline)):
            return None
        # This claim is the only irreversible preflight action. A late result is
        # retained but never transferred into a forward attempt.
        claimed = self.leases.claim_next(
            self.worker_id, lease_seconds=self.runtime.settings.lease_seconds,
            allowed_user_ids=frozenset(ready))
        if isinstance(claimed, ImportAttempt):
            with self._lock:
                self._ordinary_count += 1
        return claimed

    def run_one_ordinary(self) -> bool:
        if self.providers is None or self._stopping():
            return False
        if not self._ordinary_gate.acquire(blocking=False):
            return False
        try:
            with self._lock:
                busy = any((op := self._lanes.get(name)) is not None
                           and op.thread is not None and op.thread.is_alive()
                           for name in ("attempt", "preflight", "heartbeat"))
            if busy or self.providers.active_threads:
                return False
            preflight_deadline = time.monotonic() + self.runtime.settings.provider_timeout_seconds
            preflight = self._start("preflight", lambda abort:
                                    self._preflight(abort, preflight_deadline),
                                    deadline=preflight_deadline)
            if preflight is None or not self._observe(preflight):
                return False
            if preflight.error is not None:
                return False
            original = preflight.result
            if (not isinstance(original, ImportAttempt) or not self._allowed(
                    preflight.abort, preflight.deadline)
                    or self.providers.active_threads):
                return False
            deadline = time.monotonic() + self.runtime.settings.attempt_seconds
            forward = self._start("attempt", lambda abort:
                                  self._forward(original, abort, deadline),
                                  deadline=deadline, prerequisite=preflight)
            if forward is None:
                return True  # The claimed lease expires; never start a second forward path.
            assert forward.thread is not None
            while forward.thread.is_alive():
                with self._lock:
                    provider_deadline = self._attempt_stage_deadline
                now = time.monotonic()
                if (self._stopping() or forward.abort.is_set() or now >= deadline
                        or provider_deadline is not None and now >= provider_deadline):
                    forward.abort.set()
                    break
                forward.thread.join(min(0.05, deadline - now,
                                        provider_deadline - now if provider_deadline else 0.05))
            return True
        finally:
            self._ordinary_gate.release()

    def _stage(self, name: str, abort: Event, deadline: float, *, provider=False) -> bool:
        with self._lock:
            if not self._allowed_locked(abort, deadline):
                return False
            self._attempt_stage = name
            self._attempt_stage_deadline = (min(
                deadline, time.monotonic() + self.runtime.settings.provider_timeout_seconds)
                if provider else None)
            return True

    def _stage_end(self) -> tuple[bool, float | None]:
        with self._lock:
            stage_deadline = self._attempt_stage_deadline
            timely = (stage_deadline is None or time.monotonic() < stage_deadline)
            if not timely:
                attempt = self._lanes.get("attempt")
                if attempt is not None:
                    attempt.abort.set()
            self._attempt_stage = None
            self._attempt_stage_deadline = None
            return timely, stage_deadline

    def _authority(self, original: ImportAttempt) -> None:
        with self.runtime.database.transaction() as cursor:
            cursor.execute("begin")
            self.leases._live(cursor, original, status="running", ended=False)

    def _scopes(self, user_id: str) -> tuple[VectorScope, VectorScope]:
        settings = self.runtime.settings
        rag = VectorScope(user_id, "rag", f"pdf_{user_id}", IndexIdentity(
            "qdrant", settings.rag_collection, settings.rag_profile))
        episode = VectorScope(user_id, "episode", "episodes", IndexIdentity(
            "qdrant", settings.episode_collection, settings.episode_profile))
        return rag, episode

    def _source(self, original: ImportAttempt, store) -> bytes:
        with self.runtime.database.transaction() as cursor:
            row = cursor.execute("""select bucket,object_key,version_id,sha256,size_bytes
                from import_objects where user_id=%s and task_id=%s""",
                (original.task.user_id, original.task.task_id)).fetchone()
        ref = original.source
        if (row is None or (row["bucket"], row["object_key"], row["version_id"],
                row["sha256"], row["size_bytes"]) !=
                (original.bucket, ref.key, ref.version_id, ref.sha256, ref.size_bytes)):
            raise ImportLeaseLost("original_source_changed")
        return PostgresImportArtifactService(self.runtime.database, store).read_source_bytes(
            original.task.user_id, original.task.task_id)

    def _heartbeat(self, original: ImportAttempt, abort: Event,
                   forward_abort: Event, deadline: float) -> None:
        interval = self.runtime.settings.heartbeat_seconds
        next_beat = time.monotonic() + interval
        while True:
            if forward_abort.is_set() or not self._allowed(abort, deadline):
                return
            remaining = min(next_beat, deadline) - time.monotonic()
            if remaining > 0:
                if abort.wait(min(0.05, remaining)):
                    return
                continue
            with self._lock:
                stage_deadline = self._attempt_stage_deadline
                if (not self._allowed_locked(abort, deadline) or forward_abort.is_set()
                        or stage_deadline is not None
                        and time.monotonic() >= stage_deadline):
                    forward_abort.set()
                    return
            try:
                self.leases.heartbeat(original, lease_seconds=self.runtime.settings.lease_seconds)
            except Exception:
                abort.set()
                forward_abort.set()
                return
            next_beat = time.monotonic() + interval

    def _forward(self, original: ImportAttempt, abort: Event, deadline: float) -> None:
        assert self.providers is not None
        heartbeat = self._start("heartbeat", lambda hb_abort: self._heartbeat(
            original, hb_abort, abort, deadline))
        if heartbeat is None:
            abort.set()
            return
        issue_started = False
        try:
            user_id = original.task.user_id
            if not self._stage("selected_readiness", abort, deadline, provider=True):
                return
            ready = self.runtime.ordinary_ready(user_id)
            self._stage_end()
            if not ready or not self._allowed(abort, deadline):
                return
            if not self._stage("authority", abort, deadline):
                return
            self._authority(original)
            self._stage_end()
            if not self._stage("source", abort, deadline):
                return
            store, raw = self.runtime.storage_clients()
            source = self._source(original, store)
            self._stage_end()
            if not self._stage("authority", abort, deadline):
                return
            self._authority(original)
            self._stage_end()
            rag_scope, episode_scope = self._scopes(user_id)
            if not self._stage("prepare", abort, deadline, provider=True):
                return
            points = prepare_import_document(
                source_bytes=source, task_id=original.task.task_id, user_id=user_id,
                document_id=original.task.document_id,
                original_name=original.task.original_name,
                suffix=original.task.file_suffix, scope=rag_scope,
                embedding_runtime=self.providers.rag_runtime)
            self._stage_end()
            if not self._stage("authority", abort, deadline):
                return
            self._authority(original)
            self._stage_end()
            if not self._stage("episode", abort, deadline, provider=True):
                return
            event_text = f"用户导入了文档：{original.task.original_name}"
            event_vector = self.providers.episode_runtime.embed_query(event_text)
            if (len(event_vector) != self.runtime.settings.episode_profile.dimension
                    or not all(type(value) in (int, float) and math.isfinite(value)
                               for value in event_vector)):
                return
            self._stage_end()
            if not self._stage("authority", abort, deadline):
                return
            self._authority(original)
            self._stage_end()
            if not self._stage("issue", abort, deadline):
                return
            # Stage entry is the ambiguity boundary, including a raise from reservation.
            issue_started = True
            authority = PostgresVectorGenerationAuthority(self.runtime.database)
            vectors = VectorGenerationService(authority, raw)
            publication = ImportMemoryPublicationService(
                self.runtime.database, store, vectors, vectors,
                trusted_episode_scope=episode_scope)
            live = publication._issue_live_publication(
                rag_scope, original, points, event_vector=event_vector,
                event_profile=self.runtime.settings.episode_profile)
            self._stage_end()
            if not self._stage("adapter", abort, deadline):
                return
            disposition = ImportPublicationWorkerAdapter(
                publication, self.recovery, enabled=True).run_once(live)
            self._last_disposition = disposition.status
        except ImportDocumentPreparationError as error:
            preparing = self._attempt_stage == "prepare"
            preparation_timely, stage_deadline = self._stage_end()
            if (preparation_timely and not issue_started
                    and preparing and self._admit(
                        abort, min(deadline, stage_deadline or deadline))):
                try:
                    self.leases.fail(original, error.code, error.code)
                except Exception:
                    pass
        except Exception:
            # Source, provider, issue and adapter uncertainty is held for expiry/proof.
            self._last_disposition = "needs_reconciliation"
        finally:
            self._stage_end()
            abort.set()
            if heartbeat is not None:
                heartbeat.abort.set()

    def _recovery_round(self, abort: Event, deadline: float) -> RecoveryOutcome | None:
        trusted = self.runtime.settings.trusted_user_ids
        if not self._admit(abort, deadline) or not self.runtime.recovery_ready():
            return None
        if not self._admit(abort, deadline):
            return None
        self.leases.recover_expired(limit=100, allowed_user_ids=trusted)
        if not self._admit(abort, deadline):
            return None
        claim = self.recovery.claim_next(
            self.worker_id, lease_seconds=self.runtime.settings.lease_seconds,
            allowed_user_ids=trusted)
        if claim is None or not self._admit(abort, deadline):
            return None
        claim = self.recovery.renew(claim, lease_seconds=self.runtime.settings.lease_seconds)
        if not self._admit(abort, deadline):
            return None
        outcome = self.recovery.prove_or_hold(claim)
        if outcome is not None:
            with self._lock:
                self._recovery_count += 1
        return outcome

    def run_one_recovery(self) -> RecoveryOutcome | None:
        deadline = time.monotonic() + self.runtime.settings.provider_timeout_seconds
        operation = self._start("recovery", lambda abort:
                                self._recovery_round(abort, deadline), deadline=deadline)
        if operation is None or not self._observe(operation):
            return None
        if operation.error is not None:
            return None
        return operation.result

    def run_forever(self, stop_event: Event) -> WorkerExit:
        with self._lock:
            self._external_stop = stop_event
        while not self._stopping():
            self.run_one_recovery()
            if self._stopping():
                break
            self.run_one_ordinary()
            stop_event.wait(0.1)
        return self.close()

    def _cleanup(self, _abort: Event) -> None:
        if self.providers is not None:
            # Mark closed before the borrower snapshot so a late caller cannot
            # open a new probe between observation and runtime closure.
            self.providers.close(timeout_seconds=0)
        while True:
            with self._lock:
                borrowers = tuple(op.thread for name, op in self._lanes.items()
                                  if name != "cleanup")
            probes = self.providers.active_threads if self.providers is not None else ()
            if not any(thread is not None and thread.is_alive()
                       for thread in (*borrowers, *probes)):
                break
            time.sleep(0.02)
        if self.providers is not None and not self.providers.close(timeout_seconds=0):
            raise RuntimeError("provider_close_active")
        self.runtime.close()
        with self._lock:
            self._closed = True

    def close(self) -> WorkerExit:
        with self._lock:
            self._latch_stop_locked()
            deadline = self._close_deadline
        with self._lock:
            cleanup = self._lanes.get("cleanup")
        if cleanup is None:
            # _start refuses once stop is set; cleanup is the single exception.
            cleanup = _Operation()

            def run_cleanup() -> None:
                try:
                    self._cleanup(cleanup.abort)
                except Exception as error:
                    cleanup.error = error

            with self._lock:
                if "cleanup" not in self._lanes:
                    cleanup.thread = Thread(target=run_cleanup,
                                            name="isolated-import-cleanup", daemon=False)
                    self._lanes["cleanup"] = cleanup
                    cleanup.thread.start()
                else:
                    cleanup = self._lanes["cleanup"]
        assert cleanup.thread is not None
        cleanup.thread.join(max(0.0, deadline - time.monotonic()))
        active = len(self._active())
        clean = self._closed and cleanup.error is None and active == 0
        with self._lock:
            ordinary, recovery = self._ordinary_count, self._recovery_count
        return WorkerExit(clean, ("recovery_only" if clean and self.recovery_only
                                  else "stopped" if clean else "active_work"),
                          ordinary, recovery, active)
