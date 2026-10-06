"""Farm finances: plain arithmetic on what the farmer typed. Nothing here estimates, predicts or fills gaps.
 * Money is added with Decimal so totals never drift (0.1 + 0.2 style errors).
 * Profit = money in - money out. Profit per hectare uses the FARM's size and is shown only when a size is known,
   because the app has no per-crop area; per-crop figures are totals only.
 * Entries not tied to a crop are reported as "whole farm", never spread over crops."""
from decimal import Decimal, ROUND_HALF_UP

CATEGORY_LABELS = {
    "sale": "Sale of produce", "other_income": "Other income",
    "seed": "Seed / seedlings", "fertilizer": "Fertilizer", "pesticide": "Pesticide / chemicals", "labour": "Labour",
    "equipment": "Tools & equipment", "irrigation": "Irrigation / water", "transport": "Transport",
    "land_rent": "Land rent", "processing": "Processing / storage", "other_expense": "Other cost",
}
NOTE = ("These figures only add up what you entered. They are not accounts, tax advice or a forecast, and entries that are "
        "missing are missing from the totals.")
NO_SEASON = "No season"
WHOLE_FARM = "Whole farm"


def _d(x) -> Decimal:
    return Decimal(str(x))


def _money(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _bucket(entries) -> dict:
    inc = sum((_d(e["amount_ngn"]) for e in entries if e["kind"] == "income"), Decimal(0))
    exp = sum((_d(e["amount_ngn"]) for e in entries if e["kind"] == "expense"), Decimal(0))
    return {"income_ngn": _money(inc), "expense_ngn": _money(exp), "profit_ngn": _money(inc - exp), "entries": len(entries)}


def _size(size_ha):
    """A usable farm size (Decimal > 0) or None. Bad or missing sizes never raise."""
    try:
        size = _d(size_ha) if size_ha is not None else None
    except Exception:                                               # noqa: BLE001
        return None
    return size if size is not None and size.is_finite() and size > 0 else None


def _per_ha(profit: float, size_ha):
    size = _size(size_ha)
    return _money(_d(profit) / size) if size else None


def summarize(entries: list, size_ha=None, crop_names: dict = None) -> dict:
    """entries: rows with kind, category, amount_ngn, season, farm_crop_id. crop_names: {farm_crop_id: 'Maize'}."""
    crop_names = crop_names or {}
    total = _bucket(entries)
    total["profit_per_ha_ngn"] = _per_ha(total["profit_ngn"], size_ha)

    seasons = {}
    for e in entries:
        seasons.setdefault(e.get("season") or NO_SEASON, []).append(e)
    by_season = []
    for name, es in seasons.items():
        b = _bucket(es); b["season"] = name; b["profit_per_ha_ngn"] = _per_ha(b["profit_ngn"], size_ha)
        b["last_entry"] = max(x["entry_date"] for x in es)
        by_season.append(b)
    by_season.sort(key=lambda b: b["last_entry"], reverse=True)

    crops = {}
    for e in entries:
        crops.setdefault(e.get("farm_crop_id") or None, []).append(e)
    by_crop = []
    for cid, es in crops.items():
        b = _bucket(es); b["crop"] = crop_names.get(cid, "Crop (removed)") if cid else WHOLE_FARM
        by_crop.append(b)
    by_crop.sort(key=lambda b: (b["crop"] == WHOLE_FARM, b["crop"]))

    costs = {}
    for e in entries:
        if e["kind"] == "expense":
            costs[e["category"]] = costs.get(e["category"], Decimal(0)) + _d(e["amount_ngn"])
    expense_breakdown = [{"category": c, "label": CATEGORY_LABELS.get(c, c), "amount_ngn": _money(a),
                          "share_pct": round(float(a / _d(total["expense_ngn"]) * 100), 1) if total["expense_ngn"] else 0.0}
                         for c, a in sorted(costs.items(), key=lambda kv: kv[1], reverse=True)]
    return {"total": total, "by_season": by_season, "by_crop": by_crop, "expense_breakdown": expense_breakdown,
            "farm_size_ha": float(_size(size_ha)) if _size(size_ha) else None,
            "per_ha_note": None if total["profit_per_ha_ngn"] is not None else
            "Profit per hectare needs the farm size. Add it when you edit the farm.",
            "note": NOTE}
