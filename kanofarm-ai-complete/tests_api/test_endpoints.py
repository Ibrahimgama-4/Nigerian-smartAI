"""Endpoint tests (need FastAPI; run in CI). Supabase and the weather provider are replaced with fakes."""
import base64, unittest
from fastapi.testclient import TestClient
from kanofarm.api import main
from kanofarm.services.weather_client import WeatherService
from tests.test_weather import payload
from kanofarm.services.rainfall import RainfallService
from tests.test_rainfall import daily, TODAY
from datetime import timedelta

FARM = "22222222-2222-2222-2222-222222222222"
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 50).decode()

class FakeSB:
    def __init__(self, roles=()):
        self.roles, self.inserts = roles, []
    def user(self, jwt): return {"id": "u1", "email": "a@b.c"} if jwt == "good" else None
    def select(self, jwt, table, params=None):
        params = params or {}
        if table == "farms": return [{"id": FARM, "name": "F", "state": "Kano", "lga": "L", "ward": "W", "community": None, "size_ha": 2,
                                      "irrigation_type": None, "latitude": 12.0, "longitude": 8.5}] if params.get("id") == f"eq.{FARM}" else []
        if table == "farm_crops": return [{"id": "c1", "variety": None, "planting_date": "2026-06-15", "expected_harvest_date": None,
                                           "crops": {"slug": "maize", "name_en": "Maize"}}]
        if table == "user_roles": return [{"role": r} for r in self.roles]
        if table == "profiles" and getattr(self, "has_profile", False): return [{"full_name": "Ada Farmer"}]
        if table == "market_listings": return list(getattr(self, "listings", []))
        if table == "market_reports": return list(getattr(self, "reports", []))
        if table == "seller_verifications":
            self.__dict__.setdefault("selects", []).append((table, dict(params)))
            return list(getattr(self, "verifs", []))
        return []
    def insert(self, jwt, table, rows, **kw):
        self.inserts.append((table, rows)); r = rows if isinstance(rows, dict) else (rows[0] if rows else {})
        return [{**r, "id": "33333333-3333-3333-3333-333333333333"}]
    def update(self, jwt, table, params, patch, **k):
        self.__dict__.setdefault("updates", []).append((table, params, patch)); return [{**patch}]
    def delete(self, jwt, table, params): self.__dict__.setdefault("deleted", []).append((table, params))
    def rpc(self, jwt, fn, args): self.__dict__.setdefault("rpcs", []).append((fn, args))
    def remove_object(self, *a, **k): return True
    def upload(self, *a, **k): self.__dict__.setdefault("uploads", []).append(a[2] if len(a) > 2 else None)
    def sign_url(self, *a, **k): return None

class Base(unittest.TestCase):
    def setUp(self):
        import kanofarm.services.assistant as _asst
        for obj, name in ((_asst, "_post"), (_asst, "list_groq_models"), (main, "sb"), (main, "weather"), (main, "rain"), (main, "chat_limiter"), (main, "post_limiter"), (main, "market_limiter"),
                          (main.config, "ANTHROPIC_API_KEY"), (main.config, "GROQ_API_KEY"), (main.config, "ASSISTANT_PROVIDER")):
            self.addCleanup(setattr, obj, name, getattr(obj, name))   # undo every monkeypatch after each test
        from kanofarm.core.ratelimit import RateLimiter
        main.post_limiter = RateLimiter(5, 3600); main.market_limiter = RateLimiter(60, 60)   # fresh limits for every test
        self.fake = FakeSB(); main.sb = lambda: self.fake
        main.weather = WeatherService("https://x", fetch=lambda url: payload([0] * 7, [60, 0, 0, 0, 0, 0, 0]))
        self.c = TestClient(main.app); self.h = {"Authorization": "Bearer good"}

class Public(Base):
    def test_static_and_health(self):
        d = self.c.get("/api/health").json(); self.assertEqual(d["status"], "ok")
        self.assertIn("code_version", d); self.assertIn("assistant_module_version", d)
        for p in ("/", "/manifest.webmanifest", "/sw.js", "/static/app.js", "/static/icons/icon-192.png"):
            self.assertEqual(self.c.get(p).status_code, 200, p)
    def test_config_never_leaks_secrets(self):
        d = self.c.get("/api/config").json(); self.assertFalse({"anthropic_api_key", "service_role"} & set(d))
    def test_weather(self):
        self.assertEqual(self.c.get("/api/weather?lat=51.5&lon=-0.1").status_code, 422)
        d = self.c.get("/api/weather?lat=12&lon=8.5").json()
        self.assertEqual(d["meta"]["data_type"], "MODELLED"); self.assertIn("heavy_rain", [a["id"] for a in d["advisories"]])
    def test_states_endpoint(self):
        d = self.c.get("/api/states").json()
        self.assertEqual(len(d["states"]), 37); self.assertIn("north_west", d["zones"])
    def test_calendar_zone_filter(self):
        sw = self.c.get("/api/crop-calendar?zone=south_west").json()["entries"]
        self.assertTrue(any(e["crop_slug"] == "yam" for e in sw))
        self.assertFalse(any(e["crop_slug"] == "millet" for e in sw))
    def test_public_helpers(self):
        self.assertIn("DRAFT", self.c.get("/api/crop-calendar").json()["banner"])
        self.assertTrue(self.c.get("/api/sources").json()["sources"])
        self.assertEqual(self.c.post("/api/soil/advice", json={"ph": 5}).status_code, 200)
        self.assertEqual(self.c.post("/api/soil/advice", json={"ph": 99}).status_code, 422)

