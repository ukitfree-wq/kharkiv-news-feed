"""Fetch arbitrary RSS/Atom feeds with conditional GET (ETag / Last-Modified)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests

from newsfeed.dedup import is_google_news_url
from newsfeed.store import get_feed_meta, upsert_feed_meta

log = logging.getLogger(__name__)


def _publisher_from_title(title: str) -> tuple[str, str]:
    if " - " in (title or ""):
        head, _, tail = title.rpartition(" - ")
        if head and tail and len(tail) <= 60:
            return head.strip(), tail.strip()
    return (title or "").strip(), ""


def _pub_iso(entry: Any) -> str | None:
    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        try:
            dt = datetime(*parsed[:6], tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass
    return None


def _entry_link(entry: Any) -> str:
    link = (entry.get("link") or "").strip()
    if link:
        return link
    for cand in entry.get("links") or []:
        href = (cand.get("href") or "").strip()
        if href:
            return href
    return (entry.get("id") or entry.get("guid") or "").strip()


def _entry_guid(entry: Any) -> str:
    return (entry.get("id") or entry.get("guid") or _entry_link(entry) or "").strip()


def fetch_feeds(
    feeds: list[dict[str, Any]],
    conn,
    user_agent: str,
    timeout: float,
    accept_language: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Download enabled feeds. Returns (items, stats).

    RSS/Atom bodies are treated as metadata only — full text is always
    scraped from the article page later.
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            "Accept-Language": accept_language,
        }
    )
    items: list[dict[str, Any]] = []
    seen_links: set[str] = set()
    stats = {"feeds_ok": 0, "feeds_not_modified": 0, "feeds_failed": 0, "items": 0}

    for feed in feeds:
        url = feed["url"]
        feed_id = feed.get("id") or url
        meta = get_feed_meta(conn, url)
        headers: dict[str, str] = {}
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
        try:
            resp = session.get(url, headers=headers, timeout=timeout)
        except Exception as exc:
            log.error("failed to fetch feed %s (%s): %s", feed_id, url, exc)
            upsert_feed_meta(conn, url, feed_id=feed_id, last_status=0, last_error=str(exc))
            stats["feeds_failed"] += 1
            continue

        if resp.status_code == 304:
            upsert_feed_meta(conn, url, feed_id=feed_id, last_status=304, last_error=None)
            log.info("feed %s not modified (304)", feed_id)
            stats["feeds_not_modified"] += 1
            continue
        if resp.status_code >= 400:
            log.error("feed %s HTTP %s (%s)", feed_id, resp.status_code, url)
            upsert_feed_meta(conn, url, feed_id=feed_id, last_status=resp.status_code, last_error=f"http:{resp.status_code}")
            stats["feeds_failed"] += 1
            continue

        parsed = feedparser.parse(resp.content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            log.error("failed to parse feed %s: %s", feed_id, getattr(parsed, "bozo_exception", ""))
            upsert_feed_meta(conn, url, feed_id=feed_id, last_status=resp.status_code, last_error="parse_error")
            stats["feeds_failed"] += 1
            continue

        upsert_feed_meta(
            conn,
            url,
            feed_id=feed_id,
            etag=resp.headers.get("ETag"),
            last_modified=resp.headers.get("Last-Modified"),
            last_status=resp.status_code,
            last_error=None,
        )
        stats["feeds_ok"] += 1
        feed_title = (parsed.feed.get("title") or feed.get("name") or "").strip()
        count = 0
        for entry in parsed.entries:
            link = _entry_link(entry)
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            raw_title = (entry.get("title") or "").strip()
            headline, suffix_pub = _publisher_from_title(raw_title)
            # For Google News titles the suffix is the publisher; otherwise use the feed title.
            publisher = feed.get("name") or feed_title
            if is_google_news_url(link) and suffix_pub:
                headline = headline or raw_title
                publisher = suffix_pub
            else:
                headline = raw_title or headline
            author = ""
            if entry.get("author"):
                author = entry.get("author")
            raw_pub = (entry.get("published") or entry.get("updated") or "").strip()
            items.append(
                {
                    "source_url": link,
                    "google_url": link if is_google_news_url(link) else None,
                    "guid": _entry_guid(entry),
                    "feed_id": feed_id,
                    "feed_url": url,
                    "title": headline or raw_title,
                    "raw_title": raw_title,
                    "publisher": publisher,
                    "author": author,
                    "category": feed.get("category") or "",
                    "published_at": _pub_iso(entry),
                    "feed_published_raw": raw_pub,
                    # Kept only as a short teaser / fallback description — never as full text.
                    "summary": "",
                }
            )
            count += 1
        host = urlparse(url).hostname or feed_id
        log.info("feed %s (%s): %d items", feed_id, host, count)
        stats["items"] += count

    return items, stats
