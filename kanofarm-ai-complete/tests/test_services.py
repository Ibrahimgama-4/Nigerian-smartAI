import base64, json, unittest
from datetime import date
from kanofarm.services import validation as v
from kanofarm.services.validation import ValidationError
from kanofarm.services.supabase_rest import Supabase, SupabaseError
from kanofarm.services.soil_advice import soil_advice
from kanofarm.services.ipm import ipm_plan
from kanofarm.services.pesticides import verified_products, NONE_MSG
from kanofarm.services.quality import quality_gate
from kanofarm.services.diagnosis import build_diagnosis
from kanofarm.services.scan_service import run_scan
from kanofarm.services.model_client import ModelClient, ModelError, validate_prediction
from kanofarm.services.alerts import thresholds_from_rules, filter_advisories, alert_rows
from kanofarm.services.weather_rules import Advisory, Thresholds
from kanofarm.services.irrigation import irrigation_guidance
import unittest.mock
from kanofarm.services.assistant import build_request, build_context, ask, SYSTEM, AssistantError, default_model
from kanofarm.services.calendar import calendar

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 50
GOOD_Q = {"brightness": 120, "sharpness": 90}
def scan_body(**kw):
    b = {"image_b64": base64.b64encode(JPEG).decode(), "quality": GOOD_Q}; b.update(kw); return b

class Validation(unittest.TestCase):
    def test_farm_ok_and_bounds(self):
        f = v.farm_payload({"name": "A", "state": "Kano", "latitude": 12, "longitude": 8.5})
        self.assertEqual(f["latitude"], 12.0); self.assertEqual(f["state"], "Kano")
        with self.assertRaises(ValidationError): v.farm_payload({"name": "A", "state": "Kano", "latitude": 51, "longitude": 8})
        with self.assertRaises(ValidationError): v.farm_payload({"name": "", "state": "Kano", "latitude": 12, "longitude": 8})
        with self.assertRaises(ValidationError): v.farm_payload({"name": "A", "latitude": 12, "longitude": 8})   # missing state
    def test_uuid(self):
        with self.assertRaises(ValidationError): v.uuid_str("1; drop table farms")
    def test_farm_crop_dates(self):
        cid = "11111111-1111-1111-1111-111111111111"
        with self.assertRaises(ValidationError): v.farm_crop_payload({"crop_id": cid, "planting_date": "2999-01-01"})
        with self.assertRaises(ValidationError):
            v.farm_crop_payload({"crop_id": cid, "planting_date": "2026-06-15", "expected_harvest_date": "2026-05-01"})
        self.assertEqual(v.farm_crop_payload({"crop_id": cid, "planting_date": "2026-06-15"})["planting_date"], "2026-06-15")
    def test_scan_sniffs_content_not_claimed_type(self):
        self.assertEqual(v.scan_payload(scan_body(media_type="image/png"))["media_type"], "image/jpeg")
        with self.assertRaises(ValidationError):
            v.scan_payload({"image_b64": base64.b64encode(b"<html>not an image").decode(), "quality": GOOD_Q})
    def test_scan_limits_and_required(self):
        big = base64.b64encode(b"\xff\xd8\xff" + b"0" * 2_100_000).decode()
        with self.assertRaises(ValidationError): v.scan_payload({"image_b64": big, "quality": GOOD_Q})
        with self.assertRaises(ValidationError): v.scan_payload({"image_b64": base64.b64encode(JPEG).decode()})
        with self.assertRaises(ValidationError): v.scan_payload({"image_b64": "@@notbase64@@", "quality": GOOD_Q})
    def test_pesticide_needs_source_and_dates(self):
        base = dict(product_name="P", active_ingredient="X", registration_number="N1", registration_status="registered",
                    target_crop="maize", target_pest_or_disease="t", source_name="S", last_verified="2026-09-01", expires_at="2027-03-01")
        self.assertTrue(v.pesticide_payload(base))
        for k in ("source_name", "registration_number", "last_verified", "expires_at"):
            with self.assertRaises(ValidationError): v.pesticide_payload({**base, k: None})
        with self.assertRaises(ValidationError): v.pesticide_payload({**base, "expires_at": "2026-01-01"})
    def test_smallint_fields_are_whole_numbers(self):
        base = dict(product_name="P", active_ingredient="X", registration_number="N1", registration_status="registered",
                    target_crop="maize", target_pest_or_disease="t", source_name="S", last_verified="2026-09-01", expires_at="2027-03-01")
        self.assertIsInstance(v.pesticide_payload({**base, "pre_harvest_interval_days": "7"})["pre_harvest_interval_days"], int)
        with self.assertRaises(ValidationError): v.pesticide_payload({**base, "pre_harvest_interval_days": 7.5})
    def test_chat_history_validated_and_bounded(self):
        good = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        self.assertEqual(v.assistant_payload({"message": "m", "history": good})["history"], good)
        with self.assertRaises(ValidationError): v.assistant_payload({"message": "m", "history": [{"role": "system", "content": "x"}]})
        with self.assertRaises(ValidationError): v.assistant_payload({"message": "m", "history": [{"role": "user"}]})
        with self.assertRaises(ValidationError): v.assistant_payload({"message": "m", "history": "not a list"})
        with self.assertRaises(ValidationError): v.assistant_payload({"message": "m", "history": [{"role": "user", "content": "x"}] * 17})
        self.assertEqual(v.assistant_payload({"message": "m"})["history"], [])
    def test_review_and_rule(self):
        with self.assertRaises(ValidationError): v.review_payload({"verdict": "maybe"})
        with self.assertRaises(ValidationError): v.alert_rule_payload({"alert_type": "nope"})

