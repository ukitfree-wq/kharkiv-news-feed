"""Per-site article body selectors and junk to strip.

Tuned against live articles from the Kharkiv sources (November 2026):
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
# Other Suspilne regional newsrooms (not Kharkiv). Used by item_url_allowed().
SUSPILNE_OTHER_REGIONS = frozenset({
    "cherkasy",
    "chernihiv",
    "chernivtsi",
    "crimea",
    "dnipro",
    "donbas",
    "donetsk",
    "ivano-frankivsk",
    "kherson",
    "khmelnytskiy",
    "khmelnytskyi",
    "kropyvnytskiy",
    "kropyvnytskyi",
    "kyiv",
    "luhansk",
    "lutsk",
    "lviv",
    "mykolaiv",
    "odesa",
    "poltava",
    "rivne",
    "sumy",
    "ternopil",
    "uzhhorod",
    "vinnytsia",
    "zaporizhzhia",
    "zhytomyr",
})

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
    "objectiv.tv": {
        "name": "Обʼєктив",
        "selectors": ["div.b-single__content", "div.b-text", "article.b-single"],
        "lead_image_selectors": [
            "div.l-slider img.b-slider__img",
            "div.l-slider img",
            "article.b-single img.b-slider__img",
        ],
        "remove": [
            "div.b-google-news",
            "div.b-similarNews",
            "div.b-socialShare",
            "div.b-meta",
            "div.b-single__author",
            "div.b-postAuthor",
            "div.b-sidebar",
            "aside.b-sidebar",
            "div.b-banner250",
            "section.echo_seo_text",
            "section.b-postsList",
            "div.b-postsList",
            "div.b-postInfo",
            "nav",
            "header",
            "footer",
        ],
    },
    "nakypilo.ua": {
        "name": "Накипіло",
        "selectors": ["div.entry-content", "div.cs-entry__content-wrap"],
        "lead_image_selectors": [],
        "remove": [
            "div.cs-entry__post-related",
            "div.cs-entry__metabar",
            "div.cs-entry__header",
            ".sharedaddy",
            ".share",
            "nav",
            "header",
            "footer",
            ".comments",
            "#comments",
            ".comment-respond",
        ],
    },
    "atn.ua": {
        "name": "АТН",
        "selectors": ["div.entry-content", "div.inner-post-entry", "div.post-entry"],
        "lead_image_selectors": [
            "div.post-image img.pc-singlep-img",
            "div.post-image img",
            "img.pc-singlep-img",
        ],
        "remove": [
            "div.penci-ilrltpost-beaf",
            "div.code-block",
            "div.penci-single-link-pages",
            "div.penci-sidebar-content",
            ".penci-related",
            ".sharedaddy",
            ".share",
            "nav",
            "header",
            "footer",
            ".comments",
            "#comments",
            ".comment-respond",
        ],
    },
    "suspilne.media": {
        "name": "Суспільне Харків",
        "selectors": [
            "div.c-article-content",
            "article.post-body",
            "div.pt-bd_wrp",
        ],
        "lead_image_selectors": [
            "img.c-article-image",
            "div.l-c-md img.c-article-image",
            "div.article-image img",
        ],
        "remove": [
            "div.c-article-info-share",
            "div.c-share-btn-container",
            "a.c-google-preferred-source",
            "a.c-google-preferred-source-inline",
            "aside.c-google-preferred-source-placement",
            "div.c-google-preferred-source-inline",
            "img.c__author-avatar",
            "article.post-aside",
            "article.two-v-sctn",
            "article.c-article-card",
            "div.hdr-media-list",
            "div.hdr-media-cmpnt",
            "nav",
            "header",
            "footer",
            "aside",
        ],
        # Other regional newsrooms. National/vertical paths (/sport/, /culture/,
        # numeric ids) still pass if the title keyword filter matches.
        "drop_first_path_segments": SUSPILNE_OTHER_REGIONS,
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

def item_url_allowed(url: str, profile: dict | None = None) -> bool:
    """Return False if this article URL is from a dropped section of the site.

    Used for Suspilne's national feed: a story from another regional newsroom
    can mention Харків in the title without being Kharkiv news.
    """
    blocked = (profile or {}).get("drop_first_path_segments") or ()
    if not blocked:
        return True
    parts = [p for p in urlparse(url or "").path.split("/") if p]
    if not parts:
        return True
    return parts[0].lower() not in blocked
