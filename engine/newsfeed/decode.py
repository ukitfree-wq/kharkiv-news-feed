"""Resolve Google News RSS article URLs to publisher URLs.

Uses googlenewsdecoder's signature + batchexecute path (base64 IDs are no
longer reliable). Requests are kept small and paced to avoid HTTP 405/429.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from googlenewsdecoder import GoogleDecoder

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


def with_locale(url: str, hl: str | None, gl: str | None, ceid: str | None) -> str:
    """Ensure the Google News URL carries hl/gl/ceid so the signature page matches the edition."""
    if not url:
        return url
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    if hl and "hl" not in qs:
        qs["hl"] = [hl]
    if gl and "gl" not in qs:
        qs["gl"] = [gl]
    if ceid and "ceid" not in qs:
        qs["ceid"] = [ceid]
    query = urlencode({k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in qs.items()}, doseq=True)
    return urlunparse(parsed._replace(query=query))


def _is_rate_limit(message: str) -> bool:
    text = (message or "").lower()
    return "429" in text or "too many" in text or "sorry/index" in text


def _is_method_or_server(message: str) -> bool:
    text = (message or "").lower()
    return "405" in text or "500" in text or "502" in text or "503" in text or "method not allowed" in text


def _decode_chunk(
    decoder: GoogleDecoder,
    chunk: list[str],
    interval: float | None,
) -> dict[str, str]:
    results = decoder.decode_google_news_urls(chunk, interval=interval)
    mapping: dict[str, str] = {}
    failed: list[tuple[str, str]] = []
    if isinstance(results, dict):
        results = [results]
    for url, res in zip(chunk, results or []):
        if isinstance(res, dict) and res.get("success") and res.get("decoded_url"):
            mapping[url] = res["decoded_url"]
        else:
            msg = (res or {}).get("message") if isinstance(res, dict) else "empty"
            failed.append((url, str(msg)))
    return mapping, failed


def decode_google_urls(
    urls: Iterable[str],
    timeout: float = 20.0,
    interval: float | None = 0.35,
    chunk_size: int = 5,
    pause_between_chunks: float = 1.0,
    user_agent: str | None = None,
    hl: str | None = None,
    gl: str | None = None,
    ceid: str | None = None,
    **_ignored,
) -> dict[str, str]:
    """Map Google News item URLs to publisher URLs.

    Returns a dict keyed by the *original* input URLs.
    """
    unique: list[str] = []
    seen: set[str] = set()
    originals: dict[str, list[str]] = {}
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        localized = with_locale(url, hl, gl, ceid)
        unique.append(localized)
        originals.setdefault(localized, []).append(url)
    if not unique:
        return {}

    interval = 0.0 if interval is None else float(interval)
    chunk_size = max(1, int(chunk_size))
    mapping: dict[str, str] = {}

    with GoogleDecoder(timeout=timeout, user_agent=user_agent or DEFAULT_UA) as decoder:
        for i in range(0, len(unique), chunk_size):
            chunk = unique[i : i + chunk_size]
            log.info("decoding %d Google News URLs (%d–%d of %d)", len(chunk), i + 1, i + len(chunk), len(unique))
            succeeded, failed = _decode_chunk(decoder, chunk, interval or None)

            # Retry leftovers with backoff, shrinking the batch if Google rejects it.
            retries = failed
            delay = 8.0
            sub_size = max(1, len(chunk) // 2)
            attempt = 0
            while retries and attempt < 4:
                rate_limited = any(_is_rate_limit(msg) for _, msg in retries)
                bulky = any(_is_method_or_server(msg) for _, msg in retries)
                if not (rate_limited or bulky):
                    break
                wait = delay if rate_limited else min(delay, 4.0)
                log.warning(
                    "decode retry %d for %d URLs after %.0fs (rate_limit=%s, 405/5xx=%s)",
                    attempt + 1,
                    len(retries),
                    wait,
                    rate_limited,
                    bulky,
                )
                time.sleep(wait)
                leftover = [u for u, _ in retries]
                retries = []
                # On 405, try one-at-a-time; on 429, use smaller groups.
                step = 1 if bulky and not rate_limited else max(1, sub_size)
                for j in range(0, len(leftover), step):
                    piece = leftover[j : j + step]
                    more_ok, more_fail = _decode_chunk(decoder, piece, interval or None)
                    succeeded.update(more_ok)
                    retries.extend(more_fail)
                    if more_fail and j + step < len(leftover):
                        time.sleep(2.0)
                delay = min(delay * 2, 45.0)
                sub_size = 1
                attempt += 1

            for url, msg in retries:
                log.warning("decode failed for %s: %s", url[:90], msg)

            for loc_url, decoded in succeeded.items():
                for original in originals.get(loc_url, [loc_url]):
                    mapping[original] = decoded

            if i + chunk_size < len(unique) and pause_between_chunks:
                time.sleep(float(pause_between_chunks))

    log.info("decoded %d/%d Google News URLs", len(mapping), len(unique))
    return mapping
