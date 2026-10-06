"""Growth stage from days after planting, using ONLY the sourced lengths in data/crop_stages.json.
A crop without a sourced row gets no stage ('unavailable'), never a guess.
If the farmer entered an expected harvest date, the SAME stage proportions are stretched to fit it. That is labelled
as an adjustment: the proportions are FAO's, the total length is the farmer's own estimate."""
import json
from datetime import date
from pathlib import Path
from typing import Optional

_DATA = json.loads((Path(__file__).resolve().parent.parent.parent / "data" / "crop_stages.json").read_text(encoding="utf-8"))
STAGES = _DATA["stages"]                      # ordered: initial, development, mid, late
SCALE_MIN, SCALE_MAX = 0.6, 1.6               # only trust a farmer's harvest date within this range of the reference length

UNAVAILABLE_NOTE = "Growth stage is not shown: no sourced stage data is available for this crop."


def _scaled(lengths: list, expected_days: Optional[int]):
    """Returns (lengths, scaled?). Rounds so the total matches the farmer's estimate exactly."""
    total = sum(lengths)
    if not expected_days or expected_days <= 0 or not (SCALE_MIN <= expected_days / total <= SCALE_MAX):
        return list(lengths), False
    if expected_days == total:
        return list(lengths), False
    raw = [x * expected_days / total for x in lengths]
    out = [max(1, int(round(x))) for x in raw]
    out[2] += expected_days - sum(out)         # put any rounding difference in the longest (mid) stage
    return out, True


def days_between(planting: str, harvest: Optional[str]) -> Optional[int]:
    if not harvest: return None
    try: d = (date.fromisoformat(harvest) - date.fromisoformat(planting)).days
    except ValueError: return None
    return d if d > 0 else None


def stage_info(crop_slug: str, dap: int, expected_days: Optional[int] = None) -> dict:
    ref = _DATA["crops"].get(crop_slug)
    if not ref:
        return {"available": False, "note": UNAVAILABLE_NOTE}
    lengths, scaled = _scaled(ref["lengths"], expected_days)
    segs, start = [], 0
    for meta, n in zip(STAGES, lengths):
        segs.append({"key": meta["key"], "name": meta["name"], "from_dap": start, "to_dap": start + n - 1, "days": n})
        start += n
    base = {"available": True, "review_status": _DATA["_status"], "reference": ref["reference"], "region_fit": ref["region_fit"],
            "region_note": _DATA["region_notes"][ref["region_fit"]], "source_name": _DATA["source"]["name"],
            "source_url": _DATA["source"]["url"], "caveat": _DATA["source"]["caveat"], "total_days": start,
            "scaled_to_your_harvest_date": scaled,
            "scale_note": ("Stage lengths were stretched to fit your expected harvest date. The proportions come from FAO; the timing is an estimate."
                           if scaled else None),
            "timeline": segs}
    if dap < 0:
        return {**base, "stage": None, "note": "Planting date is in the future."}
    cur = next((s for s in segs if s["from_dap"] <= dap <= s["to_dap"]), None)
    if not cur:
        return {**base, "stage": None, "beyond_reference": True,
                "note": f"Past the typical {start}-day length for this example. Check your crop and your expected harvest date."}
    meta = next(m for m in STAGES if m["key"] == cur["key"])
    nxt = next((s for s in segs if s["from_dap"] == cur["to_dap"] + 1), None)
    return {**base, "stage": cur["key"], "stage_name": cur["name"], "what": meta["what"], "tip": meta["tip"],
            "stage_from_dap": cur["from_dap"], "stage_to_dap": cur["to_dap"],
            "days_left_in_stage": cur["to_dap"] - dap + 1,
            "next_stage": nxt["name"] if nxt else None, "next_stage_in_days": (nxt["from_dap"] - dap) if nxt else None}


def stage_start_event(crop_slug: str, crop_name: str, dap: int, expected_days: Optional[int] = None) -> Optional[dict]:
    """A gentle reminder, produced only on the exact day a new stage begins (day 0, planting day, is not a reminder)."""
    info = stage_info(crop_slug, dap, expected_days)
    if not info.get("available") or not info.get("stage") or dap <= 0 or dap != info["stage_from_dap"]:
        return None
    return {"key": f"stage_{crop_slug}_{info['stage']}", "level": "info", "kind": "stage",
            "text": f"{crop_name}: {info['stage_name']} stage is starting (day {dap} after planting). {info['tip']} "
                    f"(General guide, not a forecast.)"}
