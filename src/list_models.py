from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

client = Groq()  # reads GROQ_API_KEY from .env

for model in client.models.list().data:
    print(model.id)