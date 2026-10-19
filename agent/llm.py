from langchain_anthropic import ChatAnthropic

from backend.config import settings


def get_llm() -> ChatAnthropic:
    return ChatAnthropic(model=settings.model, api_key=settings.anthropic_api_key or None, max_tokens=2048)
