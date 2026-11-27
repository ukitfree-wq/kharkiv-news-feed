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


# --- incident rule (short attack reports of the same strike) ---------------

LANCET_A = ("27 листопада з 9:30 до 9:45 у Харкові пролунала серія вибухів. Один із прильотів припав "
            "на багатоповерхівку в Київському районі. У будівлю влучив російський безпілотник «Ланцет».")
LANCET_B = ("Зранку 27 листопада, близько 9:30, російські військові завдали удару по Київському району Харкова. "
            "Зафіксовано влучання в багатоквартирний будинок. Росіяни били дроном типу \"ланцет\". "
            "Як писав сайт, перед цим зранку атакували Шевченківський район, пошкоджена автозаправка.")
AZS = ("27 листопада о 7:45 росіяни атакували 2 райони Харкова: Шевченківський та Київський. "
       "У Шевченківському районі приліт був по автозаправній станції.")
ROUNDUP = ("Внаслідок обстрілів Харкова загинули четверо людей. Удари були по Новобаварському, Слобідському "
           "та Індустріальному районах, пошкоджено багатоповерхівку, АЗС і школу.")
ONE_STRIKE = "Вранці безпілотник влучив у багатоповерхівку в Новобаварському районі Харкова, загинули двоє."


def _slots(title, text):
    return sd.incident_slots(title, sd.clean_text(text))


def test_incident_same_strike_matches():
    a = _slots("Росіяни вдарили «Ланцетом» по багатоповерхівці в Харкові", LANCET_A)
    b = _slots("Російська армія атакувала будинок у Харкові \"ланцетом\"", LANCET_B)
    assert "lancet" in a["weapons"] and "lancet" in b["weapons"]
    # The background recap (AZS strike) must not leak into the slots.
    assert "fuel" not in b["targets"] and "шевченків" not in b["places"]
    ok, why = sd.incident_match(a, b, sd.settings({}))
    assert ok and "time" in why and "weapon" in why


def test_incident_different_strikes_same_day_do_not_match():
    s = sd.settings({})
    lancet = _slots("Росіяни вдарили «Ланцетом» по багатоповерхівці в Харкові", LANCET_A)
    azs = _slots("Вранці росіяни вдарили по АЗС у Харкові", AZS)
    ok, why = sd.incident_match(lancet, azs, s)
    assert not ok and why in {"target_conflict", "time_conflict", "place_mismatch"}


def test_incident_roundup_vs_single_strike_and_non_attack_title():
    s = sd.settings({})
    one = _slots("Російський дрон влучив у багатоповерхівку", ONE_STRIKE)
    roundup = _slots("Через обстріли загинули четверо людей", ROUNDUP)
    assert sd.incident_match(one, roundup, s) == (False, "place_mismatch")
    outage = _slots("Частина Харкова залишилась без світла", ONE_STRIKE)
    assert sd.incident_match(one, outage, s) == (False, "not_attack")


def test_incident_rule_in_score_pair_respects_window():
    a = _doc(1, "Росіяни вдарили «Ланцетом» по багатоповерхівці в Харкові", LANCET_A * 2,
             "2026-11-27 07:59:00", "status-quo", [1, 0])
    b = _doc(2, "Російська армія атакувала будинок у Харкові \"ланцетом\"", LANCET_B * 2,
             "2026-11-27 08:00:00", "2day", _emb_with_cos(0.89))
    # Tiny test corpus makes every shared word "rare"; switch the anchor rule off here.
    s = sd.settings({"anchor_emb_min": 0.99})
    sc = sd.score_pair(b, a, _idf(a, b), s)
    assert sc["duplicate"] and sc["rule"].startswith("incident:")
    b.published_at = "2026-11-27 12:00:00"  # 4h later: outside incident_max_hours
    assert not sd.score_pair(b, a, _idf(a, b), s)["rule"].startswith("incident")
