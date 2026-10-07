"""Input safety: look a product or active ingredient up against the sourced list of NAFDAC announcements, and always say
what the list cannot tell you. Pure logic over data/input_safety.json, so it is unit-tested.

Rules this enforces:
 * a match is reported with its source and effective date;
 * NO match never says "safe" or "registered": the answer is 'not_on_our_list' with the reminder to check NAFDAC;
 * every answer carries the data's own warning that it is compiled from news reports and may be out of date."""
import json
import re
from pathlib import Path

_DATA = json.loads((Path(__file__).resolve().parent.parent.parent / "data" / "input_safety.json").read_text(encoding="utf-8"))

NOT_ON_LIST = ("This name is not on the list of announced bans that this app holds. That does NOT mean the product is registered or safe. "
               "Check the NAFDAC registration number on the pack with NAFDAC, and buy only from an approved dealer.")
EMPTY_QUERY = "Type the product name or the active ingredient printed on the pack."


def data() -> dict:
    return _DATA


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def check_product(query: str) -> dict:
    q = _norm(query)
    if len(q) < 3:
        return {"result": "need_more", "message": EMPTY_QUERY, "matches": [], "note": _DATA["_note"]}
    padded = f" {q} "
    hits = []
    for item in _DATA["restricted_ingredients"]:
        if any(f" {_norm(a)} " in padded for a in item["aliases"]):
            hits.append({k: item[k] for k in ("ingredient", "status", "effective", "summary", "sources")})
    if hits:
        return {"result": "on_list", "matches": hits, "note": _DATA["_note"],
                "message": "This matches an ingredient NAFDAC has announced action on. Do not buy or use it until you have confirmed its current status with NAFDAC or your extension officer."}
    return {"result": "not_on_our_list", "matches": [], "message": NOT_ON_LIST, "note": _DATA["_note"]}
