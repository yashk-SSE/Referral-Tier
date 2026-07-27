#!/usr/bin/env python3
"""
Referral Tier Analytics — ETL
==============================
Fetches referral tier data from two Metabase saved questions and
writes structured JSON files consumed by the static dashboard.

Outputs written to data/:
  tier_mom.json      → month-over-month tier counts per cluster
  tier_sseid.json    → one row per SSEID with tier, leads, orders
  meta.json          → run metadata, months covered, cities list

Environment variables (set in GitHub Secrets or .env):
  METABASE_URL            e.g. https://your.metabase.com
  METABASE_API_KEY        preferred auth — a Metabase API key (Admin > Settings >
                          Authentication > API keys). Skips username/password entirely.
  METABASE_USERNAME       fallback auth — your login email (used only if no API key)
  METABASE_PASSWORD       fallback auth — your password (used only if no API key)
  METABASE_AGG_CARD_ID    card ID of the aggregated question (cluster x tier x count)
  METABASE_SSEID_CARD_ID  card ID of the SSEID detail question
  MONTHS                  how many months back to fetch (default 6)
  END_MONTH               override end month as YYYY-MM (default = current month)
"""

import os, sys, json, requests
from datetime import datetime, date, timezone, timedelta
from calendar import monthrange
from dotenv import load_dotenv

# Windows consoles default to cp1252, which can't print the ✓/⚠ characters below
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
METABASE_URL   = os.environ["METABASE_URL"].rstrip("/")
API_KEY        = os.environ.get("METABASE_API_KEY", "").strip()
USERNAME       = os.environ.get("METABASE_USERNAME", "")
PASSWORD       = os.environ.get("METABASE_PASSWORD", "")
AGG_CARD_ID    = int(os.environ.get("METABASE_AGG_CARD_ID",  "0"))
SSEID_CARD_ID  = int(os.environ.get("METABASE_SSEID_CARD_ID", "0"))
HELDBASE_CARD_ID = int(os.environ.get("METABASE_HELDBASE_CARD_ID", "0"))
MONTHS         = int(os.environ.get("MONTHS", "6"))
END_MONTH_STR  = os.environ.get("END_MONTH", "")   # YYYY-MM, optional override
TIMEOUT_S      = 300


# ── TIERS (ordered highest → lowest for sorting) ─────────────────────────────
TIER_ORDER = ["Platinum", "Gold", "Silver", "Bronze", "Stones", "Sticks"]

TIER_CONDITIONS = {
    "Platinum": "≥ 12 referral orders",
    "Gold":     "7–11 referral orders",
    "Silver":   "3–6 referral orders",
    "Bronze":   "1–2 referral orders",
    "Stones":   "≥ 1 lead, 0 orders",
    "Sticks":   "0 leads, 0 orders",
}

TIER_COLORS = {
    "Platinum": "#c084fc",
    "Gold":     "#f59e0b",
    "Silver":   "#94a3b8",
    "Bronze":   "#b87333",
    "Stones":   "#64748b",
    "Sticks":   "#475569",
}


# ── DATE HELPERS ──────────────────────────────────────────────────────────────
def current_month_ist() -> tuple:
    """Returns (year, month) for the current month in IST."""
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    return now_ist.year, now_ist.month


def build_month_range(months: int, end_month_str: str = "") -> list:
    """
    Returns a list of dicts [{label, year, month, end_date}, ...]
    ordered oldest → newest. end_date is the last day of each month.
    """
    if end_month_str:
        ey, em = map(int, end_month_str.split("-"))
    else:
        ey, em = current_month_ist()

    result = []
    y, m = ey, em
    for _ in range(months):
        last_day = monthrange(y, m)[1]
        end_date = date(y, m, last_day).strftime("%Y-%m-%d")
        label = date(y, m, 1).strftime("%b '%y")   # e.g. "Nov '25"
        result.insert(0, {"label": label, "year": y, "month": m, "end_date": end_date})
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return result


