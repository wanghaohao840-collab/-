"""Backup/recovery callbacks only; not a complete production maintenance backend.

The caller owns the bridge and its lifetime, with matching initialization identity.
Timeouts never cancel an external operation or release the operations lock.
"""
import json
import math
from pathlib import Path
from threading import Lock
import time

from deploy.learning_maintenance import read_journal
from deploy.learning_backup_evidence import build_backup_evidence, verify_backup_evidence


class BackupActionError(RuntimeError):
    pass


def _freeze(value):
    return json.loads(json.dumps(value,allow_nan=False))


class WindowsBackupActions:
    def __init__(self, *, bridge, state_root, operation_id, baseline, backup_root,
                 qdrant_volume, timeout_seconds=3600):
        if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds<=0:
            raise BackupActionError('INVALID_BACKUP_TIMEOUT')
        self._bridge=bridge
        self._root=Path(state_root)
        self._operation=operation_id
        self._baseline=_freeze(baseline)
        self._backup_root=Path(backup_root)
        self._volume=qdrant_volume
        self._timeout=timeout_seconds
        self._mutex=Lock()

    def _state(self):
        state=read_journal(self._root,self._operation)
        if json.loads(state.baseline_json)!=self._baseline:
            raise BackupActionError('BACKUP_IDENTITY_REJECTED')
        return state

    def _guard(self, expected_hash):
        try:
            return (self._state().record_sha256==expected_hash and
                    self._bridge.verify_context() is True and
                    self._state().record_sha256==expected_hash)
        except Exception:
            return False

    def _options(self,state):
        return dict(backup_root=self._backup_root,qdrant_volume=self._volume,
                    verify_context=lambda:self._guard(state.record_sha256))

    def _wait(self,poll):
        deadline=time.monotonic()+self._timeout
        while True:
            result=poll()
            if result is not None:
                return result
            remaining=deadline-time.monotonic()
            if remaining<=0:
                raise BackupActionError('BACKUP_ACTION_PENDING_LOCK_RETAINED')
            time.sleep(min(.1,remaining))

    def backup(self):
        with self._mutex:
            try:
                state=self._state()
                if state.phase!='stopped' or not self._guard(state.record_sha256):
                    raise ValueError
                self._bridge.begin_backup()
                receipt=self._wait(self._bridge.poll_backup)
                return build_backup_evidence(receipt,**self._options(state))
            except BackupActionError:
                raise
            except Exception:
                raise BackupActionError('BACKUP_ACTION_FAILED_INSPECTION_REQUIRED') from None

    def verify_backup(self,record):
        with self._mutex:
            try:
                record=_freeze(record)
                state=self._state()
                if state.phase not in ('stopped','backed_up','applying','applied','verified','recovering'):
                    return False
                if state.phase!='stopped' and (state.backup_json is None or json.loads(state.backup_json)!=record):
                    return False
                return verify_backup_evidence(record,**self._options(state))
            except Exception:
                return False

    def restore(self,record,baseline):
        with self._mutex:
            try:
                record,baseline=_freeze(record),_freeze(baseline)
                state=self._state()
                if (state.phase!='recovering' or baseline!=self._baseline or state.backup_json is None
                        or json.loads(state.backup_json)!=record):
                    raise ValueError
                if not verify_backup_evidence(record,**self._options(state)):
                    raise ValueError
                self._bridge.begin_restore()
                self._wait(self._bridge.poll_restore)
                if not self._guard(state.record_sha256):
                    raise ValueError
            except BackupActionError:
                raise
            except Exception:
                raise BackupActionError('RESTORE_ACTION_FAILED_INSPECTION_REQUIRED') from None
