"""Identity-bound stop callbacks; caller owns persistent lock and maintenance marker.

Not a full backend. A CLI failure cannot cancel a daemon-side stop. Uncertainty
is latched; retain maintenance and inspect before constructing a recovery adapter.
"""
from dataclasses import replace
import os
import re
import subprocess
from threading import Lock

from deploy.learning_containers import read_containers, matches_expected


class StopError(RuntimeError):
    pass


def _run(argv):
    result=subprocess.run(argv,capture_output=True,timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode!=0:
        raise StopError('STOP_COMMAND_UNCERTAIN')
    return result.stdout


class ContainerStopActions:
    def __init__(self,context,project,*,images,configurations,verify_context,
                 operation_id=None,runner=None):
        if any(type(v) is not str or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',v)
               for v in (context,project)):
            raise StopError('STOP_TARGET_INVALID')
        self._context,self._project=context,project
        self._expected=dict(images=dict(images),configurations=dict(configurations),
                            running_services=(),operation_id=operation_id)
        self._guard=verify_context
        self._runner=runner
        self._uncertain=False
        self._mutex=Lock()

    def _observe(self):
        if self._uncertain or self._guard() is not True:
            raise StopError('STOP_CONTEXT_UNAVAILABLE')
        receipts=read_containers(self._context,self._project,runner=self._runner)
        # Ignore health only for authorization to STOP; never for readiness.
        identities=tuple(replace(r,running=False) for r in receipts)
        if not matches_expected(identities,**self._expected) or self._guard() is not True:
            raise StopError('STOP_TARGET_REJECTED')
        return receipts

    @staticmethod
    def _identity(receipts):
        return tuple((r.service,r.container_id,r.image,r.configuration_sha256,r.mode,r.operation_id)
                     for r in receipts)

    def verify_stopped(self):
        with self._mutex:
            try:
                first=self._observe()
                second=self._observe()
                return first==second and all(r.running is False for r in second)
            except Exception:
                return False

    def stop(self):
        with self._mutex:
            dispatched=False
            try:
                first=self._observe()
                current=self._observe()
                if first!=current:
                    raise ValueError
                identities=self._identity(current)
                for service in ('app','qdrant'):
                    before=self._observe()
                    if self._identity(before)!=identities:
                        raise ValueError
                    if service=='qdrant' and any(r.service=='app' and r.running for r in before):
                        raise ValueError
                    target=next(r for r in before if r.service==service)
                    if not target.running:
                        continue
                    if self._guard() is not True:
                        raise ValueError
                    dispatched=True
                    (self._runner or _run)(['docker','--context',self._context,'container','stop',
                                           '--time','10',target.container_id])
                    after=self._observe()
                    if self._identity(after)!=identities or any(r.service==service and r.running for r in after):
                        raise ValueError
                final=self._observe()
                if self._identity(final)!=identities or any(r.running for r in final):
                    raise ValueError
            except Exception:
                if dispatched:
                    self._uncertain=True
                raise StopError('STOP_FAILED_MAINTENANCE_RETAINED') from None
