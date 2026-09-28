"""Turns a raw model prediction into a farmer-facing diagnosis. Never fabricates:
 - severity is 'not_assessed' (the model does not measure it)
 - 'observed indicators' are not produced (no lesion-level model)
 - low-confidence, ambiguous, or crop-mismatched results are NOT shown as a diagnosis."""
from .confidence import gate, ConfidenceConfig, LOW_MSG
from .ipm import ipm_plan

UNAVAILABLE = ("The Plant Doctor model is not available yet, so no diagnosis can be given. "
               "Please consult an agricultural extension professional.")
DISCLAIMER = ("Results are estimates based on image analysis and available model knowledge. "
              "Low-confidence results should be confirmed by an agricultural professional. "
              "AI-assisted identification only.")

def build_diagnosis(pred, crop_hint=None, cfg: ConfidenceConfig = ConfidenceConfig()) -> dict:
    base = {"disclaimer": DISCLAIMER, "severity": "not_assessed", "indicators": None,
            "validated_for_nigerian_field_conditions": False}
    if pred is None:
        return {**base, "status": "model_unavailable", "message": UNAVAILABLE, "ipm": ipm_plan("unknown")}
    g = gate(pred["probs"], cfg)
    meta = pred["label_meta"].get(g.get("top_label"), {}) if g.get("top_label") else {}
    category = meta.get("category") if meta.get("category") in (
        "disease", "pest", "nutrient_deficiency", "environmental_stress", "healthy") else "unknown"
    crop = meta.get("crop")
    top3 = sorted(pred["probs"].items(), key=lambda kv: kv[1], reverse=True)[:3]
    base.update({"model_version": pred["model_version"], "confidence_level": g["level"]})
    if crop_hint and crop and crop_hint.lower() != crop.lower():
        return {**base, "status": "crop_mismatch", "ipm": ipm_plan("unknown"),
                "message": f"The image looks like a different crop from the one you selected ({crop_hint}). Please check the crop and retake the photo."}
    if not g["show_diagnosis"]:
        return {**base, "status": "low_confidence", "message": LOW_MSG, "ipm": ipm_plan("unknown")}
    return {**base, "status": "ok", "crop": crop, "condition": meta.get("condition") or g["top_label"],
            "label": g["top_label"], "category": category, "confidence": g["confidence"],
            "alternatives": [{"label": l, "probability": round(p, 3)} for l, p in top3[1:]],
            "ipm": ipm_plan(category), "message": None}
