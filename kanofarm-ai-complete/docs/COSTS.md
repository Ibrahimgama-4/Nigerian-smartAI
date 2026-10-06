# Infrastructure & Data Costs
Prices/limits change: verify on each provider's site before launch.

| Service | Purpose | Free tier | Potential limitation | Paid upgrade trigger | Alternative |
|---|---|---|---|---|---|
| Vercel | Hosting + Python function | Hobby plan (non-commercial) | Not for commercial use; request-body and time limits | Charging users / commercial use / traffic | Render, Railway, Fly.io |
| Supabase | Auth + Postgres + storage | Free plan | Projects may pause when inactive; storage/DB size caps | Production use, backups, more users | Self-hosted Supabase, Neon + own auth |
| Open-Meteo | Weather | Non-commercial, <10,000 calls/day | Shared-IP limit hits; commercial not allowed | Any commercial use, or many farms | Paid Open-Meteo, self-host (AGPLv3) |
| GitHub Actions | CI | Free minutes | Minute caps | Heavy CI | — |
| Model hosting (Plant Doctor) | Inference service | Render free web service (ONNX; sleeps when idle). Hugging Face Docker Spaces now need a paid plan | Cold starts, memory, slow inference | Real traffic | Render/Railway/Fly, later on-device (TFLite/ONNX) |
| Model training | GPU | Google Colab free | Session limits | Larger datasets | Kaggle, cloud GPU |
| Anthropic API | Optional assistant | None (pay per use) | Cost scales with questions | Enabling the assistant | Leave assistant off |
| Web push | Alerts outside the app | Free (VAPID, no account) | Not every phone/browser supports it | — | — |
| WhatsApp Cloud API | Optional alerts | Off by default; business-initiated messages are charged by Meta | Template approval needed | Enabling it | Web push |
| SMS | Alerts | Not integrated (stub) | Paid; no dependable free route | Choosing a provider | Web push |
| Vercel Cron | Daily alert run | Hobby: once per day | No hourly runs | Need more frequent runs | GitHub Actions schedule |
| Supabase Storage (Farm Market photos) | Listing photos, public bucket | Included in the free plan | Small storage/bandwidth caps (about 1 GB at time of writing; verify) | Many active listings or heavy traffic | Cloudflare R2, or delete expired listings' photos |
