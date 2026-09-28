from datetime import date
from .quality import quality_gate, QualityConfig
from .diagnosis import build_diagnosis
from .pesticides import verified_products
from .model_client import ModelClient, ModelError

def run_scan(p: dict, model: ModelClient, pesticide_rows: list, today: date, qcfg=QualityConfig()) -> dict:
    """p = validated scan payload. Order: quality gate -> model -> confidence gate -> IPM -> product lookup."""
    q = quality_gate(p["quality"], qcfg)
    if not q["passed"]:
        return {"status": "quality_failed", "message": q["message"], "quality": q, "prediction": None}
    pred = None
    if model.configured:
        try: pred = model.predict(p["image"], p["media_type"], p["crop_hint"])
        except ModelError:
            return {"status": "error", "message": "We couldn't process this image. Please upload a clearer photo.",
                    "quality": q, "prediction": None}
    d = build_diagnosis(pred, p["crop_hint"])
    d["quality"] = q
    if d["status"] == "ok":
        d["treatment_products"] = verified_products(pesticide_rows, (d.get("crop") or "").lower(),
                                                    d.get("condition"), today)
    d["_raw"] = pred
    return d
