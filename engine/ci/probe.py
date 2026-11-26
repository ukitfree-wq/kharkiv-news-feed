"""Connectivity probe: fetch every enabled feed and scrape its newest articles.

Read-only: never touches state.db or the published feed. Used to verify that
all sources work from a GitHub runner (non-Ukrainian IPs).
Usage: python -m ci.probe [articles_per_feed]   (run from engine/)
"""
from __future__ import annotations

import sys
import time

import feedparser
import requests

from newsfeed.config import load_config
from newsfeed.extract import _session, extract_article


def main() -> int:
    per_feed = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    cfg = load_config()
    ex = cfg["extraction"]
    rss = requests.Session()
    rss.headers.update({
        "User-Agent": ex["user_agent"],
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        "Accept-Language": ex["accept_language"],
    })
    page = _session(ex["user_agent"], ex["accept_language"])
    failures = 0
    for feed in [f for f in cfg["feeds"] if f["enabled"]]:
        t = time.monotonic()
        try:
            r = rss.get(feed["url"], timeout=cfg["request"]["rss_timeout_seconds"])
            parsed = feedparser.parse(r.content)
            entries = parsed.entries
            print(f"FEED {feed['id']}: HTTP {r.status_code}, {len(entries)} entries, {time.monotonic()-t:.1f}s")
        except Exception as exc:  # noqa: BLE001
            print(f"FEED {feed['id']}: FAILED {exc!r}")
            failures += 1
            continue
        if r.status_code != 200 or not entries:
            failures += 1
            continue
        ok = 0
        for e in entries[:per_feed]:
            t = time.monotonic()
            res = extract_article(e.get("link", ""), session=page, timeout=ex["timeout_seconds"],
                                  min_text_chars=ex["min_text_chars"], fallback_title=e.get("title", ""))
            status = res["extraction_status"]
            ok += status in ("ok", "partial")
            print(f"  PAGE {feed['id']}: {status} text={len(res['content_text'])} "
                  f"images={len(res['images'])} embeds={len(res['embeds'])} "
                  f"{res['skip_reason']} {time.monotonic()-t:.1f}s {e.get('link','')}")
        if ok == 0:
            failures += 1
    print(f"probe done: {failures} source(s) failing")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
