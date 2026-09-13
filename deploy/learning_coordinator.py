"""Maintenance action ordering with a trusted, separately verified backend.

The release CLI and backend adapter own the production integration. The outer caller owns lock/marker,
input binding and ACLs. Backend verification must inspect actual state; test
doubles prove ordering only, not the safety of an eventual deployment adapter.
"""
import json
from typing import Protocol

from deploy.learning_maintenance import JournalState, advance_journal, read_journal


class CoordinatorError(RuntimeError):
    """Static failure; never include backend secrets/paths."""


class MaintenanceBackend(Protocol):
    def verify_context(self, operation_id: str, baseline: dict) -> bool: ...
    def stop(self) -> None: ...
    def verify_stopped(self) -> bool: ...
    def backup(self) -> dict: ...
    def verify_backup(self, backup: dict) -> bool: ...
    def apply(self) -> None: ...
    def restore(self, backup: dict, baseline: dict) -> None: ...
    def verify_data(self, recovered: bool) -> bool: ...
    def start_maintenance(self, baseline: dict) -> None: ...
    def verify_maintenance(self, baseline: dict, recovered: bool) -> bool: ...
    # Release is idempotent, preserves originally stopped services, and never
    # restores data. Verification inspects actual mode, identity and health.
    def release(self, baseline: dict, recovered: bool) -> None: ...
    def verify_released(self, baseline: dict, recovered: bool) -> bool: ...
    def clear_marker(self) -> None: ...


def execute_maintenance(state_root, operation_id: str, backend: MaintenanceBackend,
                        *, recover: bool = False) -> JournalState:
    state = None
    context_verified = False

    def baseline():
        return json.loads(state.baseline_json)

    def guard():
        nonlocal context_verified
        if backend.verify_context(operation_id, baseline()) is not True:
            raise CoordinatorError('MAINTENANCE_CONTEXT_UNAVAILABLE')
        context_verified = True

    def call(method, *args, verify=False):
        guard()
        result = method(*args)
        if verify and result is not True:
            raise CoordinatorError('MAINTENANCE_VERIFICATION_FAILED')
        return result

    def checkpoint(phase, *, backup=None):
        nonlocal state
        guard()
        state = advance_journal(state_root, operation_id, state.sequence, phase, backup=backup)

    def finish_terminal():
        if state.phase not in ('complete', 'recovered'):
            raise CoordinatorError('MAINTENANCE_TERMINAL_REQUIRED')
        recovered = state.phase == 'recovered'
        released = call(backend.verify_released, baseline(), recovered)
        if type(released) is not bool:
            raise CoordinatorError('MAINTENANCE_VERIFICATION_FAILED')
        if not released:
            call(backend.release, baseline(), recovered)
            call(backend.verify_released, baseline(), recovered, verify=True)
        call(backend.clear_marker)

    try:
        if type(recover) is not bool:
            raise CoordinatorError('MAINTENANCE_INVALID_MODE')
        state = read_journal(state_root, operation_id)
        if state.phase in ('complete', 'recovered'):
            # A crash between the terminal checkpoint and marker removal must
            # never trigger another apply or overwrite newly resumed writes.
            # Offline data verification is deliberately not repeated: normal
            # business writes may already have occurred after the checkpoint.
            finish_terminal()
            return state
        if recover:
            if state.phase != 'recovering':
                checkpoint('recovering')
            call(backend.stop)
            call(backend.verify_stopped, verify=True)
            if state.backup_json is not None:
                call(backend.verify_backup, json.loads(state.backup_json), verify=True)
                call(backend.restore, json.loads(state.backup_json), baseline())
            call(backend.verify_data, True, verify=True)
            call(backend.start_maintenance, baseline())
            call(backend.verify_maintenance, baseline(), True, verify=True)
            checkpoint('recovered')
        else:
            if state.phase != 'before_stop':
                raise CoordinatorError('MAINTENANCE_EXPLICIT_RECOVERY_REQUIRED')
            call(backend.stop)
            call(backend.verify_stopped, verify=True)
            checkpoint('stopped')
            backup = call(backend.backup)
            # Freeze backend-owned values before verification and persistence.
            backup_json = json.dumps(backup, allow_nan=False)
            call(backend.verify_backup, json.loads(backup_json), verify=True)
            checkpoint('backed_up', backup=json.loads(backup_json))
            call(backend.verify_stopped, verify=True)
            checkpoint('applying')
            call(backend.apply)
            checkpoint('applied')
            call(backend.verify_data, False, verify=True)
            checkpoint('verified')
            call(backend.start_maintenance, baseline())
            call(backend.verify_maintenance, baseline(), False, verify=True)
            checkpoint('complete')
        finish_terminal()
        return state
    except Exception:
        # Never clear, restart or automatically restore on failure. A lost
        # context is not permission to stop an unverified deployment target.
        if context_verified and state is not None:
            try:
                guard()
            except Exception:
                raise CoordinatorError('MAINTENANCE_CONTEXT_LOST') from None
            try:
                call(backend.stop)
                call(backend.verify_stopped, verify=True)
            except Exception:
                raise CoordinatorError('MAINTENANCE_STOP_FAILED') from None
        raise CoordinatorError('MAINTENANCE_REQUIRES_RECOVERY') from None
