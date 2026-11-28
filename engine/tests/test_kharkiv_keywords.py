"""Kharkiv title keywords and guards (hromadas, settlements, false-positive guards)."""
from __future__ import annotations

import pytest

from newsfeed.config import ROOT
from newsfeed.keywords import compile_keywords, load_title_guards, load_title_keywords, title_match

ODESA = ROOT / "regions" / "odesa"


@pytest.fixture(scope="module")
def kharkiv_kw():
    return compile_keywords(
        load_title_keywords(ROOT / "title_keywords.txt"),
        guards=load_title_guards(ROOT / "title_guards.txt"),
    )


@pytest.mark.parametrize("title", [
    "Росіяни атакували Лозівську громаду безпілотниками: є поранені",
    "У Лозівській громаді відновили світло",
    "Голова Лозівської громади звернувся до жителів",
    "Як підтримують людей, які залишаються у прифронтовому Слатиному",
    "Евакуація зі Слатиного триває",
    "Селище Слатине після обстрілу",
    "Вільхуватська громада: як працює підтримка дітей",
    "Дистанційні уроки у Дворічанській громаді",
    "На Куп'янщині відновили світло",
    "На Куп’янщині відновили світло",
    "Харків і область",
    "Обстріл Пісочина",
    "Старий Салтів після обстрілу",
    "У Великому Бурлуку немає газу",
    "Нова Водолага зустріла переселенців",
    "Козача Лопань під обстрілом",
    "Куп'янськ-Вузловий після удару",
    "У Куп'янську-Вузловому немає світла",
    "Берестинський район отримає допомогу",
    "Красноградський район отримає допомогу",
    "Миколаївка на Харківщині",
])
def test_kharkiv_keywords_match(kharkiv_kw, title):
    assert title_match(title, kharkiv_kw)


@pytest.mark.parametrize("title", [
    "Миколаївка: відкрили школу",
    "Іванівка Полтавської області",
    "Петрівка на Дніпропетровщині",
    "Олександрівка Донецької області",
    "Донецька громада евакуювала людей",
    "Обстріли Донецька і Покровська",
    "Південний фронт: Сили оборони відбили атаку",
    "Південна Корея передала допомогу",
    "Південного Бугу розлився",
    "Київський район Києва залишився без світла",
    "Шевченківський район Львова",
    "Індустріальний парк у Дніпрі",
    "Золочівський замок на Львівщині",
    "Біляївка на Одещині",
    "Слобожанська громада Дніпропетровщини",
    "Мирне небо над Україною",
    "Високе зростання цін",
    "Високий рівень небезпеки",
    "Вирішальний бій за місто",
    "На Зіньківщині негода",
    "Зіньківський район Полтавщини",
    "Новини України за добу",
    "У ЦЕК розповіли про роботу енергетиків Дніпропетровщини",
])
def test_kharkiv_guards_and_ambiguous_names(kharkiv_kw, title):
    assert not title_match(title, kharkiv_kw)


def test_odesa_keyword_files_untouched():
    raw = [l.strip() for l in (ODESA / "title_keywords.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(raw) == 527
    guards = (ODESA / "title_guards.txt").read_text(encoding="utf-8")
    assert "Exclusion guards for title keywords (Odesa region)" in guards
