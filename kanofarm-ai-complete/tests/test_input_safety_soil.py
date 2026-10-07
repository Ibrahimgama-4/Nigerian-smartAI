import json
import re
import unittest
from datetime import date, timedelta
from pathlib import Path

from kanofarm.services import fertilizer_plan as fp
from kanofarm.services import input_safety as isafe
from kanofarm.services import validation as v
from kanofarm.services.validation import ValidationError

ROOT = Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 7)


class CheckProduct(unittest.TestCase):
    def test_banned_ingredients_match_by_name_and_alias(self):
        for q in ("paraquat", "Gramoxone 20SL", "chlorpyrifos 480EC", "Atrazine + metolachlor", "DDVP 100ml", "contains tallow amine"):
            r = isafe.check_product(q)
            self.assertEqual(r["result"], "on_list", q)
            self.assertTrue(r["matches"][0]["sources"])

    def test_unknown_product_is_never_called_safe_or_registered(self):
        r = isafe.check_product("Force Up glyphosate")
        self.assertEqual(r["result"], "not_on_our_list")
        self.assertIn("does NOT mean", r["message"])
        self.assertNotIn("safe to use", r["message"].lower())

    def test_short_or_empty_query_asks_for_more(self):
        for q in ("", "  ", "ab", None):
            self.assertEqual(isafe.check_product(q)["result"], "need_more")

    def test_whole_words_only(self):
        self.assertEqual(isafe.check_product("atrazinex")["result"], "not_on_our_list")

    def test_every_entry_has_a_source_and_a_valid_date(self):
        for i in isafe.data()["restricted_ingredients"]:
            self.assertTrue(i["sources"] and all(s["url"].startswith("https://") for s in i["sources"]), i["ingredient"])
            if i["effective"]:
                date.fromisoformat(i["effective"])
        self.assertIn("news reports", isafe.data()["_note"])

    def test_no_unverified_phone_numbers_in_the_data(self):
        text = (ROOT / "data" / "input_safety.json").read_text(encoding="utf-8")
        self.assertFalse(re.search(r"\b0?\d{3}[ -]?\d{3,4}[ -]?\d{4}\b|\b\d{5}\b", text.replace("2024", "").replace("2025", "").replace("2020", "")))


class SoilReading(unittest.TestCase):
    def test_levels_apply_only_above_the_published_threshold(self):
        r = fp.read_soil({"p_bray1_ppm": 15, "k_exch_cmolkg": 0.17})
        self.assertFalse(r["phosphorus"]["enough"]); self.assertFalse(r["potassium"]["enough"])
        r = fp.read_soil({"p_bray1_ppm": 15.1, "k_exch_cmolkg": 0.18})
        self.assertTrue(r["phosphorus"]["enough"]); self.assertTrue(r["potassium"]["enough"])

    def test_no_values_no_reading(self):
        r = fp.read_soil({})
        self.assertIsNone(r["phosphorus"]); self.assertIsNone(r["potassium"])


ROW = {"zone": "all", "crop_slug": "maize", "n_kg_ha": 100, "p2o5_kg_ha": 40, "k2o_kg_ha": 30, "source_name": "Test source",
       "source_url": "https://example.org", "source_year": 2020, "last_verified": "2026-10-01", "expires_at": "2027-10-01"}


class PickRow(unittest.TestCase):
    def test_matching_row(self):
        self.assertIs(fp.pick_row([ROW], "north_west", "maize", TODAY), ROW)

    def test_wrong_crop_or_zone_gives_nothing(self):
        self.assertIsNone(fp.pick_row([ROW], "north_west", "rice", TODAY))
        self.assertIsNone(fp.pick_row([{**ROW, "zone": "south_east"}], "north_west", "maize", TODAY))

    def test_expired_unsourced_or_rateless_rows_are_ignored(self):
        self.assertIsNone(fp.pick_row([{**ROW, "expires_at": "2026-10-06"}], "north_west", "maize", TODAY))
        self.assertIsNone(fp.pick_row([{**ROW, "source_name": ""}], "north_west", "maize", TODAY))
        self.assertIsNone(fp.pick_row([{**ROW, "n_kg_ha": None, "p2o5_kg_ha": None, "k2o_kg_ha": None}], "north_west", "maize", TODAY))
        self.assertIsNone(fp.pick_row([{**ROW, "expires_at": "garbage"}], "north_west", "maize", TODAY))

    def test_zone_specific_row_beats_all(self):
        specific = {**ROW, "zone": "north_west", "n_kg_ha": 90}
        self.assertEqual(fp.pick_row([ROW, specific], "north_west", "maize", TODAY)["n_kg_ha"], 90)
        self.assertEqual(fp.pick_row([specific, ROW], "north_west", "maize", TODAY)["n_kg_ha"], 90)


