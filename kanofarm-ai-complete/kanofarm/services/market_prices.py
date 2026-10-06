"""Asking-price statistics from Farm Market listings (pure logic; the database does the aggregation, see migration 0008).

These are ASKING prices typed by sellers. They are not verified market prices and not what anyone paid. The database
already refuses small samples; this module checks again so a mis-deployed or changed function can never show thin data."""
import re

MIN_SUMMARY_LISTINGS, MIN_SUMMARY_SELLERS = 5, 3
MIN_WEEK_LISTINGS, MIN_WEEK_SELLERS = 3, 2
STEADY_BAND_PCT = 5.0
MIN_TREND_WEEKS = 3

NOTE = ("These are ASKING prices that sellers typed into Farm Market listings. They are not verified market prices and not "
        "what anyone actually paid. Product names are typed by sellers, so similar goods may appear under different names. "
        "Prices differ by quality, quantity and place. Use them as a rough guide only, and check with buyers and traders near you.")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

def normalize_product(raw) -> str:
    """Mirrors the database function market_norm_product: lowercase, trimmed, single spaces. Punctuation is kept so a name
    returned by the summary matches itself when it is sent back to ask for its weekly trend. Max 60 characters."""
    t = _CONTROL.sub(" ", str(raw or "")).lower()
    return re.sub(r"\s+", " ", t).strip()[:60]

def _num(x):
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if f == f and f >= 0 else None

def clean_summary(rows) -> list:
    out = []
    for r in rows or []:
        n, k, med, lo, hi = int(r.get("listings") or 0), int(r.get("sellers") or 0), _num(r.get("median_ngn")), _num(r.get("low_ngn")), _num(r.get("high_ngn"))
        if n < MIN_SUMMARY_LISTINGS or k < MIN_SUMMARY_SELLERS or med is None or not r.get("product") or not r.get("price_unit"):
            continue
        out.append({"product": r["product"], "unit": r["price_unit"], "listings": n, "sellers": k, "median_ngn": med,
                    "typical_low_ngn": lo, "typical_high_ngn": hi, "last_listing": r.get("last_listing")})
    return out

def clean_weekly(rows) -> list:
    out = []
    for r in rows or []:
        n, k, med = int(r.get("listings") or 0), int(r.get("sellers") or 0), _num(r.get("median_ngn"))
        if n < MIN_WEEK_LISTINGS or k < MIN_WEEK_SELLERS or med is None or not r.get("week_start"):
            continue
        out.append({"week_start": r["week_start"], "listings": n, "sellers": k, "median_ngn": med,
                    "typical_low_ngn": _num(r.get("low_ngn")), "typical_high_ngn": _num(r.get("high_ngn"))})
    return sorted(out, key=lambda p: p["week_start"])

def trend(points: list) -> dict:
    """Compares the first and last week that have enough listings. Needs at least 3 such weeks, otherwise says so."""
    if len(points) < MIN_TREND_WEEKS:
        return {"direction": "not_enough_data", "change_pct": None,
                "message": f"Not enough weeks with enough listings to show a trend yet (need {MIN_TREND_WEEKS}, have {len(points)})."}
    first, last = points[0], points[-1]
    if not first["median_ngn"]:
        return {"direction": "not_enough_data", "change_pct": None, "message": "Not enough data to show a trend."}
    pct = round((last["median_ngn"] - first["median_ngn"]) / first["median_ngn"] * 100, 1)
    direction = "steady" if abs(pct) < STEADY_BAND_PCT else ("up" if pct > 0 else "down")
    word = {"steady": "stayed about the same", "up": f"were about {abs(pct):g}% higher", "down": f"were about {abs(pct):g}% lower"}[direction]
    return {"direction": direction, "change_pct": pct, "from_week": first["week_start"], "to_week": last["week_start"],
            "message": f"Asking prices in the week of {last['week_start']} {word} than in the week of {first['week_start']}. "
                       f"This compares two weeks only and is a rough guide."}
