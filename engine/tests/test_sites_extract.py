"""Extractor profiles and helpers for the four new Kharkiv sources."""

from bs4 import BeautifulSoup

from newsfeed.extract import (
    _abs,
    _attr_str,
    _collect_images,
    _drop_text_blocks,
    _lead_images,
    _pick_body,
    _profile_image_ok,
    _strip_boilerplate,
    _strip_junk,
    _truncate_at,
    image_url,
    serialize_body,
)
from newsfeed.sites import item_url_allowed, profile_for


def _extract(html: str, url: str) -> tuple[str, list[str], str]:
    soup = BeautifulSoup(html, "lxml")
    profile = profile_for(url)
    body = _pick_body(soup, profile)
    assert body is not None, f"no body for {url}"
    _strip_junk(body, profile.get("remove") or [])
    if profile.get("link_image_selector"):
        for a in body.select(profile["link_image_selector"]):
            href = _abs(_attr_str(a.get("href")), url)
            img = a.find("img")
            if img is not None and href:
                img.attrs = {"src": href, "alt": img.get("alt") or ""}
    _truncate_at(body, profile.get("truncate_at_re"))
    _drop_text_blocks(body, profile.get("drop_text_re"))
    _strip_boilerplate(body)
    if profile.get("skip_image_re"):
        for img in list(body.find_all("img")):
            src = image_url(img, url)
            if not _profile_image_ok(src, profile):
                img.decompose()
    lead = _lead_images(soup, profile, url)
    images = list(lead)
    for src in _collect_images(body, url):
        if src not in images:
            images.append(src)
    html_out = serialize_body(body, url)
    text = BeautifulSoup(html_out, "lxml").get_text(" ", strip=True)
    return html_out, images, text


def test_image_url_prefers_lazy_data_src():
    soup = BeautifulSoup(
        '<img class="penci-lazy" src="data:image/svg+xml,%3Csvg/%3E" '
        'data-src="https://atn.ua/assets/uploads/pic-1170x781.jpg">',
        "lxml",
    )
    assert image_url(soup.img, "https://atn.ua/x") == "https://atn.ua/assets/uploads/pic-1170x781.jpg"


def test_image_url_prefers_largest_suspilne_width():
    soup = BeautifulSoup(
        '<img src="https://cdn4.suspilne.media/images/resize/400x1.5/a.jpg" '
        'data-530-src="https://cdn4.suspilne.media/images/resize/530x1.5/a.jpg" '
        'data-1040-src="https://cdn4.suspilne.media/images/resize/1040x1.5/a.jpg">',
        "lxml",
    )
    assert image_url(soup.img, "https://suspilne.media/x") == (
        "https://cdn4.suspilne.media/images/resize/1040x1.5/a.jpg"
    )


def test_objectiv_strips_subscribe_and_read_also():
    html = """
    <html><body>
      <div class="l-slider"><img class="b-slider__img" src="https://www.objectiv.tv/wp-content/uploads/lead.webp" width="1536" height="1026"></div>
      <div class="b-google-news">Підпишіться на нас в Google</div>
      <div class="b-single__content b-text">
        <p>У ГУ ДСНС України в Харківській області повідомили, що виникли три пожежі.</p>
        <p><img class="size-large" src="https://www.objectiv.tv/wp-content/uploads/2026/11/fire.webp" width="1024" height="684"></p>
        <h3>Читайте також: РФ вдарила по багатоповерхівці у Харкові</h3>
      </div>
    </body></html>
    """
    out, images, text = _extract(html, "https://www.objectiv.tv/uk/objectively/2026/11/27/x/")
    assert "три пожежі" in text
    assert "Підпишіться" not in text
    assert "Читайте також" not in out
    assert images[0].endswith("/lead.webp")
    assert any(u.endswith("/fire.webp") for u in images)
    assert profile_for("https://www.objectiv.tv/uk/x/").get("name") == "Обʼєктив"


def test_nakypilo_keeps_gallery_skips_related():
    html = """
    <html><body>
      <div class="cs-entry__metabar">Total 0 Shares 0 0 0</div>
      <div class="entry-content">
        <p class="wp-block-paragraph">Щонайменше пʼятеро жінок та один чоловік отримали гостру реакцію на стрес через російський обстріл Ізюма, повідомляє Харківська обласна прокуратура.</p>
        <div class="ngallery">
          <img class="wp-image-1" src="https://nakypilo.ua/wp-content/uploads/2026/11/a-1024.jpg" width="1024" height="700">
          <img class="wp-image-2" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'%3E%3C/svg%3E">
          <img class="wp-image-2" src="https://nakypilo.ua/wp-content/uploads/2026/11/b-1024.jpg" width="1024" height="700">
        </div>
      </div>
      <div class="cs-entry__post-related"><p>Інша новина про Харків</p><img src="https://nakypilo.ua/wp-content/uploads/related.jpg"></div>
    </body></html>
    """
    out, images, text = _extract(html, "https://nakypilo.ua/novyny/x/")
    assert "пʼятеро жінок" in text
    assert "Інша новина" not in text
    assert "Total 0 Shares" not in text
    assert "https://nakypilo.ua/wp-content/uploads/2026/11/a-1024.jpg" in images
    assert "https://nakypilo.ua/wp-content/uploads/2026/11/b-1024.jpg" in images
    assert all("related.jpg" not in u for u in images)


