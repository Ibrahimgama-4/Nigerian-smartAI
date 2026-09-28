"""Rainfall since planting from Open-Meteo (MODELLED reanalysis, not gauge measurements).
Older days: Historical Weather API (ERA5-family reanalysis, ~5 day publication lag).
Last ~8 days: forecast API `past_days` (recent model analysis). Each day keeps its source label.
A total is only presented as complete when EVERY day has a value; otherwise it is a partial minimum."""
import time, urllib.parse
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional
from .weather_client import default_fetch
from .weather_parser import validate_coordinates, grid_key, WeatherDataError, _ok

ARCHIVE_LAG_DAYS = 8          # ERA5 lags ~5 days; margin avoids requesting unpublished days
DRY_DAY_MM = 1.0

def lagos_today() -> date:    # Africa/Lagos is UTC+1 all year (no daylight saving)
    return (datetime.now(timezone.utc) + timedelta(hours=1)).date()

def plan(planting: date, today: date) -> dict:
    if planting > today - timedelta(days=1):
        return {"archive": None, "recent_days": 0}
    archive_end = today - timedelta(days=ARCHIVE_LAG_DAYS)
    if planting <= archive_end:
        return {"archive": (planting, archive_end), "recent_days": ARCHIVE_LAG_DAYS}
    return {"archive": None, "recent_days": (today - planting).days}

def archive_url(base, lat, lon, start: date, end: date, key=""):
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "start_date": start.isoformat(), "end_date": end.isoformat(),
         "daily": "precipitation_sum", "timezone": "Africa/Lagos"}
    if key: q["apikey"] = key
    return f"{base}?{urllib.parse.urlencode(q, safe=',')}"

def recent_url(base, lat, lon, past_days: int, key=""):
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "daily": "precipitation_sum",
         "past_days": past_days, "forecast_days": 1, "timezone": "Africa/Lagos"}
    if key: q["apikey"] = key
    return f"{base}?{urllib.parse.urlencode(q, safe=',')}"

def parse_daily_precip(raw) -> dict:
    try:
        t, p = raw["daily"]["time"], raw["daily"]["precipitation_sum"]
    except (KeyError, TypeError):
        raise WeatherDataError("missing daily precipitation")
    if not isinstance(t, list) or not isinstance(p, list) or len(t) != len(p):
        raise WeatherDataError("malformed daily precipitation")
    return {date.fromisoformat(d): _ok(v, "precip") for d, v in zip(t, p)}

def summarize(series: dict, src: dict, planting: date, today: date) -> dict:
    n = (today - planting).days                      # full days: planting .. yesterday
    days = [planting + timedelta(days=i) for i in range(max(n, 0))]
    vals = [series.get(d) for d in days]
    known = [v for v in vals if v is not None]
    missing = len(vals) - len(known)
    longest = run = 0
    for v in vals:
        run = run + 1 if (v is not None and v < DRY_DAY_MM) else 0
        longest = max(longest, run)
    weeks = []
    for i in range(0, len(days), 7):
        chunk = vals[i:i + 7]; k = [v for v in chunk if v is not None]
        weeks.append({"week": i // 7 + 1, "start": days[i].isoformat(), "days": len(chunk),
                      "total_mm": round(sum(k), 1), "missing_days": len(chunk) - len(k)})
    srcs = [src.get(d) for d in days if series.get(d) is not None]
    return {"planting_date": planting.isoformat(), "through": (today - timedelta(days=1)).isoformat() if n > 0 else None,
            "days": n, "days_with_data": len(known), "missing_days": missing, "complete": n > 0 and missing == 0,
            "total_mm": round(sum(known), 1) if known else None,
            "rainy_days": sum(1 for v in known if v >= DRY_DAY_MM),
            "max_day_mm": round(max(known), 1) if known else None,
            "longest_dry_run_days": longest, "weeks": weeks,
            "sources": {"reanalysis": srcs.count("reanalysis"), "recent_model": srcs.count("recent_model")},
            "note": None if n > 0 else "The crop was planted today, so there are no full days of rainfall yet."}

class RainfallService:
    def __init__(self, forecast_url, archive_base, api_key="", fetch: Callable = default_fetch,
                 clock=time.time, today_fn: Callable = lagos_today):
        self.f, self.a, self.key, self.fetch, self.clock, self.today_fn = forecast_url, archive_base, api_key, fetch, clock, today_fn
        self._cache = {}

    def _get(self, key, ttl, url, parse):
        hit = self._cache.get(key)
        if hit and self.clock() - hit[1] < ttl: return hit[0]
        try: data = parse(self.fetch(url))
        except WeatherDataError: raise
        except Exception as e: raise WeatherDataError(f"fetch failed: {type(e).__name__}") from e
        self._cache[key] = (data, self.clock()); return data

    def since(self, lat: float, lon: float, planting: date) -> dict:
        validate_coordinates(lat, lon)
        g, today = grid_key(lat, lon), self.today_fn()
        p, series, src = plan(planting, today), {}, {}
        if p["recent_days"] > 0:
            r = self._get(("recent", g, p["recent_days"], today), 1800,
                          recent_url(self.f, g[0], g[1], p["recent_days"], self.key), parse_daily_precip)
            for d, v in r.items():
                if planting <= d < today: series[d], src[d] = v, "recent_model"
        if p["archive"]:
            s, e = p["archive"]
            a = self._get(("archive", g, s, e), 86400, archive_url(self.a, g[0], g[1], s, e, self.key), parse_daily_precip)
            for d, v in a.items():                          # reanalysis wins where both exist
                if s <= d <= e and (v is not None or d not in series): series[d], src[d] = v, "reanalysis"
        return summarize(series, src, planting, today)