# ── METABASE API ──────────────────────────────────────────────────────────────
def get_session_token() -> str:
    if not USERNAME or not PASSWORD:
        raise RuntimeError(
            "No METABASE_API_KEY set, and METABASE_USERNAME/METABASE_PASSWORD are "
            "missing — set one auth method in .env."
        )
    r = requests.post(
        f"{METABASE_URL}/api/session",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def get_auth_headers() -> dict:
    """Prefers an API key (Admin > Settings > Authentication > API keys); falls
    back to a session token from username/password login."""
    if API_KEY:
        return {"x-api-key": API_KEY}
    return {"X-Metabase-Session": get_session_token()}


def fetch_card_json(headers: dict, card_id: int, end_date: str) -> list:
    """
    Calls POST /api/card/{id}/query/json with an end_date template parameter.
    Falls back to a plain fetch (no params) if the question has no parameters.
    """
    params_payload = json.dumps([{
        "type":   "date/single",
        "target": ["variable", ["template-tag", "end_date"]],
        "value":  end_date,
    }])

    body = {"ignore_cache": False, "parameters": json.loads(params_payload)}

    r = requests.post(
        f"{METABASE_URL}/api/card/{card_id}/query/json",
        headers=headers,
        json=body,
        timeout=TIMEOUT_S,
    )

    # If the question has no template tag, Metabase returns 400 — retry without params
    if r.status_code == 400:
        print(f"      ⚠  Card {card_id} has no {{{{end_date}}}} parameter — fetching without date filter.")
        r = requests.post(
            f"{METABASE_URL}/api/card/{card_id}/query/json",
            headers=headers,
            json={"ignore_cache": False},
            timeout=TIMEOUT_S,
        )

    r.raise_for_status()
    return r.json()


def fetch_heldbase_card_json(headers: dict, card_id: int, prev_end: str, curr_end: str) -> list:
    """
    Calls POST /api/card/{id}/query/json with prev_end/curr_end template parameters
    for the held-base movement question (SSEID tier at both dates, base fixed to prev_end).
    """
    params_payload = [
        {"type": "date/single", "target": ["variable", ["template-tag", "prev_end"]], "value": prev_end},
        {"type": "date/single", "target": ["variable", ["template-tag", "curr_end"]], "value": curr_end},
    ]

    r = requests.post(
        f"{METABASE_URL}/api/card/{card_id}/query/json",
        headers=headers,
        json={"ignore_cache": False, "parameters": params_payload},
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    return r.json()


# ── TRANSFORM: AGGREGATED DATA ────────────────────────────────────────────────
def normalise_agg_row(row: dict) -> dict | None:
    """
    Normalises one row from the aggregated card.
    Expected columns (case-insensitive): cluster, tier, cnt/count
    Returns None if essential fields are missing.
    """
    # Find columns by pattern (handles different column names)
    keys = {k.lower(): k for k in row}
    cluster_key = next((keys[k] for k in keys if "cluster" in k or "city" in k), None)
    tier_key    = next((keys[k] for k in keys if "tier" in k), None)
    cnt_key     = next((keys[k] for k in keys if k in ("cnt", "count", "lead_count", "n")), None)

    if not tier_key or not cnt_key:
        return None

    cluster = str(row.get(cluster_key) or "Unknown").strip() if cluster_key else "Unknown"
    tier    = str(row.get(tier_key)    or "Sticks").strip()
    cnt     = int(row.get(cnt_key)     or 0)

    # Normalise tier capitalisation
    tier_normalised = next((t for t in TIER_ORDER if t.lower() == tier.lower()), "Sticks")

    return {"cluster": cluster, "tier": tier_normalised, "cnt": cnt}


def normalise_sseid_row(row: dict) -> dict | None:
    """
    Normalises one row from the SSEID detail card.
    Expected columns: SSEID, City/Cluster, Leads Given, Orders Given, Tier
    """
    keys = {k.lower(): k for k in row}

    sseid_key   = next((keys[k] for k in keys if "sseid" in k), None)
    city_key    = next((keys[k] for k in keys if "city" in k or "cluster" in k), None)
    leads_key   = next((keys[k] for k in keys if "lead" in k and "count" not in k.replace("lead_count","x")), None)
    orders_key  = next((keys[k] for k in keys if "order" in k and "count" not in k.replace("order_count","x")), None)
    tier_key    = next((keys[k] for k in keys if "tier" in k), None)

    if not sseid_key and not city_key:
        return None

    sseid  = str(row.get(sseid_key)  or "").strip() if sseid_key  else ""
    city   = str(row.get(city_key)   or "Unknown").strip() if city_key  else "Unknown"
    leads  = int(row.get(leads_key)  or 0) if leads_key  else 0
    orders = int(row.get(orders_key) or 0) if orders_key else 0
    tier   = str(row.get(tier_key)   or "Sticks").strip() if tier_key else "Sticks"

    tier_normalised = next((t for t in TIER_ORDER if t.lower() == tier.lower()), "Sticks")

    return {
        "sseid":   sseid,
        "city":    city,
        "leads":   leads,
        "orders":  orders,
        "tier":    tier_normalised,
    }


def normalise_heldbase_row(row: dict) -> dict | None:
    """
    Normalises one row from the held-base movement card.
    Expected columns: SSEID, City, Leads Prev, Orders Prev, Tier Prev,
                       Leads Curr, Orders Curr, Tier Curr
    """
    keys = {k.lower(): k for k in row}

    def find(*parts):
        return next((keys[k] for k in keys if all(p in k for p in parts)), None)

    sseid_key       = find("sseid")
    city_key        = find("city")
    leads_prev_key  = find("lead", "prev")
    orders_prev_key = find("order", "prev")
    tier_prev_key   = find("tier", "prev")
    leads_curr_key  = find("lead", "curr")
    orders_curr_key = find("order", "curr")
    tier_curr_key   = find("tier", "curr")

    if not sseid_key or not tier_prev_key or not tier_curr_key:
        return None

    def norm_tier(v):
        v = str(v or "Sticks").strip()
        return next((t for t in TIER_ORDER if t.lower() == v.lower()), "Sticks")

    return {
        "sseid":       str(row.get(sseid_key) or "").strip(),
        "city":        str(row.get(city_key) or "Unknown").strip() if city_key else "Unknown",
        "leads_prev":  int(row.get(leads_prev_key) or 0) if leads_prev_key else 0,
        "orders_prev": int(row.get(orders_prev_key) or 0) if orders_prev_key else 0,
        "tier_prev":   norm_tier(row.get(tier_prev_key)),
        "leads_curr":  int(row.get(leads_curr_key) or 0) if leads_curr_key else 0,
        "orders_curr": int(row.get(orders_curr_key) or 0) if orders_curr_key else 0,
        "tier_curr":   norm_tier(row.get(tier_curr_key)),
    }


# ── TRANSFORM: HELD-BASE MOVEMENT ─────────────────────────────────────────────
def build_held_base_summary(rows: list) -> dict:
    """
    rows: normalised held-base rows (one per SSEID in the base fixed at prev_end).
    Returns counts/percentages/movement for that SSEID set at both dates.
    """
    base_size = len(rows)
    by_tier_prev = {t: 0 for t in TIER_ORDER}
    by_tier_curr = {t: 0 for t in TIER_ORDER}
    by_cluster_tier_prev = {}
    by_cluster_tier_curr = {}

    for r in rows:
        by_tier_prev[r["tier_prev"]] += 1
        by_tier_curr[r["tier_curr"]] += 1
        kp = f'{r["city"]}|{r["tier_prev"]}'
        kc = f'{r["city"]}|{r["tier_curr"]}'
        by_cluster_tier_prev[kp] = by_cluster_tier_prev.get(kp, 0) + 1
        by_cluster_tier_curr[kc] = by_cluster_tier_curr.get(kc, 0) + 1

    movement_pp = {}
    for t in TIER_ORDER:
        prev_pct = (by_tier_prev[t] / base_size * 100) if base_size else 0.0
        curr_pct = (by_tier_curr[t] / base_size * 100) if base_size else 0.0
        movement_pp[t] = round(curr_pct - prev_pct, 2)

    return {
        "base_size":            base_size,
        "by_tier_prev":         by_tier_prev,
        "by_tier_curr":         by_tier_curr,
        "by_cluster_tier_prev": {k: v for k, v in by_cluster_tier_prev.items()},
        "by_cluster_tier_curr": {k: v for k, v in by_cluster_tier_curr.items()},
        "movement_pp":          movement_pp,
    }


def build_tier_heldbase_sseid(rows: list, prev_mo: dict, curr_mo: dict) -> dict:
    cities = sorted({r["city"] for r in rows if r["city"] != "Unknown"})
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "prev_end":     prev_mo["end_date"],
            "curr_end":     curr_mo["end_date"],
            "prev_label":   prev_mo["label"],
            "curr_label":   curr_mo["label"],
            "total":        len(rows),
            "cities":       cities,
            "tiers":        TIER_ORDER,
            "tier_colors":  TIER_COLORS,
        },
        "records": rows,
    }


