# Referral Tier Dashboard — Project Context & Build History

> **What this file is:** A complete record of everything built, decided, and
> learned while extending the Referral Tier dashboard in one long Claude Code
> session — starting from `CLAUDE_CODE_BRIEF.md` (the original spec) through
> several rounds of iteration on the core methodology. Give this file to a
> fresh Claude Code session (alongside the repo) to pick up with full context,
> without needing the original chat history.
>
> **Read `CLAUDE_CODE_BRIEF.md` first** — it has the business context (tier
> taxonomy, SQL rules, timezone handling) that this file assumes you already
> know. This file picks up from "the brief is implemented" and documents
> everything that changed after that, especially the parts that changed
> **more than once** — so you don't repeat the same dead ends.

---

## 1. Where this lives

- **GitHub repo:** `https://github.com/yashk-SSE/Referral-Tier` (public, GitHub
  Pages serves the dashboard from `main` branch root)
- **Local working copy:** `C:\Referral Tiers` — this is where all edits get
  made and tested. **It is not a git repo itself.**
- **Git clone for pushing:** `C:\Users\Yash Kahndelwal\Referral-Tier-repo` —
  a separate clone of the actual GitHub repo, used only to push changes.
  Workflow: edit files in `C:\Referral Tiers` → copy the changed files into
  the clone → `git add` / `commit` / `push` from the clone. See Section 8,
  step 7.
- **Local preview:** double-click `preview.bat` in `C:\Referral Tiers` (or run
  `preview.ps1`) — starts `python -m http.server 8080` and opens the browser
  automatically, so changes can be reviewed before pushing.

## 2. Architecture

```
Metabase (3 saved SQL questions)
        │
        ▼
   etl.py  ──writes──►  data/tier_mom.json
                        data/tier_sseid.json
                        data/tier_heldbase_sseid.json
                        data/meta.json
        │
        ▼
  index.html  (static, Chart.js, fetches data/*.json client-side)
        │
        ▼
  GitHub Pages (auto-deploys on push to main)

GitHub Actions (.github/workflows/etl.yml) runs etl.py twice daily on a
schedule, auto-commits data/*.json if changed. Can also be triggered manually
from the Actions tab.
```

### The three Metabase questions (SQL is in `SETUP.md`)

| Question | Env var | Card ID | Purpose |
|---|---|---|---|
| Aggregated | `METABASE_AGG_CARD_ID` | `3262` | cluster × tier × count, one `end_date` param, called once per month in the window |
| SSEID Detail | `METABASE_SSEID_CARD_ID` | `3263` | one row per SSEID (current month only), one `end_date` param |
| Held-Base Movement | `METABASE_HELDBASE_CARD_ID` | `4467` | SSEID-level, **two** params `prev_end`/`curr_end` — fixes the asset base to `prev_end`, computes tier **and** leads/orders at both dates for that same fixed SSEID set |

### Auth

`etl.py` prefers `METABASE_API_KEY` (header `x-api-key`) over
`METABASE_USERNAME`/`METABASE_PASSWORD` (session login) — checks the API key
first, falls back to username/password only if it's blank. `.env` (gitignored,
never committed) holds the real values locally; GitHub Secrets holds them for
the Action.

### Known infra gotchas already hit and fixed

- **Windows console encoding**: `etl.py`'s `print()` statements use ✓/⚠
  characters that crash on Windows' default `cp1252` console. Fixed with
  `sys.stdout.reconfigure(encoding="utf-8")` near the top of the script — if
  you ever see `UnicodeEncodeError` running it locally on Windows, that's why.
- **Stray misplaced files**: at one point files got uploaded to the repo
  **root** via the GitHub website instead of into `data/` and
  `.github/workflows/` — GitHub Pages/Actions silently ignored the
  misplaced copies while the real (stale) ones stayed live. If something
  seems out of date after a manual GitHub-website upload, check for
  duplicate files sitting at the wrong path.
