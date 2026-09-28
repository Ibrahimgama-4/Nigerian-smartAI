# Fresh deployment guide (new GitHub repo + new Vercel project + new Supabase project)

Time needed: about 45 minutes. Everything below uses free tiers. Nothing here has been run against
your accounts by the author; if a screen looks different, the provider has changed its dashboard.

## 0. Accounts you need
| Account | Free? | Used for |
|---|---|---|
| GitHub | Yes | Stores the code |
| Supabase | Yes (free plan) | Database + user login |
| Vercel | Yes (Hobby plan, non-commercial only) | Hosts the app |
| Groq | Yes (free tier, no card) | AI assistant (optional) |
| Anthropic | No (pay per use, $5 minimum) | Alternative AI assistant (optional) |

## 1. Supabase (database + login)
1. supabase.com -> New project. Choose a name, a strong database password (save it), and a region near Nigeria.
2. Wait until the project finishes setting up. Open **SQL Editor -> New query**.
3. Run these six files from `database/migrations/`, one at a time, IN THIS ORDER
   (open the file, copy everything, paste, click Run, wait for "Success"):
   `0001_core.sql` -> `0002_features.sql` -> `0003_reference_crops.sql` -> `0004_nationwide.sql` -> `0005_expand_crops.sql` -> `0006_market.sql`
   (0006 creates the Farm Market tables and a public photo bucket named `market`.)
4. **Authentication -> Providers -> Email**: for a quick launch turn OFF "Confirm email".
   (If left on, new users must click a link in an email that Supabase only sends reliably once you set up your own email sender.)
5. **Project Settings -> API**: copy two values into a notes file:
   - **Project URL** (looks like `https://abcdxyz.supabase.co`)  -> this is `SUPABASE_URL`
   - **anon public key** (a long text starting `eyJ`; on newer dashboards it may be called the publishable key or sit on a "Legacy API keys" tab) -> this is `SUPABASE_ANON_KEY`
   Never use the `service_role` / secret key anywhere in this project.

## 2. GitHub (new repository)
1. github.com -> New repository. Name it e.g. `nigeria-agro`. Private or public both work. Do NOT tick "Add a README".
2. Unzip `kanofarm-ai-complete.zip` on your computer. Open the unzipped folder: you must see `index.py`, `kanofarm/`, `static/`, `database/`... directly inside it.
3. Upload. Recommended: **GitHub Desktop** (desktop.github.com): File -> Add local repository -> pick the unzipped folder -> Publish repository.
   Browser alternative: on the empty repo page choose "uploading an existing file", drag in the CONTENTS of the folder (not the folder itself).
   Warning: browser upload silently skips hidden folders such as `.github`. That only disables the automatic tests, the app still deploys.
4. Confirm on GitHub that `index.py` is at the top level of the repo, and that opening `static/app.js` shows the whole file (about 600 lines).

## 3. Vercel (new project)
1. vercel.com -> sign in with GitHub -> Add New -> Project -> import your new repo.
2. Leave every build setting on auto-detect. Do not set a build command or output directory.
3. Open **Environment Variables** and add these (tick Production, Preview and Development for each):
   | Name | Value |
   |---|---|
   | `SUPABASE_URL` | from step 1.5 |
   | `SUPABASE_ANON_KEY` | from step 1.5 |
   (Add the assistant variables from section 5 now or later.)
4. Click Deploy and wait for "Ready".
5. Any time you change a variable, or push new code: Deployments -> newest -> ... -> Redeploy is needed for variables; code pushes redeploy by themselves.

## 4. Verify (each URL should open in a normal browser, no login)
| URL | Expected |
|---|---|
| `https://YOUR-APP.vercel.app/api/health` | `{"status":"ok","code_version":"2026-09-28-market","assistant_module_version":"model-lister-v1"}` |
| `https://YOUR-APP.vercel.app/api/config` | `"accounts_enabled": true` |
| `https://YOUR-APP.vercel.app/api/states` | 37 states |
The two version words in /api/health prove which code is live. If they differ from the above, Vercel is still serving older code.

Then open the site, tap Sign in -> Create account, choose your state, add a farm and a crop.

Make yourself admin: Supabase -> Authentication -> Users -> copy your user ID, then SQL Editor:
```sql
insert into user_roles (user_id, role) values ('PASTE-YOUR-USER-ID', 'admin');
```
Sign out and back in; **More -> Expert / Admin** now appears.

