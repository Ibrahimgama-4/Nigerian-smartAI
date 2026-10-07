"""FastAPI app. Thin layer: validation + logic live in kanofarm.services (unit-tested).
Every farmer-data query runs with the farmer's own Supabase JWT, so Row Level Security enforces ownership.
NOTE: this file needs FastAPI and was NOT executed in the authoring sandbox; CI (.github/workflows/ci.yml)
runs the endpoint smoke tests in tests_api/."""
import hmac, json, logging, re
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kanofarm.core import config
from kanofarm.core.labels import WEATHER_SOURCE, RESOLUTION_NOTE
from kanofarm.core.ratelimit import RateLimiter
from kanofarm.services import validation as v
from kanofarm.services.validation import ValidationError
from kanofarm.services.supabase_rest import Supabase, SupabaseError
from kanofarm.services.weather_client import WeatherService
from kanofarm.services.rainfall import RainfallService, lagos_today
from kanofarm.services.weather_parser import WeatherDataError
from kanofarm.services.weather_rules import build_advisories, Thresholds
from kanofarm.services.alerts import thresholds_from_rules, filter_advisories, alert_rows
from kanofarm.services.crop_stage import days_after_planting
from kanofarm.services import growth_stage as gs
from kanofarm.services.soil_advice import soil_advice
from kanofarm.services.irrigation import irrigation_guidance
from kanofarm.services.calendar import calendar as crop_calendar, states as nigeria_states_data, zone_for_state
from kanofarm.services.i18n import translate
from kanofarm.services.quality import QualityConfig
from kanofarm.services.model_client import ModelClient
from kanofarm.services.scan_service import run_scan
from kanofarm.services import assistant as asst
from kanofarm.services import market as mk
from kanofarm.services import market_prices as mp
from kanofarm.services import finance as fin
from kanofarm.services import input_safety as isafe
from kanofarm.services import fertilizer_plan as fp
from kanofarm.services import planting_plan as pplan
from kanofarm.services import notify
from kanofarm.services.alerts_job import run_alert_job
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
market_limiter = RateLimiter(60, 60)       # browsing, per IP
push_channel = notify.WebPushChannel(config.VAPID_PRIVATE_KEY(), config.VAPID_SUBJECT())
wa_channel = notify.WhatsAppCloudChannel(config.WHATSAPP_TOKEN(), config.WHATSAPP_PHONE_NUMBER_ID(), config.WHATSAPP_TEMPLATE(),
                                         config.WHATSAPP_TEMPLATE_LANG())
sms_channel = notify.SmsChannel()
prefs_limiter = RateLimiter(20, 60)
planner_limiter = RateLimiter(20, 60)      # planner previews, per IP; saves per user (per instance)
report_limiter = RateLimiter(5, 3600)      # input-safety reports, per signed-in user (the database also caps 5 a day)
verify_limiter = RateLimiter(3, 3600)      # badge requests, per signed-in user (the database also enforces a 14-day wait after a rejection)
finance_limiter = RateLimiter(60, 60)     # finance + synced offline records, per signed-in user (per instance; the database also caps entries)
post_limiter = RateLimiter(5, 3600)        # new listings, per signed-in user (in-memory, per server instance)

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
                      "growth": gs.stage_info(c["crops"]["slug"], dap, gs.days_between(c["planting_date"], c.get("expected_harvest_date"))),
                      "stage_note": gs.UNAVAILABLE_NOTE})
    return fc, fetched, cached, adv, crops, th, today, rules

def adv_out(adv, lang):
    out = []
    for a in adv:
        d = a.to_dict(); d["message"] = translate(a.message_key, lang); out.append(d)
    return out

# ---------- public ----------
@app.get("/api/health")
def health(): return {"status": "ok", "code_version": "2026-09-28-market",
                       "assistant_module_version": asst.ASSISTANT_MODULE_VERSION}

