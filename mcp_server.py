"""
MCP server for the Creative Studio Operations dashboard.
Exposes the same data the dashboard reads — pod status, Coverage calendar,
crew utilisation — as MCP tools that Claude Desktop can call.

Set up:
1. pip install mcp psycopg2-binary python-dotenv  (already done in this venv)
2. Add this server to Claude Desktop's config (instructions in the assistant
   message that ships this file).
3. Restart Claude Desktop. Ask it questions like:
     "What's happening in Builders.mu this month?"
     "What shoots does Coverage have today?"
     "Who has worked the most hours this week?"
Claude Desktop will pick the right tool, call this server, and answer.
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Always load .env from THIS script's folder, not the cwd Claude Desktop
# happens to launch from. Without this the server starts in Claude Desktop
# but cannot find DATABASE_URL.
load_dotenv(Path(__file__).parent / ".env")

DATABASE_URL = os.environ["DATABASE_URL"]
COVERAGE_SHEET_ID = "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM"

# Pod definitions mirror dashboard.py
PODS = {
    "Builders.mu":              {"upload_cols": ["Date of Upload"],                   "lead": "Raja Kumar"},
    "Brand/Ad films":           {"upload_cols": [],                                   "lead": "Devansh Kotak"},
    "PGP Bharat IG":            {"upload_cols": ["Date of Upload"],                   "lead": "Sabhya Sharma"},
    "Perf Ads":                 {"upload_cols": [],                                   "lead": "Devansh Kotak"},
    "YT - Podcasts (Series C)": {"upload_cols": ["YT Date of Upload", "YT UPLOAD"],   "lead": "Ishika Aggarwal"},
    "YT - Off Campus":          {"upload_cols": ["YT Date of Upload", "YT UPLOAD"],   "lead": "Ishika Aggarwal"},
    "YT - Masters Of The Market": {"upload_cols": ["YT Date of Upload", "YT UPLOAD"], "lead": "Ishika Aggarwal"},
    "YT - Family Business":     {"upload_cols": ["YT Date of Upload", "YT UPLOAD"],   "lead": "Ishika Aggarwal"},
}

POD_DISPLAY = {
    "PGP Bharat IG": "Bharat.mu",
    "Brand/Ad films": "Brand/Ad Films",
    "Perf Ads": "Performance Ads",
}

BASE_DATE_COLS = [
    "Date of Shoot", "Edit Start Date",
    "Planned Date of Delivery", "Actual Date of Delivery",
    "Date of Upload",
]

DELIVERED_COLS = [
    "Actual Date of Delivery", "Date of Delivery",
    "Delivered Date", "Final Delivery Date", "Delivery Date",
]
EDIT_START_COLS = ["Edit Start Date", "Editing Start Date", "Edit Date", "Edit Start"]
SHOOT_DATE_COLS = ["Date of Shoot", "Shoot Date", "Shooting Date", "Date Shot"]


def display_name(pod: str) -> str:
    return POD_DISPLAY.get(pod, pod)


def parse_date_str(s) -> Optional[date]:
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d",
                "%d %b %Y", "%d %B %Y", "%d/%m/%y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_clock(t) -> Optional[datetime.time]:
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


def first_filled(row: dict, cols: list) -> str:
    for c in cols:
        v = str(row.get(c, "") or "").strip()
        if v:
            return v
    return ""


def compute_status(row: dict, pod: str) -> str:
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


def get_conn():
    return psycopg2.connect(DATABASE_URL)


def fetch_latest_pod_rows(pod: str) -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.id = (
                    select id from snapshots
                    where tab_name = %s
                    order by captured_at desc limit 1
                )
                and not sr.is_divider
                order by sr.row_number
                """,
                (pod,),
            )
            return [r["data"] for r in cur.fetchall()]
    finally:
        conn.close()


def fetch_latest_coverage_rows() -> list:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                """
                select sr.data
                from snapshot_rows sr
                join snapshots s on sr.snapshot_id = s.id
                where s.sheet_id = %s
                  and s.id = (
                      select id from snapshots
                      where sheet_id = %s
                      order by captured_at desc limit 1
                  )
                  and not sr.is_divider
                order by sr.row_number
                """,
                (COVERAGE_SHEET_ID, COVERAGE_SHEET_ID),
            )
            return [r["data"] for r in cur.fetchall()]
    finally:
        conn.close()


def in_range(row: dict, start: date, end: date, cols: list) -> bool:
    for c in cols:
        d = parse_date_str(row.get(c))
        if d and start <= d <= end:
            return True
    return False


# ---------------- MCP server ----------------

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("CS Operations Dashboard")


