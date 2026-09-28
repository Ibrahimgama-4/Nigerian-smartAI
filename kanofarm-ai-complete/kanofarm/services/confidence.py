"""Confidence gate for Plant Doctor. Thresholds are configurable defaults, NOT calibrated
probabilities: softmax scores from an unvalidated model can be overconfident."""
from dataclasses import dataclass

@dataclass(frozen=True)
class ConfidenceConfig:
    high: float = 0.85
    moderate: float = 0.65
    margin_min: float = 0.10   # top-1 must beat top-2 by this much to be shown as a diagnosis

LOW_MSG = ("The AI is uncertain about this diagnosis. Please upload a clearer image or consult "
           "an agricultural extension professional.")

def gate(probs: dict, cfg: ConfidenceConfig = ConfidenceConfig()) -> dict:
    """probs: {label: probability}. Returns level + whether a diagnosis may be displayed."""
    if not probs:
        return {"level": "none", "show_diagnosis": False, "message": LOW_MSG}
    ranked = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)
    top_label, top_p = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_p >= cfg.high and top_p - second >= cfg.margin_min:
        level = "high"
    elif top_p >= cfg.moderate and top_p - second >= cfg.margin_min:
        level = "moderate"
    else:
        level = "low"
    return {"level": level, "top_label": top_label, "confidence": round(top_p, 3),
            "show_diagnosis": level != "low",
            "message": LOW_MSG if level == "low" else None}
