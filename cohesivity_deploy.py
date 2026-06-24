"""
cohesivity_deploy.py — publish MUनीम to Cohesivity hosting.

Reads the precomputed trays from Cohesivity Postgres (munim_cache), bundles them
with munim.html as static files, and deploys to Cohesivity's Vercel hosting.
The deployed site reads data/<days>.json — no live database call at runtime.

Run:  venv/Scripts/python.exe cohesivity_deploy.py
"""

import json
import urllib.request
from pathlib import Path

BASE = "https://cohesivity.ai"
UA = "munim-deploy/1"
RANGES = [7, 30, 90]
ROOT = Path(__file__).parent


def creds():
    d = {}
    for line in (ROOT / ".cohesivity").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


C = creds()


def _post(url, payload, auth=None):
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_tray(days):
    r = _post(f"{BASE}/edge/postgres?key={C['coh_application_key']}",
              {"query": "SELECT payload FROM munim_cache WHERE days=$1", "params": [days]})
    rows = r.get("rows", [])
    if not rows:
        raise SystemExit(f"No cached tray for {days}d — run cohesivity_sync.py first.")
    return rows[0]["payload"]


def main():
    files = [{"file": "index.html", "data": (ROOT / "munim.html").read_text(encoding="utf-8"),
              "encoding": "utf-8"}]
    for days in RANGES:
        tray = fetch_tray(days)
        files.append({"file": f"data/{days}.json",
                      "data": json.dumps(tray, ensure_ascii=False), "encoding": "utf-8"})
        print(f"  bundled data/{days}.json")

    # Expense Master (window-independent, key 0) -> data/expense.json
    try:
        files.append({"file": "data/expense.json",
                      "data": json.dumps(fetch_tray(0), ensure_ascii=False), "encoding": "utf-8"})
        print("  bundled data/expense.json")
    except Exception as e:
        print(f"  (skipped expense.json: {e})")

    print("Deploying to Cohesivity hosting (static)...")
    resp = _post(f"{BASE}/api/vercel/deploy?wait=ready",
                 {"files": files}, auth=C["coh_management_key"])
    print(json.dumps({k: resp.get(k) for k in
          ("success", "state", "canonical_url", "deployment_id", "file_count")}, indent=2))
    print("\nLive at:", resp.get("canonical_url"))


if __name__ == "__main__":
    main()
