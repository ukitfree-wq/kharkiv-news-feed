"""End-to-end RSS/Atom -> full-text RSS update."""

from __future__ import annotations

import json
import logging
import sys
import time
from urllib.parse import urlparse

from datetime import datetime, timezone

from newsfeed.config import ROOT, load_config
from newsfeed.dates import (
    classify_feed_date,
    default_zone,
    format_kiev,
    freshness_of,
    parse_datetime,
    to_utc_naive,
)
from newsfeed.decode import decode_google_urls
from newsfeed.dedup import (
    canonicalize_url,
    completeness,
    content_minhash,
    contents_similar,
    is_google_news_url,
    minhash_from_str,
    minhash_similarity,
    minhash_to_str,
    normalize_title,
    title_hash,
    titles_similar,
    url_hash,
)
from newsfeed.extract import extract_many
from newsfeed.feed_write import render_feed, write_feed
from newsfeed.keywords import compile_keywords, load_title_keywords, near_misses, title_match
from newsfeed.rss_fetch import fetch_feeds
from newsfeed.sites import item_url_allowed, profile_for
from newsfeed.semantic_dedup import extract_dates
from newsfeed.store import (
    connect,
    feed_candidates,
    has_url_hash,
    insert_article,
    now_iso,
    normalize_ts,
    apply_title_keyword_purge,
    recent_for_dedup,
    record_run,
    seen_guids,
    seen_source_urls,
)

log = logging.getLogger("newsfeed")

DUP_STATUSES = {"duplicate_url", "duplicate_title", "duplicate_content", "duplicate_semantic", "decode_failed", "too_old", "no_date", "filtered_keyword"}


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _row_as_item(row) -> dict:
    images = []
    embeds = []
    try:
        images = json.loads(row["images_json"] or "[]")
    except Exception:
        images = []
    try:
        embeds = json.loads(row["embeds_json"] or "[]") if "embeds_json" in row.keys() else []
    except Exception:
        embeds = []
    return {
        "id": row["id"],
        "title": row["title"],
        "title_norm": row["title_norm"],
        "title_hash": row["title_hash"],
        "url_hash": row["url_hash"],
        "canonical_url": row["canonical_url"],
        "source_url": row["source_url"] if "source_url" in row.keys() else "",
        "published_at": row["published_at"],
        "extraction_status": row["extraction_status"],
        "content_text": row["content_text"] or "",
        "content_html": row["content_html"] or "",
        "lead_image": row["lead_image"] or "",
        "images": images,
        "embeds": embeds,
        "minhash": minhash_from_str(row["minhash"] if "minhash" in row.keys() else ""),
        "publisher": row["publisher"] or "",
    }


def _choose_existing_match(item: dict, recent: list[dict], title_th: int, content_th: float):
    """Return (existing, reason) if this item is a near-duplicate of one already kept."""
    uh = item.get("url_hash") or ""
    for other in recent:
        if uh and other.get("url_hash") == uh:
            return other, "duplicate_url"
    tnorm = item.get("title_norm") or ""
    if tnorm:
        item_title = item.get("title") or ""
        item_dates = extract_dates(item_title)
        for other in recent:
            if not titles_similar(tnorm, other.get("title_norm") or "", title_th):
                continue
            # Objectiv daily wraps / forecasts: same boilerplate, different calendar dates.
            other_dates = extract_dates(other.get("title") or "")
            if item_dates and other_dates and item_dates.isdisjoint(other_dates):
                continue
            return other, "duplicate_title"
    fp = item.get("minhash") or []
    text = item.get("content_text") or ""
    if fp and any(fp) and len(text) >= 200:
        for other in recent:
            other_text = other.get("content_text") or ""
            if len(other_text) < 200:
                continue
            if contents_similar(fp, other.get("minhash") or [], content_th):
                return other, "duplicate_content"
    return None, ""


