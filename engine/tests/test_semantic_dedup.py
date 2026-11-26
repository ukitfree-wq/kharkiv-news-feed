"""Rule tests for the semantic duplicate check (no model needed: embeddings are injected)."""

import numpy as np

from newsfeed import semantic_dedup as sd


def _doc(key, title, text, pub, src, emb):
    d = sd.make_doc(key, title, text, pub, src)
    v = np.array(emb, dtype="float32")
    d.emb = v / np.linalg.norm(v)
    return d


def _emb_with_cos(c):
    return [c, (1 - c * c) ** 0.5]


BASE = ("Проєкт бюджету передбачає 830 млн грн на підтримку військових частин ЗСУ. "
        "Міський голова Ігор Терехов подякував військовим під час громадських слухань щодо бюджету. ") * 3
OTHER = ("Наступного року Харків планує виділити на Сили Оборони 830 мільйонів гривень. "
         "Громадські слухання щодо бюджету та Статуту провів Ігор Терехов, військові дякували. ") * 3
FILLER = [("Новина %d про погоду у Сумах, мороз і сніг, синоптик Петренко%d" % (i, i)) * 5 for i in range(20)]


def _idf(*docs):
    extra = [sd.make_doc(None, "", t, None) for t in FILLER]
    return sd.Idf(list(docs) + extra)


def test_anchor_rule_catches_shared_rare_number():
    a = _doc(1, "Харків планує спрямувати 830 млн грн на підтримку військових", BASE, "2026-11-26 15:24:00", "infocity", [1, 0])
    b = _doc(2, "Перші громадські слухання у Харкові", OTHER, "2026-11-26 15:44:00", "2day", _emb_with_cos(0.88))
    sc = sd.score_pair(b, a, _idf(a, b), sd.settings({}))
    assert "#830" in sc["anchors"]
    assert sc["duplicate"] and sc["rule"] == "anchor"


def test_same_source_and_date_vetoes():
    a = _doc(1, "Генштаб: за добу три штурми", "За 24 листопада відбили три штурми біля Охрімівки. " * 5,
             "2026-11-25 07:30:00", "x", [1, 0])
    b = _doc(2, "Генштаб: за добу два штурми", "За 25 листопада відбили два штурми біля Охрімівки. " * 5,
             "2026-11-26 07:30:00", "y", _emb_with_cos(0.99))
    s = sd.settings({})
    sc = sd.score_pair(b, a, _idf(a, b), s)
    assert not sc["duplicate"] and sc["veto"] == "date_conflict"
    b.source = "x"
    assert sd.score_pair(b, a, _idf(a, b), s)["veto"] == "same_source"


def test_far_apart_and_low_embedding_not_duplicate():
    a = _doc(1, "A", BASE, "2026-11-25 07:30:00", "x", [1, 0])
    b = _doc(2, "B", OTHER, "2026-11-26 07:30:00", "y", _emb_with_cos(0.99))
    assert sd.score_pair(b, a, _idf(a, b), sd.settings({}))["veto"] == "too_far_apart"
    b.published_at = "2026-11-25 08:00:00"
    b.emb = np.array(_emb_with_cos(0.80), dtype="float32")
    assert not sd.score_pair(b, a, _idf(a, b), sd.settings({}))["duplicate"]


def test_entities_skip_years_phones_and_dates():
    ents = sd.extract_entities("Тест", "26 листопада 2027 року телефон 0800500335, сума 830 млн, Ігор Терехов сказав.")
    assert "#830" in ents and "@терех" in ents
    assert "#26" not in ents
    assert sd.extract_dates("26 листопада і 3 грудня") == {(26, 11), (3, 12)}
