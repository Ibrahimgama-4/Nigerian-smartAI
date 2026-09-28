"""FastAPI app. Thin layer: validation + logic live in kanofarm.services (unit-tested).
Every farmer-data query runs with the farmer's own Supabase JWT, so Row Level Security enforces ownership.
NOTE: this file needs FastAPI and was NOT executed in the authoring sandbox; CI (.github/workflows/ci.yml)
runs the endpoint smoke tests in tests_api/."""
import json, logging, re
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kanofarm.core import config
from kanofarm.core.labels import WEATHER_SOURCE, RESOLUTION_NOTE
from kanofarm.core.ratelimit import RateLimiter
from kanofarm.services import validation as v
from kanofarm.services.validation import ValidationError
from kanofarm.services.supabase_rest import Supabase, SupabaseError
from kanofarm.services.weather_client import WeatherService
from kanofarm.services.rainfall import RainfallService
from kanofarm.services.weather_parser import WeatherDataError
from kanofarm.services.weather_rules import build_advisories, Thresholds
from kanofarm.services.alerts import thresholds_from_rules, filter_advisories, alert_rows
from kanofarm.services.crop_stage import days_after_planting, stage_for
from kanofarm.services.soil_advice import soil_advice
from kanofarm.services.irrigation import irrigation_guidance
from kanofarm.services.calendar import calendar as crop_calendar, states as nigeria_states_data, zone_for_state
from kanofarm.services.i18n import translate
from kanofarm.services.quality import QualityConfig
from kanofarm.services.model_client import ModelClient
from kanofarm.services.scan_service import run_scan
from kanofarm.services import assistant as asst
from kanofarm.services.assistant import AssistantError

log = logging.getLogger("kanofarm")
ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
app = FastAPI(title="KanoFarm AI API", version="0.2.0")

weather = WeatherService(config.env("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast"),
                         config.env("WEATHER_API_KEY"))
rain = RainfallService(config.env("WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast"),
                       config.env("ARCHIVE_API_URL", "https://archive-api.open-meteo.com/v1/archive"), config.env("WEATHER_API_KEY"))
ip_limiter = RateLimiter(30, 60)
scan_limiter = RateLimiter(10, 60)
chat_limiter = RateLimiter(10, 60)

# ---------- errors: technical details are logged, never shown to farmers ----------
@app.exception_handler(ValidationError)
async def _val(_, e): return JSONResponse({"detail": str(e)}, 422)

@app.exception_handler(ValueError)
async def _valerr(_, e): return JSONResponse({"detail": str(e)}, 422)

@app.exception_handler(WeatherDataError)
async def _wx(_, e):
    log.warning("weather error: %s", e)
    return JSONResponse({"detail": "We couldn't retrieve the weather data right now. Please try again later."}, 503)

@app.exception_handler(AssistantError)
async def _assistant_err(_, e):
    log.warning("assistant error kind=%s detail=%s", e.kind, str(e))
    return JSONResponse({"detail": asst.FRIENDLY.get(e.kind, asst.FRIENDLY["server"])}, asst.STATUS.get(e.kind, 502))

@app.exception_handler(SupabaseError)
async def _sb(_, e):
    log.warning("supabase error: %s", e)
    if e.status in (401, 403): return JSONResponse({"detail": "Please sign in again."}, 401)
    if e.status == 409: return JSONResponse({"detail": "That record already exists."}, 409)
    if e.status == 503: return JSONResponse({"detail": "Accounts are not set up yet."}, 503)
    return JSONResponse({"detail": "We couldn't complete that. Please try again."}, 502)

# ---------- helpers ----------
def sb() -> Supabase:
    return Supabase(config.SUPABASE_URL(), config.SUPABASE_ANON_KEY())

def current(request: Request):
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "): raise HTTPException(401, "Please sign in.")
    jwt = h[7:].strip()
    u = sb().user(jwt)
    if not u: raise HTTPException(401, "Please sign in again.")
    return jwt, u

def one(rows):
    return rows[0] if rows else None

def get_farm(jwt, farm_id):
    v.uuid_str(farm_id, "farm_id")
    f = one(sb().select(jwt, "farms", {"id": f"eq.{farm_id}", "select": "*"}))
    if not f: raise HTTPException(404, "Farm not found.")
    return f

def roles(jwt, uid):
    return {r["role"] for r in sb().select(jwt, "user_roles", {"user_id": f"eq.{uid}", "select": "role"})}