- **Scheduled Action races a local push**: once, the scheduled Action ran
  with an older `etl.py` in between local edits and a push, producing data
  inconsistent with the newly-pushed code. Always `git pull` in the clone
  right before copying files over, and sanity-check the pulled data's
  shape matches what the new code expects before pushing.

## 3. The methodology journey — READ THIS BEFORE TOUCHING %-SHARE LOGIC

This is the part that changed the most times. Section 4.1 of the original
brief asked for a "held-base" calculation to isolate real tier movement from
base growth. Getting the exact right version took **four iterations**:

### V1 — Rolling pairwise (first attempt)
For month M, base = SSEIDs commissioned by M−1's end; tier computed at both
M−1 (baseline) and M (current) for that same set. Straightforward reading of
the brief.

**Problem hit:** displaying this as consecutive table columns invited users
to naively subtract one column's displayed % from the next column's — but
each column used a *different* (rolling) base, so the subtraction gave a
wildly wrong-looking number (e.g. "Mar→Apr looked like +1.4pp" when a naive
read of the two displayed numbers suggested +0.3pp).

### V2 — Single anchor (Feb, the earliest month in the window)
To make columns directly subtractable, every month's base got fixed to one
single anchor point (the first month in the loaded window), not rolled
forward. This *did* make adjacent columns subtractable (same shared
denominator throughout) — but it meant "current month" data was really about
an aging, shrinking-relative-to-total slice of the book, and didn't match
the intuitive "compare this month to last actual month" mental model.

### V3 — "Standardized ratio" (briefly tried, later reverted)
Simplified to: `share = this month's full tier count ÷ prior month's plain
total` (no cohort restriction on the numerator). Simpler to compute, but
numerator and denominator were different-sized populations, so %'s didn't
sum to 100% within a month, and it didn't actually match what the user meant
by "held base" (re-read the brief's own worked example — it's cohort-based,
not a plain ratio).

### V4 — True rolling cohort (CURRENT, final answer)
Reverted to V1's rolling design, but this is the version that actually
stuck, because the earlier complaint (adjacent columns not subtractable) was
resolved differently: **stop trying to make separate cohorts directly
comparable to each other at all** — instead, show each column's own
baseline→current pair explicitly (not hidden), and split "new vs existing"
customers into **separate tables** so nothing needs a fragile cross-column
subtraction. See Section 3.1.

**This is the current, correct, final methodology. Do not revert to V2 or
V3 without a very good reason and explicit user sign-off.**

### 3.1 — The core rule, in plain language

> **"Current month" reporting never includes SSEIDs commissioned during that
> same month."**

For month M:
- **Cohort** = every SSEID commissioned by the END of month M−1. Fixed size —
  does not grow during M.
- **Baseline** = that cohort's tier, computed as of M−1's end. (This always
  exactly equals month M−1's own plain tier breakdown — verified
  arithmetically multiple times.)
- **Current** = that *same* cohort's tier, computed as of M's end (re-tiered,
  picking up whatever leads/orders they gave during M).
- SSEIDs commissioned **during** M are excluded entirely from M's tier
  reporting. They get folded in starting the *following* month, once M
  itself becomes "the prior month" for a new cohort.
- Because numerator and denominator are always the exact same fixed
  population, %-share always sums to 100% within one column, and
  `pp = current% − baseline%` is always internally consistent for that one
  column.
- **Columns still cannot be subtracted from each other** — Mar's column and
  Apr's column track *different* cohorts (Apr's cohort is bigger, since it
  includes everyone added during March). This is expected and correct, not a
  bug. See Section 4 for how the UI now handles this without confusion.

### 3.2 — `etl.py`'s role

The held-base fetch loop in `etl.py` calls the Held-Base Metabase card once
per **consecutive** month pair in the window (`prev_mo = months[i-1]`,
**rolling**, not a fixed anchor). This writes, per month, into
`data/tier_mom.json`'s `held_base` field: `base_size`, `by_tier_prev`,
`by_tier_curr`, and the same broken out `by_cluster_tier_prev`/`_curr` for
per-city slicing. The full SSEID-level detail (leads/orders at both dates,
not just tier) is written **only for the latest pair** to
`data/tier_heldbase_sseid.json` (~47k rows, ~10MB) — historical per-SSEID
detail for older months is not persisted, only the aggregated tier counts
are.

