# Referral Tier Dashboard — Setup Guide

Complete step-by-step instructions to go from zero to a live,
auto-refreshing dashboard in about 30 minutes.

---

## What you'll end up with

```
Your Metabase  →  etl.py (GitHub Actions)  →  data/*.json  →  index.html (GitHub Pages)
```

- Dashboard auto-refreshes twice daily (configurable)
- Manually trigger a refresh anytime from GitHub → Actions
- All data files committed to your repo — full history preserved
- Share the GitHub Pages URL with anyone

---

## Step 1 — Create two Metabase saved questions

This is the most important step. The ETL calls these questions by ID.

### Question A — Aggregated (cluster × tier × count)

1. Go to Metabase → New → SQL query
2. Select your database
3. Paste the SQL below exactly:

```sql
WITH
asset_base AS (
  SELECT
    p."sseid",
    p."prospectId",
    p."site_address_cluster" AS cluster
  FROM public.project p
  WHERE p."commissioning_date" IS NOT NULL
    AND (CAST(p."commissioning_date" AS timestamp)
         AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date <= {{end_date}}
),
referral_counts AS (
  SELECT
    r."referredBy" AS referrer_prospectid,
    COUNT(*) AS lead_count,
    SUM(CASE
      WHEN l.max_order_date IS NOT NULL
        AND l.max_order_date <= {{end_date}}
      THEN 1 ELSE 0
    END) AS order_count
  FROM public.referrals r
  LEFT JOIN (
    SELECT
      "prospectId",
      MAX((CAST("order_closure_datetime" AS timestamp)
           AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date) AS max_order_date
    FROM public.lead
    GROUP BY "prospectId"
  ) l ON r."prospectId" = l."prospectId"
  WHERE (CAST(r."createdAt" AS timestamp)
         AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date <= {{end_date}}
    AND (r."type" IN ('existing customer', 'sse employee', 'solar square')
         OR r."type" IS NULL)
  GROUP BY r."referredBy"
),
with_tier AS (
  SELECT
    a.cluster,
    a.sseid,
    CASE
      WHEN COALESCE(rc.order_count, 0) >= 12 THEN 'Platinum'
      WHEN COALESCE(rc.order_count, 0) >= 7  THEN 'Gold'
      WHEN COALESCE(rc.order_count, 0) >= 3  THEN 'Silver'
      WHEN COALESCE(rc.order_count, 0) >= 1  THEN 'Bronze'
      WHEN COALESCE(rc.lead_count,   0) >= 1 THEN 'Stones'
      ELSE 'Sticks'
    END AS tier
  FROM asset_base a
  LEFT JOIN referral_counts rc ON rc.referrer_prospectid = a."prospectId"
)
SELECT cluster, tier, COUNT(*) AS cnt
FROM with_tier
GROUP BY cluster, tier
ORDER BY cluster, tier
```

4. Before saving, click the **variable** icon (the `{{ }}` button in the query editor).
   - You should see `end_date` appear as a variable.
   - Set its type to **Date** and give it a default value like `2026-04-30`.
5. Click **Save** → name it **"Referral Tier — Aggregated"**
6. After saving, look at the URL: `metabase.com/question/2345-referral-tier`
   → **Write down the number (2345 in this example)** — this is your AGG_CARD_ID

### Question B — SSEID detail (one row per project)

1. New → SQL query → same database
2. Paste:

```sql
WITH
asset_base AS (
  SELECT
    p."sseid",
    p."prospectId",
    p."site_address_cluster" AS cluster
  FROM public.project p
  WHERE p."commissioning_date" IS NOT NULL
    AND (CAST(p."commissioning_date" AS timestamp)
         AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date <= {{end_date}}
),
referral_counts AS (
  SELECT
    r."referredBy" AS referrer_prospectid,
    COUNT(*) AS lead_count,
    SUM(CASE
      WHEN l.max_order_date IS NOT NULL
        AND l.max_order_date <= {{end_date}}
      THEN 1 ELSE 0
    END) AS order_count
  FROM public.referrals r
  LEFT JOIN (
    SELECT
      "prospectId",
      MAX((CAST("order_closure_datetime" AS timestamp)
           AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date) AS max_order_date
    FROM public.lead
    GROUP BY "prospectId"
  ) l ON r."prospectId" = l."prospectId"
  WHERE (CAST(r."createdAt" AS timestamp)
         AT TIME ZONE 'UTC' AT TIME ZONE 'Asia/Kolkata')::date <= {{end_date}}
    AND (r."type" IN ('existing customer', 'sse employee', 'solar square')
         OR r."type" IS NULL)
  GROUP BY r."referredBy"
)
SELECT
  a."sseid"                        AS "SSEID",
  a.cluster                        AS "City",
  COALESCE(rc.lead_count,   0)     AS "Leads Given",
  COALESCE(rc.order_count,  0)     AS "Orders Given",
  CASE
    WHEN COALESCE(rc.order_count, 0) >= 12 THEN 'Platinum'
    WHEN COALESCE(rc.order_count, 0) >= 7  THEN 'Gold'
    WHEN COALESCE(rc.order_count, 0) >= 3  THEN 'Silver'
    WHEN COALESCE(rc.order_count, 0) >= 1  THEN 'Bronze'
    WHEN COALESCE(rc.lead_count,  0) >= 1  THEN 'Stones'
    ELSE 'Sticks'
  END                              AS "Tier"
FROM asset_base a
LEFT JOIN referral_counts rc ON rc.referrer_prospectid = a."prospectId"
ORDER BY "Orders Given" DESC NULLS LAST, "Leads Given" DESC NULLS LAST
```

