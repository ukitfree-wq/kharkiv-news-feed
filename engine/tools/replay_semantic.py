"""Read-only replay: which items now in the output feed the semantic rule would have skipped.

Walks stored stories in publish order, exactly like the pipeline would have
(reference = earlier kept stories + earlier semantic duplicates within lookback_hours),
and prints each item that would have been recorded as duplicate_semantic.
Usage: PYTHONPATH=. venv/bin/python tools/replay_semantic.py [--db path]
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from datetime import datetime, timedelta

from newsfeed.config import ROOT, load_config
from newsfeed import semantic_dedup as sd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    cfg = load_config()
    s = sd.settings((cfg.get("dedup") or {}).get("semantic"))
    db = args.db or str(ROOT / cfg["paths"]["database"])
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        """SELECT id, feed_id, published_at, extraction_status, title, content_text FROM articles
           WHERE extraction_status IN ('ok','partial','failed') AND content_len >= 200
           ORDER BY datetime(published_at), id""")]
    idf_rows = [dict(r) for r in conn.execute(
        "SELECT title, content_text FROM articles WHERE content_len >= 200 AND content_text IS NOT NULL")]
    ded = sd.SemanticDeduper(s, ROOT)
    ded.load_reference([], idf_rows)
    ded.docs = []
    lookback = timedelta(hours=float(s["lookback_hours"]))
    dropped = []
    times = []
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        pub = datetime.fromisoformat(r["published_at"][:19])
        ded.docs = [d for d in ded.docs if pub - datetime.fromisoformat(d.published_at[:19]) <= lookback]
        t0 = time.monotonic()
        key, sc = ded.check(r)
        times.append(time.monotonic() - t0)
        if key is not None:
            dropped.append((r, by_id[key], sc))
        ded.add(r)
        # add() bumps document frequencies; the corpus already counts this row.
        doc = ded.docs[-1]
        ded.idf.n -= 1
        ded.idf.df_stem.subtract(set(doc.stems))
        ded.idf.df_ent.subtract(doc.ents)
    for r, m, sc in dropped:
        print(f"DROP id={r['id']} [{r['feed_id']} {r['published_at']}] {r['title'][:90]}")
        print(f"     kept id={m['id']} [{m['feed_id']} {m['published_at']}] {m['title'][:90]}")
        print(f"     rule={sc['rule']} emb={sc['emb']} kw={sc['kw']} ent={sc['ent']} score={sc['score']} "
              f"h={sc['hours']} anchors={','.join(sc['anchors'])}")
    times.sort()
    print(f"# feed-eligible stories={len(rows)} would_drop={len(dropped)} "
          f"check time per item: median={times[len(times)//2]*1000:.0f}ms max={times[-1]*1000:.0f}ms; "
          f"model load {ded.embedder.load_seconds:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
