"""Trusted tenant selection against disposable PostgreSQL, S3 and Qdrant."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
from threading import Event
import time
from uuid import uuid4

import pytest

from app.history import EMPTY_HISTORY
from app.import_memory_publication import ImportMemoryPublicationService
from app.import_publication_recovery import (
    PostgresImportPublicationRecoveryRepository, RecoveryLeaseLost,
)
from app.postgres_coordination import PostgresUserMutationCoordinator
from app.postgres_import_leases import PostgresImportLeaseRepository
from app.postgres_import_leases import ImportLeaseLost
from app.postgres_vector_generations import VectorScope
from tests.integration.test_import_document_publication import _point, _task
from tests.integration.test_import_document_publication import publication
from tests.integration.test_import_memory_publication import memory_publication
from tests.integration.test_import_publication_recovery import (
    publish_episode_baseline,
)
from tests.integration.test_postgres_import_leases import fixture, submit
from tests.integration.test_postgres_auth_sessions import shared_database
from tests.integration.test_s3_object_store import store


def _snapshot(cursor, user):
    return {
        table: cursor.execute(f"select * from {table} where user_id=%s order by 1",
                              (user,)).fetchall()
        for table in ("import_tasks", "import_task_attempts", "user_mutation_leases",
                      "import_user_schedule", "import_publication_recovery_queue",
                      "import_publication_recovery_schedule",
                      "import_publication_recovery_leases",
                      "import_publication_recovery_token_issuance",
                      "user_publication_gates")
    }


def test_filtered_ordinary_claim_skips_earlier_disallowed_user(fixture):
    db, _, _, first, second, _ = fixture
    excluded, allowed = sorted((first, second))
    submit(fixture, excluded)
    submit(fixture, allowed)
    with db.transaction() as cursor:
        before = _snapshot(cursor, excluded)
    repo = PostgresImportLeaseRepository(db)
    claimed = repo.claim_next("filtered", allowed_user_ids=frozenset({allowed}))
    assert claimed is not None and claimed.task.user_id == allowed
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before
        task = cursor.execute("select * from import_tasks where id=%s",
                              (claimed.task.task_id,)).fetchone()
        assert task["status"] == "running" and task["user_id"] == allowed
        assert cursor.execute("select count(*) as n from import_task_attempts where user_id=%s",
                              (allowed,)).fetchone()["n"] == 1
    assert repo.claim_next("none", allowed_user_ids=frozenset({allowed})) is None
    assert repo.claim_next("legacy", allowed_user_ids=None).task.user_id == excluded


def test_filtered_expiry_skips_disallowed_running_task(fixture):
    db, _, _, first, second, _ = fixture
    excluded, allowed = sorted((first, second))
    submit(fixture, excluded)
    submit(fixture, allowed)
    repo = PostgresImportLeaseRepository(db)
    first_claim = repo.claim_next("first")
    second_claim = repo.claim_next("second")
    assert {first_claim.task.user_id, second_claim.task.user_id} == {excluded, allowed}
    with db.transaction() as cursor:
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second'")
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second'")
        before = _snapshot(cursor, excluded)
    assert repo.recover_expired(100, allowed_user_ids=frozenset({allowed})) == 1
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before
        task = cursor.execute("select status from import_tasks where user_id=%s",
                              (allowed,)).fetchone()
        assert task["status"] == "queued"
        audit = cursor.execute("select end_reason from import_task_attempts where user_id=%s",
                               (allowed,)).fetchone()
        assert audit["end_reason"] == "lease_expired"
    assert repo.recover_expired(100, allowed_user_ids=frozenset({allowed})) == 0
    assert repo.recover_expired(100) == 1


def test_filtered_ordinary_skips_busy_allowed_user_and_keeps_fairness(fixture):
    db, other_pool, _, first, second, _ = fixture
    excluded = str(uuid4())
    with db.transaction() as cursor:
        cursor.execute("insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                       (excluded, excluded, excluded, "hash", "active", "now", "now"))
    for user in (excluded, first, second):
        submit(fixture, user)
    allowed = frozenset({first, second})
    with db.transaction() as cursor:
        before = _snapshot(cursor, excluded)
    with db.transaction() as cursor:
        cursor.execute("select id from users where id=%s for update", (min(first, second),))
        claim = PostgresImportLeaseRepository(other_pool).claim_next(
            "other", allowed_user_ids=allowed)
        assert claim is not None and claim.task.user_id == max(first, second)
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before


class DriftCursor:
    """Present a stale task result while verifying the exact SQL lock pair."""

    def __init__(self, real, candidate_id, candidate_user, other_user, mode):
        self.real = real
        self.connection = real.connection
        self.candidate_id = candidate_id
        self.candidate_user = candidate_user
        self.other_user = other_user
        self.mode = mode
        self.after_read = False
        self.saw_lock = False
        self.saw_reread = False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.lower().split())
        if ("from import_tasks" in normalized and "for update" in normalized
                and "where id=%s" in normalized and "select id" in normalized):
            assert "and user_id=%s" in normalized
            assert params[:2] == (self.candidate_id, self.candidate_user)
            self.saw_lock = True
        if ("from import_tasks" in normalized and "select t.*" in normalized
                and "where t.id=%s" in normalized):
            assert "and t.user_id=%s" in normalized
            assert params[:2] == (self.candidate_id, self.candidate_user)
            assert self.saw_lock
            self.saw_reread = True
            self.after_read = True
        elif ("from import_tasks" in normalized and "select *" in normalized
              and "where id=%s" in normalized and "status='running'" in normalized):
            assert "and user_id=%s" in normalized
            assert params[:2] == (self.candidate_id, self.candidate_user)
            assert self.saw_lock
            self.saw_reread = True
            self.after_read = True
        self.real.execute(sql, params)
        return self

    def fetchone(self):
        row = self.real.fetchone()
        if self.after_read:
            self.after_read = False
            if self.mode == "missing":
                return None
            return {**row, "user_id": self.other_user}
        return row

    def __getattr__(self, name):
        return getattr(self.real, name)


class DriftDatabase:
    def __init__(self, real, candidate_id, candidate_user, other_user, mode):
        self.real = real
        self.pair = (candidate_id, candidate_user, other_user, mode)
        self.cursors = []

    @contextmanager
    def transaction(self):
        with self.real.transaction() as cursor:
            wrapped = DriftCursor(cursor, *self.pair)
            self.cursors.append(wrapped)
            yield wrapped


@pytest.mark.parametrize("mode", ["missing", "other_allowed"])
def test_filtered_ordinary_rechecks_scanned_pair_after_lease(fixture, mode):
    db, _, _, first, second, _ = fixture
    submitted = submit(fixture, first)
    task_id = submitted.tasks[0].task_id
    drift = DriftDatabase(db, task_id, first, second, mode)
    with db.transaction() as cursor:
        before = _snapshot(cursor, first)
    with pytest.raises(ImportLeaseLost, match="eligibility changed"):
        PostgresImportLeaseRepository(drift).claim_next(
            "drift", allowed_user_ids=frozenset({first, second}))
    assert any(cursor.saw_lock and cursor.saw_reread for cursor in drift.cursors)
    with db.transaction() as cursor:
        assert _snapshot(cursor, first) == before


@pytest.mark.parametrize("mode", ["missing", "other_allowed"])
def test_filtered_expiry_rechecks_scanned_pair_without_writes(fixture, mode):
    db, _, _, first, second, _ = fixture
    submit(fixture, first)
    attempt = PostgresImportLeaseRepository(db).claim_next("running")
    with db.transaction() as cursor:
        cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s",
                       (attempt.task.task_id,))
        cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s",
                       (first,))
        before = _snapshot(cursor, first)
    drift = DriftDatabase(db, attempt.task.task_id, first, second, mode)
    assert PostgresImportLeaseRepository(drift).recover_expired(
        allowed_user_ids=frozenset({first, second})) == 0
    assert any(cursor.saw_lock and cursor.saw_reread for cursor in drift.cursors)
    with db.transaction() as cursor:
        assert _snapshot(cursor, first) == before


class TaskLockWaitDatabase:
    def __init__(self, real, task_id, user_id, entered):
        self.real = real
        self.task_id = task_id
        self.user_id = user_id
        self.entered = entered
        self.entered_at = None
        self.backend_pid = None

    @contextmanager
    def transaction(self):
        with self.real.transaction() as cursor:
            owner = self

            class ObservedCursor:
                connection = cursor.connection

                def execute(self, sql, params=None):
                    normalized = " ".join(sql.lower().split())
                    if ("select id from import_tasks where id=%s and user_id=%s for update"
                            in normalized and params == (owner.task_id, owner.user_id)):
                        observed = cursor.execute("""select pg_backend_pid() as pid,
                            clock_timestamp() as now""").fetchone()
                        owner.backend_pid = observed["pid"]
                        owner.entered_at = observed["now"]
                        owner.entered.set()
                    cursor.execute(sql, params)
                    return self

                def fetchone(self):
                    return cursor.fetchone()

                def fetchall(self):
                    return cursor.fetchall()

                def __getattr__(self, name):
                    return getattr(cursor, name)

            yield ObservedCursor()


def test_filtered_expiry_uses_fresh_clock_after_task_lock_wait(fixture):
    db, other_pool, _, first, second, _ = fixture
    excluded, allowed = sorted((first, second))
    submit(fixture, excluded)
    submit(fixture, allowed)
    repo = PostgresImportLeaseRepository(db)
    assert {repo.claim_next("first").task.user_id,
            repo.claim_next("second").task.user_id} == {excluded, allowed}
    with db.transaction() as cursor:
        cursor.execute("""update import_tasks set
            lease_expires_at=clock_timestamp()+interval '5 seconds'
            where user_id=%s""", (allowed,))
        cursor.execute("""update user_mutation_leases set
            lease_expires_at=clock_timestamp()+interval '5 seconds'
            where user_id=%s""", (allowed,))
        before = _snapshot(cursor, excluded)
        task_id = cursor.execute("select id from import_tasks where user_id=%s",
                                 (allowed,)).fetchone()["id"]
    entered = Event()
    observed = TaskLockWaitDatabase(other_pool, task_id, allowed, entered)
    with ThreadPoolExecutor(max_workers=1) as worker:
        with db.transaction() as holder:
            holder.execute("select id from import_tasks where id=%s for update", (task_id,))
            waiting = worker.submit(PostgresImportLeaseRepository(observed).recover_expired,
                                    allowed_user_ids=frozenset({allowed}))
            assert entered.wait(timeout=15), "Filtered expiry did not reach task lock"
            task_expiry = holder.execute("select lease_expires_at from import_tasks where id=%s",
                                         (task_id,)).fetchone()["lease_expires_at"]
            user_expiry = holder.execute("select lease_expires_at from user_mutation_leases where user_id=%s",
                                         (allowed,)).fetchone()["lease_expires_at"]
            assert observed.entered_at < task_expiry
            assert observed.entered_at < user_expiry
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                wait_state = holder.execute("""select wait_event_type from pg_stat_activity
                    where pid=%s""", (observed.backend_pid,)).fetchone()
                if wait_state is not None and wait_state["wait_event_type"] == "Lock":
                    break
                assert not waiting.done(), "Expiry did not wait on task row"
                time.sleep(.05)
            else:
                pytest.fail("Filtered expiry backend never entered a task lock wait")
            while time.monotonic() < deadline:
                released_at = holder.execute("select clock_timestamp() as now").fetchone()["now"]
                if released_at > task_expiry and released_at > user_expiry:
                    break
                assert not waiting.done(), "Expiry returned before task lock release"
                time.sleep(.05)
            else:
                pytest.fail("Database clock did not cross both lease expiries")
            assert not waiting.done(), "Expiry did not wait on the task lock"
            assert released_at > task_expiry and released_at > user_expiry
        assert waiting.result(timeout=15) == 1
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before
        audit = cursor.execute("select end_reason,ended_at from import_task_attempts where user_id=%s",
                               (allowed,)).fetchone()
        assert audit["end_reason"] == "lease_expired"
        assert audit["ended_at"] >= released_at


def _two_publications(memory_publication, extra_user=None):
    service, db, store, rag, snapshots, rag_scope, episode_scope, profile, user, other = memory_publication
    coordinator = PostgresUserMutationCoordinator(db)
    if extra_user is not None:
        with db.transaction() as cursor:
            cursor.execute("insert into users values (%s,%s,%s,%s,%s,%s,%s)",
                           (extra_user, extra_user, extra_user, "hash", "active", "now", "now"))

    def prepare(owner):
        owner_rag = VectorScope(owner, "rag", "pdf_" + owner, rag_scope.identity)
        owner_episode = VectorScope(owner, "episode", "episodes", episode_scope.identity)
        bootstrap = coordinator.acquire(owner, "bootstrap-other", lease_seconds=120)
        assert bootstrap is not None

        def history(cursor):
            snapshots.compare_and_swap_in_transaction(cursor, owner, "history",
                dict(EMPTY_HISTORY), expected_version=0)

        rag.publish_complete(owner_rag, bootstrap, rag.authority.read_head(owner_rag), [],
                             domain_publish=history, snapshot_version=1)
        assert coordinator.release(bootstrap)
        publish_episode_baseline(db, service.pair.episode, owner_episode, [])
        owner_service = ImportMemoryPublicationService(db, store, rag, service.pair.episode,
                                                        trusted_episode_scope=owner_episode)
        return owner_service, owner_rag

    prepared = {user: (service, rag_scope), other: prepare(other)}
    if extra_user is not None:
        prepared[extra_user] = prepare(extra_user)
    issued = {}
    for owner, (publishing, scope) in prepared.items():
        attempt = _task(db, store, owner)
        live = publishing._issue_live_publication(scope, attempt,
            [_point(scope, attempt.task.document_id, "tenant-" + owner)],
            event_vector=[1., 0., 0., 0.], event_profile=profile)
        assert live is not None
        key = (owner, attempt.task.task_id, attempt.lease_version)
        with db.transaction() as cursor:
            cursor.execute("""update import_publication_recovery_queue set
                due_at=clock_timestamp(),reason_code='unknown',queue_version=queue_version+1
                where user_id=%s and task_id=%s and task_lease_version=%s""", key)
            cursor.execute("update import_tasks set lease_expires_at=clock_timestamp()-interval '1 second' where id=%s", (key[1],))
            cursor.execute("update user_mutation_leases set lease_expires_at=clock_timestamp()-interval '1 second' where user_id=%s", (owner,))
        issued[owner] = key
    return db, issued


@pytest.mark.parametrize("excluded_state", ["pending", "expired_claimed"])
def test_filtered_recovery_preserves_excluded_queue_and_authority(
        memory_publication, excluded_state):
    db, issued = _two_publications(memory_publication)
    excluded, allowed = sorted(issued)
    if excluded_state == "expired_claimed":
        seeded = PostgresImportPublicationRecoveryRepository(db).claim_next(
            "seed-excluded", lease_seconds=1)
        assert seeded is not None and seeded.user_id == excluded
        time.sleep(1.2)
    with db.transaction() as cursor:
        before = _snapshot(cursor, excluded)
    repo = PostgresImportPublicationRecoveryRepository(db)
    claim = repo.claim_next("filtered", allowed_user_ids=frozenset({allowed}))
    assert claim is not None and claim.user_id == allowed
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before
        queue = cursor.execute("select state from import_publication_recovery_queue where user_id=%s",
                               (allowed,)).fetchone()
        assert queue["state"] == "claimed"
        assert cursor.execute("select count(*) as n from import_publication_recovery_token_issuance where user_id=%s",
                              (allowed,)).fetchone()["n"] == 2
    assert repo.claim_next("none", allowed_user_ids=frozenset({allowed})) is None


def test_filtered_recovery_capture_commits_before_user_wait_and_stale_capture_loses(
        memory_publication):
    db, issued = _two_publications(memory_publication)
    excluded, allowed = sorted(issued)
    repo = PostgresImportPublicationRecoveryRepository(db)
    with ThreadPoolExecutor(max_workers=2) as workers:
        with db.transaction() as cursor:
            cursor.execute("select id from users where id=%s for update", (allowed,))
            waiting = workers.submit(repo.claim_next, "blocked", lease_seconds=1,
                                     allowed_user_ids=frozenset({allowed}))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                captured = cursor.execute("""select state,claim_token,queue_version from
                    import_publication_recovery_queue where user_id=%s and task_id=%s
                    and task_lease_version=%s""", issued[allowed]).fetchone()
                if captured["state"] == "claimed":
                    break
                assert not waiting.done()
                time.sleep(.05)
            else:
                pytest.fail("Filtered queue capture did not commit before user wait")
            with db.transaction() as check:
                excluded_queue = check.execute("select state from import_publication_recovery_queue where user_id=%s",
                                               (excluded,)).fetchone()
                assert excluded_queue["state"] == "pending"
            time.sleep(1.2)
            replacement = workers.submit(repo.claim_next, "replacement", lease_seconds=30,
                                         allowed_user_ids=frozenset({allowed}))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                current = cursor.execute("""select claim_token,queue_version from
                    import_publication_recovery_queue where user_id=%s and task_id=%s
                    and task_lease_version=%s""", issued[allowed]).fetchone()
                if current["claim_token"] != captured["claim_token"]:
                    break
                assert not replacement.done()
                time.sleep(.05)
            else:
                pytest.fail("Filtered replacement capture did not commit")
        with pytest.raises(RecoveryLeaseLost, match="expired during authority wait"):
            waiting.result(timeout=15)
        winner = replacement.result(timeout=15)
        assert winner is not None and winner.user_id == allowed


def test_filtered_recovery_rotates_between_allowed_due_users(memory_publication):
    excluded = "00000000-0000-4000-8000-000000000001"
    db, issued = _two_publications(memory_publication, extra_user=excluded)
    allowed = frozenset(set(issued) - {excluded})
    with db.transaction() as cursor:
        before = _snapshot(cursor, excluded)
    repo = PostgresImportPublicationRecoveryRepository(db)
    first = repo.claim_next("first", lease_seconds=1, allowed_user_ids=allowed)
    assert first is not None and first.user_id in allowed
    time.sleep(1.2)
    second = repo.claim_next("second", lease_seconds=30, allowed_user_ids=allowed)
    assert second is not None and second.user_id in allowed
    assert second.user_id != first.user_id
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before


def test_filtered_recovery_advances_second_allowed_while_first_waits(memory_publication):
    excluded = "00000000-0000-4000-8000-000000000002"
    db, issued = _two_publications(memory_publication, extra_user=excluded)
    first, second = sorted(set(issued) - {excluded})
    allowed = frozenset({first, second})
    with db.transaction() as cursor:
        before = _snapshot(cursor, excluded)
    repo = PostgresImportPublicationRecoveryRepository(db)
    with ThreadPoolExecutor(max_workers=1) as worker:
        with db.transaction() as holder:
            holder.execute("select id from users where id=%s for update", (first,))
            waiting = worker.submit(repo.claim_next, "first", lease_seconds=30,
                                    allowed_user_ids=allowed)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                captured = holder.execute("""select state,claim_token from
                    import_publication_recovery_queue where user_id=%s and task_id=%s
                    and task_lease_version=%s""", issued[first]).fetchone()
                if captured["state"] == "claimed" and captured["claim_token"] is not None:
                    break
                assert not waiting.done(), "First claim ended before capture was visible"
                time.sleep(.05)
            else:
                pytest.fail("First allowed queue capture was not externally visible")
            assert not waiting.done(), "First claim did not wait for user authority"
            second_claim = repo.claim_next("second", lease_seconds=30,
                                           allowed_user_ids=allowed)
            assert second_claim is not None and second_claim.user_id == second
            assert not waiting.done(), "First claim completed while user lock held"
        first_claim = waiting.result(timeout=15)
    assert first_claim is not None and first_claim.user_id == first
    with db.transaction() as cursor:
        assert _snapshot(cursor, excluded) == before
