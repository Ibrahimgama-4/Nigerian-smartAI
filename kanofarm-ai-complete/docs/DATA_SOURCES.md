# Data Sources (audit status)

Legend: VERIFIED = checked against the provider's own documentation/terms during this audit.
NOT_YET_VERIFIED = candidate only; **no feature may depend on it until verified**.

## Open-Meteo Forecast API — VERIFIED (terms), MODELLED data
- Site: https://open-meteo.com  ·  Terms: https://open-meteo.com/en/terms
- Endpoint: https://api.open-meteo.com/v1/forecast (no API key on the free tier)
- Variables used: current (temperature_2m, relative_humidity_2m, precipitation, wind_speed_10m);
  daily (temperature_2m_max/min, precipitation_sum, precipitation_probability_max,
  et0_fao_evapotranspiration, wind_speed_10m_max, shortwave_radiation_sum);
  hourly (soil_moisture_0_to_1cm).
- Data type: **MODELLED** numerical weather prediction from national weather-service models.
  Not ground-station observations.
- Resolution: global models ~11 km (regional up to 1.5 km where available). **Ward/community-level
  weather is NOT supported.** The app rounds coordinates to 0.1° for caching and says so in the UI.
- Free tier: non-commercial use only; <10,000 calls/day, 5,000/hour, 600/minute; CC BY 4.0
  attribution required; provider may block abusive IPs. Shared-hosting IPs can hit limits early.
- Commercial use: requires paid subscription or self-hosting (server code AGPLv3).
- Limitation: no warranty on accuracy or availability.
- Nigeria coverage: global model coverage; local accuracy anywhere in Nigeria NOT independently validated.

## PlantDoc — VERIFIED (license), image dataset
- https://universe.roboflow.com/pjtrs/plantdoc-6hbvl · Paper: arXiv:1911.10317
- License CC BY 4.0 (attribution: Singh et al., 2020). ~2.6k images, 13 species, non-controlled settings.
- Nigerian-crop coverage: expected to include maize, tomato, pepper, potato, soybean; **not** rice,
  cowpea, groundnut, cassava, onion (verify the class list before training).
- Not validated for Nigerian field conditions.

## PlantVillage — NOT_YET_VERIFIED (license per mirror unclear)
- Laboratory-condition images. Use only from the original source; pretraining only, never for
  validation claims.

## NAFDAC pesticide register (NARPAD / Green Book) — ACCESS NOT CONFIRMED
- No API, bulk download or reuse licence confirmed. **Do not scrape.**
- Pesticide rows are entered manually with a source citation and `last_verified`, or obtained via a
  data-sharing request to NAFDAC. Registration status changes (NAFDAC has reviewed/phased-out some
  active ingredients), so records expire (see `pesticides.expires_at`).

## Not yet verified (candidates)
NASA POWER, CHIRPS, HDX/OCHA Nigeria boundaries, OpenStreetMap (ODbL; public tile policy forbids
heavy production use), SRTM, FAOSTAT, IITA, ICRISAT, state Agricultural Development Programmes (ADPs).

## AfroTools Nigeria agriculture dataset — VERIFIED (terms), secondary/general
Used only for the geopolitical-zone groupings and each zone's commonly grown crops (`data/nigeria_zones.json`).
See `data/data_sources.json` for the full citation and limitations; the site itself calls this a planning
reference, not agronomic advice.

## Crop calendar (draft entries) — RETRIEVED, NOT REVIEWED
Secondary sources (a farming-information site and a Kano-specific "weather guide" blog, the latter used only for
its Kano-tagged entries); not official agency data. Stored in `data/crop_calendar.json`, tagged per entry with the
geopolitical zone(s) it applies to (or "all" for nationwide claims), with `review_status: draft_unreviewed` and
shown with a warning. Replace with each state's ADP / IAR / IITA-reviewed data as it becomes available.

## Nigeria states & zones reference — internal, general knowledge
`data/nigeria_states.json` lists all 36 states plus the FCT with their geopolitical zone and an approximate
capital-city coordinate (general geographic knowledge, not verified against a mapping API; used only to centre a
map or pre-fill a location field, never for scientific claims).