class FakeHttp:
    def __init__(self, status=200, body=b"[]"): self.calls, self.status, self.body = [], status, body
    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, headers, body)); return self.status, self.body

class Rest(unittest.TestCase):
    def test_uses_user_jwt_not_service_key(self):
        h = FakeHttp(); s = Supabase("https://p.supabase.co", "ANON", h); s.select("USERJWT", "farms", {"id": "eq.1"})
        m, url, hd, _ = h.calls[0]
        self.assertEqual(hd["Authorization"], "Bearer USERJWT"); self.assertEqual(hd["apikey"], "ANON")
        self.assertIn("/rest/v1/farms?id=eq.1", url)
    def test_user_lookup(self):
        self.assertIsNone(Supabase("https://p", "k", FakeHttp(401, b"{}")).user("t"))
        self.assertEqual(Supabase("https://p", "k", FakeHttp(200, b'{"id":"u1"}')).user("t")["id"], "u1")
    def test_errors_and_bad_table(self):
        with self.assertRaises(SupabaseError): Supabase("https://p", "k", FakeHttp(403, b"no")).select("t", "farms")
        with self.assertRaises(ValueError): Supabase("https://p", "k", FakeHttp()).select("t", "farms;drop")
        with self.assertRaises(SupabaseError): Supabase("", "")
    def test_insert_dedupe_headers(self):
        h = FakeHttp(201, b"[]"); Supabase("https://p", "k", h).insert("t", "alerts", [{}], on_conflict="a,b", ignore_duplicates=True)
        self.assertIn("ignore-duplicates", h.calls[0][2]["Prefer"]); self.assertIn("on_conflict=a,b", h.calls[0][1])

class Advice(unittest.TestCase):
    def test_soil_generalized_without_test(self):
        r = soil_advice({}); self.assertEqual(r["basis"], "generalized_no_soil_test"); self.assertIn("generalized", r["notice"])
    def test_soil_ph_and_no_npk_dosage(self):
        r = soil_advice({"ph": 5.0, "organic_matter_pct": 0.5, "nitrogen": 20, "previous_crop": "Cowpea"})
        t = " ".join(r["tips"]).lower()
        self.assertIn("acidic", t); self.assertIn("organic matter is low", t); self.assertIn("does not give dosages", t)
        self.assertIn("legume", t); self.assertNotIn("kg/ha", t)
    def test_ipm_order_and_chemical_last(self):
        p = ipm_plan("disease"); stages = [s["stage"] for s in p["steps"]]
        self.assertEqual(stages[0], "prevention"); self.assertEqual(stages[-1], "chemical")
        self.assertIn("registered", json.dumps(p["steps"][-1]))
    def test_ipm_unknown_category_falls_back(self):
        self.assertTrue(ipm_plan("whatever")["steps"])
    def test_irrigation(self):
        adv = [Advisory("rain_delay_irrigation", "info", "k", {"x": 1})]
        r = irrigation_guidance(adv, 0.2); self.assertEqual(r["signals"][0]["key"], "rain_expected")
        self.assertIn("volumes", r["volume_note"]); self.assertIsNotNone(irrigation_guidance([], None)["none"])

