import re

from newsfeed.feed_html import (
    image_stem,
    prefer_raster_url,
    is_social_card_image,
    sanitize_feed_html,
    youtube_id,
    youtube_watch_url,
)

def test_webp_express_rewrite():
    u = "https://gwaramedia.com/wp-content/webp-express/webp-images/uploads/2026/11/pic.jpg.webp"
    assert prefer_raster_url(u) == "https://gwaramedia.com/wp-content/uploads/2026/11/pic.jpg"
    u2 = "https://gwaramedia.com/wp-content/webp-express/webp-images/uploads/2023/06/tg-replace-2.png.webp?x40282"
    assert prefer_raster_url(u2) == "https://gwaramedia.com/wp-content/uploads/2023/06/tg-replace-2.png"

def test_sanitize_lazy_srcset_relative_and_lead():
    html = '''
    <div></div>
    <p>Hello</p>
    <img data-src="/img/lead.jpg" src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" srcset="/img/lead-300x200.jpg 300w" alt="Lead">
    <picture>
      <source data-srcset="https://gwaramedia.com/wp-content/webp-express/webp-images/uploads/2026/11/a.jpg.webp">
      <img src="https://gwaramedia.com/wp-content/webp-express/webp-images/uploads/2026/11/a.jpg.webp" alt="A">
    </picture>
    '''
    clean, lead, images = sanitize_feed_html(html, base_url="https://example.com/post", lead_image="")
    assert clean.startswith("<p><img") or "<img" in clean[:80]
    assert lead == "https://example.com/img/lead.jpg"
    assert "data-src" not in clean
    assert "srcset" not in clean
    assert "data:image" not in clean
    assert "webp-express" not in clean
    assert "https://gwaramedia.com/wp-content/uploads/2026/11/a.jpg" in clean
    assert all(u.startswith("https://") for u in images)

def test_youtube_iframe_becomes_bare_watch_url():
    html = (
        '<p>Vid</p>'
        '<iframe src="https://www.youtube-nocookie.com/embed/OR7ozlT5cLY?rel=0" width="100%" height="480"></iframe>'
        '<p><a href="https://www.youtube.com/watch?v=OR7ozlT5cLY">Watch on YouTube</a></p>'
        '<script>alert(1)</script>'
    )
    clean, lead, images = sanitize_feed_html(html, base_url="https://mykharkov.info/x")
    assert "<script" not in clean
    assert "<iframe" not in clean.lower()
    assert "Watch on YouTube" not in clean
    assert "<a" not in clean
    # Bare URL alone in a paragraph, with blank lines around it for autoembed.
    assert re.search(r"\n\n<p>https://www.youtube.com/watch\?v=OR7ozlT5cLY</p>\n\n", clean)
    assert clean.count("https://www.youtube.com/watch?v=OR7ozlT5cLY") == 1

def test_youtube_start_time_youtu_be_and_shorts():
    assert youtube_watch_url("https://youtu.be/OR7ozlT5cLY?t=90") == "https://www.youtube.com/watch?v=OR7ozlT5cLY&t=90"
    assert youtube_watch_url("https://www.youtube.com/embed/OR7ozlT5cLY?start=15") == "https://www.youtube.com/watch?v=OR7ozlT5cLY&t=15"
    assert youtube_watch_url("https://www.youtube.com/shorts/OR7ozlT5cLY") == "https://www.youtube.com/watch?v=OR7ozlT5cLY"
    html = '<iframe src="https://www.youtube.com/embed/OR7ozlT5cLY?start=15"></iframe>'
    clean, *_ = sanitize_feed_html(html)
    assert "<p>https://www.youtube.com/watch?v=OR7ozlT5cLY&amp;t=15</p>" in clean or "<p>https://www.youtube.com/watch?v=OR7ozlT5cLY&t=15</p>" in clean

