"""Plant Doctor inference service (deploy separately, e.g. Hugging Face Space / Render / Railway; NOT on Vercel).
Env: MODEL_DIR (contains model.pt, model_card.json, label_meta.json), MODEL_TOKEN (shared secret).
NOT executed in the authoring sandbox."""
import base64, io, json, os
from pathlib import Path
import torch
from fastapi import Body, FastAPI, Header, HTTPException
from PIL import Image
from torchvision import transforms

DIR = Path(os.getenv("MODEL_DIR", "model")); TOKEN = os.getenv("MODEL_TOKEN", "")
card = json.loads((DIR / "model_card.json").read_text()); classes = card["classes"]
meta = json.loads((DIR / "label_meta.json").read_text()); model = torch.jit.load(str(DIR / "model.pt")).eval()
tf = transforms.Compose([transforms.Resize(256), transforms.CenterCrop(224), transforms.ToTensor(),
                         transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
app = FastAPI()

@app.post("/predict")
def predict(payload: dict = Body(...), authorization: str = Header("")):
    if TOKEN and authorization != f"Bearer {TOKEN}": raise HTTPException(401)
    img = Image.open(io.BytesIO(base64.b64decode(payload["image_b64"]))).convert("RGB")
    with torch.no_grad(): p = torch.softmax(model(tf(img).unsqueeze(0)), 1)[0].tolist()
    return {"model_version": card["version"], "probs": dict(zip(classes, p)), "label_meta": meta}
