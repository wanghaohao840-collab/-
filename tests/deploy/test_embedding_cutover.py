from dataclasses import asdict
import json
from pathlib import Path

from deploy.embedding_cutover import candidate_from_evidence, main
from deploy.embedding_migrate import MigrationEvidence
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_cutover import inspect_cutover
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.rag.index_registry import IndexRegistry
from hello_agents.memory.rag.source_records import canonical, digest


def evidence_file(tmp_path: Path) -> tuple[Path, IndexIdentity]:
    runtime = build_rag_embedding(
        {
            "RAG_EMBEDDING_PROVIDER": "siliconflow",
            "RAG_EMBEDDING_API_KEY": "fake-key",
        },
        backend="qdrant",
    )
    identity = IndexIdentity("qdrant", "docs", runtime.profile)
    body = {
        "migration_id": "migration-1",
        "identity": identity.to_dict(),
        "source_inventory": {"count": 0, "partitions": 0, "fingerprint": "b" * 64},
        "candidate": {
            "migration_id": "migration-1", "state": "published", "embedded_count": 0,
            "published_count": 0, "total_count": 0, "fingerprint": "c" * 64,
        },
        "validation": {
            "candidate_fingerprint": "c" * 64, "source_fingerprint": "b" * 64,
            "target_fingerprint": "d" * 64, "chunk_count": 0, "partition_count": 0,
            "content_digest": "e" * 64, "scope_leaks": 0, "fingerprint": "f" * 64,
        },
        "quality_file_digest": "1" * 64,
        "quality_baseline": {"cases": 20, "recall_at_5": 0.5, "mrr_at_5": 0.2, "scope_leaks": 0},
        "quality_candidate": {"cases": 20, "recall_at_5": 1.0, "mrr_at_5": 1.0, "scope_leaks": 0},
        "created_at": "2026-09-03T12:00:00Z",
    }
    body["evidence_digest"] = digest(["embedding-migration-evidence-v1", body])
    path = (tmp_path / "evidence.json").resolve()
    path.write_text(canonical(body), encoding="utf-8")
    return path, identity


def test_cli_begin_apply_complete_requires_matching_environment(tmp_path: Path, capsys) -> None:
    evidence, identity = evidence_file(tmp_path)
    root = (tmp_path / "data").resolve()
    root.mkdir()
    env_file = (tmp_path / ".env").resolve()
    env_file.write_text(
        "RAG_EMBEDDING_PROVIDER=siliconflow\n"
        "RAG_EMBEDDING_API_KEY=fake-key\n",
        encoding="utf-8",
    )

    assert main(["begin", "--data-root", str(root), "--migration-id", "migration-1", "--evidence", str(evidence)]) == 0
    assert main(["apply", "--data-root", str(root), "--migration-id", "migration-1"]) == 0
    assert main(["complete", "--data-root", str(root), "--migration-id", "migration-1", "--env-file", str(env_file)]) == 0

    registry = IndexRegistry(root)
    assert registry.require_active(identity).state == "active"
    assert inspect_cutover(registry, "migration-1").state == "complete"
    assert "fake-key" not in capsys.readouterr().out


def test_candidate_record_uses_quality_and_structure_evidence(tmp_path: Path) -> None:
    path, identity = evidence_file(tmp_path)

    record = candidate_from_evidence(path)

    assert record.identity == identity
    assert record.validation.chunk_count == 0
    assert record.validation.recall_at_5 == 1.0
