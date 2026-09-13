"""Read-only restored App bytes proof, not Qdrant or whole recovery acceptance.

Caller supplies a journal-approved trusted local archive, root namespace, lock
and ACL protection. Stat checks are change detection, not OS write exclusion.
tarfile metadata parsing is not a hostile-input sandbox. No extraction occurs.
"""
from hashlib import sha256
import os
from pathlib import Path
import re
import stat
import tarfile
import json

MAX_MEMBERS=100000
MAX_BYTES=1024**4


def _require(value):
    if not value: raise ValueError


def _safe(path):
    for part in (*reversed(path.parents),path):
        info=part.lstat()
        _require(not stat.S_ISLNK(info.st_mode) and not getattr(info,'st_file_attributes',0)&1024)
        _require(stat.S_ISDIR(info.st_mode) if part!=path else
                 stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink==1))
    return info


def _identity(info):
    return info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_nlink,stat.S_IFMT(info.st_mode)


def _digest(stream,size):
    _require(0<=size<=MAX_BYTES)
    digest=sha256()
    while size:
        chunk=stream.read(min(1024*1024,size))
        _require(bool(chunk));size-=len(chunk);digest.update(chunk)
    _require(stream.read(1)==b'')
    return digest.hexdigest()


def _tree(root):
    _require(stat.S_ISDIR(_safe(root).st_mode))
    found={}
    pending=[root]
    while pending:
        directory=pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path=Path(entry.path)
                info=_safe(path)
                name=path.relative_to(root).as_posix()
                found[name]=_identity(info)
                _require(len(found)<=MAX_MEMBERS)
                if stat.S_ISDIR(info.st_mode): pending.append(path)
    return found


def _member_name(name):
    _require(type(name) is str and '\\' not in name and ':' not in name and '\x00' not in name)
    while name.startswith('./'): name=name[2:]
    name=name.rstrip('/')
    if name in ('','.'): return ''
    _require(not name.startswith('/') and all(p not in ('','..','.') for p in name.split('/')))
    return name


def verify_restored_tree(data_root,archive,*,expected_sha256,verify_context,verify_stopped,required_directories=('app','qdrant')):
    try:
        root,archive=Path(data_root),Path(archive)
        _require(root.is_absolute() and archive.is_absolute() and '..' not in root.parts and '..' not in archive.parts)
        _require(not archive.is_relative_to(root))
        _require(type(expected_sha256) is str and re.fullmatch('[0-9a-f]{64}',expected_sha256))
        _require(verify_context() is True and verify_stopped() is True)
        before=_tree(root)
        archive_info=_safe(archive)
        _require(stat.S_ISREG(archive_info.st_mode))
        with archive.open('rb') as source:
            _require(_identity(archive_info)==_identity(os.fstat(source.fileno())))
            _require(_digest(source,archive_info.st_size)==expected_sha256)
            source.seek(0)
            names=set();seen=set();total=0
            with tarfile.open(fileobj=source,mode='r|gz') as tar:
                for member in tar:
                    name=_member_name(member.name)
                    _require(name not in seen and len(seen)<MAX_MEMBERS)
                    seen.add(name)
                    _require((member.isdir() or member.isfile()) and not member.issparse())
                    if not name:
                        _require(member.isdir());continue
                    _require(name in before)
                    names.add(name)
                    path=root.joinpath(*name.split('/'))
                    info=_safe(path)
                    if member.isdir():
                        _require(stat.S_ISDIR(info.st_mode));continue
                    _require(stat.S_ISREG(info.st_mode) and info.st_size==member.size)
                    total+=member.size;_require(total<=MAX_BYTES)
                    with tar.extractfile(member) as archived, path.open('rb') as restored:
                        _require(_identity(info)==_identity(os.fstat(restored.fileno())))
                        _require(_digest(archived,member.size)==_digest(restored,info.st_size))
                        _require(_identity(info)==_identity(os.fstat(restored.fileno())))
            _require(names==set(before))
            _require(all(name in before and before[name][-1]==stat.S_IFDIR for name in required_directories))
            _require(verify_context() is True and verify_stopped() is True)
            _require(_tree(root)==before)
            source.seek(0)
            _require(_digest(source,archive_info.st_size)==expected_sha256)
            _require(_identity(archive_info)==_identity(os.fstat(source.fileno()))==_identity(_safe(archive)))
        return True
    except Exception:
        return False


def verify_restored_app(data_root,state_root,operation_id,*,verify_context,verify_stopped,verify_backup):
    """Bind App-only proof to a validated recovering journal; never release it."""
    from deploy.learning_maintenance import read_journal
    try:
        state=read_journal(state_root,operation_id)
        if state.phase!='recovering' or state.backup_json is None:
            return False
        backup=json.loads(state.backup_json)
        def guard():
            return (read_journal(state_root,operation_id).record_sha256==state.record_sha256 and
                    verify_context() is True and
                    read_journal(state_root,operation_id).record_sha256==state.record_sha256)
        if not guard() or verify_stopped() is not True or verify_backup(dict(backup)) is not True:
            return False
        return verify_restored_tree(data_root,backup['app_archive'],expected_sha256=backup['app_sha256'],
                                    verify_context=guard,verify_stopped=verify_stopped)
    except Exception:
        return False


if __name__=='__main__':
    # Isolated helper only: operational authorization is established by its host.
    import sys
    if len(sys.argv)!=4:
        sys.exit(2)
    result=verify_restored_tree(sys.argv[1],sys.argv[2],expected_sha256=sys.argv[3],
        verify_context=lambda:True,verify_stopped=lambda:True,required_directories=())
    print(json.dumps(dict(verified=result,archive_sha256=sys.argv[3]),separators=(',',':')))
    sys.exit(0 if result else 1)
