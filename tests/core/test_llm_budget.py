from hello_agents.core.llm import HelloAgentsLLM


class FakeTokenizer:
    def encode(self, text):
        return text.split()


def test_estimate_tokens_uses_injected_tokenizer():
    llm = HelloAgentsLLM(api_key=None, tokenizer=FakeTokenizer())

    assert llm.estimate_tokens("one two three") == 3


def test_estimate_tokens_falls_back_to_character_count():
    llm = HelloAgentsLLM(api_key=None)

    assert llm.estimate_tokens("中文 ab") == 5


def test_context_window_can_be_configured_explicitly():
    llm = HelloAgentsLLM(api_key=None, context_window_tokens=4096)

    assert llm.context_window_tokens == 4096
