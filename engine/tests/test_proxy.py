"""048.ua relay: only mounted when both env vars are set; key only in a header."""
from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter

from newsfeed import proxy


def test_not_installed_without_env(monkeypatch):
    monkeypatch.delenv("PROXY_048_URL", raising=False)
    monkeypatch.setenv("PROXY_048_KEY", "k")
    s = proxy.install(requests.Session())
    assert not isinstance(s.get_adapter("https://www.048.ua/rss"), proxy.RelayAdapter)


def test_rewrites_only_048(monkeypatch):
    monkeypatch.setenv("PROXY_048_URL", "https://relay.example/feedproxy-048.php")
    monkeypatch.setenv("PROXY_048_KEY", "secret-key")
    s = proxy.install(requests.Session())
    assert isinstance(s.get_adapter("https://048.ua/news/1/x"), proxy.RelayAdapter)
    assert isinstance(s.get_adapter("https://www.048.ua/api3/news/1"), proxy.RelayAdapter)
    assert not isinstance(s.get_adapter("https://s.048.ua/img.jpg"), proxy.RelayAdapter)
    assert not isinstance(s.get_adapter("https://evil048.ua/"), proxy.RelayAdapter)
    assert not isinstance(s.get_adapter("https://suspilne.media/rss/all.rss"), proxy.RelayAdapter)

    seen = {}

    def fake_send(self, request, **kw):
        seen["url"] = request.url
        seen["key"] = request.headers.get("X-Proxy-Key")
        r = requests.Response()
        r.status_code = 200
        r.headers["X-Final-URL"] = "https://www.048.ua/news/1/x"
        r._content = b"ok"
        r.url = request.url
        return r

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    resp = s.get("https://048.ua/news/1/x?a=1")
    assert seen["url"] == "https://relay.example/feedproxy-048.php?url=https%3A%2F%2F048.ua%2Fnews%2F1%2Fx%3Fa%3D1"
    assert seen["key"] == "secret-key"
    assert resp.url == "https://www.048.ua/news/1/x"
    assert "secret-key" not in repr(s.get_adapter("https://048.ua/"))
