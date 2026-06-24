"""
cohesivity_sync.py — push MUनीम's live data + AI insights into Cohesivity.

Pipeline:
  1. Build the data trays (overview + pods + coverage + brand) for each window
     using the existing, tested logic in munim_api.build_data().
  2. Ask Cohesivity's AI gateway (Claude) to write the "MUनीम says" insights
     from those real numbers, and embed them in each tray.
  3. Upsert each tray as JSON into a `munim_cache` table in Cohesivity Postgres.

The deployed site then just reads `munim_cache` — no heavy logic in the cloud.

Run:  venv/Scripts/python.exe cohesivity_sync.py          (data + AI)
      venv/Scripts/python.exe cohesivity_sync.py --no-ai  (data only, fast)
"""

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import munim_api  # reuse build_data + prompts

RANGES = [7, 30, 90]
# PIN that unlocks the salary view. Used ONLY to encrypt; never deployed.
SALARY_PIN = os.environ.get("MUNIM_SALARY_PIN", "4567")


def encrypt_payload(payload, pin):
    """AES-256-GCM with a PBKDF2-SHA256 key — decryptable by the browser's
    Web Crypto API. The deployed file holds only ciphertext."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, 100000, 32)
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, default=str).encode(), None)
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "kdf": "PBKDF2-SHA256", "iter": 100000,
            "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}
BASE = "https://cohesivity.ai"
UA = "munim-sync/1"
NO_AI = "--no-ai" in sys.argv


def creds():
    d = {}
    for line in (Path(__file__).parent / ".cohesivity").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip()
    return d


C = creds()
APP_KEY = C["coh_application_key"]


def _post(url, payload):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read().decode("utf-8"))


def sql(query, params=None):
    return _post(f"{BASE}/edge/postgres?key={APP_KEY}", {"query": query, "params": params or []})


def ai_json(system, user, max_tokens, keys):
    """Call Cohesivity AI gateway (Claude), throttled + retried, parse JSON reply."""
    import re
    for attempt in range(5):
        try:
            time.sleep(5)  # stay under the free-tier rate limit
            resp = _post(
                f"{BASE}/edge/ai-gateway/v1/chat/completions?key={APP_KEY}",
                {"model": "anthropic/claude-haiku-4.5", "max_tokens": max_tokens,
                 "messages": [{"role": "system", "content": system},
                              {"role": "user", "content": user}]},
            )
            text = resp["choices"][0]["message"]["content"].strip()
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    return json.loads(m.group())
                raise
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 10 * (attempt + 1)
                print(f"    429 rate-limited, backing off {wait}s...")
                time.sleep(wait)
                continue
            print(f"    AI call failed: {e}")
            break
        except Exception as e:
            print(f"    AI call failed: {e}")
            break
    return {k: ("- (insight unavailable)" if i == 0 else "") for i, k in enumerate(keys)}


def overall_insight(tray):
    snap = json.dumps({
        "period": tray["period"], "totals": tray["totals"],
        "pods": {pid: {k: p.get(k) for k in ("pod", "lead", "active", "shipped",
                 "status_counts", "on_time_rate", "avg_shoot_to_edit")}
                 for pid, p in tray["pods"].items()},
        "coverage": {"today": len(tray["coverage"]["today"]),
                     "upcoming": len(tray["coverage"]["upcoming"])},
        "brand": tray["brand"],
    }, default=str)
    user = ("Current state of the Creative Studio (JSON):\n\n" + snap + "\n\n"
            "Produce four sections. Return raw JSON with keys: wins, risks, today, note.\n"
            "wins/risks/today are markdown bullet strings (2-4 bullets, each '- ' on its own line).\n"
            "note is a single 40-60 word string Divyam can paste into Slack.\n"
            "No fences, just the JSON object.")
    return ai_json(munim_api.LEADERSHIP_SYSTEM_PROMPT, user, 2000,
                   ["wins", "risks", "today", "note"])


def pod_insight(p):
    user = (f"Current state of the {p['pod']} pod (JSON):\n\n{json.dumps(p, default=str)}\n\n"
            "Produce three sections. Return raw JSON with keys: wins, risks, action.\n"
            "Values are markdown bullet strings (1-3 bullets each, '- ' per line).\n"
            "If little data, say so honestly. No fences, just the JSON object.")
    return ai_json(munim_api.LEADERSHIP_SYSTEM_PROMPT, user, 1200,
                   ["wins", "risks", "action"])


def main():
    print("Creating munim_cache table on Cohesivity Postgres...")
    sql("CREATE TABLE IF NOT EXISTS munim_cache ("
        "days INTEGER PRIMARY KEY, payload JSONB NOT NULL, generated_at TEXT)")

    trays = {days: munim_api.build_data(days) for days in RANGES}
    for days, tray in trays.items():
        print(f"  window {days}d: {tray['totals']['active']} active, {len(tray['pods'])} live pods")

    # Per-pod insights: compute ONCE from the richest (90d) window, reuse across ranges.
    pod_ins = {}
    if not NO_AI:
        for pid, p in trays[90]["pods"].items():
            if p.get("active", 0) > 0:
                print(f"  AI for pod {pid}...")
                pod_ins[pid] = pod_insight(p)

    for days, tray in trays.items():
        if not NO_AI:
            print(f"  overall AI for {days}d...")
            tray["insights"] = {"overall": overall_insight(tray), "pods": pod_ins}
        else:
            # data-only refresh: keep the AI insights already in the cache
            try:
                ex = sql("SELECT payload->'insights' AS ins FROM munim_cache WHERE days=$1", [days])
                rows = ex.get("rows", [])
                if rows and rows[0].get("ins"):
                    tray["insights"] = rows[0]["ins"]
                    print(f"  kept existing AI insights for {days}d")
            except Exception as e:
                print(f"  (could not carry insights: {e})")
        sql("INSERT INTO munim_cache (days, payload, generated_at) VALUES ($1, $2, $3) "
            "ON CONFLICT (days) DO UPDATE SET payload = EXCLUDED.payload, "
            "generated_at = EXCLUDED.generated_at",
            [days, json.dumps(tray, default=str), tray["generated_at"]])
        print(f"  upserted window {days}d into Cohesivity.")

    # Expense Master — window-independent, stored under key 0
    print("  building expense master (FY26 + FY27)...")
    exp = munim_api.build_expense()
    sql("INSERT INTO munim_cache (days, payload, generated_at) VALUES ($1, $2, $3) "
        "ON CONFLICT (days) DO UPDATE SET payload = EXCLUDED.payload, "
        "generated_at = EXCLUDED.generated_at",
        [0, json.dumps(exp, default=str), exp["generated_at"]])
    print(f"  upserted expense master: FY26 Rs {exp['fy_totals'].get('FY26',0):,} / "
          f"FY27 Rs {exp['fy_totals'].get('FY27',0):,}, {exp['line_count']} line items.")

    # Salaries — encrypted with the PIN, stored under key -1 (only ciphertext leaves here)
    print("  building + encrypting salaries...")
    sal = munim_api.build_salaries()
    enc = encrypt_payload(sal, SALARY_PIN)
    sql("INSERT INTO munim_cache (days, payload, generated_at) VALUES ($1, $2, $3) "
        "ON CONFLICT (days) DO UPDATE SET payload = EXCLUDED.payload, "
        "generated_at = EXCLUDED.generated_at",
        [-1, json.dumps(enc), sal["generated_at"]])
    print(f"  upserted encrypted salaries ({sal['headcount']} people, PIN-locked).")

    print("\nDone. munim_cache populated on Cohesivity Postgres.")


if __name__ == "__main__":
    main()
