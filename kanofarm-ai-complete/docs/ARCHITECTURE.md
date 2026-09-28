# Architecture (phase 1)

Open-Meteo → `WeatherClient` (fetch) → `weather_parser` (validate, label MODELLED) →
`WeatherService` (TTL cache by 0.1° grid) → `weather_rules` (documented thresholds → advisories with
evidence) → FastAPI `/api/weather` → Next.js PWA.

Principles
- Pure-Python core (`kanofarm/services`) has no framework dependency and is unit-tested.
- Every response carries `data_type`, `source`, `fetched_at`, `resolution_note`.
- Thresholds are configuration with an explicit `review_status`; defaults are UNREVIEWED
  and are shown as such until an agronomist signs them off.
- Advice text lives in `kanofarm/i18n/*.json`; Hausa strings are empty until reviewed by a
  native-speaking agronomist (English fallback, flagged in the API).
- Plant Doctor: quality gate → per-crop classifier → confidence gate (`ml/inference/confidence.py`).
  No model is shipped until evaluated (see ml/evaluation).

## Phase 2 additions
```
Browser PWA (static/) ──JWT──> FastAPI (index.py, Vercel) ──JWT──> Supabase REST (RLS enforced)
   │ canvas: resize + quality metrics                │
   │                                                 ├─> Open-Meteo (weather)
   └── /api/plant/scan ──> quality gate ──> Model service (ml/serving, separate host)
                              ──> confidence gate ──> IPM plan ──> verified-pesticide lookup (source + expiry) ──> save
```
Design rules: nothing shown without a source label; low-confidence never shown as diagnosis; severity/indicators not
fabricated; pesticides only from cited, unexpired, registered rows; IPM steps precede chemical control; assistant gets
LGA/ward + advisories only (no name, phone or GPS).
