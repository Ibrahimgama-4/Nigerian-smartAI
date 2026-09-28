"""Tests use a hand-built payload in the documented Open-Meteo response SHAPE. It is a test
fixture only (never served by the app). Live-API behaviour is covered by scripts/smoke_weather.py."""
import unittest
from datetime import date
from kanofarm.services.weather_parser import parse_forecast, WeatherDataError, validate_coordinates, grid_key
from kanofarm.services.weather_client import WeatherService, build_url
from kanofarm.services.weather_rules import build_advisories, Thresholds
from kanofarm.services.crop_stage import days_after_planting, stage_for
from kanofarm.services.i18n import translate
from kanofarm.core.ratelimit import RateLimiter

def payload(past_precip, fut_precip, tmax=32.0, et0=4.0):
    days = [f"2026-09-{d:02d}" for d in range(17, 17 + len(past_precip) + len(fut_precip))]
    n = len(days)
    return {"latitude": 12.0, "longitude": 8.5, "elevation": 470, "timezone": "Africa/Lagos",
            "current": {"time": "2026-09-24T14:00", "temperature_2m": 31.2, "relative_humidity_2m": 55,
                        "precipitation": 0.0, "wind_speed_10m": 9.4},
            "daily": {"time": days,
                      "temperature_2m_max": [tmax] * n, "temperature_2m_min": [22.0] * n,
                      "precipitation_sum": past_precip + fut_precip,
                      "precipitation_probability_max": [10] * n,
                      "et0_fao_evapotranspiration": [et0] * n,
                      "wind_speed_10m_max": [15] * n, "shortwave_radiation_sum": [20] * n},
            "hourly": {"time": ["2026-09-24T13:00", "2026-09-24T14:00", "2026-09-24T15:00"],
                       "soil_moisture_0_to_1cm": [0.20, 0.21, 0.35]}}

class Parser(unittest.TestCase):
    def test_parse_and_soil_uses_latest_at_or_before_now(self):
        fc = parse_forecast(payload([0]*7, [0]*7))
        self.assertEqual(len(fc.days), 14)
        self.assertEqual(fc.current.soil_moisture_m3m3, 0.21)   # not the future 0.35
    def test_out_of_range_becomes_none_not_invented(self):
        p = payload([0]*7, [0]*7); p["current"]["temperature_2m"] = 999
        self.assertIsNone(parse_forecast(p).current.temperature_c)
    def test_provider_error_raises(self):
        with self.assertRaises(WeatherDataError): parse_forecast({"error": True, "reason": "x"})
    def test_missing_sections_raise(self):
        with self.assertRaises(WeatherDataError): parse_forecast({"current": {}})
    def test_coordinates(self):
        validate_coordinates(12.0, 8.5)
        with self.assertRaises(ValueError): validate_coordinates(51.5, -0.1)
    def test_url_has_verified_params(self):
        u = build_url("https://api.open-meteo.com/v1/forecast", 12.0, 8.5)
        for k in ("et0_fao_evapotranspiration", "soil_moisture_0_to_1cm", "past_days=7", "Africa%2FLagos"):
            self.assertIn(k, u)

class Service(unittest.TestCase):
    def test_cache_and_grid(self):
        calls = []; t = [1000.0]
        def fetch(url): calls.append(url); return payload([0]*7, [0]*7)
        s = WeatherService("https://x", fetch=fetch, clock=lambda: t[0], ttl_seconds=100)
        s.get(12.01, 8.52); _, _, cached = s.get(12.04, 8.49)
        self.assertTrue(cached); self.assertEqual(len(calls), 1)
        t[0] += 101; _, _, cached = s.get(12.0, 8.5)
        self.assertFalse(cached); self.assertEqual(len(calls), 2)
    def test_failure_never_fabricates(self):
        def boom(url): raise OSError("net")
        with self.assertRaises(WeatherDataError):
            WeatherService("https://x", fetch=boom).get(12.0, 8.5)

class Rules(unittest.TestCase):
    ids = staticmethod(lambda p, **k: {a.id for a in build_advisories(parse_forecast(p), **k)})
    def test_rain_delay(self):
        self.assertIn("rain_delay_irrigation", self.ids(payload([0]*7, [3, 4, 0, 0, 0, 0, 0])))
    def test_heavy_rain(self):
        self.assertIn("heavy_rain", self.ids(payload([0]*7, [60, 0, 0, 0, 0, 0, 0])))
    def test_recent_dry_spell(self):
        self.assertIn("dry_spell_recent", self.ids(payload([0.0]*7, [2]*7)))
    def test_no_dry_spell_when_rain_recent(self):
        self.assertNotIn("dry_spell_recent", self.ids(payload([0, 0, 0, 0, 0, 0, 12], [2]*7)))
    def test_forecast_dry_spell(self):
        self.assertIn("dry_spell_forecast", self.ids(payload([5]*7, [0]*7)))
    def test_heat_and_water_demand(self):
        got = self.ids(payload([5]*7, [0]*7, tmax=40, et0=7))
        self.assertIn("heat_stress", got); self.assertIn("high_water_demand", got)
    def test_missing_data_gives_no_dry_claim(self):
        p = payload([0]*7, [0]*7); p["daily"]["precipitation_sum"][3] = None
        self.assertNotIn("dry_spell_recent", self.ids(p))
    def test_insufficient_history_no_dry_claim(self):
        self.assertNotIn("dry_spell_recent", self.ids(payload([0]*3, [2]*7)))
    def test_evidence_present(self):
        a = build_advisories(parse_forecast(payload([0]*7, [60]*7)))
        self.assertTrue(all(x.evidence for x in a))

class Misc(unittest.TestCase):
    def test_days_after_planting(self):
        self.assertEqual(days_after_planting(date(2026, 6, 15), date(2026, 9, 24)), 101)
        with self.assertRaises(ValueError): days_after_planting(date(2026, 10, 1), date(2026, 9, 24))
    def test_stage_requires_calendar(self):
        self.assertIsNone(stage_for(40, None))
        cal = [{"stage": "X", "from_dap": 0, "to_dap": 50, "source": "s"}]
        self.assertEqual(stage_for(40, cal), "X"); self.assertIsNone(stage_for(60, cal))
    def test_hausa_falls_back_and_flags(self):
        r = translate("heat_stress_risk", "ha")
        self.assertEqual(r["language"], "en"); self.assertTrue(r["fallback"])
    def test_ratelimit(self):
        t = [0.0]; rl = RateLimiter(2, 10, clock=lambda: t[0])
        self.assertTrue(rl.allow("a")); self.assertTrue(rl.allow("a")); self.assertFalse(rl.allow("a"))
        t[0] = 11; self.assertTrue(rl.allow("a"))

if __name__ == "__main__":
    unittest.main()
