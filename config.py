import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv(encoding="utf-8-sig")


def _llm_provider() -> str:
    return os.getenv("LLM_PROVIDER", "local").strip().lower()


def _openai_chat_model(**overrides) -> ChatOpenAI:
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


def _local_chat_model(**overrides) -> ChatOpenAI:
    base_url = os.getenv("LOCAL_MODEL_BASE_URL")
    model = os.getenv("LOCAL_MODEL_NAME")
    if not base_url or not model:
        raise RuntimeError(
            "Local LLM is not configured. Set LOCAL_MODEL_BASE_URL and "
            "LOCAL_MODEL_NAME in your .env file."
        )

    settings = {
        "model": model,
        "base_url": base_url.rstrip("/"),
        "api_key": os.getenv("LOCAL_MODEL_API_KEY", "EMPTY"),
        "temperature": 0,
    }
    settings.update(overrides)
    return ChatOpenAI(**settings)


def get_chat_model(**overrides) -> ChatOpenAI:
    provider = _llm_provider()
    if provider in {"openai", "gpt"}:
        return _openai_chat_model(**overrides)
    if provider in {"local", "local_openai", "local-openai", "inhouse", "in-house"}:
        return _local_chat_model(**overrides)

    raise RuntimeError(
        "Unsupported LLM_PROVIDER. Use 'openai' or 'local'."
    )
