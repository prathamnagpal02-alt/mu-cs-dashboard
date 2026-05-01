"""
Snapshots every connected Google Sheet into Supabase in one run.
- CS Mastersheet: 8 priority pod tabs
- Coverage Main Shoot Calendar: 2026 tab
- Salaries & Expenses FY26: salary roll + expense ledgers
- AOP FY27: per-division target tabs
- Newsletters: 3 newsletter tabs
- ORM: Reddit + Quora tabs

Uses batched inserts (execute_values) and per-tab fresh connections so the
Supabase Transaction Pooler does not drop the connection halfway through.
Run with: python snapshot_all.py
"""

import os
import sys
import time
from datetime import datetime

# UTF-8 output for tab names containing rupee, etc.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import gspread
import psycopg2
from psycopg2.extras import Json, execute_values
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

KEY_FILE = os.environ["GOOGLE_CREDENTIALS_PATH"]
CS_MASTERSHEET_ID = os.environ["GOOGLE_SHEET_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]

COVERAGE_SHEET_ID = "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM"
SALARIES_SHEET_ID = "1eok2NGU7gzhM7sGraFcyeqO-AMyyQXzj4AxhV-bXUrw"
AOP_SHEET_ID = "16tKPWj33VN1Y7PGRf1LqNyYHOw6gLIJErG3f6nfY5AU"
NEWSLETTERS_SHEET_ID = "1HXFklF6_RJ3L_lSDe0AUr1xdxKQ-c9ngYvvUosyFI94"
ORM_SHEET_ID = "1kBFoCe28vrkVqnaRyn3dqNxBs_KSZf8MuZcpVp_vAXE"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

PRIORITY_TABS = [
    "Builders.mu",
    "Brand/Ad films",
    "PGP Bharat IG",
    "Perf Ads",
    "YT - Podcasts (Series C)",
    "YT - Off Campus",
    "YT - Masters Of The Market",
    "YT - Family Business",
]

# Specific tabs to snapshot from the supplementary sheets.
SALARIES_TABS = [
    "Salary Data",
    "Pod-Wise Salary | MoM Split",
    "Content Expenses",
    "Retainer",
    "Subscription Purchase",
    "Asset Purchase",
    "Reimbursement",
    "Influencer Marketing",
    "Master Payments",
]

AOP_TABS = [
    "FY25-26 Actuals (\u20b910.6 Cr)",
    "Digital (\u20b928.25 Cr)",
    "Publishing (\u20b92 Cr)",
    "Offline Events (\u20b95 Cr)",
    "New Initiatives (\u20b95 Cr)",
    "ROI Summary",
    "Consolidated (\u20b940.25 Cr)",
    "Assumptions & Notes",
]

NEWSLETTERS_TABS = ["MU newsletters", "Swati NL", "Nandini NL"]

ORM_TABS = ["Reddit", "Quora"]

INSERT_PAGE_SIZE = 500


def row_to_dict(headers: list, row: list) -> dict:
    result = {}
    for i, value in enumerate(row):
        key = headers[i] if i < len(headers) and headers[i] else f"col_{i+1}"
        result[key] = value
    return result


def is_divider_row(headers: list, row: list) -> bool:
    try:
        lead_idx = headers.index("Lead")
    except ValueError:
        return False
    if lead_idx >= len(row):
        return False
    return row[lead_idx].strip() == ""


def detect_header_row(all_rows: list, max_scan: int = 6) -> int:
    """Some tabs (Series C, Perf Ads) put a section title or merged-cell banner
    on row 1 and the actual column headers on row 2 or 3. Scan the first few
    rows and return the index of the row that looks most like a header
    (i.e. has the most non-blank cells). Falls back to 0."""
    best_idx = 0
    best_count = -1
    for i, row in enumerate(all_rows[:max_scan]):
        non_blank = sum(1 for v in row if str(v).strip())
        if non_blank > best_count:
            best_count = non_blank
            best_idx = i
    return best_idx


def open_db():
    return psycopg2.connect(DATABASE_URL)


def snapshot_tab(sheet, tab_name: str, sheet_id: str):
    """Reads a tab from the given sheet, opens a fresh DB connection,
    writes the snapshot plus all its rows in a batched INSERT."""
    try:
        worksheet = sheet.worksheet(tab_name)
    except gspread.exceptions.WorksheetNotFound:
        print(f"  SKIP: tab '{tab_name}' not found.")
        return

    all_rows = worksheet.get_all_values()
    if not all_rows:
        print(f"  SKIP: tab '{tab_name}' is empty.")
        return

    header_idx = detect_header_row(all_rows)
    headers = all_rows[header_idx]
    data_rows = all_rows[header_idx + 1:]
    if header_idx > 0:
        print(f"  Header detected at row {header_idx + 1} (rows above were section banners).")

    payload = [
        (rn, Json(row_to_dict(headers, row)), is_divider_row(headers, row))
        for rn, row in enumerate(data_rows, start=1)
    ]

    conn = open_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into snapshots (sheet_id, tab_name, row_count, headers)
                values (%s, %s, %s, %s)
                returning id
                """,
                (sheet_id, tab_name, len(data_rows), Json(headers)),
            )
            snapshot_id = cur.fetchone()[0]

            execute_values(
                cur,
                """
                insert into snapshot_rows
                    (snapshot_id, row_number, data, is_divider)
                values %s
                """,
                [(snapshot_id, rn, data, div) for rn, data, div in payload],
                page_size=INSERT_PAGE_SIZE,
            )
        conn.commit()
        print(f"  OK: {len(data_rows)} rows, snapshot_id={snapshot_id}")
    except psycopg2.OperationalError as e:
        conn.close()
        raise RuntimeError(
            f"Database connection dropped while writing '{tab_name}'."
        ) from e
    finally:
        if not conn.closed:
            conn.close()


def find_year_tab(sheet, year_hint: str = "2026"):
    """Returns the worksheet whose title contains the year hint, or None."""
    for ws in sheet.worksheets():
        if year_hint in ws.title:
            return ws
    return None


def main():
    start = datetime.now()
    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)

    # --- CS Mastersheet (production pods) ---
    print("\n=== CS Mastersheet (production pods) ===")
    cs_sheet = gc.open_by_key(CS_MASTERSHEET_ID)
    for tab_name in PRIORITY_TABS:
        print(f"\n[{tab_name}]")
        for attempt in range(2):
            try:
                snapshot_tab(cs_sheet, tab_name, CS_MASTERSHEET_ID)
                break
            except RuntimeError as e:
                if attempt == 0:
                    print(f"  WARN: {e} Retrying once...")
                    time.sleep(2)
                else:
                    print(f"  ERROR: {e} Moving on.")

    # --- Coverage sheet (separate desk, separate sheet, year-tabbed) ---
    print("\n=== Coverage sheet (Shashank, Non Fiction) ===")
    try:
        cov_sheet = gc.open_by_key(COVERAGE_SHEET_ID)
    except gspread.exceptions.APIError as e:
        print("  SKIP: Coverage sheet not accessible.")
        print(f"  Raw error: {e}")
    else:
        year_tab = find_year_tab(cov_sheet, "2026")
        if year_tab is None:
            print("  SKIP: no tab with '2026' in its name found in the Coverage sheet.")
        else:
            print(f"\n[Coverage / {year_tab.title}]")
            for attempt in range(2):
                try:
                    snapshot_tab(cov_sheet, year_tab.title, COVERAGE_SHEET_ID)
                    break
                except RuntimeError as e:
                    if attempt == 0:
                        print(f"  WARN: {e} Retrying once...")
                        time.sleep(2)
                    else:
                        print(f"  ERROR: {e} Moving on.")

    # --- Salaries & Expenses FY26 ---
    snapshot_supplementary_sheet(
        gc, SALARIES_SHEET_ID, "Salaries & Expenses FY26", SALARIES_TABS,
    )

    # --- AOP FY27 ---
    snapshot_supplementary_sheet(
        gc, AOP_SHEET_ID, "AOP FY27", AOP_TABS,
    )

    # --- Newsletters ---
    snapshot_supplementary_sheet(
        gc, NEWSLETTERS_SHEET_ID, "Newsletters", NEWSLETTERS_TABS,
    )

    # --- ORM ---
    snapshot_supplementary_sheet(
        gc, ORM_SHEET_ID, "ORM", ORM_TABS,
    )

    elapsed = (datetime.now() - start).total_seconds()
    print(f"\nDone. Total elapsed: {elapsed:.1f}s")


def snapshot_supplementary_sheet(gc, sheet_id: str, label: str, tabs: list):
    """Snapshot a list of tabs from one supplementary sheet, with retry."""
    print(f"\n=== {label} ===")
    try:
        sheet = gc.open_by_key(sheet_id)
    except gspread.exceptions.APIError as e:
        print(f"  SKIP: {label} sheet not accessible.")
        print(f"  Raw error: {e}")
        return
    for tab_name in tabs:
        print(f"\n[{label} / {tab_name}]")
        for attempt in range(2):
            try:
                snapshot_tab(sheet, tab_name, sheet_id)
                break
            except RuntimeError as e:
                if attempt == 0:
                    print(f"  WARN: {e} Retrying once...")
                    time.sleep(2)
                else:
                    print(f"  ERROR: {e} Moving on.")


if __name__ == "__main__":
    main()
