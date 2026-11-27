"""Extractor profiles and helpers for the four new Kharkiv sources."""

from bs4 import BeautifulSoup

from newsfeed.extract import (
    _collect_images,
    _lead_images,
    _pick_body,
    _strip_boilerplate,
    _strip_junk,
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
    _strip_boilerplate(body)
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
