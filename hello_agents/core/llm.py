# hello_agents/core/llm.py

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


class HelloAgentsLLM:
    """HelloAgents 的 LLM 统一封装

    这个版本的目标：
    1. 先保证 RAGTool 可以正常初始化
    2. 支持 DeepSeek / OpenAI-compatible API
    3. 如果没有配置 API，也不会让程序直接崩溃
    """

    def __init__(
        self,
        model: Optional[str] = None,
        model_id: Optional[str] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 60,
        **kwargs
    ):
        self.model = (
            model
            or model_id
            or os.getenv("LLM_MODEL_ID")
            or os.getenv("OPENAI_MODEL")
            or "deepseek-chat"
        )

        self.api_key = (
            api_key
            or os.getenv("LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
        )

        self.base_url = (
            base_url
            or os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.deepseek.com/v1"
        )

        self.timeout = timeout
        self.client = None

        if OpenAI is not None and self.api_key:
            self.client = OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
            )

    def chat(
        self,
        prompt: str,
        system_prompt: str = "你是一个有帮助的AI助手。",
        temperature: float = 0.2,
        max_tokens: int = 1024,
        **kwargs
    ) -> str:
        """普通文本对话"""

        if self.client is None:
            return (
                "【LLM未配置】当前没有可用的大模型客户端。"
                "请检查 .env 中的 LLM_API_KEY、LLM_BASE_URL、LLM_MODEL_ID。"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        return response.choices[0].message.content

    def generate(self, prompt: str, **kwargs) -> str:
        """兼容 generate 调用"""

        return self.chat(prompt, **kwargs)

    def think(self, prompt: str, **kwargs) -> str:
        """兼容 think 调用"""

        return self.chat(prompt, **kwargs)

    def invoke(self, prompt: str, **kwargs) -> str:
        """兼容 invoke 调用"""

        return self.chat(prompt, **kwargs)

    def __call__(self, prompt: str, **kwargs) -> str:
        """兼容 llm(prompt) 调用"""

        return self.chat(prompt, **kwargs)