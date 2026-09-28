import json, time, urllib.parse, urllib.request
from typing import Callable, Optional
from .weather_parser import Forecast, parse_forecast, grid_key, validate_coordinates, WeatherDataError

CURRENT = "temperature_2m,relative_humidity_2m,precipitation,wind_speed_10m"
DAILY = ("temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,"
         "et0_fao_evapotranspiration,wind_speed_10m_max,shortwave_radiation_sum")
HOURLY = "soil_moisture_0_to_1cm"

def build_url(base: str, lat: float, lon: float, api_key: str = "", past_days: int = 7, forecast_days: int = 7) -> str:
    q = {"latitude": f"{lat:.4f}", "longitude": f"{lon:.4f}", "current": CURRENT, "daily": DAILY,
         "hourly": HOURLY, "past_days": past_days, "forecast_days": forecast_days,
         "timezone": "Africa/Lagos"}
    if api_key:
        q["apikey"] = api_key
    return f"{base}?{urllib.parse.urlencode(q, safe=',')}"

def default_fetch(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "KanoFarmAI/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:   # nosec - fixed https base URL
        return json.loads(r.read().decode("utf-8"))

class WeatherService:
    """Fetches real Open-Meteo data, caches by 0.1° grid cell. Never fabricates a fallback."""
    def __init__(self, base_url: str, api_key: str = "", ttl_seconds: int = 1800,
                 fetch: Callable[[str], dict] = default_fetch, clock=time.time):
        self.base, self.key, self.ttl, self.fetch, self.clock = base_url, api_key, ttl_seconds, fetch, clock
        self._cache: dict = {}

    def get(self, lat: float, lon: float) -> tuple:
        """Returns (Forecast, fetched_at_epoch, from_cache). Raises WeatherDataError on any failure."""
        validate_coordinates(lat, lon)
        k = grid_key(lat, lon)
        hit = self._cache.get(k)
        if hit and self.clock() - hit[1] < self.ttl:
            return hit[0], hit[1], True
        try:
            raw = self.fetch(build_url(self.base, k[0], k[1], self.key))
        except Exception as e:                       # network/HTTP/JSON
            raise WeatherDataError(f"fetch failed: {type(e).__name__}") from e
        fc = parse_forecast(raw)
        self._cache[k] = (fc, self.clock())
        return fc, self._cache[k][1], False
