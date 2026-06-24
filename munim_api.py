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
CS_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS = os.environ.get("GOOGLE_CREDENTIALS_PATH", "")

# ── Sheet IDs (mirror snapshot_all.py) ──────────────────────────────────────
COVERAGE_SHEET_ID = "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM"
SALARIES_SHEET_ID = "1eok2NGU7gzhM7sGraFcyeqO-AMyyQXzj4AxhV-bXUrw"
NEWSLETTERS_SHEET_ID = "1HXFklF6_RJ3L_lSDe0AUr1xdxKQ-c9ngYvvUosyFI94"
ORM_SHEET_ID = "1kBFoCe28vrkVqnaRyn3dqNxBs_KSZf8MuZcpVp_vAXE"

# pod id -> Instagram handle (extend as we connect more accounts)
POD_IG = {"builders": "builders.mu", "elevator": "elevatorpitch.mu"}
# pod id -> canonical pod name used in the Expense Master Content ledger
POD_EXPENSE_CANON = {
    "builders": "Builders.mu", "films": "Brand Films", "perf": "Performance Ads",
    "seriesc": "Series C", "offcampus": "Offcampus", "opm": "OPM",
    "coverage": "Coverage", "bharat": "PGP Bharat", "instagram": "Instagram",
}
# IG-only pods with NO production sheet — their "projects" come from the reels.
SOCIAL_PODS = {"elevator": {"name": "Elevator Pitch", "lead": "Anu Kiran"}}
IG_CACHE_DIR = Path(__file__).parent / "ig_cache"
CPV_TARGET_SHORT = 0.10   # Das Paisa: short form
CPV_TARGET_LONG = 1.0     # long form
# Which Apify field to treat as "Views". Instagram's in-app "Views" (2024+) is
# the play-inclusive number -> 'plays' (videoPlayCount). Set to 'legacy' to use
# the smaller videoViewCount instead. Flippable from cache, no re-scrape needed.
IG_VIEWS_METRIC = os.environ.get("IG_VIEWS_METRIC", "plays")


def _reel_views(r):
    if IG_VIEWS_METRIC == "legacy":
        return r.get("view_count_legacy") or r.get("plays") or 0
    return r.get("plays") or r.get("view_count_legacy") or 0
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


# The sheet's OWN status columns are the source of truth. We map their wording
# to clean, consistent labels and only fall back to date-inference when blank.
DELIVERY_MAP = {
    "uploaded": "Uploaded", "live": "Uploaded", "published": "Uploaded",
    "delivered": "Delivered",
    "editing": "In Editing", "in editing": "In Editing", "edit": "In Editing",
    "production": "In Production", "shoot done": "In Production", "shot": "In Production",
    "scripting": "Scripting", "script": "Scripting",
    "ideation": "Ideation", "idea": "Ideation",
    "tanked": "Tanked", "dropped": "Tanked", "shelved": "Tanked", "killed": "Tanked",
    "cancelled": "Cancelled", "canceled": "Cancelled",
}
SHOOT_MAP = {
    "shot": "Shot, Awaiting Edit", "scheduled": "Scheduled",
    "tbd": "Planned", "cancelled": "Cancelled", "canceled": "Cancelled",
}
# How each clean status rolls up for the studio overview + ordering on the page.
DONE = {"Uploaded", "Delivered", "Live"}
DEAD = {"Tanked", "Cancelled"}
STATUS_RANK = {"Ideation": 0, "Scripting": 1, "In Production": 2, "Shot, Awaiting Edit": 2,
               "In Editing": 3, "Scheduled": 4, "Planned": 4,
               "Uploaded": 5, "Delivered": 5, "Live": 5,
               "Pre-production / Ideation": 1, "Tanked": 9, "Cancelled": 9}


def canonical_status(d, tab):
    """Read the sheet's own status; fall back to date-inference only if blank."""
    sd = str(d.get("Status of Delivery", "") or "").strip()
    if sd:
        return DELIVERY_MAP.get(sd.lower(), sd)
    ss = str(d.get("Status of Shoot", "") or "").strip()
    if ss:
        return SHOOT_MAP.get(ss.lower(), ss)
    return compute_status(d, tab)


def _g(d, *keys):
    for k in keys:
        v = str(d.get(k, "") or "").strip()
        if v:
            return v
    return ""


