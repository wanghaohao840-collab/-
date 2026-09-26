"""Read-only inventory of a stopped-write app/Qdrant backup pair."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import tarfile
from contextlib import closing
from pathlib import Path, PurePosixPath


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_name(member: tarfile.TarInfo) -> str:
    path = PurePosixPath(member.name)
    if path.is_absolute() or "\\" in member.name or ":" in member.name or ".." in path.parts:
        raise ValueError("backup contains an unsafe path")
    if not member.isfile() and not member.isdir():
        raise ValueError("backup contains a link or unsupported member")
    return str(path).removeprefix("./")


def _sqlite_tables(database_bytes: bytes) -> dict[str, dict[str, object]]:
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.deserialize(database_bytes)
        connection.execute("pragma query_only = on")
        integrity = connection.execute("pragma integrity_check").fetchall()
        if integrity != [("ok",)] or connection.execute("pragma foreign_key_check").fetchall():
            raise ValueError("backup SQLite integrity or foreign key check failed")
        table_names = [
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' order by name"
            )
        ]
        result: dict[str, dict[str, object]] = {}
        for table in table_names:
            quoted = '"' + table.replace('"', '""') + '"'
            rows = connection.execute(f"select * from {quoted}")
            row_hashes = []
            for row in rows:
                values = [
                    [type(value).__name__, value.hex() if isinstance(value, bytes) else value]
                    for value in row
                ]
                payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
                row_hashes.append(hashlib.sha256(payload.encode("utf-8")).hexdigest())
            digest = hashlib.sha256("".join(sorted(row_hashes)).encode("ascii"))
            result[table] = {"rows": len(row_hashes), "sha256": digest.hexdigest()}
        return result


def inventory_pair(app_archive: Path) -> dict[str, object]:
    """Validate pair hashes and inventory user files and SQLite rows without extraction."""

    app_archive = app_archive.resolve()
    metadata = json.loads(Path(str(app_archive) + ".meta").read_text(encoding="utf-8"))
    if metadata.get("archive") != app_archive.name:
        raise ValueError("backup metadata does not name the application archive")
    qdrant_name = metadata.get("qdrant_archive")
    if not isinstance(qdrant_name, str) or Path(qdrant_name).name != qdrant_name:
        raise ValueError("backup metadata has an unsafe Qdrant archive name")
    qdrant_archive = app_archive.parent / qdrant_name
    app_hash = _sha256(app_archive)
    qdrant_hash = _sha256(qdrant_archive)
    if app_hash != metadata.get("sha256") or qdrant_hash != metadata.get("qdrant_sha256"):
        raise ValueError("paired backup hash mismatch")

    qdrant_collections: set[str] = set()
    with tarfile.open(qdrant_archive, "r:gz") as archive:
        for member in archive:
            parts = PurePosixPath(_safe_name(member)).parts
            if len(parts) >= 3 and parts[0] == "collections" and parts[2] == "config.json":
                qdrant_collections.add(parts[1])
    if not qdrant_collections:
        raise ValueError("paired Qdrant archive has no collection configuration")

    seen: set[str] = set()
    files: list[dict[str, object]] = []
    database_bytes: bytes | None = None
    with tarfile.open(app_archive, "r:gz") as archive:
        for member in archive:
            name = _safe_name(member)
            if name in seen and member.isfile():
                raise ValueError("backup contains a duplicate file")
            seen.add(name)
            if not member.isfile():
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("backup file could not be read")
            content = stream.read()
            if len(content) != member.size:
                raise ValueError("backup file size mismatch")
            if name in {"app/app.db-wal", "app/app.db-journal"} and content:
                raise ValueError("backup SQLite has an uncheckpointed journal")
            if name == "app/app.db":
                database_bytes = content
            elif name.startswith("app/users/"):
                files.append(
                    {
                        "path": name,
                        "bytes": member.size,
                        "sha256": hashlib.sha256(content).hexdigest(),
                    }
                )
    if database_bytes is None:
        raise ValueError("backup has no app/app.db")
    files.sort(key=lambda item: str(item["path"]))
    return {
        "source_archive": app_archive.name,
        "source_sha256": app_hash,
        "qdrant_archive": qdrant_archive.name,
        "qdrant_sha256": qdrant_hash,
        "qdrant_collections": sorted(qdrant_collections),
        "sqlite_sha256": hashlib.sha256(database_bytes).hexdigest(),
        "sqlite_integrity": "ok",
        "tables": _sqlite_tables(database_bytes),
        "files": files,
        "user_file_count": len(files),
        "user_file_bytes": sum(int(item["bytes"]) for item in files),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inventory_pair(args.app_archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"inventory ok: {len(result['tables'])} tables, "
        f"{result['user_file_count']} user files"
    )


if __name__ == "__main__":
    main()
