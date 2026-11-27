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