ROWS = [{"product_name": "P", "active_ingredient": "A", "registration_number": "N", "registration_status": "registered",
         "target_crop": "tomato", "target_pest_or_disease": "Early blight", "source_name": "S", "last_verified": "2026-09-01",
         "expires_at": "2027-03-01"}]
class Pesticides(unittest.TestCase):
    def test_match(self):
        r = verified_products(ROWS, "tomato", "early blight", date(2026, 9, 24)); self.assertEqual(len(r["products"]), 1)
    def test_none_message_when_no_match_or_expired_or_wrong_crop_or_status(self):
        for rows, crop, tgt in [(ROWS, "maize", "early blight"), (ROWS, "tomato", "rust"),
                                ([{**ROWS[0], "expires_at": "2026-01-01"}], "tomato", "early blight"),
                                ([{**ROWS[0], "registration_status": "withdrawn"}], "tomato", "early blight"),
                                ([{**ROWS[0], "source_name": ""}], "tomato", "early blight"), ([], "tomato", "x")]:
            r = verified_products(rows, crop, tgt, date(2026, 9, 24)); self.assertEqual(r["products"], []); self.assertEqual(r["message"], NONE_MSG)

META = {"tomato___early_blight": {"crop": "tomato", "condition": "Early blight", "category": "disease"}}
def pred(p, meta=META): return {"model_version": "v1", "probs": p, "label_meta": meta}
class Diagnosis(unittest.TestCase):
    def test_quality_gate(self):
        self.assertTrue(quality_gate(GOOD_Q)["passed"])
        self.assertEqual(quality_gate({"brightness": 10, "sharpness": 5})["problems"], ["too_dark", "blurry"])
    def test_no_model_is_honest(self):
        d = build_diagnosis(None); self.assertEqual(d["status"], "model_unavailable"); self.assertNotIn("condition", d)
    def test_ok_diagnosis_has_no_fabricated_severity_or_indicators(self):
        d = build_diagnosis(pred({"tomato___early_blight": 0.9, "tomato___healthy": 0.05}), "tomato")
        self.assertEqual(d["status"], "ok"); self.assertEqual(d["severity"], "not_assessed"); self.assertIsNone(d["indicators"])
        self.assertFalse(d["validated_for_nigerian_field_conditions"]); self.assertEqual(d["category"], "disease")
    def test_low_confidence_hides_diagnosis(self):
        d = build_diagnosis(pred({"tomato___early_blight": 0.5, "x": 0.4}))
        self.assertEqual(d["status"], "low_confidence"); self.assertNotIn("condition", d)
    def test_crop_mismatch(self):
        self.assertEqual(build_diagnosis(pred({"tomato___early_blight": 0.95}), "maize")["status"], "crop_mismatch")
    def test_unknown_label_category_is_unknown(self):
        d = build_diagnosis(pred({"mystery": 0.95}, {})); self.assertEqual(d["category"], "unknown")

