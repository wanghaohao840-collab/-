"""Journal-bound read-only volume proof; not automatic service release.

Caller binds local daemon, exclusive lock, verifier script and approved immutable
Python helper image. Inspect/run is not atomic against hostile daemon mutation.
Timeout may leave the read-only helper running; never retry or clear maintenance.
"""
from hashlib import sha256
import json
import os
from pathlib import Path,PurePosixPath
import re
import subprocess

from deploy.learning_maintenance import read_journal

SCRIPT=Path(__file__).resolve().with_name('learning_restored_tree.py')


def _run(argv):
    result=subprocess.run(argv,capture_output=True,timeout=3600,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    if result.returncode!=0: raise ValueError
    return result.stdout


def _unique(pairs):
    result=dict(pairs)
    if len(result)!=len(pairs): raise ValueError
    return result


def _decode(raw):
    if type(raw) is not bytes or len(raw)>65536: raise ValueError
    return json.loads(raw.decode('utf-8'),object_pairs_hook=_unique)


def verify_restored_qdrant(state_root,operation_id,*,context,volume,volume_sha256,helper_image,
                           verify_context,verify_stopped,verify_backup,runner=None):
    try:
        for value in (context,volume):
            if type(value) is not str or not re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}',value): return False
        if not re.fullmatch('sha256:[0-9a-f]{64}',helper_image) or not re.fullmatch('[0-9a-f]{64}',volume_sha256): return False
        state=read_journal(state_root,operation_id)
        if state.phase!='recovering' or state.backup_json is None: return False
        backup=json.loads(state.backup_json)
        def guard():
            return (read_journal(state_root,operation_id).record_sha256==state.record_sha256 and
                    verify_context() is True and verify_stopped() is True and
                    read_journal(state_root,operation_id).record_sha256==state.record_sha256)
        if not guard() or verify_backup(dict(backup)) is not True: return False
        archive=Path(backup['qdrant_archive'])
        for path in (archive,SCRIPT):
            if not path.is_absolute() or '..' in path.parts or not path.is_file(): return False
            if any(c in str(path) for c in (',','\r','\n','\x00')): return False
        run=runner or _run
        prefix=['docker','--context',context]
        def inspect():
            if not guard(): raise ValueError
            items=_decode(run(prefix+['volume','inspect',volume]))
            if type(items) is not list or len(items)!=1: raise ValueError
            item=items[0]
            if (item['Name']!=volume or item['Driver']!='local' or item['Scope']!='local'
                    or item.get('Options') not in (None,{})): raise ValueError
            mount=PurePosixPath(item['Mountpoint'])
            if not mount.is_absolute() or '..' in mount.parts: raise ValueError
            digest=sha256(json.dumps(item,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
            if digest!=volume_sha256 or not guard(): raise ValueError
            return item
        before=inspect()
        if not guard(): return False
        argv=prefix+['run','--rm','--pull','never','--read-only','--network','none',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--pids-limit','64',
            '--memory','256m','--cpus','1','--user','0:0',
            '--mount',f'type=volume,source={volume},target=/volume,readonly,volume-nocopy',
            '--mount',f'type=bind,source={archive},target=/backup.tar.gz,readonly',
            '--mount',f'type=bind,source={SCRIPT},target=/verifier.py,readonly',
            '--entrypoint','python',helper_image,'-I','-B','/verifier.py','/volume','/backup.tar.gz',backup['qdrant_sha256']]
        result=_decode(run(argv))
        if (type(result) is not dict or set(result)!={'verified','archive_sha256'}
                or result['verified'] is not True or result['archive_sha256']!=backup['qdrant_sha256']): return False
        return inspect()==before and guard()
    except Exception:
        return False