def _persist_fields(item: dict, started: str) -> dict:
    images = item.get("images") or []
    embeds = item.get("embeds") or []
    text = item.get("content_text") or ""
    return {
        "source_url": item.get("source_url") or item.get("google_url") or item.get("canonical_url") or None,
        "google_url": item.get("google_url") or None,
        "guid": item.get("guid") or "",
        "feed_id": item.get("feed_id") or "",
        "feed_url": item.get("feed_url") or "",
        "canonical_url": item.get("canonical_url") or "",
        "url_hash": item.get("url_hash") or "",
        "title": item.get("title") or "",
        "title_norm": item.get("title_norm") or normalize_title(item.get("title") or ""),
        "title_hash": item.get("title_hash") or title_hash(item.get("title_norm") or ""),
        "publisher": item.get("publisher") or "",
        "category": item.get("category") or "",
        "published_at": normalize_ts(item.get("published_at"), started),
        "fetched_at": now_iso(),
        "extraction_status": item.get("extraction_status") or "failed",
        "lead_image": item.get("lead_image") or "",
        "images_json": json.dumps(images, ensure_ascii=False),
        "embeds_json": json.dumps(embeds, ensure_ascii=False),
        "minhash": minhash_to_str(item.get("minhash") or []),
        "content_len": len(text),
        "content_html": item.get("content_html") or "",
        "content_text": text,
        "summary": item.get("summary") or "",
        "skip_reason": item.get("skip_reason") or "",
    }