# ── BUILD OUTPUT FILES ────────────────────────────────────────────────────────
def build_tier_mom(month_agg_data: list, held_base_by_end_date: dict | None = None) -> dict:
    """
    month_agg_data: [{month_label, end_date, rows: [{cluster, tier, cnt}]}]
    held_base_by_end_date: {curr_end_date: held_base_summary}, optional
    Produces the structure the dashboard chart needs.
    """
    held_base_by_end_date = held_base_by_end_date or {}
    # Collect all clusters across all months
    all_clusters = sorted({
        row["cluster"]
        for m in month_agg_data
        for row in m["rows"]
        if row["cluster"] != "Unknown"
    })

    months_out = []
    for m in month_agg_data:
        total = sum(r["cnt"] for r in m["rows"])
        by_tier = {t: 0 for t in TIER_ORDER}
        by_cluster_tier = {}
        for row in m["rows"]:
            by_tier[row["tier"]] = by_tier.get(row["tier"], 0) + row["cnt"]
            key = (row["cluster"], row["tier"])
            by_cluster_tier[key] = by_cluster_tier.get(key, 0) + row["cnt"]

        month_out = {
            "label":          m["label"],
            "end_date":       m["end_date"],
            "total":          total,
            "by_tier":        by_tier,
            "by_cluster_tier": {f"{k[0]}|{k[1]}": v for k, v in by_cluster_tier.items()},
        }
        if m["end_date"] in held_base_by_end_date:
            month_out["held_base"] = held_base_by_end_date[m["end_date"]]
        months_out.append(month_out)

    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "months":        len(month_agg_data),
            "clusters":      all_clusters,
            "tiers":         TIER_ORDER,
            "tier_colors":   TIER_COLORS,
            "tier_conditions": TIER_CONDITIONS,
        },
        "months": months_out,
    }


