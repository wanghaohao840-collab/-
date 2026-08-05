"""Shared construction and lifecycle management for application services."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from app.database import initialize_database
from app.import_repository import ImportTaskRepository
from app.import_service import ImportTaskService
from app.import_worker import ImportWorkerPool
from app.migration import LegacyMigrationService
from app.session import SessionRegistry
from app.storage import UserStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ApplicationServices:
    """The persistent services shared by supported application entry points."""

    data_root: Path
    db_path: Path
    storage: UserStorage
    session_registry: SessionRegistry
    legacy_migration: LegacyMigrationService
    import_repository: ImportTaskRepository
    import_worker_pool: ImportWorkerPool
    import_service: ImportTaskService
    _started: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(cls, data_root: Path | None = None) -> "ApplicationServices":
        resolved_data_root = Path(
            data_root
            or os.getenv("PDF_ASSISTANT_DATA_DIR")
            or PROJECT_ROOT / "data"
        ).resolve()
        db_path = resolved_data_root / "app.db"

        initialize_database(db_path)
        storage = UserStorage(resolved_data_root)
        session_registry = SessionRegistry(db_path=db_path, storage=storage)
        legacy_migration = LegacyMigrationService(db_path, storage, PROJECT_ROOT)
        import_repository = ImportTaskRepository(db_path)
        import_worker_pool = ImportWorkerPool(
            import_repository,
            session_registry.runtime_registry,
            storage,
        )
        import_service = ImportTaskService(
            session_registry,
            import_repository,
            storage,
            import_worker_pool,
        )
        session_registry.runtime_registry.set_import_task_service(import_service)
        return cls(
            data_root=resolved_data_root,
            db_path=db_path,
            storage=storage,
            session_registry=session_registry,
            legacy_migration=legacy_migration,
            import_repository=import_repository,
            import_worker_pool=import_worker_pool,
            import_service=import_service,
        )

    def start(self) -> None:
        if self._started:
            return
        self.import_worker_pool.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.import_worker_pool.stop()
        self._started = False


_application_services: ApplicationServices | None = None
_application_services_lock = RLock()


def get_application_services() -> ApplicationServices:
    """Return the process-wide application services singleton."""

    global _application_services
    with _application_services_lock:
        if _application_services is None:
            _application_services = ApplicationServices.create()
        return _application_services


def reset_application_services_for_tests() -> None:
    """Stop and clear the singleton so tests can construct isolated services."""

    global _application_services
    with _application_services_lock:
        if _application_services is not None:
            _application_services.stop()
            _application_services = None
