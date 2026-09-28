"""Calls the separately-hosted Plant Doctor model service (see ml/serving). Vercel functions cannot
host PyTorch. If no endpoint is configured the app says so; it never invents a prediction."""
import base64, json, urllib.request
from typing import Callable, Optional

class ModelError(Exception):
    pass

def _post(url, payload: dict, token: str, timeout=25):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:   # nosec - configured endpoint
        return json.loads(r.read().decode())

def validate_prediction(p) -> dict:
    if not isinstance(p, dict) or not isinstance(p.get("probs"), dict) or not p["probs"]:
        raise ModelError("bad response")
    probs = {}
    for k, v in p["probs"].items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)) or not (0 <= v <= 1):
            raise ModelError("bad probability")
        probs[k] = float(v)
    if sum(probs.values()) > 1.02: raise ModelError("probabilities exceed 1")
    meta = p.get("label_meta") if isinstance(p.get("label_meta"), dict) else {}
    return {"model_version": str(p.get("model_version") or "") or None, "probs": probs, "label_meta": meta}

class ModelClient:
    def __init__(self, endpoint: str, token: str = "", post: Callable = _post):
        self.endpoint, self.token, self.post = endpoint, token, post
    @property
    def configured(self) -> bool: return bool(self.endpoint)
    def predict(self, image: bytes, media_type: str, crop_hint: Optional[str]) -> dict:
        try:
            raw = self.post(self.endpoint, {"image_b64": base64.b64encode(image).decode(),
                                            "media_type": media_type, "crop_hint": crop_hint}, self.token)
        except Exception as e:
            raise ModelError(type(e).__name__) from e
        return validate_prediction(raw)
