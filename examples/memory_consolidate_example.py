from hello_agents.memory.manager import MemoryManager
from hello_agents.tools.builtin.memory_tool import MemoryTool


memory_manager = MemoryManager()
memory_tool = MemoryTool(memory_manager=memory_manager)


# 将重要的工作记忆转为情景记忆
result = memory_tool.execute(
    "consolidate",
    from_type="working",
    to_type="episodic",
    importance_threshold=0.7
)
print(result)


# 将重要的情景记忆转为语义记忆
result = memory_tool.execute(
    "consolidate",
    from_type="episodic",
    to_type="semantic",
    importance_threshold=0.8
)
print(result)