"""Soil-record reading and the fertiliser plan. Pure logic, unit-tested.

Nothing here invents a rate. A rate appears only when an administrator has entered a sourced, unexpired row in
fertilizer_recommendations for the farm's zone (or 'all') and the crop. The only numbers built in are the two published
'apply none' soil-test levels below and the nutrient content of common straight fertilisers (arithmetic, not advice)."""
from datetime import date
from typing import Optional

# Published levels above which the source says to apply no P / no K (Bray-1 P in mg/kg = ppm; exchangeable K in cmol/kg).
# Source: Tarfa et al. (2017), "Optimizing Fertilizer Use within the Context of Integrated Soil Fertility Management in Nigeria"
# (OFRA, Table 12.4). Single source, unreviewed; shown as a guide, never as a command.
P_NO_APPLY_PPM = 15.0
K_NO_APPLY_CMOLKG = 0.17
LEVEL_SOURCE = {"name": "Tarfa et al. 2017, Optimizing Fertilizer Use within the Context of ISFM in Nigeria (OFRA), Table 12.4",
                "url": "https://agronomy.unl.edu/sites/unl.edu.ianr.agronomy-horticulture/files/media/file/OFRA%20Chapter%2012_Nigeria_HR.pdf"}

# Typical nutrient content of common straight fertilisers (fraction). The label on YOUR bag decides; SSP in particular varies.
PRODUCTS = {"n": ("Urea", 0.46, "N"), "p2o5": ("Single superphosphate (SSP)", 0.18, "P2O5"), "k2o": ("Muriate of potash (MOP)", 0.60, "K2O")}
BAG_KG = 50.0

DISCLAIMER = ("This is a planning guide from a published source, not a prescription. Fertiliser need depends on your soil, rain, variety and "
              "the crop before. Confirm with your extension officer, and follow the label of the bag you buy.")
NO_ROW = ("No sourced fertiliser recommendation has been entered for this crop and zone yet, so this app gives no rate. "
          "Ask your extension officer, or ask the app administrator to add a sourced recommendation.")


def read_soil(rec: dict) -> dict:
    """What the saved soil test says about P and K, using only the two published levels."""
    out = {"phosphorus": None, "potassium": None}
    p, k = rec.get("p_bray1_ppm"), rec.get("k_exch_cmolkg")
    if p is not None:
        out["phosphorus"] = {"value": p, "unit": "mg/kg (Bray-1)", "enough": p > P_NO_APPLY_PPM,
                             "text": (f"Phosphorus {p:g} mg/kg is above {P_NO_APPLY_PPM:g}: the source says no phosphorus fertiliser is needed."
                                      if p > P_NO_APPLY_PPM else f"Phosphorus {p:g} mg/kg is at or below {P_NO_APPLY_PPM:g}: phosphorus fertiliser may help.")}
    if k is not None:
        out["potassium"] = {"value": k, "unit": "cmol/kg (exchangeable)", "enough": k > K_NO_APPLY_CMOLKG,
                            "text": (f"Potassium {k:g} cmol/kg is above {K_NO_APPLY_CMOLKG:g}: the source says no potassium fertiliser is needed."
                                     if k > K_NO_APPLY_CMOLKG else f"Potassium {k:g} cmol/kg is at or below {K_NO_APPLY_CMOLKG:g}: potassium fertiliser may help.")}
    out["source"] = LEVEL_SOURCE
    return out


def pick_row(rows: list, zone: Optional[str], crop_slug: str, today: date) -> Optional[dict]:
    """Best usable row: right crop, right zone (a zone-specific row beats 'all'), unexpired, with a source and a rate."""
    best = None
    for r in rows or []:
        try:
            if date.fromisoformat(str(r["expires_at"])[:10]) < today:
                continue
        except (KeyError, ValueError, TypeError):
            continue
        if r.get("crop_slug") != crop_slug or r.get("zone") not in (zone, "all") or not r.get("source_name"):
            continue
        if all(r.get(k) is None for k in ("n_kg_ha", "p2o5_kg_ha", "k2o_kg_ha")):
            continue
        if best is None or (r["zone"] == zone and best["zone"] != zone):
            best = r
    return best


def build_plan(*, soil: Optional[dict], size_ha, row: Optional[dict]) -> dict:
    """soil: latest saved record or None. size_ha: the farm size. row: from pick_row."""
    if not row:
        return {"available": False, "message": NO_ROW, "disclaimer": DISCLAIMER}
    ha = float(size_ha) if size_ha else None
    reading = read_soil(soil or {})
    items = []
    for key, label in (("n", "n_kg_ha"), ("p2o5", "p2o5_kg_ha"), ("k2o", "k2o_kg_ha")):
        rate = row.get(label)
        if rate is None:
            continue
        rate = float(rate)
        skip = None
        if key == "p2o5" and reading["phosphorus"] and reading["phosphorus"]["enough"]:
            skip = reading["phosphorus"]["text"]
        if key == "k2o" and reading["potassium"] and reading["potassium"]["enough"]:
            skip = reading["potassium"]["text"]
        name, share, unit = PRODUCTS[key]
        item = {"nutrient": unit, "rate_kg_ha": rate, "skipped": bool(skip), "skip_reason": skip}
        if not skip:
            per_ha = rate / share
            item.update(product=name, product_share_pct=round(share * 100), product_kg_per_ha=round(per_ha, 1))
            if ha:
                total = per_ha * ha
                item.update(farm_total_kg=round(total, 1), farm_total_bags=round(total / BAG_KG, 2))
        items.append(item)
    return {"available": True, "farm_size_ha": ha, "items": items, "soil": reading,
            "zone": row.get("zone"), "crop": row.get("crop_slug"), "notes": row.get("notes"),
            "source": {"name": row.get("source_name"), "url": row.get("source_url"), "year": row.get("source_year"),
                       "last_verified": row.get("last_verified"), "expires_at": row.get("expires_at")},
            "size_note": None if ha else "Farm size is not set, so totals are not shown.",
            "disclaimer": DISCLAIMER}
