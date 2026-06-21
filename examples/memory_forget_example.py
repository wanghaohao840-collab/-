from hello_agents.memory.manager import MemoryManager
from hello_agents.tools.builtin.memory_tool import MemoryTool


memory_manager = MemoryManager()
memory_tool = MemoryTool(memory_manager=memory_manager)


# 1. 基于重要性的遗忘 - 删除重要性低于阈值的记忆
result = memory_tool.execute(
    "forget",
    strategy="importance_based",
    threshold=0.2
)
print(result)


# 2. 基于时间的遗忘 - 删除超过指定天数的记忆
result = memory_tool.execute(
    "forget",
    strategy="time_based",
    max_age_days=30
)
print(result)


# 3. 基于容量的遗忘 - 当记忆数量超限时删除最不重要的
result = memory_tool.execute(
    "forget",
    strategy="capacity_based",
    threshold=0.3
)
print(result)