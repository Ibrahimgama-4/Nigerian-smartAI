# Infrastructure & Data Costs
Prices/limits change: verify on each provider's site before launch.

| Service | Purpose | Free tier | Potential limitation | Paid upgrade trigger | Alternative |
|---|---|---|---|---|---|
| Vercel | Hosting + Python function | Hobby plan (non-commercial) | Not for commercial use; request-body and time limits | Charging users / commercial use / traffic | Render, Railway, Fly.io |
| Supabase | Auth + Postgres + storage | Free plan | Projects may pause when inactive; storage/DB size caps | Production use, backups, more users | Self-hosted Supabase, Neon + own auth |
| Open-Meteo | Weather | Non-commercial, <10,000 calls/day | Shared-IP limit hits; commercial not allowed | Any commercial use, or many farms | Paid Open-Meteo, self-host (AGPLv3) |
| GitHub Actions | CI | Free minutes | Minute caps | Heavy CI | — |
| Model hosting (Plant Doctor) | Inference service | Depends on host (e.g. Hugging Face Spaces free CPU) | Cold starts, memory, slow inference | Real traffic | Render/Railway/Fly, later on-device (TFLite/ONNX) |
| Model training | GPU | Google Colab free | Session limits | Larger datasets | Kaggle, cloud GPU |
| Anthropic API | Optional assistant | None (pay per use) | Cost scales with questions | Enabling the assistant | Leave assistant off |
| SMS/WhatsApp | Future alerts | Not integrated | Paid | When alerts go beyond in-app | — |
