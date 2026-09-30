# 🌾 KanoFarm AI — Smart Farming. Better Decisions. Higher Productivity.

Installable web app (PWA) for Nigerian farmers, in any of the 36 states or the FCT: real weather-based farm advice, farm records and timeline,
alerts, plant photo checks, IPM guidance, soil/irrigation guidance, expert review, and an optional AI assistant.
Built around one rule: **no fabricated data.** Where verified data does not exist, the app says so.

## Scope
The app now covers all 36 Nigerian states and the FCT: farmers pick their own state (used to default the map/weather
location and, in the crop calendar, to a geopolitical zone), and the assistant is told the farm's state instead of
assuming Kano. The brand name "KanoFarm AI" is kept as-is for now (renaming touches the manifest, PWA install
name, and every page title) but nothing in the app's logic or content is restricted to Kano. Tell me if you'd
rather rename it and I can do a full find-and-replace.

## Honest status
| Area | Status |
|---|---|
| Weather + advisories (Open-Meteo, modelled) | Working. Live call verified only by `scripts/smoke_weather.py` on your machine |
| Accounts, profile, farms, crops, timeline, alerts, history | Implemented (Supabase Auth + RLS). Needs your Supabase project |
| Rainfall since planting + rain/temperature charts | Implemented (Open-Meteo historical reanalysis + recent model days, labelled per source; partial totals flagged). Live API call not run in the authoring sandbox |
| Irrigation, soil & fertilizer guidance | Implemented, **general guidance only**, no dosages/volumes |
| Plant Doctor | Full pipeline (photo quality gate, confidence gate, IPM, product lookup, storage, expert review). **No trained model ships.** Until you train and deploy one it says the model is unavailable |
| Pesticide information | Registry + admin entry + expiry logic. **Empty** until verified records are added (NAFDAC bulk access not confirmed) |
| Crop calendar | **Draft** entries from secondary sources, tagged by geopolitical zone (North West, North East, North Central, South West, South East, South South), labelled and not agronomist-reviewed. No growth-stage calendar yet |
| Hausa, Yoruba, Igbo | The 6 weather-advisory messages and a small set of crop names now have AI-drafted translations (Hausa fullest, Igbo least certain). Every one is flagged `unreviewed` until a native speaker confirms it -- the app shows a visible "draft translation" note rather than presenting it as final. Nigerian Pidgin still has no draft text at all and falls back to English |
| AI assistant | Optional, multi-turn chat. Works with either Anthropic (paid) or Groq (has a free tier) -- set `ASSISTANT_PROVIDER` and the matching key. Off unless a key is set. Distinguishes auth, rate-limit, overloaded, timeout and network errors instead of one generic failure |
| Farm Market | Public listings with up to 4 photos: anyone can browse without an account; signed-in users post, mark sold, renew or delete; anyone signed in can report; 3 reports auto-hide a listing until an admin reviews it (More, Expert / Admin). Contact is by phone/WhatsApp; **no payments, delivery or escrow**. Moderation is report-based only (no automatic photo or text screening) |
| Voice for Ask KanoFarm | Speak a question (Web Speech API) and have replies read aloud (Speech Synthesis API). Both are free, browser-native, and feature-detected: the mic/speaker buttons simply don't appear on a phone that doesn't support them. Voice input needs an internet connection and is most reliable in English; it is a known-unreliable browser feature on some Android Chrome versions, so it is offered as an extra, not a requirement |
| Not built | Farm map/boundaries, push/SMS/WhatsApp, flood/pest/disease risk models, on-device AI, Capacitor/Play Store, full admin (users, farms, guides, model registry UI). Pesticide list is national (NAFDAC-scoped) already, not Kano-specific |

Tested here: 131 unit tests (pure logic). Endpoint tests (`tests_api/`) and app startup run in GitHub Actions because
the authoring sandbox had no network or FastAPI. The browser UI was syntax-checked but **not clicked through**;
expect some first-run fixes and please report them.