@app.get("/api/config")
def public_config():
    q = QualityConfig()
    return {"supabase_url": config.SUPABASE_URL(), "supabase_anon_key": config.SUPABASE_ANON_KEY(),
            "accounts_enabled": bool(config.SUPABASE_URL() and config.SUPABASE_ANON_KEY()),
            "quality": q.public(), "model_enabled": ModelClient(config.AI_MODEL_ENDPOINT()).configured,
            "assistant_enabled": bool(config.ASSISTANT_ACTIVE_KEY()), "assistant_provider": config.ASSISTANT_PROVIDER(),
            "alert_channels": notify.available_channels(push_channel, wa_channel, sms_channel),
            "push_public_key": config.VAPID_PUBLIC_KEY() if push_channel.configured else ""}

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
    fc, fetched, cached, adv, crops, th, today, rules = farm_view(jwt, farm, lang)
    try:    # persist alerts (deduped per farm/message/day); failure must not break the dashboard
        rows = alert_rows(farm_id, adv, today.isoformat())
        if rows: sb().insert(jwt, "alerts", rows, on_conflict="farm_id,message_key,alert_date", ignore_duplicates=True)
    except SupabaseError as e:
        log.warning("alert persist failed: %s", e)
    return {"farm": {**{k: farm[k] for k in ("id", "name", "lga", "ward", "community", "size_ha", "irrigation_type")},
                     "state": farm.get("state"), "zone": zone_for_state(farm.get("state"))},
            "forecast": asdict(fc), "advisories": adv_out(adv, lang), "crops": crops,
            "irrigation": irrigation_guidance(adv, fc.current.soil_moisture_m3m3),
            "alert_rules": rules,   # already fetched above; avoids a second round trip just for the alert-settings form
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

def insert_once(jwt, table, row):
    """Insert a record the phone may send more than once (offline sync). A row with a client_id that already exists is
    skipped by the database (unique farm_id+client_id), so a replay never creates a duplicate."""
    if not row.get("client_id"):
        return one(sb().insert(jwt, table, row))
    got = sb().insert(jwt, table, row, on_conflict="farm_id,client_id", ignore_duplicates=True)
    return one(got) or {"ok": True, "duplicate": True, "client_id": row["client_id"]}

@app.post("/api/farms/{farm_id}/observations")
def obs_add(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    if not finance_limiter.allow(u["id"]): raise HTTPException(429, "Too many saves. Please wait a minute.")
    get_farm(jwt, farm_id)
    return insert_once(jwt, "farm_observations", {**v.observation_payload(payload), "farm_id": farm_id})

# ---------- farm finances ----------
def _crop_names(jwt, farm_id):
    rows = sb().select(jwt, "farm_crops", {"farm_id": f"eq.{farm_id}", "select": "id,crops(name_en)"})
    return {r["id"]: (r.get("crops") or {}).get("name_en", "Crop") for r in rows}

@app.get("/api/farms/{farm_id}/finance")
def finance_list(farm_id: str, auth=Depends(current)):
    jwt, _ = auth
    farm = get_farm(jwt, farm_id)
    rows = sb().select(jwt, "farm_finance_entries", {"farm_id": f"eq.{farm_id}", "select": "*",
                                                      "order": "entry_date.desc,created_at.desc", "limit": "1000"})
    names = _crop_names(jwt, farm_id)
    return {"entries": rows[:200], "summary": fin.summarize(rows, farm.get("size_ha"), names),
            "truncated": len(rows) >= 1000, "categories": {"income": list(v.INCOME_CATEGORIES), "expense": list(v.EXPENSE_CATEGORIES)},
            "labels": fin.CATEGORY_LABELS}

@app.post("/api/farms/{farm_id}/finance")
def finance_add(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    if not finance_limiter.allow(u["id"]): raise HTTPException(429, "Too many saves. Please wait a minute.")
    get_farm(jwt, farm_id)
    return insert_once(jwt, "farm_finance_entries", {**v.finance_payload(payload), "farm_id": farm_id})

@app.delete("/api/farms/{farm_id}/finance/{entry_id}")
def finance_delete(farm_id: str, entry_id: str, auth=Depends(current)):
    jwt, _ = auth
    get_farm(jwt, farm_id); v.uuid_str(entry_id, "entry_id")
    sb().delete(jwt, "farm_finance_entries", {"id": f"eq.{entry_id}", "farm_id": f"eq.{farm_id}"})
    return {"ok": True}

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
    return {**soil_advice(s), "reading": fp.read_soil(s)}

SOIL_FIELDS = "id,recorded_on,soil_type,ph,organic_matter_pct,nitrogen,phosphorus,potassium,npk_units_method,p_bray1_ppm,k_exch_cmolkg,lab_name,previous_crop,fertilizer_applied"

@app.get("/api/farms/{farm_id}/soil")
def soil_history(farm_id: str, auth=Depends(current)):
    get_farm(auth[0], farm_id)
    rows = sb().select(auth[0], "soil_records", {"farm_id": f"eq.{farm_id}", "select": SOIL_FIELDS, "order": "recorded_on.desc,created_at.desc", "limit": "30"})
    return {"records": rows, "reading": fp.read_soil(rows[0]) if rows else None}

@app.delete("/api/farms/{farm_id}/soil/{record_id}")
def soil_delete(farm_id: str, record_id: str, auth=Depends(current)):
    get_farm(auth[0], farm_id); v.uuid_str(record_id, "record_id")
    sb().delete(auth[0], "soil_records", {"id": f"eq.{record_id}", "farm_id": f"eq.{farm_id}"})
    return {"ok": True}

@app.get("/api/farms/{farm_id}/fertilizer-plan")
def fertilizer_plan(farm_id: str, crop: str = Query(..., pattern="^[a-z_]{2,40}$"), auth=Depends(current)):
    jwt, _ = auth; farm = get_farm(jwt, farm_id)
    zone = zone_for_state(farm.get("state"))
    rows = sb().select(jwt, "fertilizer_recommendations", {"crop_slug": f"eq.{crop}", "select": "*", "limit": "50"})
    soil_row = one(sb().select(jwt, "soil_records", {"farm_id": f"eq.{farm_id}", "select": SOIL_FIELDS, "order": "recorded_on.desc,created_at.desc", "limit": "1"}))
    plan = fp.build_plan(soil=soil_row, size_ha=farm.get("size_ha"), row=fp.pick_row(rows, zone, crop, date.today()))
    return {**plan, "has_soil_record": bool(soil_row), "zone_used": zone}


# ---------- Smart Planting Calendar ----------
def _forecast_or_none(lat, lon):
    try: return weather.get(float(lat), float(lon))[0]
    except Exception as e:                                       # noqa: BLE001 - a plan is still useful without weather
        log.warning("planner weather unavailable: %s", type(e).__name__); return None

def _plan_args(p):
    return dict(crop_slug=p["crop_slug"], planting=date.fromisoformat(p["planting_date"]), today=lagos_today(), water=p["water"], size_ha=p.get("size_ha"),
                savanna_zone=p.get("savanna_zone"), maturity_group=p.get("maturity_group"), maturity_days=p.get("maturity_days"))

def _saved_tasks(jwt, farm_crop_id):
    rows = sb().select(jwt, "farm_plan_tasks", {"farm_crop_id": f"eq.{farm_crop_id}", "select": "id,task_key,status,done_on,note"}) or []
    return {r["task_key"]: r for r in rows}

def _attach_saved(plan, saved):
    for wk in plan.get("weeks", []):
        for t in wk["tasks"]:
            r = saved.get(t["key"])
            if r: t.update(task_id=r["id"], done_on=r.get("done_on"), note=r.get("note"))
    return plan

@app.post("/api/planner/preview")
def planner_preview(request: Request, payload: dict = Body(...)):
    """Public: needs no account. Location comes from the chosen state's capital, as on the home screen."""
    ip = request.client.host if request.client else "unknown"
    if not planner_limiter.allow(ip): raise HTTPException(429, "Too many requests. Please try again shortly.")
    p = v.planner_payload(payload)
    if not p["state"]: raise HTTPException(422, "state is required")
    st = next((s for s in nigeria_states_data()["states"] if s["name"] == p["state"]), None)
    fc = _forecast_or_none(st["capital_latitude"], st["capital_longitude"]) if st else None
    return {**pplan.build_plan(**_plan_args(p), forecast=fc), "location_note": f"Weather is for {p['state']} state's capital. Save the plan to a farm to use your farm's own location."}

@app.get("/api/planner/crops")
def planner_crops():
    return {"crops_with_schedule": pplan.supported_crops(), "savanna_zones": json.loads((ROOT / "data" / "crop_activities.json").read_text(encoding="utf-8"))["savanna_zones"]}

@app.post("/api/farms/{farm_id}/planner")
def planner_save(farm_id: str, payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    farm = get_farm(jwt, farm_id)
    p = v.planner_payload(payload)
    if not planner_limiter.allow("save:" + u["id"]): raise HTTPException(429, "Too many requests. Please try again shortly.")
    crop = one(sb().select(jwt, "crops", {"slug": f"eq.{p['crop_slug']}", "select": "id"}))
    if not crop: raise HTTPException(404, "Crop not found.")
    cols = {"crop_id": crop["id"], "variety": p["variety"], "planting_date": p["planting_date"], "water_source": p["water"],
            "savanna_zone": p["savanna_zone"], "maturity_group": p["maturity_group"], "maturity_days": p["maturity_days"]}
    fcid = p.get("farm_crop_id")
    if fcid:
        if not one(sb().select(jwt, "farm_crops", {"id": f"eq.{fcid}", "farm_id": f"eq.{farm_id}", "select": "id"})): raise HTTPException(404, "Farm crop not found.")
        sb().update(jwt, "farm_crops", {"id": f"eq.{fcid}"}, cols)
    else:
        fcid = one(sb().insert(jwt, "farm_crops", {**cols, "farm_id": farm_id}))["id"]
    rules = sb().select(jwt, "alert_rules", {"farm_id": f"eq.{farm_id}", "select": "*"})
    args = _plan_args({**p, "size_ha": p.get("size_ha") or farm.get("size_ha")})
    saved = _saved_tasks(jwt, fcid)
    plan = pplan.build_plan(**args, forecast=_forecast_or_none(farm["latitude"], farm["longitude"]), th=thresholds_from_rules(rules),
                            saved={k: r["status"] for k, r in saved.items()})
    if plan["available"]:
        sb().rpc(jwt, "plan_tasks_save", {"p_farm_crop": fcid, "p_tasks": pplan.task_rows(plan)})
        _attach_saved(plan, _saved_tasks(jwt, fcid))
    return {**plan, "farm_crop_id": fcid, "saved": plan["available"]}

@app.get("/api/farms/{farm_id}/crops/{farm_crop_id}/plan")
def planner_get(farm_id: str, farm_crop_id: str, auth=Depends(current)):
    jwt, _ = auth
    farm = get_farm(jwt, farm_id); v.uuid_str(farm_crop_id, "farm_crop_id")
    fc = one(sb().select(jwt, "farm_crops", {"id": f"eq.{farm_crop_id}", "farm_id": f"eq.{farm_id}", "select": "*,crops(slug,name_en)"}))
    if not fc: raise HTTPException(404, "Farm crop not found.")
    rules = sb().select(jwt, "alert_rules", {"farm_id": f"eq.{farm_id}", "select": "*"})
    saved = _saved_tasks(jwt, farm_crop_id)
    p = {"crop_slug": fc["crops"]["slug"], "planting_date": fc["planting_date"], "water": fc.get("water_source") or "rainfed", "size_ha": farm.get("size_ha"),
         "savanna_zone": fc.get("savanna_zone"), "maturity_group": fc.get("maturity_group"), "maturity_days": fc.get("maturity_days")}
    plan = pplan.build_plan(**_plan_args(p), forecast=_forecast_or_none(farm["latitude"], farm["longitude"]), th=thresholds_from_rules(rules),
                            saved={k: r["status"] for k, r in saved.items()})
    return {**_attach_saved(plan, saved), "farm_crop_id": farm_crop_id, "crop_name": fc["crops"]["name_en"], "variety": fc.get("variety"),
            "inputs": {k: p[k] for k in ("water", "savanna_zone", "maturity_group", "maturity_days")}, "saved": bool(saved)}

@app.post("/api/farms/{farm_id}/plan-tasks/{task_id}")
def plan_task_update(farm_id: str, task_id: str, payload: dict = Body(...), auth=Depends(current)):
    jwt, _ = auth
    get_farm(jwt, farm_id); v.uuid_str(task_id, "task_id")
    p = v.plan_task_payload(payload)
    if not one(sb().select(jwt, "farm_plan_tasks", {"id": f"eq.{task_id}", "farm_id": f"eq.{farm_id}", "select": "id"})): raise HTTPException(404, "Task not found.")
    sb().rpc(jwt, "plan_task_set", {"p_task": task_id, "p_status": p["status"], "p_note": p["note"]})
    return {"ok": True, "status": p["status"]}

# ---------- input safety ----------
@app.get("/api/input-safety")
def input_safety_info():
    d = isafe.data()
    return {k: d[k] for k in ("_status", "_note", "retrieved", "restricted_ingredients", "pending", "dealer_rule", "checklist", "report_note", "official_site")}

@app.post("/api/input-safety/check")
def input_safety_check(request: Request, payload: dict = Body(...)):
    ip = request.client.host if request.client else "?"
    if not market_limiter.allow(ip): raise HTTPException(429, "Too many requests. Please try again shortly.")
    return isafe.check_product(v.input_check_payload(payload))

REPORT_FIELDS = "id,kind,product_name,problem,state,lga,bought_on,status,admin_note,created_at"

@app.post("/api/input-reports")
def input_report_submit(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    r = v.input_report_payload(payload)
    if not report_limiter.allow(u["id"]): raise HTTPException(429, "Too many reports. Please try again later.")
    if not one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "id"})):
        raise HTTPException(409, "Please complete your profile first (More, then Account).")
    saved = one(sb().insert(jwt, "input_reports", {**r, "user_id": u["id"], "status": "new"}))
    return {"ok": True, "report": {k: (saved or r).get(k) for k in REPORT_FIELDS.split(",")}}

@app.get("/api/input-reports")
def input_reports_mine(auth=Depends(current)):
    jwt, u = auth
    return sb().select(jwt, "input_reports", {"user_id": f"eq.{u['id']}", "select": REPORT_FIELDS, "order": "created_at.desc", "limit": "30"})

@app.get("/api/admin/input-reports")
def input_reports_admin(status: str = Query("new", pattern="^(new|reviewed|forwarded|closed)$"), auth=Depends(current)):
    need(auth, {"admin"})
    return sb().select(auth[0], "input_reports", {"select": REPORT_FIELDS, "status": f"eq.{status}", "order": "created_at.asc", "limit": "50"})

@app.post("/api/admin/input-reports/{report_id}/review")
def input_report_review(report_id: str, payload: dict = Body(...), auth=Depends(current)):
    need(auth, {"admin"}); jwt, admin = auth
    v.uuid_str(report_id, "report_id")
    r = v.input_report_review_payload(payload)
    if not one(sb().select(jwt, "input_reports", {"id": f"eq.{report_id}", "select": "id"})): raise HTTPException(404, "Report not found.")
    sb().update(jwt, "input_reports", {"id": f"eq.{report_id}"}, {**r, "reviewed_by": admin["id"], "reviewed_at": _now().isoformat()})
    return {"ok": True, "status": r["status"]}

@app.get("/api/admin/fertilizer-recommendations")
def fert_reco_list(auth=Depends(current)):
    need(auth, {"admin"})
    return sb().select(auth[0], "fertilizer_recommendations", {"select": "*", "order": "crop_slug.asc,zone.asc", "limit": "200"})

@app.post("/api/admin/fertilizer-recommendations")
def fert_reco_add(payload: dict = Body(...), auth=Depends(current)):
    need(auth, {"admin"}); jwt, admin = auth
    r = v.fertilizer_reco_payload(payload)
    return one(sb().insert(jwt, "fertilizer_recommendations", {**r, "entered_by": admin["id"]}))

@app.delete("/api/admin/fertilizer-recommendations/{reco_id}")
def fert_reco_delete(reco_id: str, auth=Depends(current)):
    need(auth, {"admin"}); v.uuid_str(reco_id, "id")
    sb().delete(auth[0], "fertilizer_recommendations", {"id": f"eq.{reco_id}"})
    return {"ok": True}

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
            _, _, _, advs, crops, _, _, _ = farm_view(jwt, farm)
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


# ---------- Farm Market ----------
def _now(): return datetime.now(timezone.utc)

def _view(row): return mk.listing_view(row, config.SUPABASE_URL())

@app.get("/api/market")
def market_browse(request: Request, state: Optional[str] = Query(None, max_length=40),
                  category: Optional[str] = Query(None, max_length=20), kind: Optional[str] = Query(None, max_length=10),
                  q: str = Query("", max_length=60), limit: int = Query(12, ge=1, le=30), offset: int = Query(0, ge=0, le=1000),
                  verified: bool = Query(False)):
    """Public: no login needed. Only live (active, unexpired) listings are ever returned."""
    ip = request.client.host if request.client else "unknown"
    if not market_limiter.allow(ip): raise HTTPException(429, "Too many requests. Please try again shortly.")
    params = {"select": mk.PUBLIC_COLUMNS, "status": "eq.active", "expires_at": f"gt.{_now().isoformat()}",
              "order": "created_at.desc", "limit": str(limit), "offset": str(offset)}
    if state:
        if not v.nigeria_state({"state": state}): raise ValidationError("state is not valid")
        params["state"] = f"eq.{v.nigeria_state({'state': state})}"
    if category:
        if category not in mk.CATEGORIES: raise ValidationError("category is not valid")
        params["category"] = f"eq.{category}"
    if kind:
        if kind not in mk.KINDS: raise ValidationError("kind is not valid")
        params["kind"] = f"eq.{kind}"
    if verified: params["seller_verified"] = "eq.true"
    flt = mk.search_filter(mk.clean_search_term(q))
    if flt: params["or"] = flt
    rows = sb().select(None, "market_listings", params)       # None = anonymous visitor; RLS allows live listings only
    return {"listings": [_view(r) for r in rows], "limit": limit, "offset": offset, "has_more": len(rows) == limit,
            "notice": MARKET_NOTICE, "badge_meaning": mk.BADGE_MEANING}

MARKET_NOTICE = ("KanoFarm AI only shows listings; it does not handle payments or delivery. Meet in a public place, "
                 "inspect goods before paying, and never send money in advance to someone you have not met. "
                 "Report anything suspicious.")

# ---------- Market asking-price statistics (public, aggregated; thresholds enforced in SQL and again here) ----------
@app.get("/api/market/prices")
def market_prices(request: Request, state: Optional[str] = Query(None, max_length=40), days: int = Query(60, ge=7, le=180)):
    ip = request.client.host if request.client else "unknown"
    if not market_limiter.allow(ip): raise HTTPException(429, "Too many requests. Please try again shortly.")
    q = v.market_price_query({"state": state, "days": days})
    rows = sb().rpc(None, "market_price_summary", {"p_state": q["state"], "p_days": q["days"]}) or []
    return {"prices": mp.clean_summary(rows), "state": q["state"], "days": q["days"], "note": mp.NOTE,
            "minimum": f"Only products with at least {mp.MIN_SUMMARY_LISTINGS} listings from {mp.MIN_SUMMARY_SELLERS} different sellers are shown."}

@app.get("/api/market/prices/trend")
def market_price_trend(request: Request, product: str = Query(..., max_length=60), unit: str = Query(..., max_length=10),
                       state: Optional[str] = Query(None, max_length=40), days: int = Query(90, ge=14, le=180)):
    ip = request.client.host if request.client else "unknown"
    if not market_limiter.allow(ip): raise HTTPException(429, "Too many requests. Please try again shortly.")
    q = v.market_price_query({"product": product, "unit": unit, "state": state, "days": days})
    rows = sb().rpc(None, "market_price_weekly", {"p_product": q["product"], "p_unit": q["unit"], "p_state": q["state"], "p_days": q["days"]}) or []
    points = mp.clean_weekly(rows)
    return {"product": q["product"], "unit": q["unit"], "state": q["state"], "days": q["days"], "weeks": points,
            "trend": mp.trend(points), "note": mp.NOTE,
            "minimum": f"A week is shown only with at least {mp.MIN_WEEK_LISTINGS} listings from {mp.MIN_WEEK_SELLERS} different sellers."}

@app.get("/api/market/mine")
def market_mine(auth=Depends(current)):
    jwt, u = auth
    rows = sb().select(jwt, "market_listings", {"select": mk.PUBLIC_COLUMNS, "owner_id": f"eq.{u['id']}", "order": "created_at.desc", "limit": "100"})
    return {"listings": [_view(r) for r in rows], "max_active": mk.MAX_ACTIVE_PER_USER}

@app.post("/api/market")
def market_create(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    p = v.market_payload(payload)                      # validates everything, including photos, before anything is stored
    if not post_limiter.allow(u["id"]): raise HTTPException(429, "You are posting too fast. Please wait a while before posting again.")
    profile = one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "full_name"}))
    if not profile: raise HTTPException(409, "Please complete your profile first (More, then Account).")
    active = sb().select(jwt, "market_listings", {"owner_id": f"eq.{u['id']}", "status": "eq.active", "select": "id"})
    if len(active) >= mk.MAX_ACTIVE_PER_USER:
        raise HTTPException(409, f"You already have {mk.MAX_ACTIVE_PER_USER} live listings. Mark some as sold or delete them first.")
    row = {**p["listing"], "owner_id": u["id"]}
    row["seller_name"] = row.get("seller_name") or (profile.get("full_name") or "Farmer")[:80]
    created = one(sb().insert(jwt, "market_listings", row))
    paths = []
    try:
        for i, (data, media) in enumerate(p["images"]):
            path = f"{u['id']}/{created['id']}/{i + 1}.{ {'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp'}[media] }"
            sb().upload(jwt, "market", path, data, media); paths.append(path)
        if paths: created = one(sb().update(jwt, "market_listings", {"id": f"eq.{created['id']}"}, {"image_paths": paths})) or {**created, "image_paths": paths}
    except SupabaseError as e:
        log.warning("market photo upload failed: %s", e)       # roll back: never leave a half-made listing online
        for pth in paths: sb().remove_object(jwt, "market", pth)
        try: sb().delete(jwt, "market_listings", {"id": f"eq.{created['id']}"})
        except SupabaseError: pass
        raise HTTPException(502, "We couldn't upload your photos. Please try again.")
    return _view({**row, **created})