@mcp.tool()
def list_pods() -> str:
    """List every Creative Studio production pod we currently track,
    along with its lead. Use this first if the user asks 'what pods exist?'"""
    pods = [
        {
            "name": display_name(p),
            "internal_tab_name": p,
            "lead": meta["lead"],
            "has_upload_step": bool(meta["upload_cols"]),
        }
        for p, meta in PODS.items()
    ]
    return json.dumps(pods, indent=2)


@mcp.tool()
def get_pod_summary(pod_name: str, days_back: int = 30) -> str:
    """Status breakdown for one pod over the last N days.

    pod_name: the display or tab name. Examples: 'Builders.mu', 'Bharat.mu',
              'Performance Ads', 'YT - Podcasts (Series C)'.
    days_back: window length (default 30, max 365).
    """
    # Resolve display name to tab name
    pod = pod_name
    for tab, disp in POD_DISPLAY.items():
        if disp == pod_name:
            pod = tab
            break
    if pod not in PODS:
        return json.dumps({"error": f"Unknown pod '{pod_name}'. Call list_pods to see options."})

    days_back = max(1, min(days_back, 365))
    end = date.today()
    start = end - timedelta(days=days_back)
    upload_cols = PODS[pod]["upload_cols"]
    cols = BASE_DATE_COLS + upload_cols

    rows = fetch_latest_pod_rows(pod)
    in_range_rows = [r for r in rows if in_range(r, start, end, cols)]

    counts = {}
    for r in in_range_rows:
        s = compute_status(r, pod)
        counts[s] = counts.get(s, 0) + 1

    return json.dumps({
        "pod": display_name(pod),
        "lead": PODS[pod]["lead"],
        "period": f"{start.isoformat()} to {end.isoformat()} ({days_back} days)",
        "total_active": len(in_range_rows),
        "status_breakdown": counts,
    }, indent=2)


@mcp.tool()
def get_pod_videos(pod_name: str, status: Optional[str] = None,
                   days_back: int = 30, limit: int = 20) -> str:
    """List the actual videos in a pod, optionally filtered by status.

    pod_name: pod to look at.
    status: optional, e.g. 'Live', 'In Editing', 'Delivered, Awaiting Upload',
            'Shot, Awaiting Edit', 'Pre-production / Ideation'. Omit for all.
    days_back: window length.
    limit: max number of videos to return.
    """
    pod = pod_name
    for tab, disp in POD_DISPLAY.items():
        if disp == pod_name:
            pod = tab
            break
    if pod not in PODS:
        return json.dumps({"error": f"Unknown pod '{pod_name}'."})

    days_back = max(1, min(days_back, 365))
    limit = max(1, min(limit, 200))
    end = date.today()
    start = end - timedelta(days=days_back)
    upload_cols = PODS[pod]["upload_cols"]
    cols = BASE_DATE_COLS + upload_cols

    rows = fetch_latest_pod_rows(pod)
    videos = []
    for r in rows:
        if not in_range(r, start, end, cols):
            continue
        st = compute_status(r, pod)
        if status and st != status:
            continue
        videos.append({
            "name": (r.get("Video Name") or "").strip() or "(unnamed)",
            "status": st,
            "lead": r.get("Lead") or r.get("POC in Charge") or r.get("POC in charge", ""),
            "shoot_date": r.get("Date of Shoot", ""),
            "edit_start": r.get("Edit Start Date", ""),
            "planned_delivery": r.get("Planned Date of Delivery") or r.get("Tentative date of delivery", ""),
            "actual_delivery": r.get("Actual Date of Delivery") or r.get("Date of Delivery", ""),
            "upload_date": (
                r.get("Date of Upload")
                or r.get("YT Date of Upload")
                or r.get("YT UPLOAD", "")
            ),
            "upload_link": r.get("Upload Link") or r.get("Upload link", ""),
        })
        if len(videos) >= limit:
            break

    return json.dumps({
        "pod": display_name(pod),
        "filter_status": status,
        "period": f"{start.isoformat()} to {end.isoformat()}",
        "count": len(videos),
        "videos": videos,
    }, indent=2)


