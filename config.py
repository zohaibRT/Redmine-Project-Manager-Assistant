import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(encoding="utf-8-sig")


def get_chat_model(**overrides) -> ChatOpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM is not configured. Set OPENAI_API_KEY in your .env file."
        )

    settings = {
        "model": os.getenv("OPENAI_MODEL", "gpt-5.5"),
        "api_key": api_key,
        "temperature": 0,
    }
    settings.update(overrides)
    return ChatOpenAI(**settings)
