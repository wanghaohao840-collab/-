from dotenv import load_dotenv
import os

# 1. 加载 .env，必须 override=True
load_dotenv(
    dotenv_path=r"D:\python_self_agent\.env",
    override=True
)

# 2. 先强制指定 LLM 使用 DeepSeek 的 key
os.environ["OPENAI_API_KEY"] = os.getenv("LLM_API_KEY", "")
os.environ["DEEPSEEK_API_KEY"] = os.getenv("LLM_API_KEY", "")

print("LLM_API_KEY 是否存在:", bool(os.getenv("LLM_API_KEY")))
print("LLM_API_KEY 后4位:", os.getenv("LLM_API_KEY")[-4:] if os.getenv("LLM_API_KEY") else None)

print("OPENAI_API_KEY 后4位:", os.getenv("OPENAI_API_KEY")[-4:] if os.getenv("OPENAI_API_KEY") else None)

print("LLM_BASE_URL:", os.getenv("LLM_BASE_URL"))
print("LLM_MODEL_ID:", os.getenv("LLM_MODEL_ID"))

print("EMBED_MODEL_TYPE:", os.getenv("EMBED_MODEL_TYPE"))
print("EMBED_MODEL_NAME:", os.getenv("EMBED_MODEL_NAME"))
print("EMBED_API_KEY 是否存在:", bool(os.getenv("EMBED_API_KEY")))
print("EMBED_API_KEY 后4位:", os.getenv("EMBED_API_KEY")[-4:] if os.getenv("EMBED_API_KEY") else None)

from hello_agents import SimpleAgent, HelloAgentsLLM, ToolRegistry
from hello_agents.tools import MemoryTool, RAGTool


# 3. 先创建 LLM
llm = HelloAgentsLLM(
    provider="openai",
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL"),
    model=os.getenv("LLM_MODEL_ID")
)

# 4. 创建完 LLM 后，再把 EMBED_API_KEY 同步给 DashScope SDK
os.environ["DASHSCOPE_API_KEY"] = os.getenv("EMBED_API_KEY", "")

print("DASHSCOPE_API_KEY 是否存在:", bool(os.getenv("DASHSCOPE_API_KEY")))
print("DASHSCOPE_API_KEY 后4位:", os.getenv("DASHSCOPE_API_KEY")[-4:] if os.getenv("DASHSCOPE_API_KEY") else None)


agent = SimpleAgent(
    name="智能助手",
    llm=llm,
    system_prompt="你是一个有记忆和知识检索能力的AI助手"
)

tool_registry = ToolRegistry()

memory_tool = MemoryTool(user_id="user123")
tool_registry.register_tool(memory_tool)

rag_tool = RAGTool(knowledge_base_path="./knowledge_base")
tool_registry.register_tool(rag_tool)

agent.tool_registry = tool_registry

response = agent.run("你好！请记住我叫张三，我是一名Python开发者")
print(response)