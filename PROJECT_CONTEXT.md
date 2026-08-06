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
  the clone → `git add` / `commit` / `push` from the clone. See Section 9,
  step 7.
- **Local preview:** double-click `preview.bat` in `C:\Referral Tiers` (or run
  `preview.ps1`) — starts `python -m http.server 8080` and opens the browser
  automatically, so changes can be reviewed before pushing.

## 2. Architecture

```
Metabase (4 saved SQL questions)
        │
        ▼
   etl.py  ──writes──►  data/tier_mom.json
                        data/tier_sseid.json
                        data/tier_heldbase_sseid.json
                        data/tier_mtd.json
                        data/cohort_activation.json
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

### The four Metabase questions (SQL is in `SETUP.md`; Cohort Activation's SQL is in this file, Section 7)

| Question | Env var | Card ID | Purpose |
|---|---|---|---|
| Aggregated | `METABASE_AGG_CARD_ID` | `3262` | cluster × tier × count, one `end_date` param, called once per month in the window |
| SSEID Detail | `METABASE_SSEID_CARD_ID` | `3263` | one row per SSEID (current month only), one `end_date` param |
| Held-Base Movement | `METABASE_HELDBASE_CARD_ID` | `4467` | SSEID-level, **two** params `prev_end`/`curr_end` — fixes the asset base to `prev_end`, computes tier **and** leads/orders at both dates for that same fixed SSEID set |
| Cohort Activation | `METABASE_COHORT_CARD_ID` | `4570` | fiscal-quarter cohort × trailing-6-month grid, **one** param `as_of_date` — computes everything (tier-at-month-start, activation, repeat-activation, prior-month tier) server-side in one call. See Section 7. |

This same Held-Base card is now called **one extra time per run** — with
`curr_end = today` instead of a month-end — to produce the month-to-date
preview (`data/tier_mtd.json`, see Section 3.3). No new Metabase question was
needed for this; it's the same card, different date params.

**GitHub Secrets checklist** — `METABASE_COHORT_CARD_ID` was added to local
`.env`/`env.example` on 2026-08-04 but as of this writing it's *unconfirmed*
whether it's been added to the repo's GitHub Secrets too (Settings → Secrets
and variables → Actions). Until it is, the scheduled Action's `etl.py` run
will skip the cohort fetch and write an empty placeholder
`cohort_activation.json` (graceful — the tab just shows "no data available"
— but check this if the Cohort Activation tab looks empty after a
scheduled/automated run despite working locally).

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

**Scope note added 2026-08-04**: V4 is still exactly this, unchanged, for
its intended purpose — isolating real tier movement from base growth in a
month-over-month *comparison* (Trends & MoM's tables, held-base movement).
What changed on 2026-08-04 is narrower: Executive Summary and City
Summary's *headline, current-state* numbers stopped using this cohort and
switched to the full current total instead, because "how big is our book
right now" and "how did our existing book move this month" turned out to
be two different questions that don't need the same answer. See Section 4's
Executive Summary entry for the full reasoning — this is additive
clarification, not a reversal of V4.

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

### 3.3 — Never report the in-progress month (fixed 2026-08-03)

**The bug:** `build_month_range()`'s default window used to anchor on
`current_month_ist()` — the calendar month "today" falls in — with
`end_date` set to that month's *last* day regardless of what day it actually
was. Since the ETL runs twice daily, this meant the newest column was
routinely labeled e.g. "Jul '26" while actually only containing data up to
whatever day the ETL happened to run (Metabase can't return rows for a date
that hasn't happened yet). The number kept silently growing every run through
the month and then jumped again at month-end — a moving target mislabeled as
a finished snapshot. Caught by comparing `meta.json`'s `generated_at`
(2026-07-28) against its own `latest_end_date` (2026-07-31) — a future date
relative to the run.

**The fix:** `last_completed_month_ist()` — the default anchor is now always
one calendar month behind `current_month_ist()`, full stop, regardless of
what day of the month it is. A "Jul '26" column cannot exist until Aug 1.
Verified across a real month rollover: ETL run on 2026-07-31 anchored on
Jun '26 (as expected, July hadn't ended yet); the same code run again on
2026-08-03 correctly rolled forward to Jul '26. `END_MONTH` env var override
still works unchanged (for manually testing a specific past month).

**The month-to-date preview (new):** the in-progress month isn't just
dropped — it's tracked live, separately, in `data/tier_mtd.json`. One extra
Held-Base fetch with `prev_end = last completed month's end`, `curr_end =
today`, plus one extra Aggregated-card fetch at `end_date = today` (to derive
`new_to_base` as `today's total − mtd base_size`). Shape:
```
{ "mtd": {
    "as_of_date", "baseline_end_date", "baseline_label",
    "held_base": { "base_size", "by_tier_prev", "by_tier_curr",
                    "by_cluster_tier_prev", "by_cluster_tier_curr", "movement_pp" },
    "leads_given", "orders_given", "new_to_base"
} }
```
`leads_given`/`orders_given`/`new_to_base` are **India-wide only** — they're
summed from raw rows in `etl.py`, not broken out by cluster (the
`by_cluster_tier_*` fields inside `held_base` ARE per-city, but only cover
tier *counts*, not leads/orders sums). See Section 8 if city-level MTD
becomes a real ask.

