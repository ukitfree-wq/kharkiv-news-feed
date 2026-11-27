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


CHALLENGE = (b'<!DOCTYPE html><html><head><title>One moment, please...</title></head>'
             b"<body><script>var P='wsidchk';</script></body></html>")


def test_challenge_falls_back_to_direct(monkeypatch, caplog):
    monkeypatch.setenv("PROXY_048_URL", "https://relay.example/feedproxy-048.php")
    monkeypatch.setenv("PROXY_048_KEY", "secret-key")
    monkeypatch.setattr(proxy.RelayAdapter, "disabled_reason", None)
    s = proxy.install(requests.Session())
    calls = []

    def fake_send(self, request, **kw):
        calls.append((request.url, request.headers.get("X-Proxy-Key")))
        r = requests.Response()
        r.headers["Content-Type"] = "text/html"
        if request.url.startswith("https://relay.example/"):
            r.status_code, r._content = 200, CHALLENGE
        else:
            r.status_code, r._content = 403, b"blocked"
        r.url = request.url
        return r

    monkeypatch.setattr(HTTPAdapter, "send", fake_send)
    with caplog.at_level("WARNING"):
        r1 = s.get("https://www.048.ua/rss")
        r2 = s.get("https://048.ua/news/1/x")
    assert r1.status_code == 403 and r1.url == "https://www.048.ua/rss"
    # first: relay then direct; second: direct only (relay disabled for the run)
    assert [u.split("?")[0] for u, _ in calls] == [
        "https://relay.example/feedproxy-048.php", "https://www.048.ua/rss", "https://048.ua/news/1/x"]
    assert calls[1][1] is None and calls[2][1] is None
    msgs = [r.getMessage() for r in caplog.records if "relay unusable" in r.getMessage()]
    assert len(msgs) == 1 and "anti-bot challenge" in msgs[0] and "secret-key" not in msgs[0]
    assert r2.status_code == 403
