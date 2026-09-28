import os

def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()

SUPABASE_URL = lambda: env("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = lambda: env("SUPABASE_ANON_KEY")
AI_MODEL_ENDPOINT = lambda: env("AI_MODEL_ENDPOINT")
AI_MODEL_TOKEN = lambda: env("AI_MODEL_TOKEN")

# AI assistant: pick a provider with ASSISTANT_PROVIDER ("anthropic", the default, or "groq").
# Only the matching key needs to be set; the other can be left blank.
ASSISTANT_PROVIDER = lambda: env("ASSISTANT_PROVIDER", "anthropic").lower()
ANTHROPIC_API_KEY = lambda: env("ANTHROPIC_API_KEY")
GROQ_API_KEY = lambda: env("GROQ_API_KEY")
ASSISTANT_MODEL_OVERRIDE = lambda: env("ASSISTANT_MODEL")   # optional; a sensible default is used otherwise

def ASSISTANT_ACTIVE_KEY() -> str:
    return {"anthropic": ANTHROPIC_API_KEY(), "groq": GROQ_API_KEY()}.get(ASSISTANT_PROVIDER(), "")
