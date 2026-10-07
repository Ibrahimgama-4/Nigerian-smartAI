import json
import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from kanofarm.services import planting_plan as pp
from kanofarm.services import validation as v
from kanofarm.services.alerts_job import run_alert_job
from kanofarm.services.notify import SendResult
from kanofarm.services.validation import ValidationError
from kanofarm.services.weather_parser import Current, Day, Forecast
from kanofarm.services.weather_rules import Thresholds

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 7)


def fc(rain_by_offset: dict = None, default=0.0, days=7, past=7):
    """Forecast with `past` days before TODAY and `days` days from TODAY. rain_by_offset: {days_from_today: mm}."""
    rain_by_offset = rain_by_offset or {}
    out = []
    for off in range(-past, days):
        d = TODAY + timedelta(days=off)
        out.append(Day(d.isoformat(), 30, 20, rain_by_offset.get(off, default), 50, 4, 10, 18))
    return Forecast(12, 8, 500, "Africa/Lagos", Current(TODAY.isoformat() + "T12:00", 30, 50, 0, 5, 0.2), out)


def plan(**kw):
    a = dict(crop_slug="maize", planting=TODAY + timedelta(days=10), today=TODAY, water="rainfed", size_ha=2, forecast=None)
    a.update(kw)
    return pp.build_plan(**a)


def tasks(p):
    return {t["key"]: t for w in p["weeks"] for t in w["tasks"]}


class Data(unittest.TestCase):
    D = json.loads((ROOT / "data" / "crop_activities.json").read_text(encoding="utf-8"))

    def test_every_task_is_well_formed_and_attributed(self):
        for slug, c in self.D["crops"].items():
            self.assertIn(c["source"], self.D["sources"])
            keys = [t["key"] for t in c["tasks"]]
            self.assertEqual(len(keys), len(set(keys)), slug)
            for t in c["tasks"]:
                self.assertIn(t["category"], pp.CATEGORIES, t["key"]); self.assertIn(t["rain"], pp.RAIN_CLASSES, t["key"])
                self.assertIn(t["basis"], pp.BASES, t["key"]); self.assertRegex(t["key"], r"^[a-z0-9_]{1,40}$")
                self.assertTrue(t["source_says"] and isinstance(t["page"], int), t["key"])
                self.assertLessEqual(t["from"], t["to"], t["key"])
                self.assertLessEqual(len(t["title"]), 200)
            self.assertLessEqual(len(c["tasks"]), 30)

    def test_only_documented_crops_have_schedules(self):
        self.assertEqual(set(self.D["crops"]), {"maize", "cowpea"})

    def test_sources_are_https_iita(self):
        for s in self.D["sources"].values():
            self.assertTrue(s["url"].startswith("https://www.iita.org/"))

    def test_sql_lists_match_python(self):
        sql = (ROOT / "database" / "migrations" / "0012_planting_calendar.sql").read_text(encoding="utf-8")
        for x in pp.CATEGORIES + pp.RAIN_CLASSES + pp.BASES + pp.SAVANNA_ZONES + pp.MATURITY_GROUPS + pp.WATER:
            self.assertIn(f"'{x}'", sql, x)


class Structure(unittest.TestCase):
    def test_unknown_crop_gets_no_invented_schedule(self):
        p = plan(crop_slug="rice")
        self.assertFalse(p["available"]); self.assertEqual(p["weeks"], []); self.assertIn("does not invent", p["message"])
        self.assertIsNotNone(p["growth"])                      # FAO stages still shown
        self.assertIsNone(plan(crop_slug="sorghum")["growth"])  # no source at all: nothing

    def test_weeks_are_ordered_and_grouped(self):
        p = plan(); weeks = [w["week"] for w in p["weeks"]]
        self.assertEqual(weeks, sorted(weeks)); self.assertEqual(p["weeks"][0]["label"], "Before planting")
        t = tasks(p)
        self.assertEqual(t["weed_1"]["window_start"], (TODAY + timedelta(days=24)).isoformat())
        wk = {tk["key"]: w["label"] for w in p["weeks"] for tk in w["tasks"]}
        self.assertEqual(wk["plant"], "Week 1"); self.assertEqual(wk["weed_1"], "Week 3"); self.assertEqual(wk["top_dress"], "Week 5")

    def test_all_required_activity_types_are_present_for_both_crops(self):
        for crop in ("maize", "cowpea"):
            cats = {t["category"] for t in tasks(plan(crop_slug=crop)).values()}
            self.assertTrue({"land_prep", "planting", "weeding", "top_dressing", "pest_scouting", "harvest_prep", "harvest"} <= cats, crop)

    def test_task_rows_exclude_irrigation_checks_and_fit_the_database(self):
        rows = pp.task_rows(plan(water="irrigated"))
        self.assertTrue(rows); self.assertFalse([r for r in rows if r["category"] == "irrigation"])
        self.assertLessEqual(len(rows), 40)
        self.assertEqual(len({r["task_key"] for r in rows}), len(rows))


