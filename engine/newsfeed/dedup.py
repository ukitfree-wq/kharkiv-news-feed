"""URL canonicalization, title near-duplicates, and content similarity."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rapidfuzz import fuzz

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "utm_id",
    "utm_reader",
    "fbclid",
    "gclid",
    "gclsrc",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "igshid",
    "ocid",
    "ns_campaign",
    "ns_mchannel",
    "ns_source",
    "ns_linkname",
    "ns_fee",
    "_hsenc",
    "_hsmi",
    "ref",
    "ref_src",
}

MINHASH_PERM = 64
SHINGLE_N = 3


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    url = url.strip()
    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path.endswith("/") and path != "/":
        path = path.rstrip("/")
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in TRACKING_PARAMS and not k.lower().startswith("utm_")
    ]
    query = urlencode(sorted(query_pairs), doseq=True)
    return urlunparse((scheme, netloc, path, "", query, ""))


def is_google_news_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "news.google.com" or host.endswith(".news.google.com")


def normalize_title(title: str) -> str:
    """Lowercase, strip the trailing ' - Publisher' suffix, drop punctuation."""
    if not title:
        return ""
    text = unicodedata.normalize("NFKC", title).strip()
    if " - " in text:
        head, _, tail = text.rpartition(" - ")
        if head and 0 < len(tail) <= 60:
            text = head
    text = text.casefold()
    text = re.sub(r"[\"“”„«»‘’']", "", text)
    text = re.sub(r"[^\w\s]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def title_hash(title_norm: str) -> str:
    return hashlib.sha256((title_norm or "").encode("utf-8")).hexdigest()


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256((canonical_url or "").encode("utf-8")).hexdigest()


def titles_similar(a: str, b: str, threshold: int = 87) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    if min(len(a), len(b)) < 24:
        return a == b
    return fuzz.token_set_ratio(a, b) >= threshold


def find_title_duplicate(
    title_norm: str,
    existing: list[tuple[str, str]],
    threshold: int = 87,
) -> str | None:
    """Return the matching existing title_hash, if any."""
    if not title_norm:
        return None
    target_hash = title_hash(title_norm)
    for other_norm, other_hash in existing:
        if other_hash == target_hash or titles_similar(title_norm, other_norm, threshold):
            return other_hash
    return None


def _tokens(text: str) -> list[str]:
    norm = unicodedata.normalize("NFKC", text or "").casefold()
    norm = re.sub(r"[^\w\s]+", " ", norm, flags=re.UNICODE)
    return [t for t in norm.split() if t]


def word_shingles(text: str, n: int = SHINGLE_N) -> list[str]:
    tokens = _tokens(text)
    if not tokens:
        return []
    if len(tokens) < n:
        return [" ".join(tokens)]
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def content_minhash(text: str, num_perm: int = MINHASH_PERM) -> list[int]:
    """64-wide MinHash over word 3-grams (stable, no extra dependencies)."""
    shingles = word_shingles(text)
    if not shingles:
        return [0] * num_perm
    hashes = [2**64 - 1] * num_perm
    for shingle in shingles:
        raw = shingle.encode("utf-8")
        for i in range(num_perm):
            digest = hashlib.blake2b(raw, digest_size=8, person=i.to_bytes(2, "little")).digest()
            value = int.from_bytes(digest, "big")
            if value < hashes[i]:
                hashes[i] = value
    return hashes


def minhash_to_str(values: list[int] | None) -> str:
    if not values:
        return ""
    return ",".join(f"{v:016x}" for v in values)


def minhash_from_str(payload: str | None) -> list[int]:
    if not payload:
        return []
    try:
        return [int(part, 16) for part in payload.split(",") if part]
    except ValueError:
        return []


def minhash_similarity(a: list[int] | None, b: list[int] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x == y for x, y in zip(a, b)) / len(a)


def contents_similar(a: list[int] | None, b: list[int] | None, threshold: float = 0.58) -> bool:
    if not a or not b:
        return False
    # Empty fingerprints are all-zero; do not treat them as duplicates.
    if all(x == 0 for x in a) or all(x == 0 for x in b):
        return False
    return minhash_similarity(a, b) >= threshold


def completeness(item: dict) -> tuple[int, int]:
    """Higher is more complete: status rank, then a size score.

    Size combines text length, images, and embeds so a photo report or a
    short text with video can beat a slightly longer text-only duplicate.
    """
    status = item.get("extraction_status") or ""
    rank = {"ok": 2, "partial": 1}.get(status, 0)
    text_len = len(item.get("content_text") or "")
    images = item.get("images") or []
    embeds = item.get("embeds") or []
    score = text_len + 200 * len(images) + 300 * len(embeds)
    return (rank, score)


def earlier_timestamp(a: str | None, b: str | None) -> bool:
    """True if a is earlier than b (empty dates lose)."""
    if not a:
        return False
    if not b:
        return True
    return a < b
