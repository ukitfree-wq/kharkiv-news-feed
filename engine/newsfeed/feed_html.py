"""Normalize article HTML so RSS readers and WP Automatic can use it as-is."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, NavigableString, Tag

_WEBP_EXPRESS_RE = re.compile(
    r"^(https?://[^/]+)/wp-content/webp-express/webp-images/uploads/(.+)\.webp(?:[?#].*)?$",
    re.I,
)
_SIZE_SUFFIX_RE = re.compile(r"-(\d+)x(\d+)(?=\.(?:jpe?g|png|gif|webp)$)", re.I)
_YT_ID_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:embed/|shorts/|live/|watch\?.*?v=)|youtu\.be/)([A-Za-z0-9_-]{11})",
    re.I,
)
_LAZY_ATTRS = (
    "data-src",
    "data-lazy-src",
    "data-original",
    "data-full-url",
    "data-lazy",
    "data-bg",
    "data-large_image",
    "data-orig-file",
)
_PLACEHOLDER_PREFIXES = ("data:", "about:", "blob:")
_BLOCK = {
    "p", "div", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "figure", "figcaption", "blockquote", "table", "thead", "tbody", "tr",
    "td", "th", "pre", "hr", "section", "article", "header", "footer",
}
_VOID = {"img", "br", "hr", "source", "col", "input", "meta", "link"}


def _abs(url: str, base: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if base:
        url = urljoin(base, url)
    return url


def _is_placeholder(url: str) -> bool:
    if not url:
        return True
    low = url.strip().lower()
    if low.startswith(_PLACEHOLDER_PREFIXES) or "image/svg+xml" in low:
        return True
    return False


def prefer_raster_url(url: str) -> str:
    """Rewrite known WebP-Express URLs to the site's JPEG/PNG original."""
    m = _WEBP_EXPRESS_RE.match(url or "")
    if m:
        return f"{m.group(1)}/wp-content/uploads/{m.group(2)}"
    return url or ""


def is_social_card_image(url: str) -> bool:
    """True for social-share crops such as Suspilne's automatic-og images.

    Also matches GitHub Pages copies of those cards that were mirrored before
    we stopped appending automatic-og (filename = sha1(host + stem)[:20]).
    """
    path = (urlparse(url or "").path or "").lower()
    if "/images/generated/automatic-og/" in path or "/automatic-og/" in path:
        return True
    name = path.rsplit("/", 1)[-1]
    return name in _MIRRORED_SOCIAL_CARD_FILES


# Leftover automatic-og files copied to GitHub Pages on 2026-11-28 before
# social cards were stripped from the body. Safe to drop when a lead exists.
_MIRRORED_SOCIAL_CARD_FILES = {
    "16f4f187930859366531.jpg",
}


def force_https(url: str) -> str:
    if url.lower().startswith("http://"):
        parsed = urlparse(url)
        # Drop obviously broken/non-host URLs (mojibake, spaces, punycode junk).
        host = parsed.netloc or ""
        if " " in url or "%" in host or len(host) > 80 or not host or "." not in host:
            return ""
        return urlunparse(parsed._replace(scheme="https"))
    return url


def _from_srcset(value: str, base: str) -> str:
    best = ""
    best_w = -1
    for part in (value or "").split(","):
        bits = part.strip().split()
        if not bits:
            continue
        candidate = bits[0]
        width = 0
        if len(bits) > 1 and bits[1].endswith("w"):
            try:
                width = int(bits[1][:-1])
            except ValueError:
                width = 0
        if width >= best_w and not _is_placeholder(candidate):
            best_w = width
            best = candidate
    return _abs(best, base) if best else ""