class Maturity(unittest.TestCase):
    def test_maize_default_window_and_seed_pack_days(self):
        self.assertEqual((plan()["harvest"]["from"], plan()["harvest"]["to"]),
                         ((TODAY + timedelta(days=10 + 80)).isoformat(), (TODAY + timedelta(days=10 + 110)).isoformat()))
        p = plan(maturity_days=95)
        self.assertEqual(p["harvest"]["from"], (TODAY + timedelta(days=105)).isoformat()); self.assertEqual(p["harvest"]["basis"], "your_seed_pack")

    def test_cowpea_groups(self):
        for g, (a, b) in {"early": (60, 70), "medium": (70, 75), "late": (80, 85)}.items():
            h = plan(crop_slug="cowpea", maturity_group=g)["harvest"]
            self.assertEqual((h["from"], h["to"]), ((TODAY + timedelta(days=10 + a)).isoformat(), (TODAY + timedelta(days=10 + b)).isoformat()), g)
        h = plan(crop_slug="cowpea")["harvest"]                                     # unknown group: whole documented range
        self.assertEqual((h["from"], h["to"]), ((TODAY + timedelta(days=70)).isoformat(), (TODAY + timedelta(days=95)).isoformat()))

    def test_harvest_prep_follows_the_harvest_date(self):
        t = tasks(plan(maturity_days=100))
        self.assertEqual(t["harvest_prep"]["window_end"], (TODAY + timedelta(days=10 + 99)).isoformat())

    def test_sudan_extra_early_top_dressing_is_earlier(self):
        base = tasks(plan())["top_dress"]; sud = tasks(plan(savanna_zone="sudan", maturity_group="extra_early"))["top_dress"]
        self.assertEqual(base["from_dap"], 28); self.assertEqual(sud["from_dap"], 21)
        self.assertEqual(tasks(plan(savanna_zone="sudan"))["top_dress"]["from_dap"], 28)       # zone alone is not enough


class PlantingWindow(unittest.TestCase):
    def test_before_within_after_and_unknown(self):
        c = pp.CROPS["maize"]
        self.assertEqual(pp.planting_check(c, "sudan", date(2026, 7, 5))["status"], "within")
        self.assertEqual(pp.planting_check(c, "sudan", date(2026, 6, 1))["status"], "early")
        self.assertEqual(pp.planting_check(c, "sudan", date(2026, 8, 1))["status"], "late")
        self.assertEqual(pp.planting_check(c, None, date(2026, 7, 5))["status"], "unknown")
        self.assertEqual(pp.planting_check(c, "sahel", date(2026, 7, 5))["status"], "unknown")   # the guide gives no maize window for the Sahel
        self.assertEqual(pp.planting_check(pp.CROPS["cowpea"], "sahel", date(2026, 6, 20))["status"], "within")


class Seed(unittest.TestCase):
    def test_maize_seed_estimate_scales_with_size(self):
        self.assertEqual((plan(size_ha=2)["seed"]["low_kg"], plan(size_ha=2)["seed"]["high_kg"]), (30.0, 40.0))
        self.assertIsNone(plan(size_ha=None)["seed"]); self.assertIsNone(plan(crop_slug="cowpea")["seed"])


