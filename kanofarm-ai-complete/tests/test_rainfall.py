import unittest
from datetime import date, timedelta
from kanofarm.services.rainfall import plan, summarize, RainfallService, parse_daily_precip, archive_url, recent_url
from kanofarm.services.weather_parser import WeatherDataError

TODAY = date(2026, 9, 24)

def daily(start, end, value_fn):
    days, d = [], start
    while d <= end: days.append(d); d += timedelta(days=1)
    return {"daily": {"time": [x.isoformat() for x in days], "precipitation_sum": [value_fn(x) for x in days]}}

class Plan(unittest.TestCase):
    def test_old_planting_uses_archive_plus_recent(self):
        p = plan(date(2026, 6, 15), TODAY)
        self.assertEqual(p["archive"], (date(2026, 6, 15), date(2026, 9, 16))); self.assertEqual(p["recent_days"], 8)
    def test_recent_planting_uses_forecast_only(self):
        p = plan(date(2026, 9, 20), TODAY); self.assertIsNone(p["archive"]); self.assertEqual(p["recent_days"], 4)
    def test_planted_today_needs_nothing(self):
        self.assertEqual(plan(TODAY, TODAY), {"archive": None, "recent_days": 0})
    def test_urls(self):
        u = archive_url("https://a/v1/archive", 12.0, 8.5, date(2026, 6, 15), date(2026, 9, 16))
        self.assertIn("start_date=2026-06-15", u); self.assertIn("daily=precipitation_sum", u)
        self.assertIn("past_days=8", recent_url("https://f", 12.0, 8.5, 8))

class Summary(unittest.TestCase):
    def test_stats(self):
        planting = date(2026, 9, 10)                     # 14 full days: Sep 10..23
        series = {planting + timedelta(days=i): v for i, v in enumerate([0, 0, 0, 5, 0, 0, 0, 0, 0, 0, 12.5, 0, 0, 0])}
        src = {d: "reanalysis" for d in series}
        s = summarize(series, src, planting, TODAY)
        self.assertEqual(s["days"], 14); self.assertTrue(s["complete"]); self.assertEqual(s["total_mm"], 17.5)
        self.assertEqual(s["rainy_days"], 2); self.assertEqual(s["max_day_mm"], 12.5)
        self.assertEqual(s["longest_dry_run_days"], 6); self.assertEqual(len(s["weeks"]), 2)
        self.assertEqual(s["weeks"][0]["total_mm"], 5.0); self.assertEqual(s["through"], "2026-09-23")
    def test_missing_days_are_never_hidden(self):
        planting = date(2026, 9, 20); series = {date(2026, 9, 20): 3.0, date(2026, 9, 22): 2.0}
        s = summarize(series, {}, planting, TODAY)
        self.assertFalse(s["complete"]); self.assertEqual(s["missing_days"], 2); self.assertEqual(s["total_mm"], 5.0)
    def test_planted_today(self):
        s = summarize({}, {}, TODAY, TODAY); self.assertEqual(s["days"], 0); self.assertFalse(s["complete"]); self.assertIsNone(s["total_mm"]); self.assertTrue(s["note"])
    def test_unknown_day_breaks_dry_run(self):
        planting = date(2026, 9, 20); series = {planting: 0, planting + timedelta(days=2): 0, planting + timedelta(days=3): 0}
        self.assertEqual(summarize(series, {}, planting, TODAY)["longest_dry_run_days"], 2)

class Service(unittest.TestCase):
    def make(self, calls, bad=False):
        def fetch(url):
            calls.append(url)
            if bad: raise OSError("down")
            if "archive" in url: return daily(date(2026, 6, 15), date(2026, 9, 16), lambda d: 1.0)
            return daily(date(2026, 9, 16), TODAY, lambda d: 2.0)      # includes today, which must be excluded
        return RainfallService("https://forecast", "https://archive", fetch=fetch, today_fn=lambda: TODAY)
    def test_merge_prefers_reanalysis_excludes_today(self):
        calls = []; s = self.make(calls).since(12.0, 8.5, date(2026, 6, 15))
        self.assertEqual(s["days"], 101); self.assertTrue(s["complete"])
        self.assertEqual(s["sources"], {"reanalysis": 94, "recent_model": 7})      # Jun15..Sep16 = 94; Sep17..23 = 7
        self.assertEqual(s["total_mm"], 94 * 1.0 + 7 * 2.0)
        self.assertEqual(len(calls), 2)
    def test_cache_by_grid(self):
        calls = []; svc = self.make(calls); svc.since(12.01, 8.52, date(2026, 6, 15)); svc.since(12.04, 8.49, date(2026, 6, 15))
        self.assertEqual(len(calls), 2)
    def test_failure_raises_never_fabricates(self):
        with self.assertRaises(WeatherDataError): self.make([], bad=True).since(12.0, 8.5, date(2026, 6, 15))
    def test_outside_nigeria(self):
        with self.assertRaises(ValueError): self.make([]).since(51.5, -0.1, date(2026, 6, 15))
    def test_bad_payloads(self):
        for bad in ({}, {"daily": {"time": ["2026-01-01"], "precipitation_sum": []}}):
            with self.assertRaises(WeatherDataError): parse_daily_precip(bad)
    def test_out_of_range_value_becomes_missing(self):
        r = parse_daily_precip({"daily": {"time": ["2026-01-01"], "precipitation_sum": [99999]}}); self.assertIsNone(r[date(2026, 1, 1)])
if __name__ == "__main__": unittest.main()