def resolve_img_src(img: Tag, base: str) -> str:
    for attr in _LAZY_ATTRS + ("src",):
        val = img.get(attr)
        if isinstance(val, list):
            val = val[0] if val else ""
        val = (val or "").strip()
        if val and not _is_placeholder(val):
            return prefer_raster_url(force_https(_abs(val, base)))
    for attr in ("data-srcset", "srcset"):
        found = _from_srcset(img.get(attr) or "", base)
        if found:
            return prefer_raster_url(force_https(found))
    return ""


def image_stem(url: str) -> str:
    """Identity used to drop the same photo at multiple WordPress sizes."""
    parsed = urlparse(url or "")
    path = parsed.path
    path = path.replace("/wp-content/webp-express/webp-images/uploads/", "/wp-content/uploads/")
    if path.lower().endswith(".webp") and re.search(r"\.(jpe?g|png)\.webp$", path, re.I):
        path = path[:-5]
    path = _SIZE_SUFFIX_RE.sub("", path)
    return path.lower()


def youtube_id(url: str) -> str | None:
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else None


def youtube_start_param(url: str) -> str | None:
    """Return a YouTube start-time value (seconds or 1h2m3s) if the URL has one."""
    parsed = urlparse(url or "")
    qs = parse_qs(parsed.query)
    raw = ""
    if qs.get("t"):
        raw = qs["t"][0]
    elif qs.get("start"):
        raw = qs["start"][0]
    elif (parsed.fragment or "").lower().startswith("t="):
        raw = parsed.fragment[2:]
    raw = (raw or "").strip()
    return raw or None


def youtube_watch_url(url: str) -> str | None:
    """Normalize any YouTube watch/embed/shorts/youtu.be URL to watch?v= (keep start time)."""
    vid = youtube_id(url)
    if not vid:
        return None
    out = f"https://www.youtube.com/watch?v={vid}"
    start = youtube_start_param(url)
    if start:
        out += f"&t={start}"
    return out


_VIMEO_RE = re.compile(
    r"(?:player\.)?vimeo\.com/(?:video/|channels/[^/]+/|groups/[^/]+/videos/)?(\d+)",
    re.I,
)
_TIKTOK_RE = re.compile(
    r"tiktok\.com/(?:@[^/]+/video/|video/|embed(?:/v2)?/)(\d+)",
    re.I,
)
_TIKTOK_FULL_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@[^/]+/video/\d+",
    re.I,
)
_TWEET_RE = re.compile(
    r"https?://(?:www\.)?(?:twitter\.com|x\.com)/([^/\s]+)/status(?:es)?/(\d+)",
    re.I,
)


def vimeo_watch_url(url: str) -> str | None:
    m = _VIMEO_RE.search(url or "")
    return f"https://vimeo.com/{m.group(1)}" if m else None


def tiktok_watch_url(url: str) -> str | None:
    full = _TIKTOK_FULL_RE.search(url or "")
    if full:
        return "https://www." + re.sub(r"^https?://(?:www\.)?", "", full.group(0), flags=re.I)
    m = _TIKTOK_RE.search(url or "")
    if m:
        return f"https://www.tiktok.com/video/{m.group(1)}"
    return None


def tweet_status_url(url: str) -> str | None:
    m = _TWEET_RE.search(url or "")
    if not m:
        return None
    # twitter.com is the long-standing core oEmbed provider; x.com is accepted too.
    return f"https://twitter.com/{m.group(1)}/status/{m.group(2)}"


def _oembed_paragraph(url: str) -> Tag:
    """A paragraph whose only content is a bare oEmbed URL (no <a>, no other text)."""
    p = BeautifulSoup("", "lxml").new_tag("p")
    p.string = url
    return p


def _replace_with_oembed(tag: Tag, url: str) -> Tag:
    p = _oembed_paragraph(url)
    tag.replace_with(p)
    p.insert_before("\n\n")
    p.insert_after("\n\n")
    return p


