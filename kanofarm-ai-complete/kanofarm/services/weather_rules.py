"""Rule-based farm advisories from real forecast data. Every advisory lists its evidence.
Thresholds are DEFAULTS and are flagged UNREVIEWED until an agronomist signs them off.
Flood risk is deliberately NOT computed (needs hydrological data we have not integrated)."""
from dataclasses import dataclass, asdict
from datetime import date as _date
from .weather_parser import Forecast

@dataclass(frozen=True)
class Thresholds:
    rain_delay_mm: float = 5.0        # rain in next 2 days at/above this -> hold irrigation
    heavy_rain_mm: float = 50.0       # single-day total treated as heavy rainfall
    dry_day_mm: float = 1.0           # a day below this counts as dry
    dry_spell_days: int = 7
    heat_tmax_c: float = 38.0
    high_et0_mm: float = 6.0
    review_status: str = "default_unreviewed"

@dataclass
class Advisory:
    id: str
    level: str          # info | watch | warning
    message_key: str
    evidence: dict
    def to_dict(self): return asdict(self)

def _split(fc: Forecast):
    today = fc.current.time[:10]
    past = [d for d in fc.days if d.date < today]
    ahead = [d for d in fc.days if d.date >= today]
    return past, ahead

def _known(vals):
    return [v for v in vals if v is not None]

def build_advisories(fc: Forecast, t: Thresholds = Thresholds()) -> list:
    past, ahead = _split(fc)
    out = []

    nxt2 = ahead[:2]
    rain2 = _known(d.precip_mm for d in nxt2)
    if len(rain2) == len(nxt2) and nxt2 and sum(rain2) >= t.rain_delay_mm:
        out.append(Advisory("rain_delay_irrigation", "info", "rain_expected_delay_irrigation",
                            {"rain_next_2_days_mm": round(sum(rain2), 1), "threshold_mm": t.rain_delay_mm}))

    nxt3 = ahead[:3]
    heavy = [(d.date, d.precip_mm) for d in nxt3 if d.precip_mm is not None and d.precip_mm >= t.heavy_rain_mm]
    if heavy:
        out.append(Advisory("heavy_rain", "warning", "heavy_rainfall_risk",
                            {"days": [{"date": a, "precip_mm": b} for a, b in heavy], "threshold_mm": t.heavy_rain_mm}))

    hot = [(d.date, d.tmax_c) for d in nxt3 if d.tmax_c is not None and d.tmax_c >= t.heat_tmax_c]
    if hot:
        out.append(Advisory("heat_stress", "watch", "heat_stress_risk",
                            {"days": [{"date": a, "tmax_c": b} for a, b in hot], "threshold_c": t.heat_tmax_c}))

    # Dry spell: consecutive dry days ending yesterday, only when every past value is known
    past_p = [d.precip_mm for d in past]
    if len(past_p) >= t.dry_spell_days and all(p is not None for p in past_p):
        run = 0
        for p in reversed(past_p):
            if p < t.dry_day_mm: run += 1
            else: break
        if run >= t.dry_spell_days:
            out.append(Advisory("dry_spell_recent", "watch", "dry_spell_recent",
                                {"consecutive_dry_days": run, "dry_day_below_mm": t.dry_day_mm}))
    week = ahead[:7]
    wk = _known(d.precip_mm for d in week)
    if len(week) >= 7 and len(wk) == len(week) and all(p < t.dry_day_mm for p in wk):
        out.append(Advisory("dry_spell_forecast", "watch", "dry_spell_forecast",
                            {"forecast_dry_days": len(week), "dry_day_below_mm": t.dry_day_mm}))

    et = _known(d.et0_mm for d in nxt3)
    rn = _known(d.precip_mm for d in nxt3)
    if len(et) == len(nxt3) and nxt3 and len(rn) == len(nxt3) \
            and sum(et) / len(et) >= t.high_et0_mm and sum(rn) < t.rain_delay_mm:
        out.append(Advisory("high_water_demand", "watch", "high_water_demand_check_moisture",
                            {"mean_et0_mm_per_day": round(sum(et) / len(et), 1),
                             "rain_next_3_days_mm": round(sum(rn), 1), "threshold_et0_mm": t.high_et0_mm}))
    return out