class Auth(Base):
    def test_requires_login(self):
        for m, p in (("get", "/api/farms"), ("post", "/api/plant/scan"), ("get", "/api/history"), ("post", "/api/assistant"),
                     ("get", f"/api/farms/{FARM}/dashboard"), ("post", "/api/admin/pesticides")):
            self.assertEqual(getattr(self.c, m)(p, **({"json": {}} if m == "post" else {})).status_code, 401, p)
        self.assertEqual(self.c.get("/api/farms", headers={"Authorization": "Bearer bad"}).status_code, 401)
    def test_owner_comes_from_token_not_body(self):
        r = self.c.post("/api/farms", headers=self.h, json={"name": "F", "state": "Kano", "latitude": 12, "longitude": 8, "owner_id": "evil"})
        self.assertEqual(r.status_code, 200); self.assertEqual(self.fake.inserts[0][1]["owner_id"], "u1")
    def test_bad_farm_rejected(self):
        self.assertEqual(self.c.post("/api/farms", headers=self.h, json={"name": "F", "state": "Kano", "latitude": 99, "longitude": 8}).status_code, 422)
    def test_farm_requires_a_valid_nigerian_state(self):
        self.assertEqual(self.c.post("/api/farms", headers=self.h, json={"name": "F", "latitude": 12, "longitude": 8}).status_code, 422)
        self.assertEqual(self.c.post("/api/farms", headers=self.h, json={"name": "F", "state": "Neverland", "latitude": 12, "longitude": 8}).status_code, 422)
    def test_other_users_farm_is_404(self):
        self.assertEqual(self.c.get("/api/farms/44444444-4444-4444-4444-444444444444/dashboard", headers=self.h).status_code, 404)
        self.assertEqual(self.c.get("/api/farms/not-a-uuid/dashboard", headers=self.h).status_code, 422)
    def test_dashboard(self):
        d = self.c.get(f"/api/farms/{FARM}/dashboard", headers=self.h).json()
        self.assertEqual(d["crops"][0]["days_after_planting"], 101)
        g = d["crops"][0]["growth"]               # maize at day 101: FAO example, late season starts at day 95
        self.assertTrue(g["available"]); self.assertEqual(g["stage"], "late"); self.assertEqual(g["review_status"], "draft_unreviewed")
        self.assertIn("flood risk", d["risks_not_computed"]); self.assertIn("heavy_rain", [a["id"] for a in d["advisories"]])
        self.assertTrue(any(t == "alerts" for t, _ in self.fake.inserts))
        self.assertEqual(d["farm"]["state"], "Kano"); self.assertEqual(d["farm"]["zone"], "north_west")
        self.assertIn("alert_rules", d)   # so the frontend never needs a second request just to render the alert-settings form

class Rainfall(Base):
    def test_rainfall_since_planting(self):
        main.rain = RainfallService("https://f", "https://a", today_fn=lambda: TODAY,
                                    fetch=lambda u: daily(TODAY - timedelta(days=110), TODAY, lambda d: 1.0))
        d = self.c.get(f"/api/farms/{FARM}/rainfall", headers=self.h).json()
        self.assertEqual(d["crops"][0]["days"], 101); self.assertTrue(d["crops"][0]["complete"])
        self.assertIn("not a rain-gauge", d["meta"]["note"])
    def test_requires_login(self):
        self.assertEqual(self.c.get(f"/api/farms/{FARM}/rainfall").status_code, 401)

LISTING = "44444444-4444-4444-4444-444444444444"
ROW = {"id": LISTING, "kind": "for_sale", "title": "Fresh maize", "category": "produce", "state": "Kano", "seller_name": "Ada",
       "contact_phone": "+2348031234567", "image_paths": ["u1/" + LISTING + "/1.jpg"], "status": "active", "owner_id": "SECRET"}

