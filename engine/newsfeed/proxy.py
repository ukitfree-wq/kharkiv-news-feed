"""Optional relay for sources that block GitHub Actions runners (048.ua).

When PROXY_048_URL and PROXY_048_KEY are both set, every request a session makes
to https://www.048.ua/ or https://048.ua/ (RSS feed, JSON API, article pages) is
sent to the PHP relay instead (engine/ci/feedproxy-048.php):

    GET <PROXY_048_URL>?url=<original URL>      header X-Proxy-Key: <key>

The response keeps the original URL (or the relay's X-Final-URL after an
allowed redirect), so canonical links, dedup keys and image URLs in the feed
stay 048.ua's own. The key only travels in a request header; it is never put in
a URL, logged, or stored. Without the env vars nothing is mounted and requests
go direct, exactly as before. If the relay answers with anything that is not a
relay reply (e.g. the hosting's "One moment, please..." anti-bot page), one
warning is logged and 048.ua is fetched directly for the rest of the run.
"""
from __future__ import annotations

import logging
import os
import re
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger(__name__)

RELAY_HOSTS = ("www.048.ua", "048.ua")
KEY_HEADER = "X-Proxy-Key"


class RelayAdapter(HTTPAdapter):
    """Sends matching requests to the relay; falls back to a direct request when
    the relay is unusable (anti-bot challenge page, wrong key, network error).
    A real relay reply always carries X-Final-URL; anything without it is not
    the relay talking. After the first failure the relay is skipped for the rest
    of the run, and exactly one log line explains why."""

    disabled_reason: str | None = None  # shared by all adapters in this process

    def __init__(self, relay_url: str, key: str, hosts: tuple[str, ...] = RELAY_HOSTS):
        super().__init__()
        self._relay = relay_url
        self._key = key
        self._hosts = hosts

    def __repr__(self) -> str:  # never show the key
        return f"RelayAdapter({self._relay!r})"

    def _disable(self, reason: str) -> None:
        if RelayAdapter.disabled_reason is None:
            RelayAdapter.disabled_reason = reason
            log.warning("048 relay unusable (%s); fetching 048.ua directly for the rest of this run", reason)

    def send(self, request, **kwargs):  # type: ignore[override]
        if RelayAdapter.disabled_reason is not None:
            return super().send(request, **kwargs)
        original = request.url
        sep = "&" if "?" in self._relay else "?"
        request.url = f"{self._relay}{sep}url={quote(original, safe='')}"
        request.headers[KEY_HEADER] = self._key
        resp = None
        failure = ""
        try:
            resp = super().send(request, **kwargs)
        except requests.RequestException as exc:
            failure = f"relay request failed: {type(exc).__name__}"
        finally:
            request.url = original
            request.headers.pop(KEY_HEADER, None)
        if resp is not None:
            final = resp.headers.get("X-Final-URL") or ""
            if final:
                host = (urlparse(final).hostname or "").lower()
                resp.url = final if final.startswith("https://") and host in self._hosts else original
                return resp
            failure = _describe_non_relay(resp)
            resp.close()
        self._disable(failure)
        return super().send(request, **kwargs)


def _describe_non_relay(resp) -> str:
    ctype = (resp.headers.get("Content-Type") or "?").split(";")[0].strip()
    what = f"HTTP {resp.status_code} {ctype}, no X-Final-URL"
    try:
        head = resp.content[:20000].decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        head = ""
    m = re.search(r"<title[^>]*>\s*([^<]{1,80})", head, re.I)
    if m:
        what += f', page title "{m.group(1).strip()}"'
    server = (resp.headers.get("Server") or "").split("/")[0]
    if server:
        what += f", server {server}"
    if "wsidchk" in head or "One moment, please" in head:
        what += " (hosting anti-bot challenge)"
    elif resp.status_code == 415 and server.lower() == "openresty":
        what += " (hosting anti-bot filter: Imunify360 WebShield answers 415 to non-browser Accept headers)"
    elif resp.status_code == 403 and ctype == "text/plain":
        what += " (relay rejected the key or URL)"
    return what


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
