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
        memory_types=["working","episodic","semantic"]
    )

    print("\n========== 测试 1：添加工作记忆 ==========")

    result = memory_tool.execute(
        "add",
        content="用户今天学习了 Python 函数",
        memory_type="working",
        importance=0.6
    )
    print(result)

    print("\n========== 测试 2：搜索工作记忆 ==========")

    result = memory_tool.execute(
        "search",
        query="Python 函数",
        memory_type="working",
        limit=5
    )
    print(result)

    print("\n========== 测试 3：添加情景记忆 ==========")

    result = memory_tool.execute(
        "add",
        content="2026年6月17日，用户完成了 HelloAgents 情景记忆模块测试",
        memory_type="episodic",
        importance=0.8,
        event_type="milestone",
        location="本地开发环境"
    )
    print(result)

    print("\n========== 测试 4：搜索情景记忆 ==========")

    result = memory_tool.execute(
        "search",
        query="情景记忆 模块 测试",
        memory_type="episodic",
        limit=5
    )
    print(result)

    print("\n========== 测试 5：记忆摘要 ==========")

    result = memory_tool.execute("summary")
    print(result)

    print("\n========== 测试 6：记忆整合 working -> episodic ==========")

    result = memory_tool.execute(
        "consolidate",
        from_type="working",
        to_type="episodic",
        importance_threshold=0.5
    )
    print(result)

    print("\n========== 测试 7：整合后摘要 ==========")

    result = memory_tool.execute("summary")
    print(result)

    print("\n========== 测试 8：基于重要性的遗忘 ==========")

    result = memory_tool.execute(
        "forget",
        strategy="importance_based",
        threshold=0.7
    )
    print(result)

    print("\n========== 测试 9：遗忘后记忆摘要 ==========")

    result = memory_tool.execute("summary")
    print(result)

    print("\n========== 测试 10：清空所有记忆 ==========")

    result = memory_tool.execute("clear_all")
    print(result)

    print("\n========== 测试 11：清空后摘要 ==========")

    result = memory_tool.execute("summary")
    print(result)





if __name__ == "__main__":
    main()