def build_tier_sseid(rows: list, end_date: str) -> dict:
    cities = sorted({r["city"] for r in rows if r["city"] != "Unknown"})
    return {
        "meta": {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "as_of_date":   end_date,
            "total":        len(rows),
            "cities":       cities,
            "tiers":        TIER_ORDER,
            "tier_colors":  TIER_COLORS,
        },
        "records": rows,
    }


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    os.makedirs("data", exist_ok=True)

    print("[1/6] Authenticating with Metabase...")
    headers = get_auth_headers()
    print(f"      ✓ Using {'API key' if API_KEY else 'username/password session'}")

    months = build_month_range(MONTHS, END_MONTH_STR)
    latest_end = months[-1]["end_date"]
    print(f"[2/6] Month range: {months[0]['label']} → {months[-1]['label']}  ({len(months)} months)")

    # ── Held-base movement: base fixed to the single earliest month in the
    # window (not rolled forward month to month) — every column then shares
    # the same denominator, so adjacent columns are directly comparable and
    # base growth is excluded consistently across the whole window, not just
    # pairwise. ──────────────────────────────────────────────────────────────
    held_base_by_end_date = {}
    latest_heldbase_rows, latest_pair = [], None
    anchor_mo = months[0]
    if HELDBASE_CARD_ID and len(months) >= 2:
        print(f"[3/6] Fetching held-base movement (card {HELDBASE_CARD_ID}), "
              f"anchored to {anchor_mo['label']}...")
        for i in range(1, len(months)):
            curr_mo = months[i]
            print(f"      {anchor_mo['label']} → {curr_mo['label']} "
                  f"(prev_end={anchor_mo['end_date']}, curr_end={curr_mo['end_date']})...")
            raw = fetch_heldbase_card_json(headers, HELDBASE_CARD_ID, anchor_mo["end_date"], curr_mo["end_date"])
            rows = [r for r in (normalise_heldbase_row(row) for row in raw) if r]
            summary = build_held_base_summary(rows)
            held_base_by_end_date[curr_mo["end_date"]] = summary
            print(f"        → held base of {summary['base_size']:,} SSEIDs")
            if i == len(months) - 1:
                latest_heldbase_rows, latest_pair = rows, (anchor_mo, curr_mo)
    else:
        print("[3/6] METABASE_HELDBASE_CARD_ID not set (or fewer than 2 months) — skipping held-base fetch.")

    # ── Aggregated: one fetch per month ──────────────────────────────────────
    month_agg_data = []
    if AGG_CARD_ID:
        print(f"[4/6] Fetching aggregated data (card {AGG_CARD_ID})...")
        for mo in months:
            print(f"      Fetching {mo['label']} (end_date={mo['end_date']})...")
            raw = fetch_card_json(headers, AGG_CARD_ID, mo["end_date"])
            rows = [r for r in (normalise_agg_row(row) for row in raw) if r]
            print(f"        → {len(rows)} rows")
            month_agg_data.append({
                "label":    mo["label"],
                "end_date": mo["end_date"],
                "rows":     rows,
            })
        tier_mom = build_tier_mom(month_agg_data, held_base_by_end_date)
        with open("data/tier_mom.json", "w") as f:
            json.dump(tier_mom, f, indent=2, default=str)
        latest_total = tier_mom["months"][-1]["total"] if tier_mom["months"] else 0
        print(f"      ✓ tier_mom.json written — {latest_total:,} assets in latest month")
    else:
        print("[4/6] METABASE_AGG_CARD_ID not set — skipping aggregated fetch.")
        # Write empty placeholder so dashboard doesn't break
        with open("data/tier_mom.json", "w") as f:
            json.dump({"meta": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "months": 0, "clusters": [], "tiers": TIER_ORDER,
                                "tier_colors": TIER_COLORS, "tier_conditions": TIER_CONDITIONS},
                       "months": []}, f, indent=2)

    # ── SSEID detail: latest month only ──────────────────────────────────────
    if SSEID_CARD_ID:
        print(f"[5/6] Fetching SSEID detail (card {SSEID_CARD_ID}, end_date={latest_end})...")
        raw_sseid = fetch_card_json(headers, SSEID_CARD_ID, latest_end)
        sseid_rows = [r for r in (normalise_sseid_row(row) for row in raw_sseid) if r]
        tier_sseid = build_tier_sseid(sseid_rows, latest_end)
        with open("data/tier_sseid.json", "w") as f:
            json.dump(tier_sseid, f, indent=2, default=str)
        print(f"      ✓ tier_sseid.json written — {len(sseid_rows):,} SSEID rows")
    else:
        print("[5/6] METABASE_SSEID_CARD_ID not set — skipping SSEID fetch.")
        with open("data/tier_sseid.json", "w") as f:
            json.dump({"meta": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "as_of_date": latest_end, "total": 0,
                                "cities": [], "tiers": TIER_ORDER, "tier_colors": TIER_COLORS},
                       "records": []}, f, indent=2)

    # ── Held-base SSEID detail: latest pair only ─────────────────────────────
    if latest_pair:
        prev_mo, curr_mo = latest_pair
        tier_heldbase_sseid = build_tier_heldbase_sseid(latest_heldbase_rows, prev_mo, curr_mo)
        with open("data/tier_heldbase_sseid.json", "w") as f:
            json.dump(tier_heldbase_sseid, f, indent=2, default=str)
        print(f"      ✓ tier_heldbase_sseid.json written — {len(latest_heldbase_rows):,} SSEID rows "
              f"({prev_mo['label']} → {curr_mo['label']})")
    else:
        with open("data/tier_heldbase_sseid.json", "w") as f:
            json.dump({"meta": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "prev_end": None, "curr_end": None, "prev_label": None, "curr_label": None,
                                "total": 0, "cities": [], "tiers": TIER_ORDER, "tier_colors": TIER_COLORS},
                       "records": []}, f, indent=2)

    # ── Meta file ─────────────────────────────────────────────────────────────
    print("[6/6] Writing meta.json...")
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "months_fetched": len(months),
        "range_start":    months[0]["label"],
        "range_end":      months[-1]["label"],
        "latest_end_date": latest_end,
        "agg_card_id":    AGG_CARD_ID,
        "sseid_card_id":  SSEID_CARD_ID,
        "heldbase_card_id": HELDBASE_CARD_ID,
    }
    with open("data/meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"      ✓ meta.json written")

    print("\n✅ ETL complete.")


if __name__ == "__main__":
    main()
