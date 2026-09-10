"""CLI for journaled embedding registry cutover; never edits secrets."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

from deploy.embedding_migrate import inspect_evidence
from deploy.embedding_probe import read_values
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_cutover import (
    apply_cutover,
    begin_cutover,
    complete_cutover,
    inspect_cutover,
    rollback_cutover,
)
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import IndexRecord, IndexRegistry, IndexValidation
from hello_agents.memory.rag.source_records import canonical


def _load_evidence(path: Path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        identity = IndexIdentity.from_dict(raw["identity"])
    except Exception:
        raise RuntimeError("evidence") from None
    return inspect_evidence(path, expected_identity=identity), identity


def candidate_from_evidence(path: Path | str) -> IndexRecord:
    evidence, identity = _load_evidence(Path(path))
    validation = evidence.validation
    quality = evidence.quality_candidate
    if (
        validation.get("candidate_fingerprint") != evidence.candidate.get("fingerprint")
        or validation.get("chunk_count") != evidence.source_inventory.get("count")
        or quality.get("cases") != 20
    ):
        raise RuntimeError("evidence")
    return IndexRecord(
        identity=identity,
        source_index=identity.base_collection,
        migration_id=evidence.migration_id,
        validation=IndexValidation(
            passed=True,
            checked_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            chunk_count=validation["chunk_count"],
            content_digest=validation["content_digest"],
            scope_leaks=quality["scope_leaks"],
            recall_at_5=quality["recall_at_5"],
            mrr_at_5=quality["mrr_at_5"],
        ),
        state="active",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage an embedding index cutover journal")
    parser.add_argument("command", choices=("begin", "apply", "complete", "rollback", "status"))
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    try:
        registry = IndexRegistry(args.data_root)
        if args.command == "begin":
            if args.evidence is None:
                raise RuntimeError("evidence")
            candidate = candidate_from_evidence(args.evidence)
            if candidate.migration_id != args.migration_id:
                raise RuntimeError("migration_id")
            journal = begin_cutover(registry, candidate)
        elif args.command == "apply":
            journal = apply_cutover(registry, args.migration_id)
        elif args.command == "complete":
            if args.env_file is None:
                raise RuntimeError("environment_file")
            journal = inspect_cutover(registry, args.migration_id)
            candidate = IndexRecord.from_dict(journal.candidate)
            values = read_values(args.env_file)
            runtime = build_rag_embedding(values, backend=candidate.identity.backend)
            expected = IndexIdentity(
                candidate.identity.backend,
                candidate.identity.base_collection,
                runtime.profile,
            )
            if expected != candidate.identity:
                raise RuntimeError("environment_identity")
            journal = complete_cutover(registry, args.migration_id)
        elif args.command == "rollback":
            journal = rollback_cutover(registry, args.migration_id)
        else:
            journal = inspect_cutover(registry, args.migration_id)
        print(canonical({
            "status": journal.state,
            "migration_id": journal.migration_id,
            "candidate_fingerprint": journal.candidate["identity"]["fingerprint"],
        }))
        return 0
    except Exception as exc:
        code = getattr(exc, "code", None)
        safe = {
            "already_active", "candidate", "concurrent_change", "environment_file",
            "environment_identity", "evidence", "journal", "journal_exists",
            "migration_id", "rollback_requires_data_check", "transition",
        }
        print(canonical({"status": "failed", "code": code if code in safe else "cutover"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
