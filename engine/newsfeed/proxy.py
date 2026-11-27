"""Optional relay for sources that block GitHub Actions runners (048.ua).

When PROXY_048_URL and PROXY_048_KEY are both set, every request a session makes
to https://www.048.ua/ or https://048.ua/ (RSS feed, JSON API, article pages) is
sent to the PHP relay instead (engine/ci/feedproxy-048.php):

    GET <PROXY_048_URL>?url=<original URL>      header X-Proxy-Key: <key>

The response keeps the original URL (or the relay's X-Final-URL after an
allowed redirect), so canonical links, dedup keys and image URLs in the feed
stay 048.ua's own. The key only travels in a request header; it is never put in
a URL, logged, or stored. Without the env vars nothing is mounted and requests
go direct, exactly as before.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

RELAY_HOSTS = ("www.048.ua", "048.ua")
KEY_HEADER = "X-Proxy-Key"


class RelayAdapter(HTTPAdapter):
    def __init__(self, relay_url: str, key: str, hosts: tuple[str, ...] = RELAY_HOSTS):
        super().__init__()
        self._relay = relay_url
        self._key = key
        self._hosts = hosts

    def __repr__(self) -> str:  # never show the key
        return f"RelayAdapter({self._relay!r})"

    def send(self, request, **kwargs):  # type: ignore[override]
        original = request.url
        sep = "&" if "?" in self._relay else "?"
        request.url = f"{self._relay}{sep}url={quote(original, safe='')}"
        request.headers[KEY_HEADER] = self._key
        try:
            resp = super().send(request, **kwargs)
        finally:
            request.url = original
            request.headers.pop(KEY_HEADER, None)
        final = resp.headers.get("X-Final-URL") or ""
        host = (urlparse(final).hostname or "").lower()
        resp.url = final if final.startswith("https://") and host in self._hosts else original
        return resp


def relay_config() -> tuple[str, str] | None:
    url = (os.environ.get("PROXY_048_URL") or "").strip()
    key = (os.environ.get("PROXY_048_KEY") or "").strip()
    if url and key:
        return url, key
    return None


_announced = False


def install(session: requests.Session) -> requests.Session:
    """Route 048.ua through the relay on this session if it is configured."""
    global _announced
    cfg = relay_config()
    if not cfg:
        return session
    adapter = RelayAdapter(*cfg)
    for host in RELAY_HOSTS:
        session.mount(f"https://{host}/", adapter)
        session.mount(f"http://{host}/", adapter)
    if not _announced:
        log.info("048.ua requests go through the relay at %s", urlparse(cfg[0]).netloc)
        _announced = True
    return session
