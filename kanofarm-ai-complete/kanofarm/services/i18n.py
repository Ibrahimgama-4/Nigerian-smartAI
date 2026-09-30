import json
from pathlib import Path
_DIR = Path(__file__).resolve().parent.parent / "i18n"
_REVIEWED_LANGS = {"en"}   # add a language code here only once a native speaker has actually checked its file

def _load(lang):
    return json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))

def translate(key: str, lang: str = "en") -> dict:
    """Returns {'text','language','fallback','unreviewed'}.
    fallback=True only when the target language has no text at all for this key (English is shown instead).
    unreviewed=True for any language not yet in _REVIEWED_LANGS, whether we're showing its own draft text or
    the English fallback -- a machine/AI-drafted translation must never be presented as equivalent to a
    native-speaker-reviewed one, so the caller always gets an explicit signal to flag it."""
    if lang != "en":
        t = _load(lang).get(key, "")
        if t:
            return {"text": t, "language": lang, "fallback": False, "unreviewed": lang not in _REVIEWED_LANGS}
    return {"text": _load("en")[key], "language": "en", "fallback": lang != "en", "unreviewed": lang not in _REVIEWED_LANGS}