class Market(Base):
    def body(self, **kw):
        b = {"title": "Fresh white maize", "category": "produce", "product": "maize", "state": "Kano", "contact_phone": "0803 123 4567",
             "consent_public_contact": True, "images": [JPEG], "quantity": 20, "quantity_unit": "bag", "price_ngn": 45000, "price_unit": "bag"}
        b.update(kw); return b
    def test_browsing_needs_no_login_and_never_leaks_the_owner(self):
        self.fake.listings = [ROW]
        r = self.c.get("/api/market?state=Federal%20Capital%20Territory&category=produce&q=maize)")
        self.assertEqual(r.status_code, 200)
        d = r.json(); self.assertEqual(len(d["listings"]), 1)
        self.assertNotIn("SECRET", r.text); self.assertNotIn("owner_id", r.text)
        self.assertEqual(d["listings"][0]["contact"]["call"], "tel:+2348031234567")
        self.assertIn("payments", d["notice"])
    def test_browse_rejects_bad_filters(self):
        self.assertEqual(self.c.get("/api/market?state=Neverland").status_code, 422)
        self.assertEqual(self.c.get("/api/market?category=pesticides").status_code, 422)
        self.assertEqual(self.c.get("/api/market?limit=500").status_code, 422)
    def test_posting_needs_login(self):
        self.assertEqual(self.c.post("/api/market", json=self.body()).status_code, 401)
        self.assertEqual(self.c.get("/api/market/mine").status_code, 401)
        self.assertEqual(self.c.post(f"/api/market/{LISTING}/report", json={}).status_code, 401)
    def test_post_validation_errors(self):
        self.fake.has_profile = True
        for kw in ({"consent_public_contact": False}, {"contact_phone": "123"}, {"images": []}, {"category": "pesticides"}):
            self.assertEqual(self.c.post("/api/market", headers=self.h, json=self.body(**kw)).status_code, 422, kw)
        self.assertFalse(getattr(self.fake, "uploads", []))
    def test_post_needs_a_profile_first(self):
        self.fake.has_profile = False
        self.assertEqual(self.c.post("/api/market", headers=self.h, json=self.body()).status_code, 409)
    def test_post_success_uploads_photos_into_the_owners_folder(self):
        self.fake.has_profile = True
        r = self.c.post("/api/market", headers=self.h, json=self.body(owner_id="evil", seller_name=""))
        self.assertEqual(r.status_code, 200, r.text)
        table, row = [i for i in self.fake.inserts if i[0] == "market_listings"][0]
        self.assertEqual(row["owner_id"], "u1"); self.assertEqual(row["seller_name"], "Ada Farmer")   # owner comes from the token, not the body
        self.assertEqual(row["contact_phone"], "+2348031234567"); self.assertEqual(row["state"], "Kano")
        self.assertTrue(self.fake.uploads[0].startswith("u1/"))
        self.assertNotIn("owner_id", r.json())
    def test_posting_too_fast_is_limited(self):
        self.fake.has_profile = True
        from kanofarm.core.ratelimit import RateLimiter
        main.post_limiter = RateLimiter(1, 3600)
        self.assertEqual(self.c.post("/api/market", headers=self.h, json=self.body()).status_code, 200)
        self.assertEqual(self.c.post("/api/market", headers=self.h, json=self.body()).status_code, 429)
    def test_live_listing_cap(self):
        self.fake.has_profile = True; self.fake.listings = [ROW] * 20
        self.assertEqual(self.c.post("/api/market", headers=self.h, json=self.body()).status_code, 409)
    def test_report_goes_through_the_sql_function(self):
        r = self.c.post(f"/api/market/{LISTING}/report", headers=self.h, json={"reason": "looks like a scam"})
        self.assertEqual(r.status_code, 200); self.assertEqual(self.fake.rpcs[0][0], "report_listing")
        self.assertEqual(self.fake.rpcs[0][1]["p_listing"], LISTING)
        self.assertEqual(self.c.post("/api/market/not-a-uuid/report", headers=self.h, json={}).status_code, 422)
    def test_owner_can_mark_sold_and_delete(self):
        self.fake.listings = [ROW]
        self.assertEqual(self.c.patch(f"/api/market/{LISTING}", headers=self.h, json={"action": "sold"}).status_code, 200)
        self.assertEqual(self.c.patch(f"/api/market/{LISTING}", headers=self.h, json={"action": "explode"}).status_code, 422)
        self.assertEqual(self.c.delete(f"/api/market/{LISTING}", headers=self.h).status_code, 200)
        self.assertTrue(any(t == "market_listings" for t, _ in self.fake.deleted))
    def test_hidden_listing_cannot_be_edited_by_its_owner(self):
        self.fake.listings = [{**ROW, "status": "hidden"}]
        self.assertEqual(self.c.patch(f"/api/market/{LISTING}", headers=self.h, json={"action": "active"}).status_code, 409)
    def test_someone_elses_listing_is_404(self):
        self.fake.listings = []
        self.assertEqual(self.c.patch(f"/api/market/{LISTING}", headers=self.h, json={"action": "sold"}).status_code, 404)
        self.assertEqual(self.c.delete(f"/api/market/{LISTING}", headers=self.h).status_code, 404)
    def test_admin_moderation_is_admin_only(self):
        self.assertEqual(self.c.get("/api/admin/market-hidden", headers=self.h).status_code, 403)
        self.assertEqual(self.c.post(f"/api/admin/market/{LISTING}/moderate", headers=self.h, json={"action": "restore"}).status_code, 403)
        self.fake.roles = ("admin",); self.fake.listings = [{**ROW, "status": "hidden"}]
        self.fake.reports = [{"listing_id": LISTING, "reason": "scam"}]
        d = self.c.get("/api/admin/market-hidden", headers=self.h).json()
        self.assertEqual(d[0]["report_count"], 1)
        self.assertEqual(self.c.post(f"/api/admin/market/{LISTING}/moderate", headers=self.h, json={"action": "restore"}).status_code, 200)
        self.assertEqual(self.c.post(f"/api/admin/market/{LISTING}/moderate", headers=self.h, json={"action": "bogus"}).status_code, 422)

