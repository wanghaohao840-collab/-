import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from hello_agents.tools.builtin.memory_tool import MemoryTool
import hello_agents


def main():
    print("========== 路径检查 ==========")
    print("当前项目根目录:", PROJECT_ROOT)
    print("实际加载的 hello_agents 路径:", hello_agents.__file__)

    print("\n========== 初始化 MemoryTool ==========")

    memory_tool = MemoryTool(
        user_id="user123",
        memory_types=["working", "episodic", "semantic"]
    )

    print("\n========== 测试 1：添加语义记忆 ==========")

    result = memory_tool.execute(
        "add",
        content="Python 是一种解释型、面向对象的高级编程语言，常用于人工智能、数据分析和 Web 开发。",
        memory_type="semantic",
        importance=0.9,
        knowledge_type="factual"
    )
    print(result)

    print("\n========== 测试 2：搜索语义记忆 ==========")

    result = memory_tool.execute(
        "search",
        query="Python 编程语言 人工智能",
        memory_type="semantic",
        limit=5
    )
    print(result)

    print("\n========== 测试 3：记忆摘要 ==========")

    result = memory_tool.execute("summary")
    print(result)


if __name__ == "__main__":
    main()