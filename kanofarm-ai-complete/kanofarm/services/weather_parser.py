"""Parse + validate an Open-Meteo forecast JSON response. No invented values: anything
missing or out of physical range becomes None, and structural problems raise WeatherDataError."""
from dataclasses import dataclass, field
from typing import Optional

class WeatherDataError(Exception):
    pass

NIGERIA_BBOX = (4.0, 14.0, 2.5, 15.0)  # lat_min, lat_max, lon_min, lon_max

def validate_coordinates(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Coordinates out of range")
    a, b, c, d = NIGERIA_BBOX
    if not (a <= lat <= b and c <= lon <= d):
        raise ValueError("Coordinates are outside Nigeria; KanoFarm AI supports Nigerian farms only")

def grid_key(lat: float, lon: float) -> tuple:
    return (round(lat, 1), round(lon, 1))

RANGES = {
    "temp": (-10.0, 55.0), "rh": (0.0, 100.0), "precip": (0.0, 1000.0),
    "wind": (0.0, 250.0), "et0": (0.0, 20.0), "prob": (0.0, 100.0),
    "radiation": (0.0, 40.0), "soil": (0.0, 1.0),
}

def _ok(v, kind) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    lo, hi = RANGES[kind]
    return f if lo <= f <= hi else None

@dataclass
class Current:
    time: str
    temperature_c: Optional[float]
    humidity_pct: Optional[float]
    precipitation_mm: Optional[float]
    wind_kmh: Optional[float]
    soil_moisture_m3m3: Optional[float]

@dataclass
class Day:
    date: str
    tmax_c: Optional[float]
    tmin_c: Optional[float]
    precip_mm: Optional[float]
    precip_prob_pct: Optional[float]
    et0_mm: Optional[float]
    wind_max_kmh: Optional[float]
    radiation_mj_m2: Optional[float]

@dataclass
class Forecast:
    latitude: float
    longitude: float
    elevation_m: Optional[float]
    timezone: str
    current: Current
    days: list = field(default_factory=list)  # includes past_days then forecast days

def _col(d: dict, name: str, n: int) -> list:
    v = d.get(name)
    return v if isinstance(v, list) and len(v) == n else [None] * n

def parse_forecast(raw: dict) -> Forecast:
    if not isinstance(raw, dict) or raw.get("error"):
        raise WeatherDataError(str(raw.get("reason", "provider error")) if isinstance(raw, dict) else "bad payload")
    try:
        cur, daily = raw["current"], raw["daily"]
        times = daily["time"]
        cur_time = cur["time"]
    except (KeyError, TypeError):
        raise WeatherDataError("missing current/daily sections")
    if not isinstance(times, list) or not times:
        raise WeatherDataError("empty daily series")
    n = len(times)

    soil = None
    hourly = raw.get("hourly") or {}
    ht, hs = hourly.get("time"), hourly.get("soil_moisture_0_to_1cm")
    if isinstance(ht, list) and isinstance(hs, list) and len(ht) == len(hs):
        for t, v in zip(ht, hs):          # latest hourly value at or before "now"
            if t <= cur_time:
                soil = _ok(v, "soil") if v is not None else soil

    current = Current(
        time=cur_time,
        temperature_c=_ok(cur.get("temperature_2m"), "temp"),
        humidity_pct=_ok(cur.get("relative_humidity_2m"), "rh"),
        precipitation_mm=_ok(cur.get("precipitation"), "precip"),
        wind_kmh=_ok(cur.get("wind_speed_10m"), "wind"),
        soil_moisture_m3m3=soil,
    )
    cols = {k: _col(daily, k, n) for k in (
        "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
        "precipitation_probability_max", "et0_fao_evapotranspiration",
        "wind_speed_10m_max", "shortwave_radiation_sum")}
    days = [Day(
        date=times[i],
        tmax_c=_ok(cols["temperature_2m_max"][i], "temp"),
        tmin_c=_ok(cols["temperature_2m_min"][i], "temp"),
        precip_mm=_ok(cols["precipitation_sum"][i], "precip"),
        precip_prob_pct=_ok(cols["precipitation_probability_max"][i], "prob"),
        et0_mm=_ok(cols["et0_fao_evapotranspiration"][i], "et0"),
        wind_max_kmh=_ok(cols["wind_speed_10m_max"][i], "wind"),
        radiation_mj_m2=_ok(cols["shortwave_radiation_sum"][i], "radiation"),
    ) for i in range(n)]
    return Forecast(
        latitude=float(raw.get("latitude", 0)), longitude=float(raw.get("longitude", 0)),
        elevation_m=raw.get("elevation"), timezone=str(raw.get("timezone", "")),
        current=current, days=days)
