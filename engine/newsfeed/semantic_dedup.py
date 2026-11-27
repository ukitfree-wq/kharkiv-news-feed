"""Semantic same-event duplicate check (feature-flagged: dedup.semantic.enabled).

Catches rewrites of the same story by different outlets, where word-shingle
MinHash and title fuzz fail because the wording differs.

Signals for a candidate pair (new item vs. an earlier kept story):
  * emb   - cosine of multilingual-e5-small embeddings (quantized ONNX, CPU)
            over "title. first ~800 chars of text";
  * kw    - TF-IDF cosine of stemmed content words over title + full text;
  * ent   - IDF-weighted overlap of distinctive names / acronyms / numbers
            (e.g. "830", "Терехов"), as containment in the smaller story;
  * vetoes: both stories name explicit calendar dates and they are disjoint
            (daily round-ups of different days), or publish times too far apart.

Decision (thresholds in config.yaml -> dedup.semantic), calibrated on state.db:
  vetoes: same source (an outlet's own follow-ups / daily series), disjoint explicit
          dates, or published more than max_hours_apart apart;
  duplicate if
     strong_embedding: emb >= emb_strong and kw >= kw_min_for_strong
  or combined:         emb >= emb_min and 0.40*norm(emb) + 0.35*kw + 0.25*ent >= score_threshold
  or anchor:           emb >= anchor_emb_min, kw >= anchor_kw_min, <= anchor_max_hours apart and
                       shared rare facts (df <= anchor_max_df): a number with >= 3 digits, or
                       >= 2 rare features incl. a number, or >= 2 rare names with kw >= 0.45.
  or incident:         short strike reports (attack word in both headlines, not daily
                       round-ups) <= incident_max_hours apart, emb >= incident_emb_min, the
                       same places (Jaccard >= 0.5), no conflicting specific weapon, target
                       type or clock time, and a shared weapon / target / time (see
                       incident_slots / incident_match).

The model is downloaded once from the Hugging Face hub and cached in
``model_cache_dir`` (cache that directory on CI).
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger("newsfeed.semantic")

MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
    # Russian forms, just in case a source writes in Russian.
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}

# Boilerplate lines that sources append to every article.
BOILERPLATE_RE = re.compile(
    r"^(читайте нас у|та дивіться наші відео|останні новини|підписуйтесь|читайте також|"
    r"facebook|telegram|instagram|youtube|youтube|tiktok|viber|twitter|x\.com|,|\.|та)\s*[.,]?\s*$",
    re.IGNORECASE,
)

# Capitalized tokens that carry no event identity (sources, platforms, generic).
ENTITY_STOP = {
    "інфос", "інфосіті", "facebook", "faceb", "teleg", "insta", "youtu", "youтu", "tikto", "viber",
    "твітт", "фейсб", "телег", "сайт", "фото", "відео", "оновл", "читай", "також", "крім",
    "проте", "однак", "тому", "також", "після", "наразі", "згідн", "окрім", "водно",
    "сьогод", "сього", "вчора", "зокре", "загал", "серед", "через", "також",
}

STOPWORDS = set(
    """і й та а але або чи що щоб як де коли який яка яке які якого якої яких цей ця це ці того тієї
    тих той ту те на в у з із зі до від по за під над про для при без між через біля після перед
    не ні так також вже ще лише тільки навіть було був була були буде будуть є бути має мають
    його її їх їм він вона воно вони ми ви ти я свій своя своє свої який також зокрема саме
    тому тоже то же ж б би бо адже хоча якщо поки""".split()
)

WORD_RE = re.compile(r"[^\W\d_][\w’'ʼ-]*", re.UNICODE)
NUM_RE = re.compile(r"(?<![\w.,])(\d{1,3}(?:[ \u00a0]\d{3})+|\d+(?:[.,]\d+)?)(?![\w])")
DATE_RE = re.compile(r"\b(\d{1,2})(?:-?го)?\s+(" + "|".join(MONTHS) + r")\b", re.IGNORECASE)
CAP_RE = re.compile(r"(?<![\w’'ʼ])([A-ZА-ЯІЇЄҐ][a-zа-яіїєґ’'ʼ]{2,}|[A-ZА-ЯІЇЄҐ]{2,6})(?![\w’'ʼ])")


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return text.replace("ʼ", "’").replace("'", "’")


# Everything after these markers is an unrelated teaser block appended by the source.
TAIL_MARKERS_RE = re.compile(r"^вибір редакції\b", re.IGNORECASE)


def clean_text(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if TAIL_MARKERS_RE.match(s):
            break
        if not s or BOILERPLATE_RE.match(s):
            continue
        lines.append(s)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Incident slots for short attack / shelling reports.
# Rewrites of one strike rarely share wording, but they share the same
# place + weapon / target / clock time within a short window. Different strikes
# on the same day differ in at least one of these, which acts as a veto.

ATTACK_RE = re.compile(
    r"(вдари|удар|атакув|атаку|атак[аиуі]|обстріл|обстрілю|прилет|приліт|прильот|влуча|влучи|влучан|поціли|вибух|уражен)",
    re.IGNORECASE,
)
SUMMARY_RE = re.compile(
    r"(за добу|протягом доби|минулої доби|за минулу добу|упродовж доби|за тиждень|за минулий тиждень|протягом дня)",
    re.IGNORECASE,
)
BACKGROUND_RE = re.compile(
    r"^(як писав|як повідомля|нагадаємо|раніше|нагадуємо|також раніше|до цього|перед цим|окрім того, раніше)",
    re.IGNORECASE,
)
# Weapon classes: specific ones conflict when disjoint; "drone" is generic.
WEAPONS = {
    "lancet": r"ланцет",
    "shahed": r"шахед|герань|герберa|гербер",
    "molniya": r"молні[яїюєй]",
    "banderol": r"бандерол",
    "fpv": r"\bfpv|\bфпв",
    "kab": r"\bкаб(?:и|ами|ом|ів)?\b|авіабомб|керован\w* авіа",
    "missile": r"ракет|іскандер|\bс-300|\bс-400|балістик",
    "mlrs": r"рсзв|\bград(?:ом|у|и)?\b|ураган|смерч",
    "artillery": r"артилер|мінометн|міномет",
}
GENERIC_WEAPON = "drone"
GENERIC_WEAPON_RE = re.compile(r"дрон|безпілот|бпла|бпак", re.IGNORECASE)
TARGETS = {
    "fuel": r"\bазс\b|заправ",
    "highrise": r"багатоповерх|багатоквартир|висотк",
    "house": r"приватн\w* (?:житлов\w* )?будин|приватн\w* сектор|приватн\w* садиб",
    "enterprise": r"підприєм|завод|склад",
    "energy": r"енергооб|підстанц|енергетичн\w* інфрастр|тец\b",
    "school": r"школ|ліце|навчальн\w* заклад|виш|університет|інститут",
    "vehicle": r"автомоб|автівк|маршрутк|автобус|\bавто\b|легковик",
    "market": r"ринок|ринку",
    "shop": r"магазин|супермаркет",
    "railway": r"залізни|потяг|поїзд|вокзал",
    "medical": r"лікарн|медичн\w* заклад|амбулатор",
}
WEAPON_RES = {k: re.compile(v, re.IGNORECASE) for k, v in WEAPONS.items()}
TARGET_RES = {k: re.compile(v, re.IGNORECASE) for k, v in TARGETS.items()}
CLOCK_RE = re.compile(
    r"(?:\bо|\bоб|\bблизько|\bприблизно|\bз|\bвід|\bпісля|\bу|\bв)\s+(\d{1,2})[:.](\d{2})(?!\d)",
    re.IGNORECASE,
)
# Kharkiv city districts and region towns (stems); extended by config incident_places.
DEFAULT_PLACES = [
    "шевченків", "київськ", "салтівськ", "немишлянськ", "індустріальн", "слобідськ",
    "основ’янськ", "холодногірськ", "новобаварськ",
    "лозов", "ізюм", "чугуїв", "чугуєв", "куп’янськ", "люботин", "златопол", "златопіл", "мереф",
    "балакл", "валк", "вовчанськ", "дергач", "зміїв", "красноград", "південн", "богодух", "барвінков",
    "золочів", "золочев", "солоницівк", "пісочин", "безлюдівк", "циркун", "малоданилівк",
]
VILLAGE_RE = re.compile(
    r"(?:\bсел[оіаи]|\bселищ[еіаи]|\bбіля|\bпоблизу|\bнеподалік|\bмісті|\bмісто)\s+([A-ZА-ЯІЇЄҐ][\w’'ʼ-]+)"
    r"|([A-ZА-ЯІЇЄҐ][\w’'ʼ-]+(?:ській|ського|ська|ську))\s+(?:громад|район)",
)


# Region lexicon for incident slots and entity stop-words. Defaults are the Kharkiv
# values above; a region config (dedup.semantic.incident_places, …) replaces or
# extends them via configure_region(), called by SemanticDeduper.
_REGION: dict = {}


def configure_region(s: dict | None = None) -> None:
    """Set the place lexicon, city prefix, extra weapon / target classes and
    entity stop-words from semantic settings. With no region keys this restores
    the Kharkiv defaults exactly."""
    s = s or {}
    weapons = dict(WEAPON_RES)
    for k, v in (s.get("incident_weapons_extra") or {}).items():
        weapons[str(k)] = re.compile(str(v), re.IGNORECASE)
    targets = dict(TARGET_RES)
    for k, v in (s.get("incident_targets_extra") or {}).items():
        targets[str(k)] = re.compile(str(v), re.IGNORECASE)
    _REGION.clear()
    _REGION.update(
        places=list(s.get("incident_places") or DEFAULT_PLACES),
        city_prefixes=tuple(str(x).casefold() for x in (s.get("incident_city_prefixes") or ["харк"])),
        weapons=weapons,
        targets=targets,
        ent_stop=frozenset(ENTITY_STOP) | frozenset(str(x).casefold() for x in (s.get("entity_stop_extra") or [])),
    )


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text or "") if p.strip()]


def incident_lead(title: str, text: str, chars: int = 700) -> str:
    """Title + the first sentences of the report, without background recaps."""
    keep = []
    total = 0
    for sent in _sentences(text):
        if BACKGROUND_RE.match(sent):
            # Recaps of earlier events come last; everything after is background too.
            break
        keep.append(sent)
        total += len(sent)
        if total >= chars:
            break
    return _norm(title) + ". " + _norm(" ".join(keep))


def incident_slots(title: str, text: str, places: list[str] | None = None) -> dict:
    lead = incident_lead(title, text)
    low = lead.casefold().replace("'", "’")
    if not _REGION:
        configure_region()
    weapons = {k for k, rx in _REGION["weapons"].items() if rx.search(low)}
    if GENERIC_WEAPON_RE.search(low):
        weapons.add(GENERIC_WEAPON)
    targets = {k for k, rx in _REGION["targets"].items() if rx.search(low)}
    plc = set()
    lexicon = places or _REGION["places"]
    for stem_ in lexicon:
        if re.search(r"\b" + re.escape(stem_), low):
            plc.add(stem_)
    for m in VILLAGE_RE.finditer(lead):
        word = (m.group(1) or m.group(2) or "").casefold().replace("'", "’")
        if not word or word.startswith(_REGION["city_prefixes"]):
            continue
        known = next((st for st in lexicon if word.startswith(st)), None)
        plc.add(known or word[:6])
    times = set()
    for m in CLOCK_RE.finditer(lead):
        h, mi = int(m.group(1)), int(m.group(2))
        if h < 24 and mi < 60:
            times.add(h * 60 + mi)
    return {
        # The headline itself must be about a strike (not an outage or digest story).
        "attack": bool(ATTACK_RE.search(_norm(title).casefold())),
        "summary": bool(SUMMARY_RE.search(low)),
        "weapons": weapons,
        "targets": targets,
        "places": plc,
        "times": times,
    }


def incident_match(a: dict, b: dict, s: dict) -> tuple[bool, str]:
    """Same strike? Returns (match, reason or veto)."""
    if not (a["attack"] and b["attack"]):
        return False, "not_attack"
    if a["summary"] or b["summary"]:
        return False, "summary"
    shared_places = a["places"] & b["places"]
    if not shared_places:
        return False, "no_shared_place"
    # The two reports must be about the same set of places (a multi-district round-up
    # is not the same story as one strike it mentions).
    if len(shared_places) / len(a["places"] | b["places"]) < float(s["incident_min_place_jaccard"]):
        return False, "place_mismatch"
    spec_a = a["weapons"] - {GENERIC_WEAPON}
    spec_b = b["weapons"] - {GENERIC_WEAPON}
    if spec_a and spec_b and not (spec_a & spec_b):
        return False, "weapon_conflict"
    if a["targets"] and b["targets"] and not (a["targets"] & b["targets"]):
        return False, "target_conflict"
    tol = float(s["incident_time_tolerance_min"])
    if a["times"] and b["times"]:
        gap = min(abs(x - y) for x in a["times"] for y in b["times"])
        if gap > tol:
            return False, "time_conflict"
        time_ok = True
    else:
        time_ok = False
    support = []
    if spec_a & spec_b:
        support.append("weapon")
    max_t = int(s["incident_max_targets"])
    if (a["targets"] & b["targets"]) and len(a["targets"]) <= max_t and len(b["targets"]) <= max_t:
        support.append("target")
    if time_ok:
        support.append("time")
    if not support:
        # Shared places alone are not enough: round-ups of a day's strikes share them too.
        return False, "no_support"
    if len(shared_places) >= 2:
        support.append("places")
    return True, "+".join(["place"] + support)


def stem(word: str) -> str:
    w = word.casefold().replace("’", "")
    return w[:5] if len(w) > 5 else w


def content_stems(text: str) -> list[str]:
    out = []
    for m in WORD_RE.finditer(_norm(text)):
        w = m.group(0).casefold()
        if len(w) < 4 or w in STOPWORDS:
            continue
        out.append(stem(w))
    return out


def extract_dates(text: str) -> set[tuple[int, int]]:
    out = set()
    for m in DATE_RE.finditer(_norm(text)):
        day = int(m.group(1))
        if 1 <= day <= 31:
            out.add((day, MONTHS[m.group(2).casefold()]))
    return out


def extract_entities(title: str, text: str) -> set[str]:
    """Distinctive features: names/acronyms (stemmed, not sentence-initial) and numbers."""
    feats: set[str] = set()
    body = _norm(title) + ".\n" + _norm(text)
    no_dates = DATE_RE.sub(" ", body)
    for m in NUM_RE.finditer(no_dates):
        raw = re.sub(r"[ \u00a0]", "", m.group(1)).replace(",", ".")
        if raw.endswith(".0"):
            raw = raw[:-2]
        # Single digits are too generic to identify an event.
        if len(raw) < 2:
            continue
        feats.add("#" + raw)
    for m in CAP_RE.finditer(no_dates):
        start = m.start()
        prefix = no_dates[max(0, start - 3):start]
        # Skip sentence-initial capitals (ordinary words).
        if start == 0 or re.search(r"[.!?…:\n«\"“—–-]\s*$", prefix):
            if not m.group(1).isupper():
                continue
        token = m.group(1)
        st = token.casefold() if token.isupper() else stem(token)
        if not _REGION:
            configure_region()
        if st in _REGION["ent_stop"] or token.casefold() in STOPWORDS:
            continue
        feats.add("@" + st)
    return feats


@dataclass
class Doc:
    key: object
    title: str
    text: str
    published_at: str | None
    source: str = ""
    stems: Counter = field(default_factory=Counter)
    ents: set = field(default_factory=set)
    dates: set = field(default_factory=set)
    slots: dict = field(default_factory=dict)
    emb: object = None


def make_doc(key, title: str, text: str, published_at: str | None, source: str = "") -> Doc:
    body = clean_text(text)
    doc = Doc(key=key, title=title or "", text=body, published_at=published_at, source=source or "")
    # Title weighted twice in the bag of words.
    doc.stems = Counter(content_stems(doc.title) * 2 + content_stems(body))
    doc.ents = extract_entities(doc.title, body)
    doc.dates = extract_dates(doc.title + "\n" + body)
    doc.slots = incident_slots(doc.title, body)
    return doc


class Idf:
    def __init__(self, docs: list[Doc]):
        self.n = max(1, len(docs))
        self.df_stem: Counter = Counter()
        self.df_ent: Counter = Counter()
        for d in docs:
            self.df_stem.update(set(d.stems))
            self.df_ent.update(d.ents)

    def stem_idf(self, s: str) -> float:
        return math.log((self.n + 1) / (self.df_stem.get(s, 0) + 1)) + 1.0

    def ent_idf(self, e: str) -> float:
        return math.log((self.n + 1) / (self.df_ent.get(e, 0) + 1)) + 1.0


def tfidf_cos(a: Doc, b: Doc, idf: Idf) -> float:
    if not a.stems or not b.stems:
        return 0.0
    va = {s: (1 + math.log(c)) * idf.stem_idf(s) for s, c in a.stems.items()}
    vb = {s: (1 + math.log(c)) * idf.stem_idf(s) for s, c in b.stems.items()}
    dot = sum(v * vb[s] for s, v in va.items() if s in vb)
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return dot / (na * nb) if na and nb else 0.0


def ent_overlap(a: Doc, b: Doc, idf: Idf, min_idf: float) -> tuple[float, list[str]]:
    """IDF-weighted containment of distinctive features in the smaller set."""
    fa = {e for e in a.ents if idf.ent_idf(e) >= min_idf}
    fb = {e for e in b.ents if idf.ent_idf(e) >= min_idf}
    if not fa or not fb:
        return 0.0, []
    shared = fa & fb
    wa = sum(idf.ent_idf(e) for e in fa)
    wb = sum(idf.ent_idf(e) for e in fb)
    ws = sum(idf.ent_idf(e) for e in shared)
    # Cap the denominator so a long feature list does not dilute a few strong hits.
    denom = min(wa, wb, 8 * min_idf * 2)
    return min(1.0, ws / denom) if denom else 0.0, sorted(shared, key=lambda e: -idf.ent_idf(e))


def anchors(a: Doc, b: Doc, idf: Idf, max_df: int) -> list[str]:
    """Shared rare features (df <= max_df): event-specific names and numbers.

    Years and long numbers (phones, hotlines, IDs) are ignored.
    """
    out = []
    for e in a.ents & b.ents:
        if idf.df_ent.get(e, 0) > max_df:
            continue
        if e.startswith("#"):
            v = e[1:]
            digits = v.replace(".", "")
            if re.fullmatch(r"(19|20)\d\d", v) or len(digits) >= 6:
                continue
        out.append(e)
    return sorted(out)


def _strong_numbers(anch: list[str]) -> list[str]:
    return [e for e in anch if e.startswith("#") and len(e[1:].replace(".", "")) >= 3]


def _hours_between(a: str | None, b: str | None) -> float | None:
    try:
        da = datetime.fromisoformat(str(a).replace("Z", "").replace("T", " ")[:19])
        db = datetime.fromisoformat(str(b).replace("Z", "").replace("T", " ")[:19])
    except Exception:
        return None
    return abs((da - db).total_seconds()) / 3600.0


DEFAULTS = {
    "enabled": False,
    "model_repo": "Xenova/multilingual-e5-small",
    "model_file": "onnx/model_quantized.onnx",
    "model_revision": "761b726dd34fb83930e26aab4e9ac3899aa1fa78",
    "model_cache_dir": "data/models",
    "text_chars": 800,
    "max_tokens": 256,
    "threads": 0,  # 0 = onnxruntime default (all cores)
    "lookback_hours": 48,
    "max_hours_apart": 20,
    "emb_min": 0.86,
    "emb_strong": 0.955,
    "kw_min_for_strong": 0.33,
    "score_threshold": 0.62,
    "w_emb": 0.40,
    "w_kw": 0.35,
    "w_ent": 0.25,
    "ent_min_idf": 2.0,
    "date_veto": True,
    # Rewrites by the same outlet are follow-ups/updates or daily series, not cross-source copies.
    "skip_same_source": True,
    # Anchor rule: moderate embedding + shared rare facts (numbers/names) within a short window.
    "anchor_emb_min": 0.86,
    "anchor_kw_min": 0.20,
    "anchor_max_hours": 12,
    "anchor_max_df": 3,
    "anchor_kw_for_names_only": 0.45,
    # Incident rule: short attack reports of the same strike (place + weapon/target/time).
    "incident_enabled": True,
    "incident_emb_min": 0.85,
    "incident_max_hours": 3,
    "incident_time_tolerance_min": 30,
    "incident_min_place_jaccard": 0.5,
    # A report listing more damaged target types than this is a round-up; its targets
    # do not count as evidence of the same strike.
    "incident_max_targets": 3,
}


def settings(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    out.update((cfg or {}))
    return out


class Embedder:
    """multilingual-e5-small (quantized ONNX) with mean pooling, lazily loaded."""

    def __init__(self, s: dict, root: str | os.PathLike | None = None):
        self.s = s
        self.root = str(root or ".")
        self._sess = None
        self._tok = None
        self.load_seconds = 0.0

    def _load(self):
        if self._sess is not None:
            return
        t0 = time.monotonic()
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        cache = self.s["model_cache_dir"]
        if not os.path.isabs(cache):
            cache = os.path.join(self.root, cache)
        kw = {"cache_dir": cache, "revision": self.s.get("model_revision") or None}
        try:
            mp = hf_hub_download(self.s["model_repo"], self.s["model_file"], **kw)
            tp = hf_hub_download(self.s["model_repo"], "tokenizer.json", **kw)
        except Exception:
            # Offline: fall back to whatever is cached.
            mp = hf_hub_download(self.s["model_repo"], self.s["model_file"], local_files_only=True, **kw)
            tp = hf_hub_download(self.s["model_repo"], "tokenizer.json", local_files_only=True, **kw)
        tok = Tokenizer.from_file(tp)
        tok.enable_truncation(int(self.s["max_tokens"]))
        tok.enable_padding()
        so = ort.SessionOptions()
        if int(self.s.get("threads") or 0) > 0:
            so.intra_op_num_threads = int(self.s["threads"])
        so.log_severity_level = 3
        self._sess = ort.InferenceSession(mp, so, providers=["CPUExecutionProvider"])
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._tok = tok
        self.load_seconds = time.monotonic() - t0

    def embed(self, texts: list[str], batch: int = 16):
        import numpy as np

        self._load()
        out = []
        for i in range(0, len(texts), batch):
            enc = self._tok.encode_batch(["query: " + t for t in texts[i : i + batch]])
            ids = np.array([e.ids for e in enc], dtype=np.int64)
            mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
            feed = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self._sess.run(None, feed)[0]
            v = (hidden * mask[..., None]).sum(1) / mask.sum(1, keepdims=True)
            v = v / np.linalg.norm(v, axis=1, keepdims=True)
            out.extend(v.astype("float32"))
        return out


def embed_input(doc: Doc, chars: int) -> str:
    return (doc.title + ". " + doc.text.replace("\n", " ")[:chars]).strip()


def score_pair(a: Doc, b: Doc, idf: Idf, s: dict) -> dict:
    emb = float((a.emb * b.emb).sum()) if a.emb is not None and b.emb is not None else 0.0
    kw = tfidf_cos(a, b, idf)
    ent, shared = ent_overlap(a, b, idf, float(s["ent_min_idf"]))
    hours = _hours_between(a.published_at, b.published_at)
    veto = ""
    if s.get("skip_same_source") and a.source and a.source == b.source:
        veto = "same_source"
    elif s.get("date_veto") and a.dates and b.dates and not (a.dates & b.dates):
        veto = "date_conflict"
    elif hours is not None and hours > float(s["max_hours_apart"]):
        veto = "too_far_apart"
    elif s.get("digest_title_re") and (
        re.search(s["digest_title_re"], _norm(a.title), re.I) or re.search(s["digest_title_re"], _norm(b.title), re.I)
    ):
        # Daily round-ups ("Одеса сьогодні, 27 листопада: …") overlap many single stories.
        veto = "digest"
    lo = float(s["emb_min"])
    emb_n = max(0.0, min(1.0, (emb - lo) / (1.0 - lo)))
    score = float(s["w_emb"]) * emb_n + float(s["w_kw"]) * kw + float(s["w_ent"]) * ent
    anch = anchors(a, b, idf, int(s["anchor_max_df"]))
    strong_nums = _strong_numbers(anch)
    num_anch = [e for e in anch if e.startswith("#")]
    rule = ""
    if not veto:
        if emb >= float(s["emb_strong"]) and kw >= float(s["kw_min_for_strong"]):
            rule = "strong_embedding"
        elif emb >= lo and score >= float(s["score_threshold"]):
            rule = "combined"
        elif (
            emb >= float(s["anchor_emb_min"])
            and kw >= float(s["anchor_kw_min"])
            and hours is not None
            and hours <= float(s["anchor_max_hours"])
            and (
                strong_nums
                or (len(anch) >= 2 and num_anch)
                or (len(anch) >= 2 and kw >= float(s["anchor_kw_for_names_only"]))
            )
        ):
            rule = "anchor"
        elif (
            s.get("incident_enabled")
            and emb >= float(s["incident_emb_min"])
            and hours is not None
            and hours <= float(s["incident_max_hours"])
        ):
            ok, why = incident_match(a.slots, b.slots, s)
            if ok:
                rule = "incident:" + why
        if not rule:
            rule = topic_match(a, b, hours, s)
    return {
        "emb": round(emb, 4),
        "kw": round(kw, 4),
        "ent": round(ent, 4),
        "score": round(score, 4),
        "shared": shared[:8],
        "anchors": anch[:8],
        "hours": None if hours is None else round(hours, 2),
        "veto": veto,
        "duplicate": bool(rule),
        "rule": rule,
    }


def topic_match(a: Doc, b: Doc, hours: float | None, s: dict) -> str:
    """Region option topic_rules: recurring service items (e.g. the daily weather
    forecast) from different sites for the same day are one story. Each rule:
    {name, title_re, max_hours}; both headlines must match title_re, and either
    name the same calendar date or (if one has no date) be <= max_hours apart."""
    for rule in s.get("topic_rules") or []:
        rx = rule.get("title_re")
        if not rx:
            continue
        ta, tb = _norm(a.title), _norm(b.title)
        if not (re.search(rx, ta, re.I) and re.search(rx, tb, re.I)):
            continue
        da, db = extract_dates(ta), extract_dates(tb)
        if da and db:
            if da & db:
                return f"topic:{rule.get('name', 'topic')}"
            continue
        if hours is not None and hours <= float(rule.get("max_hours") or 12):
            return f"topic:{rule.get('name', 'topic')}"
    return ""


class SemanticDeduper:
    """Holds earlier stories (with embeddings) and checks new items against them."""

    def __init__(self, cfg: dict | None, root=None):
        self.s = settings(cfg)
        configure_region(self.s)
        self.embedder = Embedder(self.s, root)
        self.docs: list[Doc] = []
        self.idf: Idf | None = None
        self.embed_seconds = 0.0

    def load_reference(self, rows: list[dict], idf_rows: list[dict] | None = None) -> None:
        """rows: dicts with id/title/content_text/published_at for the lookback window."""
        self.docs = [
            make_doc(r.get("id"), r.get("title") or "", r.get("content_text") or "", r.get("published_at"),
                     r.get("feed_id") or "")
            for r in rows
            if len(r.get("content_text") or "") >= 200
        ]
        idf_docs = self.docs
        if idf_rows:
            idf_docs = [make_doc(None, r.get("title") or "", r.get("content_text") or "", None) for r in idf_rows]
        self.idf = Idf(idf_docs)
        self._embed(self.docs)

    def _embed(self, docs: list[Doc]) -> None:
        todo = [d for d in docs if d.emb is None]
        if not todo:
            return
        t0 = time.monotonic()
        vecs = self.embedder.embed([embed_input(d, int(self.s["text_chars"])) for d in todo])
        for d, v in zip(todo, vecs):
            d.emb = v
        self.embed_seconds += time.monotonic() - t0

    def check(self, item: dict) -> tuple[object | None, dict | None]:
        """Return (matching doc key, scores) of the best duplicate match, or (None, None)."""
        text = item.get("content_text") or ""
        if len(text) < 200 or not self.docs:
            return None, None
        doc = make_doc(item.get("id"), item.get("title") or "", text, item.get("published_at"),
                       item.get("feed_id") or "")
        self._embed([doc])
        # Count the new story in the document frequencies while scoring it (as in calibration).
        self.idf.n += 1
        self.idf.df_stem.update(set(doc.stems))
        self.idf.df_ent.update(doc.ents)
        best = None
        try:
            for other in self.docs:
                sc = score_pair(doc, other, self.idf, self.s)
                if sc["duplicate"] and (best is None or sc["score"] > best[1]["score"]):
                    best = (other.key, sc)
        finally:
            self.idf.n -= 1
            self.idf.df_stem.subtract(set(doc.stems))
            self.idf.df_ent.subtract(doc.ents)
        return best if best else (None, None)

    def add(self, item: dict) -> None:
        text = item.get("content_text") or ""
        if len(text) < 200:
            return
        doc = make_doc(item.get("id"), item.get("title") or "", text, item.get("published_at"),
                       item.get("feed_id") or "")
        self._embed([doc])
        self.docs.append(doc)
        if self.idf is not None:
            # Keep document frequencies current so a story's own facts are not "rare" forever.
            self.idf.n += 1
            self.idf.df_stem.update(set(doc.stems))
            self.idf.df_ent.update(doc.ents)
