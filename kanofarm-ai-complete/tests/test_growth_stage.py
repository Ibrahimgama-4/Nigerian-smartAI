"""Growth stages: only sourced FAO example lengths; nothing is guessed for other crops."""
import json, unittest
from datetime import date
from pathlib import Path
from kanofarm.services import growth_stage as gs
from kanofarm.services.alerts_job import run_alert_job
from kanofarm.services.i18n import translate
from kanofarm.services.notify import SendResult
from kanofarm.services.weather_parser import parse_forecast
from tests.test_weather import payload

# Totals as printed in FAO Irrigation and Drainage Paper 56, Table 11 (checked 2026-10-05).
FAO_TOTALS = {"maize": 125, "groundnut": 130, "rice": 150, "soybean": 85, "cassava": 210, "millet": 105}


# Sorghum is deliberately absent: the sorghum rows I could read from Table 11 did not add up (20+35+40+30 = 125, listed as 130),
# and the table could not be re-checked directly. Add it only after reading the printed table.
class Data(unittest.TestCase):
    def test_every_crop_matches_the_fao_totals_and_has_four_stages(self):
        data = json.loads((Path(gs.__file__).resolve().parents[2] / "data" / "crop_stages.json").read_text())
        self.assertEqual(set(data["crops"]), set(FAO_TOTALS))
        for slug, c in data["crops"].items():
            self.assertEqual(len(c["lengths"]), 4, slug); self.assertTrue(all(isinstance(n, int) and n > 0 for n in c["lengths"]))
            self.assertEqual(sum(c["lengths"]), FAO_TOTALS[slug], slug)
            self.assertIn(c["region_fit"], data["region_notes"])
        self.assertEqual(data["_status"], "draft_unreviewed")
        self.assertTrue(data["source"]["url"].startswith("https://www.fao.org/"))
        self.assertEqual([s["key"] for s in data["stages"]], ["initial", "development", "mid", "late"])


class Lookup(unittest.TestCase):
    def test_unknown_crop_has_no_stage_and_no_guess(self):
        for slug in ("tomato", "yam", "cocoa", "nonsense"):
            r = gs.stage_info(slug, 30)
            self.assertFalse(r["available"]); self.assertNotIn("stage", r); self.assertIn("no sourced stage data", r["note"])

    def test_maize_boundaries(self):
        for dap, key in ((0, "initial"), (19, "initial"), (20, "development"), (54, "development"), (55, "mid"),
                         (94, "mid"), (95, "late"), (124, "late")):
            self.assertEqual(gs.stage_info("maize", dap)["stage"], key, dap)

    def test_beyond_reference_is_not_a_stage(self):
        r = gs.stage_info("maize", 125)
        self.assertIsNone(r["stage"]); self.assertTrue(r["beyond_reference"]); self.assertIn("125-day", r["note"])

    def test_future_planting(self):
        self.assertIsNone(gs.stage_info("maize", -1)["stage"])

    def test_details_for_the_screen(self):
        r = gs.stage_info("maize", 60)
        self.assertEqual(r["stage_name"], "Mid-season"); self.assertEqual(r["days_left_in_stage"], 35)
        self.assertEqual(r["next_stage"], "Late season (ripening)"); self.assertEqual(r["next_stage_in_days"], 35)
        self.assertEqual(r["review_status"], "draft_unreviewed"); self.assertEqual(r["region_fit"], "nigeria")
        self.assertEqual(r["total_days"], 125); self.assertFalse(r["scaled_to_your_harvest_date"])
        last = gs.stage_info("maize", 110)
        self.assertIsNone(last["next_stage"])

    def test_other_region_crops_say_so(self):
        self.assertIn("rough guide", gs.stage_info("millet", 10)["region_note"])
        self.assertIn("not specifically Nigeria", gs.stage_info("rice", 10)["region_note"])

    def test_timeline_is_contiguous(self):
        tl = gs.stage_info("rice", 1)["timeline"]
        self.assertEqual(tl[0]["from_dap"], 0)
        for a, b in zip(tl, tl[1:]): self.assertEqual(b["from_dap"], a["to_dap"] + 1)
        self.assertEqual(tl[-1]["to_dap"] + 1, 150)