class WeatherShifts(unittest.TestCase):
    def test_no_forecast_means_no_shift_and_a_clear_note(self):
        p = plan(forecast=None)
        self.assertFalse(p["weather"]["available"])
        self.assertTrue(all(t["status"] != "delayed" for t in tasks(p).values()))

    def test_spray_waits_for_a_dry_day(self):
        # cowpea first spray window is days 30-35 after planting; plant 2 days ago so it opens in 28 days: outside the forecast.
        # Use the herbicide (maize, window = planting day and the next) planted today with rain forecast today and tomorrow.
        p = plan(planting=TODAY, forecast=fc({0: 8, 1: 8}, default=0))
        h = tasks(p)["herbicide"]
        self.assertEqual(h["status"], "hold")                  # window is only 2 days and both are wet: do not push past the window
        self.assertIn("wash", h["weather_reason"])
        self.assertIsNone(h["suggested"])

    def test_spray_shifts_inside_a_wide_window(self):
        p = plan(crop_slug="cowpea", planting=TODAY - timedelta(days=30), forecast=fc({0: 10, 1: 10}, default=0))
        s = tasks(p)["spray_1"]                                 # window 30-35 days: today..+5
        self.assertEqual(s["status"], "delayed")
        self.assertEqual(s["suggested"], (TODAY + timedelta(days=2)).isoformat())
        self.assertIn("wash", s["weather_reason"])

    def test_spray_not_delayed_by_a_light_shower(self):
        p = plan(crop_slug="cowpea", planting=TODAY - timedelta(days=30), forecast=fc({0: 2, 1: 1}, default=0))
        self.assertEqual(tasks(p)["spray_1"]["status"], "due_now")

    def test_fertiliser_ignores_moderate_rain_but_waits_for_very_heavy_rain(self):
        base = dict(planting=TODAY - timedelta(days=28))      # top dressing window opens today
        moderate = tasks(plan(**base, forecast=fc({0: 20, 1: 20}, default=0)))["top_dress"]
        self.assertEqual(moderate["status"], "due_now")
        heavy = tasks(plan(**base, forecast=fc({0: 60}, default=0)))["top_dress"]
        self.assertEqual(heavy["status"], "delayed"); self.assertEqual(heavy["suggested"], (TODAY + timedelta(days=1)).isoformat())
        self.assertIn("Heavy rain", heavy["weather_reason"])

    def test_heavy_rain_day_blocks_field_work(self):
        p = plan(planting=TODAY - timedelta(days=14), forecast=fc({0: 55}, default=0))
        self.assertEqual(tasks(p)["weed_1"]["suggested"], (TODAY + timedelta(days=1)).isoformat())

    def test_a_shift_never_passes_the_end_of_the_window(self):
        p = plan(planting=TODAY - timedelta(days=14), forecast=fc(default=60))     # heavy rain every day
        w = tasks(p)["weed_1"]
        self.assertEqual(w["status"], "hold"); self.assertIsNone(w["suggested"])

    def test_rainfed_planting_holds_while_the_whole_week_is_dry(self):
        dry = plan(planting=TODAY + timedelta(days=1), forecast=fc(default=0))
        t = tasks(dry)["plant"]
        self.assertEqual(t["status"], "hold"); self.assertIn("rains are established", t["weather_reason"])
        self.assertTrue(dry["weather"]["notes"])
        wet = tasks(plan(planting=TODAY + timedelta(days=1), forecast=fc({2: 15}, default=0)))["plant"]
        self.assertIn(wet["status"], ("upcoming", "due_now"))

    def test_irrigated_planting_is_not_held_by_a_dry_week(self):
        t = tasks(plan(planting=TODAY + timedelta(days=1), water="irrigated", forecast=fc(default=0)))["plant"]
        self.assertNotEqual(t["status"], "hold")

    def test_dates_beyond_the_forecast_are_left_alone_and_flagged(self):
        p = plan(planting=TODAY - timedelta(days=14), forecast=fc(default=60, days=7))
        far = tasks(plan(planting=TODAY + timedelta(days=40), forecast=fc(default=60)))["weed_1"]
        self.assertEqual(far["suggested"], far["window_start"]); self.assertTrue(far["beyond_forecast"])
        self.assertIn("not weather-adjusted", plan(forecast=fc())["weather"]["beyond"])

    def test_custom_thresholds_are_respected(self):
        th = Thresholds(heavy_rain_mm=10)
        p = plan(planting=TODAY - timedelta(days=14), forecast=fc({0: 12}, default=0), th=th)
        self.assertEqual(tasks(p)["weed_1"]["suggested"], (TODAY + timedelta(days=1)).isoformat())


