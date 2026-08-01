from pathlib import Path

from hello_agents.memory.rag.pipeline import SimpleRAGPipeline
from hello_agents.tools.builtin.rag_tool import RAGTool
from evals.multi_document_qa import assert_all_pass, evaluate_trace, load_cases


class FakeLLM:
    def __init__(self):
        self.prompts = []

    def estimate_tokens(self, text):
        return len(str(text or ""))

    def generate(self, prompt, **kwargs):
        self.prompts.append(str(prompt))
        return "golden summary"


def make_tool(cache_path):
    pipeline = SimpleRAGPipeline(rag_namespace="golden", cache_path=str(cache_path))
    pipeline.dimension = 2
    pipeline._to_vector = lambda text: [1.0, 0.0]
    for document_id, content in (
        ("doc-1", "alpha document one"),
        ("doc-2", "alpha document two"),
        ("doc-3", "alpha forbidden document"),
    ):
        pipeline.add_text(content, document_id=document_id)

    tool = RAGTool.__new__(RAGTool)
    tool.rag_namespace = "golden"
    tool._pipelines = {"golden": pipeline}
    tool.llm = FakeLLM()
    return tool


def test_golden_cases_cover_scope_modes_and_missing_documents(tmp_path):
    cases_path = Path(__file__).parents[2] / "evals" / "data" / "multi_document_qa.json"
    cases = load_cases(cases_path)
    tool = make_tool(tmp_path / "golden.json")
    results = []

    for case in cases:
        tool.llm.prompts.clear()
        output = tool.execute(
            "ask",
            query=case.query,
            document_ids=list(case.document_ids),
            mode=case.mode,
            rag_namespace="golden",
            limit=5,
        )
        results.append(
            evaluate_trace(case, prompts=tool.llm.prompts, output=output)
        )

    assert_all_pass(results)
