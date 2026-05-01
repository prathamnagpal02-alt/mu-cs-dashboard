"""
Discovers every Google Sheet the dashboard cares about: tries to open each,
lists its tab names with row/column counts, and reports which ones are not
reachable yet (usually because they haven't been shared with the service
account). Run with: python list_all_sheets.py
"""

import os
import sys

# Force UTF-8 output so rupee, emoji, and other non-ASCII tab names print
# without crashing on Windows's default cp1252 console encoding.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

KEY_FILE = os.environ["GOOGLE_CREDENTIALS_PATH"]
SERVICE_ACCOUNT_EMAIL = "cs-dashboard-reader@cs-dashboard-weekly.iam.gserviceaccount.com"

SHEETS = [
    {
        "name": "CS Mastersheet",
        "id": "1Y3wiAYnS2e9Pjjo420Ul7GQ0_PNpl6nNNY2DzDVXEjg",
        "purpose": "Production tracker for the eight priority pods.",
    },
    {
        "name": "Coverage (Main Shoot Calendar)",
        "id": "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM",
        "purpose": "Shoot scheduling for the Coverage desk.",
    },
    {
        "name": "Salaries & Expenses FY26",
        "id": "1eok2NGU7gzhM7sGraFcyeqO-AMyyQXzj4AxhV-bXUrw",
        "purpose": "Per-employee salaries plus expense ledger.",
    },
    {
        "name": "Newsletters (3 newsletters)",
        "id": "1HXFklF6_RJ3L_lSDe0AUr1xdxKQ-c9ngYvvUosyFI94",
        "purpose": "Paradox Weekly, Swati's Memo, Nandini's Newsletter.",
    },
    {
        "name": "ORM",
        "id": "1kBFoCe28vrkVqnaRyn3dqNxBs_KSZf8MuZcpVp_vAXE",
        "purpose": "Online Reputation Management tracker (Reddit, Quora, etc.).",
    },
    {
        "name": "Annual Operating Plan (AOP) FY27",
        "id": "16tKPWj33VN1Y7PGRf1LqNyYHOw6gLIJErG3f6nfY5AU",
        "purpose": "Per-pod annual targets. Used for AOP-attainment progress bars.",
    },
    {
        "name": "PR (Public Relations)",
        "id": "1Tr4HPLouJsXRHtJDWBjsxKgLxX2StDI8Cb6f0Wyb2D0",
        "purpose": "Press, publications, and tiered media coverage tracker.",
    },
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main():
    creds = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)

    print(f"Service account: {SERVICE_ACCOUNT_EMAIL}\n")

    unreachable = []
    for entry in SHEETS:
        print(f"===== {entry['name']} =====")
        print(f"  Purpose: {entry['purpose']}")
        print(f"  ID:      {entry['id']}")
        try:
            sheet = gc.open_by_key(entry["id"])
        except gspread.exceptions.APIError as e:
            print("  STATUS:  NOT REACHABLE")
            print("  Action:  share this sheet with the service account email above.")
            print(f"  Raw error: {e}")
            unreachable.append(entry["name"])
            print()
            continue

        print(f"  STATUS:  OK ({sheet.title})")
        print(f"  Tabs ({len(sheet.worksheets())}):")
        for ws in sheet.worksheets():
            print(f"    - {ws.title}   (rows={ws.row_count}, cols={ws.col_count})")
        print()

    print("=" * 60)
    if unreachable:
        print("The following sheets still need to be shared with the service account:")
        for name in unreachable:
            print(f"  - {name}")
        print(f"\nShare them with: {SERVICE_ACCOUNT_EMAIL} (Viewer, untick notify)")
    else:
        print("All sheets reachable. We are clear to extend snapshot_all.py.")


if __name__ == "__main__":
    main()
