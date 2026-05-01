"""
First connection test.
Opens the CS Mastersheet using our service account and lists its tabs.
If this runs cleanly, the whole chain from Python to Google Sheets works.
"""

import gspread
from google.oauth2.service_account import Credentials

# Path to the service account key, relative to this script's folder.
KEY_FILE = "credentials/service-account.json"

# Permissions the robot is asking Google for. Read-only on both.
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

# The unique ID of the CS Mastersheet (from its URL).
SHEET_ID = "1Y3wiAYnS2e9Pjjo420Ul7GQ0_PNpl6nNNY2DzDVXEjg"


def main():
    # Load the key and turn it into a proven identity.
    credentials = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)

    # Hand the identity to gspread so it can make authorised requests.
    client = gspread.authorize(credentials)

    # Open the sheet by its ID.
    sheet = client.open_by_key(SHEET_ID)

    # Ask for the list of tabs and pull out just their titles.
    tab_names = [ws.title for ws in sheet.worksheets()]

    print("Hello from your dashboard.")
    print(f"Connected to: {sheet.title}")
    print(f"Found {len(tab_names)} tab(s) inside:")
    for name in tab_names:
        print(f"  - {name}")


if __name__ == "__main__":
    main()
