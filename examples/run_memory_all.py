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

    memory_tool = MemoryTool(
        user_id="user123",
        memory_types=["working", "episodic", "semantic"]
    )

    print("\n========== 添加 working 记忆 ==========")
    print(memory_tool.execute(
        "add",
        content="用户刚才在学习 Python 函数的参数传递",
        memory_type="working",
        importance=0.6
    ))

    print("\n========== 添加 episodic 记忆 ==========")
    print(memory_tool.execute(
        "add",
        content="2026年6月17日，用户完成了 HelloAgents 记忆系统的阶段性测试",
        memory_type="episodic",
        importance=0.8,
        event_type="milestone"
    ))

    print("\n========== 添加 semantic 记忆 ==========")
    print(memory_tool.execute(
        "add",
        content="Python 函数可以通过位置参数、关键字参数、默认参数和可变参数接收输入。",
        memory_type="semantic",
        importance=0.9,
        knowledge_type="factual"
    ))

    print("\n========== 搜索所有记忆 ==========")
    print(memory_tool.execute(
        "search",
        query="Python 函数 参数",
        limit=10
    ))

    print("\n========== 指定搜索 semantic ==========")
    print(memory_tool.execute(
        "search",
        query="Python 函数 参数",
        memory_type="semantic",
        limit=5
    ))

    print("\n========== 记忆摘要 ==========")
    print(memory_tool.execute("summary"))


if __name__ == "__main__":
    main()