def _own_listing(jwt, uid, listing_id):
    v.uuid_str(listing_id, "listing_id")
    row = one(sb().select(jwt, "market_listings", {"id": f"eq.{listing_id}", "owner_id": f"eq.{uid}", "select": mk.PUBLIC_COLUMNS}))
    if not row: raise HTTPException(404, "Listing not found.")
    return row

@app.patch("/api/market/{listing_id}")
def market_update(listing_id: str, payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    action = v.market_action_payload(payload)
    row = _own_listing(jwt, u["id"], listing_id)
    if row["status"] == "hidden": raise HTTPException(409, "This listing is under review and cannot be changed.")
    patch = {"sold": {"status": "sold"}, "active": {"status": "active"},
             "renew": {"status": "active", "expires_at": (_now() + timedelta(days=mk.LISTING_DAYS)).isoformat()}}[action]
    if patch["status"] == "active" and row["status"] != "active":
        n = len(sb().select(jwt, "market_listings", {"owner_id": f"eq.{u['id']}", "status": "eq.active", "select": "id"}))
        if n >= mk.MAX_ACTIVE_PER_USER: raise HTTPException(409, f"You already have {mk.MAX_ACTIVE_PER_USER} live listings.")
    updated = one(sb().update(jwt, "market_listings", {"id": f"eq.{listing_id}", "owner_id": f"eq.{u['id']}"}, patch))
    return _view({**row, **(updated or patch)})

@app.delete("/api/market/{listing_id}")
def market_delete(listing_id: str, auth=Depends(current)):
    jwt, u = auth
    row = _own_listing(jwt, u["id"], listing_id)
    sb().delete(jwt, "market_listings", {"id": f"eq.{listing_id}", "owner_id": f"eq.{u['id']}"})
    for pth in mk.storage_paths(row): sb().remove_object(jwt, "market", pth)
    return {"ok": True}

@app.post("/api/market/{listing_id}/report")
def market_report(listing_id: str, payload: dict = Body(default={}), auth=Depends(current)):
    jwt, _ = auth
    v.uuid_str(listing_id, "listing_id")
    sb().rpc(jwt, "report_listing", {"p_listing": listing_id, "p_reason": v.market_report_payload(payload)})
    return {"ok": True, "message": "Thank you. Listings reported by several people are hidden until an admin reviews them."}

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

@app.get("/api/admin/market-hidden")
def market_hidden(auth=Depends(current)):
    need(auth, {"admin"}); jwt = auth[0]
    rows = sb().select(jwt, "market_listings", {"select": mk.PUBLIC_COLUMNS, "status": "eq.hidden", "order": "updated_at.desc", "limit": "50"})
    reasons = {}
    if rows:
        ids = ",".join(r["id"] for r in rows)
        for rep in sb().select(jwt, "market_reports", {"select": "listing_id,reason", "listing_id": f"in.({ids})"}):
            reasons.setdefault(rep["listing_id"], []).append(rep.get("reason") or "(no reason given)")
    return [{**_view(r), "report_count": len(reasons.get(r["id"], [])), "report_reasons": reasons.get(r["id"], [])[:5]} for r in rows]

@app.post("/api/admin/market/{listing_id}/moderate")
def market_moderate(listing_id: str, payload: dict = Body(...), auth=Depends(current)):
    need(auth, {"admin"}); jwt = auth[0]
    v.uuid_str(listing_id, "listing_id")
    action = v.market_moderation_payload(payload)
    row = one(sb().select(jwt, "market_listings", {"id": f"eq.{listing_id}", "select": mk.PUBLIC_COLUMNS}))
    if not row: raise HTTPException(404, "Listing not found.")
    if action == "restore":
        sb().delete(jwt, "market_reports", {"listing_id": f"eq.{listing_id}"})    # a fresh set of reports is needed to hide it again
        sb().update(jwt, "market_listings", {"id": f"eq.{listing_id}"}, {"status": "active"})
    else:
        sb().delete(jwt, "market_listings", {"id": f"eq.{listing_id}"})
        for pth in mk.storage_paths(row): sb().remove_object(jwt, "market", pth)
    return {"ok": True, "action": action}


# ---------- verified seller badge ----------
VERIFY_FIELDS = "status,phone,proof_kind,proof_detail,requested_at,reviewed_at,review_note,verified_until"

@app.get("/api/seller/verification")
def seller_verification_get(auth=Depends(current)):
    jwt, u = auth
    row = one(sb().select(jwt, "seller_verifications", {"user_id": f"eq.{u['id']}", "select": VERIFY_FIELDS}))
    return {"verification": row, "proof_kinds": mk.PROOF_KINDS, "meaning": mk.BADGE_MEANING}

@app.post("/api/seller/verification")
def seller_verification_submit(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    p = v.verification_request_payload(payload)
    if not verify_limiter.allow(u["id"]): raise HTTPException(429, "Too many requests. Please try again later.")
    profile = one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "full_name"}))
    if not profile: raise HTTPException(409, "Please complete your profile first (More, then Account).")
    existing = one(sb().select(jwt, "seller_verifications", {"user_id": f"eq.{u['id']}", "select": "status"}))
    if existing and existing["status"] == "verified": raise HTTPException(409, "You are already a verified seller.")
    if existing and existing["status"] == "revoked":
        raise HTTPException(409, "This badge was removed by an administrator. Please contact support before applying again.")
    row = {**p, "seller_name": (profile.get("full_name") or "Farmer")[:80], "status": "pending", "requested_at": _now().isoformat(),
           "reviewed_by": None, "reviewed_at": None, "review_note": None, "verified_until": None}
    saved = (one(sb().update(jwt, "seller_verifications", {"user_id": f"eq.{u['id']}"}, row)) if existing
             else one(sb().insert(jwt, "seller_verifications", {**row, "user_id": u["id"]})))
    return {"verification": {k: (saved or row).get(k) for k in VERIFY_FIELDS.split(",")}}

