"""
MUनीम API — "the waiter".

A small, dependency-light web service that:
  1. Serves the MUनीम dashboard HTML at  /
  2. Serves live data as JSON the dashboard can fetch:
       /api/data?days=N      -> one tray: overview + per-pod ops + coverage + brand
       /api/insights         -> live AI "MUनीम says" for the whole studio
       /api/insights/pod/<id>-> live AI for one pod
       /api/health           -> quick OK check

It reuses the SAME Supabase snapshots the Streamlit dashboard reads, and the
SAME status / KPI logic, so the numbers match. No Streamlit, no pandas — just
the standard library + psycopg2 + anthropic.

Run it:   venv/Scripts/python.exe munim_api.py
Then open http://localhost:8787  in your browser.
"""

import json
import os
import re
import sys
import threading
import time

try:  # Windows consoles default to cp1252 and choke on Devanagari output
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
HTML_FILE = Path(__file__).parent / "munim.html"
PORT = int(os.environ.get("MUNIM_PORT", "8787"))

# ── Sheet IDs (mirror snapshot_all.py) ──────────────────────────────────────
COVERAGE_SHEET_ID = "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM"
NEWSLETTERS_SHEET_ID = "1HXFklF6_RJ3L_lSDe0AUr1xdxKQ-c9ngYvvUosyFI94"
ORM_SHEET_ID = "1kBFoCe28vrkVqnaRyn3dqNxBs_KSZf8MuZcpVp_vAXE"
INFLUENCER_SHEET_ID = "1RCMD8DHsIVBnwrIfl_2qgvt0LQZaUG2eoDFLwwuHano"

# ── Production pods (mirror mcp_server.py / dashboard.py) ────────────────────
PODS = {
    "Builders.mu":              {"upload_cols": ["Date of Upload"],                 "lead": "Raja Kumar"},
    "Brand/Ad films":           {"upload_cols": [],                                 "lead": "Devansh Kotak"},
    "PGP Bharat IG":            {"upload_cols": ["Date of Upload"],                 "lead": "Sabhya Sharma"},
    "Perf Ads":                 {"upload_cols": [],                                 "lead": "Devansh Kotak"},
    "YT - Podcasts (Series C)": {"upload_cols": ["YT Date of Upload", "YT UPLOAD"], "lead": "Ishika Aggarwal"},
    "YT - Off Campus":          {"upload_cols": ["YT Date of Upload", "YT UPLOAD"], "lead": "Ishika Aggarwal"},
    "YT - Masters Of The Market": {"upload_cols": ["YT Date of Upload", "YT UPLOAD"], "lead": "Ishika Aggarwal"},
    "YT - Family Business":     {"upload_cols": ["YT Date of Upload", "YT UPLOAD"], "lead": "Ishika Aggarwal"},
}

# Map the live sheet tab name  ->  the MUनीम front-end pod id (the key in its
# PODS registry). This is how a real sheet lights up the right card.
TAB_TO_MUNIM = {
    "Builders.mu":                "builders",
    "Brand/Ad films":             "films",
    "Perf Ads":                   "perf",
    "YT - Off Campus":            "offcampus",
    "YT - Podcasts (Series C)":   "seriesc",
    "YT - Masters Of The Market": "cmt",
    "YT - Family Business":       "opm",
    "PGP Bharat IG":              "bharat",   # no dedicated MUनीम card yet; shows in overview
}

DISPLAY = {
    "PGP Bharat IG": "Bharat.mu",
    "Brand/Ad films": "Brand/Ad Films",
    "Perf Ads": "Performance Ads",
}

BASE_DATE_COLS = [
    "Date of Shoot", "Edit Start Date",
    "Planned Date of Delivery", "Actual Date of Delivery", "Date of Upload",
]
DELIVERED_COLS = ["Actual Date of Delivery", "Date of Delivery", "Delivered Date",
                  "Final Delivery Date", "Delivery Date"]
EDIT_START_COLS = ["Edit Start Date", "Editing Start Date", "Edit Date", "Edit Start"]
SHOOT_DATE_COLS = ["Date of Shoot", "Shoot Date", "Shooting Date", "Date Shot"]


def display_name(pod):
    return DISPLAY.get(pod, pod)


# ── tiny parsing helpers (mirror the dashboard) ─────────────────────────────
def parse_date_str(s):
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_clock(t):
    if not t:
        return None
    s = str(t).strip().lower().replace(" ", "")
    if not s:
        return None
    for fmt in ["%I:%M%p", "%I%p", "%H:%M", "%H%M"]:
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def first_filled(row, cols):
    for c in cols:
        v = str(row.get(c, "") or "").strip()
        if v:
            return v
    return ""


