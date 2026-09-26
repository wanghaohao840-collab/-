import hashlib
import io
import json
import sqlite3
import tarfile

import pytest

from deploy.inventory_paired_backup import inventory_pair


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _add_file(archive, name, content):
    member = tarfile.TarInfo(name)
    member.size = len(content)
    archive.addfile(member, io.BytesIO(content))


def _pair(tmp_path, *, unsafe=False, wal=False):
    db_path = tmp_path / "source.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("create table users (id text primary key, username text not null)")
        conn.execute("insert into users values (?, ?)", ("user-id", "private-name"))
    conn.close()
    app_archive = tmp_path / "paired.tar.gz"
    with tarfile.open(app_archive, "w:gz") as archive:
        _add_file(archive, "./app/app.db", db_path.read_bytes())
        _add_file(archive, "./app/users/user-id/documents/doc.txt", b"content")
        if wal:
            _add_file(archive, "./app/app.db-wal", b"uncheckpointed")
        if unsafe:
            link = tarfile.TarInfo("./app/users/user-id/documents/link")
            link.type = tarfile.SYMTYPE
            link.linkname = "../../../../outside"
            archive.addfile(link)
    qdrant_archive = tmp_path / "paired.qdrant.tar.gz"
    with tarfile.open(qdrant_archive, "w:gz") as archive:
        _add_file(archive, "./collections/doc_learning_vectors/config.json", b"{}")
    metadata = {
        "archive": app_archive.name,
        "sha256": _hash(app_archive),
        "qdrant_archive": qdrant_archive.name,
        "qdrant_sha256": _hash(qdrant_archive),
    }
    (tmp_path / "paired.tar.gz.meta").write_text(json.dumps(metadata), encoding="utf-8")
    return app_archive


def test_inventory_verifies_pair_and_business_contents(tmp_path):
    result = inventory_pair(_pair(tmp_path))

    assert result["sqlite_integrity"] == "ok"
    assert result["tables"]["users"]["rows"] == 1
    assert result["user_file_count"] == 1
    assert result["user_file_bytes"] == 7
    assert result["files"][0]["sha256"] == hashlib.sha256(b"content").hexdigest()


def test_inventory_refuses_changed_archive(tmp_path):
    app_archive = _pair(tmp_path)
    with app_archive.open("ab") as stream:
        stream.write(b"changed")

    with pytest.raises(ValueError, match="hash mismatch"):
        inventory_pair(app_archive)


def test_inventory_refuses_archive_links(tmp_path):
    with pytest.raises(ValueError, match="link or unsupported"):
        inventory_pair(_pair(tmp_path, unsafe=True))


def test_inventory_refuses_uncheckpointed_source(tmp_path):
    with pytest.raises(ValueError, match="uncheckpointed"):
        inventory_pair(_pair(tmp_path, wal=True))
