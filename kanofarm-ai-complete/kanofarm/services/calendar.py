"""Crop calendar: national + zone-tagged draft entries, and the zone/state reference data.
Nothing here is a precise local forecast; every entry keeps its own source and review_status."""
import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CAL = json.loads((_DATA_DIR / "crop_calendar.json").read_text(encoding="utf-8"))
_ZONES = json.loads((_DATA_DIR / "nigeria_zones.json").read_text(encoding="utf-8"))
_STATES = json.loads((_DATA_DIR / "nigeria_states.json").read_text(encoding="utf-8"))
MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
_STATE_ZONE = {s["name"].lower(): s["zone"] for s in _STATES["states"]}

def zone_for_state(state: str):
    return _STATE_ZONE.get((state or "").strip().lower())

def states() -> dict:
    return {"note": _STATES["_note"], "zones": _ZONES["zones"], "states": _STATES["states"]}

def calendar(crop_slug=None, zone=None) -> dict:
    entries = [e for e in _CAL["entries"] if not crop_slug or e["crop_slug"] == crop_slug]
    if zone:
        entries = [e for e in entries if "all" in e["zones"] or zone in e["zones"]]
    out = []
    for e in entries:
        e = dict(e)
        e["months_label"] = "-".join(MONTHS[m - 1] for m in (e["months"][0], e["months"][-1])) if e.get("months") else None
        out.append(e)
    return {"banner": _CAL["banner"], "zone": zone, "entries": out}