def item_record(d, tab):
    return {
        "name": _g(d, "Video Name") or "(unnamed)",
        "status": canonical_status(d, tab),
        "type": _g(d, "Type of video"),
        "format": _g(d, "Formats"),
        "shoot_status": _g(d, "Status of Shoot"),
        "delivery_status": _g(d, "Status of Delivery"),
        "shoot_lead": _g(d, "Shoot Lead"),
        "crew": _g(d, "Team who shot it", "who shot it"),
        "editor": _g(d, "Editor's name"),
        "editing_team": _g(d, "Editing team"),
        "tat": _g(d, "TAT"),
        "shoot_date": _g(d, "Date of Shoot", "Shoot Date"),
        "edit_start": _g(d, "Edit Start Date"),
        "planned_delivery": _g(d, "Planned Date of Delivery", "Tentative date of delivery"),
        "actual_delivery": _g(d, "Actual Date of Delivery", "Date of Delivery"),
        "upload": _g(d, "Date of Upload", "YT Date of Upload", "YT UPLOAD"),
        "upload_link": _g(d, "Upload Link", "Upload link"),
        "dit": _g(d, "DIT Status"),
        "last_update": _g(d, "Last Update"),
        "remarks": _g(d, "Remarks by the Producer ", "Remarks by the Producer"),
    }


def _is_real_row(d):
    return bool(_g(d, "Video Name")) or bool(_g(d, "Status of Delivery"))


# ── reel URLs: pulled from the sheet's Upload Link hyperlinks (rich-text links) ──
_reel_cache = {}            # tab -> (timestamp, {video_name: url})
REEL_TTL = 3600
_SHORTCODE = re.compile(r"instagram\.com/(?:reel|p|tv)/([A-Za-z0-9_-]+)", re.I)


def reel_shortcode(url):
    m = _SHORTCODE.search(url or "")
    return m.group(1) if m else None


