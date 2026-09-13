import hashlib
import io
import tarfile

import pytest

from deploy.verify_isolated_restore import checked_archive


def archive(tmp_path, member):
    path = tmp_path / 'backup.tar.gz'
    with tarfile.open(path, 'w:gz') as target:
        target.addfile(member, io.BytesIO(b'x') if member.isfile() else None)
    path.with_name(path.name + '.sha256').write_text(hashlib.sha256(path.read_bytes()).hexdigest())
    return path


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/escape', 'app/../../escape'])
def test_rejects_archive_path_escape(tmp_path, name):
    member = tarfile.TarInfo(name)
    member.size = 1
    with pytest.raises(ValueError, match='Unsafe'):
        checked_archive(archive(tmp_path, member))


def test_rejects_archive_link(tmp_path):
    member = tarfile.TarInfo('app/link')
    member.type = tarfile.SYMTYPE
    member.linkname = '../../production'
    with pytest.raises(ValueError, match='Unsafe'):
        checked_archive(archive(tmp_path, member))


def test_checks_checksum_before_extracting(tmp_path):
    member = tarfile.TarInfo('app/document')
    member.size = 1
    path = archive(tmp_path, member)
    assert checked_archive(path)[0] == path.resolve()
    path.write_bytes(path.read_bytes() + b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        checked_archive(path)
