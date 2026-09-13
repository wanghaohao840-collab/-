"""Read-only backup evidence under the caller's operations lock and trusted ACL.

Checks detect changed artifacts; open Python streams do NOT exclude Windows
writers. This proves neither archive contents nor original deployment provenance.
The caller must bind the receipt, approved volume and context independently.
"""
from contextlib import ExitStack
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

SIDECAR_LIMIT = 65536
PATH_KEYS = {'Archive','Checksum','Metadata','QdrantArchive','QdrantChecksum'}
RECORD_KEYS = {'app_archive','app_sha256','qdrant_archive','qdrant_sha256'}


class BackupEvidenceError(ValueError):
    """Static failure without artifact content or paths."""


def _require(condition):
    if not condition:
        raise ValueError


def _path(value):
    _require(isinstance(value, (str, Path)))
    path = Path(value)
    _require(path.is_absolute() and '..' not in path.parts)
    _require(all(':' not in part and '\x00' not in part for part in path.parts[1:]))
    return path


def _safe(path, *, directory=False):
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024)
        if component != path or directory:
            _require(stat.S_ISDIR(info.st_mode))
        else:
            _require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
    return info


def _identity(info):
    # Windows path stat and handle stat can disagree about ctime.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_nlink


def _unique(pairs):
    result = dict(pairs)
    _require(len(result) == len(pairs))
    return result


def build_backup_evidence(receipt, *, backup_root, qdrant_volume, verify_context):
    """Verify a KeepStopped receipt and return a detached journal backup record."""
    try:
        # Freeze caller-owned data before external callbacks or long reads.
        receipt = json.loads(json.dumps(receipt, allow_nan=False))
        _require(verify_context() is True)
        _require(type(receipt) is dict and set(receipt) == PATH_KEYS | {'KeptStopped','PreviouslyRunningServices'})
        _require(receipt['KeptStopped'] is True)
        services = receipt['PreviouslyRunningServices']
        _require(type(services) is list and all(type(s) is str and s in ('app','qdrant') for s in services))
        _require(len(services) == len(set(services)))
        _require(type(qdrant_volume) is str and re.fullmatch('[a-zA-Z0-9][a-zA-Z0-9_.-]*',qdrant_volume))
        root = _path(backup_root)
        _safe(root, directory=True)
        _require(all(type(receipt[key]) is str for key in PATH_KEYS))
        paths = {key: _path(receipt[key]) for key in PATH_KEYS}
        app = paths['Archive']
        _require(re.fullmatch(r'assistant-(?:\d{8}T\d{6}Z|week-\d{4}-W\d{2})\.tar\.gz',app.name))
        expected = dict(Checksum=Path(str(app)+'.sha256'),Metadata=Path(str(app)+'.meta'),
                        QdrantArchive=Path(str(app)+'.qdrant-volume.tar.gz'),
                        QdrantChecksum=Path(str(app)+'.qdrant-volume.tar.gz.sha256'))
        _require(all(paths[k] == p for k,p in expected.items()))
        for path in paths.values():
            _require(path != root and path.is_relative_to(root))
        with ExitStack() as stack:
            opened = {}
            for key,path in paths.items():
                before = _safe(path)
                _require(before.st_size > 0)
                if key not in ('Archive','QdrantArchive'):
                    _require(before.st_size <= SIDECAR_LIMIT)
                stream = stack.enter_context(path.open('rb'))
                _require(_identity(before) == _identity(os.fstat(stream.fileno())))
                opened[key] = stream,before
            hashes = {}
            for key in ('Archive','QdrantArchive'):
                stream,info = opened[key]
                remaining,digest = info.st_size,sha256()
                while remaining:
                    chunk = stream.read(min(1024*1024,remaining))
                    _require(bool(chunk))
                    remaining -= len(chunk)
                    digest.update(chunk)
                _require(stream.read(1) == b'')
                hashes[key] = digest.hexdigest()
            for archive_key,checksum_key in (('Archive','Checksum'),('QdrantArchive','QdrantChecksum')):
                raw = opened[checksum_key][0].read(SIDECAR_LIMIT+1)
                expected_line = f'{hashes[archive_key]}  {paths[archive_key].name}'.encode('ascii')
                _require(raw in (expected_line, expected_line+b'\n', expected_line+b'\r\n'))
            raw = opened['Metadata'][0].read(SIDECAR_LIMIT+1)
            _require(len(raw) <= SIDECAR_LIMIT)
            meta = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
            _require(type(meta) is dict and set(meta) == {'archive','sha256','created_at','kind','format',
                                                         'qdrant_volume_name','qdrant_archive','qdrant_sha256'})
            _require(type(meta['format']) is int and meta['format'] == 2 and meta['kind'] in ('daily','weekly'))
            _require(meta['kind'] == ('weekly' if app.name.startswith('assistant-week-') else 'daily'))
            _require(type(meta['created_at']) is str and datetime.fromisoformat(meta['created_at']).utcoffset() is not None)
            _require(meta['qdrant_volume_name'] == qdrant_volume)
            _require(meta['archive'] == app.name and meta['qdrant_archive'] == paths['QdrantArchive'].name)
            _require(meta['sha256'] == hashes['Archive'] and meta['qdrant_sha256'] == hashes['QdrantArchive'])
            _require(verify_context() is True)
            for key,(stream,before) in opened.items():
                _require(_identity(before) == _identity(os.fstat(stream.fileno())) == _identity(_safe(paths[key])))
            return dict(app_archive=str(app),app_sha256=hashes['Archive'],
                        qdrant_archive=str(paths['QdrantArchive']),qdrant_sha256=hashes['QdrantArchive'])
    except Exception:
        raise BackupEvidenceError('BACKUP_EVIDENCE_REJECTED') from None


def verify_backup_evidence(record, *, backup_root, qdrant_volume, verify_context):
    """Re-read artifacts and compare to an independently trusted journal record."""
    try:
        record = json.loads(json.dumps(record, allow_nan=False))
        _require(type(record) is dict and set(record) == RECORD_KEYS)
        app,qdrant = record['app_archive'],record['qdrant_archive']
        _require(type(app) is str and type(qdrant) is str)
        receipt = dict(Archive=app,Checksum=app+'.sha256',Metadata=app+'.meta',
                       QdrantArchive=qdrant,QdrantChecksum=qdrant+'.sha256',
                       KeptStopped=True,PreviouslyRunningServices=[])
        return build_backup_evidence(receipt, backup_root=backup_root, qdrant_volume=qdrant_volume,
                                     verify_context=verify_context) == record
    except Exception:
        return False
