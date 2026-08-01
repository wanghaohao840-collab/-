from hello_agents.memory.rag.pipeline import SimpleRAGPipeline
from hello_agents.memory.rag.result_utils import (
    hybrid_rank_results,
    lexical_overlap_score,
    mmr_select,
)


def test_lexical_overlap_supports_word_and_cjk_tokens():
    assert lexical_overlap_score("alpha beta", "alpha appears") == 0.5
    assert lexical_overlap_score("你好世界", "世界和平") == 0.5


def test_hybrid_rank_can_recall_lexical_candidate_outside_vector_top_k(tmp_path):
    pipeline = SimpleRAGPipeline(
        rag_namespace="hybrid-test",
        cache_path=str(tmp_path / "hybrid.json"),
    )
    pipeline.dimension = 2
    pipeline._to_vector = lambda text: [1.0, 0.0]
    for index in range(10):
        pipeline.add_text(
            f"generic content {index}",
            document_id=f"doc-{index}",
        )
    pipeline.add_text(
        "rare needle lexical term",
        document_id="doc-rare",
    )

    vector_only = pipeline.search("needle", limit=1)
    hybrid = pipeline.search(
        "needle",
        limit=1,
        retrieval_mode="hybrid",
        vector_weight=0.2,
    )

    assert vector_only[0]["metadata"]["document_id"] != "doc-rare"
    assert hybrid[0]["metadata"]["document_id"] == "doc-rare"


def test_mmr_select_prefers_non_duplicate_content():
    results = [
        {"id": "a", "score": 0.95, "content": "alpha beta gamma"},
        {"id": "b", "score": 0.94, "content": "alpha beta gamma duplicate"},
        {"id": "c", "score": 0.80, "content": "delta epsilon zeta"},
    ]

    selected = mmr_select(results, limit=2, lambda_mult=0.5)

    assert [item["id"] for item in selected] == ["a", "c"]


def test_hybrid_rank_is_stable_for_equal_scores():
    results = hybrid_rank_results(
        query="alpha",
        vector_results=[
            {"id": "b", "score": 0.5, "content": "alpha b"},
            {"id": "a", "score": 0.5, "content": "alpha a"},
        ],
        lexical_results=[],
        limit=2,
    )
    assert [item["id"] for item in results] == ["a", "b"]