## 4. Current dashboard structure (`index.html`)

Sidebar + tab layout (not a single scrolling page). `TABS` array near the top
of the `<script>` block is the single place to add future tabs.

### Executive Summary

**Changed 2026-08-04 — Total assets is now the FULL current total, not the
lagged cohort.** Originally, "Total assets" showed the held-base cohort's
`base_size` (commissioned by the month BEFORE the latest, e.g. 46,902 for
"Jun-commissioned, tracked to Jul"), holding that month's new commissions
out for a full extra month before folding them in. The user pushed back on
this directly: the "hold new commissions out" rule exists to stop a
still-forming, partial month from being shown as final (Section 3.3) — but
once that month is actually CLOSED, there's no more instability to protect
against, so there's no real reason to keep holding its new adds out any
further. Confirmed explicitly (with scope): change **Executive Summary and
City Summary's current-state numbers** to the full total; leave the
**Trends & MoM comparison tables exactly as they were** (they need a
stable, unchanging population to compare against itself validly, which is a
different requirement than "what's the current picture right now").

- Metrics: **Total assets** (`fullTotal` — every SSEID commissioned through
  the latest closed month, e.g. 51,032 for Jul '26 — now identical to Table
  1 "Cumulative"'s latest column), **Engaged (non-Sticks)**, **Metals**. The
  old **"New to base"** card was removed — there's no more held-out group
  for the just-closed month to report on; the *in-progress* month's new
  commissions are still covered by the "This month so far" card below.
- **"This month so far" card** (added 2026-08-03, sourced from
  `data/tier_mtd.json`): leads/orders given and new-to-base count for the
  *currently in-progress* month, tracked live to today. Visually flagged
  `IN PROGRESS` (`.mtd-card` CSS, accent left-border) and explicitly worded
  as "not a finalized column" so it never gets confused with the tier tables
  below it, which only ever show completed months. If a city filter is
  active, this card says so and stays India-wide (see Section 3.3 — MTD
  leads/orders aren't split by city yet).
- **Tier breakdown** table: Count/Share for the FULL current total (same
  `fullTierB` as the metrics above), not the cohort. Now matches Trends &
  MoM's "1. Cumulative" latest column exactly, by construction.
- The held-base cohort concept itself is **not removed** — `latestCohort`/
  `total`/`active`/`metalsCount`/`latestCurrB` (the old, cohort-restricted
  versions) are still computed and still power Trends & MoM's comparison
  tables and the reconciliation footnote, completely unchanged. Two parallel
  sets of similarly-named variables now coexist in `renderAll()` on purpose
  — `full*` (Exec Summary/City Summary, this change) vs. the un-prefixed
  ones (Trends & MoM, untouched). Don't merge them.

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

**Redesign pass (2026-08-03) — tables 1–3 specifically:**
- **% share / Absolute count toggle** (`numberMode`, shared `#numToggle`
  control above table 1) — switches every cell in tables 1–3 between showing
  %/pp or raw count/delta, instead of cramming both into every cell. Default
  `"pct"`.
- **Column headers now show the population size** for that column (`.th-sub`
  CSS class) — e.g. table 1's "Jun '26" header also shows "46,902 total",
  table 2's shows "3,835 new", table 3's shows "43,067 SSEIDs" — so you don't
  have to hunt in the card subtitle to know what a column actually contains.
- **Card subtitles rewritten in plainer language** — the original wording was
  dense/methodology-heavy; kept the substance, cut the jargon.
- **Sanity-check footnote** under table 3 (`#reconNote`), two checks:
  1. City-level tier counts (summed from `by_cluster_tier_curr`) vs. the
     national total (`by_tier_curr`) — genuinely independent aggregates from
     `etl.py`, so a real mismatch here would mean something's actually wrong
     upstream, not just a display quirk.
  2. The existing+new=cumulative identity below, spelled out with real
     numbers so it's eyeball-checkable against the other cards on screen.
- **Gotcha hit and fixed while building this**: the footnote's "new this
  month" sum originally iterated the raw `TIER_ORDER` (6 fixed tier names)
  over `bucketize()`d objects. In Metals mode, a bucketized object's keys are
  `Sticks`/`Stones`/`Metals` — indexing it with `TIER_ORDER`'s `Platinum`/
  `Gold`/`Silver`/`Bronze` silently returns `undefined`→`0`, and `Metals`
  itself never gets summed at all (it's not in `TIER_ORDER`). Caught via the
  reconciliation footnote itself going wrong under Metals+city filter
  (7,760 + 818 ≠ 8,696). **Fix: always use `activeTiers()`, never
  `TIER_ORDER`, when iterating over anything that's been through
  `bucketize()`.** `TIER_ORDER` is only correct for raw, un-bucketized 6-tier
  data straight from the JSON files.

**Reconciliation identity worth remembering** (tables 2/3 tie together
exactly):
```
Table "3. Existing customers"'s current Sticks count (existing only)
  + Table 2's new-customer Sticks count (this month)
  = Table "1. Cumulative"'s latest-month Sticks count (everyone)
```
**Changed 2026-08-04**: this used to also equal Executive Summary's
headline Sticks count, back when Exec Summary showed the cohort-restricted
"existing only" number. Since Exec Summary switched to the full total
(Section 4), it now equals Table 1's number *directly* — Exec Summary and
Table 1 are the same number by construction, not something that needs
reconciling anymore. The three-way identity above still holds entirely
within Trends & MoM, unaffected. Surfaced live in the sanity-check footnote
under Table 3.

### City Summary
- **City summary table + India rollup row, City breakdown visual grid**:
  changed 2026-08-04 along with Executive Summary — now the full current
  per-city total (`latestMonth.by_cluster_tier`, the same field Table 1
  "Cumulative" uses), not the held-base cohort's per-city breakdown. The
  India row is guaranteed to equal Executive Summary's Total assets exactly,
  since they're now literally the same underlying numbers.
- **City tier trend chart**: deliberately left as the cohort-based rolling
  series (unchanged) — it's a trend/comparison view, the same category of
  thing as Trends & MoM's charts, not a current-snapshot view. Its card
  subtitle says so explicitly, since it now sits next to full-total siblings
  and the difference needs calling out.
- **City-wise progress this month table**: unchanged (leads/orders given,
  by city, from the held-base SSEID detail) — this is a Trends & MoM-style
  comparison, not a current-snapshot metric.

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
- **`.mtd-card`** (2026-08-03): the month-to-date card uses a colored left
  border (`--accent`) plus a small pill-shaped `IN PROGRESS` badge appended
  via CSS `::after` on the card title — deliberate visual distinction so a
  live, still-changing number is never mistaken for a finalized one at a
  glance, without needing to read the subtitle text.
- **`.roomy`** (2026-08-03): a table-class modifier (bigger cell padding/
  font-size) applied to tables 1–3 only, per the ask for a "spacious, less
  cluttered" feel — other tables (Tier-wise progress, City summary, SSEID
  Detail) keep the original denser `.ttbl` sizing.
- **Abs/% toggle reuses `.seg-toggle`** (the same pill-button CSS already
  used for the 6-Tier/Metals switch) rather than introducing a second toggle
  style.

## 6. Things that look like bugs but aren't

- **Tiny (~0.1pp) pp discrepancies**: JavaScript floating-point rounding at
  exact `.5` boundaries (e.g. `0.35` can store as `0.34999...` and
  `toFixed(1)` rounds down to `0.3` instead of `0.4`). Not a logic error.
- **Same displayed baseline number in two adjacent columns** (e.g. both
  showing "48.0→...") — coincidental rounding of two genuinely different
  underlying values (e.g. 47.98% and 48.00% both round to 48.0% at one
  decimal place). Check the unrounded data before assuming it's a bug.
- **Cumulative table's *latest* column now equals Executive Summary's
  numbers exactly** (changed 2026-08-04, see Section 4) — they're the same
  full-total population by construction now. Older months in the Cumulative
  table have no Exec Summary equivalent at all (Exec Summary only ever
  shows the latest month), so there's nothing to compare there. What's
  still *intentionally* different: Trends & MoM's own cohort-based tables
  (2/3, held-base movement) — those still use the lagged cohort on purpose,
  see Section 3's scope note.
- **"This month so far" (MTD) numbers change on every single ETL run**,
  including intra-day — that's the point, it's a live preview of an
  in-progress month, not a bug. Don't expect it to match between two runs a
  few hours apart. It's only ever India-wide (see Section 3.3).
- **Local preview shows stale numbers right after re-running `etl.py`** —
  almost always the browser's HTTP cache serving an old `fetch()` response
  for `data/*.json`, not stale files on disk. See Section 9, step 3a.
- **Executive Summary says "Commissioned by Jun '26, tracked to Jul '26" on a
  day well into August** — this looks like the dashboard is stuck a month
  behind, but it isn't. "Tracked to" is the real, current, last-completed
  month (checked directly against `meta.json`'s `latest_end_date` — it *is*
  July 31 here). "Commissioned by" is one month earlier by design: it's
  naming the **cohort's own baseline**, not how stale the data is (see
  Section 3.1 — the cohort excludes SSEIDs commissioned during the tracked
  month itself, so its baseline is always the month before). Check
  `meta.json.latest_end_date` directly before assuming this metric is behind.
- **Cohort Activation: "Pool" looking smaller than a cohort's "Size", or a
  tier breakdown that "loses" people** — three different numbers that are
  easy to conflate, asked about more than once while building this:
  1. **Size** (fixed, per cohort) — the whole cohort, same in every segment.
  2. **Pool** (per cell) — only the *currently selected segment's* slice,
     as of *that column's* month-start. For a still-forming cohort (the
     current fiscal quarter), Pool in an early column can be well under
     Size simply because part of the cohort hadn't been commissioned yet —
     not a bug, same "not yet commissioned" rule as the `—` cells elsewhere.
  3. **Engaged till date** (tooltip, added 2026-08-04) — Metals share of the
     *whole* cohort as of the *latest* available data, divided by the true
     Size. Exists specifically to give a stable answer that can't be
     confused with either of the above. See Section 7.4.

