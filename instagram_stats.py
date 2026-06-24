"""
Instagram stats via Apify — public scrape of an account's reels + profile.

Pulls per-reel views/likes/comments (matched later by shortcode to the sheet)
and account-level followers, then caches the result to ig_cache/<handle>.json
so the data sync can read it without re-scraping every time.

Run:  venv/Scripts/python.exe instagram_stats.py            (default: builders.mu)
      venv/Scripts/python.exe instagram_stats.py someother  (another handle)

Apify is pay-per-result (~$1.50 / 1,000) with $5/month free credit, so one
account is effectively free. Scraping is against Instagram's ToS (public data,
widely used); it can occasionally break when Instagram changes its site.
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
CACHE_DIR = Path(__file__).parent / "ig_cache"
ACTOR = "apify~instagram-scraper"
BASE = "https://api.apify.com/v2/acts/" + ACTOR + "/run-sync-get-dataset-items"


def _run(payload, timeout=600):
    url = BASE + "?token=" + APIFY_TOKEN
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def scrape(handle, results_limit=400):
    """Returns a dict with profile + reels, ready to cache."""
    if not APIFY_TOKEN:
        raise RuntimeError("APIFY_TOKEN missing from .env")
    profile_url = f"https://www.instagram.com/{handle}/"

    print(f"[{handle}] scraping posts (limit {results_limit})...")
    posts = _run({
        "directUrls": [profile_url],
        "resultsType": "posts",
        "resultsLimit": results_limit,
        "addParentData": False,
    })

    print(f"[{handle}] scraping profile details...")
    try:
        details = _run({
            "directUrls": [profile_url],
            "resultsType": "details",
            "resultsLimit": 1,
        })
        prof = details[0] if details else {}
    except Exception as e:
        print(f"  details failed ({e}); continuing without profile-level stats")
        prof = {}

    reels = []
    for p in posts:
        code = p.get("shortCode")
        if not code:
            continue
        reels.append({
            "shortCode": code,
            "type": p.get("type"),
            "product_type": p.get("productType"),
            "views": p.get("videoPlayCount") or p.get("videoViewCount") or 0,   # IG's current "Views" = plays
            "view_count_legacy": p.get("videoViewCount") or 0,
            "plays": p.get("videoPlayCount") or 0,
            "likes": p.get("likesCount") or 0,
            "comments": p.get("commentsCount") or 0,
            "caption": (p.get("caption") or "")[:140],
            "timestamp": p.get("timestamp"),
            "url": p.get("url"),
        })

    followers = (prof.get("followersCount") or prof.get("ownerFollowersCount")
                 or (posts[0].get("ownerFollowersCount") if posts else None))

    out = {
        "handle": handle,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "followers": followers,
        "follows": prof.get("followsCount"),
        "posts_count": prof.get("postsCount"),
        "full_name": prof.get("fullName"),
        "biography": prof.get("biography"),
        "verified": prof.get("verified"),
        "reels_scraped": len(reels),
        "reels": reels,
    }
    return out


def save(data):
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / (data["handle"] + ".json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(handle):
    path = CACHE_DIR / (handle + ".json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


if __name__ == "__main__":
    handle = sys.argv[1] if len(sys.argv) > 1 else "builders.mu"
    t0 = time.time()
    data = scrape(handle)
    path = save(data)
    tv = sum(r["views"] for r in data["reels"])
    tl = sum(r["likes"] for r in data["reels"])
    print(f"\n[{handle}] {data['reels_scraped']} reels | followers={data['followers']} "
          f"| total views={tv:,} | total likes={tl:,}")
    print(f"saved -> {path}  ({time.time()-t0:.0f}s)")
