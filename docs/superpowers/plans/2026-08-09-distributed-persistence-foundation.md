# Distributed Persistence Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the validated distributed runtime configuration, PostgreSQL connection layer, versioned PostgreSQL schema, and local integration deployment needed before any production repository is switched away from SQLite.

**Architecture:** Existing SQLite repositories and local runtime behavior remain the default and do not change in this slice. A strict `local|distributed` configuration model describes process role and shared dependencies; runtime PostgreSQL access uses `psycopg_pool`, while Alembic owns explicit schema upgrades. Docker Compose exposes PostgreSQL only through the `distributed` profile, and opt-in integration tests verify a real migrated database.

**Tech Stack:** Python 3.11/3.12, psycopg 3, psycopg-pool, Alembic, SQLAlchemy 2, PostgreSQL 17, Docker Compose, pytest 8.4.1.

## Program Decomposition

This is slice 1 of the approved distributed-system program. Later slices are deliberately separate reviewer gates and will be planned immediately before execution in this dependency order: PostgreSQL application repositories and persistent sessions; S3/MinIO object storage and distributed import submission; leased independent Workers; PostgreSQL History/Memory/report state; cancellation, quotas, backpressure and observability; offline migration, consistency repair, Kubernetes deployment and load/fault acceptance. No later slice may bypass this plan's configuration, migration, or connection contracts.

## Global Constraints

- Read `PROJECT_KNOWLEDGE.md`; current code, tests, configuration, and runtime behavior are authoritative.
- Preserve `user_id`, `document_id`, source-page, citation, RAG namespace, Memory, History, report, and import-task isolation rules.
- Preserve unrelated dirty files and stage only paths owned by the current task.
- `local` remains the default and continues using SQLite, local files, and the existing embedded `ImportWorkerPool`.
- `distributed` requires PostgreSQL, S3-compatible object storage, and an explicit `api` or `worker` process role; missing settings fail before services start.
- Do not add Redis, Celery, Kafka, live dual writes, or implicit schema upgrades during application startup.
- PostgreSQL server time is authoritative for future lease comparisons.
- Runtime SQL uses `psycopg` parameter binding; identifiers are never assembled from request data.
- Alembic is the only production PostgreSQL schema migration mechanism.
- Default tests must not require Docker, PostgreSQL, Qdrant, Neo4j, S3, or a live LLM.
- Run Python tests with `D:\python_self_agent\venv\Scripts\python.exe` and a repository-local `--basetemp` path.

## File and Responsibility Map

- `requirements.txt`: bounded PostgreSQL and migration dependencies.
- `app/deployment.py`: immutable environment configuration and fail-fast validation.
- `app/postgres.py`: `psycopg_pool` lifecycle, transaction, health, and server-time access.
- `alembic.ini`: repository-local Alembic command configuration without secrets.
- `migrations/env.py`: reads `DATABASE_URL` at command time and configures online/offline migrations.
- `migrations/script.py.mako`: standard revision template.
- `migrations/versions/20260809_01_distributed_foundation.py`: initial distributed schema.
- `compose.yaml`: opt-in PostgreSQL service and health check for local distributed testing.
- `deploy/.env.example`: non-secret distributed configuration contract.
- `deploy/README.md`: migration and profile commands.
- `tests/test_deployment_settings.py`: local/distributed configuration contract.
- `tests/test_postgres.py`: connection adapter behavior with fakes.
- `tests/integration/test_postgres_schema.py`: opt-in real PostgreSQL migration/schema checks.
- `tests/deploy/test_compose_contract.py`: Compose and environment static contract.

---

### Task 1: Distributed Runtime Configuration

**Files:**
- Create: `app/deployment.py`
- Create: `tests/test_deployment_settings.py`

**Interfaces:**
- Consumes: environment variables only.
- Produces: `DataMode`, `ProcessRole`, `DeploymentConfigurationError`, `DeploymentSettings.from_env(env=None)`, `DeploymentSettings.validate()`.

- [ ] **Step 1: Write failing configuration tests**

Create `tests/test_deployment_settings.py` with these cases:

