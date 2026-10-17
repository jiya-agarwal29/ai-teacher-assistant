# Deploying to Render — step by step

This walks through putting a real, working copy of this app online, for free,
using:

- **[Neon](https://neon.tech)** — a free hosted Postgres database.
- **[Supabase](https://supabase.com)** — a free private file bucket (S3-compatible Storage).
- **[Render](https://render.com)** — hosts both the backend (as a web service) and the frontend (as a static site), deployed together from one `render.yaml` Blueprint already in this repo.

It assumes you've never used any of the three before. Every secret value is
yours to create and keep — **nothing below shows a real key or password**;
wherever you see a placeholder like `<something>`, replace the whole
placeholder (including the `<` `>`) with your own value.

You'll need: this code pushed to a GitHub repository you own, and a free
account on each of Neon, Supabase, and Render (all three let you sign up with
your GitHub account, which is the easiest option).

---

## 1. Create the database (Neon)

1. Go to [neon.tech](https://neon.tech) and sign in (GitHub sign-in is fine).
2. Create a new **Project**. Pick a region close to Singapore (Render's
   backend will run in Singapore — see step 3) — Neon's closest region is
   usually fine even if it's not an exact match; cross-region latency between
   Neon and Render is small compared to a cold start anyway.
3. Once the project is created, you're on a branch called `production` (or
   `main`) by default — that's fine, that *is* your production database. (If
   you later want a separate branch for testing migrations before they hit
   production, Neon lets you create one, but you don't need that for this
   guide.)
4. Click **Connect** (or **Connection Details**) on the project dashboard.
5. There are usually two connection strings shown: a **direct** one and a
   **pooled** one (sometimes labelled "Pooled connection" with a toggle, and
   the pooled hostname contains `-pooler` in it). **Use the pooled one** —
   the app opens a new connection per request under load, and Neon's pooler
   handles that far better than a direct connection.
6. Copy that pooled connection string somewhere safe for a moment (e.g. a
   local text file you'll delete after step 5 below) — you'll need it as
   `DATABASE_URL`. It looks like:
   ```
   postgresql://<user>:<password>@<something>-pooler.<region>.aws.neon.tech/<dbname>?sslmode=require
   ```

Keep this tab open — you'll come back to it if you ever need to rotate the
password (see [Rotating a leaked key](#rotating-a-leaked-key) below).

---

## 2. Create the file bucket (Supabase)

Uploaded documents and scanned page images need somewhere to live that
survives a redeploy — Render's own disk does not.

1. Go to [supabase.com](https://supabase.com) and sign in.
2. Create a new **Project** (pick any region; it doesn't need to match
   Neon's or Render's).
3. In the left sidebar, open **Storage** and click **New bucket**.
   - Name it exactly `uploads`.
   - **Turn "Public bucket" OFF.** This is important — the bucket must stay
     **private**. This app never generates a public link to a file; it
     always streams the file through the backend after checking you're the
     owner, so a private bucket is all it ever needs, and a public one would
     let anyone who guesses a filename download it directly.
4. Now get S3-compatible credentials for that bucket: open
   **Project Settings → Storage** (or, in newer Supabase UIs, **Project
   Settings → Data API** / **Storage settings**) and find the **S3
   Connection** section. It shows:
   - An **Endpoint URL** (something like `https://<project-ref>.supabase.co/storage/v1/s3`)
   - A **Region** (e.g. `ap-southeast-1`)
5. Click **New access key** (sometimes called "S3 Access Keys") to generate
   an **Access Key ID** and **Secret Access Key**. **Create a key dedicated
   to this production deployment** — don't reuse a personal/test key you've
   used elsewhere, so you can revoke this one later without breaking
   anything else.
6. Note down all five values — you'll need them as `S3_ENDPOINT_URL`,
   `S3_REGION`, `S3_BUCKET` (`uploads`), `S3_ACCESS_KEY_ID`, and
   `S3_SECRET_ACCESS_KEY`.

Supabase's free tier gives you **1 GB of Storage total** and caps any single
uploaded file at **50 MB** — this is why `render.yaml` sets
`MAX_UPLOAD_MB=50` for the backend (see [Free-plan limits](#free-plan-limits-and-what-they-mean-for-your-users) below).

---

## 3. Get a Gemini API key

If you don't already have one: go to
[aistudio.google.com/apikey](https://aistudio.google.com/apikey), sign in,
and create a key. This is `GEMINI_API_KEY` — it's used for both AI answer
generation and OCR (scanned page transcription), and by default for
embeddings too.

---

## 4. Deploy on Render

1. Go to [render.com](https://render.com) and sign in with GitHub (this
   makes connecting your repo in the next step a single click).
2. Click **New +** → **Blueprint**.
3. Pick the GitHub repository this code lives in. Render will find the
   `render.yaml` file at the repo root automatically and show you a preview
   of the two services it defines: a backend web service and a frontend
   static site.
4. Render will ask you to fill in every environment variable marked
   `sync: false` in `render.yaml` before it lets you deploy. Fill them in
   now:

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | The Neon **pooled** connection string from step 1. |
   | `SECRET_KEY` | A long random string — generate one locally with `python -c "import secrets; print(secrets.token_hex(32))"` and paste the output. This is only ever typed here, never committed anywhere. |
   | `GEMINI_API_KEY` | Your key from step 3. |
   | `S3_ENDPOINT_URL` | From step 2. |
   | `S3_REGION` | From step 2. |
   | `S3_BUCKET` | `uploads` |
   | `S3_ACCESS_KEY_ID` | From step 2. |
   | `S3_SECRET_ACCESS_KEY` | From step 2. |
   | `CORS_ORIGINS` | Leave this as a placeholder for now (e.g. `https://placeholder.onrender.com`) — you don't know the real frontend URL yet. **You'll come back and fix this in step 5.** |
   | `VITE_API_URL` | Same — leave a placeholder for now, fix it in step 5. |

5. Both services are on Render's **Free** plan already (set in
   `render.yaml`) — nothing to pick here. The region is pinned to
   **Singapore** for the backend.
6. Click **Apply** / **Create Blueprint**. Render will build and deploy both
   services. The backend's build installs Python dependencies and its start
   command runs `alembic upgrade head` (creating all the database tables on
   Neon for the very first deploy) before starting the server. The first
   deploy of each service can take a few minutes — watch the build logs on
   each service's page.

---

## 5. Connect the two services to each other

This is the step most people get wrong on the first try, because the two
URLs don't exist until *after* the first deploy in step 4.

1. Once both services show **Live**, open the backend service's page and
   copy its URL from the top of the page (looks like
   `https://ai-teacher-assistant-backend-xxxx.onrender.com`).
2. Open the frontend service's page and copy *its* URL the same way (looks
   like `https://ai-teacher-assistant-frontend-xxxx.onrender.com`).
3. Go back to the **backend** service → **Environment** tab, and set
   `CORS_ORIGINS` to the **frontend's** URL from step 2 (exactly, no trailing
   slash). Save — this triggers a backend redeploy.
4. Go to the **frontend** service → **Environment** tab, and set
   `VITE_API_URL` to the **backend's** URL from step 1. Save.
5. **`VITE_API_URL` is baked into the frontend's build at build time, not
   read at runtime** — so saving it alone isn't enough. Go to the frontend
   service's **Manual Deploy** button and trigger **Deploy latest commit**
   (or **Clear build cache & deploy**) so it actually rebuilds with the new
   value. Skipping this step is the single most common reason a Render
   deployment "doesn't work" — the frontend will look deployed but will
   silently still be pointed at whatever `VITE_API_URL` was (or wasn't) set
   to during its very first build.

Wait for both redeploys to finish (**Live** again on both).

---

## 6. First-login smoke test

1. Open the frontend's URL in a browser.
2. **The backend may be asleep** (see [Free-plan limits](#free-plan-limits-and-what-they-mean-for-your-users)) — the first page load might take up to a minute and you should see the "Waking up the server…" banner while it does. This is expected, not a bug.
3. Register a new account, then log in with it.
4. Upload a small text file or PDF as a test document, and confirm it reaches
   `status: ready` (refresh the Documents page after a few seconds).
5. Ask it a question in Chat about that document and confirm you get a real,
   grounded answer back (not an error).

If any of this fails, see [Reading the logs](#reading-the-logs) below before
assuming something is broken — a Gemini free-tier rate limit or a sleeping
backend both look like failures at first glance but aren't actual bugs.

---

## Reading the logs

On Render, open the service (backend or frontend) → **Logs** tab. This
streams live output as requests come in. A few things worth knowing:

- The backend never logs secrets, uploaded document text, or AI
  prompts/responses — only status/error-level messages about what happened
  (see `README.md`'s "ENV=production" section). If you see something that
  *looks* like a secret in the logs, that's a bug worth reporting, not
  expected behavior.
- A one-off request error shows a Python traceback ending in the real
  exception — the user who made that request only ever sees a generic
  "Internal server error", so the logs are the only place to see what
  actually went wrong.
- `GET /health` (used by Render's own health check, and safe to open
  yourself in a browser) reports whether the database is reachable, which
  storage backend and AI provider are active, and nothing secret — a quick
  first thing to check if the app seems down.

---

## Rotating a leaked key

If any secret above (Neon's connection string, a Supabase S3 key,
`SECRET_KEY`, or `GEMINI_API_KEY`) is ever exposed — committed to git by
mistake, pasted somewhere public, etc. — rotate it immediately:

- **Neon `DATABASE_URL`:** In the Neon dashboard, go to your project →
  **Roles** (or **Settings → Reset password** on the role used in the
  connection string) and reset the password. This immediately invalidates
  the old connection string. Copy the new pooled connection string and
  update `DATABASE_URL` in Render's backend **Environment** tab, then
  save (triggers a redeploy).
- **Supabase S3 key:** In Supabase → **Project Settings → Storage → S3
  Connection**, delete the leaked access key and create a new one. Update
  `S3_ACCESS_KEY_ID` and `S3_SECRET_ACCESS_KEY` in Render's backend
  environment, then save.
- **`SECRET_KEY`:** Generate a new one the same way as in step 4
  (`python -c "import secrets; print(secrets.token_hex(32))"`) and update it
  in Render's backend environment. **Every existing logged-in user will be
  signed out** — tokens signed with the old key stop validating — so only
  rotate this one when you actually need to.
- **`GEMINI_API_KEY`:** In [aistudio.google.com/apikey](https://aistudio.google.com/apikey), delete the leaked key and create a new one. Update it in
  Render's backend environment.

In every case: update the value in Render's **Environment** tab (never in a
file committed to the repo), save, and confirm the service redeploys
successfully before considering the old key fully retired.

---

## Free-plan limits and what they mean for your users

Everything above runs entirely on free tiers. That's enough for personal use
or a small class, but worth understanding:

- **The backend sleeps after inactivity (Render free plan).** After roughly
  15 minutes with no requests, Render spins the backend down; the next
  request wakes it back up, which can take up to about a minute. Your users
  will see the frontend's "Waking up the server…" banner (see
  `README.md`'s item 8 / `ColdStartBanner`) during that first request after
  a quiet period — it's not an error, just a one-time wait.
- **Uploads are capped at 50 MB per file.** This matches Supabase Storage's
  free-tier per-file limit (`MAX_UPLOAD_MB=50` in `render.yaml`). A larger
  scanned PDF needs to be split or compressed before uploading.
- **Total storage is capped at 1 GB (Supabase free tier).** Across every
  user's uploaded documents and page images combined. Once you're close to
  that limit, new uploads will start failing — there's no automatic
  warning for this today, so keep an eye on Supabase's dashboard (**Storage**
  → bucket usage) if you have several active users.
- **Neon's free tier also auto-suspends an inactive database** after a
  period with no queries, and resumes automatically on the next query — this
  adds to, but is separate from, the backend's own sleep above. Both
  "waking up" delays can stack on the very first request after a long quiet
  period.
- **Supabase projects pause after about a week of total inactivity** (no
  Storage or database activity on that project at all) — see `README.md`'s
  "Pausing (Supabase free tier)" section for how to un-pause it from the
  Supabase dashboard if this happens.

None of this needs a cron job or any kind of keep-alive workaround to "fix"
— letting a genuinely idle deployment sleep/pause is the free tier working
as intended, not a problem.
