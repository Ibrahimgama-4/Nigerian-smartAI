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
        return []
    def insert(self, jwt, table, rows, **kw):
        self.inserts.append((table, rows)); r = rows if isinstance(rows, dict) else (rows[0] if rows else {})
        return [{**r, "id": "33333333-3333-3333-3333-333333333333"}]
    def update(self, *a, **k): return [{}]
    def upload(self, *a, **k): pass
    def sign_url(self, *a, **k): return None

class Base(unittest.TestCase):
    def setUp(self):
        import kanofarm.services.assistant as _asst
        for obj, name in ((_asst, "_post"), (_asst, "list_groq_models"), (main, "sb"), (main, "weather"), (main, "rain"), (main, "chat_limiter"),
                          (main.config, "ANTHROPIC_API_KEY"), (main.config, "GROQ_API_KEY"), (main.config, "ASSISTANT_PROVIDER")):
            self.addCleanup(setattr, obj, name, getattr(obj, name))   # undo every monkeypatch after each test
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
        self.assertEqual(d["crops"][0]["days_after_planting"], 101); self.assertIsNone(d["crops"][0]["stage"])
        self.assertIn("flood risk", d["risks_not_computed"]); self.assertIn("heavy_rain", [a["id"] for a in d["advisories"]])
        self.assertTrue(any(t == "alerts" for t, _ in self.fake.inserts))
        self.assertEqual(d["farm"]["state"], "Kano"); self.assertEqual(d["farm"]["zone"], "north_west")

class Rainfall(Base):
    def test_rainfall_since_planting(self):
        main.rain = RainfallService("https://f", "https://a", today_fn=lambda: TODAY,
                                    fetch=lambda u: daily(TODAY - timedelta(days=110), TODAY, lambda d: 1.0))
        d = self.c.get(f"/api/farms/{FARM}/rainfall", headers=self.h).json()
        self.assertEqual(d["crops"][0]["days"], 101); self.assertTrue(d["crops"][0]["complete"])
        self.assertIn("not a rain-gauge", d["meta"]["note"])
    def test_requires_login(self):
        self.assertEqual(self.c.get(f"/api/farms/{FARM}/rainfall").status_code, 401)

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
if __name__ == "__main__": unittest.main()
