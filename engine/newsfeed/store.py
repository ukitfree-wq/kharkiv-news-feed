"""SQLite persistence for seen articles, feed HTTP cache, and history."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT UNIQUE,
    google_url TEXT,
    guid TEXT,
    feed_id TEXT,
    feed_url TEXT,
    canonical_url TEXT,
    url_hash TEXT,
    title TEXT,
    title_norm TEXT,
    title_hash TEXT,
    publisher TEXT,
    category TEXT,
    published_at TEXT,
    fetched_at TEXT,
    extraction_status TEXT,
    lead_image TEXT,
    images_json TEXT,
    embeds_json TEXT,
    minhash TEXT,
    content_len INTEGER DEFAULT 0,
    content_html TEXT,
    content_text TEXT,
    summary TEXT,
    skip_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_articles_url_hash ON articles(url_hash);
CREATE INDEX IF NOT EXISTS idx_articles_title_hash ON articles(title_hash);
CREATE INDEX IF NOT EXISTS idx_articles_canonical ON articles(canonical_url);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);
CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(extraction_status);
CREATE INDEX IF NOT EXISTS idx_articles_source ON articles(source_url);
CREATE INDEX IF NOT EXISTS idx_articles_guid ON articles(guid);
CREATE INDEX IF NOT EXISTS idx_articles_google ON articles(google_url);

CREATE TABLE IF NOT EXISTS feeds (
    url TEXT PRIMARY KEY,
    feed_id TEXT,
    etag TEXT,
    last_modified TEXT,
    last_fetch TEXT,
    last_status INTEGER,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    stats_json TEXT
);
"""

ARTICLE_COLUMNS = {
    "source_url",
    "google_url",
    "guid",
    "feed_id",
    "feed_url",
    "canonical_url",
    "url_hash",
    "title",
    "title_norm",
    "title_hash",
    "publisher",
    "category",
    "published_at",
    "fetched_at",
    "extraction_status",
    "lead_image",
    "images_json",
    "embeds_json",
    "minhash",
    "content_len",
    "content_html",
    "content_text",
    "summary",
    "skip_reason",
}

EXTRA_COLUMNS = [
    ("source_url", "TEXT"),
    ("google_url", "TEXT"),
    ("guid", "TEXT"),
    ("feed_id", "TEXT"),
    ("feed_url", "TEXT"),
    ("embeds_json", "TEXT"),
    ("minhash", "TEXT"),
    ("content_len", "INTEGER DEFAULT 0"),
]


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
    for name, typ in EXTRA_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE articles ADD COLUMN {name} {typ}")
    # Backfill source_url from google_url if needed
    conn.execute(
        """
        UPDATE articles
        SET source_url = google_url
        WHERE (source_url IS NULL OR source_url = '')
          AND google_url IS NOT NULL AND google_url != ''
        """
    )


def now_iso() -> str:
    """UTC timestamp in a form SQLite datetime() accepts."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def normalize_ts(value: str | None, fallback: str | None = None) -> str:
    """Normalize a date string to 'YYYY-MM-DD HH:MM:SS' UTC."""
    if value:
        raw = value.strip().replace("T", " ")
        if raw.endswith("Z"):
            raw = raw[:-1]
        if len(raw) >= 19 and raw[4] == "-" and raw[10] in "  ":
            return raw[:19]
        try:
            from dateutil import parser as dateparser

            dt = dateparser.parse(value)
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return fallback or now_iso()


def get_feed_meta(conn: sqlite3.Connection, url: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM feeds WHERE url = ?", (url,)).fetchone()
    if not row:
        return {"url": url, "etag": None, "last_modified": None}
    return dict(row)


def upsert_feed_meta(
    conn: sqlite3.Connection,
    url: str,
    feed_id: str = "",
    etag: str | None = None,
    last_modified: str | None = None,
    last_status: int | None = None,
    last_error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO feeds (url, feed_id, etag, last_modified, last_fetch, last_status, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
            feed_id = excluded.feed_id,
            etag = COALESCE(excluded.etag, feeds.etag),
            last_modified = COALESCE(excluded.last_modified, feeds.last_modified),
            last_fetch = excluded.last_fetch,
            last_status = excluded.last_status,
            last_error = excluded.last_error
        """,
        (url, feed_id, etag, last_modified, now_iso(), last_status, last_error),
    )


def _chunked_in(conn, sql_template: str, values: list, key: str = "") -> set[str]:
    found: set[str] = set()
    if not values:
        return found
    for i in range(0, len(values), 400):
        chunk = values[i : i + 400]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(sql_template.format(placeholders=placeholders), chunk).fetchall()
        found.update(r[0] for r in rows if r[0])
    return found


def seen_source_urls(conn: sqlite3.Connection, urls: Iterable[str]) -> set[str]:
    urls = [u for u in urls if u]
    a = _chunked_in(conn, "SELECT source_url FROM articles WHERE source_url IN ({placeholders})", urls)
    b = _chunked_in(conn, "SELECT google_url FROM articles WHERE google_url IN ({placeholders})", urls)
    return a | b