def farm_view(jwt, farm, lang="en"):
    """Weather + advisories + crop status for one farm."""
    rules = sb().select(jwt, "alert_rules", {"farm_id": f"eq.{farm['id']}", "select": "*"})
    th = thresholds_from_rules(rules)
    fc, fetched, cached = weather.get(float(farm["latitude"]), float(farm["longitude"]))
    adv = filter_advisories(build_advisories(fc, th), rules)
    today = date.fromisoformat(fc.current.time[:10])
    crops = []
    for c in sb().select(jwt, "farm_crops", {"farm_id": f"eq.{farm['id']}", "select": "*,crops(slug,name_en)",
                                              "order": "planting_date.desc"}):
        dap = days_after_planting(date.fromisoformat(c["planting_date"]), today)
        crops.append({"id": c["id"], "slug": c["crops"]["slug"], "name": c["crops"]["name_en"], "variety": c.get("variety"),
                      "planting_date": c["planting_date"], "expected_harvest_date": c.get("expected_harvest_date"),
                      "days_after_planting": dap,
                      "stage": stage_for(dap, None),
                      "stage_note": "Growth stage is not shown: no verified stage calendar is available for this crop."})
    return fc, fetched, cached, adv, crops, th, today

def adv_out(adv, lang):
    out = []
    for a in adv:
        d = a.to_dict(); d["message"] = translate(a.message_key, lang); out.append(d)
    return out

# ---------- public ----------
@app.get("/api/health")
def health(): return {"status": "ok", "code_version": "2026-09-28-final",
                       "assistant_module_version": asst.ASSISTANT_MODULE_VERSION}

@app.get("/api/config")
def public_config():
    q = QualityConfig()
    return {"supabase_url": config.SUPABASE_URL(), "supabase_anon_key": config.SUPABASE_ANON_KEY(),
            "accounts_enabled": bool(config.SUPABASE_URL() and config.SUPABASE_ANON_KEY()),
            "quality": q.public(), "model_enabled": ModelClient(config.AI_MODEL_ENDPOINT()).configured,
            "assistant_enabled": bool(config.ASSISTANT_ACTIVE_KEY()), "assistant_provider": config.ASSISTANT_PROVIDER()}

@app.get("/api/weather")
def get_weather(request: Request, lat: float = Query(...), lon: float = Query(...),
                lang: str = Query("en", pattern="^(en|ha|yo|ig|pcm)$")):
    ip = request.client.host if request.client else "unknown"
    if not ip_limiter.allow(ip): raise HTTPException(429, "Too many requests. Please try again shortly.")
    fc, fetched, cached = weather.get(lat, lon)
    return {"forecast": asdict(fc), "advisories": adv_out(build_advisories(fc, Thresholds()), lang),
            "meta": {**WEATHER_SOURCE, "resolution_note": RESOLUTION_NOTE,
                     "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(),
                     "from_cache": cached, "thresholds_review_status": Thresholds().review_status}}

@app.get("/api/crop-calendar")
def get_calendar(crop: Optional[str] = Query(None, pattern="^[a-z_]{1,30}$"),
                 zone: Optional[str] = Query(None, pattern="^[a-z_]{1,20}$")):
    return crop_calendar(crop, zone)

@app.get("/api/sources")
def sources():
    return json.loads((ROOT / "data" / "data_sources.json").read_text(encoding="utf-8"))

@app.get("/api/states")
def states_endpoint():
    return nigeria_states_data()

@app.post("/api/soil/advice")
def soil(payload: dict = Body(...)):
    return soil_advice(v.soil_payload(payload))

# ---------- profile ----------
@app.get("/api/profile")
def profile_get(auth=Depends(current)):
    jwt, u = auth
    p = one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "*"}))
    return {"profile": p, "email": u.get("email"), "roles": sorted(roles(jwt, u["id"]))}

@app.put("/api/profile")
def profile_put(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    name = v._text(payload, "full_name", 120, True)
    row = {"full_name": name, "language": payload.get("language") if payload.get("language") in v.LANGUAGES else "en",
           "state": v.nigeria_state(payload, required=True),
           "phone": v._text(payload, "phone", 30), "lga": v._text(payload, "lga", 80), "ward": v._text(payload, "ward", 80),
           "community": v._text(payload, "community", 80), "farm_type": v._text(payload, "farm_type", 60),
           "experience_years": v._int(payload, "experience_years", 0, 80)}
    if one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "id"})):
        return one(sb().update(jwt, "profiles", {"id": f"eq.{u['id']}"}, row))
    return one(sb().insert(jwt, "profiles", {**row, "id": u["id"]}))

# ---------- crops / farms ----------
@app.get("/api/crops")
def crops_list(auth=Depends(current)):
    return sb().select(auth[0], "crops", {"select": "id,slug,name_en", "order": "name_en"})