def _widget_only_parent(tag: Tag) -> Tag | None:
    """If the iframe/blockquote sits in a wrapper with no other real content, replace the wrapper."""
    parent = tag.parent
    if not isinstance(parent, Tag) or parent.name not in {"p", "div", "figure", "span", "section"}:
        return None
    others = []
    for child in parent.children:
        if isinstance(child, NavigableString) and not str(child).strip():
            continue
        if child is tag:
            continue
        others.append(child)
    # Allow a leftover "Watch on YouTube" link/text in the same wrapper.
    meaningful = []
    for child in others:
        if isinstance(child, Tag):
            text = child.get_text(" ", strip=True)
            href = child.get("href") or (child.find("a").get("href") if child.find("a") else "")
            if re.search(r"watch on youtube", text, re.I) or (href and youtube_id(href)):
                continue
            if child.name in {"br"}:
                continue
            meaningful.append(child)
        elif str(child).strip() and not re.search(r"watch on youtube", str(child), re.I):
            meaningful.append(child)
    return parent if not meaningful else None


def _empty_element(tag: Tag) -> bool:
    if tag.name in _VOID:
        return False
    if tag.name == "iframe":
        return not (tag.get("src") or "").strip()
    if tag.find(["img", "iframe", "br"]):
        return False
    text = tag.get_text(" ", strip=True)
    return not text


def _unwrap_picture(pic: Tag, base: str) -> None:
    img = pic.find("img")
    src = ""
    if img is not None:
        src = resolve_img_src(img, base)
    if not src:
        for source in pic.find_all("source"):
            src = _from_srcset(source.get("srcset") or source.get("data-srcset") or "", base)
            if src:
                src = prefer_raster_url(force_https(src))
                break
    if not src:
        pic.decompose()
        return
    alt = (img.get("alt") if img is not None else "") or ""
    new = BeautifulSoup("", "lxml").new_tag("img", src=src, alt=alt)
    pic.replace_with(new)



_BARE_URL_P_RE = re.compile(
    r"(?:[ \t]*\n)*<p>\s*(https?://[^\s<]+)\s*</p>(?:[ \t]*\n)*",
    re.I,
)


def _blank_lines_around_oembed_paragraphs(html: str) -> str:
    """Ensure each bare oEmbed URL sits in its own <p> with a blank line on both sides.

    WordPress autoembed matches a URL on its own line *or* alone in a <p>.
    Blank lines make both the classic and block paths reliable.
    """

    def repl(m: re.Match) -> str:
        return f"\n\n<p>{m.group(1)}</p>\n\n"

    return _BARE_URL_P_RE.sub(repl, html or "")


