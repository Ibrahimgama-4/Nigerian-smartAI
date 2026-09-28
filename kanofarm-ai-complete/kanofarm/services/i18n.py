import json
from pathlib import Path
_DIR = Path(__file__).resolve().parent.parent / "i18n"

def _load(lang):
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))

def translate(key: str, lang: str = "en") -> dict:
    """Returns {'text','language','fallback'}; falls back to English when a reviewed translation is missing."""
    if lang != "en":
        t = _load(lang).get(key, "")
        if t:
            return {"text": t, "language": lang, "fallback": False}
    return {"text": _load("en")[key], "language": "en", "fallback": lang != "en"}