class BuildPlan(unittest.TestCase):
    def test_no_row_means_no_numbers(self):
        p = fp.build_plan(soil=None, size_ha=2, row=None)
        self.assertFalse(p["available"]); self.assertNotIn("items", p)
        self.assertIn("no rate", p["message"])

    def test_arithmetic(self):
        p = fp.build_plan(soil=None, size_ha=2, row=ROW)
        n = p["items"][0]
        self.assertEqual(n["nutrient"], "N"); self.assertEqual(n["product"], "Urea")
        self.assertAlmostEqual(n["product_kg_per_ha"], 217.4, 1)             # 100 / 0.46
        self.assertAlmostEqual(n["farm_total_kg"], 434.8, 1)
        self.assertAlmostEqual(n["farm_total_bags"], 8.7, 1)
        self.assertEqual(p["items"][1]["product_kg_per_ha"], 222.2)           # 40 / 0.18
        self.assertEqual(p["items"][2]["product_kg_per_ha"], 50.0)            # 30 / 0.60

    def test_enough_p_and_k_in_the_soil_skips_those_nutrients(self):
        p = fp.build_plan(soil={"p_bray1_ppm": 20, "k_exch_cmolkg": 0.3}, size_ha=1, row=ROW)
        by = {i["nutrient"]: i for i in p["items"]}
        self.assertFalse(by["N"]["skipped"])
        self.assertTrue(by["P2O5"]["skipped"]); self.assertTrue(by["K2O"]["skipped"])
        self.assertNotIn("product", by["P2O5"])

    def test_nitrogen_is_never_skipped_by_soil_values(self):
        p = fp.build_plan(soil={"p_bray1_ppm": 99, "k_exch_cmolkg": 9}, size_ha=1, row={**ROW, "p2o5_kg_ha": None, "k2o_kg_ha": None})
        self.assertEqual([i["nutrient"] for i in p["items"]], ["N"]); self.assertFalse(p["items"][0]["skipped"])

    def test_missing_farm_size_hides_totals_and_says_so(self):
        p = fp.build_plan(soil=None, size_ha=None, row=ROW)
        self.assertNotIn("farm_total_kg", p["items"][0]); self.assertTrue(p["size_note"])

    def test_source_and_disclaimer_always_present(self):
        p = fp.build_plan(soil=None, size_ha=1, row=ROW)
        self.assertEqual(p["source"]["name"], "Test source"); self.assertIn("not a prescription", p["disclaimer"])


class Payloads(unittest.TestCase):
    REPORT = {"kind": "agrochemical", "product_name": "Some herbicide", "problem": "Pack had no NAFDAC number", "state": "Kano", "lga": "Nasarawa"}

    def test_report_ok_and_state_normalised(self):
        r = v.input_report_payload({**self.REPORT, "state": "kano"})
        self.assertEqual(r["state"], "Kano"); self.assertIsNone(r["bought_on"])

    def test_report_rejects_bad_input(self):
        for bad in ({"kind": "gun"}, {"product_name": "x"}, {"problem": "bad"}, {"state": "Atlantis"}, {"bought_on": (date.today() + timedelta(days=3)).isoformat()}, {"bought_on": "yesterday"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                v.input_report_payload({**self.REPORT, **bad})

    def test_report_cannot_carry_status_or_user(self):
        r = v.input_report_payload({**self.REPORT, "status": "closed", "user_id": "x", "admin_note": "x"})
        self.assertFalse({"status", "user_id", "admin_note"} & set(r))

    def test_review(self):
        self.assertEqual(v.input_report_review_payload({"status": "closed", "admin_note": " done "})["admin_note"], "done")
        with self.assertRaises(ValidationError): v.input_report_review_payload({"status": "new"})

    def test_fertilizer_row(self):
        ok = {"zone": "all", "crop_slug": "maize", "n_kg_ha": 100, "source_name": "Some source", "last_verified": "2026-10-01", "expires_at": "2027-10-01"}
        self.assertEqual(v.fertilizer_reco_payload(ok)["n_kg_ha"], 100.0)
        for bad in ({"zone": "mars"}, {"n_kg_ha": None}, {"n_kg_ha": 501}, {"n_kg_ha": -1}, {"expires_at": "2026-10-01"}, {"source_name": ""},
                    {"source_name": "ab"}, {"source_url": "javascript:alert(1)"}, {"crop_slug": ""}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                v.fertilizer_reco_payload({**ok, **bad})

    def test_soil_payload_new_fields(self):
        s = v.soil_payload({"p_bray1_ppm": "12.5", "k_exch_cmolkg": "0.2", "lab_name": "ABU lab", "recorded_on": "2026-09-01"})
        self.assertEqual((s["p_bray1_ppm"], s["k_exch_cmolkg"], s["recorded_on"]), (12.5, 0.2, "2026-09-01"))
        self.assertNotIn("recorded_on", v.soil_payload({}))
        for bad in ({"p_bray1_ppm": -1}, {"k_exch_cmolkg": 25}, {"recorded_on": "2999-01-01"}, {"recorded_on": "x"}):
            with self.assertRaises(ValidationError, msg=str(bad)):
                v.soil_payload(bad)


class AppAndDocs(unittest.TestCase):
    APP = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    API = (ROOT / "kanofarm" / "api" / "main.py").read_text(encoding="utf-8")

    def test_app_calls_only_endpoints_that_exist(self):
        for path in ("/api/input-safety", "/api/input-safety/check", "/api/input-reports", "/api/admin/input-reports", "/api/admin/fertilizer-recommendations", "fertilizer-plan"):
            self.assertIn(path, self.API, path); self.assertIn(path, self.APP, path)

    def test_app_has_a_route_and_a_tile(self):
        self.assertIn("safety: vSafety", self.APP); self.assertIn('href="#/safety"', self.APP)

    def test_zone_lists_agree_with_the_database(self):
        sql = (ROOT / "database" / "migrations" / "0011_input_safety_soil_plan.sql").read_text(encoding="utf-8")
        for z in v.FERT_ZONES:
            self.assertIn(f"'{z}'", sql)

    def test_sql_limits_match_python(self):
        sql = (ROOT / "database" / "migrations" / "0011_input_safety_soil_plan.sql").read_text(encoding="utf-8")
        self.assertIn(">= 5 then", sql)          # 5 reports a day
        self.assertIn("between 0 and 500", sql)


if __name__ == "__main__":
    unittest.main()
