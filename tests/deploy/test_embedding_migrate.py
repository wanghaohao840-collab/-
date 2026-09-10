from contextlib import closing
import json
from pathlib import Path
import sqlite3

import pytest

from deploy.embedding_migrate import (
    MigrationFailure,
    inspect_evidence,
    prepare_qdrant_migration,
)
from evals.rag_embedding_acceptance import compare_runtimes, write_report
from evals.rag_retrieval_quality import load_dataset
from hello_agents.memory.rag.embedding_runtime import build_rag_embedding
from hello_agents.memory.rag.index_identity import IndexIdentity
from hello_agents.memory.storage.vector_store import InMemoryVectorStore
from hello_agents.memory.storage.vector_scan import VectorScanPage


DATA = Path(__file__).parents[2] / "evals" / "data" / "bge_m3_retrieval.json"


class PerfectRuntime:
    def __init__(self, items):
        base = build_rag_embedding({"RAG_EMBEDDING_PROVIDER": "simple"}, backend="qdrant")
        self.profile = base.profile
        self.batch_size = base.batch_size
        self.documents = {item["content"]: index for index, item in enumerate(items)}
        self.questions = {item["question"]: index for index, item in enumerate(items)}

    def _vector(self, index):
        vector = [0.0] * self.profile.dimension
        vector[index] = 1.0
        return vector

    def embed_documents(self, texts):
        return [self._vector(self.documents[text]) for text in texts]

    def embed_query(self, text):
        return self._vector(self.questions[text])


class EmptyMigrationStore(InMemoryVectorStore):
    def iter_scroll_pages(self, collection_name, *, page_size, max_pages):
        yield VectorScanPage((), None)


def app_root(tmp_path: Path) -> Path:
    root = (tmp_path / "app").resolve()
    root.mkdir()
    with closing(sqlite3.connect(root / "app.db")) as database:
        database.execute("CREATE TABLE users(id TEXT PRIMARY KEY,status TEXT,password_hash TEXT)")
        database.commit()
    return root


def quality_report(tmp_path: Path, runtime) -> Path:
    items = load_dataset(DATA)
    result = compare_runtimes(items, runtime, runtime)
    path = (tmp_path / "quality.json").resolve()
    write_report(path, result)
    return path


def test_empty_qdrant_migration_is_validated_and_resumable(tmp_path: Path) -> None:
    root = app_root(tmp_path)
    runtime = PerfectRuntime(load_dataset(DATA))
    store = EmptyMigrationStore("docs", 384)

    first = prepare_qdrant_migration(
        root,
        migration_id="migration-1",
        base_collection="docs",
        source_dimension=384,
        store=store,
        candidate_runtime=runtime,
        quality_report=quality_report(tmp_path, runtime),
    )
    second = prepare_qdrant_migration(
        root,
        migration_id="migration-1",
        base_collection="docs",
        source_dimension=384,
        store=store,
        candidate_runtime=runtime,
        quality_report=(tmp_path / "quality.json").resolve(),
    )

    identity = IndexIdentity("qdrant", "docs", runtime.profile)
    assert first == second
    assert first.validation["chunk_count"] == 0
    assert store.count(identity.physical_collection) == 0
    assert inspect_evidence(
        root / "vector_indexes" / "rag" / "migrations" / "migration-1" / "evidence.json",
        expected_identity=identity,
    ) == first


def test_evidence_tampering_fails_closed(tmp_path: Path) -> None:
    root = app_root(tmp_path)
    runtime = PerfectRuntime(load_dataset(DATA))
    store = EmptyMigrationStore("docs", 384)
    prepare_qdrant_migration(
        root,
        migration_id="migration-1",
        base_collection="docs",
        source_dimension=384,
        store=store,
        candidate_runtime=runtime,
        quality_report=quality_report(tmp_path, runtime),
    )
    path = root / "vector_indexes" / "rag" / "migrations" / "migration-1" / "evidence.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    body["validation"]["chunk_count"] = 1
    path.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(MigrationFailure, match="evidence"):
        inspect_evidence(path, expected_identity=IndexIdentity("qdrant", "docs", runtime.profile))