```python
import pytest

from app.deployment import (
    DeploymentConfigurationError,
    DeploymentSettings,
)


def test_local_mode_is_the_safe_default():
    settings = DeploymentSettings.from_env({})

    assert settings.data_mode == "local"
    assert settings.process_role == "all"
    assert settings.database_url is None
    settings.validate()


def test_distributed_mode_requires_explicit_shared_dependencies():
    settings = DeploymentSettings.from_env({
        "APP_DATA_MODE": "distributed",
        "APP_PROCESS_ROLE": "worker",
    })

    with pytest.raises(
        DeploymentConfigurationError,
        match="DATABASE_URL, S3_ENDPOINT_URL, S3_BUCKET",
    ):
        settings.validate()


def test_distributed_mode_accepts_api_and_worker_roles():
    base = {
        "APP_DATA_MODE": "distributed",
        "DATABASE_URL": "postgresql://app:secret@postgres/app",
        "S3_ENDPOINT_URL": "http://minio:9000",
        "S3_BUCKET": "documents",
        "S3_ACCESS_KEY_ID": "app",
        "S3_SECRET_ACCESS_KEY": "secret",
    }

    for role in ("api", "worker"):
        settings = DeploymentSettings.from_env({**base, "APP_PROCESS_ROLE": role})
        settings.validate()
        assert settings.process_role == role


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_DATA_MODE", "cluster"),
        ("APP_PROCESS_ROLE", "scheduler"),
        ("POSTGRES_POOL_MIN_SIZE", "-1"),
        ("POSTGRES_POOL_MAX_SIZE", "0"),
    ],
)
def test_invalid_enumerations_and_pool_sizes_are_rejected(name, value):
    with pytest.raises(DeploymentConfigurationError):
        DeploymentSettings.from_env({name: value})


def test_database_url_is_redacted_in_safe_summary():
    settings = DeploymentSettings.from_env({
        "APP_DATA_MODE": "distributed",
        "APP_PROCESS_ROLE": "api",
        "DATABASE_URL": "postgresql://app:top-secret@postgres/app",
        "S3_ENDPOINT_URL": "http://minio:9000",
        "S3_BUCKET": "documents",
        "S3_ACCESS_KEY_ID": "app",
        "S3_SECRET_ACCESS_KEY": "also-secret",
    })

    summary = settings.safe_summary()

    assert "top-secret" not in repr(summary)
    assert "also-secret" not in repr(summary)
    assert summary["database_configured"] is True
    assert summary["object_store_configured"] is True
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_deployment_settings.py -q --basetemp=.runtime/pytest-distributed-settings-red
```

Expected: FAIL because `app.deployment` does not exist.

- [ ] **Step 3: Implement the immutable settings model**

Create `app/deployment.py` with these public definitions and validation behavior:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Mapping


DataMode = Literal["local", "distributed"]
ProcessRole = Literal["all", "api", "worker"]


class DeploymentConfigurationError(ValueError):
    """Raised when a runtime mode cannot satisfy its dependency contract."""


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise DeploymentConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise DeploymentConfigurationError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class DeploymentSettings:
    data_mode: DataMode
    process_role: ProcessRole
    database_url: str | None
    postgres_pool_min_size: int
    postgres_pool_max_size: int
    s3_endpoint_url: str | None
    s3_bucket: str | None
    s3_region: str
    s3_access_key_id: str | None
    s3_secret_access_key: str | None

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None
    ) -> "DeploymentSettings":
        values = os.environ if env is None else env
        data_mode = values.get("APP_DATA_MODE", "local").strip().lower()
        process_role = values.get("APP_PROCESS_ROLE", "all").strip().lower()
        if data_mode not in {"local", "distributed"}:
            raise DeploymentConfigurationError(
                "APP_DATA_MODE must be local or distributed"
            )
        if process_role not in {"all", "api", "worker"}:
            raise DeploymentConfigurationError(
                "APP_PROCESS_ROLE must be all, api, or worker"
            )
        settings = cls(
            data_mode=data_mode,
            process_role=process_role,
            database_url=values.get("DATABASE_URL") or None,
            postgres_pool_min_size=_positive_int(
                values, "POSTGRES_POOL_MIN_SIZE", 1
            ),
            postgres_pool_max_size=_positive_int(
                values, "POSTGRES_POOL_MAX_SIZE", 10
            ),
            s3_endpoint_url=values.get("S3_ENDPOINT_URL") or None,
            s3_bucket=values.get("S3_BUCKET") or None,
            s3_region=values.get("S3_REGION", "us-east-1"),
            s3_access_key_id=values.get("S3_ACCESS_KEY_ID") or None,
            s3_secret_access_key=values.get("S3_SECRET_ACCESS_KEY") or None,
        )
        if settings.postgres_pool_min_size > settings.postgres_pool_max_size:
            raise DeploymentConfigurationError(
                "POSTGRES_POOL_MIN_SIZE cannot exceed POSTGRES_POOL_MAX_SIZE"
            )
        return settings

    def validate(self) -> None:
        if self.data_mode == "local":
            if self.process_role != "all":
                raise DeploymentConfigurationError(
                    "local mode requires APP_PROCESS_ROLE=all"
                )
            return
        if self.process_role not in {"api", "worker"}:
            raise DeploymentConfigurationError(
                "distributed mode requires APP_PROCESS_ROLE=api or worker"
            )
        required = {
            "DATABASE_URL": self.database_url,
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_BUCKET": self.s3_bucket,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise DeploymentConfigurationError(
                f"distributed mode requires {', '.join(missing)}"
            )

    def safe_summary(self) -> dict[str, object]:
        return {
            "data_mode": self.data_mode,
            "process_role": self.process_role,
            "database_configured": bool(self.database_url),
            "object_store_configured": bool(
                self.s3_endpoint_url and self.s3_bucket
            ),
            "postgres_pool_min_size": self.postgres_pool_min_size,
            "postgres_pool_max_size": self.postgres_pool_max_size,
            "s3_region": self.s3_region,
        }