class Scan(Base):
    def body(self, **q): return {"image_b64": JPEG, "quality": {"brightness": 120, "sharpness": 90, **q}}
    def test_bad_quality_not_stored(self):
        r = self.c.post("/api/plant/scan", headers=self.h, json=self.body(brightness=5)).json()
        self.assertEqual(r["status"], "quality_failed"); self.assertFalse(self.fake.inserts)
    def test_no_model_is_honest_and_saved(self):
        r = self.c.post("/api/plant/scan", headers=self.h, json=self.body()).json()
        self.assertEqual(r["status"], "model_unavailable"); self.assertNotIn("condition", r)
        self.assertEqual([t for t, _ in self.fake.inserts], ["plant_scans", "diagnoses"])
    def test_non_image_rejected(self):
        r = self.c.post("/api/plant/scan", headers=self.h, json={"image_b64": base64.b64encode(b"hello").decode(),
                                                                  "quality": {"brightness": 100, "sharpness": 100}})
        self.assertEqual(r.status_code, 422)

class Admin(Base):
    PEST = dict(product_name="P", active_ingredient="A", registration_number="N", registration_status="registered",
                target_crop="maize", target_pest_or_disease="t", source_name="S", last_verified="2026-09-01", expires_at="2027-03-01")
    def setUp(self):
        super().setUp()
        self._orig_anthropic = main.config.ANTHROPIC_API_KEY; self._orig_groq = main.config.GROQ_API_KEY
    def tearDown(self):
        main.config.ANTHROPIC_API_KEY = self._orig_anthropic; main.config.GROQ_API_KEY = self._orig_groq
    def test_non_admin_forbidden(self):
        self.assertEqual(self.c.post("/api/admin/pesticides", headers=self.h, json=self.PEST).status_code, 403)
        self.assertEqual(self.c.get("/api/admin/reviews", headers=self.h).status_code, 403)
        self.assertEqual(self.c.get("/api/admin/assistant-check", headers=self.h).status_code, 403)
    def test_assistant_check_masks_keys_and_skips_live_call_by_default(self):
        self.fake.roles = ("admin",)
        main.config.ANTHROPIC_API_KEY = lambda: "sk-ant-abcdefghijklmnop"
        main.config.GROQ_API_KEY = lambda: ""
        d = self.c.get("/api/admin/assistant-check", headers=self.h).json()
        self.assertEqual(d["provider"], "anthropic")
        self.assertTrue(d["anthropic_key"].startswith("sk-ant")); self.assertTrue(d["anthropic_key"].endswith(")"))
        self.assertNotIn("abcdefghijklmnop", d["anthropic_key"])  # full key never fully exposed
        self.assertEqual(d["groq_key"], "not set")
        self.assertIn("not run", d["live_check"])
    def test_assistant_check_live_reports_real_failure_reason(self):
        import kanofarm.services.assistant as asst, urllib.error, io
        self.fake.roles = ("admin",)
        main.config.ANTHROPIC_API_KEY = lambda: "sk-ant-abcdefghijklmnop"
        asst._post = lambda provider, key, payload, timeout=30: (_ for _ in ()).throw(
            urllib.error.HTTPError("u", 401, "m", {}, io.BytesIO(b'{"error":{"type":"authentication_error"}}')))
        d = self.c.get("/api/admin/assistant-check?live=true", headers=self.h).json()
        self.assertEqual(d["live_check"], "failed"); self.assertEqual(d["live_check_reason"], "auth")
    def test_assistant_check_live_lists_groq_models(self):
        import kanofarm.services.assistant as asst
        self.fake.roles = ("admin",)
        main.config.ASSISTANT_PROVIDER = lambda: "groq"; main.config.GROQ_API_KEY = lambda: "gsk_abcdefghijklmnop"
        asst.list_groq_models = lambda key, timeout=15: ["openai/gpt-oss-120b"]
        asst._post = lambda provider, key, payload, timeout=30: {"choices": [{"message": {"content": "ok"}}]}
        d = self.c.get("/api/admin/assistant-check?live=true", headers=self.h).json()
        self.assertEqual(d["available_models"], ["openai/gpt-oss-120b"]); self.assertEqual(d["live_check"], "success")
    def test_assistant_check_live_skips_when_no_key(self):
        self.fake.roles = ("admin",)
        main.config.ANTHROPIC_API_KEY = lambda: ""; main.config.GROQ_API_KEY = lambda: ""
        d = self.c.get("/api/admin/assistant-check?live=true", headers=self.h).json()
        self.assertIn("skipped", d["live_check"])
    def test_admin_allowed_but_source_required(self):
        self.fake.roles = ("admin",)
        self.assertEqual(self.c.post("/api/admin/pesticides", headers=self.h, json=self.PEST).status_code, 200)
        self.assertEqual(self.c.post("/api/admin/pesticides", headers=self.h, json={**self.PEST, "source_name": ""}).status_code, 422)
    def test_assistant_off_without_key(self):
        self.assertEqual(self.c.post("/api/assistant", headers=self.h, json={"message": "hi"}).status_code, 503)

