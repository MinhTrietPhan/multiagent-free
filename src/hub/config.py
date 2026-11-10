"""Hub settings."""
import os
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
load_dotenv()

TEMPERATURE = 0.0

# Thứ tự ưu tiên: model đầu tiên được thử trước.
MODEL_POOL = [
    model.strip()
    for model in os.getenv(
        "HUB_MODELS",
        "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-2.5-flash-lite,gemini-2.5-flash"
    ).split(",")
    if model.strip()
]