class Irrigated(unittest.TestCase):
    def test_weekly_checks_only_for_irrigated_farms(self):
        self.assertFalse([k for k in tasks(plan()) if k.startswith("irrigation_check")])
        irr = [k for k in tasks(plan(water="irrigated")) if k.startswith("irrigation_check")]
        self.assertTrue(8 <= len(irr) <= 30)

    def test_rain_removes_the_need_to_irrigate(self):
        p = plan(water="irrigated", planting=TODAY - timedelta(days=3), forecast=fc({1: 12}, default=0))
        t = tasks(p)["irrigation_check_1"]
        self.assertTrue(t["weather_reason"] and "probably not needed" in t["weather_reason"])


class Statuses(unittest.TestCase):
    def test_past_due_and_ticked_tasks(self):
        p = plan(planting=TODAY - timedelta(days=40), saved={"weed_1": "done", "plant": "skipped"})
        t = tasks(p)
        self.assertEqual(t["weed_1"]["status"], "done"); self.assertEqual(t["plant"]["status"], "skipped")
        self.assertEqual(t["weed_2"]["status"], "overdue"); self.assertEqual(t["clear_land"]["status"], "overdue")

    def test_future_planting_everything_upcoming(self):
        self.assertTrue(all(t["status"] in ("upcoming", "due_now", "overdue") for t in tasks(plan(planting=TODAY + timedelta(days=60))).values()))


class Reminders(unittest.TestCase):
    def row(self, **kw):
        r = {"id": "t1", "task_key": "weed_1", "title": "First weeding", "rain_class": "field", "window_start": TODAY.isoformat(),
             "window_end": (TODAY + timedelta(days=3)).isoformat(), "status": "pending"}
        r.update(kw); return r

    def test_do_message_when_doable(self):
        ev = pp.reminder_events([self.row()], "Maize", TODAY, fc(default=0))
        self.assertEqual([e["key"] for e in ev], ["plan_t1_do"]); self.assertIn("First weeding", ev[0]["text"])

    def test_wait_message_on_the_first_day_only(self):
        f = fc({0: 60}, default=0)
        ev = pp.reminder_events([self.row()], "Maize", TODAY, f)
        self.assertEqual([e["key"] for e in ev], ["plan_t1_wait"]); self.assertIn("wait until", ev[0]["text"])
        later = pp.reminder_events([self.row(window_start=(TODAY - timedelta(days=1)).isoformat())], "Maize", TODAY, f)
        self.assertEqual(later, [])                                                  # no daily nagging mid-window

    def test_last_day_message(self):
        ev = pp.reminder_events([self.row(window_start=(TODAY - timedelta(days=3)).isoformat(), window_end=TODAY.isoformat())], "Maize", TODAY, fc(default=0))
        self.assertEqual([e["key"] for e in ev], ["plan_t1_last"])

    def test_nothing_for_done_future_or_past_tasks(self):
        self.assertEqual(pp.reminder_events([self.row(status="done")], "Maize", TODAY, None), [])
        self.assertEqual(pp.reminder_events([self.row(window_start=(TODAY + timedelta(days=2)).isoformat(), window_end=(TODAY + timedelta(days=4)).isoformat())], "Maize", TODAY, None), [])
        self.assertEqual(pp.reminder_events([self.row(window_start=(TODAY - timedelta(days=9)).isoformat(), window_end=(TODAY - timedelta(days=5)).isoformat())], "Maize", TODAY, None), [])

    def test_no_forecast_still_reminds(self):
        self.assertEqual([e["key"] for e in pp.reminder_events([self.row()], "Maize", TODAY, None)], ["plan_t1_do"])

    def test_flows_through_the_alert_job_once_per_day(self):
        sent, claimed = [], set()

        class Ch:
            def send(self, dest, msg, farm): sent.append(msg); return SendResult(True)
        target = {"farm_id": "f1", "user_id": "u1", "farm_name": "My farm", "latitude": 12.0, "longitude": 8.5, "push_enabled": True, "stage_reminders": True,
                  "language": "en", "rules": []}
        events = lambda t, day: pp.reminder_events([self.row()], "Maize", day, t["forecast"])
        stats = run_alert_job(targets=[target], subs_by_user={"u1": [{"endpoint": "e"}]}, get_forecast=lambda la, lo: fc(default=0),
                              translate=lambda k, lang: {"text": k}, channels={"push": Ch()}, available={"push": True, "whatsapp": False, "sms": False},
                              claim=lambda f, k, c: (f, k, c) not in claimed and not claimed.add((f, k, c)), release=lambda *a: None,
                              drop_sub=lambda e: None, today=TODAY, lagos_hour=9, extra_events=events)
        self.assertEqual(sum("First weeding" in str(m) for m in sent), 1)            # weather advisories may also go out; the task reminder goes exactly once
        self.assertEqual([k for (_, k, _) in claimed if k.startswith("plan_")], ["plan_t1_do"])


