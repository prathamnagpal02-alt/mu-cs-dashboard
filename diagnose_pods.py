"""
Diagnose why a pod isn't pulling numbers in the dashboard.
Prints, per pod: total rows, captured-at, every column header, and how many
rows have any of the date columns the dashboard relies on.
Run with: python diagnose_pods.py
"""

import os

import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.environ["DATABASE_URL"]

PODS = [
    "Builders.mu",
    "Brand/Ad films",
    "PGP Bharat IG",
    "Perf Ads",
    "YT - Podcasts (Series C)",
    "YT - Off Campus",
    "YT - Masters Of The Market",
    "YT - Family Business",
]

DATE_COLUMNS_TO_CHECK = [
    "Date of Shoot",
    "Edit Start Date",
    "Planned Date of Delivery",
    "Actual Date of Delivery",
    "Date of Upload",
    "YT Date of Upload",
    "YT UPLOAD",
]


def main():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            for tab in PODS:
                cur.execute(
                    """
                    select s.id, s.headers, s.row_count, s.captured_at
                    from snapshots s
                    where s.tab_name = %s
                    order by s.captured_at desc
                    limit 1
                    """,
                    (tab,),
                )
                row = cur.fetchone()
                print(f"\n===== {tab} =====")
                if not row:
                    print("  No snapshot found in the database.")
                    continue
                snapshot_id, headers, row_count, captured_at = row
                print(f"  Captured at: {captured_at}")
                print(f"  Total rows:  {row_count}")
                print(f"  Headers ({len(headers)}):")
                for i, h in enumerate(headers, 1):
                    flag = "  <-- date column we look for" if h in DATE_COLUMNS_TO_CHECK else ""
                    print(f"    {i:2}. {h or '(blank)'}{flag}")

                # Count how many rows actually have each date column filled
                cur.execute(
                    """
                    select sr.data
                    from snapshot_rows sr
                    where sr.snapshot_id = %s
                      and not sr.is_divider
                    """,
                    (snapshot_id,),
                )
                all_rows = cur.fetchall()
                print(f"\n  Date-column fill rates (excluding divider rows):")
                for col in DATE_COLUMNS_TO_CHECK:
                    filled = 0
                    for (data,) in all_rows:
                        if data and str(data.get(col, "") or "").strip():
                            filled += 1
                    if col in headers or filled > 0:
                        print(f"    {col:32}  {filled:5}/{len(all_rows)} filled")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