def parse_count(s):
    """'41,895' / '1.2 Mn' / '32%' -> float (mirror dashboard._parse_count)."""
    if s is None:
        return None
    s = str(s).strip()
    if not s or s in ("-", "N/A", "NA"):
        return None
    s = s.replace(",", "")
    is_pct = "%" in s
    s = s.replace("%", "").strip()
    mult = 1.0
    low = s.lower()
    if low.endswith("mn"):
        mult, s = 1_000_000.0, low[:-2].strip()
    elif low.endswith("k"):
        mult, s = 1_000.0, low[:-1].strip()
    try:
        return float(s) * mult / (100.0 if is_pct else 1.0)
    except ValueError:
        return None


def pick(row, keys):
    """First parseable numeric value across candidate column names."""
    for k in keys:
        v = parse_count(row.get(k))
        if v is not None:
            return v
    return None


def parse_nl_date(s):
    if not s:
        return None
    s = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", str(s).strip())
    for fmt in ["%d %B, %Y", "%d %B %Y", "%d %b, %Y", "%d %b %Y", "%d/%m/%Y", "%Y-%m-%d"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_month_yr(s):
    """'April'25' / 'Apr'25' -> date (first of month)."""
    if not s:
        return None
    s = str(s).strip().replace("’", "'")
    for fmt in ["%B'%y", "%b'%y", "%B '%y", "%b '%y", "%B'%Y", "%b'%Y", "%B %Y", "%b %Y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def compute_status(row, pod):
    has_upload = bool(PODS.get(pod, {}).get("upload_cols"))
    if has_upload:
        for col in PODS[pod]["upload_cols"]:
            if str(row.get(col, "") or "").strip():
                return "Live"
    if first_filled(row, DELIVERED_COLS):
        return "Delivered, Awaiting Upload" if has_upload else "Delivered"
    if first_filled(row, EDIT_START_COLS):
        return "In Editing"
    if first_filled(row, SHOOT_DATE_COLS):
        return "Shot, Awaiting Edit"
    return "Pre-production / Ideation"


def in_range(row, start, end, cols):
    for c in cols:
        d = parse_date_str(row.get(c))
        if d and start <= d <= end:
            return True
    return False


# ── DB access ───────────────────────────────────────────────────────────────
_tls = threading.local()


def _get_conn():
    """Reuse a per-build connection when one is active, else a throwaway one."""
    c = getattr(_tls, "conn", None)
    if c is not None:
        return c, False
    return psycopg2.connect(DATABASE_URL), True


def fetch_latest_rows(tab_name, sheet_id=None):
    """Latest non-divider snapshot rows for a tab (optionally pinned to a sheet)."""
    conn, owned = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if sheet_id:
                cur.execute(
                    """
                    select sr.data from snapshot_rows sr
                    join snapshots s on sr.snapshot_id = s.id
                    where s.id = (select id from snapshots
                                  where tab_name=%s and sheet_id=%s
                                  order by captured_at desc limit 1)
                      and not sr.is_divider
                    order by sr.row_number
                    """,
                    (tab_name, sheet_id),
                )
            else:
                cur.execute(
                    """
                    select sr.data from snapshot_rows sr
                    join snapshots s on sr.snapshot_id = s.id
                    where s.id = (select id from snapshots
                                  where tab_name=%s order by captured_at desc limit 1)
                      and not sr.is_divider
                    order by sr.row_number
                    """,
                    (tab_name,),
                )
            return [r["data"] for r in cur.fetchall() if r["data"]]
    finally:
        if owned:
            conn.close()


def fetch_coverage_rows():
    conn, owned = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                select sr.data from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.id = (select id from snapshots
                              where sheet_id=%s order by captured_at desc limit 1)
                  and not sr.is_divider
                order by sr.row_number
                """,
                (COVERAGE_SHEET_ID,),
            )
            return [r["data"] for r in cur.fetchall() if r["data"]]
    finally:
        if owned:
            conn.close()


# ── per-pod operations metrics (port of compute_pod_performance) ────────────
def _latest_date(d, cols):
    best = None
    for c in cols:
        dt = parse_date_str(d.get(c))
        if dt and (best is None or dt > best):
            best = dt
    return best


def _weekly_series(rows, cols, start, today):
    """Real trend: count of pieces whose latest date lands in each week bucket."""
    span = max((today - start).days, 7)
    weeks = max(1, min(12, (span + 6) // 7))
    buckets = [0] * weeks
    labels = []
    for w in range(weeks):
        wk_start = start + timedelta(days=int(w * span / weeks))
        labels.append(wk_start.strftime("%-d %b") if os.name != "nt" else wk_start.strftime("%d %b").lstrip("0"))
    for d in rows:
        dt = _latest_date(d, cols)
        if not dt:
            continue
        idx = int((dt - start).days / span * weeks)
        idx = max(0, min(weeks - 1, idx))
        buckets[idx] += 1
    return buckets, labels


def pod_operations(tab, rows_in_range, start=None, today=None):
    upload_cols = PODS[tab]["upload_cols"]
    has_upload = bool(upload_cols)
    final_status = "Live" if has_upload else "Delivered"

    status_counts = {}
    t_shoot_to_live, t_edit_to_deliv, t_shoot_to_edit, slippage = [], [], [], []
    items_with_shoot = items_at_final = 0

    for d in rows_in_range:
        st = compute_status(d, tab)
        status_counts[st] = status_counts.get(st, 0) + 1
        shoot = parse_date_str(first_filled(d, SHOOT_DATE_COLS))
        edit = parse_date_str(first_filled(d, EDIT_START_COLS))
        planned = parse_date_str(first_filled(d, ["Planned Date of Delivery",
                                                  "Tentative Date Of Delivery",
                                                  "Tentative date of delivery"]))
        actual = parse_date_str(first_filled(d, DELIVERED_COLS))
        upload = None
        for col in (["Date of Upload"] + upload_cols):
            u = parse_date_str(d.get(col))
            if u:
                upload = u
                break
        if shoot:
            items_with_shoot += 1
        if st == final_status:
            items_at_final += 1
        if shoot and upload:
            t_shoot_to_live.append((upload - shoot).days)
        if edit and actual:
            t_edit_to_deliv.append((actual - edit).days)
        if shoot and edit:
            t_shoot_to_edit.append((edit - shoot).days)
        if planned and actual:
            slippage.append((actual - planned).days)

    def avg(xs):
        return round(sum(xs) / len(xs), 1) if xs else None

    on_time = sum(1 for s in slippage if s <= 0)
    series, series_labels = ([], [])
    if start and today:
        cols = BASE_DATE_COLS + upload_cols
        series, series_labels = _weekly_series(rows_in_range, cols, start, today)
    return {
        "munim_id": TAB_TO_MUNIM.get(tab),
        "pod": display_name(tab),
        "lead": PODS[tab]["lead"],
        "active": len(rows_in_range),
        "shipped": items_at_final,
        "status_counts": status_counts,
        "on_time_rate": round(on_time / len(slippage), 2) if slippage else None,
        "conversion_rate": round(items_at_final / items_with_shoot, 2) if items_with_shoot else None,
        "avg_shoot_to_live": avg(t_shoot_to_live),
        "avg_edit_to_delivery": avg(t_edit_to_deliv),
        "avg_shoot_to_edit": avg(t_shoot_to_edit),
        "final_status_label": final_status,
        "series": series,
        "series_labels": series_labels,
        "recent": _recent_videos(tab, rows_in_range),
    }


def _recent_videos(tab, rows, limit=14):
    out = []
    for d in rows:
        out.append({
            "name": (d.get("Video Name") or "").strip() or "(unnamed)",
            "status": compute_status(d, tab),
            "lead": d.get("Lead") or d.get("POC in Charge") or d.get("POC in charge", ""),
            "shoot_date": d.get("Date of Shoot", ""),
            "planned_delivery": d.get("Planned Date of Delivery")
                                or d.get("Tentative date of delivery", ""),
            "upload": (d.get("Date of Upload") or d.get("YT Date of Upload")
                       or d.get("YT UPLOAD", "")),
        })
        if len(out) >= limit:
            break
    return out


# ── the big tray: everything the dashboard needs in one fetch ───────────────
_data_cache = {}      # days -> (timestamp, payload)
DATA_TTL = 120        # seconds; sheets only refresh on snapshot anyway


def build_data(days):
    hit = _data_cache.get(days)
    if hit and time.time() - hit[0] < DATA_TTL:
        return hit[1]
    payload = _build_data(days)
    _data_cache[days] = (time.time(), payload)
    return payload


def _build_data(days):
    _tls.conn = psycopg2.connect(DATABASE_URL)
    try:
        return _build_data_inner(days)
    finally:
        try:
            _tls.conn.close()
        finally:
            _tls.conn = None


def _build_data_inner(days):
    today = date.today()
    start = today - timedelta(days=days)
    pods, totals = {}, {
        "active": 0, "live_or_delivered": 0, "in_editing": 0,
        "delivered_pending_upload": 0, "shot_awaiting_edit": 0, "pre_production": 0,
    }
    ranked = []
    for tab in PODS:
        cols = BASE_DATE_COLS + PODS[tab]["upload_cols"]
        rows = [r for r in fetch_latest_rows(tab) if in_range(r, start, today, cols)]
        ops = pod_operations(tab, rows, start, today)
        if ops["munim_id"]:
            pods[ops["munim_id"]] = ops
        sc = ops["status_counts"]
        totals["active"] += ops["active"]
        totals["live_or_delivered"] += sc.get("Live", 0) + sc.get("Delivered", 0)
        totals["delivered_pending_upload"] += sc.get("Delivered, Awaiting Upload", 0)
        totals["in_editing"] += sc.get("In Editing", 0)
        totals["shot_awaiting_edit"] += sc.get("Shot, Awaiting Edit", 0)
        totals["pre_production"] += sc.get("Pre-production / Ideation", 0)
        ranked.append({"pod": ops["pod"], "munim_id": ops["munim_id"],
                       "active": ops["active"], "shipped": ops["shipped"]})

    ranked.sort(key=lambda x: x["shipped"], reverse=True)

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": {"days": days, "from": start.isoformat(), "to": today.isoformat()},
        "totals": totals,
        "pods": pods,
        "ranked": ranked,
        "coverage": build_coverage(days),
        "brand": build_brand(),
        "live_pod_ids": [m for m in TAB_TO_MUNIM.values()],
    }


def build_coverage(days):
    rows = fetch_coverage_rows()
    today = date.today()
    upcoming, today_list, hours = [], [], {}
    for r in rows:
        d = parse_date_str(r.get("Date"))
        if not d:
            continue
        crew_str = first_filled(r, ["Team who shot it", "who shot it", "Crew"])
        item = {
            "date": d.isoformat(),
            "subject": first_filled(r, ["Shoot Subject", "Nature of Shoot"]),
            "department": first_filled(r, ["Department Who Requested", "DEPARTMENT [BILL]", "Department POC"]),
            "time": f"{r.get('Time (From)', '')}-{r.get('Time (Till)', '')}".strip("-"),
            "location": r.get("Location", ""),
            "lead": r.get("Shoot Lead", ""),
            "crew": crew_str,
        }
        if d == today:
            today_list.append(item)
        if today <= d <= today + timedelta(days=14):
            upcoming.append(item)
        # crew hours over the window
        if today - timedelta(days=days) <= d <= today:
            t1, t2 = parse_clock(r.get("Time (From)")), parse_clock(r.get("Time (Till)"))
            if t1 and t2:
                s = datetime.combine(today, t1)
                e = datetime.combine(today, t2)
                if e <= s:
                    e += timedelta(days=1)
                dur = (e - s).total_seconds() / 3600.0
                for p in [x.strip() for x in crew_str.replace("&", ",").replace("+", ",").split(",") if x.strip()]:
                    hours[p] = hours.get(p, 0) + dur
    upcoming.sort(key=lambda x: x["date"])
    crew = sorted([{"crew": k, "hours": round(v, 1)} for k, v in hours.items()],
                  key=lambda x: x["hours"], reverse=True)
    return {"today": today_list, "upcoming": upcoming[:12], "crew_hours": crew[:10]}


# ── brand pods: newsletters, ORM, influencer ────────────────────────────────
NL_TABS = {"MU newsletters": "nl_pratham", "Swati NL": "nl_swati", "Nandini NL": "nl_nandini"}
NL_NAME = {"nl_pratham": "Pratham Mittal", "nl_swati": "Swati Ganeti", "nl_nandini": "Dr. Nandini Seth"}


def build_brand():
    out = {}
    try:
        out["newsletters"] = build_newsletters()
    except Exception as e:
        out["newsletters_error"] = str(e)
    try:
        out["orm"] = build_orm()
    except Exception as e:
        out["orm_error"] = str(e)
    try:
        out["influencer"] = build_influencer()
    except Exception as e:
        out["influencer_error"] = str(e)
    return out


# Real column names confirmed from the live snapshots.
SUB_KEYS = ["Delivered", "Processed (Sent)", "Subscribers", "Recipients", "Sent"]
OPEN_KEYS = ["Open rates", "Open Rate", "Open %", "Unique Open Rate"]


def build_newsletters():
    authors = {}
    for tab, mid in NL_TABS.items():
        rows = [r for r in fetch_latest_rows(tab, NEWSLETTERS_SHEET_ID)
                if any(str(v).strip() for v in r.values())]
        if not rows:
            continue
        dated = [(parse_nl_date(r.get("Newsletter Deployment Date")), r) for r in rows]
        dated = [x for x in dated if x[0]]
        dated.sort(key=lambda x: x[0])
        latest = dated[-1][1] if dated else rows[-1]

        delivered = pick(latest, SUB_KEYS)
        opens = pick(latest, OPEN_KEYS)                  # 0.32 from "32%"
        clicks_abs = parse_count(latest.get("Absolute Clicks"))
        click_rate = (clicks_abs / delivered * 100) if (clicks_abs and delivered) else None

        trend = []
        for d0, r in dated:
            v = pick(r, SUB_KEYS)
            if v:
                trend.append({"date": d0.isoformat(), "value": int(v)})

        authors[mid] = {
            "name": NL_NAME.get(mid, mid),
            "newsletter_name": latest.get("Newsletter Name", ""),
            "subscribers": int(delivered) if delivered else None,
            "open_rate": round(opens * 100, 1) if opens is not None and opens <= 1 else (round(opens, 1) if opens else None),
            "click_rate": round(click_rate, 2) if click_rate is not None else None,
            "issues": len(rows),
            "trend": trend[-12:],
        }
    return authors


def build_orm():
    surfaces = {}
    latest_each = []
    total_views = 0
    for tab in ["Reddit", "Quora"]:
        rows = fetch_latest_rows(tab, ORM_SHEET_ID)
        dated = [(parse_month_yr(r.get("Month'Yr")), r) for r in rows]
        dated = [x for x in dated if x[0]]
        if not dated:
            continue
        dated.sort(key=lambda x: x[0])
        latest = dated[-1][1]
        pos = parse_count(latest.get("Positive Sentiment %"))
        neu = parse_count(latest.get("Neutral Sentiment %"))
        neg = parse_count(latest.get("Negative Sentiment %"))
        views = sum(parse_count(r.get("Views")) or 0 for _, r in dated)
        eng = sum(parse_count(r.get("Engagement")) or 0 for _, r in dated)
        total_views += views
        surfaces[tab] = {
            "positive_pct": round(pos * 100) if pos is not None else None,
            "neutral_pct": round(neu * 100) if neu is not None else None,
            "negative_pct": round(neg * 100) if neg is not None else None,
            "views": int(views), "engagement": int(eng),
            "months": len(dated), "as_of": dated[-1][0].isoformat(),
        }
        latest_each.append((pos, neu, neg))

    def blend(i):
        vals = [t[i] for t in latest_each if t[i] is not None]
        return round(sum(vals) / len(vals) * 100) if vals else None

    return {
        "positive_pct": blend(0), "neutral_pct": blend(1), "negative_pct": blend(2),
        "total_views": int(total_views), "by_surface": surfaces,
    }


def build_influencer():
    rows = [r for r in fetch_latest_rows("2025 Collabs", INFLUENCER_SHEET_ID)
            if str(r.get("Name ", "")).strip() or str(r.get("Link", "")).strip()]
    views = sum(parse_count(r.get("No. of Views")) or 0 for r in rows)
    impr = sum(parse_count(r.get("No. of Impression")) or 0 for r in rows)
    spend = sum(parse_count(r.get("Budget Spent")) or 0 for r in rows)
    agencies = {}
    for r in rows:
        a = str(r.get("Agency", "")).strip() or "Direct"
        agencies[a] = agencies.get(a, 0) + (parse_count(r.get("No. of Views")) or 0)
    top = sorted(({"agency": k, "reach": int(v)} for k, v in agencies.items()),
                 key=lambda x: x["reach"], reverse=True)[:6]
    return {
        "campaigns": len(rows),
        "total_reach": int(views) if views else None,
        "total_impressions": int(impr) if impr else None,
        "total_spend": int(spend) if spend else None,
        "blended_cpv": round(spend / views, 2) if (views and spend) else None,
        "by_agency": top,
    }


# ── AI insights (port of dashboard._generate_ai_insights) ───────────────────
LEADERSHIP_SYSTEM_PROMPT = (
    "You are the chief-of-staff to Divyam Goenka, AD-Brand at Masters' Union "
    "Creative Studio. Divyam is leadership; he reads dashboards in 30 seconds "
    "and acts. Make him remember EVERYTHING the moment he reads your insight, "
    "ask the right question of his team, and spend his energy on the few things "
    "that matter this week.\n\n"
    "Your output must be:\n"
    "- Punchy. Short sentences. No filler.\n"
    "- Specific. Always reference the pod and the number.\n"
    "- Decision-ready. Every line is a celebration, a risk, or a prompt to act.\n"
    "- Markdown bullets, each starting with '- ', one sentence of 10-20 words.\n"
    "- British English. No em dashes. No corporate jargon.\n"
    "- Pod naming: 'Builders.mu', 'Bharat.mu', 'Brand/Ad Films', "
    "'Performance Ads' (not 'Perf Ads'). Never 'MU' for the org."
)

_insight_cache = {}  # key -> (timestamp, dict)
CACHE_TTL = 3600


def _ai_call(user_prompt, max_tokens, keys):
    import anthropic
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=os.environ.get("MUNIM_MODEL", "claude-sonnet-4-5"),
        max_tokens=max_tokens,
        system=LEADERSHIP_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = resp.content[0].text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {k: ("- (Could not parse AI response.)" if i == 0 else "") for i, k in enumerate(keys)}


def insights_overall(data):
    key = "overall:" + data["period"]["from"] + ":" + data["period"]["to"]
    hit = _insight_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    snap = json.dumps({
        "period": data["period"], "totals": data["totals"],
        "pods": {pid: {k: p[k] for k in ("pod", "lead", "active", "shipped",
                 "status_counts", "on_time_rate", "avg_shoot_to_edit")}
                 for pid, p in data["pods"].items()},
        "coverage": {"today": len(data["coverage"]["today"]),
                     "upcoming": len(data["coverage"]["upcoming"])},
        "brand": data["brand"],
    }, default=str)
    prompt = (
        "Current state of the Creative Studio (JSON):\n\n" + snap + "\n\n"
        "Produce four sections. Return raw JSON with keys: wins, risks, today, note.\n"
        "wins/risks/today are markdown bullet strings (2-4 bullets, each '- ' on its own line).\n"
        "note is a single 40-60 word string Divyam can paste into Slack.\n"
        "No fences, just the JSON object."
    )
    out = _ai_call(prompt, 2000, ["wins", "risks", "today", "note"])
    _insight_cache[key] = (time.time(), out)
    return out


def insights_pod(pod_id, data):
    p = data["pods"].get(pod_id)
    if not p:
        return {"wins": "- No live data for this pod yet.", "risks": "", "action": ""}
    key = "pod:" + pod_id + ":" + data["period"]["from"]
    hit = _insight_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    prompt = (
        f"Current state of the {p['pod']} pod (JSON):\n\n{json.dumps(p, default=str)}\n\n"
        "Produce three sections. Return raw JSON with keys: wins, risks, action.\n"
        "Values are markdown bullet strings (1-3 bullets each, '- ' per line).\n"
        "If little data, say so honestly. No fences, just the JSON object."
    )
    out = _ai_call(prompt, 1200, ["wins", "risks", "action"])
    _insight_cache[key] = (time.time(), out)
    return out


# ── HTTP handler ────────────────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass  # quiet

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)
        days = int(q.get("days", ["30"])[0])
        try:
            if path in ("/", "/index.html"):
                if HTML_FILE.exists():
                    return self._send(200, HTML_FILE.read_bytes(), "text/html; charset=utf-8")
                return self._send(404, {"error": "munim.html not found next to munim_api.py"})
            if path.startswith("/data/") and path.endswith(".json"):
                f = HTML_FILE.parent / "munim_static" / path.lstrip("/")
                if f.exists():
                    return self._send(200, f.read_bytes(), "application/json")
                return self._send(404, {"error": "no data file"})
            if path == "/api/health":
                return self._send(200, {"ok": True, "time": datetime.now().isoformat()})
            if path == "/api/data":
                return self._send(200, build_data(days))
            if path == "/api/insights":
                return self._send(200, insights_overall(build_data(days)))
            if path.startswith("/api/insights/pod/"):
                pid = path.rsplit("/", 1)[-1]
                return self._send(200, insights_pod(pid, build_data(days)))
            return self._send(404, {"error": "unknown path", "path": path})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main():
    print(f"MUnim API serving on http://localhost:{PORT}")
    print(f"  dashboard : http://localhost:{PORT}/")
    print(f"  data      : http://localhost:{PORT}/api/data?days=30")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
