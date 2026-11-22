import os
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# The key is read from .env (GROQ_API_KEY). Never type a key in the code.
client = Groq()

response = client.chat.completions.create(
    model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
    messages=[{"role": "user", "content": "Say hello in Egyptian Arabic."}],
)

print(response.choices[0].message.content)
