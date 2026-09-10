from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.database import connect, initialize_database
from app.learning_source import LegacySourceError, read_legacy_source
import app.learning_source as reader


@pytest.fixture
def setup(tmp_path):
    user = str(uuid4())
    initialize_database(tmp_path / 'app.db')
    with connect(tmp_path / 'app.db') as db:
        db.execute('insert into users values (?,?,?,?,?,?,?)', (user,user,user,'hash','active','now','now'))
    return tmp_path, user, tmp_path / 'users' / user / 'learning.json'


def test_missing_source_does_not_create_dirs(setup):
    root, user, _ = setup
    assert read_legacy_source(root, user_id=user) is None
    assert not (root / 'users').exists()


@pytest.mark.parametrize('_attempt', range(10))
def test_preserves_exact_bytes(setup, _attempt):
    root, user, source = setup
    source.parent.mkdir(parents=True)
    payload = b'{ "version": 1 }\n'
    source.write_bytes(payload)
    before = source.stat()
    assert read_legacy_source(root, user_id=user) == payload
    assert source.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize('user', ['../escape', '', 'bad', str(uuid4())])
def test_invalid_or_unknown_user(setup, user):
    with pytest.raises(LegacySourceError):
        read_legacy_source(setup[0], user_id=user)
    assert not (setup[0] / 'users').exists()


def test_missing_registry_not_created(tmp_path):
    with pytest.raises(LegacySourceError):
        read_legacy_source(tmp_path, user_id=str(uuid4()))
    assert list(tmp_path.iterdir()) == []


def test_inactive_user(setup):
    root, user, _ = setup
    with connect(root / 'app.db') as db:
        db.execute("update users set status='disabled' where id=?", (user,))
    with pytest.raises(LegacySourceError, match='USER_UNAVAILABLE'):
        read_legacy_source(root, user_id=user)


@pytest.mark.parametrize('kind', ['directory', 'large'])
def test_bad_source(setup, kind):
    root, user, source = setup
    source.parent.mkdir(parents=True)
    if kind == 'directory':
        source.mkdir()
    else:
        source.write_bytes(b' ' * (16*1024*1024+1))
    with pytest.raises(LegacySourceError):
        read_legacy_source(root, user_id=user)


def test_windows_reparse_metadata_rejected():
    with pytest.raises(LegacySourceError, match='UNSAFE_PATH'):
        reader._reject_link(SimpleNamespace(st_mode=0, st_file_attributes=1024))


def test_changed_descriptor_rejected(setup, monkeypatch):
    root, user, source = setup
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{}')
    original = reader.os.fstat
    calls = 0
    def changed(fd):
        nonlocal calls
        calls += 1
        info = original(fd)
        if calls == 2:
            values = {name: getattr(info, name) for name in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}
            values['st_size'] += 1
            return SimpleNamespace(**values)
        return info
    monkeypatch.setattr(reader.os, 'fstat', changed)
    with pytest.raises(LegacySourceError, match='SOURCE_CHANGED'):
        read_legacy_source(root, user_id=user)


def test_cross_query_ctime_difference_is_not_a_file_change(setup, monkeypatch):
    root, user, source = setup
    source.parent.mkdir(parents=True)
    source.write_bytes(b'{}')
    original = reader.os.fstat
    def descriptor(fd):
        info = original(fd)
        values = {name: getattr(info, name) for name in ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns')}
        values['st_ctime_ns'] = 123
        return SimpleNamespace(**values)
    monkeypatch.setattr(reader.os, 'fstat', descriptor)
    assert read_legacy_source(root, user_id=user) == b'{}'