def test_vimeo_tiktok_tweet_oembed_facebook_stays_link():
    html = """
    <iframe src="https://player.vimeo.com/video/123456789"></iframe>
    <blockquote class="twitter-tweet"><a href="https://x.com/foo/status/1234567890123456789">tweet</a></blockquote>
    <iframe src="https://www.tiktok.com/embed/v2/7123456789012345678"></iframe>
    <iframe src="https://www.facebook.com/plugins/post.php?href=https://www.facebook.com/foo/posts/1"></iframe>
    <p>See <a href="https://t.me/channel/12">this Telegram post</a>.</p>
    """
    clean, *_ = sanitize_feed_html(html)
    assert "<iframe" not in clean.lower()
    assert re.search(r"<p>https://vimeo.com/123456789</p>", clean)
    assert re.search(r"<p>https://twitter.com/foo/status/1234567890123456789</p>", clean)
    assert re.search(r"<p>https://www.tiktok.com/video/7123456789012345678</p>", clean)
    # Facebook / Telegram stay ordinary links, not bare oEmbed paragraphs.
    assert "<a href=" in clean and "facebook.com" in clean
    assert '<a href="https://t.me/channel/12">' in clean
    assert not re.search(r"<p>https://t\.me/channel/12</p>", clean)

def test_dedup_wordpress_sizes_and_forced_lead():
    html = '''
    <p>Text first</p>
    <figure><img src="https://infocity.kharkiv.ua/wp-content/uploads/2026/11/abc-1110x657.jpg" alt="crop"></figure>
    <img src="https://infocity.kharkiv.ua/wp-content/uploads/2026/11/abc.jpg" alt="full">
    '''
    lead = "https://infocity.kharkiv.ua/wp-content/uploads/2026/11/abc.jpg"
    clean, out_lead, images = sanitize_feed_html(html, lead_image=lead)
    assert out_lead == lead
    assert images[0] == lead
    assert images.count(lead) == 1
    assert "1110x657" not in clean
    # lead image is the first element
    assert clean.lstrip().startswith("<p><img")
    assert lead in clean[:200]

def test_image_stem():
    a = "https://x.com/wp-content/uploads/2026/11/abc-1110x657.jpg"
    b = "https://x.com/wp-content/uploads/2026/11/abc.jpg"
    assert image_stem(a) == image_stem(b)

if __name__ == "__main__":
    for fn, f in list(globals().items()):
        if fn.startswith("test_"):
            f()
            print("ok", fn)
    print("all passed")

def test_drops_trailing_automatic_og_when_lead_exists():
    lead = "https://cdn4.suspilne.media/images/resize/1040x0.1/905ba4a2b79b414d.jpg"
    og = "https://cdn4.suspilne.media/images/generated/automatic-og/v1/2f/2f5040bac674fec381b6c351ea49a5d589f310263e0ceb13fb064ae3cfbb78c8.jpg"
    html = f'<p>Text</p><p><img alt="" src="{og}"/></p>'
    clean, out_lead, images = sanitize_feed_html(html, lead_image=lead)
    assert out_lead == lead
    assert og not in clean
    assert all("automatic-og" not in u for u in images)
    assert lead in clean
    assert is_social_card_image(og)
    assert not is_social_card_image(lead)


def test_keeps_automatic_og_as_lead_when_it_is_the_only_photo():
    og = "https://cdn4.suspilne.media/images/generated/automatic-og/v1/2f/2f5040bac674fec381b6c351ea49a5d589f310263e0ceb13fb064ae3cfbb78c8.jpg"
    html = f'<p>No other photos.</p><p><img alt="" src="{og}"/></p>'
    clean, out_lead, images = sanitize_feed_html(html, lead_image="")
    assert out_lead == og
    assert og in clean
    assert images == [og]


def test_drops_already_mirrored_automatic_og_file():
    lead = "https://ukitfree-wq.github.io/kharkiv-news-feed/img/92f3ff56485ecbd9cd04.jpg"
    og = "https://ukitfree-wq.github.io/kharkiv-news-feed/img/16f4f187930859366531.jpg"
    html = f'<p>Text</p><p><img alt="" src="{og}"/></p>'
    clean, out_lead, images = sanitize_feed_html(html, lead_image=lead)
    assert out_lead == lead
    assert og not in clean
    assert all("16f4f187930859366531" not in u for u in images)
