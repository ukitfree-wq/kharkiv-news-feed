"""Scrape publisher pages for full text, all article images, and social embeds."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
import trafilatura
from bs4 import BeautifulSoup, NavigableString, Tag
from trafilatura.settings import use_config

from newsfeed.sites import profile_for

log = logging.getLogger(__name__)

_SKIP_IMG_RE = re.compile(
    r"(sprite|pixel|spacer|blank|icon|logo|avatar|emoji|badge|button|1x1|"
    r"tracking|doubleclick|advert|adservice|gravatar|share-|facebook\.com/tr|"
    r"google-preferred|google-news|gnews|openweathermap|brand-icon|"
    r"custom-logo|/fb32\.|/tg32\.|/vb32\.|/x32\.|/wa32\.|my_kiev_ico)",
    re.I,
)

# Tags we keep when serializing the article body.
_ALLOWED = {
    "p", "h1", "h2", "h3", "h4", "h5", "ul", "ol", "li", "br", "em", "strong",
    "b", "i", "u", "a", "img", "figure", "figcaption", "blockquote", "iframe",
    "video", "source", "picture", "table", "thead", "tbody", "tr", "td", "th",
    "hr", "span", "div", "script", "section",
}

_EMBED_SCRIPT_HOSTS = (
    "platform.twitter.com",
    "www.instagram.com",
    "instagram.com",
    "www.tiktok.com",
    "tiktok.com",
    "telegram.org",
    "www.telegram.org",
    "connect.facebook.net",
)

_EMBED_IFRAME_HOSTS = (
    "youtube.com",
    "www.youtube.com",
    "youtube-nocookie.com",
    "www.youtube-nocookie.com",
    "youtu.be",
    "player.vimeo.com",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "tiktok.com",
    "www.tiktok.com",
    "t.me",
    "telegram.org",
    "www.telegram.org",
    "platform.twitter.com",
    "twitter.com",
    "www.twitter.com",
    "x.com",
    "www.x.com",
    "syndication.twitter.com",
)

_EMBED_KIND_RE = [
    ("youtube", re.compile(r"youtube\.com|youtu\.be|youtube-nocookie", re.I)),
    ("telegram", re.compile(r"t\.me/|telegram\.org|telegram-post|telegram-widget", re.I)),
    ("facebook", re.compile(r"facebook\.com|fb\.com|fb-post|fb-video|fbcdn", re.I)),
    ("instagram", re.compile(r"instagram\.com|instagram-media|instgrm", re.I)),
    ("twitter", re.compile(r"twitter\.com|x\.com|twitter-tweet|platform\.twitter", re.I)),
    ("tiktok", re.compile(r"tiktok\.com|tiktok-embed", re.I)),
]


def _session(user_agent: str, accept_language: str) -> requests.Session:
    sess = requests.Session()
    sess.headers.update(
        {
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": accept_language,
            "Accept-Encoding": "gzip, deflate",
            "Cache-Control": "no-cache",
        }
    )
    return sess


def guess_image_type(url: str) -> str:
    path = urlparse(url).path
    typ, _ = mimetypes.guess_type(path)
    if typ and typ.startswith("image/"):
        return typ
    lower = path.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/jpeg"



def _walk_ld_json(obj, found: list[str]) -> None:
    if isinstance(obj, dict):
        for key in ("datePublished", "dateCreated", "uploadDate"):
            val = obj.get(key)
            if val:
                found.append(str(val))
        for val in obj.values():
            if isinstance(val, (dict, list)):
                _walk_ld_json(val, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_ld_json(item, found)


def extract_page_published(soup: BeautifulSoup) -> str | None:
    """Best published timestamp on the page (page time, not the RSS fallback)."""
    candidates: list[str] = []
    for key in (
        "article:published_time",
        "og:published_time",
        "article:published",
        "publish-date",
        "pubdate",
        "datePublished",
        "parsely-pub-date",
        "sailthru.date",
    ):
        val = _meta(soup, key)
        if val:
            candidates.append(val)
    for script in soup.find_all("script"):
        typ = script.get("type") or ""
        if isinstance(typ, list):
            typ = " ".join(typ)
        if "ld+json" not in typ.lower():
            continue
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            # Sometimes two JSON objects are concatenated
            try:
                data = json.loads(raw.split("\n")[0])
            except Exception:
                continue
        _walk_ld_json(data, candidates)
    for tag in soup.find_all("time"):
        val = tag.get("datetime") or tag.get("content") or ""
        if val:
            candidates.append(val.strip())
    # Prefer a value that includes a clock time
    from newsfeed.dates import parse_datetime, is_date_only
    timed = [c for c in candidates if c and not is_date_only(c) and parse_datetime(c)]
    if timed:
        return timed[0]
    for c in candidates:
        if parse_datetime(c):
            return c
    return None

def _meta(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _abs(url: str, base: str) -> str:
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    return urljoin(base, url)


def _is_placeholder(url: str) -> bool:
    if not url or url.startswith("data:"):
        return True
    return "image/svg+xml" in url or url.startswith("about:")


_DATA_WIDTH_SRC = re.compile(r"^data-(\d+)-src$", re.I)


def _attr_str(val) -> str:
    if isinstance(val, list):
        return str(val[0]) if val else ""
    return str(val) if val else ""


def image_url(img: Tag, base: str) -> str:
    """Resolve a real image URL, including lazy-loaded data-src (2day.kh.ua)."""
    # Lazy-load attributes are the real URL when src is a placeholder SVG.
    for attr in (
        "data-src",
        "data-lazy-src",
        "data-original",
        "data-full-url",
        "data-lazy",
        "data-bg",
    ):
        val = _attr_str(img.get(attr))
        if val and not _is_placeholder(val):
            return _abs(val, base)

    # Prefer the widest explicit variant (Suspilne data-1040-src / srcset).
    candidates: list[tuple[int, str]] = []
    best_w = -1
    best_src = ""
    for attr, val in img.attrs.items():
        m = _DATA_WIDTH_SRC.match(str(attr))
        if not m:
            continue
        src = _attr_str(val)
        if not src or _is_placeholder(src):
            continue
        width = int(m.group(1))
        if width > best_w:
            best_w = width
            best_src = src
    if best_src:
        candidates.append((best_w, best_src))
    for attr in ("data-srcset", "srcset"):
        val = img.get(attr)
        if not val:
            continue
        for part in str(val).split(","):
            bits = part.strip().split()
            if not bits or _is_placeholder(bits[0]):
                continue
            width = 0
            if len(bits) >= 2 and bits[1].endswith("w") and bits[1][:-1].isdigit():
                width = int(bits[1][:-1])
            candidates.append((width, bits[0]))
    src = _attr_str(img.get("src"))
    if src and not _is_placeholder(src):
        candidates.append((0, src))
    if not candidates:
        return ""
    url = max(candidates, key=lambda item: item[0])[1]
    return _abs(url, base)


def _good_image(url: str) -> bool:
    if not url or _is_placeholder(url):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if _SKIP_IMG_RE.search(url):
        return False
    return True


def _blob(tag: Tag) -> str:
    parts = [tag.name or ""]
    for attr in (
        "src", "href", "class", "cite", "data-src", "id",
        "data-telegram-post", "data-instgrm-permalink", "data-video-id",
    ):
        val = tag.get(attr)
        if val:
            parts.append(" ".join(val) if isinstance(val, list) else str(val))
    return " ".join(parts).lower()


def embed_kind(tag: Tag) -> str | None:
    blob = _blob(tag)
    for kind, pattern in _EMBED_KIND_RE:
        if pattern.search(blob):
            return kind
    return None


def is_embed_widget(tag: Tag) -> bool:
    if not isinstance(tag, Tag):
        return False
    name = tag.name or ""
    blob = _blob(tag)
    kind = embed_kind(tag)
    if name == "iframe" and kind:
        return True
    if name == "blockquote" and kind:
        return True
    if name == "script":
        src = (tag.get("src") or "").lower()
        host = (urlparse(tag.get("src") or "").hostname or "").lower()
        if any(host == h or host.endswith("." + h) for h in _EMBED_SCRIPT_HOSTS):
            return True
        if "telegram-widget" in src or "instagram.com/embed" in src or "tiktok.com/embed" in src:
            return True
        if tag.get("data-telegram-post") or tag.get("data-instgrm-permalink"):
            return True
        return False
    if name in {"div", "figure", "section"}:
        if any(
            token in blob
            for token in (
                "wp-block-embed",
                "fb-post",
                "fb-video",
                "instagram-media",
                "twitter-tweet",
                "tiktok-embed",
                "telegram-widget",
                "telegram-post",
            )
        ):
            return True
    return False


def _iframe_allowed(src: str) -> bool:
    host = (urlparse(src).hostname or "").lower()
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in _EMBED_IFRAME_HOSTS)


def classify_embeds(root: Tag) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for tag in root.find_all(["iframe", "blockquote", "script"]):
        if not is_embed_widget(tag):
            continue
        kind = embed_kind(tag) or "embed"
        key = (
            tag.get("src")
            or tag.get("cite")
            or tag.get("data-telegram-post")
            or tag.get("data-instgrm-permalink")
            or str(tag.get("class"))
        )
        ident = (kind, str(key)[:180])
        if ident in seen:
            continue
        seen.add(ident)
        found.append({"kind": kind, "tag": tag.name, "ref": ident[1]})
    # Standalone widget containers that have no iframe/blockquote child (e.g. fb-post)
    for tag in root.find_all(["div", "figure"]):
        if not is_embed_widget(tag):
            continue
        if tag.find(["iframe", "blockquote", "script"]):
            continue
        kind = embed_kind(tag) or "embed"
        key = str(tag.get("class") or tag.get("id") or tag.name)
        ident = (kind, key[:180])
        if ident in seen:
            continue
        seen.add(ident)
        found.append({"kind": kind, "tag": tag.name, "ref": ident[1]})
    return found


def _pick_body(soup: BeautifulSoup, profile: dict) -> Tag | None:
    best: Tag | None = None
    best_score = 0
    for sel in profile.get("selectors") or []:
        for node in soup.select(sel):
            text_len = len(node.get_text(" ", strip=True))
            score = text_len + 80 * len(node.find_all("img")) + 120 * len(node.find_all("iframe"))
            if score > best_score and text_len >= 80:
                best, best_score = node, score
        if best is not None:
            # first selector family that matches wins if it's reasonably long
            if best_score >= 200:
                return best
    return best


def _strip_junk(root: Tag, selectors: list[str]) -> None:
    for sel in selectors:
        for node in root.select(sel):
            node.decompose()


# Subscribe / share / "read also" lines that leak into the article body.
_BOILER_START_RE = re.compile(
    r"^\s*("
    r"підпишіться(\s+на)?|"
    r"підписуйтесь(\s+на)?|"
    r"підписатися(\s+на)?|"
    r"читайте також|"
    r"читати також|"
    r"див(іться|ись) також|"
    r"вам може бути цікаво|"
    r"більше новин|"
    r"рекомендуємо|"
    r"поділитися|"
    r"поделиться|"
    r"share this|"
    r"читать на русском|"
    r"total\s+\d+\s+shares"
    r")\b",
    re.I,
)


def _strip_boilerplate(root: Tag) -> None:
    for tag in list(root.find_all(["p", "h2", "h3", "h4", "div", "span", "a", "section", "aside"])):
        if not getattr(tag, "name", None) or tag.decompose is None:
            continue
        # Skip if already removed with a parent.
        if tag.parent is None:
            continue
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        if _BOILER_START_RE.match(text):
            tag.decompose()


_TEXT_BLOCKS = ["p", "div", "h2", "h3", "h4", "h5", "h6", "blockquote", "section", "aside", "ul", "strong", "b", "a", "span", "figure"]


def _drop_text_blocks(root: Tag, pattern: str | None) -> None:
    """Profile option drop_text_re: remove the outermost blocks whose text matches."""
    if not pattern:
        return
    rx = re.compile(pattern, re.I)
    for tag in list(root.find_all(_TEXT_BLOCKS)):
        if tag.parent is None:
            continue
        text = tag.get_text(" ", strip=True)
        if text and rx.match(text):
            tag.decompose()


def _truncate_at(root: Tag, pattern: str | None) -> None:
    """Profile option truncate_at_re: drop the first block whose text matches and
    everything after it inside the article body ("Читайте також" lists, tags, …)."""
    if not pattern:
        return
    rx = re.compile(pattern, re.I)
    marker = None
    for tag in root.find_all(_TEXT_BLOCKS):
        text = tag.get_text(" ", strip=True)
        if text and rx.match(text):
            marker = tag
            break
    if marker is None:
        return
    node = marker
    while node is not None and node is not root:
        for sib in list(node.next_siblings):
            if isinstance(sib, Tag):
                sib.decompose()
            else:
                sib.extract()
        node = node.parent
    marker.decompose()


def _profile_image_ok(url: str, profile: dict) -> bool:
    pattern = profile.get("skip_image_re")
    return not (pattern and re.search(pattern, url or "", re.I))


def _good_for(url: str, profile: dict) -> bool:
    """_good_image, except that a profile's allow_image_re can whitelist a CDN path
    the generic junk filter would reject (048.ua posters live under /newsIcon/)."""
    allow = profile.get("allow_image_re")
    if allow and url and re.search(allow, url, re.I) and urlparse(url).scheme in ("http", "https"):
        return _profile_image_ok(url, profile)
    return _good_image(url) and _profile_image_ok(url, profile)


def _cis_api_body(url: str, session: requests.Session, timeout: float, profile: dict) -> tuple[str, str]:
    """Article HTML + poster URL from the CIS city-site API (048.ua, 057.ua, …).

    Their pages are Angular SSR: in-text photos are placeholders (cap.jpg) filled
    in by JS from /api3/news/<id>?expand=parsedContent. Returns ("", "") on failure.
    """
    m = re.search(r"/news/(\d+)", urlparse(url).path)
    if not m:
        return "", ""
    host = urlparse(url).scheme + "://" + urlparse(url).netloc
    try:
        resp = session.get(
            f"{host}/api3/news/{m.group(1)}",
            params={"expand": "parsedContent,internalPoster"},
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return "", ""
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        log.warning("cis api failed %s: %s", url, exc)
        return "", ""

    def image_html(block: dict) -> str:
        src = str(block.get("sourceUrl") or "").strip()
        if not src:
            return ""
        caption = " ".join(
            str(x).strip() for x in (block.get("description"), block.get("author")) if x and str(x).strip()
        )
        out = f'<figure><img src="{escape(src, quote=True)}" alt="{escape(str(block.get("description") or ""), quote=True)}"/>'
        if caption:
            out += f"<figcaption>{escape(caption)}</figcaption>"
        return out + "</figure>"

    parts: list[str] = []
    for block in data.get("parsedContent") or []:
        kind = block.get("type") or ""
        if kind == "imageBlock":
            parts.append(image_html(block))
        elif kind == "galleryBlock":
            parts.extend(image_html(b) for b in block.get("images") or [])
        elif kind == "quoteBlock":
            parts.append(f"<blockquote>{block.get('content') or ''}</blockquote>")
        elif block.get("content"):
            parts.append(str(block["content"]))
    poster = ((data.get("internalPoster") or {}).get("url") or "").strip()
    return "\n".join(p for p in parts if p), poster


def _lead_images(soup: BeautifulSoup, profile: dict, page_url: str) -> list[str]:
    urls: list[str] = []
    for sel in profile.get("lead_image_selectors") or []:
        for img in soup.select(sel):
            src = image_url(img, page_url)
            if _good_image(src) and src not in urls:
                urls.append(src)
    return urls


def _collect_images(root: Tag, page_url: str) -> list[str]:
    urls: list[str] = []
    for img in root.find_all("img"):
        src = image_url(img, page_url)
        if not _good_image(src):
            continue
        try:
            w = int(img.get("width") or 0)
            h = int(img.get("height") or 0)
            if w and h and w < 80 and h < 80:
                continue
        except ValueError:
            pass
        if src not in urls:
            urls.append(src)
    return urls


def _rewrite_images(root: Tag, page_url: str) -> None:
    for img in list(root.find_all("img")):
        src = image_url(img, page_url)
        if not _good_image(src):
            img.decompose()
            continue
        alt = img.get("alt") or ""
        img.attrs = {"src": src, "alt": alt}


def _clean_iframe(tag: Tag, page_url: str) -> bool:
    src = _abs(tag.get("src") or tag.get("data-src") or "", page_url)
    if not src or not _iframe_allowed(src):
        return False
    attrs = {"src": src}
    for key in ("width", "height", "allow", "allowfullscreen", "title", "frameborder"):
        if tag.get(key) is not None:
            attrs[key] = tag.get(key)
    if "allowfullscreen" in tag.attrs and "allowfullscreen" not in attrs:
        attrs["allowfullscreen"] = "true"
    tag.attrs = attrs
    return True


def serialize_body(root: Tag, page_url: str) -> str:
    """Return clean HTML for content:encoded, keeping images and social embeds."""
    working = BeautifulSoup(str(root), "lxml")
    body = working.body or working

    for junk in body.find_all(["style", "noscript", "form", "button", "svg", "link"]):
        junk.decompose()

    def walk(node: Tag) -> None:
        for child in list(node.children):
            if isinstance(child, NavigableString):
                continue
            if not isinstance(child, Tag):
                continue
            name = (child.name or "").lower()
            if is_embed_widget(child):
                if name == "iframe" and not _clean_iframe(child, page_url):
                    child.decompose()
                    continue
                if name == "script":
                    src = child.get("src")
                    if src:
                        child.attrs = {
                            k: child.attrs[k]
                            for k in list(child.attrs)
                            if k in {"src", "async", "defer", "charset"}
                            or k.startswith("data-")
                        }
                        child["src"] = _abs(src, page_url)
                # keep widget subtree as-is (minus junk scripts of unknown origin)
                continue
            if name == "iframe":
                if not _clean_iframe(child, page_url):
                    child.decompose()
                continue
            if name == "img":
                src = image_url(child, page_url)
                if not _good_image(src):
                    child.decompose()
                    continue
                child.attrs = {"src": src, "alt": child.get("alt") or ""}
                continue
            if name == "a":
                href = _abs(child.get("href") or "", page_url)
                child.attrs = {"href": href} if href else {}
                walk(child)
                continue
            if name == "script":
                child.decompose()
                continue
            if name not in _ALLOWED:
                child.unwrap()
                walk(node)
                return
            # Drop noise attributes
            kept = {}
            for k, v in list(child.attrs.items()):
                if k in {"href", "src", "alt", "cite", "colspan", "rowspan"} or k.startswith("data-"):
                    kept[k] = v
            child.attrs = kept
            walk(child)

    walk(body)
    # Avoid wrapping <html><body>
    inner = body
    # If we parsed a fragment, body contains the node
    return "".join(str(c) for c in inner.children).strip()


def _text_of(html: str) -> str:
    if not html:
        return ""
    return BeautifulSoup(html, "lxml").get_text("\n", strip=True)


def _ensure_media(html: str, images: list[str], lead: str, embeds_html: str = "") -> str:
    existing = set(re.findall(r'src="([^"]+)"', html or ""))
    parts: list[str] = []
    if lead and lead not in existing:
        parts.append(f'<p><img src="{escape(lead, quote=True)}" alt=""/></p>')
        existing.add(lead)
    if html:
        parts.append(html)
    for url in images:
        if url not in existing:
            parts.append(f'<p><img src="{escape(url, quote=True)}" alt=""/></p>')
            existing.add(url)
    if embeds_html and embeds_html not in (html or ""):
        # Only append embeds not already serialized
        parts.append(embeds_html)
    return "\n".join(p for p in parts if p)


def extract_article(
    url: str,
    session: requests.Session,
    timeout: float,
    min_text_chars: int,
    fallback_title: str = "",
    fallback_publisher: str = "",
    fallback_date: str | None = None,
    fallback_summary: str = "",
) -> dict[str, Any]:
    """Download one publisher page and extract a clean article. Never raises."""
    result: dict[str, Any] = {
        "canonical_url": url,
        "title": fallback_title,
        "publisher": fallback_publisher,
        "published_at": fallback_date,
        "page_published_at": None,
        "lead_image": "",
        "images": [],
        "embeds": [],
        "content_html": "",
        "content_text": "",
        "summary": fallback_summary,
        "extraction_status": "failed",
        "skip_reason": "",
    }
    try:
        resp = session.get(url, timeout=timeout, allow_redirects=True)
    except requests.Timeout:
        result["skip_reason"] = "timeout"
        return result
    except requests.RequestException as exc:
        result["skip_reason"] = f"request_error:{exc.__class__.__name__}"
        log.warning("fetch failed %s: %s", url, exc)
        return result

    if resp.status_code in (401, 402, 403):
        result["skip_reason"] = f"blocked:{resp.status_code}"
        return result
    if resp.status_code == 404:
        result["skip_reason"] = "not_found"
        return result
    if resp.status_code >= 400:
        result["skip_reason"] = f"http:{resp.status_code}"
        return result

    resp.encoding = resp.apparent_encoding or resp.encoding or "utf-8"
    html = resp.text
    final_url = resp.url or url
    result["canonical_url"] = final_url

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as exc:
        result["skip_reason"] = f"parse_error:{exc}"
        return result

    profile = profile_for(final_url)
    page_title = _meta(soup, "og:title", "twitter:title") or (
        soup.title.string.strip() if soup.title and soup.title.string else ""
    )
    # Prefer the on-page headline if present
    h1 = soup.select_one(profile["title_selector"]) if profile.get("title_selector") else None
    if h1 is None:
        h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        result["title"] = h1.get_text(" ", strip=True)
    elif page_title:
        result["title"] = page_title
    site = _meta(soup, "og:site_name") or profile.get("name") or result["publisher"]
    if site:
        result["publisher"] = site
    # Some sites print a wrong machine-readable date (chis-chernomorsk.com.ua);
    # for those the RSS pubDate is the publish time.
    page_published = None if profile.get("ignore_page_date") else extract_page_published(soup)
    result["page_published_at"] = page_published
    if page_published:
        # Page time is authoritative when present; RSS time stays on the item as feed_published_at.
        result["published_at"] = page_published

    body = None
    api_poster = ""
    if profile.get("api") == "cis":
        api_html, api_poster = _cis_api_body(final_url, session, timeout, profile)
        if api_html:
            body = BeautifulSoup(f"<div>{api_html}</div>", "lxml").div
    if body is None:
        body = _pick_body(soup, profile)
    if body is None:
        body = soup.find("article") or soup.find("main") or soup.body

    if body is not None:
        if profile.get("strip_comments"):
            from bs4 import Comment

            for c in list(body.find_all(string=lambda t: isinstance(t, Comment))):
                c.extract()
        _strip_junk(body, profile.get("remove") or [])
        if profile.get("link_image_selector"):
            # Thumbnails that link to the full-size photo (grad.ua highslide): use the link.
            for a in body.select(profile["link_image_selector"]):
                href = _abs(_attr_str(a.get("href")), final_url)
                img = a.find("img")
                if img is not None and re.search(r"\.(?:jpe?g|png|webp|gif)(?:[?#].*)?$", href, re.I):
                    img.attrs = {"src": href, "alt": img.get("alt") or ""}
        _truncate_at(body, profile.get("truncate_at_re"))
        _drop_text_blocks(body, profile.get("drop_text_re"))
        _strip_boilerplate(body)
        _rewrite_images(body, final_url)
        if profile.get("skip_image_re"):
            for img in list(body.find_all("img")):
                if not _profile_image_ok(img.get("src") or "", profile):
                    img.decompose()

    lead_from_profile = _lead_images(soup, profile, final_url)
    if api_poster and api_poster not in lead_from_profile:
        lead_from_profile.insert(0, api_poster)
    if profile.get("skip_image_re"):
        lead_from_profile = [u for u in lead_from_profile if _profile_image_ok(u, profile)]
    og_image = _meta(soup, "og:image", "og:image:url", "twitter:image", "twitter:image:src")
    og_image = _abs(og_image, final_url) if og_image else ""
    if og_image and not _profile_image_ok(og_image, profile):
        og_image = ""

    images: list[str] = []
    for src in lead_from_profile:
        if (_good_for(src, profile) if profile.get("allow_image_re") else _good_image(src)) and src not in images:
            images.append(src)
    if body is not None:
        for src in _collect_images(body, final_url):
            if src not in images:
                images.append(src)
    og_ok = _good_for(og_image, profile) if profile.get("allow_image_re") else _good_image(og_image)
    if og_ok and og_image not in images:
        # og:image often a crop of the lead; put it first only if we have no lead yet
        if not lead_from_profile:
            images.insert(0, og_image)
        else:
            images.append(og_image)

    embeds = classify_embeds(body) if body is not None else []
    # Also look in the selected body only (not footer share icons)
    result["embeds"] = embeds
    result["images"] = images
    result["lead_image"] = images[0] if images else (og_image if og_ok else "")

    content_html = serialize_body(body, final_url) if body is not None else ""
    content_text = _text_of(content_html)

    # If the site body is too thin, fill text from trafilatura but keep our images/embeds.
    if len(content_text) < min_text_chars:
        try:
            cfg = use_config()
            cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "0")
            t_text = trafilatura.extract(
                html, url=final_url, include_comments=False, include_tables=False,
                favor_recall=True, config=cfg,
            ) or ""
            t_html = trafilatura.extract(
                html, url=final_url, include_comments=False, include_tables=True,
                include_images=True, output_format="html", favor_recall=True, config=cfg,
            ) or ""
        except Exception as exc:
            log.warning("trafilatura failed for %s: %s", final_url, exc)
            t_text, t_html = "", ""
        if len(t_text) > len(content_text):
            content_text = t_text
            if t_html:
                # merge: trafilatura html + our images/embeds (trafilatura drops iframes)
                content_html = _ensure_media(t_html, images, result["lead_image"])
                # append embed widgets from the original body
                if body is not None:
                    extras = []
                    for tag in body.find_all(["iframe", "blockquote", "figure", "script"]):
                        if is_embed_widget(tag):
                            extras.append(str(tag))
                    if extras:
                        content_html += "\n" + "\n".join(extras)

    content_html = _ensure_media(content_html, images, result["lead_image"])
    result["content_html"] = content_html
    result["content_text"] = content_text or _text_of(content_html)

    text_len = len(result["content_text"])
    if text_len >= min_text_chars or (images and text_len >= 80):
        # Photo reports may have little text but many images — still useful.
        result["extraction_status"] = "ok" if text_len >= min_text_chars or len(images) >= 3 else "partial"
        result["skip_reason"] = "" if result["extraction_status"] == "ok" else f"short_text:{text_len}"
    elif result["content_text"]:
        result["extraction_status"] = "partial"
        result["skip_reason"] = f"short_text:{text_len}"
    else:
        result["extraction_status"] = "failed"
        result["skip_reason"] = result["skip_reason"] or "no_text"
    return result


def extract_many(
    items: list[dict[str, Any]],
    user_agent: str,
    accept_language: str,
    timeout: float,
    min_text_chars: int,
    concurrency: int,
) -> list[dict[str, Any]]:
    session = _session(user_agent, accept_language)
    results: list[dict[str, Any] | None] = [None] * len(items)

    def work(idx: int, item: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        extracted = extract_article(
            item["canonical_url"],
            session=session,
            timeout=timeout,
            min_text_chars=min_text_chars,
            fallback_title=item.get("title") or "",
            fallback_publisher=item.get("publisher") or "",
            fallback_date=item.get("published_at"),
            fallback_summary=item.get("summary") or "",
        )
        return idx, extracted

    workers = max(1, min(int(concurrency), len(items) or 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(work, i, item) for i, item in enumerate(items)]
        for fut in as_completed(futs):
            try:
                idx, extracted = fut.result()
                results[idx] = extracted
            except Exception as exc:
                log.warning("worker crashed: %s", exc)

    out: list[dict[str, Any]] = []
    for item, extracted in zip(items, results):
        if extracted is None:
            extracted = {
                "canonical_url": item.get("canonical_url") or "",
                "title": item.get("title") or "",
                "publisher": item.get("publisher") or "",
                "published_at": item.get("published_at"),
                "page_published_at": None,
                "lead_image": "",
                "images": [],
                "embeds": [],
                "content_html": "",
                "content_text": "",
                "summary": item.get("summary") or "",
                "extraction_status": "failed",
                "skip_reason": "worker_error",
            }
        merged = dict(item)
        merged.update(extracted)
        out.append(merged)
    return out