def seen_guids(conn: sqlite3.Connection, guids: Iterable[str]) -> set[str]:
    guids = [g for g in guids if g]
    return _chunked_in(conn, "SELECT guid FROM articles WHERE guid IN ({placeholders})", guids)


def has_url_hash(conn: sqlite3.Connection, h: str) -> sqlite3.Row | None:
    if not h:
        return None
    return conn.execute(
        "SELECT * FROM articles WHERE url_hash = ? AND extraction_status NOT LIKE 'duplicate_%' ORDER BY id DESC LIMIT 1",
        (h,),
    ).fetchone()


def recent_for_dedup(conn: sqlite3.Connection, lookback_days: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT id, title, title_norm, title_hash, url_hash, canonical_url, source_url,
                   minhash, content_text, content_len, published_at, extraction_status,
                   lead_image, images_json, embeds_json, content_html, publisher
            FROM articles
            WHERE extraction_status IN ('ok', 'partial', 'failed', 'filtered_keyword')
              AND datetime(COALESCE(fetched_at, published_at)) >= datetime('now', ?)
            """,
            (f"-{int(lookback_days)} days",),
        ).fetchall()
    )


def insert_article(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    payload = {k: item.get(k) for k in ARTICLE_COLUMNS}
    if not payload.get("source_url"):
        payload["source_url"] = item.get("google_url") or item.get("canonical_url") or None
    if payload.get("google_url") == "":
        payload["google_url"] = None
    if payload.get("content_len") is None:
        payload["content_len"] = len(item.get("content_text") or "")
    cols = ", ".join(ARTICLE_COLUMNS)
    params = ", ".join(":" + c for c in ARTICLE_COLUMNS)
    cur = conn.execute(f"INSERT INTO articles ({cols}) VALUES ({params})", payload)
    return int(cur.lastrowid)


def update_article(conn: sqlite3.Connection, article_id: int, item: dict[str, Any]) -> None:
    fields = {k: item[k] for k in ARTICLE_COLUMNS if k in item}
    if not fields:
        return
    assignments = ", ".join(f"{k} = :{k}" for k in fields)
    fields["id"] = article_id
    conn.execute(f"UPDATE articles SET {assignments} WHERE id = :id", fields)


def mark_duplicate(conn: sqlite3.Connection, article_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE articles SET extraction_status = ?, skip_reason = ? WHERE id = ?",
        (reason, reason, article_id),
    )


def get_article(conn: sqlite3.Connection, article_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()


def feed_candidates(
    conn: sqlite3.Connection,
    max_age_days: int,
    max_items: int,
    include_failed: bool,
) -> list[sqlite3.Row]:
    status_filter = ""
    if not include_failed:
        status_filter = "AND extraction_status = 'ok'"
    rows = conn.execute(
        f"""
        SELECT *
        FROM articles
        WHERE canonical_url IS NOT NULL AND canonical_url != ''
          AND extraction_status NOT LIKE 'duplicate_%'
          AND extraction_status NOT IN ('decode_failed', 'too_old', 'no_date', 'filtered_keyword')
          AND datetime(published_at) >= datetime('now', ?)
          {status_filter}
        ORDER BY datetime(published_at) DESC, id DESC
        LIMIT ?
        """,
        (f"-{int(max_age_days)} days", int(max_items)),
    ).fetchall()
    return list(rows)


def apply_title_keyword_purge(conn: sqlite3.Connection, matcher) -> dict[str, int]:
    """Retag collected items so only keyword-matching titles stay in the output feed.

    Non-matches become filtered_keyword (content is kept for dedup). If the
    keyword file later gains a form that matches, a previously purged item
    with stored content is restored.
    ``matcher`` is a callable title -> matched keyword or None.
    """
    rows = conn.execute(
        """
        SELECT id, title, extraction_status, skip_reason, content_html
        FROM articles
        WHERE extraction_status IN ('ok', 'partial', 'failed', 'filtered_keyword')
        """
    ).fetchall()
    removed = 0
    restored = 0
    for row in rows:
        matched = matcher(row["title"] or "")
        status = row["extraction_status"] or ""
        if matched:
            if status == "filtered_keyword" and (row["content_html"] or "").strip():
                prev = (row["skip_reason"] or "")
                if prev.startswith("filtered_keyword:"):
                    prev = prev.split(":", 1)[1] or "ok"
                if prev not in {"ok", "partial", "failed"}:
                    prev = "ok"
                conn.execute(
                    "UPDATE articles SET extraction_status = ?, skip_reason = '' WHERE id = ?",
                    (prev, row["id"]),
                )
                restored += 1
        else:
            if status in {"ok", "partial", "failed"}:
                conn.execute(
                    "UPDATE articles SET extraction_status = 'filtered_keyword', skip_reason = ? WHERE id = ?",
                    (f"filtered_keyword:{status}", row["id"]),
                )
                removed += 1
    return {"removed": removed, "restored": restored}


def record_run(
conn: sqlite3.Connection, started_at: str, finished_at: str, stats_json: str) -> None:
    conn.execute(
        "INSERT INTO runs (started_at, finished_at, stats_json) VALUES (?, ?, ?)",
        (started_at, finished_at, stats_json),
    )
