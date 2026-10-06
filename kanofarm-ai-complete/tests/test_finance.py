"""Farm finances and offline-safe records: pure logic only."""
import unittest
from datetime import date
from kanofarm.services import finance as fin
from kanofarm.services.validation import finance_payload, observation_payload, ValidationError

TODAY = date(2026, 10, 6)
CID = "11111111-1111-1111-1111-111111111111"
OK = {"kind": "expense", "category": "seed", "amount_ngn": "12500.50", "entry_date": "2026-10-01"}


class Validation(unittest.TestCase):
    def test_valid_entry(self):
        p = finance_payload({**OK, "season": " 2026 rainy ", "note": "Hybrid maize seed", "client_id": CID}, TODAY)
        self.assertEqual(p["amount_ngn"], 12500.5); self.assertEqual(p["season"], "2026 rainy"); self.assertEqual(p["client_id"], CID)

    def test_category_must_match_kind(self):
        with self.assertRaises(ValidationError): finance_payload({**OK, "category": "sale"}, TODAY)
        with self.assertRaises(ValidationError): finance_payload({**OK, "kind": "income"}, TODAY)
        self.assertEqual(finance_payload({**OK, "kind": "income", "category": "sale"}, TODAY)["kind"], "income")

    def test_bad_amounts(self):
        for bad in ("0", "-5", "abc", "", None, "1e12", "nan"):
            with self.assertRaises(ValidationError, msg=str(bad)): finance_payload({**OK, "amount_ngn": bad}, TODAY)

    def test_dates(self):
        with self.assertRaises(ValidationError): finance_payload({**OK, "entry_date": "2026-12-25"}, TODAY)
        with self.assertRaises(ValidationError): finance_payload({**OK, "entry_date": "yesterday"}, TODAY)
        self.assertEqual(finance_payload({**OK, "entry_date": "2026-10-07"}, TODAY)["entry_date"], "2026-10-07")   # Lagos may be a day ahead of UTC

    def test_ids_must_be_uuids(self):
        with self.assertRaises(ValidationError): finance_payload({**OK, "client_id": "not-a-uuid"}, TODAY)
        with self.assertRaises(ValidationError): finance_payload({**OK, "farm_crop_id": "1"}, TODAY)

    def test_long_note_rejected(self):
        with self.assertRaises(ValidationError): finance_payload({**OK, "note": "x" * 301}, TODAY)

    def test_observation_accepts_client_id(self):
        o = observation_payload({"kind": "note", "observed_on": "2026-10-01", "text": "t", "client_id": CID})
        self.assertEqual(o["client_id"], CID)
        with self.assertRaises(ValidationError): observation_payload({"kind": "note", "observed_on": "2026-10-01", "client_id": "x"})
        self.assertNotIn("client_id", observation_payload({"kind": "note", "observed_on": "2026-10-01"}))


E_ = lambda kind, cat, amt, season=None, crop=None, day="2026-09-01": {
    "kind": kind, "category": cat, "amount_ngn": amt, "season": season, "farm_crop_id": crop, "entry_date": day}


class Summary(unittest.TestCase):
    def test_empty(self):
        s = fin.summarize([], 2)
        self.assertEqual(s["total"]["profit_ngn"], 0.0); self.assertEqual(s["by_season"], []); self.assertEqual(s["expense_breakdown"], [])

    def test_totals_profit_and_per_hectare(self):
        rows = [E_("income", "sale", "150000.10", "A"), E_("expense", "seed", 30000, "A"), E_("expense", "labour", "20000.20", "A")]
        s = fin.summarize(rows, 2)
        self.assertEqual(s["total"]["income_ngn"], 150000.10); self.assertEqual(s["total"]["expense_ngn"], 50000.20)
        self.assertEqual(s["total"]["profit_ngn"], 99999.90); self.assertEqual(s["total"]["profit_per_ha_ngn"], 49999.95)

    def test_no_float_drift(self):
        rows = [E_("income", "sale", "0.1"), E_("income", "sale", "0.2")]
        self.assertEqual(fin.summarize(rows)["total"]["income_ngn"], 0.3)

    def test_loss_is_negative(self):
        s = fin.summarize([E_("expense", "seed", 1000), E_("income", "sale", 400)], 1)
        self.assertEqual(s["total"]["profit_ngn"], -600.0); self.assertEqual(s["total"]["profit_per_ha_ngn"], -600.0)

    def test_per_hectare_needs_a_farm_size(self):
        for size in (None, 0, "0", -1, "abc"):
            s = fin.summarize([E_("income", "sale", 100)], size)
            self.assertIsNone(s["total"]["profit_per_ha_ngn"], msg=str(size)); self.assertIsNotNone(s["per_ha_note"])

    def test_seasons_newest_first_and_missing_season_named(self):
        rows = [E_("income", "sale", 10, "Old", day="2025-09-01"), E_("income", "sale", 20, "New", day="2026-09-01"), E_("expense", "seed", 5)]
        names = [b["season"] for b in fin.summarize(rows)["by_season"]]
        self.assertIn("New", names); self.assertIn(fin.NO_SEASON, names)
        self.assertLess(names.index("New"), names.index("Old"))

    def test_by_crop_keeps_whole_farm_separate_and_last(self):
        rows = [E_("income", "sale", 100, crop="c1"), E_("expense", "labour", 40), E_("expense", "seed", 10, crop="gone")]
        by = fin.summarize(rows, 1, {"c1": "Maize"})["by_crop"]
        names = [b["crop"] for b in by]
        self.assertEqual(names[-1], fin.WHOLE_FARM); self.assertIn("Maize", names); self.assertIn("Crop (removed)", names)
        self.assertNotIn("profit_per_ha_ngn", by[0])        # per-crop area is unknown, so no per-crop per-hectare figure

    def test_expense_breakdown_sorted_with_shares(self):
        rows = [E_("expense", "seed", 25), E_("expense", "labour", 75), E_("income", "sale", 999)]
        b = fin.summarize(rows)["expense_breakdown"]
        self.assertEqual([x["category"] for x in b], ["labour", "seed"]); self.assertEqual(b[0]["share_pct"], 75.0)

    def test_every_category_has_a_label(self):
        from kanofarm.services.validation import INCOME_CATEGORIES, EXPENSE_CATEGORIES
        for c in INCOME_CATEGORIES + EXPENSE_CATEGORIES: self.assertIn(c, fin.CATEGORY_LABELS)

    def test_note_says_figures_are_only_what_was_typed(self):
        self.assertIn("only add up what you entered", fin.summarize([])["note"])


if __name__ == "__main__":
    unittest.main()