```

Do not log or return raw URLs, access keys, or secret keys.

- [ ] **Step 4: Run configuration tests and verify GREEN**

Run the Step 2 command again.

Expected: all tests PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add app/deployment.py tests/test_deployment_settings.py
git commit -m "feat: validate distributed runtime settings"
```

---

### Task 2: PostgreSQL Runtime Connection Adapter

**Files:**
- Modify: `requirements.txt`
- Create: `app/postgres.py`
- Create: `tests/test_postgres.py`

**Interfaces:**
- Consumes: `DeploymentSettings.database_url`, pool min/max sizes.
- Produces: `PostgresDatabase`, `PostgresDatabase.open()`, `close()`, `connection()`, `transaction()`, `ping()`, and `server_now()`.

- [ ] **Step 1: Add failing adapter tests with a fake pool**

Create `tests/test_postgres.py`:

```python
from datetime import datetime, timezone

import pytest

from app.postgres import PostgresDatabase


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    def execute(self, sql, parameters=None):
        self.executed.append((sql, parameters))
        return self

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_instance = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, *args):
        return False


class FakePool:
    def __init__(self, connection):
        self.connection_instance = connection
        self.opened = 0
        self.closed = 0

    def open(self, wait=True):
        self.opened += 1

    def close(self):
        self.closed += 1

    def connection(self):
        return Context(self.connection_instance)


def test_pool_lifecycle_is_idempotent():
    pool = FakePool(FakeConnection(FakeCursor()))
    database = PostgresDatabase("postgresql://redacted", pool=pool)

    database.open()
    database.open()
    database.close()
    database.close()

    assert pool.opened == 1
    assert pool.closed == 1


def test_transaction_commits_and_rolls_back():
    connection = FakeConnection(FakeCursor())
    database = PostgresDatabase(
        "postgresql://redacted", pool=FakePool(connection)
    )

    with database.transaction() as cursor:
        cursor.execute("select %s", (1,))
    assert connection.commits == 1

    with pytest.raises(RuntimeError):
        with database.transaction():
            raise RuntimeError("boom")
    assert connection.rollbacks == 1


def test_ping_and_server_now_use_server_queries():
    expected = datetime(2026, 8, 9, tzinfo=timezone.utc)
    cursor = FakeCursor({"server_now": expected})
    database = PostgresDatabase(
        "postgresql://redacted", pool=FakePool(FakeConnection(cursor))
    )

    assert database.ping() is True
    assert database.server_now() == expected
    assert cursor.executed == [
        ("select 1", None),
        ("select clock_timestamp() as server_now", None),
    ]
```

- [ ] **Step 2: Run adapter tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_postgres.py -q --basetemp=.runtime/pytest-postgres-adapter-red
```

Expected: FAIL because `app.postgres` does not exist.

- [ ] **Step 3: Add bounded dependencies**

Append to `requirements.txt`:

```text
psycopg[binary,pool]>=3.2,<4
SQLAlchemy>=2.0,<3
alembic>=1.16,<2
```

Install into the repository virtual environment:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Expected: exit 0; `python -c "import psycopg, psycopg_pool, alembic"` exits 0.

- [ ] **Step 4: Implement `PostgresDatabase`**

Create `app/postgres.py`. Construct a `psycopg_pool.ConnectionPool` with `open=False`, `kwargs={"row_factory": dict_row}`, `min_size`, `max_size`, and a 10-second timeout when no pool is injected. Guard `open()`/`close()` with `threading.Lock` and an `_opened` flag. Implement:

```python
@contextmanager
def connection(self):
    with self._pool.connection() as connection:
        yield connection