class Assistant(Base):
    def setUp(self):
        super().setUp()
        self._orig = main.config.ANTHROPIC_API_KEY; main.config.ANTHROPIC_API_KEY = lambda: "test-key"
        from kanofarm.core.ratelimit import RateLimiter
        main.chat_limiter = RateLimiter(10, 60)   # fresh limiter so tests never interfere with each other
    def tearDown(self):
        main.config.ANTHROPIC_API_KEY = self._orig
    def ask(self, text, history=None, **kw):
        return self.c.post("/api/assistant", headers=self.h, json={"message": text, "history": history or [], **kw})
    def test_happy_path_and_disclaimer(self):
        import kanofarm.services.assistant as asst
        asst._post = lambda provider, key, payload, timeout=30: {"content": [{"type": "text", "text": "Try removing affected leaves."}]}
        r = self.ask("My tomato leaves have spots")
        self.assertEqual(r.status_code, 200); d = r.json()
        self.assertIn("removing affected leaves", d["reply"]); self.assertTrue(d["ai_generated"]); self.assertIn("does not replace", d["disclaimer"])
    def test_history_reaches_the_model_in_order(self):
        import kanofarm.services.assistant as asst
        seen = {}
        def fake(provider, key, payload, timeout=30):
            seen["messages"] = payload["messages"]; return {"content": [{"type": "text", "text": "ok"}]}
        asst._post = fake
        self.ask("second question", history=[{"role": "user", "content": "first question"}, {"role": "assistant", "content": "first answer"}])
        self.assertEqual([m["content"] for m in seen["messages"]], ["first question", "first answer", "second question"])
    def test_invalid_history_role_rejected(self):
        self.assertEqual(self.ask("hi", history=[{"role": "system", "content": "x"}]).status_code, 422)
    def test_invalid_api_key_returns_friendly_503_not_500(self):
        import kanofarm.services.assistant as asst, urllib.error, io
        def fake(provider, key, payload, timeout=30): raise urllib.error.HTTPError("u", 401, "m", {}, io.BytesIO(b'{"error":{"type":"authentication_error"}}'))
        asst._post = fake
        r = self.ask("hi")
        self.assertEqual(r.status_code, 503); self.assertNotIn("401", r.json()["detail"]); self.assertNotIn("api_key", r.json()["detail"].lower())
    def test_rate_limited_returns_429(self):
        import kanofarm.services.assistant as asst, urllib.error, io
        asst._post = lambda provider, key, payload, timeout=30: (_ for _ in ()).throw(urllib.error.HTTPError("u", 429, "m", {}, io.BytesIO(b"{}")))
        self.assertEqual(self.ask("hi").status_code, 429)
    def test_overloaded_returns_friendly_error(self):
        import kanofarm.services.assistant as asst, urllib.error, io
        asst._post = lambda provider, key, payload, timeout=30: (_ for _ in ()).throw(urllib.error.HTTPError("u", 529, "m", {}, io.BytesIO(b"{}")))
        r = self.ask("hi"); self.assertIn("busy", r.json()["detail"])
    def test_network_failure_is_friendly(self):
        import kanofarm.services.assistant as asst, urllib.error
        asst._post = lambda provider, key, payload, timeout=30: (_ for _ in ()).throw(urllib.error.URLError("down"))
        r = self.ask("hi"); self.assertEqual(r.status_code, 503); self.assertIn("connection", r.json()["detail"].lower())
    def test_groq_provider_end_to_end(self):
        import kanofarm.services.assistant as asst
        main.config.ASSISTANT_PROVIDER = lambda: "groq"; main.config.GROQ_API_KEY = lambda: "gk"
        seen = {}
        def fake(provider, key, payload, timeout=30):
            seen["provider"] = provider; seen["key"] = key
            return {"choices": [{"message": {"content": "Try crop rotation."}}]}
        asst._post = fake
        r = self.ask("pests in my cowpea")
        self.assertEqual(r.status_code, 200); self.assertIn("crop rotation", r.json()["reply"])
        self.assertEqual(seen["provider"], "groq"); self.assertEqual(seen["key"], "gk")
        main.config.ASSISTANT_PROVIDER = lambda: "anthropic"
    def test_farm_context_included_when_farm_selected(self):
        import kanofarm.services.assistant as asst
        seen = {}
        def fake(provider, key, payload, timeout=30): seen["system"] = payload["system"]; return {"content": [{"type": "text", "text": "ok"}]}
        asst._post = fake
        self.ask("why yellow leaves?", farm_id=FARM)
        self.assertIn("Maize", seen["system"]); self.assertIn("101 days", seen["system"])