class Scaling(unittest.TestCase):
    def test_farmers_harvest_date_stretches_the_proportions_and_says_so(self):
        r = gs.stage_info("maize", 10, expected_days=100)               # 0.8 x [20,35,40,30]
        self.assertTrue(r["scaled_to_your_harvest_date"]); self.assertEqual(r["total_days"], 100)
        self.assertEqual([s["days"] for s in r["timeline"]], [16, 28, 32, 24]); self.assertIn("estimate", r["scale_note"])

    def test_total_always_matches_the_farmers_estimate(self):
        for n in range(75, 200):
            r = gs.stage_info("maize", 0, expected_days=n)
            self.assertEqual(sum(s["days"] for s in r["timeline"]), n if r["scaled_to_your_harvest_date"] else 125, n)

    def test_implausible_harvest_dates_are_ignored(self):
        for n in (30, 74, 201, 400):
            r = gs.stage_info("maize", 10, expected_days=n)
            self.assertFalse(r["scaled_to_your_harvest_date"]); self.assertEqual(r["total_days"], 125)
        self.assertTrue(gs.stage_info("maize", 10, expected_days=75)["scaled_to_your_harvest_date"])      # exactly 0.6x is accepted
        self.assertTrue(gs.stage_info("maize", 10, expected_days=200)["scaled_to_your_harvest_date"])     # exactly 1.6x is accepted

    def test_same_as_reference_is_not_called_an_adjustment(self):
        self.assertFalse(gs.stage_info("maize", 10, expected_days=125)["scaled_to_your_harvest_date"])

    def test_days_between(self):
        self.assertEqual(gs.days_between("2026-06-01", "2026-09-09"), 100)
        for bad in (None, "", "garbage", "2026-05-01"): self.assertIsNone(gs.days_between("2026-06-01", bad))


class Reminders(unittest.TestCase):
    def test_only_on_the_day_a_stage_begins(self):
        self.assertIsNone(gs.stage_start_event("maize", "Maize", 0))        # planting day is not a reminder
        self.assertIsNone(gs.stage_start_event("maize", "Maize", 19))
        ev = gs.stage_start_event("maize", "Maize", 20)
        self.assertEqual(ev["key"], "stage_maize_development"); self.assertEqual(ev["kind"], "stage")
        self.assertIn("Crop development", ev["text"]); self.assertIn("not a forecast", ev["text"]); self.assertIn("day 20", ev["text"])
        self.assertIsNone(gs.stage_start_event("maize", "Maize", 21))
        self.assertIsNone(gs.stage_start_event("maize", "Maize", 125))
        self.assertEqual({gs.stage_start_event("maize", "Maize", d)["key"] for d in (20, 55, 95)},
                         {"stage_maize_development", "stage_maize_mid", "stage_maize_late"})

    def test_no_reminder_for_a_crop_without_a_source(self):
        self.assertIsNone(gs.stage_start_event("tomato", "Tomato", 20))

    def test_reminder_follows_the_farmers_harvest_date(self):
        self.assertIsNotNone(gs.stage_start_event("maize", "Maize", 16, expected_days=100))   # 0.8x: development starts day 16
        self.assertIsNone(gs.stage_start_event("maize", "Maize", 20, expected_days=100))


FC = parse_forecast(payload([5] * 7, [5] * 7))     # calm weather: no weather alerts at all


class JobIntegration(unittest.TestCase):
    def run_job(self, target, today=date(2026, 9, 24), planting="2026-09-04", harvest=None):
        sent = []
        class Push:
            def send(self, sub, msg, farm_name=""): sent.append(msg); return SendResult(True)
        crop = {"slug": "maize", "name_en": "Maize", "planting_date": planting, "expected_harvest_date": harvest}
        def stage_events(t, day):
            ev = gs.stage_start_event(crop["slug"], crop["name_en"], (day - date.fromisoformat(crop["planting_date"])).days,
                                      gs.days_between(crop["planting_date"], crop["expected_harvest_date"]))
            return [ev] if ev else []
        stats = run_alert_job(targets=[target], subs_by_user={"u1": [{"endpoint": "https://e"}]}, get_forecast=lambda a, b: FC,
                              translate=translate, channels={"push": Push()}, available={"push": True, "whatsapp": False, "sms": False},
                              claim=lambda *a: True, release=lambda *a: None, drop_sub=lambda e: None, today=today, lagos_hour=6,
                              extra_events=stage_events)
        return stats, sent

    def target(self, **kw):
        t = {"user_id": "u1", "farm_id": "f1", "farm_name": "Farm", "latitude": 12.0, "longitude": 8.5, "language": "en",
             "push_enabled": True, "whatsapp_enabled": False, "sms_enabled": False, "phone": None, "min_level": "warning",
             "stage_reminders": True, "quiet_start": None, "quiet_end": None, "rules": []}
        t.update(kw); return t

    def test_stage_reminder_is_sent_even_when_weather_level_is_warnings_only(self):
        stats, sent = self.run_job(self.target())                       # 2026-09-24 minus 2026-09-04 = day 20
        self.assertEqual(stats["sent"], 1); self.assertIn("Crop development", sent[0]["body"])

    def test_switching_stage_reminders_off(self):
        stats, sent = self.run_job(self.target(stage_reminders=False))
        self.assertEqual(stats["sent"], 0); self.assertEqual(sent, [])

    def test_no_reminder_on_other_days(self):
        stats, _ = self.run_job(self.target(), planting="2026-09-03")  # day 21
        self.assertEqual(stats["sent"], 0)

    def test_quiet_hours_still_apply(self):
        stats, _ = self.run_job(self.target(quiet_start=4, quiet_end=8))
        self.assertEqual(stats["sent"], 0); self.assertEqual(stats["skipped_quiet"], 1)


if __name__ == "__main__":
    unittest.main()