@app.get("/api/farms")
def farms_list(auth=Depends(current)):
    return sb().select(auth[0], "farms", {"select": "*", "order": "created_at.desc"})

@app.post("/api/farms")
def farms_create(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    return one(sb().insert(jwt, "farms", {**v.farm_payload(payload), "owner_id": u["id"]}))

@app.delete("/api/farms/{farm_id}")
def farms_delete(farm_id: str, auth=Depends(current)):
    get_farm(auth[0], farm_id); sb().delete(auth[0], "farms", {"id": f"eq.{farm_id}"}); return {"ok": True}

@app.post("/api/farms/{farm_id}/crops")
def farm_crop_add(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    get_farm(auth[0], farm_id)
    return one(sb().insert(auth[0], "farm_crops", {**v.farm_crop_payload(payload), "farm_id": farm_id}))

@app.get("/api/farms/{farm_id}/dashboard")
def farm_dashboard(farm_id: str, lang: str = Query("en", pattern="^(en|ha|yo|ig|pcm)$"), auth=Depends(current)):
    jwt, _ = auth
    farm = get_farm(jwt, farm_id)
    fc, fetched, cached, adv, crops, th, today = farm_view(jwt, farm, lang)
    try:    # persist alerts (deduped per farm/message/day); failure must not break the dashboard
        rows = alert_rows(farm_id, adv, today.isoformat())
        if rows: sb().insert(jwt, "alerts", rows, on_conflict="farm_id,message_key,alert_date", ignore_duplicates=True)
    except SupabaseError as e:
        log.warning("alert persist failed: %s", e)
    return {"farm": {**{k: farm[k] for k in ("id", "name", "lga", "ward", "community", "size_ha", "irrigation_type")},
                     "state": farm.get("state"), "zone": zone_for_state(farm.get("state"))},
            "forecast": asdict(fc), "advisories": adv_out(adv, lang), "crops": crops,
            "irrigation": irrigation_guidance(adv, fc.current.soil_moisture_m3m3),
            "risks_not_computed": ["flood risk", "pest risk", "disease risk", "crop stress indicators"],
            "meta": {**WEATHER_SOURCE, "resolution_note": RESOLUTION_NOTE,
                     "fetched_at": datetime.fromtimestamp(fetched, timezone.utc).isoformat(), "from_cache": cached,
                     "thresholds_review_status": th.review_status}}

RAIN_NOTE = ("Rainfall is reanalysis/model data for a grid cell of roughly 10-25 km, not a rain-gauge measurement at your farm. "
             "Older days come from the ERA5-family historical dataset; the most recent days come from recent model output. "
             "A total is only called complete when every day has a value.")

@app.get("/api/farms/{farm_id}/rainfall")
def farm_rainfall(farm_id: str, auth=Depends(current)):
    jwt, _ = auth
    farm = get_farm(jwt, farm_id)
    out = []
    for c in sb().select(jwt, "farm_crops", {"farm_id": f"eq.{farm_id}", "select": "id,planting_date,crops(name_en)",
                                              "order": "planting_date.desc"}):
        r = rain.since(float(farm["latitude"]), float(farm["longitude"]), date.fromisoformat(c["planting_date"]))
        out.append({"farm_crop_id": c["id"], "crop": c["crops"]["name_en"], **r})
    return {"crops": out, "meta": {"source": "Open-Meteo Historical Weather API + forecast API past days", "url": "https://open-meteo.com",
            "attribution": WEATHER_SOURCE["attribution"], "data_type": "HISTORICAL/MODELLED", "note": RAIN_NOTE}}

@app.get("/api/farms/{farm_id}/observations")
def obs_list(farm_id: str, auth=Depends(current)):
    get_farm(auth[0], farm_id)
    return sb().select(auth[0], "farm_observations", {"farm_id": f"eq.{farm_id}", "select": "*",
                                                       "order": "observed_on.desc,created_at.desc", "limit": "200"})

@app.post("/api/farms/{farm_id}/observations")
def obs_add(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    get_farm(auth[0], farm_id)
    return one(sb().insert(auth[0], "farm_observations", {**v.observation_payload(payload), "farm_id": farm_id}))

@app.get("/api/farms/{farm_id}/alert-rules")
def rules_get(farm_id: str, auth=Depends(current)):
    get_farm(auth[0], farm_id)
    return sb().select(auth[0], "alert_rules", {"farm_id": f"eq.{farm_id}", "select": "*"})

@app.put("/api/farms/{farm_id}/alert-rules")
def rules_put(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    jwt, _ = auth; get_farm(jwt, farm_id)
    r = v.alert_rule_payload(payload)
    existing = one(sb().select(jwt, "alert_rules", {"farm_id": f"eq.{farm_id}", "alert_type": f"eq.{r['alert_type']}", "select": "id"}))
    if existing: return one(sb().update(jwt, "alert_rules", {"id": f"eq.{existing['id']}"}, r))
    return one(sb().insert(jwt, "alert_rules", {**r, "farm_id": farm_id}))

@app.get("/api/farms/{farm_id}/alerts")
def alerts_list(farm_id: str, auth=Depends(current)):
    get_farm(auth[0], farm_id)
    return sb().select(auth[0], "alerts", {"farm_id": f"eq.{farm_id}", "select": "*", "order": "created_at.desc", "limit": "50"})

@app.post("/api/farms/{farm_id}/soil")
def soil_save(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    get_farm(auth[0], farm_id)
    s = v.soil_payload(payload)
    sb().insert(auth[0], "soil_records", {**s, "farm_id": farm_id})
    return soil_advice(s)

# ---------- Plant Doctor ----------
@app.post("/api/plant/scan")
def plant_scan(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    if not scan_limiter.allow(u["id"]): raise HTTPException(429, "Too many scans. Please wait a minute.")
    p = v.scan_payload(payload)
    if p["farm_id"]: get_farm(jwt, p["farm_id"])
    rows = sb().select(jwt, "pesticides", {"registration_status": "eq.registered", "select": "*", "limit": "1000"})
    model = ModelClient(config.AI_MODEL_ENDPOINT(), config.AI_MODEL_TOKEN())
    res = run_scan(p, model, rows, date.today())
    raw = res.pop("_raw", None)
    if res["status"] in ("quality_failed", "error"): return res
    scan = one(sb().insert(jwt, "plant_scans", {"user_id": u["id"], "farm_id": p["farm_id"], "crop_hint": p["crop_hint"],
                                                 "quality": p["quality"], "contributed_for_training": p["contribute_image"]}))
    if p["contribute_image"]:
        try:
            path = f"{u['id']}/{scan['id']}.{'jpg' if p['media_type']=='image/jpeg' else p['media_type'].split('/')[1]}"
            sb().upload(jwt, "scans", path, p["image"], p["media_type"])
            sb().update(jwt, "plant_scans", {"id": f"eq.{scan['id']}"}, {"image_path": path})
        except SupabaseError as e:
            log.warning("image upload failed: %s", e)
    d = one(sb().insert(jwt, "diagnoses", {"scan_id": scan["id"], "status": res["status"],
            "model_version": res.get("model_version"), "top_label": res.get("label"), "confidence": res.get("confidence"),
            "level": res.get("confidence_level"), "probs": raw["probs"] if raw else None}))
    res.update({"scan_id": scan["id"], "diagnosis_id": d["id"]})
    return res

@app.get("/api/history")
def history(q: str = Query("", max_length=60), auth=Depends(current)):
    jwt, _ = auth
    term = re.sub(r"[^\w\s-]", "", q).strip()
    params = {"select": "id,farm_id,kind,observed_on,text", "order": "observed_on.desc", "limit": "100"}
    if term: params["text"] = f"ilike.*{term}*"
    obs = sb().select(jwt, "farm_observations", params)
    scans = sb().select(jwt, "plant_scans", {"select": "id,created_at,crop_hint,diagnoses(id,status,top_label,confidence,level)",
                                            "order": "created_at.desc", "limit": "100"})
    if term:
        t = term.lower()
        scans = [s for s in scans if t in (s.get("crop_hint") or "").lower()
                 or any(t in (d.get("top_label") or "").lower() for d in s.get("diagnoses", []))]
    return {"observations": obs, "scans": scans}

# ---------- assistant / feedback ----------
@app.post("/api/assistant")
def assistant(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    provider = config.ASSISTANT_PROVIDER()
    key = config.ASSISTANT_ACTIVE_KEY()
    if not key: raise HTTPException(503, "The assistant is not switched on for this site yet.")
    if not chat_limiter.allow(u["id"]): raise HTTPException(429, "Too many questions. Please wait a minute before asking again.")
    a = v.assistant_payload(payload)
    profile = one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "language,lga,ward"})) or {}
    farm, crops, adv = None, [], []
    if a["farm_id"]:
        farm = get_farm(jwt, a["farm_id"])
        try:
            _, _, _, advs, crops, _, _ = farm_view(jwt, farm)
            adv = [x.to_dict() for x in advs]
        except (WeatherDataError, ValueError):
            pass   # farm context is a bonus; the assistant still answers without it
    ctx = asst.build_context({**profile, "language": a["lang"]}, farm, crops, adv)
    model = config.ASSISTANT_MODEL_OVERRIDE() or asst.default_model(provider)
    req = asst.build_request(provider, model, ctx, a["history"], a["message"])
    reply = asst.ask(provider, key, req)   # raises AssistantError, handled by the exception handler above
    return {"reply": reply, "ai_generated": True,
            "disclaimer": "AI-generated advice. It does not replace qualified agricultural extension professionals."}

@app.post("/api/feedback")
def feedback(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    sb().insert(jwt, "feedback", {**v.feedback_payload(payload), "user_id": u["id"]})
    return {"ok": True}

# ---------- expert / admin ----------
def need(auth, allowed):
    jwt, u = auth
    if not (roles(jwt, u["id"]) & set(allowed)): raise HTTPException(403, "Not allowed.")

@app.get("/api/admin/reviews")
def reviews_list(auth=Depends(current)):
    need(auth, {"admin", "expert"}); jwt = auth[0]
    ds = sb().select(jwt, "diagnoses", {"select": "*,plant_scans(crop_hint,image_path,contributed_for_training),expert_reviews(verdict)",
                                        "or": "(status.eq.low_confidence,level.eq.moderate,level.eq.low)",
                                        "order": "created_at.desc", "limit": "50"})
    for d in ds:
        path = (d.get("plant_scans") or {}).get("image_path")
        d["image_url"] = sb().sign_url(jwt, "scans", path) if path else None
    return ds

@app.post("/api/admin/reviews/{diagnosis_id}")
def review_add(diagnosis_id: str, payload: dict = Body(...), auth=Depends(current)):
    need(auth, {"admin", "expert"}); v.uuid_str(diagnosis_id, "diagnosis_id")
    return one(sb().insert(auth[0], "expert_reviews", {**v.review_payload(payload), "diagnosis_id": diagnosis_id, "reviewer_id": auth[1]["id"]}))

@app.post("/api/admin/pesticides")
def pesticide_add(payload: dict = Body(...), auth=Depends(current)):
    need(auth, {"admin"})
    return one(sb().insert(auth[0], "pesticides", {**v.pesticide_payload(payload), "entered_by": auth[1]["id"]}))

@app.get("/api/admin/feedback")
def feedback_list(auth=Depends(current)):
    need(auth, {"admin"})
    return sb().select(auth[0], "feedback", {"select": "*", "order": "created_at.desc", "limit": "100"})

def _mask_key(k: str) -> str:
    if not k: return "not set"
    if len(k) <= 10: return "set (too short to show safely)"
    return f"{k[:6]}...{k[-4:]} ({len(k)} characters)"

@app.get("/api/admin/assistant-check")
def assistant_check(live: bool = Query(False), auth=Depends(current)):
    """Diagnoses assistant setup without needing to read server logs. With live=true it sends one
    real, tiny test message through the configured provider and reports exactly what came back --
    this uses one real request against your quota/bill, so it is opt-in, not automatic."""
    need(auth, {"admin"})
    provider = config.ASSISTANT_PROVIDER()
    key = config.ASSISTANT_ACTIVE_KEY()
    out = {
        "provider": provider,
        "provider_recognised": provider in ("anthropic", "groq"),
        "anthropic_key": _mask_key(config.ANTHROPIC_API_KEY()),
        "groq_key": _mask_key(config.GROQ_API_KEY()),
        "active_key_for_current_provider": _mask_key(key),
        "model": config.ASSISTANT_MODEL_OVERRIDE() or asst.default_model(provider),
    }
    if not live:
        out["live_check"] = "not run (add ?live=true to actually test the connection)"
        return out
    if not key:
        out["live_check"] = "skipped: no key configured for the active provider"
        return out
    if provider == "groq":
        try:
            out["available_models"] = asst.list_groq_models(key)
        except AssistantError as e:
            out["available_models_error"] = str(e)
    try:
        req = asst.build_request(provider, out["model"], "FARM CONTEXT\nnone", [], "Reply with the single word: ok")
        reply = asst.ask(provider, key, req)
        out["live_check"] = "success"; out["live_reply"] = reply
    except AssistantError as e:
        out["live_check"] = "failed"; out["live_check_reason"] = e.kind; out["live_check_detail"] = str(e)
    return out

# ---------- installable web app ----------
@app.get("/", include_in_schema=False)
def home(): return FileResponse(STATIC / "index.html", media_type="text/html")

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest(): return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
