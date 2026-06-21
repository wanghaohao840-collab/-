import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from hello_agents.tools.builtin.rag_tool import RAGTool
import hello_agents


def main():
    print("========== 路径检查 ==========")
    print("当前项目根目录:", PROJECT_ROOT)
    print("实际加载的 hello_agents 路径:", hello_agents.__file__)

    rag_tool = RAGTool(
        knowledge_base_path="./knowledge_base",
        collection_name="test_rag_document_collection",
        rag_namespace="document_test"
    )

    test_file = PROJECT_ROOT / "knowledge_base" / "test_doc.md"

    print("\n========== 测试 1：导入 Markdown 文档 ==========")
    result = rag_tool.execute(
        "add_document",
        file_path=str(test_file),
        document_id="test_doc"
    )
    print(result)

    print("\n========== 测试 2：搜索文档内容 ==========")
    result = rag_tool.execute(
        "search",
        query="Python 函数可以接收哪些参数",
        limit=5,
        min_score=0.0
    )
    print(result)

    print("\n========== 测试 3：文档问答 ==========")
    result = rag_tool.execute(
        "ask",
        query="RAG 的核心流程是什么？",
        limit=5,
        min_score=0.0
    )
    print(result)

    print("\n========== 测试 4：知识库统计 ==========")
    result = rag_tool.execute("stats")
    print(result)


if __name__ == "__main__":
    main()