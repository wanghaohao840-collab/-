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

    print("\n========== 初始化 RAGTool ==========")

    rag_tool = RAGTool(
        knowledge_base_path="./knowledge_base",
        collection_name="test_rag_collection",
        rag_namespace="test"
    )

    print("\n========== 测试 1：添加文本知识 ==========")

    result = rag_tool.execute(
        "add_text",
        text="Python 是一种高级编程语言，常用于人工智能、数据分析、自动化脚本和 Web 开发。",
        document_id="python_intro"
    )
    print(result)

    print("\n========== 测试 2：继续添加文本知识 ==========")

    result = rag_tool.execute(
        "add_text",
        text="RAG 是检索增强生成技术，它会先从知识库中检索相关内容，再让大语言模型基于上下文生成答案。",
        document_id="rag_intro"
    )
    print(result)

    print("\n========== 测试 3：搜索知识库 ==========")

    result = rag_tool.execute(
        "search",
        query="Python 可以用来做什么",
        limit=3,
        min_score=0.1
    )
    print(result)

    print("\n========== 测试 4：搜索 RAG 知识 ==========")

    result = rag_tool.execute(
        "search",
        query="什么是检索增强生成",
        limit=3,
        min_score=0.1
    )
    print(result)

    print("\n========== 测试 5：知识库统计 ==========")

    result = rag_tool.execute("stats")
    print(result)

    print("\n========== 测试 6：RAG 问答 ==========")

    result = rag_tool.execute(
        "ask",
        query="Python 主要可以用来做什么？",
        limit=3,
        min_score=0.1
    )
    print(result)

    print("\n========== 测试 7：RAG 概念问答 ==========")

    result = rag_tool.execute(
        "ask",
        query="什么是 RAG？",
        limit=3,
        min_score=0.0
    )
    print(result)

    print("\n========== 测试 8：清空 RAG 知识库 ==========")

    result = rag_tool.execute("clear")
    print(result)

    print("\n========== 测试 9：清空后统计 ==========")

    result = rag_tool.execute("stats")
    print(result)


if __name__ == "__main__":
    main()