@app.get("/api/admin/verifications")
def verifications_list(status: str = Query("pending", pattern="^(pending|verified|rejected|revoked|expired)$"), auth=Depends(current)):
    need(auth, {"admin"})
    return sb().select(auth[0], "seller_verifications", {"select": "user_id,seller_name," + VERIFY_FIELDS, "status": f"eq.{status}",
                                                         "order": "requested_at.asc", "limit": "50"})

@app.post("/api/admin/verifications/{user_id}/review")
def verification_review(user_id: str, payload: dict = Body(...), auth=Depends(current)):
    need(auth, {"admin"}); jwt, admin = auth
    v.uuid_str(user_id, "user_id")
    r = v.verification_review_payload(payload)
    if user_id == admin["id"]: raise HTTPException(409, "You cannot review your own request.")
    row = one(sb().select(jwt, "seller_verifications", {"user_id": f"eq.{user_id}", "select": "status"}))
    if not row: raise HTTPException(404, "Request not found.")
    allowed_from = {"approve": "pending", "reject": "pending", "revoke": "verified"}[r["action"]]
    if row["status"] != allowed_from: raise HTTPException(409, f"That request is {row['status']}, so it cannot be {r['action']}d.")
    now = _now()
    patch = {"reviewed_by": admin["id"], "reviewed_at": now.isoformat(), "review_note": r["note"]}
    if r["action"] == "approve":
        patch.update(status="verified", verified_until=(now + timedelta(days=30 * r["months"])).date().isoformat())
    else:
        patch.update(status="rejected" if r["action"] == "reject" else "revoked", verified_until=None)
    sb().update(jwt, "seller_verifications", {"user_id": f"eq.{user_id}"}, patch)
    return {"ok": True, "status": patch["status"]}

