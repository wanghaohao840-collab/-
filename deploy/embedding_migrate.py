"""Prepare a validated Qdrant embedding candidate without activating it."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

from app.rag_authority import checked_path
from app.rag_inventory import build_app_inventory
from evals.rag_embedding_acceptance import AcceptanceResult, inspect_report
from hello_agents.memory.rag.candidate_rebuild import (
    CandidateSummary,
    build_candidate,
    inspect_candidate,
    publish_qdrant_candidate,
)
from hello_agents.memory.rag.candidate_validation import (
    CandidateValidation,
    inspect_validation,
    validate_candidate,
)
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.source_inventory import (
    InventorySummary,
    build_inventory,
    inspect_inventory,
    iter_partitions,
)
from hello_agents.memory.rag.source_qdrant import QdrantChunkSource
from hello_agents.memory.rag.source_records import SourceInventoryError, canonical, digest
from hello_agents.memory.storage.vector_store import QdrantVectorStore


_MIGRATION_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


class MigrationFailure(RuntimeError):
    """Migration preparation failed without exposing source or credentials."""


@dataclass(frozen=True)
class MigrationEvidence:
    migration_id: str
    identity: dict[str, object]
    source_inventory: dict[str, object]
    candidate: dict[str, object]
    validation: dict[str, object]
    quality_file_digest: str
    quality_baseline: dict[str, object]
    quality_candidate: dict[str, object]
    created_at: str
    evidence_digest: str


def _file_digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(65536):
            hasher.update(block)
    return hasher.hexdigest()


def _write_evidence(path: Path, body: dict[str, object]) -> MigrationEvidence:
    body["evidence_digest"] = digest(["embedding-migration-evidence-v1", body])
    evidence = MigrationEvidence(**body)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(asdict(evidence)))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except (OSError, UnicodeError):
        raise MigrationFailure("evidence_write") from None
    finally:
        temporary.unlink(missing_ok=True)
    return evidence


def inspect_evidence(path: Path | str, *, expected_identity: IndexIdentity) -> MigrationEvidence:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        evidence = MigrationEvidence(**raw)
        body = dict(raw)
        fingerprint = body.pop("evidence_digest")
        if (
            evidence.identity != expected_identity.to_dict()
            or fingerprint != digest(["embedding-migration-evidence-v1", body])
            or CandidateSummary(**evidence.candidate).state != "published"
            or CandidateValidation(**evidence.validation).scope_leaks != 0
        ):
            raise ValueError()
        return evidence
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise MigrationFailure("evidence") from None


def prepare_qdrant_migration(
    data_root: Path | str,
    *,
    migration_id: str,
    base_collection: str,
    source_dimension: int,
    store,
    candidate_runtime,
    quality_report: Path | str,
) -> MigrationEvidence:
    root = Path(data_root)
    quality_path = Path(quality_report)
    if (
        not root.is_absolute()
        or not root.is_dir()
        or not _MIGRATION_ID.fullmatch(migration_id or "")
        or not isinstance(base_collection, str)
        or not base_collection
        or type(source_dimension) is not int
        or not 1 <= source_dimension <= 65536
        or not quality_path.is_absolute()
        or not quality_path.is_file()
    ):
        raise MigrationFailure("configuration")
    identity = IndexIdentity("qdrant", base_collection, candidate_runtime.profile)
    quality = inspect_report(quality_path, expected_fingerprint=identity.profile.fingerprint)
    root = checked_path(root, directory=True)
    registry_root = root / "vector_indexes" / "rag"
    inventory_root = registry_root / "inventories"
    migration_root = registry_root / "migrations" / migration_id
    inventory_root.mkdir(parents=True, exist_ok=True)
    migration_root.mkdir(parents=True, exist_ok=True)
    checked_path(inventory_root, directory=True)
    checked_path(migration_root, directory=True)
    inventory_path = inventory_root / f"{migration_id}-source.sqlite"
    checkpoint_path = migration_root / "candidate.sqlite"
    target_inventory_path = migration_root / "target.sqlite"
    validation_path = migration_root / "validation.json"
    evidence_path = migration_root / "evidence.json"

    def source():
        return QdrantChunkSource(
            store,
            collection=base_collection,
            dimension=source_dimension,
        )

    if inventory_path.exists():
        inventory = inspect_inventory(inventory_path)
    else:
        inventory = build_app_inventory(root, inventory_path, source=source())

    verification_number = 0

    def verify_live_source() -> InventorySummary:
        nonlocal verification_number
        while True:
            verification_number += 1
            verification_path = inventory_root / (
                f"{migration_id}-verify-{verification_number:04d}.sqlite"
            )
            if not verification_path.exists():
                break
        current = build_app_inventory(root, verification_path, source=source())
        if current != inventory:
            raise SourceInventoryError("source_changed")
        return current

    candidate = build_candidate(
        checkpoint_path,
        migration_id=migration_id,
        inventory_path=inventory_path,
        inventory=inventory,
        identity=identity,
        embedding=candidate_runtime,
        verify_live_source=verify_live_source,
        batch_size=candidate_runtime.batch_size,
    )
    candidate = publish_qdrant_candidate(
        checkpoint_path,
        expected=candidate,
        identity=identity,
        store=store,
    )
    def target_source():
        return QdrantChunkSource(
            store,
            collection=identity.physical_collection,
            dimension=identity.profile.dimension,
            identity=identity,
        )

    if validation_path.exists():
        validation = inspect_validation(validation_path)
        inspect_candidate(checkpoint_path, expected=candidate)
        verify_live_source()
        expected_target = inspect_inventory(target_inventory_path)
        target_verification_path = migration_root / (
            f"target-verify-{uuid.uuid4().hex}.sqlite"
        )
        owners = (
            (part.namespace, part.document_id, part.user_id)
            for part in iter_partitions(inventory_path, expected=inventory)
        )
        current_target = build_inventory(
            target_verification_path,
            source=target_source(),
            owners=owners,
        )
        if (
            current_target != expected_target
            or validation.target_fingerprint != expected_target.fingerprint
        ):
            raise SourceInventoryError("candidate_changed")
    else:
        validation = validate_candidate(
            validation_path,
            candidate_path=checkpoint_path,
            candidate=candidate,
            source_inventory_path=inventory_path,
            source_inventory=inventory,
            target_inventory_path=target_inventory_path,
            target_source=target_source(),
            verify_live_source=verify_live_source,
        )
    if evidence_path.exists():
        evidence = inspect_evidence(evidence_path, expected_identity=identity)
        if (
            evidence.quality_file_digest != _file_digest(quality_path)
            or evidence.quality_baseline != asdict(quality.baseline)
            or evidence.quality_candidate != asdict(quality.candidate)
            or evidence.candidate != asdict(candidate)
            or evidence.validation != asdict(validation)
            or evidence.source_inventory != asdict(inventory)
        ):
            raise MigrationFailure("evidence_changed")
        return evidence
    body = {
        "migration_id": migration_id,
        "identity": identity.to_dict(),
        "source_inventory": asdict(inventory),
        "candidate": asdict(candidate),
        "validation": asdict(validation),
        "quality_file_digest": _file_digest(quality_path),
        "quality_baseline": asdict(quality.baseline),
        "quality_candidate": asdict(quality.candidate),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    return _write_evidence(evidence_path, body)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare a validated RAG embedding migration")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--migration-id", required=True)
    parser.add_argument("--quality-report", required=True, type=Path)
    parser.add_argument("--source-dimension", type=int, default=384)
    args = parser.parse_args(argv)
    try:
        values = dict(os.environ)
        values["RAG_EMBEDDING_PROVIDER"] = "siliconflow"
        runtime = build_rag_embedding(values, backend="qdrant")
        base_collection = os.getenv("QDRANT_COLLECTION", "doc_learning_vectors").strip()
        url = os.getenv("QDRANT_URL", "").strip()
        if not url:
            raise MigrationFailure("configuration")
        evidence = prepare_qdrant_migration(
            args.data_root,
            migration_id=args.migration_id,
            base_collection=base_collection,
            source_dimension=args.source_dimension,
            store=QdrantVectorStore(url=url, api_key=os.getenv("QDRANT_API_KEY") or None),
            candidate_runtime=runtime,
            quality_report=args.quality_report,
        )
        print(canonical({
            "status": "validated",
            "migration_id": evidence.migration_id,
            "candidate_fingerprint": evidence.identity["fingerprint"],
            "chunk_count": evidence.validation["chunk_count"],
            "evidence_digest": evidence.evidence_digest,
        }))
        return 0
    except Exception as exc:
        allowed = {"configuration", "evidence", "evidence_write"}
        code = getattr(exc, "code", None)
        print(canonical({"status": "failed", "code": code if code in allowed else "migration"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
