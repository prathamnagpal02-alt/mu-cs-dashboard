"""
Small helper that lists every tab inside the Coverage sheet, so we can
identify the exact 2026 tab name before wiring it into the dashboard.
Run with: python list_coverage_tabs.py
"""

import os

import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

load_dotenv()

KEY_FILE = os.environ["GOOGLE_CREDENTIALS_PATH"]
COVERAGE_SHEET_ID = "14GfWMoxVUjFVmvEan5c_-CDzBh-qIuujgsW8Z_m1pUM"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def main():
    creds = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)

    try:
        sheet = gc.open_by_key(COVERAGE_SHEET_ID)
    except gspread.exceptions.APIError as e:
        print("ERROR: cannot open the Coverage sheet.")
        print("Most likely cause: the sheet has not been shared with the")
        print("service account yet. Share it with:")
        print("  cs-dashboard-reader@cs-dashboard-weekly.iam.gserviceaccount.com")
        print(f"\nRaw error: {e}")
        return

    print(f"Connected to: {sheet.title}")
    print("\nTabs inside:")
    for ws in sheet.worksheets():
        print(f"  - {ws.title}   (rows={ws.row_count}, cols={ws.col_count})")


if __name__ == "__main__":
    main()
