# Plant Doctor on Render (free, ONNX)
Hugging Face Docker Spaces now need a paid plan, so this host uses ONNX + onnxruntime (small enough for a 512 MB free
instance). Same `/predict` contract the app already uses. Verify Render's current free-tier limits at render.com/pricing.

## 1. Convert your trained model (Colab, ~3 minutes)
New Colab notebook (CPU is fine), then:
```
!git clone --depth 1 https://github.com/YOUR-NAME/YOUR-REPO
%cd YOUR-REPO/SUBDIR-IF-ANY
!pip install -q onnx onnxruntime
# left sidebar -> Files -> upload plant_doctor_v1.zip into this folder, then:
!python -m ml.training.export_onnx --zip plant_doctor_v1.zip --out out_onnx
from google.colab import files; files.download("plant_doctor_onnx.zip")
```
It must print `READY: ...` and a tiny "max probability difference". If it says ONNX does NOT match, stop and report it.

## 2. Put it on GitHub
Create a NEW repo (e.g. `kanofarm-plant-doctor`), unzip `plant_doctor_onnx.zip`, upload everything so `Dockerfile` is at the repo root
and `model/model.onnx` exists. (The `model/` folder must be uploaded too.)

## 3. Render
render.com -> New -> Web Service -> connect that repo. Language **Docker**, Instance type **Free**.
Environment variable: `MODEL_TOKEN` = a long random string you invent. Under Advanced set Health Check Path `/health`. Deploy.
URL looks like `https://kanofarm-plant-doctor.onrender.com`. Open `/health`: it should show the model version and class count.

## 4. Connect the app (Vercel)
`AI_MODEL_ENDPOINT = https://<your-service>.onrender.com/predict` and `AI_MODEL_TOKEN = <same token>`, then Redeploy.
`/api/config` should show `"model_enabled": true`.

Free instances sleep after idle time; the first scan afterwards can be slow. Open `/health` in a browser before demos, or
use a free uptime pinger on `/health`.