def sanitize_feed_html(
    html: str,
    base_url: str = "",
    lead_image: str = "",
) -> tuple[str, str, list[str]]:
    """Return (clean HTML, lead image URL, all absolute image URLs).

    The lead image is placed as the first element. Images use a single
    absolute https ``src`` (no data-src / srcset-only). WebP-Express URLs
    are rewritten to the JPEG/PNG original when the path pattern matches.
    """
    html = (html or "").strip()
    lead_image = prefer_raster_url(force_https(_abs(lead_image, base_url))) if lead_image else ""
    if not html and not lead_image:
        return "", "", []

    soup = BeautifulSoup(html or "<div></div>", "lxml")
    body = soup.body or soup

    for junk in body.find_all(["script", "style", "noscript", "form", "button", "svg", "link", "meta"]):
        junk.decompose()

    for pic in list(body.find_all("picture")):
        _unwrap_picture(pic, base_url)

    for img in list(body.find_all("img")):
        src = resolve_img_src(img, base_url)
        if not src or urlparse(src).scheme not in {"http", "https"}:
            img.decompose()
            continue
        src = prefer_raster_url(force_https(src))
        if not src:
            img.decompose()
            continue
        alt = img.get("alt") or ""
        img.attrs = {"src": src, "alt": alt}

    def _embed_src(tag: Tag) -> str:
        return force_https(_abs(tag.get("src") or tag.get("data-src") or tag.get("cite") or "", base_url))

    def _convert_embed(tag: Tag, url: str | None) -> bool:
        if not url:
            return False
        target = _widget_only_parent(tag) or tag
        _replace_with_oembed(target, url)
        return True

    # YouTube / Vimeo / TikTok / Twitter → bare oEmbed URL in its own <p>.
    # Facebook, Instagram, Telegram are not (reliably) core-oEmbed: keep a normal <a>.
    for iframe in list(body.find_all("iframe")):
        raw_src = _embed_src(iframe)
        if _convert_embed(iframe, youtube_watch_url(raw_src)):
            continue
        if _convert_embed(iframe, vimeo_watch_url(raw_src)):
            continue
        if _convert_embed(iframe, tiktok_watch_url(raw_src)):
            continue
        if _convert_embed(iframe, tweet_status_url(raw_src)):
            continue
        # Telegram widget or t.me embed → ordinary link, not a bare oEmbed paragraph.
        if raw_src and ("t.me/" in raw_src or "telegram.org" in raw_src):
            a = BeautifulSoup("", "lxml").new_tag("a", href=raw_src)
            a.string = raw_src
            wrap = BeautifulSoup("", "lxml").new_tag("p")
            wrap.append(a)
            target = _widget_only_parent(iframe) or iframe
            target.replace_with(wrap)
            continue
        if raw_src and urlparse(raw_src).scheme == "https":
            # Facebook / Instagram / anything else: visible link (core oEmbed does not apply).
            a = BeautifulSoup("", "lxml").new_tag("a", href=raw_src)
            a.string = raw_src
            wrap = BeautifulSoup("", "lxml").new_tag("p")
            wrap.append(a)
            target = _widget_only_parent(iframe) or iframe
            target.replace_with(wrap)
            continue
        iframe.decompose()

    # twitter-tweet / tiktok-embed blockquotes → bare status/video URL.
    for bq in list(body.find_all("blockquote")):
        classes = " ".join(bq.get("class") or []).lower()
        cite = bq.get("cite") or ""
        hrefs = [a.get("href") or "" for a in bq.find_all("a")]
        blob = " ".join([cite] + hrefs)
        url = tweet_status_url(blob) or tiktok_watch_url(blob) or youtube_watch_url(blob) or vimeo_watch_url(blob)
        if url and (re.search(r"twitter-tweet|tiktok-embed|twitter|tiktok|youtube", classes) or url):
            # Only convert known embed blockquotes, not ordinary quoted text that happens to contain a link.
            if re.search(r"twitter-tweet|tiktok-embed|instagram-media", classes) or tweet_status_url(blob) or tiktok_watch_url(cite):
                _convert_embed(bq, url)

    # Drop leftover "Watch on YouTube" fallbacks from earlier feed versions.
    for p in list(body.find_all("p")):
        text = p.get_text(" ", strip=True)
        if re.fullmatch(r"watch on youtube", text or "", flags=re.I):
            p.decompose()
            continue
        links = p.find_all("a")
        if (
            len(links) == 1
            and re.fullmatch(r"watch on youtube", links[0].get_text(" ", strip=True) or "", flags=re.I)
            and youtube_id(links[0].get("href") or "")
            and not (p.get_text(" ", strip=True) or "").replace(links[0].get_text(" ", strip=True), "").strip()
        ):
            p.decompose()

    # Drop javascript: and empty/broken links; absolutize the rest.
    for a in list(body.find_all("a")):
        href = force_https(_abs(a.get("href") or "", base_url))
        if not href or href.lower().startswith("javascript:"):
            a.unwrap()
            continue
        a.attrs = {"href": href}

    # Remove empty wrappers (often leftover lazy/gallery chrome).
    changed = True
    while changed:
        changed = False
        for tag in list(body.find_all(["div", "span", "section", "p", "figure"])):
            if _empty_element(tag):
                tag.decompose()
                changed = True

    # Collect current image URLs so we can pick a stable lead (prefer unsized).
    images: list[str] = []
    for img in body.find_all("img"):
        src = img.get("src") or ""
        if src and src not in images:
            images.append(src)

    if lead_image:
        lead_image = prefer_raster_url(force_https(_abs(lead_image, base_url)))
        stem = image_stem(lead_image)
        for url in images:
            if image_stem(url) == stem and not _SIZE_SUFFIX_RE.search(urlparse(url).path):
                lead_image = url
                break
    elif images:
        lead_image = images[0]
        stem = image_stem(lead_image)
        for url in images:
            if image_stem(url) == stem and not _SIZE_SUFFIX_RE.search(urlparse(url).path):
                lead_image = url
                break

    # Drop trailing/in-body social-card crops (Suspilne automatic-og) when the
    # item already has a real lead or body photo. Keep the og image only when
    # it is the sole photo, so it can still serve as the lead.
    has_real_photo = any(src and not is_social_card_image(src) for src in (images + ([lead_image] if lead_image else [])))
    if has_real_photo:
        for img in list(body.find_all("img")):
            if not is_social_card_image(img.get("src") or ""):
                continue
            parent = img.parent
            img.decompose()
            if parent and isinstance(parent, Tag) and parent.name in {"p", "figure", "div"} and _empty_element(parent):
                parent.decompose()
        if lead_image and is_social_card_image(lead_image):
            lead_image = next((u for u in images if u and not is_social_card_image(u)), "")
        images = [u for u in images if u and not is_social_card_image(u)]

    inner = "".join(str(c) for c in body.children).strip()
    inner_soup = BeautifulSoup(inner or "", "lxml")
    inner_body = inner_soup.body or inner_soup

    def _first_meaningful(node):
        for child in node.children:
            if isinstance(child, NavigableString) and not str(child).strip():
                continue
            return child
        return None

    first = _first_meaningful(inner_body)
    starts_with_lead = False
    if lead_image and isinstance(first, Tag):
        if first.name == "img" and (first.get("src") == lead_image):
            starts_with_lead = True
        elif first.name in {"p", "figure", "div"}:
            imgs = first.find_all("img")
            if len(imgs) == 1 and imgs[0].get("src") == lead_image and not first.get_text(strip=True):
                starts_with_lead = True

    if lead_image and not starts_with_lead:
        lead_tag = BeautifulSoup("", "lxml").new_tag("img", src=lead_image, alt="")
        wrap = BeautifulSoup("", "lxml").new_tag("p")
        wrap.append(lead_tag)
        # If the first node is already the same photo at another size, drop it.
        if isinstance(first, Tag):
            first_img = first if first.name == "img" else first.find("img")
            if first_img and image_stem(first_img.get("src") or "") == image_stem(lead_image):
                if first.name in {"p", "figure", "div"} and first.find("img") and not first.get_text(strip=True):
                    first.decompose()
                elif first.name == "img":
                    first.decompose()
        inner_body.insert(0, wrap)

    # Dedup after the lead is in place so a crop later in the article is dropped.
    seen_stems: set[str] = set()
    for img in list(inner_body.find_all("img")):
        src = img.get("src") or ""
        stem = image_stem(src)
        if stem in seen_stems:
            parent = img.parent
            img.decompose()
            if parent and isinstance(parent, Tag) and parent.name == "figure" and _empty_element(parent):
                parent.decompose()
            continue
        if stem:
            seen_stems.add(stem)

    clean = "".join(str(c) for c in inner_body.children).strip()
    final_soup = BeautifulSoup(clean or "", "lxml")
    images = []
    for img in (final_soup.body or final_soup).find_all("img"):
        src = img.get("src") or ""
        if src and src not in images:
            images.append(src)
    if images and not lead_image:
        lead_image = images[0]
    clean = _blank_lines_around_oembed_paragraphs(clean)
    return clean, lead_image, images