def reel_links(tab):
    """{Video Name: instagram_url} read from the tab's Upload Link hyperlinks.
    Returns {} silently if Google creds are unavailable (e.g. on a server
    without the service account)."""
    hit = _reel_cache.get(tab)
    if hit and time.time() - hit[0] < REEL_TTL:
        return hit[1]
    out = {}
    if not (CS_SHEET_ID and GOOGLE_CREDS and os.path.exists(GOOGLE_CREDS)):
        return out
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file(
            GOOGLE_CREDS, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        gc = gspread.authorize(creds)
        from urllib.parse import quote
        rng = quote("'" + tab.replace("'", "''") + "'")   # A1 sheet names with spaces need quoting
        url = (f"https://sheets.googleapis.com/v4/spreadsheets/{CS_SHEET_ID}"
               f"?ranges={rng}&fields=sheets(data(rowData(values(formattedValue,hyperlink))))")
        resp = gc.http_client.request("get", url)
        grid = resp.json()["sheets"][0]["data"][0].get("rowData", [])
        # locate header row + the Video Name / Upload Link columns
        rows = [[(v or {}) for v in (r.get("values") or [])] for r in grid]
        hdr_idx = max(range(min(6, len(rows))),
                      key=lambda i: sum(1 for c in rows[i] if c.get("formattedValue")), default=0)
        header = [c.get("formattedValue", "") for c in rows[hdr_idx]]

        def col(name):
            return header.index(name) if name in header else -1
        name_c, link_c = col("Video Name"), col("Upload Link")
        if name_c < 0 or link_c < 0:
            return out
        for r in rows[hdr_idx + 1:]:
            if name_c >= len(r) or link_c >= len(r):
                continue
            nm = r[name_c].get("formattedValue", "").strip()
            cell = r[link_c]
            link = cell.get("hyperlink") or (cell.get("formattedValue", "")
                                             if "instagram.com" in cell.get("formattedValue", "") else "")
            if nm and link and "instagram.com" in link:
                out[nm] = link
    except Exception as e:
        print(f"  reel_links({tab}) skipped: {e}")
    _reel_cache[tab] = (time.time(), out)
    return out


def pod_operations(tab, all_rows, win_rows, start=None, today=None):
    upload_cols = PODS[tab]["upload_cols"]
    has_upload = bool(upload_cols)
    final_status = "Live" if has_upload else "Delivered"
    cols = BASE_DATE_COLS + upload_cols

    # ---- FULL pod: every real row, real status, rich detail ----
    items = [item_record(d, tab) for d in all_rows if _is_real_row(d)]
    full_counts = {}
    for it in items:
        full_counts[it["status"]] = full_counts.get(it["status"], 0) + 1

    def sort_key(it):
        d = parse_date_str(it["upload"]) or parse_date_str(it["actual_delivery"]) \
            or parse_date_str(it["planned_delivery"]) or parse_date_str(it["shoot_date"])
        return (STATUS_RANK.get(it["status"], 6), -(d.toordinal() if d else 0))
    items.sort(key=sort_key)

    uploaded = sum(v for k, v in full_counts.items() if k in DONE)
    in_progress = sum(v for k, v in full_counts.items()
                      if k in ("Ideation", "Scripting", "In Production",
                               "In Editing", "Shot, Awaiting Edit"))
    tanked = sum(v for k, v in full_counts.items() if k in DEAD)

    # ---- WINDOWED: TAT + on-time + activity, for the studio overview ----
    win_counts = {}
    t_shoot_to_live, t_edit_to_deliv, t_shoot_to_edit, slippage = [], [], [], []
    items_with_shoot = items_at_final = 0
    for d in win_rows:
        st = canonical_status(d, tab)
        win_counts[st] = win_counts.get(st, 0) + 1
        shoot = parse_date_str(first_filled(d, SHOOT_DATE_COLS))
        edit = parse_date_str(first_filled(d, EDIT_START_COLS))
        planned = parse_date_str(first_filled(d, ["Planned Date of Delivery",
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
        if st in DONE:
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
        series, series_labels = _weekly_series(win_rows, cols, start, today)

    return {
        "munim_id": TAB_TO_MUNIM.get(tab),
        "pod": display_name(tab),
        "lead": PODS[tab]["lead"],
        # full-pod truth (matches the sheet exactly)
        "total": len(items),
        "uploaded": uploaded,
        "in_progress": in_progress,
        "tanked": tanked,
        "status_counts": full_counts,
        "items": items,
        # windowed signals (for the studio overview + trend)
        "active": len(win_rows),
        "shipped": items_at_final,
        "win_status_counts": win_counts,
        "on_time_rate": round(on_time / len(slippage), 2) if slippage else None,
        "conversion_rate": round(items_at_final / items_with_shoot, 2) if items_with_shoot else None,
        "avg_shoot_to_live": avg(t_shoot_to_live),
        "avg_edit_to_delivery": avg(t_edit_to_deliv),
        "avg_shoot_to_edit": avg(t_shoot_to_edit),
        "final_status_label": final_status,
        "series": series,
        "series_labels": series_labels,
    }


# ── Instagram stats (Apify scrape cache) + CPV from the expense sheet ────────
def _money(s):
    """'₹26,668.00' / 'Rs 5,631' -> float."""
    s = re.sub(r"[^0-9.]", "", str(s or ""))
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def load_ig_stats(handle):
    path = IG_CACHE_DIR / (handle + ".json")
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _reel_date(r):
    t = r.get("timestamp")
    if not t:
        return None
    try:
        return datetime.fromisoformat(str(t).replace("Z", "+00:00")).date()
    except Exception:
        return None


def pod_expenses(tag_substr):
    """Content-Expenses rows whose 'Pod' dropdown contains tag_substr, with each
    row's date + amount so callers can window them. Amount = with GST."""
    rows = fetch_latest_rows("Content Expenses", SALARIES_SHEET_ID)
    matched = []
    for r in rows:
        if tag_substr.lower() not in str(r.get("Pod", "") or "").lower():
            continue
        d = (parse_date_str(r.get("Date of Incurring the Expense"))
             or parse_date_str(r.get("Date of Shoot"))
             or parse_date_str(r.get("Date of Payment")))
        matched.append({"date": d, "amount": _money(r.get("Amount with GST")),
                        "pod": str(r.get("Pod", "")).strip()})
    by_month = {}
    for m in matched:
        if m["date"]:
            k = m["date"].strftime("%b %Y")
            by_month[k] = by_month.get(k, 0) + m["amount"]
    return {
        "total": round(sum(m["amount"] for m in matched)),
        "count": len(matched),
        "by_month": by_month,
        "tag": matched[0]["pod"] if matched else tag_substr,
        "rows": matched,
    }


def build_insta(pod_id, items, start=None, today=None):
    handle = POD_IG.get(pod_id)
    if not handle:
        return None
    ig = load_ig_stats(handle)
    if not ig or not ig.get("reels"):
        return None
    reels = ig["reels"]
    by_code = {r["shortCode"]: r for r in reels}
    # merge each reel's OWN total performance into the project items (window-independent)
    matched = 0
    for it in items:
        r = by_code.get(it.get("reel_code"))
        if r:
            it["views"] = _reel_views(r)
            it["likes"] = r["likes"]
            it["comments"] = r["comments"]
            matched += 1
    # window the reels by upload date for the headline stats
    if start and today:
        win = [r for r in reels if (_reel_date(r) and start <= _reel_date(r) <= today)]
    else:
        win = reels
    total_views = sum(_reel_views(r) for r in win)
    total_likes = sum(r["likes"] for r in win)
    total_comments = sum(r["comments"] for r in win)
    top = sorted(win, key=_reel_views, reverse=True)[:5]
    vbm = {}   # views by month (all reels) — for per-month CPV
    for r in reels:
        d = _reel_date(r)
        if d:
            mk = d.strftime("%Y-%m")
            vbm[mk] = vbm.get(mk, 0) + _reel_views(r)
    return {
        "handle": handle,
        "followers": ig.get("followers"),          # current / point-in-time (not windowable)
        "reels_count": len(win),
        "reels_total": len(reels),
        "total_views": total_views,
        "total_likes": total_likes,
        "total_comments": total_comments,
        "total_views_alltime": sum(_reel_views(r) for r in reels),
        "avg_views": round(total_views / len(win)) if win else 0,
        "engagement_rate": round((total_likes + total_comments) / total_views * 100, 2) if total_views else None,
        "windowed": bool(start and today),
        "views_by_month": vbm,
        "matched_to_sheet": matched,
        "scraped_at": ig.get("scraped_at"),
        "views_metric": IG_VIEWS_METRIC,
        "top": [{"code": r["shortCode"], "views": _reel_views(r), "likes": r["likes"],
                 "comments": r["comments"], "url": r.get("url"), "caption": r.get("caption", "")} for r in top],
    }


def build_cpv(pod_id, insta, start=None, today=None):
    """CPV = pod's Content-Expenses spend (from the new FY26/FY27 Expense Master)
    divided by views. Windowed when the range has logged spend, else all-time."""
    if not insta:
        return None
    canon = POD_EXPENSE_CANON.get(pod_id)
    if not canon:
        return None
    exp_all = content_pod_expense(canon)
    views_all = insta.get("total_views_alltime") or 0
    cpv = exp_all["total_gst"] / views_all if views_all else None
    # per-month CPV: spend that month ÷ views of reels posted that month
    vbm = insta.get("views_by_month", {})
    sbm = {m["month"]: m["amount"] for m in exp_all["by_month"]}
    by_month_cpv = []
    for mk in sorted(set(list(vbm) + list(sbm))):
        sp, vw = sbm.get(mk, 0), vbm.get(mk, 0)
        by_month_cpv.append({"month": mk, "spend": round(sp), "views": int(vw),
                             "cpv": round(sp / vw, 3) if (vw and sp) else None})
    return {
        "cost": exp_all["total_gst"], "views": views_all, "count": exp_all["count"],
        "tag": canon, "scope": "all-time",
        "cpv": round(cpv, 3) if cpv is not None else None,
        "target_short": CPV_TARGET_SHORT, "target_long": CPV_TARGET_LONG,
        "by_month": [{"date": m["month"], "value": m["amount"]} for m in exp_all["by_month"]],
        "by_month_cpv": by_month_cpv,
    }


def build_social_pod(pod_id, start=None, today=None):
    """An Instagram-only pod (no production sheet): its 'projects' are the reels
    themselves, pulled from the Apify cache."""
    handle = POD_IG.get(pod_id)
    cfg = SOCIAL_PODS.get(pod_id, {})
    ig = load_ig_stats(handle) if handle else None
    if not ig or not ig.get("reels"):
        return None
    reels = sorted(ig["reels"], key=lambda r: (r.get("timestamp") or ""), reverse=True)
    items = []
    for r in reels:
        d = _reel_date(r)
        cap = (r.get("caption") or "").strip().split("\n")[0][:70] or ("Reel " + r["shortCode"])
        items.append({
            "name": cap, "status": "Uploaded", "type": (r.get("product_type") or "Reel"),
            "format": "", "shoot_status": "", "delivery_status": "Uploaded",
            "shoot_lead": "", "crew": "", "editor": "", "editing_team": "", "tat": "",
            "shoot_date": "", "edit_start": "", "planned_delivery": "", "actual_delivery": "",
            "upload": d.isoformat() if d else "", "upload_link": r.get("url", ""),
            "dit": "", "last_update": "", "remarks": "",
            "views": _reel_views(r), "likes": r["likes"], "comments": r["comments"],
            "reel_url": r.get("url"), "reel_code": r["shortCode"],
        })
    status_counts = {"Uploaded": len(items)}
    ops = {
        "munim_id": pod_id, "pod": cfg.get("name", handle), "lead": cfg.get("lead", ""),
        "total": len(items), "uploaded": len(items), "in_progress": 0, "tanked": 0,
        "status_counts": status_counts, "items": items,
        "active": len(items), "shipped": len(items), "win_status_counts": status_counts,
        "on_time_rate": None, "conversion_rate": None, "avg_shoot_to_live": None,
        "avg_edit_to_delivery": None, "avg_shoot_to_edit": None, "final_status_label": "Uploaded",
        "series": [], "series_labels": [],
        "gallery_reels": [r["shortCode"] for r in reels if r.get("shortCode")],
        "social_only": True,
    }
    # real "reels per week" trend over the window
    if start and today:
        span = max((today - start).days, 7)
        weeks = max(1, min(12, (span + 6) // 7))
        buckets, labels = [0] * weeks, []
        for w in range(weeks):
            ws = start + timedelta(days=int(w * span / weeks))
            labels.append(ws.strftime("%d %b").lstrip("0"))
        for r in reels:
            d = _reel_date(r)
            if d and start <= d <= today:
                idx = max(0, min(weeks - 1, int((d - start).days / span * weeks)))
                buckets[idx] += 1
        ops["series"], ops["series_labels"] = buckets, labels

    insta = build_insta(pod_id, items, start, today)
    if insta:
        ops["insta"] = insta
        ops["cpv"] = build_cpv(pod_id, insta, start, today)   # None unless a tag exists
    return ops


# ── Expense Master: unified FY26 + FY27 spend, normalized to line items ──────
EXPENSE_FY26_SHEET_ID = "1fAOjd15uQyj1vLgCOuhzcSnN8pL73F_33xDIDZ0nVN0"
EXPENSE_FY27_SHEET_ID = "1lRCXckgd4BPDK8R0Wbik17m_0s2l6OEhaLEDKSVv6rg"
EXP_FY = {"FY26": EXPENSE_FY26_SHEET_ID, "FY27": EXPENSE_FY27_SHEET_ID}

# canonical pod names so a pod rolls up across both FYs' different labels
POD_CANON = [
    ("builders", "Builders.mu"), ("student stor", "Builders.mu"),
    ("coverage", "Coverage"), ("perf", "Performance Ads"),
    ("series c", "Series C"), ("offcampus", "Offcampus"), ("off campus", "Offcampus"),
    ("opm", "OPM"), ("bharat", "PGP Bharat"), ("masters of the market", "Masters of the Market"),
    ("brand film", "Brand Films"), ("brand comm", "Brand"), ("brand", "Brand"),
    ("socials - instagram", "Instagram"), ("instagram", "Instagram"),
    ("socials youtube", "YouTube"), ("youtube", "YouTube"),
    ("faculty", "Faculty Videos"), ("scratch", "Scratch"),
    ("a la carte", "A la Carte"), ("offline event", "Offline Events"),
    ("classroom", "Classroom"), ("nandini", "Nandini IP"), ("prospectus", "Prospectus"),
]


def canon_pod(raw):
    s = str(raw or "").strip().lower()
    if not s:
        return "Unassigned"
    for kw, name in POD_CANON:
        if kw in s:
            return name
    return str(raw).strip()


def _norm_status(s):
    s = str(s or "").strip().lower()
    if not s:
        return "Unknown"
    if "clear" in s or "paid" in s or "done" in s:
        return "Cleared"
    if "pending" in s or "review" in s or "process" in s or "hold" in s:
        return "Pending"
    return str(s).title()


def _firstkey(r, cols):
    for c in (cols if isinstance(cols, list) else [cols]):
        v = str(r.get(c, "") or "").strip()
        if v:
            return v
    return ""


def _firstdate(r, cols):
    for c in (cols if isinstance(cols, list) else [cols]):
        d = parse_date_str(r.get(c))
        if d:
            return d
    return None


# category -> source tabs per FY + the columns to read
EXP_CATS = [
    {"cat": "Content", "tabs": {"FY26": "Content Expense", "FY27": "Content Expenses"},
     "gst": "Amount with GST", "nogst": "Total (Without GST)",
     "date": "Date of Incurring the Expense", "pod": "Pod",
     "dept": ["Department", "Department = P&L"], "vendor": "Vendor Name",
     "desc": "Description of Work", "status": ["Payment Status ", "Payment Status"],
     "sub": {"Equipment": "Equipment", "Manpower": "Manpower", "TBL + Misc": "TBL + Misc",
             "Props": "Props", "Production": "Production Expenses"}},
    {"cat": "Influencer", "tabs": {"FY26": "Influencer Marketing", "FY27": "Influencer Marketing"},
     "gst": "Amount with GST", "nogst": "Total (Without GST)",
     "date": ["Date of Incurring the Expense = Date of Video going Live", "Date of Video going Live"],
     "vendor": "Agency", "desc": "Particulars",
     "dept": ["Department", "Department = P&L\n\n(Campaign executed for which department)", "CS - Vertical"],
     "status": ["Payment Status ", "Payment Status"]},
    {"cat": "Subscription", "tabs": {"FY26": "Subscription Purchase", "FY27": "Subscriptions"},
     "gst": "Amount", "nogst": "Amount", "date": "Date of Incurring the Expense",
     "pod": "Pod", "dept": ["Department", "Department = P&L"], "vendor": "Vendor Name",
     "desc": "Description of Work", "status": ["Payment Status ", "Payment Status"]},
    {"cat": "Asset", "tabs": {"FY26": "Asset Purchase", "FY27": "Asset Purchase"},
     "gst": "Amount", "nogst": "Amount", "date": "Date of Incurring the Expense",
     "pod": "Pod", "dept": ["Department", "Department = P&L"], "vendor": "Vendor Name",
     "desc": "Description of Work", "status": ["Payment Status ", "Payment Status"]},
    {"cat": "Reimbursement", "tabs": {"FY26": "Reimbursement", "FY27": "Reimbursement"},
     "gst": "Amount", "nogst": "Amount",
     "date": ["Date of Incurring\n the Expense", "Date of Incurring the Expense"],
     "pod": "Pod", "dept": ["Department", "Department = P&L\n\n(Default Central Functions here)"],
     "vendor": ["Vendor Name", "Employee Name"], "desc": "Description of Work",
     "status": ["Payment Status", "Payment Status "]},
    {"cat": "Retainer", "tabs": {"FY27": "Retainer"},   # FY26 retainer comes from Master Payments
     "gst": "Amount (₹)", "nogst": "Amount (₹)", "date": "Date of Incurring the Expense",
     "pod": "Pod", "dept": ["Department = P&L"], "vendor": "Vendor Name",
     "desc": "Description of Work", "status": ["Payment Status ", "Payment Status"]},
    {"cat": "One-Time", "tabs": {"FY26": "One Time Payments"},
     "gst": "Amount (₹) (without GST)", "nogst": "Amount (₹) (without GST)",
     "date": "Date of Incurring the Expense", "vendor": "Vendor Name",
     "desc": "Description of Work", "status": ["Payment Status ", "Payment Status"]},
]

_MONTHS = ["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]


def _fy26_retainer_from_master():
    """FY26 Retainer ledger tab is malformed; take the clean monthly Retainer
    totals from FY26 Master Payments instead."""
    out = []
    rows = fetch_latest_rows("Master Payments", EXPENSE_FY26_SHEET_ID)
    for r in rows:
        mlabel = str(r.get("Month", "") or "").strip()
        d = parse_date_str(mlabel) or parse_date_str("1 " + mlabel)
        amt = _money(r.get("Retainer"))
        if d and amt and "total" not in mlabel.lower():
            out.append({"fy": "FY26", "cat": "Retainer", "date": d.isoformat(),
                        "month": d.strftime("%Y-%m"), "pod": "Brand", "dept": "BRAND",
                        "vendor": "Retainer partners", "desc": "Monthly retainer",
                        "gst": round(amt), "nogst": round(amt), "status": "Cleared", "sub": None})
    return out


# plausible date window per FY (Apr 1 -> following Mar 31, with a little grace)
FY_WINDOW = {"FY26": (date(2025, 4, 1), date(2026, 6, 30)),
             "FY27": (date(2026, 4, 1), date(2027, 6, 30))}
# Master Payments category columns -> clean labels (these are the OFFICIAL totals)
MP_CATS = {
    "Content Expenses\n(Total - A+B+C+D+E)": "Content", "Retainer": "Retainer",
    "Asset Purchase": "Asset", "Subscription Purchase ": "Subscription",
    "Influencer Marketing": "Influencer", "Infleuncer Marketing": "Influencer",
    "Reimbursements": "Reimbursement",
}
MP_SUB = {"Equipment Rental (A)": "Equipment", "Manpower (B)": "Manpower",
          "TBL + Misc (C)": "TBL + Misc", "Props (D)": "Props",
          "Production Expenses (one time payments like production house & crew etc) - (E)": "Production"}


def _master_payments(fy):
    """Official monthly x category rollup from the Master Payments tab."""
    rows = fetch_latest_rows("Master Payments", EXP_FY[fy])
    out = []
    for r in rows:
        mlabel = str(r.get("Month", "") or "").strip()
        if not mlabel or "total" in mlabel.lower():
            continue
        d = parse_date_str(mlabel) or parse_date_str("1 " + mlabel)
        cats = {lab: round(_money(r.get(col))) for col, lab in MP_CATS.items() if _money(r.get(col))}
        sub = {lab: round(_money(r.get(col))) for col, lab in MP_SUB.items() if _money(r.get(col))}
        total = round(_money(r.get("Total")))
        if total or cats:
            out.append({"month_label": mlabel, "month": d.strftime("%Y-%m") if d else "",
                        "cats": cats, "content_sub": sub, "total": total})
    return out


def _content_lines():
    """Clean, pod-attributed line items from the Content Expenses ledger of both
    FYs (one ledger per FY -> no double counting). The granular detail layer."""
    cfg = EXP_CATS[0]  # Content
    lo, hi = date(2024, 1, 1), date(2027, 12, 31)
    lines = []
    for fy, tab in cfg["tabs"].items():
        for r in fetch_latest_rows(tab, EXP_FY[fy]):
            g = _money(_firstkey(r, cfg["gst"]))
            n = _money(_firstkey(r, cfg["nogst"]))
            if g == 0 and n == 0:
                continue
            d = _firstdate(r, cfg["date"])
            if d and not (lo <= d <= hi):
                d = None
            lines.append({
                "fy": fy, "date": d.isoformat() if d else "",
                "month": d.strftime("%Y-%m") if d else "",
                "pod": canon_pod(_firstkey(r, cfg["pod"])),
                "dept": _firstkey(r, cfg["dept"]) or "—",
                "vendor": _firstkey(r, cfg["vendor"]) or "—",
                "desc": _firstkey(r, cfg["desc"])[:90],
                "gst": round(g or n), "nogst": round(n or g),
                "status": _norm_status(_firstkey(r, cfg["status"])),
                "sub": {k: round(_money(r.get(v))) for k, v in cfg["sub"].items()},
            })
    return lines


def _ledger_master(fy):
    """Fallback monthly x category rollup from the per-category ledgers, used
    when an FY's Master Payments tab hasn't been filled in yet (e.g. FY27)."""
    bym = {}
    for cfg in EXP_CATS:
        tab = cfg["tabs"].get(fy)
        if not tab:
            continue
        for r in fetch_latest_rows(tab, EXP_FY[fy]):
            amt = _money(_firstkey(r, cfg["gst"])) or _money(_firstkey(r, cfg["nogst"]))
            d = _firstdate(r, cfg["date"])
            if not amt or not d or not (FY_WINDOW[fy][0] <= d <= FY_WINDOW[fy][1]):
                continue
            mk = d.strftime("%Y-%m")
            bym.setdefault(mk, {}).setdefault(cfg["cat"], 0.0)
            bym[mk][cfg["cat"]] += amt
    out = []
    for mk in sorted(bym):
        cats = {c: round(v) for c, v in bym[mk].items()}
        out.append({"month": mk, "month_label": mk, "cats": cats,
                    "content_sub": {}, "total": round(sum(cats.values()))})
    return out


_clines = {}   # ts -> lines


def content_lines_cached():
    hit = _clines.get("v")
    if hit and time.time() - hit[0] < 180:
        return hit[1]
    lines = _content_lines()
    _clines["v"] = (time.time(), lines)
    return lines


def content_pod_expense(canon, start=None, today=None):
    """One pod's Content-Expenses spend (from the FY26/FY27 Expense Master),
    optionally windowed by date. Used by CPV and the per-pod expense panels."""
    lines = [l for l in content_lines_cached() if l["pod"] == canon]
    if start and today:
        lines = [l for l in lines
                 if parse_date_str(l["date"]) and start <= parse_date_str(l["date"]) <= today]
    bym, vend = {}, {}
    for l in lines:
        if l["month"]:
            bym[l["month"]] = bym.get(l["month"], 0) + l["gst"]
        vend[l["vendor"]] = vend.get(l["vendor"], 0) + l["gst"]
    return {
        "total_gst": round(sum(l["gst"] for l in lines)),
        "total_nogst": round(sum(l["nogst"] for l in lines)),
        "count": len(lines),
        "by_month": [{"month": m, "amount": round(bym[m])} for m in sorted(bym)],
        "top_vendors": sorted(({"vendor": k, "amount": round(v)} for k, v in vend.items()),
                              key=lambda x: -x["amount"])[:6],
        "lines": sorted(lines, key=lambda l: l["date"] or "", reverse=True)[:10],
    }


def build_salaries():
    """Sensitive salary payload (per-employee CTC + pod-wise monthly). Returned
    in plaintext here; encrypted with the PIN before it ever leaves the build."""
    emp_rows = fetch_latest_rows("Salary Data", SALARIES_SHEET_ID)
    pod_rows = fetch_latest_rows("Pod-Wise Salary | MoM Split", SALARIES_SHEET_ID)
    employees = []
    for r in emp_rows:
        name = _g(r, "NAME")
        if not name:
            continue
        employees.append({
            "name": name, "role": _g(r, "DESIGNATION", "ROLE"),
            "pod": _g(r, "Pod Name"), "dept": _g(r, "DEPARTMENT"),
            "total_ctc": round(_money(r.get(" TOTAL CTC ") or r.get("TOTAL CTC"))),
            "fixed": round(_money(r.get(" FIXED CTC ") or r.get("FIXED CTC"))),
            "variable": round(_money(r.get(" VARIABLE ") or r.get("VARIABLE"))),
            "doj": _g(r, "DOJ"), "manager": _g(r, "REPORTING MANAGER"),
        })
    months = ["April ", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec", "Jan", "Feb", "March"]
    sumcols = ["SUM of " + m for m in months]
    latest = next((c for c in reversed(sumcols) if any(_money(r.get(c)) for r in pod_rows)), None)
    by_pod = []
    for r in pod_rows:
        pod = _g(r, "Pod Name")
        amt = _money(r.get(latest)) if latest else 0
        if pod and amt:
            by_pod.append({"pod": pod, "monthly": round(amt)})
    by_pod.sort(key=lambda x: -x["monthly"])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "headcount": len(employees),
        "total_monthly": sum(p["monthly"] for p in by_pod),
        "latest_month": (latest or "").replace("SUM of", "").strip(),
        "by_pod": by_pod,
        "employees": sorted(employees, key=lambda e: -e["total_ctc"]),
    }


def build_expense():
    master = {}
    master_source = {}
    for fy in EXP_FY:
        mp = _master_payments(fy)
        if mp:
            master[fy], master_source[fy] = mp, "Master Payments (official)"
        else:
            master[fy], master_source[fy] = _ledger_master(fy), "ledgers (Master Payments not yet filled)"
    lines = _content_lines()
    months = sorted({l["month"] for l in lines if l["month"]}
                    | {m["month"] for fy in master for m in master[fy] if m["month"]})
    fy_totals = {fy: sum(m["total"] for m in master[fy]) for fy in master}
    return {"generated_at": datetime.now().isoformat(timespec="seconds"),
            "fys": ["FY26", "FY27"], "months": months,
            "master": master, "master_source": master_source, "fy_totals": fy_totals,
            "lines": lines, "line_count": len(lines)}


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
        all_rows = fetch_latest_rows(tab)
        win_rows = [r for r in all_rows if in_range(r, start, today, cols)]
        ops = pod_operations(tab, all_rows, win_rows, start, today)
        # attach real Instagram reel URLs from the sheet's Upload Link hyperlinks
        links = reel_links(tab)
        if links:
            reels = []
            for it in ops["items"]:
                u = links.get(it["name"])
                if u:
                    it["reel_url"] = u
                    code = reel_shortcode(u)
                    if code:
                        it["reel_code"] = code
                        reels.append(code)
            ops["gallery_reels"] = reels
        # Instagram performance (Apify) + CPV (expense sheet ÷ views)
        mid = ops["munim_id"]
        if mid and mid in POD_IG:
            insta = build_insta(mid, ops["items"], start, today)
            if insta:
                ops["insta"] = insta
                ops["cpv"] = build_cpv(mid, insta, start, today)
        # per-pod spend from the Expense Master (shows on the pod's own page)
        if mid and mid in POD_EXPENSE_CANON:
            pe = content_pod_expense(POD_EXPENSE_CANON[mid])
            if pe["count"]:
                ops["pod_expense"] = pe
        if ops["munim_id"]:
            pods[ops["munim_id"]] = ops
        sc = ops["win_status_counts"]
        totals["active"] += ops["active"]
        totals["live_or_delivered"] += sum(v for k, v in sc.items() if k in DONE)
        totals["delivered_pending_upload"] += sc.get("Delivered, Awaiting Upload", 0)
        totals["in_editing"] += sc.get("In Editing", 0)
        totals["shot_awaiting_edit"] += sc.get("In Production", 0) + sc.get("Shot, Awaiting Edit", 0)
        totals["pre_production"] += sc.get("Ideation", 0) + sc.get("Scripting", 0) \
            + sc.get("Planned", 0) + sc.get("Scheduled", 0) + sc.get("Pre-production / Ideation", 0)
        ranked.append({"pod": ops["pod"], "munim_id": ops["munim_id"],
                       "active": ops["active"], "shipped": ops["shipped"]})

    ranked.sort(key=lambda x: x["shipped"], reverse=True)

    # Instagram-only pods (no production sheet) — e.g. Elevator Pitch
    for spid in SOCIAL_PODS:
        sp = build_social_pod(spid, start, today)
        if sp:
            pods[spid] = sp

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "period": {"days": days, "from": start.isoformat(), "to": today.isoformat()},
        "totals": totals,
        "pods": pods,
        "ranked": ranked,
        "coverage": build_coverage(days),
        "brand": build_brand(),
        "live_pod_ids": [m for m in TAB_TO_MUNIM.values()] + list(SOCIAL_PODS.keys()),
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
            if path == "/api/expense":
                return self._send(200, build_expense())
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