@contextmanager
def transaction(self):
    with self.connection() as connection:
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except Exception:
            connection.rollback()
            raise

def ping(self) -> bool:
    with self.connection() as connection:
        connection.cursor().execute("select 1")
    return True

def server_now(self) -> datetime:
    with self.connection() as connection:
        row = connection.cursor().execute(
            "select clock_timestamp() as server_now"
        ).fetchone()
    return row["server_now"]
```

Reject an empty DSN with `ValueError("database URL is required")`. Do not expose the DSN in `repr`, exceptions created by this module, or logs.

- [ ] **Step 5: Run adapter and settings tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_postgres.py tests/test_deployment_settings.py -q --basetemp=.runtime/pytest-postgres-adapter
```

Expected: PASS.

- [ ] **Step 6: Commit Task 2**

```powershell
git add requirements.txt app/postgres.py tests/test_postgres.py
git commit -m "feat: add PostgreSQL connection adapter"
```

---

### Task 3: Versioned Distributed PostgreSQL Schema

**Files:**
- Create: `alembic.ini`
- Create: `migrations/__init__.py`
- Create: `migrations/env.py`
- Create: `migrations/script.py.mako`
- Create: `migrations/versions/__init__.py`
- Create: `migrations/versions/20260809_01_distributed_foundation.py`
- Create: `tests/test_migration_contract.py`

**Interfaces:**
- Consumes: `DATABASE_URL` only when Alembic commands run.
- Produces: Alembic revision `20260809_01`; tables `users`, `sessions`, `report_records`, `import_batches`, `import_tasks`, `import_task_attempts`, `import_workers`, `user_queue_state`, `history_records`, `memory_snapshots`, `outbox_events`, and `audit_events`.

- [ ] **Step 1: Write migration source-contract tests**

Create `tests/test_migration_contract.py` that imports the revision module and asserts:

```python
from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_distributed_revision_has_stable_identity_and_all_tables():
    revision = import_module(
        "migrations.versions.20260809_01_distributed_foundation"
    )

    assert revision.revision == "20260809_01"
    assert revision.down_revision is None
    assert revision.TABLES == (
        "users", "sessions", "report_records", "import_batches",
        "import_tasks", "import_task_attempts", "import_workers",
        "user_queue_state", "history_records", "memory_snapshots",
        "outbox_events", "audit_events",
    )


def test_alembic_configuration_contains_no_database_secret():
    source = (ROOT / "alembic.ini").read_text(encoding="utf-8")

    assert "sqlalchemy.url" not in source
    assert "password" not in source.lower()
```

- [ ] **Step 2: Run contract tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_migration_contract.py -q --basetemp=.runtime/pytest-migration-contract-red
```

Expected: FAIL because the migration package does not exist.

- [ ] **Step 3: Add Alembic command files**

Create a secret-free `alembic.ini` with `script_location = migrations`, logging sections, and no `sqlalchemy.url` key. In `migrations/env.py`:

```python
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = None


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required for Alembic")
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+psycopg://", 1)
    return value


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Use the standard Alembic `script.py.mako` template. Add empty `__init__.py` files to `migrations/` and `migrations/versions/` so contract tests can import revisions.

- [ ] **Step 4: Implement the complete initial revision**

Define `TABLES` exactly as tested and use Alembic `op.create_table`/`op.create_index`. Required constraints include:

```python
sa.CheckConstraint(
    "status in ('queued','running','retry_wait','cancelling',"
    "'succeeded','failed','cancelled')",
    name="ck_import_tasks_status",
)
sa.CheckConstraint("progress between 0 and 100", name="ck_import_tasks_progress")
sa.UniqueConstraint("user_id", "document_id", name="uq_import_tasks_user_document")
```

`import_tasks` includes the existing fields plus `claimed_by`, UUID `lease_token`, bigint `lease_version`, timezone-aware `heartbeat_at`, `lease_expires_at`, `cancellation_requested_at`, `cancelled_at`, smallint `priority`, 64-character `content_sha256`, and `object_key`. Create:

```sql
create unique index uq_import_tasks_executing_user
on import_tasks(user_id)
where status in ('running', 'cancelling')
```

