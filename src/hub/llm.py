"""One place that creates the LLM, so swapping provider/model later touches only this file."""
import os

from .config import MODEL, TEMPERATURE


def get_llm():
    if not (os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")):
        raise RuntimeError("Chưa có GOOGLE_API_KEY. Lấy key miễn phí ở aistudio.google.com/apikey "
                           "rồi `export GOOGLE_API_KEY=...`")
    from langchain_google_genai import ChatGoogleGenerativeAI
    return ChatGoogleGenerativeAI(model=MODEL, temperature=TEMPERATURE,
                                  google_api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))
