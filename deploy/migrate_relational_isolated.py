"""Copy frozen SQLite relational data into an explicitly migrated test schema."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql


TABLES = (
    "users", "report_records", "import_batches", "import_tasks",
    "qa_conversations", "qa_conversation_documents", "qa_messages",
    "qa_retry_requests", "qa_message_sources", "qa_jobs",
    "qa_deletion_fences", "qa_legacy_imports", "notes", "note_tags",
    "note_sources", "note_document_sources", "note_projection_tasks",
    "note_legacy_imports", "data_migrations", "learning_plans",
    "learning_plan_documents", "learning_tasks", "learning_requests",
    "learning_task_events", "learning_migrations",
)
FTS_TABLES = {
    "notes_fts", "notes_fts_data", "notes_fts_idx", "notes_fts_content",
    "notes_fts_docsize", "notes_fts_config",
}
REVISION = "20260926_01"


class MigrationError(RuntimeError):
    def __init__(self, message: str, evidence: dict | None = None):
        super().__init__(message)
        self.evidence = evidence


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value):
    if value is None:
        return ["null"]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["integer", str(value)]
    if isinstance(value, float):
        return ["real", value.hex()]
    raise MigrationError("Unsupported database value type")


def _fingerprint(rows) -> str:
    hashes = sorted(hashlib.sha256(json.dumps(
        [_canonical(value) for value in row], ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")).digest() for row in rows)
    digest = hashlib.sha256()
    for row_hash in hashes:
        digest.update(row_hash)
    return digest.hexdigest()


def _reject_source_journals(path: Path) -> None:
    for suffix in ("-wal", "-journal", "-shm"):
        if Path(str(path) + suffix).exists():
            raise MigrationError("Source has live SQLite journal siblings")


def _source(path: Path, expected_sha256: str):
    # The caller must keep the source frozen throughout the command. Rechecks
    # detect common violations; immutable SQLite is not a writer coordination API.
    if not path.is_file():
        raise MigrationError("Source SQLite file does not exist")
    _reject_source_journals(path)
    actual = _digest_file(path)
    if len(expected_sha256) != 64 or actual != expected_sha256.lower():
        raise MigrationError("Source SHA-256 mismatch")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if connection.execute("pragma integrity_check").fetchall() != [("ok",)]:
            raise MigrationError("SQLite integrity check failed")
        if connection.execute("pragma foreign_key_check").fetchone() is not None:
            raise MigrationError("SQLite foreign key check failed")
        tables = {row[0] for row in connection.execute(
            "select name from sqlite_master where type='table'"
        ) if not row[0].startswith("sqlite_")}
        if tables - set(TABLES) - FTS_TABLES or set(TABLES) - tables:
            raise MigrationError("SQLite business table baseline mismatch")
        columns = {}
        rows = {}
        for table in TABLES:
            columns[table] = tuple(row[1] for row in connection.execute(
                f'pragma table_info("{table}")'
            ))
            rows[table] = connection.execute(f'select * from "{table}"').fetchall()
        if _digest_file(path) != actual:
            raise MigrationError("Source changed during inspection")
        _reject_source_journals(path)
        return actual, columns, rows
    finally:
        connection.close()


def _ordered_messages(rows, columns):
    """Insert retry parents first, regardless of SQLite row order."""
    id_index = columns.index("id")
    parent_index = columns.index("retry_of_message_id")
    by_id = {row[id_index]: row for row in rows}
    if len(by_id) != len(rows):
        raise MigrationError("Duplicate QA message IDs")
    ordered, visiting, visited = [], set(), set()

    def visit(message_id):
        if message_id in visited:
            return
        if message_id in visiting:
            raise MigrationError("Cyclic QA retry references")
        visiting.add(message_id)
        row = by_id[message_id]
        parent = row[parent_index]
        if parent is not None:
            if parent not in by_id:
                raise MigrationError("Missing QA retry parent")
            visit(parent)
        visiting.remove(message_id)
        visited.add(message_id)
        ordered.append(row)

    for message_id in by_id:
        visit(message_id)
    return ordered


def _target_rows(cursor, schema, columns):
    rows = {}
    for table in TABLES:
        cursor.execute(sql.SQL("select {} from {}").format(
            sql.SQL(", ").join(map(sql.Identifier, columns[table])),
            sql.Identifier(schema, table),
        ))
        rows[table] = cursor.fetchall()
    return rows


def _evidence(mode, source_sha, source_rows, target_rows, inserted=None):
    tables, discrepancies = {}, []
    for table in TABLES:
        source_hash = _fingerprint(source_rows[table])
        target_hash = _fingerprint(target_rows[table])
        tables[table] = {
            "source_count": len(source_rows[table]), "source_sha256": source_hash,
            "target_count": len(target_rows[table]), "target_sha256": target_hash,
            "inserted_count": (inserted or {}).get(table, 0),
        }
        if len(source_rows[table]) != len(target_rows[table]) or source_hash != target_hash:
            discrepancies.append(table)
    return {"status": "equal" if not discrepancies else "different", "mode": mode,
            "source_sha256": source_sha, "tables": tables,
            "discrepancies": discrepancies,
            "inserted_row_counts": {table: (inserted or {}).get(table, 0) for table in TABLES}}


def migrate_relational(source_path: Path, *, expected_sha256: str, database_url: str,
                       target_schema: str, mode: str) -> dict:
    if mode not in {"dry-run", "apply", "verify"}:
        raise MigrationError("Invalid migration mode")
    if (len(target_schema) <= len("cutover_") or
            not target_schema.startswith("cutover_") or
            not target_schema.replace("_", "").isalnum()):
        raise MigrationError("Target must be an isolated cutover_ schema")
    source_sha, columns, source_rows = _source(Path(source_path), expected_sha256)
    try:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("select exists(select 1 from pg_catalog.pg_namespace where nspname=%s)", (target_schema,))
                if not cursor.fetchone()[0]:
                    raise MigrationError("Target schema does not exist")
                cursor.execute("select tablename from pg_catalog.pg_tables where schemaname=%s", (target_schema,))
                if {row[0] for row in cursor.fetchall()} != set(TABLES) | {"alembic_version"}:
                    raise MigrationError("Target business table baseline mismatch")
                cursor.execute(sql.SQL("select version_num from {}").format(sql.Identifier(target_schema, "alembic_version")))
                if cursor.fetchall() != [(REVISION,)]:
                    raise MigrationError("Target schema has not been explicitly migrated")
                cursor.execute("select table_name,column_name from information_schema.columns where table_schema=%s order by ordinal_position", (target_schema,))
                target_columns = {}
                for table, column in cursor.fetchall():
                    target_columns.setdefault(table, []).append(column)
                for table in TABLES:
                    if set(columns[table]) != set(target_columns.get(table, [])) or len(columns[table]) != len(target_columns[table]):
                        raise MigrationError("Source and target business columns differ")
                cursor.execute(sql.SQL("lock table {} in share mode").format(
                    sql.SQL(", ").join(sql.Identifier(target_schema, table) for table in TABLES)
                ))
                before = _target_rows(cursor, target_schema, columns)
                evidence = _evidence(mode, source_sha, source_rows, before)
                equal = not evidence["discrepancies"]
                empty = all(not before[table] for table in TABLES)
                if mode == "dry-run":
                    evidence["status"] = "equal" if equal else "ready" if empty else "mismatch"
                    return evidence
                if mode == "verify":
                    if not equal:
                        raise MigrationError("Target differs from source", evidence)
                    return evidence
                if equal:
                    evidence["status"] = "unchanged"
                    return evidence
                if not empty:
                    raise MigrationError("Nonempty target differs from source", evidence)
                inserted = {}
                for table in TABLES:
                    rows = source_rows[table]
                    if table == "qa_messages":
                        rows = _ordered_messages(rows, columns[table])
                    statement = sql.SQL("insert into {} ({}) values ({})").format(
                        sql.Identifier(target_schema, table),
                        sql.SQL(", ").join(map(sql.Identifier, columns[table])),
                        sql.SQL(", ").join(sql.Placeholder() for _ in columns[table]),
                    )
                    if rows:
                        cursor.executemany(statement, rows)
                    inserted[table] = len(rows)
                after = _target_rows(cursor, target_schema, columns)
                evidence = _evidence(mode, source_sha, source_rows, after, inserted)
                if evidence["discrepancies"]:
                    raise MigrationError("Post-copy verification failed", evidence)
                # PostgreSQL sequences are not transactional; change it only after
                # every row and content check has passed.
                cursor.execute(sql.SQL("select setval(pg_get_serial_sequence(%s, 'id'), coalesce((select max(id) from {}), 1), %s)").format(
                    sql.Identifier(target_schema, "data_migrations")
                ), (f'"{target_schema}"."data_migrations"', bool(source_rows["data_migrations"])))
                evidence["status"] = "applied"
                return evidence
    except psycopg.Error as exc:
        raise MigrationError("PostgreSQL operation failed: " + type(exc).__name__) from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--target-schema", required=True)
    parser.add_argument("--mode", choices=("dry-run", "apply", "verify"), required=True)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args(argv)
    database_url = os.environ.get("CUTOVER_TEST_DATABASE_URL")
    if not database_url:
        parser.error("CUTOVER_TEST_DATABASE_URL is required")
    try:
        result = migrate_relational(args.source, expected_sha256=args.expected_sha256,
                                    database_url=database_url, target_schema=args.target_schema,
                                    mode=args.mode)
    except (MigrationError, sqlite3.Error) as exc:
        if isinstance(exc, sqlite3.Error):
            exc = MigrationError("SQLite operation failed: " + type(exc).__name__)
        result = exc.evidence or {"status": "failed", "mode": args.mode,
                                  "discrepancies": [], "error": str(exc)}
        print("Relational migration check failed: " + str(exc), file=sys.stderr)
        exit_code = 1
    else:
        exit_code = 1 if result["status"] == "mismatch" else 0
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
