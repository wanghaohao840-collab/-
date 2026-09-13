"""Start already prepared approved-mode containers, never perform mode conversion.

Caller owns lock/marker and container preparation. CLI failure may leave a daemon
action pending: uncertainty latches, with no retry, stop, kill or marker removal.
"""
from dataclasses import replace
import json
import math
import os
import re
import subprocess
from threading import Lock
import time

from deploy.learning_containers import read_containers,matches_expected
from deploy.learning_maintenance import read_journal


class StartError(RuntimeError):
    pass


def _run(argv):
    result=subprocess.run(argv,capture_output=True,timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode!=0: raise ValueError
    return result.stdout


class ContainerStartActions:
    def __init__(self,context,project,state_root,operation_id,*,baseline,images,normal_configurations,
                 maintenance_configurations,verify_context,verify_maintenance,verify_released,
                 runner=None,timeout_seconds=120):
        if any(type(v) is not str or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',v) for v in (context,project)):
            raise StartError('START_TARGET_INVALID')
        if type(timeout_seconds) not in (int,float) or not math.isfinite(timeout_seconds) or timeout_seconds<=0:
            raise StartError('START_TIMEOUT_INVALID')
        self._context,self._project,self._root,self._operation=context,project,state_root,operation_id
        self._approved=json.loads(json.dumps(dict(baseline=baseline,images=images,normal=normal_configurations,
            maintenance=maintenance_configurations),allow_nan=False))
        self._context_guard=verify_context
        self._maintenance_ready,self._released_ready=verify_maintenance,verify_released
        self._runner,self._timeout=runner,timeout_seconds
        self._uncertain=False
        self._mutex=Lock()

    def _start(self,baseline,recovered,*,released):
        with self._mutex:
            dispatched=False
            try:
                if self._uncertain: raise ValueError
                baseline=json.loads(json.dumps(baseline,allow_nan=False))
                state=read_journal(self._root,self._operation)
                if released:
                    if type(recovered) is not bool or state.phase!=('recovered' if recovered else 'complete'): raise ValueError
                else:
                    if state.phase not in ('verified','recovering'): raise ValueError
                    recovered=state.phase=='recovering'
                if baseline!=self._approved['baseline'] or json.loads(state.baseline_json)!=baseline: raise ValueError
                desired=set(baseline['running_services'])
                def guard():
                    if (read_journal(self._root,self._operation).record_sha256!=state.record_sha256 or
                            self._context_guard() is not True or
                            read_journal(self._root,self._operation).record_sha256!=state.record_sha256): raise ValueError
                def observe():
                    guard()
                    rows=read_containers(self._context,self._project,runner=self._runner)
                    if not matches_expected(tuple(replace(r,running=False) for r in rows),images=self._approved['images'],
                        configurations=self._approved['normal' if released else 'maintenance'],running_services=(),
                        operation_id=None if released else self._operation): raise ValueError
                    if not {r.service for r in rows if r.running}<=desired: raise ValueError
                    guard()
                    return rows
                def identity(rows):
                    return tuple((r.service,r.container_id,r.image,r.configuration_sha256,r.mode,r.operation_id) for r in rows)
                first=observe()
                if observe()!=first: raise ValueError
                approved_identity=identity(first)
                def current():
                    rows=observe()
                    if identity(rows)!=approved_identity: raise ValueError
                    return rows
                deadline=time.monotonic()+self._timeout
                def pause():
                    remaining=deadline-time.monotonic()
                    if remaining<=0: raise ValueError
                    time.sleep(min(.1,remaining))
                for service in ('qdrant','app'):
                    if service not in desired: continue
                    rows=current()
                    if service=='app' and 'qdrant' in desired:
                        dependency=next(r for r in rows if r.service=='qdrant')
                        if not dependency.running or dependency.health!='healthy': raise ValueError
                    target=next(r for r in rows if r.service==service)
                    if not target.running:
                        guard();dispatched=True
                        (self._runner or _run)(['docker','--context',self._context,'container','start',target.container_id])
                    while True:
                        rows=current()
                        target=next(r for r in rows if r.service==service)
                        if target.running and (service!='qdrant' or target.health=='healthy'): break
                        pause()
                ready=self._released_ready if released else self._maintenance_ready
                while True:
                    rows=current()
                    if {r.service for r in rows if r.running}==desired:
                        result=ready(json.loads(json.dumps(baseline)),recovered)
                        if type(result) is not bool: raise ValueError
                        if result:
                            after=current()
                            if after==rows:
                                return
                            # A successful probe can straddle App health becoming
                            # ready. Re-probe to obtain a stable observation pair;
                            # never accept identity, port, running-state or health
                            # regression changes through this retry.
                            if not all(a==b or (
                                    a.service=='app' and a.health=='starting' and
                                    b.health=='healthy' and replace(a,health=b.health)==b)
                                    for a,b in zip(rows,after)):
                                raise ValueError
                    pause()
            except Exception:
                if dispatched: self._uncertain=True
                raise StartError('START_FAILED_MAINTENANCE_RETAINED') from None

    def start_maintenance(self,baseline):
        self._start(baseline,None,released=False)

    def release(self,baseline,recovered):
        self._start(baseline,recovered,released=True)
