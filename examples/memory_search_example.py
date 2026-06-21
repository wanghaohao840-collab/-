from hello_agents.memory.manager import MemoryManager
from hello_agents.tools.builtin.memory_tool import MemoryTool


memory_manager = MemoryManager()
memory_tool = MemoryTool(memory_manager=memory_manager)


# 基础搜索
result = memory_tool.execute("search", query="Python编程", limit=5)
print(result)


# 指定记忆类型搜索
result = memory_tool.execute(
    "search",
    query="学习进度",
    memory_type="episodic",
    limit=3
)
print(result)


# 多类型搜索
result = memory_tool.execute(
    "search",
    query="函数定义",
    memory_types=["semantic", "episodic"],
    min_importance=0.5
)
print(result)