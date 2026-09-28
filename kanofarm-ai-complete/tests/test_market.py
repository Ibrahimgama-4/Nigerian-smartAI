import base64, json, unittest
from kanofarm.services import market as mk
from kanofarm.services import validation as v
from kanofarm.services.validation import ValidationError
from kanofarm.services.supabase_rest import Supabase

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 200).decode()
def body(**kw):
    b = {"title": "Fresh white maize", "category": "produce", "state": "kano", "contact_phone": "0803 123 4567",
         "consent_public_contact": True, "images": [JPEG], "quantity": 20, "quantity_unit": "bag",
         "price_ngn": 45000, "price_unit": "bag"}
    b.update(kw); return b

class Phone(unittest.TestCase):
    def test_accepts_common_nigerian_formats(self):
        for raw in ("08031234567", "0803 123 4567", "+234 803 123 4567", "2348031234567", "0803-123-4567"):
            self.assertEqual(mk.normalize_phone(raw), "+2348031234567", raw)
    def test_rejects_bad_numbers(self):
        for raw in ("", "12345", "0603 123 4567", "+44 7700 900123", "0803 123 456", None, 8031234567):
            self.assertIsNone(mk.normalize_phone(raw), raw)
    def test_display_and_links(self):
        self.assertEqual(mk.display_phone("+2348031234567"), "0803 123 4567")
        self.assertEqual(mk.whatsapp_url("+2348031234567"), "https://wa.me/2348031234567")

class Helpers(unittest.TestCase):
    def test_search_term_cannot_inject_filter_syntax(self):
        t = mk.clean_search_term("maize),status.eq.hidden,(x*")
        for bad in (",", "(", ")", "*", "."): self.assertNotIn(bad, t)
        self.assertIsNone(mk.search_filter("")); self.assertIn("title.ilike.*maize*", mk.search_filter("maize"))
    def test_image_url(self):
        self.assertEqual(mk.public_image_url("https://p.supabase.co/", "u/l/1.jpg"), "https://p.supabase.co/storage/v1/object/public/market/u/l/1.jpg")
    def test_listing_view_never_leaks_owner(self):
        row = {"id": "i", "owner_id": "SECRET-OWNER", "title": "t", "category": "produce", "contact_phone": "+2348031234567",
               "image_paths": ["u/l/1.jpg"], "negotiable": None, "latitude": 12.1}
        out = mk.listing_view(row, "https://p.supabase.co")
        self.assertNotIn("owner_id", out); self.assertNotIn("latitude", out); self.assertNotIn("SECRET-OWNER", json.dumps(out))
        self.assertEqual(out["contact"]["call"], "tel:+2348031234567"); self.assertEqual(len(out["images"]), 1)
    def test_listing_view_hides_malformed_phone(self):
        self.assertIsNone(mk.listing_view({"contact_phone": "javascript:alert(1)"}, "https://x")["contact"])
    def test_strip_control_characters(self):
        self.assertEqual(mk.strip_control("a\x00b\x07c\nd"), "abc\nd")