3. Set the `end_date` variable type to **Date** (same as above)
4. Save → name it **"Referral Tier — SSEID Detail"**
5. Note the card ID from the URL → this is your SSEID_CARD_ID

---

## Step 2 — Create a GitHub repository

1. Go to github.com → New repository
2. Name it `referral-dashboard` (or anything you like)
3. Set it to **Public** if you want GitHub Pages for free; Private also works
4. Don't add any template files — leave it empty

---

## Step 3 — Push the project files

The files in this folder map directly to the repo root:

```
referral-dashboard/
├── .github/
│   └── workflows/
│       └── etl.yml          ← GitHub Actions pipeline
├── data/                    ← Created by ETL, committed automatically
├── .env.example             ← Template for local dev
├── .gitignore
├── etl.py                   ← ETL script
├── index.html               ← Dashboard (GitHub Pages entry point)
├── requirements.txt
└── SETUP.md                 ← This file
```

In your terminal:

```bash
cd referral-dashboard

# Initialise git (if not already done)
git init
git branch -M main

# Add your GitHub repo as remote
git remote add origin https://github.com/YOUR_USERNAME/referral-dashboard.git

# First push
git add .
git commit -m "init: referral tier dashboard"
git push -u origin main
```

---

## Step 4 — Add GitHub Secrets

These replace your .env file in production. Secrets are encrypted and
never visible in logs.

1. Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each of the following:

| Secret name              | Value                                      |
|-------------------------|--------------------------------------------|
| `METABASE_URL`          | `https://your.metabase.com` (no trailing /) |
| `METABASE_USERNAME`     | your Metabase email                        |
| `METABASE_PASSWORD`     | your Metabase password                     |
| `METABASE_AGG_CARD_ID`  | the number from Step 1 Question A          |
| `METABASE_SSEID_CARD_ID`| the number from Step 1 Question B          |

---

## Step 5 — Run the ETL for the first time

1. Go to your repo → **Actions** tab
2. Click **Referral Tier ETL** in the left sidebar
3. Click **Run workflow** → keep defaults → click the green **Run workflow** button
4. Watch the run — click on it to see live logs
5. After it completes (~1–5 minutes depending on data size), you'll see:
   - A new commit: `data: refresh 2026-05-22T... [skip ci]`
   - Three new files in `data/`: `tier_mom.json`, `tier_sseid.json`, `meta.json`

If it fails, click the failed step to read the error message — most common
issues are wrong card IDs or a Metabase URL with a trailing slash.

---

## Step 6 — Enable GitHub Pages (to share the dashboard)

1. Go to repo → **Settings** → **Pages**
2. Under Source, select **Deploy from a branch**
3. Branch: `main`, folder: `/ (root)`
4. Click **Save**
5. After ~1 minute your dashboard is live at:
   `https://YOUR_USERNAME.github.io/referral-dashboard/`

The dashboard reads `data/*.json` relative to itself — no server needed,
everything is static files.

---

## Changing the schedule

Edit `.github/workflows/etl.yml` and change the cron lines.
Current schedule: 23:00 UTC (= 4:30 AM IST) and 05:00 UTC (= 10:30 AM IST).

Cron format: `minute hour day month weekday`

Examples:
```yaml
# Once a day at 8 AM IST (2:30 UTC)
- cron: '30 2 * * *'

# Three times a day: 7 AM, 1 PM, 6 PM IST
- cron: '30 1,7,12 * * *'

# Weekdays only, 9 AM IST
- cron: '30 3 * * 1-5'
```

---

## Running locally (for testing)

```bash
# 1. Clone the repo
git clone https://github.com/YOUR_USERNAME/referral-dashboard.git
cd referral-dashboard

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up your .env
cp .env.example .env
# Edit .env with your real values

# 5. Run the ETL
python etl.py

# 6. Preview the dashboard
python -m http.server 8080
# Open http://localhost:8080 in your browser
```

Note: You can't just open index.html by double-clicking — browsers block
`fetch()` on local files. Always use a local server (step 6 above).

---

## Changing the number of months

Option A — permanent change: edit `MONTHS=6` in `.env` (local) or the
`MONTHS` default in `etl.yml`.

Option B — one-off: when triggering manually from GitHub Actions,
enter a different number in the "months" input field.

---

## Troubleshooting

**"Failed to load data" on the dashboard**
→ The ETL hasn't run yet or the data/ folder is empty. Run the ETL first.
→ If local, make sure you're serving via `python -m http.server`, not file://

**ETL error: 400 Bad Request on the card fetch**
→ The saved question doesn't have an `{{end_date}}` template variable.
   Re-check Step 1 — the `{{ }}` must be in the SQL, not a filter.

**ETL error: 401 Unauthorized**
→ Wrong METABASE_USERNAME or METABASE_PASSWORD in Secrets.

**ETL error: 404 Not Found on card fetch**
→ Wrong card ID. Double-check the number in the Metabase URL.

**Tier totals don't match asset base count**
→ Run the diagnostic query from the knowledge base (Section 8) in Metabase
   directly to verify. Usually caused by NULL cluster values.

**Dashboard shows 0 for a city I know has data**
→ Check for spelling variants in `site_address_cluster` using the
   cluster diagnostic query in Section 8 of the knowledge base.
