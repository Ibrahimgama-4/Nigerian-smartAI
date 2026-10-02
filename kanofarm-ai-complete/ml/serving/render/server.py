"""Plant Doctor web service (ONNX, CPU). Same /predict contract the app already calls.
Env: MODEL_DIR (model.onnx, model_card.json, label_meta.json), MODEL_TOKEN (shared secret), PORT (set by Render)."""
import hmac, logging, os
from fastapi import Body, FastAPI, Header, HTTPException
try: from .inference import Predictor
except ImportError: from inference import Predictor

log = logging.getLogger("plant_doctor")
TOKEN = os.getenv("MODEL_TOKEN", "")
if not TOKEN: log.warning("MODEL_TOKEN is not set: anyone who knows the URL can call /predict")
predictor = Predictor.load(os.getenv("MODEL_DIR", "model"))     # fail at startup, visibly, if the model is missing
app = FastAPI(title="KanoFarm Plant Doctor")


@app.get("/health")
def health(): return {"status": "ok", "model_version": predictor.version, "classes": len(predictor.classes)}


@app.post("/predict")
def predict(payload: dict = Body(...), authorization: str = Header("")):
    if TOKEN and not hmac.compare_digest(authorization, f"Bearer {TOKEN}"): raise HTTPException(401)
    try: return predictor.predict(payload.get("image_b64"))
    except ValueError as e: raise HTTPException(400, str(e))
    except Exception:
        log.exception("prediction failed"); raise HTTPException(500, "prediction failed")
