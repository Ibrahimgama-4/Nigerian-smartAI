"""Smart Planting Calendar engine. Pure logic (the forecast is passed in), so every rule is unit-tested.

WHAT IS SOURCED, AND WHAT IS NOT
 * Task timings come from data/crop_activities.json: IITA guides for maize and cowpea in northern Nigeria. Each task says whether its
   timing is 'source' (the guide gives it) or 'planning_default' (our own scheduling choice, shown as such to the farmer).
 * A crop with no entry there gets NO task list. It still gets the FAO growth-stage timeline when one exists. Nothing is guessed.
 * The weather shifts are general good-practice rules driven by the same (unreviewed) thresholds as the farm weather advice.
   A shift can never move a task past the end of its window, and it only looks at days inside the real forecast.

WEATHER RULES (per task 'rain' class)
   field       hold on a day with heavy rain (>= heavy_rain_mm)
   spray       hold if rain >= rain_delay_mm falls on that day or the next (a spray would wash off), and on heavy-rain days
   fertilizer  hold if rain >= heavy_rain_mm falls on that day or the next (fertiliser can wash away), and on heavy-rain days
   planting    hold on heavy-rain days; for RAIN-FED crops also hold while the whole 7-day forecast is dry (IITA: plant once the rains are established)
   irrigation  (irrigated farms only) no irrigation check needed when rain >= rain_delay_mm is forecast within 2 days"""
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

from . import growth_stage as gs
from .weather_rules import Thresholds

_DATA = json.loads((Path(__file__).resolve().parent.parent.parent / "data" / "crop_activities.json").read_text(encoding="utf-8"))
CROPS = _DATA["crops"]
SAVANNA_ZONES = tuple(_DATA["savanna_zones"])
MATURITY_GROUPS = ("extra_early", "early", "medium", "late")
WATER = ("rainfed", "irrigated")
CATEGORIES = ("land_prep", "planting", "thinning", "weeding", "top_dressing", "spraying", "pest_scouting", "harvest_prep", "harvest", "irrigation")
RAIN_CLASSES = ("field", "planting", "spray", "fertilizer", "irrigation")
BASES = ("source", "planning_default")
MAX_SHIFT_DAYS = 14

DISCLAIMER = ("A planning guide built from published IITA guides plus our own scheduling defaults, not a prescription. Your variety, soil and weather can "
              "change the best dates. Follow product labels, and confirm with your extension officer.")
NO_SCHEDULE = ("There is no sourced week-by-week task schedule for this crop yet, so the app does not invent one. "
               "The growth stages below are FAO's general example. Ask your extension officer, or the app administrator to add a sourced schedule.")
BEYOND_FORECAST = "Dates more than a week ahead are not weather-adjusted yet. They will adjust as the forecast reaches them."


def supported_crops() -> list:
    return sorted(CROPS)


def _iso(d: date) -> str:
    return d.isoformat()


def harvest_window(crop: dict, group: Optional[str], days: Optional[int]) -> dict:
    """(start_dap, end_dap) of the harvest period and where it came from."""
    if days:
        return {"start": days, "end": days + 7, "basis": "your_seed_pack",
                "note": f"Using the {days} days to maturity you entered, and a one-week harvest window (our planning default)."}
    groups = crop.get("maturity_groups")
    if groups and group in groups:
        a, b = groups[group]
        return {"start": a, "end": b, "basis": "source", "note": crop["harvest_note"]}
    if groups:
        a, b = min(g[0] for g in groups.values()), max(g[1] for g in groups.values())
        return {"start": a, "end": b, "basis": "source", "note": crop["harvest_note"] + " Pick your variety group for a tighter date."}
    a, b = crop["grain_harvest_days"]
    return {"start": a, "end": b, "basis": "source", "note": crop["harvest_note"]}


def _resolve(tpl: dict, hv: dict, zone: Optional[str], group: Optional[str]):
    f, t, says = tpl["from"], tpl["to"], tpl["source_says"]
    for var in tpl.get("variants", []):
        w = var["when"]
        if w.get("savanna_zone") == zone and w.get("maturity_group") == group:
            f, t, says = var["from"], var["to"], var["says"]
    anchor = tpl.get("anchor")
    if anchor == "harvest_start":
        base = hv["start"]
        if tpl.get("anchor_end") == "harvest_end":
            return base, hv["end"], says
        return base + f, base + t, says
    return f, t, says


