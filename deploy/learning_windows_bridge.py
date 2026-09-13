"""Own a same-lock PowerShell host; never release a maintenance marker."""
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
from threading import Lock, Thread

from deploy.learning_maintenance import read_journal

HOST = Path(__file__).resolve().parent / 'windows' / 'Invoke-LearningContextBridge.ps1'


class BridgeError(RuntimeError):
    pass


class WindowsContextBridge:
    def __init__(self, config):
        self._process = None
        self._reader = None
        self._backup_pending = False
        self._restore_pending = False
        self._mutex = Lock()
        self._responses = Queue(maxsize=4)
        try:
            payload = json.dumps(config, ensure_ascii=True, allow_nan=False).encode('ascii')
            frozen = json.loads(payload)
            self._state_root = frozen['state_root']
            self._operation_id = frozen['operation_id']
            if len(payload) > 16384 or os.name != 'nt':
                raise ValueError
            self._process = subprocess.Popen(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(HOST)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self._reader = Thread(target=self._read, daemon=True)
            self._reader.start()
            self._send(payload)
            if self._responses.get(timeout=10) != b'READY':
                raise ValueError
        except Exception:
            self.close()
            raise BridgeError('WINDOWS_CONTEXT_BRIDGE_UNAVAILABLE') from None

    def _read(self):
        try:
            while True:
                line = self._process.stdout.readline(16384)
                if not line or len(line) >= 16384 or not line.endswith(b'\n'):
                    self._responses.put_nowait(b'ERROR')
                    return
                self._responses.put_nowait(line.rstrip(b'\r\n'))
        except Exception:
            try:
                self._responses.put_nowait(b'ERROR')
            except Exception:
                pass

    def _send(self, command):
        self._process.stdin.write(command + b'\n')
        self._process.stdin.flush()

    def verify_context(self):
        with self._mutex:
            if self._process is None or self._backup_pending or self._restore_pending:
                return False
            try:
                self._send(b'VERIFY')
                response = self._responses.get(timeout=75)
                if response not in (b'VERIFIED', b'REJECTED'):
                    raise ValueError
                return response == b'VERIFIED'
            except Exception:
                self._close()
                return False

    def begin_backup(self):
        with self._mutex:
            if self._process is None or self._backup_pending or self._restore_pending:
                raise BridgeError('BACKUP_UNAVAILABLE_OR_PENDING')
            self._backup_pending = True
            try:
                self._send(b'BACKUP')
            except Exception:
                # A failed flush does not prove the host missed the command.
                # Do not kill a possibly active backup or release its lock.
                raise BridgeError('BACKUP_DISPATCH_UNCERTAIN_LOCK_RETAINED') from None

    def poll_backup(self):
        with self._mutex:
            if not self._backup_pending:
                raise BridgeError('NO_BACKUP_PENDING')
            try:
                response = self._responses.get_nowait()
            except Empty:
                return None
            if response == b'BACKUP_REJECTED':
                self._backup_pending = False
                raise BridgeError('BACKUP_FAILED_REQUIRES_INSPECTION')
            try:
                if not response.startswith(b'BACKUP:'):
                    raise ValueError
                result = json.loads(response[7:])
                paths = {'Archive','Checksum','Metadata','QdrantArchive','QdrantChecksum'}
                if set(result) != paths | {'KeptStopped','PreviouslyRunningServices'}:
                    raise ValueError
                if result['KeptStopped'] is not True or not all(type(result[p]) is str and result[p] for p in paths):
                    raise ValueError
                services = result['PreviouslyRunningServices']
                if type(services) is not list or any(s not in ('app','qdrant') for s in services) or len(set(services)) != len(services):
                    raise ValueError
                self._backup_pending = False
                return result
            except Exception:
                # Unknown protocol output could precede an unfinished action.
                raise BridgeError('BACKUP_PROTOCOL_UNCERTAIN_LOCK_RETAINED') from None

    def begin_restore(self):
        with self._mutex:
            if self._process is None or self._backup_pending or self._restore_pending:
                raise BridgeError('RESTORE_UNAVAILABLE_OR_PENDING')
            try:
                journal = read_journal(self._state_root, self._operation_id)
                if journal.phase != 'recovering' or journal.backup_json is None:
                    raise ValueError
                self._restore_archive = json.loads(journal.backup_json)['app_archive']
            except Exception:
                raise BridgeError('RESTORE_JOURNAL_REJECTED') from None
            self._restore_pending = True
            try:
                self._send(('RESTORE:' + journal.record_sha256).encode('ascii'))
            except Exception:
                raise BridgeError('RESTORE_DISPATCH_UNCERTAIN_LOCK_RETAINED') from None

    def poll_restore(self):
        with self._mutex:
            if not self._restore_pending:
                raise BridgeError('NO_RESTORE_PENDING')
            try:
                response = self._responses.get_nowait()
            except Empty:
                return None
            if response == b'RESTORE_REJECTED':
                self._restore_pending = False
                raise BridgeError('RESTORE_FAILED_REQUIRES_INSPECTION')
            try:
                def unique(pairs):
                    result = dict(pairs)
                    if len(result) != len(pairs):
                        raise ValueError
                    return result
                if not response.startswith(b'RESTORE:'):
                    raise ValueError
                result = json.loads(response[8:], object_pairs_hook=unique)
                paths = {'RestoredArchive','DataRoot','Rollback','QdrantVolume','QdrantRollback'}
                if set(result) != paths | {'KeptStopped','PreviouslyRunningServices'}:
                    raise ValueError
                if result['KeptStopped'] is not True or not all(type(result[p]) is str and result[p] for p in paths):
                    raise ValueError
                if result['RestoredArchive'] != self._restore_archive:
                    raise ValueError
                services = result['PreviouslyRunningServices']
                if type(services) is not list or any(s not in ('app','qdrant') for s in services) or len(set(services)) != len(services):
                    raise ValueError
                self._restore_pending = False
                return result
            except Exception:
                raise BridgeError('RESTORE_PROTOCOL_UNCERTAIN_LOCK_RETAINED') from None

    def _close(self):
        process = self._process
        if process is None:
            return
        try:
            if process.stdin:
                try:
                    process.stdin.close()  # EOF releases only the host-owned handle.
                except OSError:
                    pass  # Windows may reject a flush after the peer has exited.
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        finally:
            if self._reader:
                self._reader.join(timeout=2)
            if process.stdout:
                process.stdout.close()
            self._process = None

    def close(self):
        with self._mutex:
            if self._backup_pending:
                raise BridgeError('BACKUP_PENDING_LOCK_RETAINED')
            if self._restore_pending:
                raise BridgeError('RESTORE_PENDING_LOCK_RETAINED')
            self._close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
