"""Region (Odesa) options: keyword guards, site overrides, digest/topic dedup rules."""
from __future__ import annotations

import pytest

from newsfeed import semantic_dedup as sd
from newsfeed.config import ROOT, load_config
from newsfeed.keywords import compile_keywords, load_title_guards, load_title_keywords, title_match

ODESA = ROOT / "regions" / "odesa"


@pytest.fixture(scope="module")
def odesa_kw():
    return compile_keywords(load_title_keywords(ODESA / "title_keywords.txt"),
                            guards=load_title_guards(ODESA / "title_guards.txt"))


@pytest.mark.parametrize("title", [
    "В Одесі вандали пошкодили укриття",
    "На Одещині оголосили штормове попередження",
    "У Чорноморську відремонтують покрівлі",
    "Порт Південний прийняв перше судно",
    "У Подільському районі Одещини відкрили ЦНАП",
])
def test_odesa_keywords_match(odesa_kw, title):
    assert title_match(title, odesa_kw)


@pytest.mark.parametrize("title", [
    "Удар по Подільському району Києва: є загиблі",
    "У Кам'янці-Подільському відкрили міст",
    "Південна Корея передасть Україні обладнання",
    "Ситуація на Південному напрямку: ворог штурмує",
    "Кораблі Чорноморського флоту рф вийшли в море",
])
def test_odesa_guards_block(odesa_kw, title):
    assert not title_match(title, odesa_kw)


def test_user_keywords_all_kept():
    raw = [l.strip() for l in (ODESA / "title_keywords.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(raw) == 527


def test_missing_region_config_fails(monkeypatch):
    monkeypatch.setenv("NEWSFEED_CONFIG", "regions/nope/config.yaml")
    with pytest.raises(FileNotFoundError):
        load_config()


def _doc(i, title, when, feed):
    return sd.make_doc(i, title, title + ". " + "текст " * 50, when, feed)


@pytest.fixture
def odesa_region(monkeypatch):
    monkeypatch.setenv("NEWSFEED_CONFIG", "regions/odesa/config.yaml")
    s = sd.settings(load_config()["dedup"]["semantic"])
    sd.configure_region(s)
    yield s
    sd.configure_region(sd.settings({}))  # back to the Kharkiv lexicon for other tests


def test_topic_and_digest_rules(odesa_region):
    s = odesa_region
    a = _doc(1, "Погода в Одесі 27 листопада: дощі", "2026-11-27 06:00:00", "048")
    b = _doc(2, "Яка погода буде в Одесі 27 листопада 2026 року", "2026-11-27 08:00:00", "pushkinska")
    c = _doc(3, "Погода в Одесі 28 листопада: сонце", "2026-11-27 20:00:00", "grad")
    assert sd.topic_match(a, b, 2.0, s) == "topic:weather"
    assert sd.topic_match(a, c, 14.0, s) == ""
    d = _doc(4, "Одеса сьогодні, 27 листопада: графіки світла", "2026-11-27 05:00:00", "pushkinska")
    assert sd.score_pair(d, a, sd.Idf([a, d]), s)["veto"] == "digest"
    # Kharkiv settings have neither option.
    k = sd.settings(load_config(ROOT / "config.yaml")["dedup"]["semantic"])
    assert not k.get("topic_rules") and not k.get("digest_title_re")
