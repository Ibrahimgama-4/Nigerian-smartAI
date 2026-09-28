import json
from pathlib import Path
_KB = json.loads((Path(__file__).resolve().parent.parent / "knowledge" / "ipm.json").read_text(encoding="utf-8"))
ORDER = ["prevention", "cultural", "mechanical", "biological", "chemical"]

def ipm_plan(category: str) -> dict:
    cats = _KB["categories"]
    c = cats.get(category) or cats["unknown"]
    return {"review_status": _KB["review_status"], "source_note": _KB["source_note"],
            "steps": [{"stage": s, "items": c.get(s, [])} for s in ORDER if c.get(s)]}
