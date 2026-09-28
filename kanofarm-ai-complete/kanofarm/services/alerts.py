from dataclasses import replace
from .weather_rules import Thresholds, Advisory

FIELD = {"heavy_rain": "heavy_rain_mm", "dry_spell": "dry_spell_days", "heat_stress": "heat_tmax_c",
         "high_water_demand": "high_et0_mm", "rain_delay_irrigation": "rain_delay_mm"}
ADV_TO_TYPE = {"rain_delay_irrigation": "rain_delay_irrigation", "heavy_rain": "heavy_rain", "heat_stress": "heat_stress",
               "dry_spell_recent": "dry_spell", "dry_spell_forecast": "dry_spell", "high_water_demand": "high_water_demand"}

def thresholds_from_rules(rules: list, base: Thresholds = Thresholds()) -> Thresholds:
    kw, custom = {}, False
    for r in rules or []:
        f = FIELD.get(r.get("alert_type"))
        if f and r.get("threshold") is not None and r.get("enabled", True):
            v = float(r["threshold"]); kw[f] = int(v) if f == "dry_spell_days" else v; custom = True
    return replace(base, **kw, review_status="user_configured" if custom else base.review_status)

def filter_advisories(advisories: list, rules: list) -> list:
    off = {r["alert_type"] for r in rules or [] if r.get("enabled") is False}
    return [a for a in advisories if ADV_TO_TYPE.get(a.id) not in off]

def alert_rows(farm_id: str, advisories: list, day: str) -> list:
    return [{"farm_id": farm_id, "alert_type": ADV_TO_TYPE.get(a.id, a.id), "level": a.level,
             "message_key": a.message_key, "evidence": a.evidence, "data_kind": "MODELLED", "alert_date": day}
            for a in advisories]
