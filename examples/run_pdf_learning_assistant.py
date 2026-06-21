import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from assistants.pdf_learning_assistant import PDFLearningAssistant
import hello_agents


def main():
    print("========== 路径检查 ==========")
    print("当前项目根目录:", PROJECT_ROOT)
    print("实际加载的 hello_agents 路径:", hello_agents.__file__)

    assistant = PDFLearningAssistant(user_id="user123")

    pdf_file = PROJECT_ROOT / "knowledge_base" / "test_pdf.pdf"

    print("\n========== 测试 1：导入 PDF ==========")
    result = assistant.load_document(str(pdf_file))
    print(result)

    print("\n========== 测试 2：搜索 PDF 内容 ==========")
    result = assistant.search("这个文档主要讲了什么", limit=5)
    print(result)

    print("\n========== 测试 3：PDF 问答 ==========")
    result = assistant.ask("请总结这个 PDF 的主要内容", limit=5)
    print(result)

    print("\n========== 测试 4：添加学习笔记 ==========")
    result = assistant.add_note(
        note="RAG 的核心是先检索相关资料，再让大模型基于资料生成答案。",
        concept="RAG"
    )
    print(result)

    print("\n========== 测试 5：回忆学习内容 ==========")
    result = assistant.recall("RAG 核心")
    print(result)

    print("\n========== 测试 6：学习统计 ==========")
    result = assistant.get_stats()
    print(result)


if __name__ == "__main__":
    main()