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

    print("\n========== 第一次创建 RAGTool，并添加知识 ==========")

    rag_tool_1 = RAGTool(
        knowledge_base_path="./knowledge_base",
        collection_name="persistence_test_collection",
        rag_namespace="persistence_test"
    )

    result = rag_tool_1.execute(
        "add_text",
        text="持久化测试：RAG 知识库应该在程序重启后仍然可以检索到这句话。",
        document_id="persistence_doc"
    )
    print(result)

    print("\n========== 第一次统计 ==========")
    print(rag_tool_1.execute("stats"))

    print("\n========== 模拟程序重启：重新创建 RAGTool ==========")

    rag_tool_2 = RAGTool(
        knowledge_base_path="./knowledge_base",
        collection_name="persistence_test_collection",
        rag_namespace="persistence_test"
    )

    print("\n========== 第二次统计：应该还能看到刚才的 chunk ==========")
    print(rag_tool_2.execute("stats"))

    print("\n========== 第二次搜索：应该能检索到持久化测试内容 ==========")
    result = rag_tool_2.execute(
        "search",
        query="程序重启后还能检索",
        limit=5,
        min_score=0.0
    )
    print(result)


if __name__ == "__main__":
    main()