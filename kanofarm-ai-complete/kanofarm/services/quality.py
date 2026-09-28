"""Image quality gate. Metrics (mean brightness 0-255, Laplacian-variance sharpness on a 256px grayscale)
are computed in the browser BEFORE upload; the same thresholds are enforced here. Defaults are UNVALIDATED.
Not checked (cannot be done honestly without a vision model): plant too small, obstruction, multiple plants."""
from dataclasses import dataclass, asdict

MSG = ("The image quality is not sufficient for reliable diagnosis. Please take another clear photo "
       "of the affected leaf or plant.")

@dataclass(frozen=True)
class QualityConfig:
    min_brightness: float = 40.0
    max_brightness: float = 235.0
    min_sharpness: float = 40.0
    review_status: str = "default_unvalidated"
    def public(self): return asdict(self)

def quality_gate(q: dict, cfg: QualityConfig = QualityConfig()) -> dict:
    problems = []
    if q["brightness"] < cfg.min_brightness: problems.append("too_dark")
    if q["brightness"] > cfg.max_brightness: problems.append("too_bright")
    if q["sharpness"] < cfg.min_sharpness: problems.append("blurry")
    return {"passed": not problems, "problems": problems, "message": None if not problems else MSG}
