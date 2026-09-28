# Plant Doctor: train, deploy, connect

**Honest scope.** PlantDoc (CC BY 4.0) can support **tomato, potato and pepper** disease classes for a starter model.
It has no healthy-maize class and no usable soybean class, and nothing for cowpea, groundnut, rice, sorghum, millet, cassava or onion.
Any model trained only on it is **unvalidated for Nigerian field conditions**. For Kano crops you must collect and expert-label
local images (the app's opt-in photo contribution + expert review is built for this).

## A. Train (free Google Colab, no local install)
1. Put this repo on GitHub. Open `ml/train_plant_doctor.ipynb` in Colab (File → Open notebook → GitHub tab). Runtime → T4 GPU.
2. Edit the `REPO` line, run the cells in order (about 10-30 minutes).
3. Read the per-class table. Classes with low recall or a handful of test images are unreliable. Accuracy alone means little.
4. The last cell downloads `plant_doctor_v1.zip` (model + label map + serving files).

Notes: the official PlantDoc test split is used. Crops from one original photo can still sit in both train and validation, so
scores are optimistic. Categories in `plantdoc_label_map.json` are drafts: **an agronomist must review them**.

## B. Host the model (Hugging Face Space, free CPU tier; verify current limits)
1. huggingface.co → New Space → SDK **Docker** (blank). Clone it, unzip `plant_doctor_v1.zip` into it, commit, push
   (model.pt may need `git lfs track "*.pt"`).
2. Space → Settings → **Variables and secrets** → add secret `MODEL_TOKEN` = a long random string you invent.
3. When the Space is running, test: `POST https://<user>-<space>.hf.space/predict` with header `Authorization: Bearer <token>`
   and JSON `{"image_b64": "<base64 jpeg>"}`. You should get `probs` and `label_meta`.
Free Spaces can sleep when idle, so the first scan after a quiet period may be slow or time out. Paid hardware or another
host (Render, Railway, Fly.io) avoids that.

## C. Connect to the app
Vercel → Project → Settings → Environment Variables:
`AI_MODEL_ENDPOINT = https://<user>-<space>.hf.space/predict` and `AI_MODEL_TOKEN = <same token>`, then **Redeploy**.
Open the site: Scan Plant no longer says the model is unavailable. Vercel function time limits vary by plan; if scans time out, check them.

## D. Register the model (audit trail)
`python scripts/model_card_to_sql.py ml/models/v1/model_card.json` → paste the output in the Supabase SQL editor.
Leave `validated_on_nigerian_field_data = false` until you test on expert-labelled Nigerian images.

## E. Make it good for Nigeria (the real work)
1. Farmers scan with "keep this photo" ticked. Experts label them in **More → Expert / Admin**.
2. Collect at least a few hundred expert-confirmed images per class across seasons, phones, lighting and growth stages,
   including **healthy** leaves and "other" cases. Add classes for cowpea, groundnut, sorghum, millet, rice, maize (with healthy).
3. Retrain with the new images added to `data/raw/<crop>___<condition>/`, keep a separate Nigerian **test** set the model never trains on,
   and only then consider changing the "unvalidated" wording.
4. Keep the confidence gate: low or ambiguous results are never shown as a diagnosis.
