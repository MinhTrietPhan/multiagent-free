"""Hub settings. Free Gemini key: https://aistudio.google.com/apikey  ->  export GOOGLE_API_KEY=..."""
import os

from dotenv import find_dotenv, load_dotenv

# Loads GOOGLE_API_KEY from a .env file (cwd first, then the project root). Real env vars win.
load_dotenv(find_dotenv(usecwd=True))
load_dotenv()

# Model used by the supervisor (and by future LLM agents). Check AI Studio for what your free tier offers.
MODEL = os.getenv("HUB_MODEL", "gemini-2.5-flash")
TEMPERATURE = 0.0
