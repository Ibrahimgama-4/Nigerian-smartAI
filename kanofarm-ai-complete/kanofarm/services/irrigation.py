"""Irrigation guidance from real weather signals. Deliberately gives NO water volumes."""
from .weather_rules import Advisory

MAP = {"rain_delay_irrigation": "rain_expected", "dry_spell_recent": "dry_conditions",
       "dry_spell_forecast": "dry_conditions", "high_water_demand": "high_demand", "heavy_rain": "heavy_rain"}
TEXT = {
    "rain_expected": "Rain is expected soon: reduce or avoid unnecessary irrigation.",
    "dry_conditions": "Dry conditions: check soil moisture in the root zone and consider irrigation where available.",
    "high_demand": "High crop water demand: increase how often you check soil moisture.",
    "heavy_rain": "Heavy rain is forecast: check drainage and do not irrigate.",
}
def irrigation_guidance(advisories: list, soil_moisture) -> dict:
    keys = []
    for a in advisories:
        k = MAP.get(a.id if isinstance(a, Advisory) else a["id"])
        if k and k not in keys: keys.append(k)
    return {"signals": [{"key": k, "text": TEXT[k]} for k in keys],
            "none": "No irrigation signal from the current weather data." if not keys else None,
            "soil_moisture_m3m3": soil_moisture,
            "soil_moisture_note": "Soil moisture is a modelled value for the top 1 cm of soil, not your root zone. Check the soil by hand.",
            "volume_note": "Exact irrigation volumes are not given: they need a crop and soil model that this app does not have."}