def scan_feed_duplicates(rows, title_th: int, content_th: float) -> list[dict]:
    """Pairwise near-duplicate scan of items already in the output feed."""
    items = []
    for row in rows:
        item = _row_as_item(row)
        if not item.get("minhash"):
            item["minhash"] = content_minhash(item.get("content_text") or "")
        items.append(item)
    pairs: list[dict] = []
    for i, a in enumerate(items):
        for b in items[i + 1 :]:
            reason = None
            score = None
            if a.get("url_hash") and a.get("url_hash") == b.get("url_hash"):
                reason, score = "url", 1.0
            elif titles_similar(a.get("title_norm") or "", b.get("title_norm") or "", title_th):
                from rapidfuzz import fuzz
                reason = "title"
                score = fuzz.token_set_ratio(a.get("title_norm") or "", b.get("title_norm") or "") / 100.0
            else:
                ta, tb = a.get("content_text") or "", b.get("content_text") or ""
                if len(ta) >= 200 and len(tb) >= 200:
                    sim = minhash_similarity(a.get("minhash") or [], b.get("minhash") or [])
                    if sim >= content_th:
                        reason, score = "content", round(sim, 3)
            if reason:
                pairs.append(
                    {
                        "reason": reason,
                        "score": score,
                        "a": {"title": a.get("title"), "source": a.get("publisher"), "url": a.get("canonical_url")},
                        "b": {"title": b.get("title"), "source": b.get("publisher"), "url": b.get("canonical_url")},
                    }
                )
    return pairs


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    started = now_iso()
    t0 = time.monotonic()
    cfg = load_config()
    region = cfg["region"]
    extract_cfg = cfg["extraction"]
    decode_cfg = cfg["decode"]
    dedup_cfg = cfg["dedup"]
    window = cfg["window"]
    retention = cfg.get("feed_retention") or {
        "max_items": window.get("max_items", 200),
        "max_age_days": window.get("max_age_days", 3),
    }
    max_age_minutes = int(cfg.get("max_age_minutes") or 30)
    future_grace_minutes = int(cfg.get("future_grace_minutes") or 5)
    tz = default_zone(cfg.get("default_timezone"))
    now = datetime.now(timezone.utc)
    paths = cfg["paths"]
    feeds = cfg.get("feeds_enabled") or []
    keywords = load_title_keywords(cfg["title_keywords_file"])
    keyword_index = compile_keywords(keywords)
    if not keywords:
        log.error("title keyword file is empty: %s", cfg["title_keywords_file"])
        return 1

    log.info(
        "starting update; %d enabled feed(s): %s; intake last %d min; retention %s day(s) / %s items; title keywords=%d (%s)",
        len(feeds),
        ", ".join(f["id"] for f in feeds) or "(none)",
        max_age_minutes,
        retention.get("max_age_days"),
        retention.get("max_items"),
        len(keywords),
        cfg["title_keywords_file"],
    )
    if not feeds:
        log.error("no enabled feeds in feeds.yaml / config — nothing to do")
        return 1

    conn = connect(paths["database"])
    stats = {
        "feeds_ok": 0,
        "feeds_not_modified": 0,
        "feeds_failed": 0,
        "rss_items": 0,
        "already_seen": 0,
        "keyword_matched": 0,
        "keyword_filtered": 0,
        "filtered_keyword": 0,
        "filtered_section": 0,
        "purged_from_feed": 0,
        "restored_to_feed": 0,
        "decoded": 0,
        "decode_failed": 0,
        "url_duplicates": 0,
        "title_duplicates": 0,
        "content_duplicates": 0,
        "semantic_duplicates": 0,
        "skipped_too_old": 0,
        "scraped_unreliable_date": 0,
        "dropped_page_too_old": 0,
        "no_date": 0,
        "fresh_collected": 0,
        "capped": 0,
        "fetched": 0,
        "extracted_ok": 0,
        "extracted_partial": 0,
        "extracted_failed": 0,
        "feed_items": 0,
        "feed_ok": 0,
        "feed_not_ok": 0,
        "by_source": {},
        "seconds": 0,
        "notable_failures": [],
    }

    items, feed_stats = fetch_feeds(
        feeds,
        conn,
        user_agent=extract_cfg["user_agent"],
        timeout=cfg["request"]["rss_timeout_seconds"],
        accept_language=extract_cfg.get("accept_language") or "uk,en;q=0.8",
    )
    conn.commit()
    stats.update({k: feed_stats.get(k, 0) for k in ("feeds_ok", "feeds_not_modified", "feeds_failed")})
    stats["rss_items"] = len(items)
    log.info("fetched %d unique feed items", len(items))

    # Intake preview for every item in this pull (seen or not), using feed dates only.
    preview = {"fresh": 0, "too_old": 0, "unreliable": 0, "missing": 0}
    for it in items:
        verdict = classify_feed_date(
            it.get("feed_published_raw") or it.get("published_at") or "",
            now,
            max_age_minutes=max_age_minutes,
            future_grace_minutes=future_grace_minutes,
            default_tz=tz,
        )
        preview[verdict["status"] if verdict["status"] in preview else "unreliable"] = (
            preview.get(verdict["status"] if verdict["status"] in preview else "unreliable", 0) + 1
        )
    stats["intake_preview"] = preview
    log.info(
        "intake preview (feed dates, all items): fresh=%d too_old=%d unreliable=%d missing=%d",
        preview["fresh"], preview["too_old"], preview["unreliable"], preview["missing"],
    )

    source_urls = [it["source_url"] for it in items if it.get("source_url")]
    already = seen_source_urls(conn, source_urls)
    guids = [it["guid"] for it in items if it.get("guid")]
    already_guids = seen_guids(conn, guids)
    new_items = [
        it
        for it in items
        if it.get("source_url") not in already and (not it.get("guid") or it["guid"] not in already_guids)
    ]
    stats["already_seen"] = len(items) - len(new_items)
    log.info("%d already in state db, %d new", stats["already_seen"], len(new_items))

    # Title keyword preview for every item in this pull, then filter NEW items
    # before any scrape / Google-News decode.
    matched_titles: list[str] = []
    filtered_titles: list[str] = []
    near: list[tuple[str, list[str]]] = []
    for it in items:
        title = it.get("title") or it.get("raw_title") or ""
        hit = title_match(title, keyword_index)
        if hit:
            matched_titles.append(title)
        else:
            filtered_titles.append(title)
            misses = near_misses(title, keyword_index)
            if misses:
                near.append((title, misses))
    stats["keyword_matched"] = len(matched_titles)
    stats["keyword_filtered"] = len(filtered_titles)
    stats["keyword_near_misses"] = [{"title": t, "hints": h} for t, h in near]
    log.info(
        "title keywords: %d matched, %d filtered (of %d items in this pull)",
        len(matched_titles),
        len(filtered_titles),
        len(items),
    )
    if near:
        log.info("title keyword near-misses (forms not in the list):")
        for title, hints in near:
            log.info("  %s  [%s]", title, ", ".join(hints))

    still_new = []
    for it in new_items:
        title = it.get("title") or it.get("raw_title") or ""
        url = it.get("canonical_url") or it.get("source_url") or ""
        if title_match(title, keyword_index):
            if item_url_allowed(url, profile_for(url)):
                still_new.append(it)
                continue
            stats["filtered_section"] += 1
            it["extraction_status"] = "filtered_section"
            it["skip_reason"] = "filtered_section"
            if not it.get("canonical_url"):
                it["canonical_url"] = url
            insert_article(conn, _persist_fields(it, started))
            log.info("filter (other regional section): %s", title[:100])
            continue
        stats["filtered_keyword"] += 1
        it["extraction_status"] = "filtered_keyword"
        it["skip_reason"] = "filtered_keyword"
        if not it.get("canonical_url"):
            it["canonical_url"] = url
        insert_article(conn, _persist_fields(it, started))
        log.info("filter (no title keyword): %s", title[:100])
    new_items = still_new
    conn.commit()
    log.info(
        "%d new items left to consider after title filter (%d marked filtered_keyword, %d filtered_section)",
        len(new_items),
        stats["filtered_keyword"],
        stats["filtered_section"],
    )

    # Decode Google News links only.
    google_items = [it for it in new_items if is_google_news_url(it.get("source_url") or "")]
    if google_items:
        decoded = decode_google_urls(
            [it["source_url"] for it in google_items],
            timeout=decode_cfg["timeout_seconds"],
            interval=decode_cfg.get("interval"),
            chunk_size=decode_cfg.get("chunk_size") or 5,
            pause_between_chunks=decode_cfg.get("pause_between_chunks") or 1.0,
            user_agent=extract_cfg.get("user_agent"),
            hl=region.get("hl"),
            gl=region.get("gl"),
            ceid=region.get("ceid"),
        )
        stats["decoded"] = len(decoded)
        kept = []
        for it in new_items:
            if not is_google_news_url(it.get("source_url") or ""):
                kept.append(it)
                continue
            publisher_url = decoded.get(it["source_url"])
            if not publisher_url:
                stats["decode_failed"] += 1
                log.warning("leaving undecoded Google News URL for later: %s", it["source_url"][:80])
                continue
            it = dict(it)
            it["canonical_url"] = canonicalize_url(publisher_url)
            it["google_url"] = it["source_url"]
            kept.append(it)
        new_items = kept

    # Non-Google items already have a real publisher URL.
    for it in new_items:
        if not it.get("canonical_url"):
            it["canonical_url"] = canonicalize_url(it.get("source_url") or "")
        it["url_hash"] = url_hash(it["canonical_url"])
        it["title_norm"] = normalize_title(it.get("title") or it.get("raw_title") or "")
        it["title_hash"] = title_hash(it["title_norm"])

    # Skip scrape if this canonical URL was already added or marked seen.
    to_fetch = []
    for it in new_items:
        existing = has_url_hash(conn, it["url_hash"])
        if existing:
            # Once-only: never scrape or replace a story we have already seen.
            stats["url_duplicates"] += 1
            it["extraction_status"] = "duplicate_url"
            it["skip_reason"] = "duplicate_url"
            it["canonical_url"] = existing["canonical_url"] or it["canonical_url"]
            insert_article(conn, _persist_fields(it, started))
            continue

        raw_pub = it.get("feed_published_raw") or it.get("published_at") or ""
        verdict = classify_feed_date(
            raw_pub,
            now,
            max_age_minutes=max_age_minutes,
            future_grace_minutes=future_grace_minutes,
            default_tz=tz,
        )
        it["feed_date_status"] = verdict["status"]
        it["feed_date_reason"] = verdict["reason"]
        if verdict["dt"] is not None:
            it["feed_published_at"] = to_utc_naive(verdict["dt"])
            it["published_at"] = it["feed_published_at"]

        if verdict["status"] == "too_old":
            stats["skipped_too_old"] += 1
            it["extraction_status"] = "too_old"
            it["skip_reason"] = "too_old_feed_date"
            insert_article(conn, _persist_fields(it, started))
            log.info(
                "skip (too old, feed date): %s [%s] %s",
                it.get("feed_id"),
                format_kiev(verdict["dt"], tz),
                (it.get("title") or "")[:80],
            )
            continue
        if verdict["status"] in {"missing", "unreliable"}:
            stats["scraped_unreliable_date"] += 1
            log.info(
                "feed date %s (%s) — will scrape to confirm: %s",
                verdict["status"],
                verdict["reason"],
                (it.get("title") or "")[:80],
            )
        to_fetch.append(it)
    conn.commit()

    cap = int(extract_cfg["max_new_articles_per_run"])
    if len(to_fetch) > cap:
        stats["capped"] = len(to_fetch) - cap
        log.info("capping new page fetches at %d (%d deferred)", cap, stats["capped"])
        to_fetch = to_fetch[:cap]

    log.info("scraping %d new article pages (never using RSS body as full text)", len(to_fetch))
    extracted = extract_many(
        to_fetch,
        user_agent=extract_cfg["user_agent"],
        accept_language=extract_cfg.get("accept_language") or "uk,en;q=0.8",
        timeout=extract_cfg["timeout_seconds"],
        min_text_chars=extract_cfg["min_text_chars"],
        concurrency=extract_cfg["concurrency"],
    )
    stats["fetched"] = len(extracted)

    title_th = int(dedup_cfg["title_similarity_threshold"])
    content_th = float(dedup_cfg["content_similarity_threshold"])
    lookback_days = int(dedup_cfg.get("lookback_days") or 14)
    # Compare against ALL previously added unique stories (not just the output window).
    recent = [_row_as_item(r) for r in recent_for_dedup(conn, lookback_days)]
    failure_counts: dict[str, int] = {}

    fresh: list[dict] = []
    for item in extracted:
        item["canonical_url"] = canonicalize_url(item.get("canonical_url") or item.get("source_url") or "")
        item["url_hash"] = url_hash(item["canonical_url"])
        item["title_norm"] = normalize_title(item.get("title") or "")
        item["title_hash"] = title_hash(item["title_norm"])
        item["minhash"] = content_minhash(item.get("content_text") or "")

        page_dt = parse_datetime(item.get("page_published_at"), default_tz=tz)
        feed_dt = parse_datetime(item.get("feed_published_at") or item.get("feed_published_raw"), default_tz=tz)
        # Prefer the page published time when we have it.
        chosen_dt = page_dt or feed_dt
        if chosen_dt is not None:
            item["published_at"] = to_utc_naive(chosen_dt)
        else:
            item["published_at"] = item.get("published_at") or None

        verdict = freshness_of(
            chosen_dt,
            now,
            max_age_minutes=max_age_minutes,
            future_grace_minutes=future_grace_minutes,
        )
        if chosen_dt is None:
            stats["no_date"] += 1
            item["extraction_status"] = "no_date"
            item["skip_reason"] = "no_publish_date"
            insert_article(conn, _persist_fields(item, started))
            conn.commit()
            log.info("skip (no publish date after scrape): %s", (item.get("title") or "")[:80])
            continue
        if verdict["status"] == "too_old":
            stats["dropped_page_too_old"] += 1
            item["extraction_status"] = "too_old"
            item["skip_reason"] = "too_old_page_date" if page_dt else "too_old_feed_date"
            insert_article(conn, _persist_fields(item, started))
            conn.commit()
            log.info(
                "drop after scrape (too old, %s): %s [%s]",
                item["skip_reason"],
                (item.get("title") or "")[:80],
                format_kiev(chosen_dt, tz),
            )
            continue
        if verdict["status"] == "unreliable":
            # Far-future page date with no better stamp: do not treat as fresh.
            stats["no_date"] += 1
            item["extraction_status"] = "no_date"
            item["skip_reason"] = "unreliable_page_date"
            insert_article(conn, _persist_fields(item, started))
            conn.commit()
            continue

        status = item.get("extraction_status") or "failed"
        if status == "ok":
            stats["extracted_ok"] += 1
        elif status == "partial":
            stats["extracted_partial"] += 1
        else:
            stats["extracted_failed"] += 1
            host = urlparse(item.get("canonical_url") or "").netloc or "unknown"
            key = f"{host} ({item.get('skip_reason') or status})"
            failure_counts[key] = failure_counts.get(key, 0) + 1
        fresh.append(item)

    # Same-run: earliest publish time first; completeness only breaks a tie.
    def _sort_key(it: dict):
        pub = it.get("published_at") or "9999"
        rank, score = completeness(it)
        return (pub, -rank, -score)

    fresh.sort(key=_sort_key)

    # Optional semantic same-event check (dedup.semantic.enabled). Loaded lazily, only
    # when there is something new to check; any failure disables it for this run.
    semantic = None
    sem_cfg = (dedup_cfg.get("semantic") or {})
    if fresh and sem_cfg.get("enabled"):
        t_sem = time.monotonic()
        try:
            from newsfeed.semantic_dedup import SemanticDeduper
            from newsfeed.store import semantic_reference

            ref_rows, idf_rows = semantic_reference(
                conn,
                lookback_hours=float(sem_cfg.get("lookback_hours") or 48),
                idf_days=lookback_days,
            )
            semantic = SemanticDeduper(sem_cfg, ROOT)
            semantic.load_reference(ref_rows, idf_rows)
            log.info(
                "semantic dedup ready: %d reference stories, model load %.1fs, embed %.1fs",
                len(semantic.docs), semantic.embedder.load_seconds, semantic.embed_seconds,
            )
        except Exception as exc:
            log.warning("semantic dedup disabled for this run: %s", exc)
            semantic = None
        stats["semantic_seconds"] = round(time.monotonic() - t_sem, 2)

    for item in fresh:
        match, reason = _choose_existing_match(item, recent, title_th, content_th)
        sem_scores = None
        if match is None and semantic is not None:
            try:
                key, sem_scores = semantic.check(item)
            except Exception as exc:
                log.warning("semantic check failed for %s: %s", (item.get("title") or "")[:60], exc)
                key, sem_scores = None, None
            if key is not None:
                match = next((o for o in recent if o.get("id") == key), None) or {"id": key, "title": f"id {key}"}
                for o in semantic.docs:
                    if o.key == key:
                        match = dict(match, title=match.get("title") or o.title)
                        break
                reason = "duplicate_semantic"
        if match is not None:
            # Once-only: never replace a story that was already added.
            item["extraction_status"] = reason
            item["skip_reason"] = reason
            if reason == "duplicate_url":
                stats["url_duplicates"] += 1
            elif reason == "duplicate_title":
                stats["title_duplicates"] += 1
            elif reason == "duplicate_semantic":
                stats["semantic_duplicates"] += 1
                sc = sem_scores or {}
                item["skip_reason"] = (
                    f"duplicate_semantic:of={match.get('id')};rule={sc.get('rule')};emb={sc.get('emb')};"
                    f"kw={sc.get('kw')};ent={sc.get('ent')};anchors={','.join(sc.get('anchors') or [])}"
                )
            else:
                stats["content_duplicates"] += 1
            row_id = insert_article(conn, _persist_fields(item, started))
            conn.commit()
            if reason == "duplicate_semantic" and semantic is not None:
                # Keep it as a reference so later rewrites of the same event also match.
                item["id"] = row_id
                semantic.add(item)
            log.info(
                "once-only skip (%s) %s  [kept earlier: %s]",
                reason,
                (item.get("title") or "")[:80],
                (match.get("title") or "")[:80],
            )
            continue

        row_id = insert_article(conn, _persist_fields(item, started))
        item["id"] = row_id
        recent.append(item)
        if semantic is not None:
            semantic.add(item)
        stats["fresh_collected"] += 1
        conn.commit()
        log.info(
            "fresh +%s [%s] %s",
            item.get("feed_id"),
            format_kiev(item.get("published_at"), tz),
            (item.get("title") or "")[:80],
        )

    stats["notable_failures"] = sorted(failure_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:15]

    purge = apply_title_keyword_purge(
        conn, lambda title: title_match(title, keyword_index)
    )
    conn.commit()
    stats["purged_from_feed"] = purge["removed"]
    stats["restored_to_feed"] = purge["restored"]
    if purge["removed"] or purge["restored"]:
        log.info(
            "keyword purge of already-collected items: removed %d from output, restored %d",
            purge["removed"],
            purge["restored"],
        )

    rows = feed_candidates(
        conn,
        max_age_days=int(retention["max_age_days"]),
        max_items=int(retention["max_items"]),
        include_failed=bool(extract_cfg["include_failed"]),
    )
    stats["feed_items"] = len(rows)
    stats["feed_ok"] = sum(1 for r in rows if r["extraction_status"] == "ok")
    stats["feed_not_ok"] = stats["feed_items"] - stats["feed_ok"]

    dup_pairs = scan_feed_duplicates(rows, title_th, content_th)
    stats["feed_near_duplicates"] = len(dup_pairs)
    if dup_pairs:
        log.warning("feed self-check: %d near-duplicate pair(s) still in the output feed:", len(dup_pairs))
        for pair in dup_pairs[:20]:
            log.warning(
                "  %s (score=%s): %s  ||  %s",
                pair["reason"],
                pair["score"],
                (pair["a"]["title"] or "")[:70],
                (pair["b"]["title"] or "")[:70],
            )
    else:
        log.info("feed self-check: no near-duplicate pairs in the current output feed")

    by_source: dict[str, dict[str, int]] = {}
    for r in rows:
        key = r["feed_id"] or r["publisher"] or "unknown"
        bucket = by_source.setdefault(key, {"items": 0, "ok": 0, "partial": 0, "failed": 0})
        bucket["items"] += 1
        st = r["extraction_status"] or "failed"
        if st in bucket:
            bucket[st] += 1
        elif st != "ok":
            bucket["failed"] += 1
    stats["by_source"] = by_source

    xml_text = render_feed(
        channel={
            "title": region["feed_title"],
            "link": region["feed_link"],
            "description": region["feed_description"],
            "language": region["language"],
        },
        items=rows,
        self_link=cfg.get("feed_self_link") or None,
        ttl_minutes=int(window.get("ttl_minutes") or 5),
    )
    write_feed(xml_text, paths["output_feed"], paths.get("extra_output_feed"))

    try:
        import feedparser

        parsed = feedparser.parse(xml_text)
        if parsed.bozo:
            log.error("feedparser reported the feed as invalid: %s", parsed.bozo_exception)
            stats["feed_valid"] = False
            stats["feed_error"] = str(parsed.bozo_exception)
        else:
            stats["feed_valid"] = True
            log.info("feedparser accepted the feed (%d entries)", len(parsed.entries))
    except Exception as exc:
        log.error("feedparser validation failed: %s", exc)
        stats["feed_valid"] = False
        stats["feed_error"] = str(exc)

    finished = now_iso()
    stats["seconds"] = round(time.monotonic() - t0, 2)
    record_run(conn, started, finished, json.dumps(stats, ensure_ascii=False, default=str))
    conn.commit()
    conn.close()

    log.info(
        "done in %.1fs: rss=%d seen=%d keyword_matched=%d keyword_filtered=%d filtered_new=%d purged=%d "
        "skipped_too_old=%d scraped_unreliable=%d "
        "dropped_page_too_old=%d no_date=%d fetched=%d fresh=%d ok=%d partial=%d failed=%d "
        "url_dup=%d title_dup=%d content_dup=%d semantic_dup=%d capped=%d feed_items=%d (ok=%d other=%d) near_dups=%d",
        stats["seconds"],
        stats["rss_items"],
        stats["already_seen"],
        stats["keyword_matched"],
        stats["keyword_filtered"],
        stats["filtered_keyword"],
        stats["purged_from_feed"],
        stats["skipped_too_old"],
        stats["scraped_unreliable_date"],
        stats["dropped_page_too_old"],
        stats["no_date"],
        stats["fetched"],
        stats["fresh_collected"],
        stats["extracted_ok"],
        stats["extracted_partial"],
        stats["extracted_failed"],
        stats["url_duplicates"],
        stats["title_duplicates"],
        stats["content_duplicates"],
        stats["semantic_duplicates"],
        stats["capped"],
        stats["feed_items"],
        stats["feed_ok"],
        stats["feed_not_ok"],
        stats.get("feed_near_duplicates", 0),
    )
    if by_source:
        log.info("by source: %s", by_source)
    if stats["notable_failures"]:
        log.info("notable extraction failures: %s", stats["notable_failures"])
    return 0 if stats.get("feed_valid", False) else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
