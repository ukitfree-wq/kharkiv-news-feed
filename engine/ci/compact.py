"""Keep state.db small enough to live on the `state` branch indefinitely.

Never deletes article rows (URL / title / hashes stay forever, so a story is
never re-added). It only:
- clears the heavy body columns of articles fetched more than 30 days ago
  (far beyond the 3-day feed retention and the 14-day dedup lookback),
- deletes run-history rows older than 7 days,
- VACUUMs the file.
Usage (from engine/): python -m ci.compact [path/to/state.db]
"""
from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

BODY_DAYS = 30
RUNS_DAYS = 7


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else "data/state.db"
    now = datetime.now(timezone.utc)
    body_cut = (now - timedelta(days=BODY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    runs_cut = (now - timedelta(days=RUNS_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    before = os.path.getsize(path)
    conn = sqlite3.connect(path)
    with conn:
        stripped = conn.execute(
            "UPDATE articles SET content_html = NULL, content_text = NULL, summary = NULL "
            "WHERE fetched_at IS NOT NULL AND fetched_at < ? "
            "AND (content_html IS NOT NULL OR content_text IS NOT NULL OR summary IS NOT NULL)",
            (body_cut,),
        ).rowcount
        runs = conn.execute("DELETE FROM runs WHERE started_at < ?", (runs_cut,)).rowcount
    conn.execute("VACUUM")
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    conn.close()
    print(f"compact: {total} article rows kept, {stripped} old bodies cleared, {runs} old run rows deleted, "
          f"{before} -> {os.path.getsize(path)} bytes, integrity {ok}")
    return 0 if ok == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