def test_atn_lazy_lead_and_telegram_subscribe():
    html = """
    <html><body>
      <div class="post-image">
        <img class="pc-singlep-img penci-lazy" width="1170" height="781"
             src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'%3E%3C/svg%3E"
             data-src="https://atn.ua/assets/uploads/lead-1170x781.jpg">
      </div>
      <div class="entry-content">
        <p>Російські війська атакували Харків та чотири райони області.</p>
        <figure class="wp-block-gallery">
          <img class="penci-lazy" src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'%3E%3C/svg%3E"
               data-src="https://atn.ua/assets/uploads/gallery.jpg">
        </figure>
        <p>Підписуйтесь на Telegram-канал “Новини АТН”</p>
        <div class="penci-ilrltpost-beaf">Вам може бути цікаво Кількість загиблих у Вовчанську зросла</div>
      </div>
    </body></html>
    """
    out, images, text = _extract(html, "https://atn.ua/kharkiv/x-1/")
    assert "чотири райони області" in text
    assert "Підписуйтесь" not in text
    assert "Вам може бути цікаво" not in text
    assert images[0] == "https://atn.ua/assets/uploads/lead-1170x781.jpg"
    assert "https://atn.ua/assets/uploads/gallery.jpg" in images


def test_suspilne_lead_and_other_region_urls():
    html = """
    <html><body>
      <div class="l-c-md">
        <img class="c-article-image" src="https://cdn4.suspilne.media/images/resize/400x1.78/lead.jpg"
             srcset="https://cdn4.suspilne.media/images/resize/530x1.78/lead.jpg 530w, https://cdn4.suspilne.media/images/resize/1040x1.78/lead.jpg 1040w">
      </div>
      <article class="post-body">
        <div class="c-article-content">
          <p>Вранці 27 листопада росіяни завдали ударів по Шевченківському району Харкова.</p>
          <div class="article-image">
            <img class="js-lazy-loading-img article-image__picture"
                 src="https://cdn4.suspilne.media/images/resize/400x1.5/body.jpg"
                 data-530-src="https://cdn4.suspilne.media/images/resize/530x1.5/body.jpg"
                 data-1040-src="https://cdn4.suspilne.media/images/resize/1040x1.5/body.jpg">
          </div>
          <a class="c-google-preferred-source-inline">Рекомендоване джерело Google</a>
        </div>
      </article>
    </body></html>
    """
    out, images, text = _extract(html, "https://suspilne.media/kharkiv/1458107-x/")
    assert "Шевченківському району Харкова" in text
    assert "Рекомендоване джерело" not in text
    assert images[0].endswith("/1040x1.78/lead.jpg")
    assert "https://cdn4.suspilne.media/images/resize/1040x1.5/body.jpg" in images

    prof = profile_for("https://suspilne.media/kharkiv/1/")
    assert item_url_allowed("https://suspilne.media/kharkiv/1458107-x/", prof)
    assert item_url_allowed("https://suspilne.media/1458274-national-story/", prof)
    assert item_url_allowed("https://suspilne.media/sport/1/", prof)
    assert not item_url_allowed(
        "https://suspilne.media/chernihiv/1457923-harkiv-stavit-a-cernigiv-ni/",
        prof,
    )


def test_title_dedup_skips_conflicting_calendar_dates():
    from newsfeed.main import _choose_existing_match
    from newsfeed.dedup import normalize_title

    recent = [
        {
            "title": "Новини Харкова 26 листопада 2026 року – головне за день",
            "title_norm": normalize_title("Новини Харкова 26 листопада 2026 року – головне за день"),
            "url_hash": "a",
        },
        {
            "title": "Без опадів, але з вітром: прогноз погоди на 26 листопада у Харкові та області",
            "title_norm": normalize_title(
                "Без опадів, але з вітром: прогноз погоди на 26 листопада у Харкові та області"
            ),
            "url_hash": "b",
        },
    ]
    wrap27 = {
        "title": "Новини Харкова 27 листопада 2026 року – головне за день",
        "title_norm": normalize_title("Новини Харкова 27 листопада 2026 року – головне за день"),
        "url_hash": "c",
    }
    weather27 = {
        "title": "Без опадів та -2: прогноз погоди на 27 листопада у Харкові та області",
        "title_norm": normalize_title(
            "Без опадів та -2: прогноз погоди на 27 листопада у Харкові та області"
        ),
        "url_hash": "d",
    }
    same_outage = {
        "title": "Адреси в Київському районі Харкова, де 30 листопада не буде світла",
        "title_norm": normalize_title(
            "Адреси в Київському районі Харкова, де 30 листопада не буде світла"
        ),
        "url_hash": "e",
    }
    recent.append(
        {
            "title": "Світла не буде в одному з районів Харкова 30 листопада: адреси",
            "title_norm": normalize_title(
                "Світла не буде в одному з районів Харкова 30 листопада: адреси"
            ),
            "url_hash": "f",
        }
    )
    assert _choose_existing_match(wrap27, recent, 87, 0.58) == (None, "")
    assert _choose_existing_match(weather27, recent, 87, 0.58) == (None, "")
    match, reason = _choose_existing_match(same_outage, recent, 87, 0.58)
    assert reason == "duplicate_title"
    assert "30 листопада" in (match["title"] if match else "")


