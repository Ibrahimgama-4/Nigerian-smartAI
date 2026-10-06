import re
import unittest
from pathlib import Path

from kanofarm.services import market as mk
from kanofarm.services import validation as v
from kanofarm.services.validation import ValidationError

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")


def good(**kw):
    d = {"phone": "0803 123 4567", "proof_kind": "cooperative", "proof_detail": "Member of Kura Rice Farmers Cooperative", "consent": True}
    d.update(kw)
    return d


class RequestValidation(unittest.TestCase):
    def test_a_good_request(self):
        r = v.verification_request_payload(good())
        self.assertEqual(r, {"phone": "+2348031234567", "proof_kind": "cooperative", "proof_detail": "Member of Kura Rice Farmers Cooperative"})

    def test_phone_must_be_a_nigerian_mobile(self):
        for bad in (None, "", "123", "+447911123456", "0203 123 4567"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                v.verification_request_payload(good(phone=bad))

    def test_unknown_evidence_kind_is_refused(self):
        with self.assertRaises(ValidationError):
            v.verification_request_payload(good(proof_kind="passport"))

    def test_evidence_is_words_not_numbers(self):
        for bad in ("My NIN is 12345678901", "BVN 22222222222", "account 0123456789", "call 0803 123 4567 now", "id 1234-5678-901"):
            with self.assertRaises(ValidationError, msg=bad):
                v.verification_request_payload(good(proof_detail=bad))
        # years and small numbers are fine
        v.verification_request_payload(good(proof_detail="Member since 2019, farm of 12 hectares"))

    def test_evidence_must_be_present_and_not_tiny(self):
        for bad in (None, "", "  ", "ab"):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                v.verification_request_payload(good(proof_detail=bad))

    def test_consent_must_be_exactly_true(self):
        for bad in (None, False, "true", 1):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                v.verification_request_payload(good(consent=bad))

    def test_control_characters_are_stripped(self):
        r = v.verification_request_payload(good(proof_detail="Member\x00 of\x07 a cooperative"))
        self.assertNotIn("\x00", r["proof_detail"]); self.assertNotIn("\x07", r["proof_detail"])


class ReviewValidation(unittest.TestCase):
    def approve(self, **kw):
        d = {"action": "approve", "confirmed_phone": True, "checked_proof": True}; d.update(kw); return d

    def test_approve_needs_both_checks(self):
        self.assertEqual(v.verification_review_payload(self.approve())["action"], "approve")
        for kw in ({"confirmed_phone": False}, {"checked_proof": False}, {"confirmed_phone": None}, {"checked_proof": "yes"}):
            with self.assertRaises(ValidationError, msg=str(kw)):
                v.verification_review_payload(self.approve(**kw))

    def test_reject_and_revoke_need_a_reason(self):
        for action in ("reject", "revoke"):
            with self.assertRaises(ValidationError):
                v.verification_review_payload({"action": action})
            self.assertEqual(v.verification_review_payload({"action": action, "note": "could not reach the number"})["note"], "could not reach the number")

    def test_unknown_action(self):
        with self.assertRaises(ValidationError):
            v.verification_review_payload({"action": "promote"})

    def test_months_default_and_limit(self):
        self.assertEqual(v.verification_review_payload(self.approve())["months"], mk.VERIFY_MAX_MONTHS)
        self.assertEqual(v.verification_review_payload(self.approve(months=3))["months"], 3)
        for bad in (0, 13, -1, 2.5, "x"):
            with self.assertRaises(ValidationError, msg=str(bad)):
                v.verification_review_payload(self.approve(months=bad))


class ListingView(unittest.TestCase):
    ROW = {"id": "i", "kind": "for_sale", "title": "Maize", "category": "produce", "seller_name": "Ada", "seller_verified": True,
           "contact_phone": "+2348031234567", "image_paths": [], "owner_id": "SECRET", "status": "active"}

    def test_badge_is_exposed_and_owner_is_not(self):
        out = mk.listing_view(self.ROW, "https://x.supabase.co")
        self.assertIs(out["seller_verified"], True)
        self.assertNotIn("owner_id", out); self.assertNotIn("SECRET", str(out))

    def test_missing_or_odd_values_mean_not_verified(self):
        for val in (None, False, 0, ""):
            self.assertIs(mk.listing_view({**self.ROW, "seller_verified": val}, "https://x.supabase.co")["seller_verified"], False)
        row = dict(self.ROW); del row["seller_verified"]
        self.assertIs(mk.listing_view(row, "https://x.supabase.co")["seller_verified"], False)

    def test_public_columns_include_the_badge_but_not_the_owner(self):
        cols = mk.PUBLIC_COLUMNS.split(",")
        self.assertIn("seller_verified", cols); self.assertNotIn("owner_id", cols)

    def test_report_thresholds_match_the_database(self):
        sql = (ROOT / "database" / "migrations" / "0010_seller_verification.sql").read_text(encoding="utf-8")
        self.assertIn(f"then {mk.REPORTS_TO_HIDE_VERIFIED} else {mk.REPORTS_TO_HIDE} end", sql)


class FrontEndAndBackendAgree(unittest.TestCase):
    def test_every_evidence_kind_has_a_label_in_the_app(self):
        for key in mk.PROOF_KINDS:
            self.assertRegex(APP_JS, rf"\b{key}\b", key)

    def test_app_shows_the_badge_and_filter(self):
        self.assertIn("Verified seller", APP_JS); self.assertIn("seller_verified", APP_JS); self.assertIn("Verified sellers only", APP_JS)

    def test_app_calls_only_endpoints_that_exist(self):
        api = (ROOT / "kanofarm" / "api" / "main.py").read_text(encoding="utf-8")
        for path in ("/api/seller/verification", "/api/admin/verifications"):
            self.assertIn(path, api); self.assertIn(path, APP_JS)
        self.assertTrue(re.search(r'"/api/admin/verifications/\{user_id\}/review"', api))

    def test_database_message_limits_match_python_constants(self):
        sql = (ROOT / "database" / "migrations" / "0010_seller_verification.sql").read_text(encoding="utf-8")
        self.assertIn(f">= {mk.MAX_ACTIVE_PER_USER} then", sql)           # 20 live listings
        self.assertIn(f"interval '{mk.LISTING_DAYS} days'", sql)           # 30-day life


if __name__ == "__main__":
    unittest.main()