## 7. Cohort Activation tab (added 2026-08-04)

A second, structurally different analysis alongside everything in Sections
3-6: instead of the monthly rolling held-base cohort, this tracks
**fiscal-quarter commissioning cohorts** (Apr-Mar, e.g. "Q4 FY24-25" =
Jan-Mar 2025) — a cohort that's fixed **forever** once its quarter ends,
never rolling — against a **trailing 6-month window** of referral
**activation** (gave ≥1 lead that specific month). Built from the reference
file `cohort_analysis_fresh (2).html` the user supplied, adapted to real
Metabase data, our real 6-tier taxonomy (the reference had no Platinum —
we added it as a 4th Metal sub-tab), and our fiscal-year quarter convention
(the reference used calendar quarters).

### 7.1 — Why a whole separate cohort concept

The held-base cohort (Sections 3-4) answers "how is the base we already had
doing." Fiscal-quarter cohorts answer a different question: "of everyone
commissioned in Q4 FY24-25, how engaged have they become, and is that
engagement still growing months or years later." Cohort membership here
never changes (unlike the rolling monthly cohort) — a customer commissioned
in Q4 FY24-25 is in that cohort permanently, and their activation can be
checked against *any* later calendar month, including ones years after
their own quarter ended.

### 7.2 — The SQL (card 4570, `METABASE_COHORT_CARD_ID`)

