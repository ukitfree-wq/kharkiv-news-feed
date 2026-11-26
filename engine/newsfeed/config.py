"""Load config.yaml and the feed list (feeds.yaml or feeds.txt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "feeds_file": "feeds.yaml",
    "title_keywords_file": "title_keywords.txt",
    "region": {
        "hl": "uk",
        "gl": "UA",
        "ceid": "UA:uk",
        "language": "uk",
        "feed_title": "Харків — агреговані новини",
        "feed_description": "Повний текст, світлини та вбудовані відео з харківських видань.",
        "feed_link": "https://infocity.kharkiv.ua/",
    },
    "max_age_minutes": 30,
    "future_grace_minutes": 5,
    "default_timezone": "Europe/Kiev",
    "feed_retention": {"max_items": 200, "max_age_days": 3},
    "window": {"max_items": 200, "max_age_days": 3, "ttl_minutes": 5},
    "extraction": {
        "include_failed": True,
        "min_text_chars": 250,
        "timeout_seconds": 15,
        "concurrency": 5,
        "max_new_articles_per_run": 150,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "accept_language": "uk-UA,uk;q=0.9,ru;q=0.5,en;q=0.4",
    },
    "decode": {
        "timeout_seconds": 20,
        "chunk_size": 5,
        "interval": 0.35,
        "pause_between_chunks": 1.0,
        "wave_size": 20,
    },
    "dedup": {
        "title_similarity_threshold": 87,
        "content_similarity_threshold": 0.58,
        "lookback_days": 14,
    },
    "paths": {
        "database": "data/state.db",
        "output_feed": "output/feed.xml",
        "extra_output_feed": "/workspace/news_feed.xml",
    },
    "request": {"rss_timeout_seconds": 20},
}


def _merge(base: dict, override: dict | None) -> dict:
    out = dict(base)
    if not override:
        return out
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], val)
        else:
            out[key] = val
    return out


def _normalize_feed(entry: Any, index: int) -> dict[str, Any] | None:
    if isinstance(entry, str):
        url = entry.strip()
        if not url or url.startswith("#"):
            return None
        return {"id": f"feed-{index}", "url": url, "enabled": True, "category": "", "name": ""}
    if not isinstance(entry, dict) or not entry.get("url"):
        return None
    return {
        "id": str(entry.get("id") or f"feed-{index}"),
        "url": str(entry["url"]).strip(),
        "enabled": bool(entry.get("enabled", True)),
        "category": str(entry.get("category") or ""),
        "name": str(entry.get("name") or ""),
    }


def load_feeds_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    if path.suffix.lower() in {".yaml", ".yml"}:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            entries = raw.get("feeds") or raw.get("urls") or []
        elif isinstance(raw, list):
            entries = raw
        else:
            entries = []
    else:
        # feeds.txt: one URL per line, # comments
        entries = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                entries.append(line)
    feeds = []
    for i, entry in enumerate(entries):
        item = _normalize_feed(entry, i)
        if item:
            feeds.append(item)
    return feeds


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or (ROOT / "config.yaml")
    raw: dict[str, Any] = {}
    if cfg_path.is_file():
        with cfg_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    cfg = _merge(DEFAULTS, raw)

    for key in ("database", "output_feed"):
        p = Path(cfg["paths"][key])
        cfg["paths"][key] = str(p if p.is_absolute() else ROOT / p)
    extra = cfg["paths"].get("extra_output_feed")
    if extra:
        cfg["paths"]["extra_output_feed"] = str(Path(extra))

    feeds: list[dict[str, Any]] = []
    if raw.get("feeds"):
        feeds = [f for f in (_normalize_feed(e, i) for i, e in enumerate(raw["feeds"])) if f]
    else:
        feeds_path = Path(cfg.get("feeds_file") or "feeds.yaml")
        if not feeds_path.is_absolute():
            feeds_path = ROOT / feeds_path
        feeds = load_feeds_file(feeds_path)
        if not feeds:
            txt = ROOT / "feeds.txt"
            if txt.is_file():
                feeds = load_feeds_file(txt)

    # Fallback: build Google News topic URLs from region + topics, if present.
    if not feeds and raw.get("topics"):
        region = cfg["region"]
        query = (
            f"hl={quote(str(region.get('hl') or 'pl'))}"
            f"&gl={quote(str(region.get('gl') or 'PL'))}"
            f"&ceid={quote(str(region.get('ceid') or 'PL:pl'), safe=':')}"
        )
        for i, topic in enumerate(raw["topics"]):
            topic = str(topic).strip()
            if topic.upper() == "TOP":
                url = f"https://news.google.com/rss?{query}"
            else:
                url = f"https://news.google.com/rss/headlines/section/topic/{quote(topic)}?{query}"
            feeds.append({"id": topic.lower(), "url": url, "enabled": True, "category": topic.upper(), "name": topic})

    cfg["feeds"] = feeds
    cfg["feeds_enabled"] = [f for f in feeds if f.get("enabled", True)]

    # feed_retention is the official output-window setting; fall back to window.*
    retention = cfg.get("feed_retention") or {}
    window = cfg.get("window") or {}
    cfg["feed_retention"] = {
        "max_items": int(retention.get("max_items") or window.get("max_items") or 200),
        "max_age_days": int(retention.get("max_age_days") or window.get("max_age_days") or 3),
    }
    cfg["max_age_minutes"] = int(cfg.get("max_age_minutes") or 30)
    kw_path = Path(cfg.get("title_keywords_file") or "title_keywords.txt")
    if not kw_path.is_absolute():
        kw_path = ROOT / kw_path
    cfg["title_keywords_file"] = str(kw_path)
    cfg["future_grace_minutes"] = int(cfg.get("future_grace_minutes") or 5)
    return cfg
