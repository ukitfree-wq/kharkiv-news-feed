"""Pairwise calibration of the semantic duplicate check over state.db.

Usage: PYTHONPATH=. venv/bin/python tools/calibrate_semantic.py [--days 3] [--min-emb 0.84] [--all]
Prints every pair the current rule flags (or, with --all, every pair above --min-emb)
with its scores, as TSV, plus a JSON dump to data/semantic_calibration.json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time

from newsfeed.config import ROOT, load_config
from newsfeed import semantic_dedup as sd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--min-emb", type=float, default=0.84)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--with-dups", action="store_true",
                    help="also include rows already skipped as duplicates (full history)")
    ap.add_argument("--db", default=None, help="state.db to read (opened read-only)")
    args = ap.parse_args()
    cfg = load_config()
    s = sd.settings((cfg.get("dedup") or {}).get("semantic"))
    db = args.db or str(ROOT / cfg["paths"]["database"])
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in conn.execute(
            """SELECT id, feed_id, published_at, extraction_status, title, content_text FROM articles
               WHERE extraction_status IN ({statuses})
                 AND content_len >= 200 AND datetime(published_at) >= datetime('now', ?)
               ORDER BY datetime(published_at), id""".format(statuses=(
                "'ok','partial','failed','filtered_keyword'"
                + (",'duplicate_semantic','duplicate_title','duplicate_content'" if args.with_dups else ""))),
            (f"-{int(args.days * 24)} hours",),
        )
    ]
    t0 = time.monotonic()
    ded = sd.SemanticDeduper(s, ROOT)
    idf_rows = [dict(r) for r in conn.execute(
        "SELECT title, content_text FROM articles WHERE content_len >= 200 AND content_text IS NOT NULL "
        "AND datetime(COALESCE(fetched_at, published_at)) >= datetime('now', '-14 days')")]
    ded.load_reference(rows, idf_rows)
    t_embed = time.monotonic() - t0
    by_id = {r["id"]: r for r in rows}
    out = []
    t1 = time.monotonic()
    docs = ded.docs
    for i, a in enumerate(docs):
        for b in docs[i + 1 :]:
            sc = sd.score_pair(b, a, ded.idf, s)
            if sc["duplicate"] or (args.all and sc["emb"] >= args.min_emb):
                ra, rb = by_id[a.key], by_id[b.key]
                out.append({"a": a.key, "b": b.key, **sc,
                            "a_src": ra["feed_id"], "b_src": rb["feed_id"],
                            "a_title": ra["title"], "b_title": rb["title"]})
    t_pairs = time.monotonic() - t1
    out.sort(key=lambda p: -p["score"])
    for p in out:
        print("\t".join(str(x) for x in (
            "DUP" if p["duplicate"] else "-", p["rule"] or p["veto"] or "", p["emb"], p["kw"], p["ent"], p["score"],
            p["hours"], p["a"], p["b"], p["a_src"] + "/" + p["b_src"],
            p["a_title"][:75], p["b_title"][:75], ",".join(p["anchors"][:5]))))
    print(f"# docs={len(docs)} pairs={len(docs)*(len(docs)-1)//2} printed={len(out)} flagged={sum(p['duplicate'] for p in out)} "
          f"model_load={ded.embedder.load_seconds:.2f}s embed_total={t_embed:.2f}s pair_scoring={t_pairs:.2f}s", file=sys.stderr)
    (ROOT / "data" / "semantic_calibration.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
