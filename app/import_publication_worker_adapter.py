"""Private opt-in Worker seam for one originally issued publication."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Literal
from weakref import WeakSet

from app.import_memory_publication import (
    DurablePublicationUnknown, ImportMemoryPublicationService,
    _LivePublication,
)
from app.import_publication_evidence import AttemptKey
from app.import_publication_proof import (
    DetachedImportMemoryPublication, ImportPublicationProofService,
)
from app.import_publication_recovery import PostgresImportPublicationRecoveryRepository


_UNKNOWN_PHASES = frozenset({
    'document_put', 'document_evidence', 'rag_candidate_or_seal',
    'episode_candidate_or_seal', 'terminal_commit', 'terminal_proof',
})


@dataclass(frozen=True)
class FrozenDisposition:
    attempt_key: AttemptKey
    status: Literal['committed_success', 'needs_reconciliation']
    task_id: str
    reconciliation_reason: Literal['none', 'durable_unknown', 'recovery_unavailable']


class ImportPublicationWorkerAdapter:
    """Execute C once; leave every ambiguous outcome held for recovery."""

    def __init__(self, service: ImportMemoryPublicationService,
                 recovery: PostgresImportPublicationRecoveryRepository, *, enabled=False):
        if type(service) is not ImportMemoryPublicationService:
            raise TypeError('Original import publication service required')
        if (type(recovery) is not PostgresImportPublicationRecoveryRepository
                or recovery.database is not service.database):
            raise TypeError('Recovery repository must share the original database')
        self.service = service
        self.recovery = recovery
        self.enabled = enabled is True
        self._attempted = WeakSet()
        self._attempted_lock = Lock()

    @staticmethod
    def _held(key: AttemptKey, reason: Literal[
            'durable_unknown', 'recovery_unavailable']) -> FrozenDisposition:
        return FrozenDisposition(key, 'needs_reconciliation', key.task_id, reason)

    def run_once(self, live: _LivePublication) -> FrozenDisposition:
        if not self.enabled:
            raise RuntimeError('Durable publication Worker adapter is disabled')
        if type(live) is not _LivePublication:
            raise TypeError('Original issued live handle required')
        issue = self.service._require_live_issue(live)
        if (type(self.recovery) is not PostgresImportPublicationRecoveryRepository
                or self.recovery.database is not self.service.database
                or issue.database is not self.recovery.database):
            raise TypeError('Original issue and recovery database differ')
        key = issue.key
        with self._attempted_lock:
            if live in self._attempted:
                return self._held(key, 'recovery_unavailable')
            self._attempted.add(live)
        try:
            returned = self.service._execute_live_publication(live)
        except DurablePublicationUnknown as unknown:
            if (type(unknown.attempt_key) is not AttemptKey
                    or unknown.attempt_key != key
                    or type(unknown.phase) is not str
                    or unknown.phase not in _UNKNOWN_PHASES):
                return self._held(key, 'recovery_unavailable')
            try:
                self.recovery.schedule_unknown(key)
                ImportPublicationProofService(self.service.database).prove_exact(
                    key.user_id, key.task_id, key.task_lease_version)
            except Exception:
                return self._held(key, 'recovery_unavailable')
            return self._held(key, 'durable_unknown')
        except Exception:
            return self._held(key, 'recovery_unavailable')
        try:
            fresh = ImportPublicationProofService(self.service.database).prove_exact(
                key.user_id, key.task_id, key.task_lease_version)
        except Exception:
            return self._held(key, 'recovery_unavailable')
        if (type(returned) is DetachedImportMemoryPublication
                and type(fresh) is DetachedImportMemoryPublication
                and returned.proof.attempt_key == key
                and fresh.proof.attempt_key == key and fresh == returned):
            return FrozenDisposition(key, 'committed_success', key.task_id, 'none')
        return self._held(key, 'recovery_unavailable')