class Scan(unittest.TestCase):
    P = {"image": JPEG, "media_type": "image/jpeg", "quality": GOOD_Q, "crop_hint": "tomato"}
    def test_quality_fail_never_calls_model(self):
        calls = []; m = ModelClient("http://x", post=lambda *a: calls.append(a))
        r = run_scan({**self.P, "quality": {"brightness": 5, "sharpness": 1}}, m, [], date.today())
        self.assertEqual(r["status"], "quality_failed"); self.assertEqual(calls, [])
    def test_no_endpoint_configured(self):
        self.assertEqual(run_scan(self.P, ModelClient(""), [], date.today())["status"], "model_unavailable")
    def test_full_path_with_products(self):
        m = ModelClient("http://x", post=lambda *a: {"model_version": "v1", "probs": {"tomato___early_blight": 0.95},
                                                     "label_meta": META})
        r = run_scan(self.P, m, ROWS, date(2026, 9, 24))
        self.assertEqual(r["status"], "ok"); self.assertEqual(len(r["treatment_products"]["products"]), 1)
    def test_no_products_message(self):
        m = ModelClient("http://x", post=lambda *a: {"probs": {"tomato___early_blight": 0.95}, "label_meta": META})
        self.assertEqual(run_scan(self.P, m, [], date.today())["treatment_products"]["message"], NONE_MSG)
    def test_model_failure_is_friendly(self):
        def boom(*a): raise OSError("down")
        r = run_scan(self.P, ModelClient("http://x", post=boom), [], date.today())
        self.assertEqual(r["status"], "error"); self.assertNotIn("OSError", r["message"])
    def test_prediction_validation(self):
        for bad in ({}, {"probs": {"a": 1.5}}, {"probs": {"a": 0.9, "b": 0.9}}, {"probs": {"a": True}}, {"probs": []}):
            with self.assertRaises(ModelError): validate_prediction(bad)

