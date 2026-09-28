"""Input validation in plain Python (no framework) so it is unit-tested. Raises ValidationError."""
import base64, binascii, json, re
from datetime import date
from pathlib import Path
from . import market as mk

LANGUAGES = ("en", "ha", "yo", "ig", "pcm")
_STATE_NAMES = {s["name"] for s in json.loads(
    (Path(__file__).resolve().parent.parent.parent / "data" / "nigeria_states.json").read_text(encoding="utf-8"))["states"]}
_STATE_LOOKUP = {n.lower(): n for n in _STATE_NAMES}

def nigeria_state(d, key="state", required=False):
    v = d.get(key)
    if v is None or v == "":
        if required: raise ValidationError(f"{key} is required")
        return None
    if not isinstance(v, str) or v.strip().lower() not in _STATE_LOOKUP:
        raise ValidationError(f"{key} must be one of Nigeria's 36 states or 'Federal Capital Territory'")
    return _STATE_LOOKUP[v.strip().lower()]

class ValidationError(ValueError):
    pass

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
MAX_IMAGE_BYTES = 2_000_000

def uuid_str(v, name="id"):
    if not isinstance(v, str) or not _UUID.match(v):
        raise ValidationError(f"{name} is not valid")
    return v

def _text(d, key, max_len, required=False):
    v = d.get(key)
    if v is None or v == "":
        if required: raise ValidationError(f"{key} is required")
        return None
    if not isinstance(v, str): raise ValidationError(f"{key} must be text")
    v = v.strip()
    if len(v) > max_len: raise ValidationError(f"{key} is too long")
    if required and not v: raise ValidationError(f"{key} is required")
    return v or None

def _num(d, key, lo, hi, required=False):
    v = d.get(key)
    if v is None or v == "":
        if required: raise ValidationError(f"{key} is required")
        return None
    try: f = float(v)
    except (TypeError, ValueError): raise ValidationError(f"{key} must be a number")
    if f != f or not (lo <= f <= hi): raise ValidationError(f"{key} must be between {lo} and {hi}")
    return f

def _int(d, key, lo, hi, required=False):
    f = _num(d, key, lo, hi, required)
    if f is None: return None
    if f != int(f): raise ValidationError(f"{key} must be a whole number")
    return int(f)

def _date(d, key, required=False):
    v = d.get(key)
    if v is None or v == "":
        if required: raise ValidationError(f"{key} is required")
        return None
    try: return date.fromisoformat(str(v)).isoformat()
    except ValueError: raise ValidationError(f"{key} must be a date (YYYY-MM-DD)")

def farm_payload(d: dict) -> dict:
    return {"name": _text(d, "name", 120, True), "state": nigeria_state(d, required=True), "lga": _text(d, "lga", 80), "ward": _text(d, "ward", 80),
            "community": _text(d, "community", 80),
            "latitude": _num(d, "latitude", 4, 14, True), "longitude": _num(d, "longitude", 2.5, 15, True),
            "size_ha": _num(d, "size_ha", 0.001, 100000), "irrigation_type": _text(d, "irrigation_type", 60),
            "notes": _text(d, "notes", 1000)}

def farm_crop_payload(d: dict) -> dict:
    planting = _date(d, "planting_date", True)
    harvest = _date(d, "expected_harvest_date")
    if date.fromisoformat(planting) > date.today(): raise ValidationError("planting_date cannot be in the future")
    if harvest and harvest < planting: raise ValidationError("expected_harvest_date is before planting_date")
    return {"crop_id": uuid_str(d.get("crop_id"), "crop_id"), "variety": _text(d, "variety", 80),
            "planting_date": planting, "expected_harvest_date": harvest}

OBS_KINDS = {"planting","germination","observation","pest","disease","treatment","fertilizer",
             "irrigation","harvest","weather_event","note"}

def observation_payload(d: dict) -> dict:
    kind = d.get("kind")
    if kind not in OBS_KINDS: raise ValidationError("kind is not valid")
    out = {"kind": kind, "observed_on": _date(d, "observed_on", True), "text": _text(d, "text", 2000)}
    if d.get("farm_crop_id"): out["farm_crop_id"] = uuid_str(d["farm_crop_id"], "farm_crop_id")
    return out

