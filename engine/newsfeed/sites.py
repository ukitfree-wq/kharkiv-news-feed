"""Per-site article body selectors and junk to strip.

Tuned against live articles from the five Kharkiv sources (November 2026):
trafilatura often drops in-article images (sq.com.ua, mykharkov.info, 2day
lazy-loaded galleries) and always drops iframes/embeds.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Each profile:
#   selectors: CSS selectors tried in order for the article body
#   lead_image_selectors: extra lead/hero images outside the body
#   remove: CSS selectors to drop from the chosen body (related, comments, ads)
#   name: default publisher label
SITE_PROFILES: dict[str, dict] = {
    "infocity.kharkiv.ua": {
        "name": "Інфосіті",
        "selectors": ["article.post main", "article main", "article.post", "article"],
        "lead_image_selectors": [],
        "remove": [
            "header",
            "footer",
            "nav",
            ".breadcrumbs",
            ".header-meta",
            ".wp-block-latest-posts",
            ".related",
            ".sidebar",
            ".comments",
            "#comments",
            ".comment-respond",
            ".sharedaddy",
            ".metas",
        ],
    },
    "www.sq.com.ua": {
        "name": "Status Quo",
        "selectors": ["#news-body", "sq-news .article-body #news-body", ".article-body"],
        "lead_image_selectors": ["sq-news img.big-img", "img.big-img"],
        "remove": [
            "sq-ad-banners",
            "sq-news-block",
            "sq-small-article",
            ".share",
            "sq-weather-widget",
        ],
    },
    "sq.com.ua": {
        "name": "Status Quo",
        "selectors": ["#news-body", "sq-news .article-body #news-body", ".article-body"],
        "lead_image_selectors": ["sq-news img.big-img", "img.big-img"],
        "remove": ["sq-ad-banners", "sq-news-block", "sq-small-article", ".share", "sq-weather-widget"],
    },
    "2day.kh.ua": {
        "name": "Kharkiv Today",
        "selectors": [
            "article.node-type--article .field--name-field-paragraphs",
            "article.node-type--article",
            "article",
        ],
        "lead_image_selectors": [
            "article .field--name-field-teaser-media img",
            "article img.image-style-glavnoe",
        ],
        "remove": [
            ".block-similartag",
            ".field--name-field-tags",
            ".inline__links",
            "nav",
            ".sidebar",
            ".comments",
            "#comments",
            ".node-teaser",
            "script",
        ],
    },
    "gwaramedia.com": {
        "name": "Ґвара Медіа",
        "selectors": ["div.article-body", "article .article-body", "article.article"],
        "lead_image_selectors": [".article-promo-screen img", ".post-promo-wrap img"],
        "remove": [".article-tags", ".sign-error", ".tags", "footer", "nav", ".comments", "#comments"],
    },
    "www.gwaramedia.com": {
        "name": "Ґвара Медіа",
        "selectors": ["div.article-body", "article .article-body", "article.article"],
        "lead_image_selectors": [".article-promo-screen img", ".post-promo-wrap img"],
        "remove": [".article-tags", ".sign-error", ".tags", "footer", "nav", ".comments", "#comments"],
    },
    "mykharkov.info": {
        "name": "Мій Харків",
        "selectors": ["div.art-main", "article.article-instance div.art-main"],
        "lead_image_selectors": ["figure.hero-img img", ".hero-img-media img"],
        "remove": [
            "aside.art-side",
            "aside",
            ".related",
            "section.block",
            "nav.crumbs",
            ".share-modal",
            ".side-weather",
            "iframe.side-weather__frame",
            ".comments",
            "#comments",
        ],
    },
}

DEFAULT_REMOVE = [
    "nav",
    "header",
    "footer",
    "aside",
    ".sidebar",
    ".related",
    ".comments",
    "#comments",
    ".comment-respond",
    ".advert",
    ".ads",
    ".ad-block",
    ".share",
    ".social-share",
    ".breadcrumbs",
]

GENERIC_SELECTORS = [
    "article .entry-content",
    "article .post-content",
    "article .article-body",
    "[itemprop=articleBody]",
    "article",
    "main",
]


def profile_for(url: str) -> dict:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www.") and host not in SITE_PROFILES:
        alt = host[4:]
        if alt in SITE_PROFILES:
            host = alt
    if host in SITE_PROFILES:
        return SITE_PROFILES[host]
    # try without www already handled; try suffix match
    for key, prof in SITE_PROFILES.items():
        if host == key or host.endswith("." + key):
            return prof
    return {
        "name": host,
        "selectors": GENERIC_SELECTORS,
        "lead_image_selectors": [],
        "remove": DEFAULT_REMOVE,
    }