One query, one `{{as_of_date}}` param (same convention as `end_date`
elsewhere — always the last *completed* month's end; the in-progress month
is never shown here either, matching Section 3.3's rule). Per SSEID, per
each of the trailing 6 months:

- **Pool** = cohort members already commissioned by that month's baseline
  cutoff (end of the prior month), bucketed by their **tier as of that
  cutoff**. SSEIDs commissioned mid-window (the current, still-forming
  fiscal quarter) simply don't appear yet — same "not yet commissioned"
  handling as `—` cells elsewhere, no special-case code needed.
- **Activated** = of that Pool, how many gave ≥1 referral lead **during**
  that specific month.
- **Repeat Activated** (added after the first version) = of those Activated,
  how many *also* gave a lead in the **prior calendar month** — computed by
  widening the same per-referrer date check to a second range, no self-join
  needed (the query already has full referral history, not just the visible
  6-month window).
- **Prior Tier** (added after Repeat Activated) = the same SSEID's tier as
  of **two months before** the current baseline cutoff — computed by
  deriving a `prev_baseline_cutoff` directly (`date_trunc('month',
  baseline_cutoff) - 1 day`), again no self-join. This is what lets the
  frontend compute "who moved into Metals this month, and from which tier"
  (Section 7.4).

Both `Repeat Activated` and `Prior Tier` were added as **backward-compatible
column additions** — `etl.py`'s `normalise_cohort_row()` looks for them by
name and sets `meta.has_repeat_data` / `meta.has_prior_tier_data` to `false`
if they're absent, so the frontend degrades gracefully (those tooltip lines
just don't render) rather than showing fake zeros. Useful pattern if this
card grows again.

City is a real output dimension (not summed away), so the topbar city filter
works on this tab exactly like everywhere else.

### 7.3 — `etl.py` output (`data/cohort_activation.json`)

Flat `records` array — `{cohort, city, month, tier, pop, act, repeat_act,
prior_tier}` — plus a `meta` block with pre-sorted `cohorts` (chronological
by fiscal year/quarter, not a string sort — `Q10` would otherwise misorder)
and `months`/`moLabels`. `COHORT_MONTHS = 6` in `etl.py` is currently a
constant, not an env var like `MONTHS` — see Section 8 if that needs to
become configurable.

### 7.4 — The tooltip

Went through several rounds of clarification while building this, because
the underlying numbers are genuinely easy to conflate. Final shape, top to
bottom:

1. **Pool (this cell, `<segment>`)** — the currently-selected segment's
   population this specific month. Segment-dependent and month-dependent.
2. **Engaged till date (Metals, whole cohort)** — added 2026-08-04
   specifically to stop this being confused with #1. Metals share of the
   *entire* cohort as of the *latest available* month (same anchor as the
   Size column), divided by true cohort Size. Identical in every cell for a
   given cohort, regardless of which month/segment you're hovering —
   deliberately a stable reference point.
3. **Activated** — of Pool, how many gave a lead this month.
4. **Also active in `<prev month>`** (if `has_repeat_data`) — of Activated,
   how many repeated from last month too.
5. **Activated by tier** (if the segment blends >1 raw tier — Metals or All
   Customers) — baseline-tier breakdown of the Activated count. Can only
   ever list Bronze/Silver/Gold/Platinum for a Metals cell — Sticks/Stones
   are excluded from Metals' Pool by definition, so they're mathematically
   impossible to see here. This is *not* the same thing as tier movement —
   see #6.
6. **New to Metals this month** (Metals/Combined only, if
   `has_prior_tier_data`) — a **different metric than Activated**: net
   count who crossed from Sticks/Stones into Metals during the month,
   split by which tier they came from. Since tiers only ever move up, this
   is exactly `(this month's Metals Pool) − (last month's Metals Pool)`,
   further split by prior tier.
7. **⚠ pool < 30** — small-sample flag, same idea as the reference file's
   noise dot.

**Pool vs. Activated vs. New-to-Metals, the distinction that actually
caused confusion twice while building this**: Pool is a population snapshot.
Activated is "did they refer *this specific month*" — it says nothing about
whether their tier changed. New-to-Metals is "did their tier cross into
Metals *this specific month*" — it says nothing about whether they referred
anyone. A person can activate without moving tiers (most do), and can move
tiers without activating that same month (e.g. an order closing from a lead
given months earlier). Don't assume one implies the other.

### 7.5 — UI controls

- **Segment toggle**: Sticks / Stones / Metals (→ Bronze/Silver/Gold/
  Platinum sub-tabs) / All Customers. Reuses `.seg-toggle` styling, not a
  new component.
- **Display mode**: Rate % / Active / Pool — same three the reference file
  had.
- **Global filter interplay** (per explicit ask — "global filters should
  apply perfectly here too"):
  - **City filter**: filters the whole tab to that city — real, substantial.
  - **6-Tier/Metals topbar toggle**: in Metals mode, the Bronze/Silver/Gold/
    Platinum sub-tabs hide (this tab's own segment selector already covers
    both granularities, so the toggle just picks which one's exposed).
  - **Tier filter (topbar dropdown)**: when set to a specific tier, pins
    this tab to that tier and disables the local segment buttons (with an
    on-screen note explaining why) — so picking e.g. "Gold" anywhere shows
    Gold's cohort view here too. Clearing it returns control to the local
    buttons.
- Era rows group cohorts by **fiscal year** (not the reference's ad hoc
  "Legacy/Mature/Growing" bands, which don't map onto FY numbering) — each
  with its own rollup row, same idea as the reference's era aggregates.

## 8. Possible next steps (not yet built)

- ~~A short reconciliation footnote directly under the "1. Cumulative" table~~
  — **done 2026-08-03**, see the sanity-check footnote under table 3 in
  Section 4.
- **City-level split for the month-to-date card** — `tier_mtd.json`'s
  `held_base.by_cluster_tier_prev/curr` already has per-city tier *counts*
  for the in-progress month, but `leads_given`/`orders_given`/`new_to_base`
  are national-only scalars. If "how's Nagpur doing so far this month"
  becomes a real question, `etl.py` needs to sum leads/orders given from the
  raw MTD rows grouped by city, not just nationally.
- Historical per-SSEID leads/orders detail (`tier_heldbase_sseid.json`) is
  only kept for the latest month pair — if "Tier-wise progress" needs to
  show a trend across multiple months instead of just "this month," `etl.py`
  would need to persist this detail every run instead of overwriting it.
- No automated tests exist — every verification in this build was done by
  spinning up a local static server and checking real numbers by hand
  (see Section 9).
- **Cohort Activation's 6-month window is a hardcoded constant**
  (`COHORT_MONTHS = 6` in `etl.py`), unlike the main dashboard's `MONTHS`
  which is an env var. Make it one too if a longer/shorter trailing window
  becomes a real ask.
- **"New to Metals" breakdown only exists for the Metals/Combined segments**
  — the same prior-tier data would support a narrower "new to Gold
  specifically" (or Silver, Bronze) view; not built since it wasn't asked
  for, but the SQL already has everything needed (Section 7.2's `Prior
  Tier` column).
- **Confirm `METABASE_COHORT_CARD_ID` is actually in GitHub Secrets** — see
  the checklist note in Section 2. Unverified as of 2026-08-04.

## 9. How to verify changes (what's actually been done every time so far)

1. Edit `index.html` / `etl.py` in `C:\Referral Tiers`.
2. If `etl.py` changed, re-run it locally (`.env` has real credentials) to
   regenerate `data/*.json` before checking the frontend.
3. Serve locally (`preview.bat`, or `python -m http.server 8080` manually)
   and open in a browser.
   - **3a. Hard-reload, don't trust a normal reload.** Python's
     `http.server` doesn't send cache-control headers, so a browser that
     already fetched `data/*.json` once (e.g. from an earlier preview in the
     same session) can silently keep serving that cached response to
     `index.html`'s plain `fetch()` calls even after `etl.py` writes fresh
     files — a real hard-reload/cache-bypass, or a one-off
     `fetch(url, {cache: "no-store"})`, is needed to be sure you're looking
     at current data. Hit this directly on 2026-08-03: the page kept showing
     a 3-day-old "Jul '26" column after the ETL had already been re-run and
     the file on disk was confirmed correct.
4. Cross-check displayed numbers against the raw JSON by hand (e.g.
   `python -c "import json; ..."` one-liners) — this caught every real
   issue in this build; don't skip it.
5. Check browser console for JS errors.
6. Test both toggle modes (6-Tier / Metals) and with a city/tier filter
   applied — several bugs only showed up under a specific filter combo.
7. Only then copy changed files into the separate git clone and push
   (Section 1) — always `git pull` in the clone first.