def soil_payload(d: dict) -> dict:
    return {"soil_type": _text(d, "soil_type", 60), "ph": _num(d, "ph", 3, 10),
            "organic_matter_pct": _num(d, "organic_matter_pct", 0, 100),
            "nitrogen": _num(d, "nitrogen", 0, 1e6), "phosphorus": _num(d, "phosphorus", 0, 1e6),
            "potassium": _num(d, "potassium", 0, 1e6), "npk_units_method": _text(d, "npk_units_method", 120),
            "previous_crop": _text(d, "previous_crop", 60), "fertilizer_applied": _text(d, "fertilizer_applied", 200)}

def sniff_image(b: bytes):
    if b[:3] == b"\xff\xd8\xff": return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n": return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP": return "image/webp"
    return None

def scan_payload(d: dict) -> dict:
    raw = d.get("image_b64")
    if not isinstance(raw, str) or not raw: raise ValidationError("image is required")
    if len(raw) > MAX_IMAGE_BYTES * 4 // 3 + 16: raise ValidationError("image is too large")
    try: img = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError): raise ValidationError("image is not valid")
    if len(img) > MAX_IMAGE_BYTES: raise ValidationError("image is too large")
    mt = sniff_image(img)
    if not mt: raise ValidationError("image must be JPEG, PNG or WebP")   # content sniffed, client type ignored
    q = d.get("quality")
    if not isinstance(q, dict): raise ValidationError("quality metrics are required")
    quality = {"brightness": _num(q, "brightness", 0, 255, True), "sharpness": _num(q, "sharpness", 0, 1e7, True)}
    return {"image": img, "media_type": mt, "quality": quality,
            "crop_hint": _text(d, "crop_hint", 40),
            "farm_id": uuid_str(d["farm_id"], "farm_id") if d.get("farm_id") else None,
            "contribute_image": d.get("contribute_image") is True}

def alert_rule_payload(d: dict) -> dict:
    t = d.get("alert_type")
    if t not in {"heavy_rain","dry_spell","heat_stress","high_water_demand","rain_delay_irrigation"}:
        raise ValidationError("alert_type is not valid")
    return {"alert_type": t, "enabled": d.get("enabled") is not False, "threshold": _num(d, "threshold", 0, 1000)}

def feedback_payload(d: dict) -> dict:
    return {"topic": _text(d, "topic", 40) or "general", "message": _text(d, "message", 2000, True)}

def review_payload(d: dict) -> dict:
    v = d.get("verdict")
    if v not in {"correct","incorrect","alternative","needs_more_info"}: raise ValidationError("verdict is not valid")
    return {"verdict": v, "alternative_label": _text(d, "alternative_label", 120), "notes": _text(d, "notes", 1000)}

def pesticide_payload(d: dict) -> dict:
    out = {"product_name": _text(d, "product_name", 120, True), "active_ingredient": _text(d, "active_ingredient", 200, True),
           "manufacturer": _text(d, "manufacturer", 120), "registration_number": _text(d, "registration_number", 60, True),
           "registration_status": d.get("registration_status"),
           "formulation": _text(d, "formulation", 60), "target_crop": _text(d, "target_crop", 40, True),
           "target_pest_or_disease": _text(d, "target_pest_or_disease", 120, True),
           "application_info": _text(d, "application_info", 1000), "safety_info": _text(d, "safety_info", 1000),
           "pre_harvest_interval_days": _int(d, "pre_harvest_interval_days", 0, 365),
           "source_name": _text(d, "source_name", 200, True), "source_url": _text(d, "source_url", 500),
           "last_verified": _date(d, "last_verified", True), "expires_at": _date(d, "expires_at", True)}
    if out["registration_status"] not in {"registered","suspended","withdrawn","unknown"}:
        raise ValidationError("registration_status is not valid")
    if out["expires_at"] <= out["last_verified"]: raise ValidationError("expires_at must be after last_verified")
    return out

MAX_CHAT_HISTORY = 16
MAX_CHAT_MESSAGE_CHARS = 1000

