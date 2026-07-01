"""Quick manual check of Signal 1 on its own, before it goes near the endpoint.

Run:  python test_signal.py
Needs a real GROQ_API_KEY in .env, otherwise every input falls back to 0.5.
"""
from dotenv import load_dotenv

load_dotenv()

from signals import groq_signal

SAMPLES = {
    "clearly_ai": (
        "Artificial intelligence represents a transformative paradigm shift in modern "
        "society. It is important to note that while the benefits of AI are numerous, it "
        "is equally essential to consider the ethical implications. Furthermore, "
        "stakeholders across various sectors must collaborate to ensure responsible "
        "deployment."
    ),
    "clearly_human": (
        "ok so i finally tried that new ramen place downtown and honestly? underwhelming. "
        "the broth was fine but they put WAY too much sodium in it and i was thirsty for "
        "like three hours after. my friend got the spicy version and said it was better. "
        "probably won't go back unless someone drags me there"
    ),
}

for name, text in SAMPLES.items():
    print(name, "->", groq_signal(text))
