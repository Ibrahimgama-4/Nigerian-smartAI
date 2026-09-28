from datetime import date
NONE_MSG = "No verified product information is currently available."
DISCLAIMER = ("Always follow the registered product label, applicable Nigerian regulations, safety "
              "instructions, and professional agricultural guidance.")

def verified_products(rows: list, crop_slug, target_text, today: date) -> dict:
    """Only registered, unexpired, source-cited rows that match the crop AND the target."""
    t = (target_text or "").strip().lower()
    out = []
    for r in rows or []:
        try: exp = date.fromisoformat(r["expires_at"])
        except (KeyError, ValueError, TypeError): continue
        if r.get("registration_status") != "registered" or exp < today: continue
        if not r.get("source_name") or not r.get("last_verified") or not r.get("registration_number"): continue
        if (r.get("target_crop") or "").lower() not in (crop_slug or "", "any"): continue
        tp = (r.get("target_pest_or_disease") or "").lower()
        if not t or (t not in tp and tp not in t): continue
        out.append({k: r.get(k) for k in ("product_name","active_ingredient","manufacturer","registration_number",
                    "formulation","application_info","safety_info","pre_harvest_interval_days","source_name",
                    "source_url","last_verified","expires_at")})
    return {"products": out, "message": None if out else NONE_MSG, "disclaimer": DISCLAIMER}
