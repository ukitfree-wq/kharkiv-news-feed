from pathlib import Path

from newsfeed.image_mirror import ImageMirror


class _Resp:
    def __init__(self, status, ctype, data=b"img"):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self._data = data

    def iter_content(self, n):
        yield self._data


def _mirror(tmp_path: Path, replies):
    m = ImageMirror(
        {"hosts": ["cdn4.suspilne.media", "od-news.com"], "public_dir": "pub", "base_url": "https://x.github.io/f/img/"},
        tmp_path, tmp_path / "stage", "UA",
    )
    calls = []

    def get(url, headers=None, timeout=None, stream=None):
        calls.append((url, headers))
        return replies[url]

    m.session.get = get
    return m, calls


def test_rewrites_only_listed_hosts(tmp_path):
    a = "https://cdn4.suspilne.media/images/resize/1040x1.78/abc.jpg"
    m, calls = _mirror(tmp_path, {a: _Resp(200, "image/jpeg", b"JPEG")})
    item = {
        "canonical_url": "https://suspilne.media/odesa/1-x/",
        "content_html": f'<img src="{a}" srcset="{a} 1x" alt="A"/><p>t</p><img src="https://grad.ua/i.jpg"/>',
        "lead_image": a,
        "images": [a, "https://grad.ua/i.jpg"],
    }
    m.apply(item)
    new = item["lead_image"]
    assert new.startswith("https://x.github.io/f/img/") and new.endswith(".jpg")
    assert f'<img alt="A" src="{new}"/>' in item["content_html"] or f'<img src="{new}" alt="A"/>' in item["content_html"]
    assert "https://grad.ua/i.jpg" in item["content_html"]
    assert item["images"] == [new, "https://grad.ua/i.jpg"]
    assert len(calls) == 1 and calls[0][1] == {"Referer": "https://suspilne.media/odesa/1-x/"}
    assert (tmp_path / "stage" / new.rsplit("/", 1)[1]).read_bytes() == b"JPEG"


def test_failed_download_keeps_original(tmp_path):
    a = "https://od-news.com/wp-content/uploads/2026/11/a.jpeg"
    m, _ = _mirror(tmp_path, {a: _Resp(403, "text/html")})
    item = {"canonical_url": "https://od-news.com/x/", "content_html": f'<img src="{a}"/>', "lead_image": a, "images": [a]}
    m.apply(item)
    assert item["lead_image"] == a and item["images"] == [a] and a in item["content_html"]


def test_finalize_keeps_only_referenced_and_reuses_published(tmp_path):
    a = "https://cdn4.suspilne.media/images/a.jpg"
    m, calls = _mirror(tmp_path, {})
    stem = m._name_stem(a)
    (tmp_path / "pub").mkdir()
    (tmp_path / "pub" / f"{stem}.jpg").write_bytes(b"OLD")
    (tmp_path / "pub" / ("f" * 20 + ".jpg")).write_bytes(b"GONE")
    url = m.mirror_url(a)
    assert calls == [] and url.endswith(f"{stem}.jpg")
    m.finalize([{"content_html": f'<img src="{url}"/>', "lead_image": url, "images_json": "[]"}])
    assert sorted(p.name for p in (tmp_path / "stage").iterdir()) == [f"{stem}.jpg"]
    assert (tmp_path / "stage" / f"{stem}.jpg").read_bytes() == b"OLD"


def test_objectiv_lead_and_body_images_are_copied(tmp_path):
    lead = "https://www.objectiv.tv/wp-content/uploads/2026/11/lead.webp"
    body = "https://www.objectiv.tv/wp-content/uploads/2026/11/inside.jpg"
    apex = "https://objectiv.tv/wp-content/uploads/2026/11/apex.jpg"
    m, calls = _mirror(
        tmp_path,
        {
            lead: _Resp(200, "image/webp", b"WEBP"),
            body: _Resp(200, "image/jpeg", b"JPEG"),
            apex: _Resp(200, "image/jpeg", b"APEX"),
        },
    )
    m.hosts = {"objectiv.tv", "www.objectiv.tv"}
    item = {
        "canonical_url": "https://www.objectiv.tv/uk/story/",
        "content_html": f'<img src="{lead}" alt="Lead"/><p>text</p><img src="{body}" alt="Inside"/>',
        "lead_image": lead,
        "images": [lead, body, apex],
    }
    m.apply(item)
    new_lead = item["lead_image"]
    assert new_lead.startswith("https://x.github.io/f/img/") and new_lead.endswith(".webp")
    assert f'src="{new_lead}"' in item["content_html"]
    assert "www.objectiv.tv/wp-content" not in item["content_html"]
    assert all(u.startswith("https://x.github.io/f/img/") for u in item["images"])
    # first body <img> is the copied lead
    from bs4 import BeautifulSoup
    first = BeautifulSoup(item["content_html"], "lxml").find("img")
    assert first is not None and first.get("src") == new_lead
    assert {u for u, _ in calls} == {lead, body, apex}


def test_kharkiv_config_lists_objectiv_odesa_does_not():
    from newsfeed.config import ROOT, load_config
    kh = load_config(ROOT / "config.yaml")
    hosts = [h.lower() for h in (kh.get("image_mirror") or {}).get("hosts") or []]
    assert "objectiv.tv" in hosts and "www.objectiv.tv" in hosts
    odesa = load_config(ROOT / "regions" / "odesa" / "config.yaml")
    oh = [h.lower() for h in (odesa.get("image_mirror") or {}).get("hosts") or []]
    assert "objectiv.tv" not in oh and "www.objectiv.tv" not in oh