class RpcSB(FakeSB):
    """FakeSB whose rpc() returns canned results per function name and records every call."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.rpc_results, self.rpc_calls = {}, []
    def rpc(self, jwt, fn, args):
        self.rpc_calls.append((fn, args)); return self.rpc_results.get(fn)


class AlertChannels(Base):
    def setUp(self):
        super().setUp()
        for obj, name in ((main, "push_channel"), (main, "wa_channel"), (main.config, "CRON_SECRET"), (main.config, "VAPID_PUBLIC_KEY"), (main, "prefs_limiter")):
            self.addCleanup(setattr, obj, name, getattr(obj, name))
        from kanofarm.core.ratelimit import RateLimiter
        from kanofarm.services import notify
        main.prefs_limiter = RateLimiter(20, 60)
        self.sent = []
        main.push_channel = notify.WebPushChannel("priv", "mailto:a@b.c", sender=lambda sub, data, key, claims: self.sent.append((sub, data)))
        main.config.VAPID_PUBLIC_KEY = lambda: "PUBKEY"
        self.fake = RpcSB(); main.sb = lambda: self.fake

    def test_prefs_get_reports_available_channels_without_secrets(self):
        d = self.c.get("/api/alerts/prefs", headers=self.h).json()
        self.assertTrue(d["available_channels"]["push"]); self.assertFalse(d["available_channels"]["whatsapp"])
        self.assertEqual(d["push_public_key"], "PUBKEY"); self.assertNotIn("priv", str(d))

    def test_config_exposes_channels_and_public_key_only(self):
        d = self.c.get("/api/config").json()
        self.assertTrue(d["alert_channels"]["push"]); self.assertEqual(d["push_public_key"], "PUBKEY")
        self.assertNotIn("priv", str(d))

    def test_prefs_need_login(self):
        self.assertEqual(self.c.get("/api/alerts/prefs").status_code, 401)
        self.assertEqual(self.c.put("/api/alerts/prefs", json={}).status_code, 401)

    def test_prefs_put_requires_profile(self):
        self.assertEqual(self.c.put("/api/alerts/prefs", headers=self.h, json={"push_enabled": True}).status_code, 409)

    def test_prefs_put_saves_with_profile(self):
        self.fake.has_profile = True
        r = self.c.put("/api/alerts/prefs", headers=self.h, json={"push_enabled": True, "min_level": "warning"})
        self.assertEqual(r.status_code, 200)
        table, row = self.fake.inserts[-1]
        self.assertEqual(table, "alert_prefs"); self.assertEqual(row["user_id"], "u1"); self.assertTrue(row["push_enabled"])

    def test_prefs_put_rejects_phone_channel_without_consent(self):
        self.fake.has_profile = True
        r = self.c.put("/api/alerts/prefs", headers=self.h, json={"whatsapp_enabled": True, "phone": "0803 123 4567"})
        self.assertEqual(r.status_code, 422)

    def test_push_subscribe_validates_and_stores(self):
        body = {"endpoint": "https://fcm.googleapis.com/fcm/send/abcdef123456", "keys": {"p256dh": "x" * 30, "auth": "y" * 12}}
        self.assertEqual(self.c.post("/api/alerts/push", headers=self.h, json={"endpoint": "http://x", "keys": {}}).status_code, 422)
        self.assertEqual(self.c.post("/api/alerts/push", headers=self.h, json=body).status_code, 200)
        self.assertEqual(self.fake.inserts[-1][0], "push_subscriptions")

    def test_push_subscribe_503_when_push_not_configured(self):
        from kanofarm.services import notify
        main.push_channel = notify.WebPushChannel("", "")
        body = {"endpoint": "https://fcm.googleapis.com/fcm/send/abcdef123456", "keys": {"p256dh": "x" * 30, "auth": "y" * 12}}
        self.assertEqual(self.c.post("/api/alerts/push", headers=self.h, json=body).status_code, 503)

    def test_cron_is_closed_without_secret_or_with_wrong_one(self):
        main.config.CRON_SECRET = lambda: ""
        self.assertEqual(self.c.get("/api/cron/alerts").status_code, 503)
        main.config.CRON_SECRET = lambda: "s3cret"
        self.assertEqual(self.c.get("/api/cron/alerts").status_code, 401)
        self.assertEqual(self.c.get("/api/cron/alerts", headers={"Authorization": "Bearer nope"}).status_code, 401)
        self.assertEqual(self.fake.rpc_calls, [])             # nothing touched the database

    def test_cron_sends_once_and_passes_the_secret_to_the_database(self):
        main.config.CRON_SECRET = lambda: "s3cret"
        self.fake.rpc_results = {
            "cron_alert_targets": [{"user_id": "u1", "farm_id": FARM, "farm_name": "F", "latitude": 12.0, "longitude": 8.5, "language": "en",
                                    "push_enabled": True, "whatsapp_enabled": False, "sms_enabled": False, "phone": None,
                                    "min_level": "warning", "quiet_start": None, "quiet_end": None, "rules": []}],
            "cron_push_subscriptions": [{"user_id": "u1", "endpoint": "https://fcm.googleapis.com/x", "p256dh": "p", "auth": "a"}],
            "cron_alert_claim": True}
        r = self.c.get("/api/cron/alerts", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(r.status_code, 200); self.assertEqual(r.json()["stats"]["sent"], 1)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(all(a.get("p_secret") == "s3cret" for fn, a in self.fake.rpc_calls))
        self.assertNotIn("s3cret", r.text)

    def test_cron_does_nothing_when_no_channel_is_configured(self):
        from kanofarm.services import notify
        main.config.CRON_SECRET = lambda: "s3cret"; main.push_channel = notify.WebPushChannel("", "")
        r = self.c.get("/api/cron/alerts", headers={"Authorization": "Bearer s3cret"})
        self.assertEqual(r.status_code, 200); self.assertEqual(self.fake.rpc_calls, [])

class DupAwareSB(FakeSB):
    """Behaves like Postgres with unique (farm_id, client_id): a repeated client_id is ignored and returns no row."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k); self.seen, self.finance_rows = set(), []
    def insert(self, jwt, table, rows, **kw):
        cid = rows.get("client_id") if isinstance(rows, dict) else None
        if kw.get("ignore_duplicates") and cid:
            if (table, cid) in self.seen: return []
            self.seen.add((table, cid))
        return super().insert(jwt, table, rows, **kw)
    def select(self, jwt, table, params=None):
        if table == "farm_finance_entries": return list(self.finance_rows)
        return super().select(jwt, table, params)