# ---------- alerts outside the app ----------
def _channels_view():
    return notify.available_channels(push_channel, wa_channel, sms_channel)

@app.get("/api/alerts/prefs")
def alert_prefs_get(auth=Depends(current)):
    jwt, u = auth
    p = one(sb().select(jwt, "alert_prefs", {"user_id": f"eq.{u['id']}", "select": "*"})) or {}
    return {"prefs": p, "available_channels": _channels_view(),
            "push_public_key": config.VAPID_PUBLIC_KEY() if push_channel.configured else "",
            "notes": {"push": "Free. Works on Android Chrome and installed apps; not every phone supports it.",
                      "whatsapp": "Needs a paid WhatsApp Business setup by the site owner, so it may be unavailable.",
                      "sms": "Needs a paid SMS provider, so it is usually unavailable."}}

@app.put("/api/alerts/prefs")
def alert_prefs_put(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    if not prefs_limiter.allow(u["id"]): raise HTTPException(429, "Too many changes. Please wait a minute.")
    row = v.alert_prefs_payload(payload)
    if not one(sb().select(jwt, "profiles", {"id": f"eq.{u['id']}", "select": "id"})):
        raise HTTPException(409, "Please complete your profile first (More, then Account).")
    if one(sb().select(jwt, "alert_prefs", {"user_id": f"eq.{u['id']}", "select": "user_id"})):
        return one(sb().update(jwt, "alert_prefs", {"user_id": f"eq.{u['id']}"}, row))
    return one(sb().insert(jwt, "alert_prefs", {**row, "user_id": u["id"]}))

@app.post("/api/alerts/push")
def push_subscribe(payload: dict = Body(...), auth=Depends(current)):
    jwt, u = auth
    if not push_channel.configured: raise HTTPException(503, "Push alerts are not switched on for this site yet.")
    if not prefs_limiter.allow(u["id"]): raise HTTPException(429, "Too many changes. Please wait a minute.")
    row = v.push_subscription_payload(payload)
    sb().insert(jwt, "push_subscriptions", {**row, "user_id": u["id"]}, on_conflict="endpoint", ignore_duplicates=True)
    return {"ok": True}

@app.delete("/api/alerts/push")
def push_unsubscribe(endpoint: str = Query(..., max_length=1000), auth=Depends(current)):
    jwt, u = auth
    sb().delete(jwt, "push_subscriptions", {"user_id": f"eq.{u['id']}", "endpoint": f"eq.{endpoint}"})
    return {"ok": True}

def _lagos_now(): return datetime.now(timezone.utc) + timedelta(hours=1)

@app.get("/api/cron/alerts", include_in_schema=False)
def cron_alerts(authorization: str = Header("")):
    """Called by Vercel Cron (see vercel.json). The same secret is checked here AND inside the database functions."""
    secret = config.CRON_SECRET()
    if not secret: raise HTTPException(503, "Scheduled alerts are not switched on (CRON_SECRET is not set).")
    if not hmac.compare_digest(authorization, f"Bearer {secret}"): raise HTTPException(401, "Not allowed.")
    avail = _channels_view()
    expired = _expire_verifications(secret)
    if not any(avail.values()): return {"ok": True, "note": "No delivery channel is configured.", "stats": {}, "verifications_expired": expired}
    now = _lagos_now(); today = now.date()
    db = sb()
    targets = db.rpc(None, "cron_alert_targets", {"p_secret": secret, "p_limit": config.CRON_MAX_FARMS()}) or []
    users = sorted({t["user_id"] for t in targets if t.get("push_enabled")})
    subs = {}
    if users and avail["push"]:
        for r in db.rpc(None, "cron_push_subscriptions", {"p_secret": secret, "p_users": users}) or []:
            subs.setdefault(r["user_id"], []).append(r)
    crops_by_farm = {}
    stage_farms = sorted({t["farm_id"] for t in targets if t.get("stage_reminders") is not False})
    if stage_farms:
        for r in db.rpc(None, "cron_farm_crops", {"p_secret": secret, "p_farms": stage_farms}) or []:
            crops_by_farm.setdefault(r["farm_id"], []).append(r)

    tasks_by_farm = {}
    if stage_farms:
        try:
            for r in db.rpc(None, "cron_plan_tasks", {"p_secret": secret, "p_farms": stage_farms, "p_today": today.isoformat()}) or []:
                tasks_by_farm.setdefault(r["farm_id"], []).append(r)
        except SupabaseError as e:                                  # planting-calendar reminders are optional: never stop weather alerts
            log.warning("plan reminders unavailable: %s", e)

    def stage_events(t, day):
        out = []
        by_crop = {}
        for r in tasks_by_farm.get(t["farm_id"], []):
            by_crop.setdefault((r["crop_name"], r.get("water_source") or "rainfed"), []).append(r)
        for (cname, water), rows in by_crop.items():
            out += pplan.reminder_events(rows, cname, day, t.get("forecast"), thresholds_from_rules(t.get("rules") or []), water)
        for c in crops_by_farm.get(t["farm_id"], []):
            dap = (day - date.fromisoformat(c["planting_date"])).days
            ev = gs.stage_start_event(c["slug"], c["name_en"], dap, gs.days_between(c["planting_date"], c.get("expected_harvest_date")))
            if ev: out.append(ev)
        return out

    claim_args = lambda farm, key, ch: {"p_secret": secret, "p_farm": farm, "p_key": key, "p_date": today.isoformat(), "p_channel": ch}
    stats = run_alert_job(
        targets=targets, subs_by_user=subs, get_forecast=lambda la, lo: weather.get(la, lo)[0], translate=translate,
        channels={"push": push_channel, "whatsapp": wa_channel, "sms": sms_channel}, available=avail,
        claim=lambda farm, key, ch: bool(db.rpc(None, "cron_alert_claim", claim_args(farm, key, ch))),
        release=lambda farm, key, ch: db.rpc(None, "cron_alert_release", claim_args(farm, key, ch)),
        drop_sub=lambda ep: db.rpc(None, "cron_drop_subscription", {"p_secret": secret, "p_endpoint": ep}),
        today=today, lagos_hour=now.hour, extra_events=stage_events)
    return {"ok": True, "stats": stats, "verifications_expired": expired}

def _expire_verifications(secret) -> int:
    """Badges last at most 12 months; the daily job flips lapsed ones to 'expired' (and the database removes the badge)."""
    try: return int(sb().rpc(None, "cron_expire_verifications", {"p_secret": secret}) or 0)
    except SupabaseError as e:
        log.warning("verification expiry failed: %s", e); return 0

# ---------- installable web app ----------
@app.get("/", include_in_schema=False)
def home(): return FileResponse(STATIC / "index.html", media_type="text/html")

@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest(): return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")

@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Cache-Control": "no-cache"})

app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
