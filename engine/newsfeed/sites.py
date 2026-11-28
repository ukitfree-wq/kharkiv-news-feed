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
        # Social-links promo paragraph ("Читайте нас у Facebook, Telegram, … TikTok.").
        # It often sits mid-article, so only that block is removed (the whole-text
        # match keeps any wrapper with real content).
        "drop_text_re": r"^читайте нас у\b[^\n]{0,250}?(facebook|telegram|instagram|youtube|tiktok)[^\n]{0,80}$",
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
    "slk.kh.ua": {
        "name": "Слобідський край",
        "strip_comments": True,
        "selectors": ["div.news", "div.news__content"],
        "title_selector": "div.news__title h1, h1.title",
        "lead_image_selectors": ["div.news__content > img"],
        # og:image is the same photo under /storage/… (different path); keep the
        # on-page lead so the enclosure matches the first <img>.
        "skip_og_when_lead": True,
        # JSON-LD datePublished is consistently two hours ahead of the visible
        # time and the RSS pubDate (local 09:27 is stored as 11:27+02:00).
        "ignore_page_date": True,
        "link_image_selector": "div.popup-gallery a",
        "skip_image_re": r"/img/social/",
        # Subscribe pitches, including ones inserted mid-article. Do not
        # truncate_at: later paragraphs are still part of the story.
        "drop_text_re": (
            r"^(вдячні кожному читачеві|кожен з вас допомагає нам|ваша підтримка)"
            r"\b[^\n]{0,300}?спільнот"
        ),
        "remove": [
            "div.breadcrumbs",
            "div.news__title",
            "div.news__share",
            "div.news__info",
            "div.specific-content__728x90",
            "ins.adsbygoogle",
            "div.about-author",
            "div.tags",
            "a.new-platform-link",
            "div.news-platforms",
            # «Читайте також» block after the body (not the body itself).
            "div.text-editor-content:not(.news__content)",
            "aside",
            "nav",
            "header",
            "footer",
            "script",
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
            "div.read-m",  # «Читати ще» related-article cards inside the text
            "div.hdr-media-cmpnt",
            "nav",
            "header",
            "footer",
            "aside",
        ],
        # Other regional newsrooms. National/vertical paths (/sport/, /culture/,
        # numeric ids) still pass if the title keyword filter matches.
        "drop_first_path_segments": SUSPILNE_OTHER_REGIONS,
        # Boilerplate tail ("Читайте нас у Telegram…", "Стежте за головними новинами…").
        "truncate_at_re": r"^(читайте нас у|стежте за головними новинами)\b",
    },

    # ---- Odesa region (engine/regions/odesa) ----
    "pushkinska.net": {
        "strip_comments": True,
        "name": "Пушкінська",
        "selectors": ["article div.entry-content", "div.bnq-single-con div.entry-content"],
        "lead_image_selectors": ["article div.pfy-single-post-thumb img"],
        "remove": ["div.bnq-inner-social", ".sharedaddy", ".share", "nav", "header", "footer", "script"],
    },
    "chis-chernomorsk.com.ua": {
        "strip_comments": True,
        "name": "ЧІС",
        "selectors": ["article.page__article"],
        "lead_image_selectors": ["article.page__article div.page__poster img"],
        # <time datetime> is often a day off; the RSS pubDate is right.
        "ignore_page_date": True,
        "remove": [
            "header.page__header",
            "a.page__subscribe",
            "div.bannerAdvertising",
            "div.page__prev-next",
            "div.page__tags",
            "div.page__footer",
            "footer",
            "div.sect",
            "div.page__comments",
            "form",
            "script",
        ],
        "drop_text_re": r"^(підписуйся|підписуйтесь|слідкуйте)\b.{0,120}(facebook|telegram|instagram)",
    },
    "bessarabia.ua": {
        "strip_comments": True,
        "name": "Бессарабія UA",
        "selectors": ["div.content div.postContent", "div.postContent"],
        "lead_image_selectors": [],
        "remove": ["#fb-root", "noscript", ".share", "script", "ins", "div.subscribeTelegramWrap"],
    },
    "bdmr.gov.ua": {
        "strip_comments": True,
        "name": "Білгород-Дністровська міська рада",
        "selectors": ["section.section-one-news div.news-block", "div.news-block"],
        # The page <h1> is the site name; the headline is the <h4>.
        "title_selector": "div.news-block > h4",
        "lead_image_selectors": [],
        "remove": [":scope > div.breadcrumb", ":scope > p.date", ":scope > h4", "script"],
    },
    "bessarabiainform.com": {
        "strip_comments": True,
        "name": "Бессарабія INFORM",
        "selectors": ["article.post-item div.post-item__content.typography-default"],
        "lead_image_selectors": ["article.post-item div.post-item__thumbnail img"],
        "remove": [
            "a.bi-follow-banner",
            "div.bi-follow-banner",
            "div.g",
            "p.bi-previous-news",
            "aside",
            "ul.share-list",
            "div.post-tags",
            "script",
            "ins",
        ],
        "drop_text_re": r"^(будьте в курсі головних подій|найважливіші події бессарабії|додайте «бессарабія inform»)",
        "truncate_at_re": r"^(раніше ми повідомляли|читайте також)\s*:",
    },
    "on.od.ua": {
        # Одеса News (Ukrainian edition /uk/; the default edition is Russian).
        "strip_comments": True,
        "name": "Одеса News",
        "selectors": ["article.main-article div.entry-content", "article div.entry-content"],
        # No lead selector: the og:image is the same file, percent-encoded like the body images.
        "lead_image_selectors": [],
        "remove": ["div.onea-related-links", ".sharedaddy", ".share", "script", "ins"],
        "drop_text_re": r"^(стежте за важливими новинами|следите за важными новостями)",
    },
    "intent.press": {
        # Інтент: the article page is div#article-text-<slug>; the headline,
        # date and lead photo sit in the div.blog_list_item before it.
        "strip_comments": True,
        "name": "Інтент",
        "selectors": ['#type-cycle-0 div[id^="article-text-"]', 'div[id^="article-text-"]'],
        "lead_image_selectors": ["#type-cycle-0 div.blog_list_item picture img", "div.blog_list_item picture img"],
        "skip_og_when_lead": True,
        "remove": ['div[style*="background:#eeeeee"]', "p.italic.font-light.text-end", "script", "ins"],
        "drop_text_re": r"^(читати також|читайте також)\s*:",
    },
    "od-news.com": {
        "strip_comments": True,
        "name": "Od-news",
        "selectors": ["article div.entry-content"],
        "lead_image_selectors": [],
        "remove": ["#telegram_subscribe", "div.yarpp", ".sharedaddy", ".share", "script", "ins"],
        "truncate_at_re": r"^(другие интересные новости|інші цікаві новини)\s*:",
    },
    "odessa-life.od.ua": {
        "strip_comments": True,
        "name": "Одеське життя",
        "selectors": ["article div.entry-content"],
        "lead_image_selectors": [],
        "remove": [
            "div.ai-viewports",
            "div.code-block",
            "blockquote.box_color",
            "section.below-content",
            "div.social-share-buttons",
            "script",
            "ins",
        ],
        "drop_text_re": r"^(ви також можете дізнатися|за темою|читайте також)\s*:",
    },
    "www.048.ua": {
        "strip_comments": True,
        "name": "048.ua",
        "api": "cis",
        "allow_image_re": r"^https://s\.048\.ua/",
        "selectors": ["div.article-details__text app-model-content", "div.article-details__text"],
        "lead_image_selectors": [],
        "skip_image_re": r"/dist-assets/images/",
        "truncate_at_re": r"^читайте також\s*:?\s*$",
        "drop_text_re": r"^(нагадуємо|читайте також|раніше ми писали)\s*:",
        "remove": ["script"],
    },
    "osvita-omr.gov.ua": {
        "strip_comments": True,
        "name": "Департамент освіти Одеської міськради",
        "selectors": ["article.post-full div.entry-content"],
        "title_selector": "article.post-full h2.entry-title",
        "lead_image_selectors": ["article.post-full div.post-image img"],
        "remove": ["div.post-share", "script"],
    },
    "grad.ua": {
        "strip_comments": True,
        "name": "Новини Град",
        "selectors": ["div#flstr div.dpad div.maincont", "div#flstr div.dpad", "div.fullstory"],
        "lead_image_selectors": [],
        "link_image_selector": "a.highslide, a[href*='/uploads/posts/']",
        "remove": ["style", "script", "ins", "div.g_news", "div.grad_tags", "div.googleBnr", "div.related_blok"],
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


def apply_overrides(overrides: dict | None) -> None:
    """Region config `site_overrides`: per-host changes to SITE_PROFILES.

    Used for shared sources such as Suspilne, whose regional newsroom filter and
    publisher name differ per region feed. Lists become frozensets for
    drop_first_path_segments; other keys replace the profile value.
    """
    for host, changes in (overrides or {}).items():
        prof = dict(SITE_PROFILES.get(host) or {"name": host, "selectors": GENERIC_SELECTORS,
                                                  "lead_image_selectors": [], "remove": DEFAULT_REMOVE})
        for key, val in (changes or {}).items():
            if key == "drop_first_path_segments":
                val = frozenset(str(v).lower() for v in (val or []))
            prof[key] = val
        SITE_PROFILES[host] = prof


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