## Deploy (GitHub → Vercel + Supabase, no local machine)
> **Starting from scratch? Follow `docs/FRESH_DEPLOYMENT.md`** (step-by-step, including API keys and how to read errors).

1. **Supabase**: create a project → SQL editor → run all six files in `database/migrations/` in order (`0001` … `0006`).
   Authentication → Providers → Email. For a quick MVP turn **off "Confirm email"**; otherwise configure SMTP so confirmation emails arrive.
2. **GitHub**: create a repo and push this folder (root must contain `index.py`). Use GitHub Desktop or `git push`; browser upload skips dotfiles like `.github/`.
   Check the **Actions** tab: CI must be green.

   **Upgrading an old Kano-only database?** You already ran 0001–0003; run just `0004_nationwide.sql` then
   `0005_expand_crops.sql` (see `database/README.md`).
3. **Vercel**: Add New → Project → import the repo. Keep auto-detected settings. Add environment variables from `.env.example`
   (`SUPABASE_URL`, `SUPABASE_ANON_KEY` at minimum). Deploy.
4. Open `https://YOUR-APP.vercel.app/api/health` → it shows `status`, `code_version` and `assistant_module_version` (these prove which code is live), then open the site on Android Chrome → Install app.
5. Sign up in the app. To become an admin: Supabase SQL editor →
   `insert into user_roles (user_id, role) values ('<your id from Authentication → Users>', 'admin');`
6. Optional: train the Plant Doctor (`ml/README.md`), deploy `ml/serving`, set `AI_MODEL_ENDPOINT`/`AI_MODEL_TOKEN`.
7. Add verified pesticide records in **More → Expert / Admin** only when you have a citable source.
8. Optional: switch on the AI assistant for **free** with Groq (or with Anthropic, paid):
   - Create a free account at console.groq.com (no card required) and generate an API key.
   - In Vercel, set `ASSISTANT_PROVIDER=groq` and `GROQ_API_KEY=<your key>`, then redeploy.
   - Check `/api/config` shows `"assistant_enabled": true, "assistant_provider": "groq"`.
   - Open **More → Expert / Admin → AI assistant setup → Run a real live test**. It reports the exact provider error and
     lists the models your Groq key may use; put one of those names in `ASSISTANT_MODEL` if the default is refused.
   - Groq's free tier is real but finite (roughly 30 requests/minute and ~1,000/day on free models as of
     writing) — verify current numbers at console.groq.com/settings/limits, since providers change these
     without notice. Switch `ASSISTANT_PROVIDER` back to `anthropic` any time if you outgrow it.

## Local development (optional)
```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -t .          # pure-logic tests
python -m unittest discover -s tests_api -t .      # endpoint tests
uvicorn index:app --reload                         # http://localhost:8000
python scripts/smoke_weather.py                    # live Open-Meteo check
```

## Layout
`index.py` Vercel entrypoint · `kanofarm/` API + services + knowledge · `static/` mobile web app + PWA ·
`database/` migrations · `data/` crop calendar + data sources · `ml/` training, evaluation, serving ·
`tests/`, `tests_api/` · `docs/`

## Security model
Farmer requests carry the farmer's own Supabase JWT to Postgres, so **Row Level Security** enforces ownership; the backend holds no
service-role key. Uploads are size-limited and type-checked by content. Images are stored only if the farmer opts in.
Farm GPS is visible only to its owner. Rate limits are in-memory (per instance). Technical errors are logged, never shown.

## Licensing and cost warnings
* Open-Meteo free tier and Vercel Hobby are **non-commercial**. See `docs/COSTS.md` and `docs/DATA_SOURCES.md`.
* PlantDoc is CC BY 4.0 (attribution). PlantVillage license must be checked at your source.
* Choose and add your own project `LICENSE` before publishing.

## Limitations you must communicate to users
Weather is modelled at ~11 km. Alert thresholds are unreviewed defaults. Any future model is unvalidated for Nigerian field
conditions until tested on expert-labelled Nigerian images. Not a replacement for extension officers.