@mcp.tool()
def get_overall_summary(days_back: int = 30) -> str:
    """Cross-pod aggregate: how many videos are active / live / in editing
    across every production pod for the last N days."""
    days_back = max(1, min(days_back, 365))
    end = date.today()
    start = end - timedelta(days=days_back)

    out = {"period": f"{start.isoformat()} to {end.isoformat()}",
           "totals": {"active": 0, "live_or_delivered": 0, "in_editing": 0,
                      "delivered_pending_upload": 0, "shot_awaiting_edit": 0,
                      "pre_production": 0},
           "by_pod": []}

    for pod in PODS:
        upload_cols = PODS[pod]["upload_cols"]
        has_upload = bool(upload_cols)
        cols = BASE_DATE_COLS + upload_cols
        rows = fetch_latest_pod_rows(pod)
        active = 0
        statuses = {}
        for r in rows:
            if not in_range(r, start, end, cols):
                continue
            active += 1
            s = compute_status(r, pod)
            statuses[s] = statuses.get(s, 0) + 1

        out["totals"]["active"] += active
        out["totals"]["live_or_delivered"] += statuses.get("Live", 0) + statuses.get("Delivered", 0)
        out["totals"]["delivered_pending_upload"] += statuses.get("Delivered, Awaiting Upload", 0)
        out["totals"]["in_editing"] += statuses.get("In Editing", 0)
        out["totals"]["shot_awaiting_edit"] += statuses.get("Shot, Awaiting Edit", 0)
        out["totals"]["pre_production"] += statuses.get("Pre-production / Ideation", 0)

        out["by_pod"].append({
            "pod": display_name(pod),
            "lead": PODS[pod]["lead"],
            "active": active,
            "statuses": statuses,
        })

    return json.dumps(out, indent=2)


@mcp.tool()
def get_coverage_today() -> str:
    """List Coverage shoots scheduled for today."""
    today = date.today()
    rows = fetch_latest_coverage_rows()
    today_shoots = []
    for r in rows:
        d = parse_date_str(r.get("Date"))
        if d == today:
            today_shoots.append({
                "subject": r.get("Shoot Subject", ""),
                "department": r.get("Department", ""),
                "time": f"{r.get('Time (From)', '')} to {r.get('Time (Till)', '')}",
                "location": r.get("Location", ""),
                "lead": r.get("Shoot Lead", ""),
                "crew": r.get("who shot it", ""),
                "requested_by": r.get("Who Requested", ""),
            })
    return json.dumps({"date": today.isoformat(), "count": len(today_shoots),
                       "shoots": today_shoots}, indent=2)


@mcp.tool()
def get_coverage_upcoming(days_ahead: int = 7) -> str:
    """Coverage shoots in the next N days (default 7, max 60)."""
    days_ahead = max(1, min(days_ahead, 60))
    today = date.today()
    end = today + timedelta(days=days_ahead)
    rows = fetch_latest_coverage_rows()
    upcoming = []
    for r in rows:
        d = parse_date_str(r.get("Date"))
        if d and today <= d <= end:
            upcoming.append({
                "date": d.isoformat(),
                "subject": r.get("Shoot Subject", ""),
                "department": r.get("Department", ""),
                "time": f"{r.get('Time (From)', '')} to {r.get('Time (Till)', '')}",
                "location": r.get("Location", ""),
                "lead": r.get("Shoot Lead", ""),
                "crew": r.get("who shot it", ""),
            })
    upcoming.sort(key=lambda x: x["date"])
    return json.dumps({
        "period": f"today to {end.isoformat()}",
        "count": len(upcoming),
        "shoots": upcoming,
    }, indent=2)


def _shoot_duration_hours(row: dict) -> float:
    t1 = parse_clock(row.get("Time (From)"))
    t2 = parse_clock(row.get("Time (Till)"))
    if not t1 or not t2:
        return 0.0
    d = date.today()
    s = datetime.combine(d, t1)
    e = datetime.combine(d, t2)
    if e <= s:
        e += timedelta(days=1)
    return (e - s).total_seconds() / 3600.0


def _parse_crew(s: str) -> list:
    if not s:
        return []
    return [p.strip() for p in str(s).replace("&", ",").split(",") if p.strip()]


@mcp.tool()
def get_crew_hours(days_back: int = 30) -> str:
    """Total hours each crew member worked over the last N days, ranked.
    Each person on a shoot gets credited the full shoot duration."""
    days_back = max(1, min(days_back, 365))
    today = date.today()
    start = today - timedelta(days=days_back)
    rows = fetch_latest_coverage_rows()
    hours = {}
    for r in rows:
        d = parse_date_str(r.get("Date"))
        if not d or not (start <= d <= today):
            continue
        dur = _shoot_duration_hours(r)
        if dur <= 0:
            continue
        for person in _parse_crew(r.get("who shot it", "")):
            hours[person] = hours.get(person, 0) + dur

    ranked = sorted(
        [{"crew": k, "hours": round(v, 1)} for k, v in hours.items()],
        key=lambda x: x["hours"], reverse=True,
    )
    return json.dumps({
        "period": f"{start.isoformat()} to {today.isoformat()} ({days_back} days)",
        "ranked": ranked,
    }, indent=2)


if __name__ == "__main__":
    mcp.run()