class Payloads(unittest.TestCase):
    OK = {"crop_slug": "maize", "planting_date": (date.today() + timedelta(days=5)).isoformat(), "state": "Kano"}

    def test_defaults_and_normalising(self):
        p = v.planner_payload(self.OK)
        self.assertEqual((p["water"], p["state"], p["savanna_zone"], p["maturity_days"]), ("rainfed", "Kano", None, None))
        p = v.planner_payload({**self.OK, "water": "irrigated", "savanna_zone": "sudan", "maturity_group": "early", "maturity_days": "90", "size_ha": "2.5"})
        self.assertEqual((p["maturity_days"], p["size_ha"]), (90, 2.5))

    def test_rejects_bad_values(self):
        for bad in ({"crop_slug": "Maize;DROP"}, {"crop_slug": ""}, {"planting_date": "soon"}, {"planting_date": "2020-01-01"},
                    {"planting_date": (date.today() + timedelta(days=900)).isoformat()}, {"water": "swamp"}, {"savanna_zone": "mars"},
                    {"maturity_group": "slow"}, {"maturity_days": 5}, {"maturity_days": 90.5}, {"size_ha": -1}, {"state": "Atlantis"}, {"farm_crop_id": "x"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                v.planner_payload({**self.OK, **bad})

    def test_task_payload(self):
        self.assertEqual(v.plan_task_payload({"status": "done", "note": " ok "})["note"], "ok")
        for bad in ({}, {"status": "finished"}, {"status": "done", "note": "x" * 301}):
            with self.assertRaises(ValidationError): v.plan_task_payload(bad)


class AppAndApi(unittest.TestCase):
    APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    API = (ROOT / "kanofarm" / "api" / "main.py").read_text(encoding="utf-8")

    def test_app_calls_only_endpoints_that_exist(self):
        for path in ("/api/planner/preview", "/api/planner/crops", "/planner", "/plan-tasks/", "/plan"):
            self.assertIn(path, self.API, path)
        for path in ("/api/planner/preview", "/api/planner/crops", "/plan-tasks/", "/planner"):
            self.assertIn(path, self.APP, path)

    def test_route_tile_and_farm_link(self):
        self.assertIn("planner: () => vPlanner", self.APP); self.assertIn('href="#/planner"', self.APP); self.assertIn("Planting plan</a>", self.APP)

    def test_every_status_the_engine_emits_has_a_label(self):
        for s in ("due_now", "delayed", "hold", "overdue", "done", "skipped", "upcoming"):
            self.assertRegex(self.APP, rf"\b{s}\b")


if __name__ == "__main__":
    unittest.main()
