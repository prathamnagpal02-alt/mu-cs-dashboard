"""
Snapshots one tab of the CS Mastersheet into Supabase.
Reads every row from the chosen tab and writes it into the `snapshots`
and `snapshot_rows` tables. Runs end-to-end in a few seconds.
Change TAB_NAME below to snapshot a different tab.
"""

import os
import gspread
import psycopg2
from psycopg2.extras import Json
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv

# Pull values from the .env file into os.environ.
load_dotenv()

# Config, all read from .env so secrets never live in code.
KEY_FILE = os.environ["GOOGLE_CREDENTIALS_PATH"]
SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
DATABASE_URL = os.environ["DATABASE_URL"]

# Which tab to snapshot. Change this string to point at any priority tab.
TAB_NAME = "Builders.mu"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def read_tab(tab_name: str):
    """Fetches a tab's full contents as (headers, data_rows)."""
    creds = Credentials.from_service_account_file(KEY_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SHEET_ID)
    worksheet = sheet.worksheet(tab_name)

    all_rows = worksheet.get_all_values()
    if not all_rows:
        return [], []

    headers = all_rows[0]
    data_rows = all_rows[1:]
    return headers, data_rows


def row_to_dict(headers: list, row: list) -> dict:
    """Packages a row list into a {header: value} dict.
    Uses a fallback key for columns whose header is blank."""
    result = {}
    for i, value in enumerate(row):
        key = headers[i] if i < len(headers) and headers[i] else f"col_{i+1}"
        result[key] = value
    return result


def is_divider_row(headers: list, row: list) -> bool:
    """Returns True if the row looks like a visual divider
    (our heuristic: the 'Lead' column is blank)."""
    try:
        lead_idx = headers.index("Lead")
    except ValueError:
        return False

    if lead_idx >= len(row):
        return False

    return row[lead_idx].strip() == ""


def main():
    print(f"Reading tab: {TAB_NAME}")
    headers, data_rows = read_tab(TAB_NAME)
    if not data_rows:
        print("Tab is empty, nothing to snapshot.")
        return

    print(f"Pulled {len(data_rows)} data rows. Connecting to database.")
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into snapshots (sheet_id, tab_name, row_count, headers)
                values (%s, %s, %s, %s)
                returning id
                """,
                (SHEET_ID, TAB_NAME, len(data_rows), Json(headers)),
            )
            snapshot_id = cur.fetchone()[0]

            for row_number, row in enumerate(data_rows, start=1):
                row_dict = row_to_dict(headers, row)
                divider = is_divider_row(headers, row)
                cur.execute(
                    """
                    insert into snapshot_rows
                        (snapshot_id, row_number, data, is_divider)
                    values (%s, %s, %s, %s)
                    """,
                    (snapshot_id, row_number, Json(row_dict), divider),
                )

        conn.commit()
    finally:
        conn.close()

    print(f"Done. snapshot_id = {snapshot_id}")
    print(f"Rows inserted: {len(data_rows)}")


if __name__ == "__main__":
    main()
