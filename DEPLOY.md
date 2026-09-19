# 🚀 Deploy the Campus Digital Twin — FREE, step by step

This guide gets your project onto the internet at a real `https://…` URL
for **₹0 / $0**, using only free tiers:

| Piece | Free tool | What it does |
|---|---|---|
| App host (backend + frontend + simulator) | **Render** | Runs the whole platform, serves the dashboard, keeps WebSockets alive |
| Database (optional but recommended) | **Supabase** | Postgres so history survives restarts |

> ❓ Why not Vercel/Netlify for the app? Those run serverless functions
> that can't hold WebSockets open or keep the 14 simulator threads
> running — the live-streaming core of this project needs a real
> always-on server, which Render's free tier provides.

---

## PART 1 — Supabase database (~5 minutes)

*You can skip this and use SQLite — but on Render's free tier the disk
is wiped on every redeploy, so history would reset. Supabase keeps it.*

1. Go to **https://supabase.com** → **Start your project** → sign up
   (GitHub login works).
2. Click **New project**.
   - Name: `campus-twin`
   - Database Password: click **Generate** and **save it somewhere**
   - Region: choose the one closest to you (e.g. Mumbai)
   - Click **Create new project** (takes ~2 min to provision).
3. When the dashboard opens, look for **Connect** (top bar). Click it.
4. In the panel that opens, find the **Session pooler** section and copy
   that connection string. It looks like:
   ```
   postgresql://postgres.abcdefgh:[YOUR-PASSWORD]@aws-0-ap-south-1.pooler.supabase.com:5432/postgres
   ```
5. Replace `[YOUR-PASSWORD]` in it with the database password you saved
   in step 2. **This full string is your `DATABASE_URL`.** Keep it handy
   for Part 2 — and treat it like a password (never commit it).

> ✅ No need to create any tables! The app creates its own
> `occupancy_history` table automatically on first boot.

---

## PART 2 — Render app (~5 minutes)

1. Go to **https://render.com** → **Get Started** → sign in **with
   GitHub** (choose the `KAMUUZ-git` account that owns `Code_hiest`).
2. Authorize Render to see your repos when asked.
3. On the dashboard click **New +** → **Web Service**.
4. If your repo is listed, click **Connect** next to `Code_hiest`.
   (If it's not listed: "Configure account" → select the repo → Save.)
5. Fill the form like this (Render's blueprint may pre-fill some — the
   important ones are):
   - **Name:** `campus-digital-twin` (this becomes your URL)
   - **Region:** Singapore (closest free region to India)
   - **Branch:** `main`
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:**
     ```
     uvicorn backend.app:app --host 0.0.0.0 --port $PORT --workers 1
     ```
6. Scroll to **Environment Variables** → click **Add Environment
   Variable**:
   - Key: `DATABASE_URL`
   - Value: *paste the Supabase string from Part 1*
   > Skip this if you skipped Supabase — the app falls back to SQLite.
7. Choose the **Free** instance type → click **Deploy Web Service**.
8. Watch the logs — first build takes ~3-5 minutes. Wait for
   **"Your service is live"** 🎉
9. Your dashboard is now at:
   ```
   https://campus-digital-twin.onrender.com
   ```

**Sanity checks (30 seconds):**
- Open the URL → the dark dashboard loads, map centres on IIT Delhi,
  buildings start ticking within a few seconds.
- Open `https://…/api/health` → `{"status": "online", …}`.
- Click a building → the Chart.js history graph fills in live.

---

## PART 3 — Every-day usage & gotchas

- 😴 **Sleeping:** free Render services sleep after ~15 min idle. The
  next visit takes ~30-60 s to wake (the page will hang briefly, then
  load — that's the wake-up, not a bug). Keep it awake during a demo by
  leaving the dashboard open, or ping `/api/health` with `uptime-monitor.com` (free).
- 🔁 **Redeploys:** every `git push` to `main` auto-deploys. With
  Supabase set, history survives; without it, history resets (fine for
  demos).
- 🔒 **Secrets:** never paste the Supabase URL with password into any
  file. Only into Render's dashboard (or Supabase's own dashboard).
- 📈 **Sharing:** the URL is public — judges can open it on their
  phones during the demo. The ⚡ Chaos button works from their browser
  too (one shared campus — that's the fun part).

---

## Cost: ₹0 forever (demo scale)

- Render free web service: $0
- Supabase free Postgres (500 MB — years of telemetry at our tick rate): $0
- Custom domain later: optional, ~$12/yr (not needed to demo)

Ship it. 🚢
