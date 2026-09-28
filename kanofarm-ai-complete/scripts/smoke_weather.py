"""Run this on a machine WITH internet to verify the live Open-Meteo integration end to end:
    python scripts/smoke_weather.py
Prints real forecast + advisories for Kano city area (approx 12.0N, 8.5E)."""
import sys, json
sys.path.insert(0, ".")
from kanofarm.services.weather_client import WeatherService
from kanofarm.services.weather_rules import build_advisories
svc = WeatherService("https://api.open-meteo.com/v1/forecast")
fc, ts, _ = svc.get(12.0, 8.5)
print("current:", fc.current); print("days:", len(fc.days))
for a in build_advisories(fc): print(a.to_dict())