Create scheduler index columns `(status, next_attempt_at, priority, created_at, id)`, lease index `(lease_expires_at)` for executing states, and user-created index `(user_id, created_at, id)`.

`sessions` stores only `token_hash`, never the raw token, with unique hash, user FK, created/last-accessed/expires/revoked timestamps. `history_records` and `memory_snapshots` include `user_id`, version, JSONB payload, and timestamps. `outbox_events` includes aggregate identity, event type, JSONB payload, created/available/processed timestamps and attempt count. `audit_events` is append-only at the application layer and stores actor user, action, target type/ID, result, safe summary, request ID, and timestamp.

`downgrade()` drops tables in exact reverse dependency order and never drops PostgreSQL extensions or databases.

- [ ] **Step 5: Run contract tests and render offline SQL**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_migration_contract.py -q --basetemp=.runtime/pytest-migration-contract
$env:DATABASE_URL='postgresql://app:placeholder@localhost/app'; D:\python_self_agent\venv\Scripts\python.exe -m alembic upgrade head --sql *> .runtime/distributed-schema.sql
Select-String -Path .runtime/distributed-schema.sql -Pattern 'CREATE TABLE import_tasks','uq_import_tasks_executing_user'
```

Expected: tests PASS; offline SQL exits 0 and contains `CREATE TABLE import_tasks` and `uq_import_tasks_executing_user`.

- [ ] **Step 6: Commit Task 3**

```powershell
git add alembic.ini migrations tests/test_migration_contract.py
git commit -m "feat: define distributed PostgreSQL schema"
```

---

### Task 4: Opt-in PostgreSQL Integration Environment

**Files:**
- Modify: `compose.yaml`
- Modify: `deploy/.env.example`
- Modify: `tests/deploy/test_compose_contract.py`
- Create: `tests/integration/test_postgres_schema.py`

**Interfaces:**
- Consumes: `POSTGRES_TEST_URL` for opt-in tests.
- Produces: Compose `postgres` service in profile `distributed`; real migration smoke test.

- [ ] **Step 1: Extend static deployment tests first**

Add assertions to `tests/deploy/test_compose_contract.py`:

```python
def test_distributed_profile_contains_private_postgres_service():
    source = COMPOSE.read_text(encoding="utf-8")
    postgres = source.split("  postgres:", 1)[1].split("networks:", 1)[0]

    assert "postgres:17.6-bookworm" in postgres
    assert "- distributed" in postgres
    assert "pg_isready" in postgres
    assert "ports:" not in postgres
    assert "/var/lib/postgresql/data" in postgres


def test_environment_template_declares_distributed_contract_without_secrets():
    source = ENV_EXAMPLE.read_text(encoding="utf-8")

    for name in (
        "APP_DATA_MODE=", "APP_PROCESS_ROLE=", "DATABASE_URL=",
        "POSTGRES_POOL_MIN_SIZE=", "POSTGRES_POOL_MAX_SIZE=",
        "S3_ENDPOINT_URL=", "S3_BUCKET=", "S3_REGION=",
        "S3_ACCESS_KEY_ID=", "S3_SECRET_ACCESS_KEY=",
    ):
        assert name in source
```

- [ ] **Step 2: Run deployment tests and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_compose_contract.py -q --basetemp=.runtime/pytest-postgres-compose-red
```

Expected: FAIL because PostgreSQL and distributed variables are absent.

- [ ] **Step 3: Add the private PostgreSQL service**

Add `postgres` to `compose.yaml`:

```yaml
  postgres:
    profiles:
      - distributed
    image: postgres:17.6-bookworm
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-pdf_assistant}
      POSTGRES_USER: ${POSTGRES_USER:-pdf_assistant}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-replace-before-distributed-use}
    expose:
      - "5432"
    volumes:
      - "${DEPLOY_DATA_ROOT:-./deploy-data}/postgres:/var/lib/postgresql/data"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      start_period: 20s
      retries: 12
    networks:
      - app_net
    restart: unless-stopped
```

Do not publish port 5432. Add the tested variables to `deploy/.env.example` with placeholder values and `APP_DATA_MODE=local`, `APP_PROCESS_ROLE=all` defaults.

- [ ] **Step 4: Write the opt-in migrated-schema integration test**

Create `tests/integration/test_postgres_schema.py`. Skip the module unless `POSTGRES_TEST_URL` is set. Use `alembic.command.upgrade(Config("alembic.ini"), "head")`, then `psycopg.connect` and assert:

