import os
import base64

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


def get_chat_model(**overrides) -> ChatOpenAI:
    username = os.getenv("BASIC_AUTH_USERNAME", "dev")
    password = os.getenv("BASIC_AUTH_PASSWORD")

    basic_token = base64.b64encode(
        f"{username}:{password}".encode("utf-8")
    ).decode("utf-8")

    settings = {
        "model": os.getenv("LOCAL_MODEL", "Qwen/Qwen3-8B-AWQ"),
        "base_url": os.getenv("OPENAI_API_BASE", "http://192.168.99.95:8000/v1"),
        "api_key": os.getenv("OPENAI_API_KEY", "EMPTY"),
        "temperature": 0,
        "default_headers": {"Authorization": f"Basic {basic_token}"},
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": False}
        },
    }
    settings.update(overrides)
    return ChatOpenAI(**settings)