class AlertsAndAssistant(unittest.TestCase):
    def test_threshold_override_and_disable(self):
        rules = [{"alert_type": "heat_stress", "threshold": 35, "enabled": True},
                 {"alert_type": "dry_spell", "threshold": 5, "enabled": True},
                 {"alert_type": "heavy_rain", "enabled": False}]
        t = thresholds_from_rules(rules); self.assertEqual(t.heat_tmax_c, 35.0); self.assertEqual(t.dry_spell_days, 5)
        self.assertEqual(t.review_status, "user_configured"); self.assertEqual(thresholds_from_rules([]).review_status, "default_unreviewed")
        adv = [Advisory("heavy_rain", "warning", "k1", {}), Advisory("heat_stress", "watch", "k2", {})]
        self.assertEqual([a.id for a in filter_advisories(adv, rules)], ["heat_stress"])
    def test_alert_rows(self):
        rows = alert_rows("f1", [Advisory("dry_spell_recent", "watch", "dry_spell_recent", {"n": 8})], "2026-09-24")
        self.assertEqual(rows[0]["alert_type"], "dry_spell"); self.assertEqual(rows[0]["alert_date"], "2026-09-24")
    def test_assistant_request_has_rules_and_no_pii(self):
        ctx = build_context({"language": "en"}, {"lga": "Dawakin Kudu", "ward": "W", "latitude": 12.3, "full_name": "Ada"},
                            [{"name": "Maize", "planting_date": "2026-06-15", "days_after_planting": 101}], [])
        self.assertNotIn("12.3", ctx); self.assertNotIn("Ada", ctx); self.assertIn("101 days", ctx)
        req = build_request("anthropic", "m", ctx, [], "why yellow?")
        self.assertIn("Never recommend a specific pesticide", req["system"]); self.assertEqual(req["messages"][-1]["content"], "why yellow?")
    def test_assistant_request_carries_history_in_order(self):
        hist = [{"role": "user", "content": "first"}, {"role": "assistant", "content": "reply"}]
        req = build_request("anthropic", "m", "ctx", hist, "second")
        self.assertEqual([m["content"] for m in req["messages"]], ["first", "reply", "second"])
    def test_groq_request_puts_system_prompt_as_first_message(self):
        req = build_request("groq", "llama-3.3-70b-versatile", "ctx", [], "hello")
        self.assertNotIn("system", req)   # OpenAI-compatible shape has no top-level "system" field
        self.assertEqual(req["messages"][0]["role"], "system")
        self.assertIn("Never recommend a specific pesticide", req["messages"][0]["content"])
        self.assertEqual(req["messages"][-1], {"role": "user", "content": "hello"})
    def test_401_is_auth_403_is_forbidden_not_the_same_thing(self):
        # A 401 means the key itself is bad; a 403 means the key is fine but lacks permission for
        # this specific model/feature. Conflating them hides real, fixable problems from admins.
        from kanofarm.services.assistant import FRIENDLY, STATUS
        self.assertNotEqual(FRIENDLY["auth"], FRIENDLY["forbidden"])
        self.assertIn("forbidden", STATUS)
    def test_default_model_per_provider(self):
        self.assertEqual(default_model("anthropic"), "claude-haiku-4-5-20251001")
        self.assertEqual(default_model("groq"), "llama-3.3-70b-versatile")
        self.assertEqual(default_model("unknown"), default_model("anthropic"))
    def test_ask_parses_and_rejects_empty_both_providers(self):
        self.assertEqual(ask("anthropic", "k", {}, post=lambda p, k, pl: {"content": [{"type": "text", "text": " hi "}]}), "hi")
        self.assertEqual(ask("groq", "k", {}, post=lambda p, k, pl: {"choices": [{"message": {"content": " hi "}}]}), "hi")
        with self.assertRaises(AssistantError) as cm: ask("anthropic", "k", {}, post=lambda p, k, pl: {"content": []})
        self.assertEqual(cm.exception.kind, "empty")
        with self.assertRaises(AssistantError) as cm: ask("groq", "k", {}, post=lambda p, k, pl: {"choices": []})
        self.assertEqual(cm.exception.kind, "empty")
    def test_ask_uses_module_level_post_at_call_time(self):
        """Regression: a default-argument binding made monkeypatching _post silently ineffective."""
        import kanofarm.services.assistant as asst
        orig = asst._post
        try:
            asst._post = lambda provider, key, payload, timeout=30: {"content": [{"type": "text", "text": "patched"}]}
            self.assertEqual(asst.ask("anthropic", "k", {}), "patched")
        finally:
            asst._post = orig
    def test_list_groq_models_parses_sorted_ids_and_sends_auth(self):
        import io
        from kanofarm.services.assistant import list_groq_models, GROQ_MODELS_URL
        seen = {}
        def fake_urlopen(req, timeout=15):
            seen["url"] = req.full_url; seen["h"] = {k.lower(): v for k, v in req.headers.items()}
            class R:
                def read(self_): return b'{"data":[{"id":"openai/gpt-oss-120b"},{"id":"llama-3.1-8b-instant"},{"nope":1}]}'
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
            return R()
        with unittest.mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(list_groq_models("gk"), ["llama-3.1-8b-instant", "openai/gpt-oss-120b"])
        self.assertEqual(seen["url"], GROQ_MODELS_URL); self.assertEqual(seen["h"]["authorization"], "Bearer gk")
        self.assertIn("kanofarmai", seen["h"]["user-agent"].lower())
    def test_list_groq_models_reports_cloudflare_block_and_errors(self):
        import urllib.error, io
        from kanofarm.services.assistant import list_groq_models
        def blocked(*a, **k): raise urllib.error.HTTPError("u", 403, "m", {}, io.BytesIO(b"error code: 1010"))
        with unittest.mock.patch("urllib.request.urlopen", side_effect=blocked):
            with self.assertRaises(AssistantError) as cm: list_groq_models("gk")
        self.assertEqual(cm.exception.kind, "forbidden"); self.assertIn("1010", str(cm.exception))
        with unittest.mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(AssistantError) as cm: list_groq_models("gk")
        self.assertEqual(cm.exception.kind, "network")
    def test_ask_requires_key(self):
        with self.assertRaises(AssistantError) as cm: ask("anthropic", "", {})
        self.assertEqual(cm.exception.kind, "auth")
    def test_post_maps_http_errors_to_kinds_for_both_providers(self):
        import urllib.error
        from kanofarm.services.assistant import _post
        def http_err(code, body=b'{"error":{"type":"x"}}'):
            def fetch(*a, **k): raise urllib.error.HTTPError("u", code, "msg", {}, __import__("io").BytesIO(body))
            return fetch
        for provider in ("anthropic", "groq"):
            for code, kind in ((401, "auth"), (403, "forbidden"), (429, "rate_limit"), (503, "overloaded"), (400, "bad_request"), (500, "server")):
                with unittest.mock.patch("urllib.request.urlopen", side_effect=http_err(code)):
                    with self.assertRaises(AssistantError) as cm: _post(provider, "k", {})
                    self.assertEqual(cm.exception.kind, kind, (provider, code))
    def test_post_surfaces_the_provider_s_actual_error_message(self):
        import urllib.error, io
        from kanofarm.services.assistant import _post
        body = b'{"error":{"type":"permissions_error","code":"model_permission_blocked_project","message":"The model is blocked at the project level. Enable it in project settings."}}'
        def fetch(*a, **k): raise urllib.error.HTTPError("u", 403, "msg", {}, io.BytesIO(body))
        with unittest.mock.patch("urllib.request.urlopen", side_effect=fetch):
            with self.assertRaises(AssistantError) as cm: _post("groq", "k", {})
        self.assertEqual(cm.exception.kind, "forbidden")
        self.assertIn("blocked at the project level", str(cm.exception))
    def test_post_shows_raw_body_when_not_valid_provider_json(self):
        """A network/proxy/firewall block often returns HTML or plain text, not the provider's JSON
        error shape -- the admin needs to see that raw text to tell the two apart."""
        import urllib.error, io
        from kanofarm.services.assistant import _post
        for body in (b"<html><body>403 Forbidden</body></html>", b"", b"Access Denied"):
            def fetch(*a, **k): raise urllib.error.HTTPError("u", 403, "msg", {}, io.BytesIO(body))
            with unittest.mock.patch("urllib.request.urlopen", side_effect=fetch):
                with self.assertRaises(AssistantError) as cm: _post("groq", "k", {})
            detail = str(cm.exception)
            self.assertTrue("raw response" in detail or "empty response body" in detail, detail)
    def test_post_maps_network_and_timeout(self):
        import urllib.error, socket
        from kanofarm.services.assistant import _post
        with unittest.mock.patch("urllib.request.urlopen", side_effect=socket.timeout()):
            with self.assertRaises(AssistantError) as cm: _post("anthropic", "k", {})
            self.assertEqual(cm.exception.kind, "timeout")
        with unittest.mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            with self.assertRaises(AssistantError) as cm: _post("groq", "k", {})
            self.assertEqual(cm.exception.kind, "network")
    def test_post_uses_the_right_url_and_auth_style(self):
        seen = {}
        def fake_urlopen(req, timeout=30):
            seen["url"] = req.full_url; seen["headers"] = {k.lower(): v for k, v in req.headers.items()}
            class R:
                def read(self_): return b'{"content":[{"type":"text","text":"ok"}]}'
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
            return R()
        from kanofarm.services.assistant import _post, GROQ_URL, ANTHROPIC_URL
        with unittest.mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            _post("groq", "gk", {})
            self.assertEqual(seen["url"], GROQ_URL); self.assertEqual(seen["headers"]["authorization"], "Bearer gk")
            self.assertIn("kanofarmai", seen["headers"]["user-agent"].lower())
            self.assertNotIn("python-urllib", seen["headers"]["user-agent"].lower())  # the exact signature Cloudflare's bot-management blocks
            _post("anthropic", "ak", {})
            self.assertEqual(seen["url"], ANTHROPIC_URL); self.assertEqual(seen["headers"]["x-api-key"], "ak")
            self.assertIn("kanofarmai", seen["headers"]["user-agent"].lower())
    def test_calendar_is_draft_and_sourced(self):
        c = calendar()
        self.assertIn("DRAFT", c["banner"])
        for e in c["entries"]: self.assertTrue(e["source_url"] and e["review_status"] == "draft_unreviewed")
        self.assertTrue(all(e["crop_slug"] == "sorghum" for e in calendar("sorghum")["entries"]))
if __name__ == "__main__": unittest.main()