class Payload(unittest.TestCase):
    def test_valid(self):
        p = v.market_payload(body())
        l = p["listing"]
        self.assertEqual(l["state"], "Kano"); self.assertEqual(l["contact_phone"], "+2348031234567")
        self.assertEqual(l["quantity_unit"], "bag"); self.assertEqual(len(p["images"]), 1); self.assertEqual(p["images"][0][1], "image/jpeg")
    def test_requires_consent_for_public_phone(self):
        with self.assertRaises(ValidationError): v.market_payload(body(consent_public_contact=False))
        b = body(); del b["consent_public_contact"]
        with self.assertRaises(ValidationError): v.market_payload(b)
    def test_sale_needs_a_photo_but_wanted_does_not(self):
        with self.assertRaises(ValidationError): v.market_payload(body(images=[]))
        self.assertEqual(v.market_payload(body(kind="wanted", images=[]))["images"], [])
    def test_bad_inputs(self):
        for kw in ({"title": "ab"}, {"title": ""}, {"category": "pesticides"}, {"kind": "auction"}, {"state": "Neverland"},
                   {"contact_phone": "123"}, {"quantity": -1}, {"price_ngn": -5}, {"quantity_unit": "ton"},
                   {"price_unit": None}, {"images": [JPEG] * 5}):
            with self.assertRaises(ValidationError, msg=str(kw)): v.market_payload(body(**kw))
    def test_photo_must_really_be_an_image_and_small_enough(self):
        with self.assertRaises(ValidationError): v.market_payload(body(images=[base64.b64encode(b"<html>nope").decode()]))
        with self.assertRaises(ValidationError): v.market_payload(body(images=["@@notbase64@@"]))
        big = base64.b64encode(b"\xff\xd8\xff" + b"0" * (mk.MAX_IMAGE_BYTES + 10)).decode()
        with self.assertRaises(ValidationError): v.market_payload(body(images=[big]))
    def test_free_text_is_cleaned(self):
        l = v.market_payload(body(title="Yam\x00 tubers", description="ok\x07 text"))["listing"]
        self.assertEqual(l["title"], "Yam tubers"); self.assertEqual(l["description"], "ok text")
    def test_price_optional_and_unit_dropped_without_price(self):
        l = v.market_payload(body(price_ngn=None, price_unit="bag", quantity=None))["listing"]
        self.assertIsNone(l["price_ngn"]); self.assertIsNone(l["price_unit"]); self.assertIsNone(l["quantity_unit"])
    def test_actions(self):
        self.assertEqual(v.market_action_payload({"action": "sold"}), "sold")
        with self.assertRaises(ValidationError): v.market_action_payload({"action": "hidden"})
        with self.assertRaises(ValidationError): v.market_moderation_payload({"action": "sold"})
        self.assertEqual(v.market_report_payload({"reason": "scam\x00"}), "scam"); self.assertEqual(v.market_report_payload({}), "")

class FakeHttp:
    def __init__(self, status=200, body=b"[]"): self.calls, self.status, self.body = [], status, body
    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, headers, body)); return self.status, self.body

class Rest(unittest.TestCase):
    def test_anonymous_calls_send_no_authorization_header(self):
        h = FakeHttp(); Supabase("https://p.supabase.co", "ANON", h).select(None, "market_listings", {"status": "eq.active"})
        self.assertNotIn("Authorization", h.calls[0][2]); self.assertEqual(h.calls[0][2]["apikey"], "ANON")
    def test_spaces_are_percent_20_not_plus(self):
        h = FakeHttp(); Supabase("https://p", "k", h).select(None, "market_listings", {"state": "eq.Federal Capital Territory"})
        self.assertIn("Federal%20Capital%20Territory", h.calls[0][1]); self.assertNotIn("+Capital", h.calls[0][1])
    def test_rpc_and_bad_function_name(self):
        h = FakeHttp(200, b"null"); Supabase("https://p", "k", h).rpc("jwt", "report_listing", {"p_listing": "x"})
        self.assertIn("/rest/v1/rpc/report_listing", h.calls[0][1]); self.assertEqual(h.calls[0][2]["Authorization"], "Bearer jwt")
        with self.assertRaises(ValueError): Supabase("https://p", "k", h).rpc("jwt", "x; drop table", {})
    def test_remove_object_never_raises(self):
        self.assertTrue(Supabase("https://p", "k", FakeHttp(200)).remove_object("j", "market", "u/l/1.jpg"))
        self.assertFalse(Supabase("https://p", "k", FakeHttp(403)).remove_object("j", "market", "u/l/1.jpg"))
        def boom(*a, **k): raise OSError("down")
        self.assertFalse(Supabase("https://p", "k", boom).remove_object("j", "market", "u/l/1.jpg"))
if __name__ == "__main__": unittest.main()