def _fdays(forecast, today: date) -> dict:
    if forecast is None:
        return {}
    return {d.date: d for d in forecast.days if d.date >= _iso(today)}


def _p(fd: dict, d: date):
    x = fd.get(_iso(d))
    return None if x is None else x.precip_mm


def _blocked(rain: str, d: date, fd: dict, th: Thresholds, water: str, dry_week: bool) -> Optional[str]:
    """Reason a task should not be done on day d, or None. Only days inside the forecast can be blocked."""
    p0 = _p(fd, d)
    if p0 is None:
        return None
    p1 = _p(fd, d + timedelta(days=1)) or 0.0
    if p0 >= th.heavy_rain_mm:
        return f"Heavy rain ({p0:g} mm) is forecast on {d.strftime('%d %b')}."
    if rain == "spray" and p0 + p1 >= th.rain_delay_mm:
        return f"Rain is forecast ({p0 + p1:g} mm over 2 days from {d.strftime('%d %b')}); it would wash a spray off."
    if rain == "fertilizer" and p0 + p1 >= th.heavy_rain_mm:
        return f"Very heavy rain ({p0 + p1:g} mm over 2 days from {d.strftime('%d %b')}) could wash fertiliser away."
    if rain == "planting" and water == "rainfed" and dry_week:
        return "No rain is forecast for the next 7 days. IITA advises planting once the rains are established."
    if rain == "irrigation" and p0 + p1 >= th.rain_delay_mm:
        return f"Rain is forecast ({p0 + p1:g} mm over 2 days), so irrigating is probably not needed."
    return None


def _dry_week(fd: dict, th: Thresholds, today: date) -> bool:
    vals = [_p(fd, today + timedelta(days=i)) for i in range(7)]
    return all(v is not None for v in vals) and all(v < th.dry_day_mm for v in vals)


def suggest(window_start: date, window_end: date, today: date, rain: str, forecast, th: Thresholds = Thresholds(), water: str = "rainfed") -> dict:
    """Best day to do a task inside its window. {'date': date|None, 'reason': str|None, 'beyond_forecast': bool}."""
    fd = _fdays(forecast, today)
    start = max(window_start, today)
    if start > window_end:
        return {"date": None, "reason": None, "beyond_forecast": False, "overdue": True}
    dry = _dry_week(fd, th, today)
    first_reason = None
    d = start
    while d <= window_end and (d - start).days <= MAX_SHIFT_DAYS:
        if _iso(d) not in fd:
            return {"date": d, "reason": first_reason, "beyond_forecast": True, "overdue": False}     # past the forecast: cannot judge, keep the date
        r = _blocked(rain, d, fd, th, water, dry)
        if r is None:
            return {"date": d, "reason": first_reason, "beyond_forecast": False, "overdue": False}
        first_reason = first_reason or r
        d += timedelta(days=1)
    return {"date": None, "reason": first_reason, "beyond_forecast": False, "overdue": False}


def _status(saved: Optional[str], ws: date, we: date, today: date, sg: dict) -> str:
    if saved in ("done", "skipped"):
        return saved
    if we < today:
        return "overdue"
    if sg["date"] is None:
        return "hold"
    if sg["date"] > max(ws, today):
        return "delayed"
    return "due_now" if ws <= today <= we else "upcoming"


def planting_check(crop: dict, zone: Optional[str], planting: date) -> dict:
    wins = {w["zone"]: w for w in crop.get("planting_windows", [])}
    if not zone:
        return {"status": "unknown", "text": "Choose your savanna zone (ask your extension officer if unsure) to check your planting date against IITA's recommended window."}
    w = wins.get(zone)
    zname = _DATA["savanna_zones"].get(zone, zone)
    if not w:
        return {"status": "unknown", "text": f"The IITA guide gives no planting window for this crop in the {zname}."}
    s, e = date(planting.year, *w["start"]), date(planting.year, *w["end"])
    src = {"name": _DATA["sources"][crop["source"]]["name"], "url": _DATA["sources"][crop["source"]]["url"], "page": w["page"]}
    if planting < s:
        return {"status": "early", "text": f"Your date is before the IITA window for the {zname} ({w['text']}). Early planting can fail if the rains are not established.", "source": src}
    if planting > e:
        return {"status": "late", "text": f"Your date is after the IITA window for the {zname} ({w['text']}). Check with your extension officer whether a shorter-season variety suits.", "source": src}
    return {"status": "within", "text": f"Your date is inside the IITA window for the {zname} ({w['text']}).", "source": src}


