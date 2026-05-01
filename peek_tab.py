"""
Peeks at the contents of one tab inside the CS Mastersheet.
Prints column headers, row count, and a preview of the first few rows.
Change TAB_NAME to look at a different tab.
"""

import gspread
from google.oauth2.service_account import Credentials

KEY_FILE = "credentials/service-account.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
SHEET_ID = "1Y3wiAYnS2e9Pjjo420Ul7GQ0_PNpl6nNNY2DzDVXEjg"

# Which tab do we want to peek at? Change this to any of the priority tabs.
TAB_NAME = "Builders.mu"

# How many rows of real data to preview after the headers.
ROWS_TO_PREVIEW = 5


def main():
    credentials = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    client = gspread.authorize(credentials)
    sheet = client.open_by_key(SHEET_ID)

    # worksheet() with a name gives us one specific tab.
    worksheet = sheet.worksheet(TAB_NAME)

    # Pull down every cell. Each row is a list of string values.
    all_rows = worksheet.get_all_values()

    if not all_rows:
        print(f"Tab '{TAB_NAME}' is empty.")
        return

    headers = all_rows[0]
    data_rows = all_rows[1:]

    print(f"Tab: {TAB_NAME}")
    print(f"Total data rows (excluding header): {len(data_rows)}")
    print(f"Number of columns: {len(headers)}")
    print()

    print("Column headers:")
    for i, header in enumerate(headers, start=1):
        display = header if header else "(blank)"
        print(f"  {i}. {display}")
    print()

    preview_count = min(ROWS_TO_PREVIEW, len(data_rows))
    print(f"First {preview_count} data row(s):")
    for i, row in enumerate(data_rows[:preview_count], start=1):
        print(f"  Row {i}: {row}")


if __name__ == "__main__":
    main()
