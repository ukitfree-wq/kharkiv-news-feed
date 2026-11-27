"""Copies images from hosts that block WordPress importers onto the feed's own site.

Some image hosts refuse requests that WP Automatic (or a reader's browser on the
importing site) makes: cdn4.suspilne.media answers 403 to an empty User-Agent,
od-news.com and 2day.kh.ua answer 403 to any foreign Referer. Public image
proxies are no fix (Suspilne's CDN blocks wsrv.nl and i0.wp.com too), so for the
hosts listed in ``image_mirror.hosts`` the engine downloads each image once with
the browser User-Agent and publishes it next to the feed on GitHub Pages, which
serves it to any client. Images on every other host keep their original URL.

Only new items are rewritten (``apply`` runs just before a fresh item is
stored); items already in the feed keep the URLs they were published with.
``finalize`` writes exactly the files the current feed references into the
staging directory; publish.sh mirrors that directory onto main, so files of
items that left the feed are deleted. A download that fails keeps the original
URL, so an image is never lost because of the mirror.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from newsfeed.feed_html import _unwrap_picture, image_stem, resolve_img_src

log = logging.getLogger(__name__)

_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}
_MAX_BYTES = 10 * 1024 * 1024


class ImageMirror:
    def __init__(self, cfg: dict, repo_root: Path, staging: Path, user_agent: str, timeout: float = 20):
        self.hosts = {h.lower() for h in (cfg.get("hosts") or [])}
        self.base_url = (cfg.get("base_url") or "").rstrip("/") + "/"
        self.public_dir = repo_root / (cfg.get("public_dir") or "img")
        self.staging = staging
        self.max_per_item = int(cfg.get("max_per_item") or 30)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
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
            try:
                headers = {"Referer": referer} if referer else {}
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
            except Exception as exc:
                self.failed += 1
                log.warning("image mirror: keeping original %s (%s)", url[:120], exc)
                self._done[url] = url
                return url
        mirrored = self.base_url + found.name
        self._done[url] = mirrored
        return mirrored

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


def from_config(cfg: dict, repo_root: Path, output_feed: str) -> ImageMirror | None:
    mc = cfg.get("image_mirror") or {}
    if not mc.get("hosts"):
        return None
    ua = (cfg.get("extraction") or {}).get("user_agent") or "Mozilla/5.0"
    staging = Path(output_feed).parent / "img"
    return ImageMirror(mc, repo_root, staging, ua)
