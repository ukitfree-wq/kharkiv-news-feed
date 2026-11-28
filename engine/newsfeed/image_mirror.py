"""Copies images from hosts that block WordPress importers onto the feed's own site.

Some image hosts refuse requests that WP Automatic (or a reader's browser on the
importing site) makes: cdn4.suspilne.media answers 403 to an empty User-Agent,
od-news.com and 2day.kh.ua answer 403 to any foreign Referer. Public image
proxies are no fix (Suspilne's CDN blocks wsrv.nl and i0.wp.com too), so for the
hosts listed in ``image_mirror.hosts`` the engine downloads each image once with
the browser User-Agent and publishes it next to the feed on GitHub Pages, which
serves it to any client. Images on every other host keep their original URL.

``apply`` runs just before a fresh item is stored. ``backfill`` also rewrites
cdn/objectiv URLs still sitting on items already in the output feed (GUIDs and
canonical links are left untouched) so a later import sees working URLs.
``finalize`` writes exactly the files the current feed references into the
staging directory; publish.sh mirrors that directory onto main, so files of
items that left the feed are deleted. A download that fails is retried with
backoff; if every attempt fails the original URL is kept, so an image is never
lost because of the mirror. Suspilne's CDN intermittently answers 403 when
many images are fetched at once; retries and a short gap between requests
are the countermeasure.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from newsfeed.feed_html import _unwrap_picture, image_stem, resolve_img_src

log = logging.getLogger(__name__)

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAX_BYTES = 10 * 1024 * 1024
_RETRYABLE_STATUS = (403, 408, 425, 429, 500, 502, 503, 504)


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    text = str(exc)
    return any(f"HTTP {code}" in text for code in _RETRYABLE_STATUS)




class ImageMirror:
    def __init__(self, cfg: dict, repo_root: Path, staging: Path, user_agent: str, timeout: float = 20):
        self.hosts = {h.lower() for h in (cfg.get("hosts") or [])}
        self.base_url = (cfg.get("base_url") or "").rstrip("/") + "/"
        self.public_dir = repo_root / (cfg.get("public_dir") or "img")
        self.staging = staging
        self.max_per_item = int(cfg.get("max_per_item") or 30)
        self.timeout = timeout
        self.max_attempts = int(cfg.get("max_attempts") or 4)
        self.backoff_seconds = float(cfg.get("backoff_seconds") or 0.6)
        self.request_gap_seconds = float(cfg.get("request_gap_seconds") or 0.25)
        self._sleep = time.sleep
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.4",
            "Sec-Fetch-Dest": "image",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "cross-site",
        })
        self.downloaded = 0
        self.failed = 0
        self._done: dict[str, str] = {}
        if self.staging.exists():
            shutil.rmtree(self.staging)
        self.staging.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.hosts and self.base_url != "/")

    def _wanted(self, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == h or host.endswith("." + h) for h in self.hosts)

    def _name_stem(self, url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        return hashlib.sha1(f"{host}{image_stem(url)}".encode()).hexdigest()[:20]

    def _existing(self, stem: str) -> Path | None:
        for d in (self.staging, self.public_dir):
            for ext in _EXT.values():
                p = d / f"{stem}{ext}"
                if p.is_file() and p.stat().st_size > 0:
                    return p
        return None

    def mirror_url(self, url: str, referer: str = "") -> str:
        """Mirrored URL for ``url`` (downloading it if needed), or ``url`` unchanged."""
        if not url or not self._wanted(url):
            return url
        if url in self._done:
            return self._done[url]
        stem = self._name_stem(url)
        found = self._existing(stem)
        if found is None:
            found = self._download(url, stem, referer)
            if found is None:
                self._done[url] = url
                return url
        mirrored = self.base_url + found.name
        self._done[url] = mirrored
        return mirrored

    def _gap(self) -> None:
        gap = self.request_gap_seconds
        if gap <= 0:
            return
        now = time.monotonic()
        wait = self._last_request_at + gap - now
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()

    def _download(self, url: str, stem: str, referer: str) -> Path | None:
        """Fetch ``url`` with retries/backoff. Returns the staged file, or None."""
        headers = {"Referer": referer} if referer else {}
        last_exc: Exception | None = None
        attempts = max(1, self.max_attempts)
        for attempt in range(1, attempts + 1):
            try:
                self._gap()
                r = self.session.get(url, headers=headers, timeout=self.timeout, stream=True)
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if r.status_code != 200 or ctype not in _EXT:
                    raise ValueError(f"HTTP {r.status_code} {ctype or 'no content-type'}")
                data = b""
                for chunk in r.iter_content(65536):
                    data += chunk
                    if len(data) > _MAX_BYTES:
                        raise ValueError("larger than 10 MB")
                if not data:
                    raise ValueError("empty body")
                found = self.staging / f"{stem}{_EXT[ctype]}"
                found.write_bytes(data)
                self.downloaded += 1
                if attempt > 1:
                    log.info("image mirror: downloaded %s on attempt %d/%d", url[:120], attempt, attempts)
                return found
            except Exception as exc:
                last_exc = exc
                retryable = _retryable(exc)
                log.warning(
                    "image mirror: %s attempt %d/%d failed (%s)%s",
                    url[:160],
                    attempt,
                    attempts,
                    exc,
                    "; retrying" if retryable and attempt < attempts else "",
                )
                if not retryable or attempt >= attempts:
                    break
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        self.failed += 1
        log.warning("image mirror: keeping original %s (%s)", url[:160], last_exc)
        return None

    def apply(self, item: dict) -> None:
        """Rewrite a new item's images on mirrored hosts (content, lead, image list)."""
        if not self.enabled:
            return
        page = item.get("canonical_url") or ""
        budget = [self.max_per_item]

        def m(url: str) -> str:
            if not url or not self._wanted(url) or budget[0] <= 0:
                return url
            budget[0] -= 1
            return self.mirror_url(url, page)

        html = item.get("content_html") or ""
        if html and any(h in html for h in self.hosts):
            soup = BeautifulSoup(html, "lxml")
            body = soup.body or soup
            changed = False
            for pic in list(body.find_all("picture")):
                img = pic.find("img")
                src = resolve_img_src(img, page) if img is not None else ""
                if src and self._wanted(src):
                    _unwrap_picture(pic, page)
                    changed = True
            for img in body.find_all("img"):
                src = resolve_img_src(img, page)
                if not src or not self._wanted(src):
                    continue
                new = m(src)
                if new != src:
                    img.attrs = {"src": new, "alt": img.get("alt") or ""}
                    changed = True
            if changed:
                item["content_html"] = "".join(str(c) for c in body.contents)
        if item.get("lead_image"):
            item["lead_image"] = m(item["lead_image"])
        if item.get("images"):
            item["images"] = [m(u) if isinstance(u, str) else u for u in item["images"]]

    def backfill(self, rows: list[dict]) -> dict:
        """Rewrite leftover mirrored-host URLs on items already in the feed.

        Updates ``content_html``, ``lead_image`` and ``images`` in place. Does
        not touch GUIDs, canonical URLs or links. Social-card (automatic-og)
        images are left for sanitize_feed_html to drop when a lead exists.
        """
        if not self.enabled:
            return {"items": 0, "urls": 0}
        items_changed = 0
        urls_before = 0
        for row in rows:
            html = row.get("content_html") or ""
            lead = row.get("lead_image") or ""
            images = row.get("images")
            if images is None:
                raw = row.get("images_json") or "[]"
                try:
                    images = json.loads(raw) if isinstance(raw, str) else list(raw or [])
                except json.JSONDecodeError:
                    images = []
            if not isinstance(images, list):
                images = []
            item = {
                "canonical_url": row.get("canonical_url") or row.get("source_url") or "",
                "content_html": html,
                "lead_image": lead,
                "images": list(images),
            }
            pending = [u for u in [lead, *images] if isinstance(u, str) and self._wanted(u)]
            if html:
                pending.extend(u for u in _urls_in(html) if self._wanted(u))
            # unique pending originals
            seen = set()
            uniq = []
            for u in pending:
                if u not in seen:
                    seen.add(u)
                    uniq.append(u)
            if not uniq:
                continue
            urls_before += len(uniq)
            self.apply(item)
            if (
                item["content_html"] != html
                or item["lead_image"] != lead
                or item["images"] != images
            ):
                row["content_html"] = item["content_html"]
                row["lead_image"] = item["lead_image"]
                row["images"] = item["images"]
                row["images_json"] = json.dumps(item["images"], ensure_ascii=False)
                row["_mirror_backfilled"] = True
                items_changed += 1
        log.info(
            "image mirror backfill: rewrote %d item(s), %d original URL(s) were candidates",
            items_changed,
            urls_before,
        )
        return {"items": items_changed, "urls": urls_before}

    def finalize(self, rows) -> None:
        """Stage exactly the mirrored files the output feed references."""
        if not self.enabled:
            return
        pat = re.compile(re.escape(self.base_url) + r"([0-9a-f]{20}\.(?:jpg|png|webp|gif))")
        wanted: set[str] = set()
        for r in rows:
            keys = r.keys()
            for k in ("content_html", "lead_image", "images_json"):
                if k in keys and r[k]:
                    wanted.update(pat.findall(r[k]))
        missing = 0
        for name in sorted(wanted):
            dst = self.staging / name
            if dst.is_file():
                continue
            src = self.public_dir / name
            if src.is_file():
                shutil.copy2(src, dst)
            else:
                missing += 1
        for p in self.staging.iterdir():
            if p.name not in wanted:
                p.unlink()
        log.info(
            "image mirror: %d file(s) referenced by the feed, %d downloaded this run, %d kept original, %d missing",
            len(wanted), self.downloaded, self.failed, missing,
        )


def _urls_in(html: str) -> list[str]:
    return re.findall(r'https?://[^\s"\'<>]+', html or "")


def from_config(cfg: dict, repo_root: Path, output_feed: str) -> ImageMirror | None:
    mc = cfg.get("image_mirror") or {}
    if not mc.get("hosts"):
        return None
    ua = (cfg.get("extraction") or {}).get("user_agent") or "Mozilla/5.0"
    staging = Path(output_feed).parent / "img"
    return ImageMirror(mc, repo_root, staging, ua)