## 5. AI assistant keys (optional) - read this whole section before starting
The assistant is OFF until a provider and its key are set. Set exactly ONE provider.

### Option A - Groq (free tier)
1. console.groq.com -> sign up. Verify email (and phone, if asked).
2. **Test the account first, in the browser:** console.groq.com/playground -> pick a model -> send "hello".
   If Playground cannot answer, no API key can; fix your Groq account first.
3. API Keys -> Create API Key -> name it -> copy it at once (starts with `gsk_`, shown only once).
4. Vercel variables:
   | Name | Value |
   |---|---|
   | `ASSISTANT_PROVIDER` | `groq` |
   | `GROQ_API_KEY` | your `gsk_...` key |
   | `ASSISTANT_MODEL` | leave empty at first |
5. Redeploy. Then **More -> Expert / Admin -> AI assistant setup -> Run a real live test**.
6. The card shows **"Models this key can use"** (asked from Groq itself). If the test says
   "model does not exist or you do not have access", copy one name from that list exactly
   (for example `openai/gpt-oss-120b`), set it as `ASSISTANT_MODEL` in Vercel, redeploy, test again.
7. If the card shows a Groq access message instead, open the links Groq gives in that message
   (console.groq.com/settings/limits and .../settings/project/limits: model permissions) and enable a model there.
Groq's free limits change without notice; check console.groq.com/settings/limits.

### Option B - Anthropic (paid)
1. console.anthropic.com -> sign up -> Settings -> Billing: add a card and buy credits (minimum $5, prepaid).
   A Claude.ai chat subscription does NOT include API credit.
2. Settings -> API Keys -> Create Key -> copy it at once (starts `sk-ant-`, shown only once).
3. Vercel variables: `ASSISTANT_PROVIDER` = `anthropic`, `ANTHROPIC_API_KEY` = your key. Redeploy and run the live test.

### Rules that prevent the common mistakes
- The provider name decides which key is read. `ASSISTANT_PROVIDER=groq` reads only `GROQ_API_KEY`.
  Deleting `ASSISTANT_PROVIDER` does not switch the assistant off - it falls back to `anthropic`.
- A blank `ASSISTANT_MODEL` is correct. A wrong model name gives "model does not exist".
- Paste keys with no spaces, quotes or line breaks. The masked preview in the admin card shows the key length.

## 6. Other optional variables
| Name | When |
|---|---|
| `WEATHER_API_KEY`, `WEATHER_API_URL`, `ARCHIVE_API_URL` | Only when you move to a paid/commercial Open-Meteo plan (free tier is non-commercial only) |
| `AI_MODEL_ENDPOINT`, `AI_MODEL_TOKEN` | Only after you train and host a Plant Doctor model (see `ml/README.md`) |

## 7. Reading assistant errors
| Message in the admin card | Meaning | Fix |
|---|---|---|
| skipped: no key configured | Active provider has no key | Set the matching key variable, redeploy |
| forbidden ... `error code: 1010` | Cloudflare blocked the server's request signature | Fixed in this version (proper User-Agent). If it returns, send the full line |
| http 401 | Key wrong, revoked or mistyped | Create a new key, paste again |
| http 403 with a Groq sentence | Account/project permission | Follow the link inside the sentence |
| http 404 model does not exist / no access | Model name not allowed for this key | Use a name from "Models this key can use" |
| http 429 | Free-tier rate limit | Wait, or reduce use |

## 8. Before charging users
Vercel Hobby and Open-Meteo's free API are for non-commercial use. See `docs/COSTS.md`.

## 9. Farm Market notes (read before opening it to the public)
- Anyone can browse; posting needs an account and a completed profile. Each user: max 5 new listings per hour and 20 live listings.
- Every listing shows the seller's chosen name and phone number publicly (the poster must tick a consent box). No GPS is ever shown.
- Photos are resized on the phone and stored in Supabase Storage (public bucket `market`). The free Supabase plan has a small storage
  and bandwidth allowance (about 1 GB storage at the time of writing; check current limits), so watch usage as the market grows.
- Moderation is by reports only: 3 different signed-in users reporting a listing hides it until an admin restores or removes it
  (More -> Expert / Admin -> "Market listings under review"). There is no automatic screening of photos or text, so assign someone to check that page.
- The app cannot stop scams or unsafe sales: it shows a safety notice, holds no money, and arranges no delivery.
- Pesticides and veterinary drugs are not a category and the rules say they must not be advertised; this is enforced by reports and admin review, not by software.