```python
EXPECTED_TABLES = {
    "alembic_version", "users", "sessions", "report_records",
    "import_batches", "import_tasks", "import_task_attempts",
    "import_workers", "user_queue_state", "history_records",
    "memory_snapshots", "outbox_events", "audit_events",
}


def test_real_postgres_upgrade_is_idempotent(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", POSTGRES_TEST_URL)
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.upgrade(config, "head")

    with psycopg.connect(POSTGRES_TEST_URL) as connection:
        rows = connection.execute(
            "select tablename from pg_tables where schemaname = 'public'"
        ).fetchall()

    assert EXPECTED_TABLES <= {row[0] for row in rows}
```

Also insert two users and two running tasks successfully, then assert a second running task for the same user raises `psycopg.errors.UniqueViolation`.

- [ ] **Step 5: Run static tests and opt-in integration when configured**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_compose_contract.py tests/integration/test_postgres_schema.py -q --basetemp=.runtime/pytest-postgres-compose
```

Expected without `POSTGRES_TEST_URL`: static tests PASS and integration tests SKIP. With an authorized disposable URL: all PASS.

- [ ] **Step 6: Commit Task 4**

```powershell
git add compose.yaml deploy/.env.example tests/deploy/test_compose_contract.py tests/integration/test_postgres_schema.py
git commit -m "test: add PostgreSQL integration profile"
```

---

### Task 5: Migration and Operator Documentation

**Files:**
- Modify: `deploy/README.md`
- Create: `tests/deploy/test_distributed_foundation_docs.py`

**Interfaces:**
- Consumes: Tasks 1-4 commands and environment variables.
- Produces: exact local PostgreSQL startup, migration, verification, shutdown, and secret-boundary runbook.

- [ ] **Step 1: Write documentation contract test**

Create `tests/deploy/test_distributed_foundation_docs.py`:

```python
from pathlib import Path


README = Path(__file__).parents[2] / "deploy" / "README.md"


def test_distributed_foundation_runbook_has_exact_safe_commands():
    source = README.read_text(encoding="utf-8")

    for text in (
        "docker compose --profile distributed",
        "python -m alembic upgrade head",
        "APP_DATA_MODE=distributed",
        "APP_PROCESS_ROLE=api",
        "APP_PROCESS_ROLE=worker",
        "PostgreSQL",
        "S3",
        "不要在命令行参数中传递密码",
    ):
        assert text in source
```

- [ ] **Step 2: Run the documentation test and verify RED**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/deploy/test_distributed_foundation_docs.py -q --basetemp=.runtime/pytest-distributed-docs-red
```

Expected: FAIL because the distributed foundation runbook is absent.

- [ ] **Step 3: Add the runbook**

Document:

- `docker compose --env-file deploy/.env --profile distributed up -d postgres`.
- supplying `DATABASE_URL` through the environment and running `python -m alembic upgrade head` before application startup.
- verifying `alembic current`, PostgreSQL health, and the table list.
- `APP_DATA_MODE=distributed` with separate `APP_PROCESS_ROLE=api|worker`; state clearly that application role startup is delivered by later slices.
- local mode remains the current default.
- secrets belong in an environment file or external secret manager; do not pass passwords in command-line arguments or commit `deploy/.env`.
- safe shutdown and the persistent PostgreSQL data directory.

- [ ] **Step 4: Run focused and full local regressions**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/test_deployment_settings.py tests/test_postgres.py tests/test_migration_contract.py tests/deploy/test_compose_contract.py tests/deploy/test_distributed_foundation_docs.py tests/integration/test_postgres_schema.py -q --basetemp=.runtime/pytest-distributed-foundation
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime/pytest-distributed-foundation-full
```

Expected: focused tests PASS with the real PostgreSQL test skipped unless configured; full default suite PASS with only existing/authorized integration skips.

- [ ] **Step 5: Commit Task 5**

```powershell
git add deploy/README.md tests/deploy/test_distributed_foundation_docs.py
git commit -m "docs: add distributed database runbook"
```

## Plan Self-Review Checklist

- [ ] Every requirement in this slice maps to a task and a named test.
- [ ] No local SQLite repository or runtime behavior changes in this slice.
- [ ] No database URL, access key, or secret is logged, committed, or asserted verbatim.
- [ ] Migration is explicit and idempotent; application startup does not call Alembic.
- [ ] PostgreSQL integration remains opt-in and uses an explicitly authorized disposable database.
- [ ] `git diff --check` and the full default suite pass before the slice is considered complete.