## 4. Current dashboard structure (`index.html`)

Sidebar + tab layout (not a single scrolling page). `TABS` array near the top
of the `<script>` block is the single place to add future tabs.

### Executive Summary
- Metrics: **Total assets** (= latest cohort's `base_size`, NOT the true
  current full count), **Engaged (non-Sticks)**, **Metals**, **New to base**
  (SSEIDs commissioned this month, deliberately excluded from tier %'s,
  shown here instead of silently dropped — the brief's own guidance).
- **Tier breakdown** table: Count/Share for the latest cohort's *current*
  tier (as of the latest month). This is genuinely the "current tier state"
  — it can differ from what the Trends tab's "Existing customers" table
  shows as that same cohort's *baseline*, because some SSEIDs moved up
  during the month (see the "Moved up" reconciliation in the Tier-wise
  progress table).

### Trends & MoM
Five cards, **read top to bottom in this order** — each answers a different
question and none of them should be subtracted from each other across
months:

1. **Month-over-month tier distribution** (stacked bar) — each bar = the
   cohort commissioned by the prior month's end, re-tiered as of that
   month. Segments sum to 100% of that cohort.
2. **1. Cumulative — all customers till date** — plain tier %/count of
   *everyone* commissioned by each month's end, no cohort restriction. pp
   here is a genuinely simple month-to-month subtraction of the plain %'s
   (this table intentionally does NOT try to isolate new-signup dilution —
   tables 2 and 3 do that instead). This is the "headline" number most
   people will look at first.
3. **2. New customers this month** — just the SSEIDs commissioned *during*
   that month, tiered as of that same month's end. Derived arithmetically
   as `(that month's plain tier count) − (pre-existing cohort's current
   tier count)` — verified to reconcile exactly, no separate query needed.
   % is share of that month's own new-customer total, not the whole base.
4. **3. Existing customers — tracked from the prior month** — the V4 cohort
   table itself (baseline→current per cell, cohort size shown per column
   header so it's visually obvious columns are different-sized groups).
   Plus an **Existing-customer tier trend** line chart right after it.
5. **Tier-wise progress this month** — from the SSEID-level held-base
   detail file (`tier_heldbase_sseid.json`), grouped by baseline tier
   (`tier_prev`): shows leads/orders given so far this month by each
   baseline tier's cohort (e.g. "of June's 22,511 Sticks, how many
   leads/orders have they given in July"), plus **Still/Moved up**
   reconciliation columns tying this back to Executive Summary's current
   tier counts.

**Reconciliation identity worth remembering** (all three of tables 2/3/Exec
Summary tie together exactly):
```
Executive Summary's current Sticks count (existing only)
  + Table 2's new-customer Sticks count (this month)
  = Table "1. Cumulative"'s latest-month Sticks count (everyone)
```

### City Summary
Same ideas, sliced by city: City summary table + India rollup row, City tier
trend chart (respects the topbar city filter), City breakdown visual grid,
and City-wise progress this month table (leads/orders given, by city).

### SSEID Detail
Plain searchable/filterable table over `tier_sseid.json` (current month,
one row per SSEID) — unchanged since early in the build, no cohort
complexity here.

### Global controls (topbar, apply across all tabs)
- **6-Tier / Sticks-Stones-Metals toggle** — `Metals` = Bronze+Silver+Gold+
  Platinum summed. `bucketize()`/`activeTiers()`/`activeColor()` are the
  shared helpers every section routes through.
- **City filter** and **Tier filter** dropdowns.
- **Export CSV** button (raw SSEID rows, respects filters).

## 5. Design decisions

- **Brand colors**: navy `#131ca2`, blue `#00bdff` (sampled from the actual
  logo file), gold `#FFC100` — applied to UI chrome (buttons, active toggle
  state, focus rings, spinner) via CSS variables `--accent`/`--accent-hover`,
  and `--brand-gold` doubles as the "Metals" bucket color.
- **Tier colors**: mostly exactly per the brief (Platinum `#c084fc`, Gold
  `#f59e0b`, Bronze `#b87333` unchanged). **Silver/Stones/Sticks were
  changed from the brief's originals** because all three were similar
  slate-grays and hard to tell apart — this change is **display-only**, in
  `index.html`'s JS `TIER_COLORS` object and CSS vars. `etl.py`'s own
  `TIER_COLORS` (written into `data/*.json` meta, unused by the frontend for
  rendering) still has the brief's canonical values, deliberately left
  alone.
- **Metals badge text**: `#FFC100` is too light for white text — Metals
  badges/legend use dark text (`badgeTextColor()` helper), every other tier
  uses white.
- **Light theme forced** — removed the `prefers-color-scheme:dark` media
  query entirely; the dashboard is always light regardless of OS setting.
- **Logo**: `sse-logo.png` (copied from user-provided `SSE Logo.png`, renamed
  to remove the space) — shown top-left in the topbar and as the favicon.
- **Color semantics in tables** (`pp-inc`/`pp-dec`/`pp-flat` CSS classes):
  color is **direction-only** (green=grew, red=shrank), completely separate
  from whether that direction is favorable for a given tier. A `favorability()`
  helper judges good/bad per tier (Sticks/Stones shrinking = good; everything
  else growing = good) and that judgment only appears in hover tooltips, never
  as the color itself — this split was made after color+direction being
  conflated caused real confusion (a red "up" arrow for Sticks reads as
  "bad" instinctively even when growing Sticks is in fact bad, which
  coincidentally lines up — but for Stones a red-for-up would have been
  backwards under the old combined scheme).

## 6. Things that look like bugs but aren't

- **Tiny (~0.1pp) pp discrepancies**: JavaScript floating-point rounding at
  exact `.5` boundaries (e.g. `0.35` can store as `0.34999...` and
  `toFixed(1)` rounds down to `0.3` instead of `0.4`). Not a logic error.
- **Same displayed baseline number in two adjacent columns** (e.g. both
  showing "48.0→...") — coincidental rounding of two genuinely different
  underlying values (e.g. 47.98% and 48.00% both round to 48.0% at one
  decimal place). Check the unrounded data before assuming it's a bug.
- **Cumulative table's monthly numbers ≠ Executive Summary's numbers** —
  expected; they're different populations and/or different dates by
  design. See the reconciliation identity in Section 4.

## 7. Possible next steps (not yet built)

- A short reconciliation footnote directly under the "1. Cumulative" table
  (e.g. "Jul'26 = Existing + New, see tables 2 & 3") was offered but not
  yet implemented — worth adding if the same question keeps coming up.
- Historical per-SSEID leads/orders detail (`tier_heldbase_sseid.json`) is
  only kept for the latest month pair — if "Tier-wise progress" needs to
  show a trend across multiple months instead of just "this month," `etl.py`
  would need to persist this detail every run instead of overwriting it.
- No automated tests exist — every verification in this build was done by
  spinning up a local static server and checking real numbers by hand
  (see Section 8).

## 8. How to verify changes (what's actually been done every time so far)

1. Edit `index.html` / `etl.py` in `C:\Referral Tiers`.
2. If `etl.py` changed, re-run it locally (`.env` has real credentials) to
   regenerate `data/*.json` before checking the frontend.
3. Serve locally (`preview.bat`, or `python -m http.server 8080` manually)
   and open in a browser.
4. Cross-check displayed numbers against the raw JSON by hand (e.g.
   `python -c "import json; ..."` one-liners) — this caught every real
   issue in this build; don't skip it.
5. Check browser console for JS errors.
6. Test both toggle modes (6-Tier / Metals) and with a city/tier filter
   applied — several bugs only showed up under a specific filter combo.
7. Only then copy changed files into the separate git clone and push
   (Section 1) — always `git pull` in the clone first.
