
"""Create Gemini models."""

import os

from langchain_google_genai import ChatGoogleGenerativeAI

from .config import MODEL_POOL, TEMPERATURE


def get_llm_models():
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Chưa có GOOGLE_API_KEY hoặc GEMINI_API_KEY."
        )

    return [
        ChatGoogleGenerativeAI(
            model=model_name,
            temperature=TEMPERATURE,
            google_api_key=api_key,
            api_version="v1beta",
        )
        for model_name in MODEL_POOL
    ]