def chat_history(d) -> list:
    if d is None: return []
    if not isinstance(d, list): raise ValidationError("history must be a list")
    if len(d) > MAX_CHAT_HISTORY: raise ValidationError(f"history is limited to the last {MAX_CHAT_HISTORY} messages")
    out = []
    for m in d:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            raise ValidationError("each history item needs a role of 'user' or 'assistant'")
        text = _text(m, "content", MAX_CHAT_MESSAGE_CHARS, True)
        out.append({"role": m["role"], "content": text})
    return out

def assistant_payload(d: dict) -> dict:
    return {"message": _text(d, "message", MAX_CHAT_MESSAGE_CHARS, True),
            "history": chat_history(d.get("history")),
            "farm_id": uuid_str(d["farm_id"], "farm_id") if d.get("farm_id") else None,
            "lang": d.get("lang") if d.get("lang") in LANGUAGES else "en"}


# ---------------------------------------------------------------- Farm Market
def _decode_photo(raw, i):
    if not isinstance(raw, str) or not raw: raise ValidationError(f"photo {i} is not valid")
    if len(raw) > mk.MAX_IMAGE_BYTES * 4 // 3 + 16: raise ValidationError(f"photo {i} is too large; try a smaller photo")
    try: img = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError): raise ValidationError(f"photo {i} is not valid")
    if len(img) > mk.MAX_IMAGE_BYTES: raise ValidationError(f"photo {i} is too large; try a smaller photo")
    mt = sniff_image(img)
    if not mt: raise ValidationError(f"photo {i} must be a JPEG, PNG or WebP image")
    return img, mt

def _clean_text(d, key, max_len, required=False, min_len=0):
    v = _text(d, key, max_len, required)
    if v is None: return None
    v = mk.strip_control(v).strip()
    if len(v) < min_len: raise ValidationError(f"{key} is too short")
    return v or None

def market_payload(d: dict) -> dict:
    kind = d.get("kind", "for_sale")
    if kind not in mk.KINDS: raise ValidationError("kind is not valid")
    category = d.get("category")
    if category not in mk.CATEGORIES: raise ValidationError("category is not valid")
    if d.get("consent_public_contact") is not True:
        raise ValidationError("You must agree that your phone number will be visible to everyone")
    phone = mk.normalize_phone(d.get("contact_phone"))
    if not phone: raise ValidationError("Enter a Nigerian mobile number, for example 0803 123 4567")
    qty = _num(d, "quantity", 0, 1e9)
    qty_unit = d.get("quantity_unit") or None
    if qty is not None and qty_unit not in mk.UNITS: raise ValidationError("quantity_unit is not valid")
    price = _num(d, "price_ngn", 0, 1e10)
    price_unit = d.get("price_unit") or None
    if price is not None and price_unit not in mk.UNITS: raise ValidationError("price_unit is not valid")
    photos = d.get("images") or []
    if not isinstance(photos, list) or len(photos) > mk.MAX_IMAGES:
        raise ValidationError(f"You can add at most {mk.MAX_IMAGES} photos")
    images = [_decode_photo(p, i + 1) for i, p in enumerate(photos)]
    if kind == "for_sale" and not images: raise ValidationError("Please add at least one photo of the product")
    listing = {
        "kind": kind, "title": _clean_text(d, "title", 100, True, 3), "category": category,
        "product": _clean_text(d, "product", 60), "description": _clean_text(d, "description", 1000),
        "quantity": qty, "quantity_unit": qty_unit if qty is not None else None,
        "price_ngn": price, "price_unit": price_unit if price is not None else None,
        "negotiable": d.get("negotiable") is True,
        "state": nigeria_state(d, required=True), "lga": _clean_text(d, "lga", 80),
        "contact_phone": phone, "seller_name": _clean_text(d, "seller_name", 80),
    }
    return {"listing": listing, "images": images}

def market_action_payload(d: dict) -> str:
    a = d.get("action")
    if a not in ("sold", "active", "renew"): raise ValidationError("action is not valid")
    return a

def market_report_payload(d: dict) -> str:
    return _clean_text(d, "reason", 300) or ""

def market_moderation_payload(d: dict) -> str:
    a = d.get("action")
    if a not in ("restore", "remove"): raise ValidationError("action is not valid")
    return a
