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
        collection_name="test_rag_pdf_collection",
        rag_namespace="pdf_test"
    )

    pdf_file = PROJECT_ROOT / "knowledge_base" / "test_pdf.pdf"

    print("\n========== 测试 1：导入 PDF 文档 ==========")
    result = rag_tool.execute(
        "add_document",
        file_path=str(pdf_file),
        document_id="test_pdf"
    )
    print(result)

    print("\n========== 测试 2：搜索 PDF 内容 ==========")
    result = rag_tool.execute(
        "search",
        query="文档主要讲了什么",
        limit=5,
        min_score=0.0
    )
    print(result)

    print("\n========== 测试 3：PDF 文档问答 ==========")
    result = rag_tool.execute(
        "ask",
        query="请总结这个 PDF 的主要内容",
        limit=5,
        min_score=0.0
    )
    print(result)

    print("\n========== 测试 4：知识库统计 ==========")
    result = rag_tool.execute("stats")
    print(result)


if __name__ == "__main__":
    main()