def test_slk_strips_boilerplate_keeps_gallery_and_embed():
    html = """
    <html><head>
      <meta property="og:image" content="/storage/13/70/abc.jpeg">
      <meta property="og:site_name" content="Слобідський край">
    </head><body>
      <div class="news-page__main main-content">
        <div class="breadcrumbs">Головна / Новини</div>
        <div class="news">
          <div class="news__title"><h1 class="title">Атака безпілотників на Лозівську громаду</h1></div>
          <div>
            <div class="news__caption"><div class="news__info"><span class="date">28.11.2026</span></div></div>
            <div class="news__share"><a class="a2a_button_telegram">Share</a></div>
          </div>
          <div class="news__content text-editor-content">
            <img alt="Наслідки обстрілу" src="/storage_cache/41/29/lead_w694h0.jpeg">
            <small class="news__image__title">Наслідки російського обстрілу / Фото: ОВА</small>
            <div class="text-editor-content__styled"><p><em>У Лозівській громаді постраждали п’ятеро людей.</em></p></div>
            <div class="specific-content__728x90"><ins class="adsbygoogle"></ins></div>
            <p><span>Про це повідомляє медіа «Слобідський край».</span></p>
            <p><img alt="Інфографіка" src="https://www.slk.kh.ua/content/2026/11/18/infographic.jpg"></p>
            <p><iframe src="https://www.youtube.com/embed/BSt6by4y7yQ" title="plot" width="361" height="642"></iframe></p>
            <div class="news__content text-editor-content">
              <p><em><strong>Ваша підтримка – запорука нашої щоденної праці. Ставайте частиною
              <a href="https://www.slk.kh.ua/community.html">спільноти «Слобідського краю»</a>.</strong></em></p>
            </div>
            <p>У старостинському окрузі допомагають літнім людям заготовляти дрова.</p>
            <p><em>Вдячні кожному читачеві «Слобідського краю» за довіру та підтримку. Приєднуйтесь до читацької
            <a href="https://www.slk.kh.ua/community.html">спільноти «Слобідського краю»</a>.</em></p>
          </div>
          <div class="col-2 popup-gallery">
            <a href="/storage_cache/78/82/gal_w1920h0.webp">
              <img src="/storage_cache/78/82/gal_w323h215.webp">
            </a>
          </div>
          <div class="text-editor-content">
            <p><em><strong>Читайте також:</strong>
              <a href="https://www.slk.kh.ua/news/other.html">Інша новина про Харків</a></em></p>
          </div>
          <div class="about-author">
            <div class="about-author__avatar">
              <img src="/storage/22/41/author.jpeg" width="70" height="70" alt="">
            </div>
            <div class="tags"><a href="/tags/x">#війна</a></div>
          </div>
          <a href="https://t.me/SlobKray" class="new-platform-link">
            <span class="new-platform-link__text">Оперативні новини читайте в нашому Telegram</span>
            <img src="/img/social/new-telegram.svg" class="new-platform-link__icon" alt="">
          </a>
        </div>
      </div>
    </body></html>
    """
    url = "https://www.slk.kh.ua/oblast-online/lozova/ataka-bezpilotnikiv.html"
    out, images, text = _extract(html, url)
    assert "постраждали п’ятеро людей" in text
    assert "допомагають літнім людям заготовляти дрова" in text
    assert "Наслідки російського обстрілу" in text
    assert "Ваша підтримка" not in text
    assert "Вдячні кожному" not in text
    assert "Приєднуйтесь" not in text
    assert "Читайте також" not in out
    assert "Інша новина про Харків" not in text
    assert "Оперативні новини" not in text
    assert "Telegram" not in text
    assert "Головна / Новини" not in text
    assert "#війна" not in text
    lead = "https://www.slk.kh.ua/storage_cache/41/29/lead_w694h0.jpeg"
    assert images[0] == lead
    assert "https://www.slk.kh.ua/content/2026/11/18/infographic.jpg" in images
    assert "https://www.slk.kh.ua/storage_cache/78/82/gal_w1920h0.webp" in images
    assert all("w323h215" not in u for u in images)
    assert all("author.jpeg" not in u for u in images)
    assert all("/storage/13/70/abc.jpeg" not in u for u in images)
    assert all("/img/social/" not in u for u in images)
    assert 'src="https://www.youtube.com/embed/BSt6by4y7yQ"' in out
    assert lead in out
    first_img = BeautifulSoup(out, "lxml").find("img")
    assert first_img is not None and first_img.get("src") == lead
    prof = profile_for(url)
    assert prof.get("name") == "Слобідський край"
    assert prof.get("ignore_page_date") is True
    assert profile_for("https://slk.kh.ua/news/x.html").get("name") == "Слобідський край"