class FinanceAndOffline(Base):
    CID = "44444444-4444-4444-4444-444444444444"
    ENTRY = {"kind": "expense", "category": "seed", "amount_ngn": "12000", "entry_date": "2026-09-01"}

    def setUp(self):
        super().setUp()
        self.addCleanup(setattr, main, "finance_limiter", main.finance_limiter)
        from kanofarm.core.ratelimit import RateLimiter
        main.finance_limiter = RateLimiter(60, 60)
        self.fake = DupAwareSB(); main.sb = lambda: self.fake

    def test_login_required(self):
        self.assertEqual(self.c.get(f"/api/farms/{FARM}/finance").status_code, 401)
        self.assertEqual(self.c.post(f"/api/farms/{FARM}/finance", json=self.ENTRY).status_code, 401)

    def test_add_entry_is_stored_against_the_farm(self):
        r = self.c.post(f"/api/farms/{FARM}/finance", headers=self.h, json=self.ENTRY)
        self.assertEqual(r.status_code, 200)
        table, row = self.fake.inserts[-1]
        self.assertEqual(table, "farm_finance_entries"); self.assertEqual(row["farm_id"], FARM); self.assertEqual(row["amount_ngn"], 12000.0)

    def test_bad_entries_are_refused(self):
        for bad in ({**self.ENTRY, "amount_ngn": "-1"}, {**self.ENTRY, "category": "sale"}, {**self.ENTRY, "entry_date": "2030-01-01"}):
            self.assertEqual(self.c.post(f"/api/farms/{FARM}/finance", headers=self.h, json=bad).status_code, 422)
        self.assertEqual(self.fake.inserts, [])

    def test_someone_elses_farm_is_not_found(self):
        other = "99999999-9999-9999-9999-999999999999"
        self.assertEqual(self.c.post(f"/api/farms/{other}/finance", headers=self.h, json=self.ENTRY).status_code, 404)
        self.assertEqual(self.c.get(f"/api/farms/{other}/finance", headers=self.h).status_code, 404)

    def test_replayed_entry_is_not_duplicated_and_is_not_an_error(self):
        body = {**self.ENTRY, "client_id": self.CID}
        first = self.c.post(f"/api/farms/{FARM}/finance", headers=self.h, json=body)
        again = self.c.post(f"/api/farms/{FARM}/finance", headers=self.h, json=body)
        self.assertEqual((first.status_code, again.status_code), (200, 200))
        self.assertTrue(again.json().get("duplicate")); self.assertNotIn("duplicate", first.json())

    def test_replayed_observation_is_not_duplicated(self):
        body = {"kind": "note", "observed_on": "2026-09-01", "text": "weeding done", "client_id": self.CID}
        self.assertEqual(self.c.post(f"/api/farms/{FARM}/observations", headers=self.h, json=body).status_code, 200)
        r = self.c.post(f"/api/farms/{FARM}/observations", headers=self.h, json=body)
        self.assertEqual(r.status_code, 200); self.assertTrue(r.json().get("duplicate"))

    def test_observation_without_client_id_still_works(self):
        r = self.c.post(f"/api/farms/{FARM}/observations", headers=self.h, json={"kind": "note", "observed_on": "2026-09-01"})
        self.assertEqual(r.status_code, 200); self.assertNotIn("duplicate", r.json())

    def test_list_gives_summary_with_profit_per_hectare(self):
        self.fake.finance_rows = [
            {"id": "e1", "kind": "income", "category": "sale", "amount_ngn": 150000, "entry_date": "2026-09-01", "season": "S", "farm_crop_id": "c1"},
            {"id": "e2", "kind": "expense", "category": "seed", "amount_ngn": 30000, "entry_date": "2026-06-01", "season": "S", "farm_crop_id": "c1"}]
        d = self.c.get(f"/api/farms/{FARM}/finance", headers=self.h).json()
        self.assertEqual(d["summary"]["total"]["profit_ngn"], 120000.0)
        self.assertEqual(d["summary"]["total"]["profit_per_ha_ngn"], 60000.0)          # FakeSB farm has size_ha = 2
        self.assertEqual(d["summary"]["by_crop"][0]["crop"], "Maize"); self.assertFalse(d["truncated"])

    def test_delete_checks_ids(self):
        self.assertEqual(self.c.delete(f"/api/farms/{FARM}/finance/not-a-uuid", headers=self.h).status_code, 422)
        r = self.c.delete(f"/api/farms/{FARM}/finance/{self.CID}", headers=self.h)
        self.assertEqual(r.status_code, 200); self.assertEqual(self.fake.deleted[-1][0], "farm_finance_entries")


