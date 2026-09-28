"""Tests for the nationwide (all-36-states) expansion: state/zone reference data, zone-filtered
crop calendar, and state validation."""
import unittest
from kanofarm.services.calendar import calendar, zone_for_state, states
from kanofarm.services.validation import nigeria_state, farm_payload, ValidationError, LANGUAGES

class States(unittest.TestCase):
    def test_37_entries_36_states_plus_fct(self):
        d = states()
        self.assertEqual(len(d["states"]), 37)
        names = {s["name"] for s in d["states"]}
        self.assertIn("Federal Capital Territory", names); self.assertIn("Lagos", names); self.assertIn("Kano", names)
    def test_every_state_has_a_real_zone_and_coordinates(self):
        d = states()
        for s in d["states"]:
            self.assertIn(s["zone"], d["zones"]); self.assertTrue(4 <= s["capital_latitude"] <= 14)
            self.assertTrue(2.5 <= s["capital_longitude"] <= 15)
    def test_no_duplicate_states(self):
        names = [s["name"] for s in states()["states"]]
        self.assertEqual(len(names), len(set(names)))
    def test_zone_lookup_case_insensitive(self):
        self.assertEqual(zone_for_state("KANO"), "north_west")
        self.assertEqual(zone_for_state("lagos"), "south_west")
        self.assertEqual(zone_for_state("rivers"), "south_south")
        self.assertIsNone(zone_for_state("Neverland"))
        self.assertIsNone(zone_for_state(None))

class Calendar(unittest.TestCase):
    def test_every_entry_has_a_source_and_is_marked_draft(self):
        for e in calendar()["entries"]:
            self.assertTrue(e["source_url"]); self.assertEqual(e["review_status"], "draft_unreviewed")
            self.assertTrue(e["zones"])  # every entry says who it applies to
    def test_zone_filter_includes_all_tagged_entries(self):
        nw = calendar(zone="north_west")["entries"]
        self.assertTrue(any(e["crop_slug"] == "maize" for e in nw))       # tagged "all"
        self.assertTrue(any(e["crop_slug"] == "sorghum" for e in nw))     # tagged north_west specifically
        self.assertFalse(any(e["crop_slug"] == "yam" for e in nw))        # yam not tagged for north_west
    def test_south_west_gets_yam_and_cassava_not_millet(self):
        sw = calendar(zone="south_west")["entries"]
        slugs = {e["crop_slug"] for e in sw}
        self.assertIn("yam", slugs); self.assertIn("cassava", slugs); self.assertNotIn("millet", slugs)
    def test_crop_and_zone_filters_combine(self):
        r = calendar(crop_slug="maize", zone="south_west")["entries"]
        self.assertTrue(r); self.assertTrue(all(e["crop_slug"] == "maize" for e in r))
    def test_unknown_zone_returns_only_nationwide_entries(self):
        r = calendar(zone="mars")["entries"]
        self.assertTrue(r); self.assertTrue(all("all" in e["zones"] for e in r))

class StateValidation(unittest.TestCase):
    def test_accepts_known_state_any_case(self):
        self.assertEqual(nigeria_state({"state": "lagos"}), "Lagos")
        self.assertEqual(nigeria_state({"state": "FEDERAL CAPITAL TERRITORY"}), "Federal Capital Territory")
    def test_rejects_unknown_state(self):
        with self.assertRaises(ValidationError): nigeria_state({"state": "Timbuktu"})
    def test_optional_unless_required(self):
        self.assertIsNone(nigeria_state({}))
        with self.assertRaises(ValidationError): nigeria_state({}, required=True)
    def test_farm_payload_requires_valid_state(self):
        base = {"name": "F", "latitude": 6.6, "longitude": 3.3}
        with self.assertRaises(ValidationError): farm_payload(base)               # missing state
        with self.assertRaises(ValidationError): farm_payload({**base, "state": "Neverland"})
        self.assertEqual(farm_payload({**base, "state": "oyo"})["state"], "Oyo")
    def test_languages_expanded_beyond_hausa(self):
        self.assertEqual(set(LANGUAGES), {"en", "ha", "yo", "ig", "pcm"})
if __name__ == "__main__": unittest.main()