def _growth(slug: str, planting: date) -> Optional[dict]:
    info = gs.stage_info(slug, -1)
    if not info.get("available"):
        return None
    return {"reference": info["reference"], "source_name": info["source_name"], "source_url": info["source_url"], "caveat": info["caveat"],
            "stages": [{"name": s["name"], "from": _iso(planting + timedelta(days=s["from_dap"])), "to": _iso(planting + timedelta(days=s["to_dap"]))} for s in info["timeline"]]}


def _weather_summary(fd: dict, th: Thresholds, today: date, water: str) -> dict:
    if not fd:
        return {"available": False, "note": "No forecast was available, so dates are not weather-adjusted."}
    days = sorted(fd)
    rain = [fd[k].precip_mm for k in days if fd[k].precip_mm is not None]
    notes = []
    if water == "rainfed" and _dry_week(fd, th, today):
        notes.append("No rain is forecast for the next 7 days. Hold planting and watch young crops.")
    return {"available": True, "covers_from": days[0], "covers_to": days[-1], "rain_total_mm": round(sum(rain), 1), "notes": notes, "beyond": BEYOND_FORECAST}


def build_plan(*, crop_slug: str, planting: date, today: date, water: str = "rainfed", size_ha: Optional[float] = None,
               savanna_zone: Optional[str] = None, maturity_group: Optional[str] = None, maturity_days: Optional[int] = None,
               forecast=None, th: Thresholds = Thresholds(), saved: Optional[dict] = None) -> dict:
    """saved: {task_key: 'done'|'skipped'} for tasks the farmer already ticked."""
    saved = saved or {}
    crop = CROPS.get(crop_slug)
    base = {"crop": crop_slug, "planting_date": _iso(planting), "water": water, "size_ha": size_ha, "disclaimer": DISCLAIMER,
            "growth": _growth(crop_slug, planting), "today": _iso(today)}
    if not crop:
        return {**base, "available": False, "message": NO_SCHEDULE, "weeks": []}
    hv = harvest_window(crop, maturity_group, maturity_days)
    fd = _fdays(forecast, today)
    tasks = []
    for tpl in crop["tasks"]:
        f, t, says = _resolve(tpl, hv, savanna_zone, maturity_group)
        ws, we = planting + timedelta(days=f), planting + timedelta(days=t)
        sg = suggest(ws, we, today, tpl["rain"], forecast, th, water)
        st = _status(saved.get(tpl["key"]), ws, we, today, sg)
        tasks.append({"key": tpl["key"], "title": tpl["title"], "category": tpl["category"], "rain": tpl["rain"], "optional": bool(tpl.get("optional")),
                      "window_start": _iso(ws), "window_end": _iso(we), "from_dap": f, "to_dap": t,
                      "suggested": _iso(sg["date"]) if sg["date"] else None, "status": st, "weather_reason": sg["reason"] if st in ("delayed", "hold") else None,
                      "beyond_forecast": sg["beyond_forecast"], "basis": tpl["basis"], "source_says": says,
                      "source": {"name": _DATA["sources"][crop["source"]]["name"], "url": _DATA["sources"][crop["source"]]["url"], "page": tpl["page"]},
                      "persist": True})
    if water == "irrigated":
        weeks = max(1, min(30, (hv["start"] + 6) // 7))
        for w in range(weeks):
            ws, we = planting + timedelta(days=7 * w), planting + timedelta(days=7 * w + 6)
            sg = suggest(ws, we, today, "irrigation", forecast, th, water)
            tasks.append({"key": f"irrigation_check_{w + 1}", "title": "Check soil moisture and irrigate if the soil is drying", "category": "irrigation", "rain": "irrigation",
                          "optional": False, "window_start": _iso(ws), "window_end": _iso(we), "from_dap": 7 * w, "to_dap": 7 * w + 6,
                          "suggested": _iso(sg["date"]) if sg["date"] else None, "status": _status(None, ws, we, today, sg),
                          "weather_reason": sg["reason"] if sg["date"] is None or (sg["date"] and sg["reason"]) else None,
                          "beyond_forecast": sg["beyond_forecast"], "basis": "planning_default",
                          "source_says": "A weekly moisture check is a general practice, not a figure from the IITA guide. No irrigation volumes are given by this app.",
                          "source": None, "persist": False})
    grouped = {}
    for tk in tasks:
        wk = max(-1, (date.fromisoformat(tk["window_start"]) - planting).days // 7)
        grouped.setdefault(wk, []).append(tk)
    weeks = []
    for wk in sorted(grouped):
        items = sorted(grouped[wk], key=lambda x: (x["window_start"], x["key"]))
        start = _iso(planting + timedelta(days=7 * wk)) if wk >= 0 else None
        weeks.append({"week": wk, "label": "Before planting" if wk < 0 else f"Week {wk + 1}", "starts": start, "tasks": items})
    seed = None
    if crop.get("seed_kg_per_ha") and size_ha:
        lo, hi, page = crop["seed_kg_per_ha"]
        seed = {"low_kg": round(lo * size_ha, 1), "high_kg": round(hi * size_ha, 1), "text": f"About {round(lo * size_ha, 1):g} to {round(hi * size_ha, 1):g} kg of seed for {size_ha:g} ha ({lo} to {hi} kg per hectare, IITA p. {page}).".replace("  ", " ")}
    return {**base, "available": True, "weeks": weeks, "tasks_total": len(tasks),
            "harvest": {"from": _iso(planting + timedelta(days=hv["start"])), "to": _iso(planting + timedelta(days=hv["end"])), "basis": hv["basis"], "note": hv["note"]},
            "planting_check": planting_check(crop, savanna_zone, planting), "seed": seed,
            "weather": _weather_summary(fd, th, today, water),
            "source": _DATA["sources"][crop["source"]], "note": _DATA["_note"], "weather_rules_note": _DATA["weather_rules_note"]}


def task_rows(plan: dict) -> list:
    """The tasks worth saving to the farm (irrigation checks are recomputed, not stored)."""
    out = []
    for wk in plan.get("weeks", []):
        for t in wk["tasks"]:
            if t.get("persist"):
                out.append({"task_key": t["key"], "title": t["title"], "category": t["category"], "rain_class": t["rain"], "optional": t["optional"],
                            "basis": t["basis"], "window_start": t["window_start"], "window_end": t["window_end"]})
    return out


def reminder_events(tasks: list, crop_name: str, today: date, forecast, th: Thresholds = Thresholds(), water: str = "rainfed") -> list:
    """Push/WhatsApp messages for saved, still-pending tasks. At most two per task: when it becomes doable, and on its last day.
    A postponement notice is sent only on the first day of a window, so a long weather delay does not nag every day."""
    out = []
    for t in tasks:
        if t.get("status", "pending") != "pending":
            continue
        ws, we = date.fromisoformat(str(t["window_start"])[:10]), date.fromisoformat(str(t["window_end"])[:10])
        if not (ws <= today <= we):
            continue
        sg = suggest(ws, we, today, t.get("rain_class") or "field", forecast, th, water)
        tid = t.get("id") or t["task_key"]
        if today == we:
            tail = "" if sg["date"] == today else " Weather may make today hard; do it as soon as you safely can."
            out.append({"key": f"plan_{tid}_last", "level": "watch", "kind": "plan", "text": f"{crop_name}: last day for '{t['title']}'.{tail}"})
        elif sg["date"] == today:
            out.append({"key": f"plan_{tid}_do", "level": "info", "kind": "plan", "text": f"{crop_name}: time for '{t['title']}' (until {we.strftime('%d %b')})."})
        elif today == ws and sg["date"] and sg["reason"]:
            out.append({"key": f"plan_{tid}_wait", "level": "info", "kind": "plan",
                        "text": f"{crop_name}: '{t['title']}' is due, but wait until {sg['date'].strftime('%d %b')}. {sg['reason']}"})
    return out