if __name__ == "__main__": unittest.main()


class SellerVerification(Base):
    UID = "55555555-5555-4555-8555-555555555555"
    BODY = {"phone": "0803 123 4567", "proof_kind": "cooperative", "proof_detail": "Member of Kura Rice Farmers Cooperative", "consent": True}
    def setUp(self):
        super().setUp(); self.fake.has_profile = True
    def post(self, body=None, **kw): return self.c.post("/api/seller/verification", json={**self.BODY, **(body or {}), **kw}, headers=self.h)
    def review(self, body, uid=None): return self.c.post(f"/api/admin/verifications/{uid or self.UID}/review", json=body, headers=self.h)

    def test_login_required(self):
        self.assertEqual(self.c.get("/api/seller/verification").status_code, 401)
        self.assertEqual(self.c.post("/api/seller/verification", json=self.BODY).status_code, 401)
    def test_no_consent_is_refused(self):
        self.assertEqual(self.post(consent=False).status_code, 422)
        self.assertFalse(self.fake.inserts)
    def test_success_inserts_pending_for_the_token_user_and_ignores_body_overrides(self):
        r = self.post(status="verified", user_id="someone-else", verified_until="2099-01-01", reviewed_by="x")
        self.assertEqual(r.status_code, 200, r.text)
        table, row = self.fake.inserts[-1]
        self.assertEqual(table, "seller_verifications")
        self.assertEqual((row["user_id"], row["status"], row["phone"]), ("u1", "pending", "+2348031234567"))
        self.assertIsNone(row["verified_until"]); self.assertIsNone(row["reviewed_by"])
        self.assertEqual(row["seller_name"], "Ada Farmer")
    def test_already_verified_or_revoked_cannot_reapply(self):
        for st in ("verified", "revoked"):
            self.fake.verifs = [{"status": st}]
            self.assertEqual(self.post().status_code, 409, st)
    def test_reapply_after_rejection_updates_instead_of_inserting(self):
        self.fake.verifs = [{"status": "rejected"}]
        self.assertEqual(self.post().status_code, 200)
        self.assertFalse([i for i in self.fake.inserts if i[0] == "seller_verifications"])
        self.assertEqual(self.fake.updates[-1][2]["status"], "pending")
    def test_admin_only(self):
        self.assertEqual(self.c.get("/api/admin/verifications", headers=self.h).status_code, 403)
        self.assertEqual(self.review({"action": "reject", "note": "no"}).status_code, 403)
    def test_list_filters_by_status(self):
        self.fake.roles = ("admin",)
        self.assertEqual(self.c.get("/api/admin/verifications?status=verified", headers=self.h).status_code, 200)
        self.assertEqual(self.fake.selects[-1][1]["status"], "eq.verified")
        self.assertEqual(self.c.get("/api/admin/verifications?status=bogus", headers=self.h).status_code, 422)
    def test_approve_needs_both_confirmations(self):
        self.fake.roles = ("admin",); self.fake.verifs = [{"status": "pending"}]
        self.assertEqual(self.review({"action": "approve"}).status_code, 422)
        self.assertFalse(getattr(self.fake, "updates", []))
    def test_approve_sets_review_fields_and_expiry(self):
        self.fake.roles = ("admin",); self.fake.verifs = [{"status": "pending"}]
        r = self.review({"action": "approve", "confirmed_phone": True, "checked_proof": True, "months": 3})
        self.assertEqual(r.status_code, 200, r.text)
        patch = self.fake.updates[-1][2]
        self.assertEqual((patch["status"], patch["reviewed_by"]), ("verified", "u1"))
        self.assertTrue(patch["verified_until"])
    def test_review_target_must_be_a_uuid(self):
        self.fake.roles = ("admin",); self.fake.verifs = [{"status": "pending"}]
        self.assertEqual(self.review({"action": "reject", "note": "x"}, uid="u1").status_code, 422)  # "u1" is not a uuid
    def test_non_pending_cannot_be_approved_and_only_verified_can_be_revoked(self):
        self.fake.roles = ("admin",)
        self.fake.verifs = [{"status": "rejected"}]
        self.assertEqual(self.review({"action": "approve", "confirmed_phone": True, "checked_proof": True}).status_code, 409)
        self.assertEqual(self.review({"action": "revoke", "note": "x"}).status_code, 409)
        self.fake.verifs = [{"status": "verified"}]
        self.assertEqual(self.review({"action": "revoke", "note": "fake claim"}).json()["status"], "revoked")
    def test_missing_request_is_404(self):
        self.fake.roles = ("admin",); self.fake.verifs = []
        self.assertEqual(self.review({"action": "reject", "note": "x"}).status_code, 404)
