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
  METABASE_USERNAME       your login email
  METABASE_PASSWORD       your password
  METABASE_AGG_CARD_ID    card ID of the aggregated question (cluster x tier x count)
  METABASE_SSEID_CARD_ID  card ID of the SSEID detail question
  MONTHS                  how many months back to fetch (default 6)
  END_MONTH               override end month as YYYY-MM (default = current month)
"""

import os, json, requests
from datetime import datetime, date, timezone, timedelta
from calendar import monthrange
from dotenv import load_dotenv

load_dotenv()

# ── CONFIG ────────────────────────────────────────────────────────────────────
METABASE_URL   = os.environ["METABASE_URL"].rstrip("/")
USERNAME       = os.environ["METABASE_USERNAME"]
PASSWORD       = os.environ["METABASE_PASSWORD"]
AGG_CARD_ID    = int(os.environ.get("METABASE_AGG_CARD_ID",  "0"))
SSEID_CARD_ID  = int(os.environ.get("METABASE_SSEID_CARD_ID", "0"))
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
    r = requests.post(
        f"{METABASE_URL}/api/session",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["id"]


def fetch_card_json(token: str, card_id: int, end_date: str) -> list:
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
        headers={"X-Metabase-Session": token},
        json=body,
        timeout=TIMEOUT_S,
    )

    # If the question has no template tag, Metabase returns 400 — retry without params
    if r.status_code == 400:
        print(f"      ⚠  Card {card_id} has no {{{{end_date}}}} parameter — fetching without date filter.")
        r = requests.post(
            f"{METABASE_URL}/api/card/{card_id}/query/json",
            headers={"X-Metabase-Session": token},
            json={"ignore_cache": False},
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


# ── BUILD OUTPUT FILES ────────────────────────────────────────────────────────
def build_tier_mom(month_agg_data: list) -> dict:
    """
    month_agg_data: [{month_label, end_date, rows: [{cluster, tier, cnt}]}]
    Produces the structure the dashboard chart needs.
    """
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

        months_out.append({
            "label":          m["label"],
            "end_date":       m["end_date"],
            "total":          total,
            "by_tier":        by_tier,
            "by_cluster_tier": {f"{k[0]}|{k[1]}": v for k, v in by_cluster_tier.items()},
        })

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

    print("[1/5] Authenticating with Metabase...")
    token = get_session_token()
    print("      ✓ Token acquired")

    months = build_month_range(MONTHS, END_MONTH_STR)
    latest_end = months[-1]["end_date"]
    print(f"[2/5] Month range: {months[0]['label']} → {months[-1]['label']}  ({len(months)} months)")

    # ── Aggregated: one fetch per month ──────────────────────────────────────
    month_agg_data = []
    if AGG_CARD_ID:
        print(f"[3/5] Fetching aggregated data (card {AGG_CARD_ID})...")
        for mo in months:
            print(f"      Fetching {mo['label']} (end_date={mo['end_date']})...")
            raw = fetch_card_json(token, AGG_CARD_ID, mo["end_date"])
            rows = [r for r in (normalise_agg_row(row) for row in raw) if r]
            print(f"        → {len(rows)} rows")
            month_agg_data.append({
                "label":    mo["label"],
                "end_date": mo["end_date"],
                "rows":     rows,
            })
        tier_mom = build_tier_mom(month_agg_data)
        with open("data/tier_mom.json", "w") as f:
            json.dump(tier_mom, f, indent=2, default=str)
        latest_total = tier_mom["months"][-1]["total"] if tier_mom["months"] else 0
        print(f"      ✓ tier_mom.json written — {latest_total:,} assets in latest month")
    else:
        print("[3/5] METABASE_AGG_CARD_ID not set — skipping aggregated fetch.")
        # Write empty placeholder so dashboard doesn't break
        with open("data/tier_mom.json", "w") as f:
            json.dump({"meta": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "months": 0, "clusters": [], "tiers": TIER_ORDER,
                                "tier_colors": TIER_COLORS, "tier_conditions": TIER_CONDITIONS},
                       "months": []}, f, indent=2)

    # ── SSEID detail: latest month only ──────────────────────────────────────
    if SSEID_CARD_ID:
        print(f"[4/5] Fetching SSEID detail (card {SSEID_CARD_ID}, end_date={latest_end})...")
        raw_sseid = fetch_card_json(token, SSEID_CARD_ID, latest_end)
        sseid_rows = [r for r in (normalise_sseid_row(row) for row in raw_sseid) if r]
        tier_sseid = build_tier_sseid(sseid_rows, latest_end)
        with open("data/tier_sseid.json", "w") as f:
            json.dump(tier_sseid, f, indent=2, default=str)
        print(f"      ✓ tier_sseid.json written — {len(sseid_rows):,} SSEID rows")
    else:
        print("[4/5] METABASE_SSEID_CARD_ID not set — skipping SSEID fetch.")
        with open("data/tier_sseid.json", "w") as f:
            json.dump({"meta": {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                "as_of_date": latest_end, "total": 0,
                                "cities": [], "tiers": TIER_ORDER, "tier_colors": TIER_COLORS},
                       "records": []}, f, indent=2)

    # ── Meta file ─────────────────────────────────────────────────────────────
    print("[5/5] Writing meta.json...")
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "months_fetched": len(months),
        "range_start":    months[0]["label"],
        "range_end":      months[-1]["label"],
        "latest_end_date": latest_end,
        "agg_card_id":    AGG_CARD_ID,
        "sseid_card_id":  SSEID_CARD_ID,
    }
    with open("data/meta.json", "w") as f:
        json.dump(meta, f, indent=2)
    print(f"      ✓ meta.json written")

    print("\n✅ ETL complete.")


if __name__ == "__main__":
    main()
