"""Whole-word title matching against an editable keyword list.

Rules:
- case-insensitive via casefold
- apostrophes ' ’ ʼ ` (and similar) are treated as the same character
- a keyword must appear as a whole word: bounded by non-letter characters
  (Unicode-aware), so "Валки" does not match inside another word, while
  "Харків" matches the word "Харків" but not "Харківський".
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Common apostrophe / typographic variants
_APOSTROPHES = dict.fromkeys(
    map(ord, "'’ʼ`´ʹʻʾʿˈ՚＇′ꞌ"),
    "'",
)

# Unicode letters: \w minus digits and underscore
_LETTER = r"[^\W\d_]"


def normalize_match_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.translate(_APOSTROPHES)
    return text.casefold()


def load_title_keywords(path: str | Path) -> list[str]:
    raw = Path(path).read_text(encoding="utf-8")
    seen: set[str] = set()
    keywords: list[str] = []
    for line in raw.splitlines():
        word = line.strip()
        if not word or word.startswith("#"):
            continue
        key = normalize_match_text(word)
        if key and key not in seen:
            seen.add(key)
            keywords.append(word)
    return keywords


def _word_pattern(keyword: str) -> re.Pattern[str]:
    norm = normalize_match_text(keyword)
    return re.compile(
        rf"(?<!{_LETTER}){re.escape(norm)}(?!{_LETTER})",
        flags=re.UNICODE,
    )


def compile_keywords(keywords: list[str]) -> list[tuple[str, re.Pattern[str]]]:
    return [(kw, _word_pattern(kw)) for kw in keywords]


def title_match(title: str, compiled: list[tuple[str, re.Pattern[str]]]) -> str | None:
    """Return the first keyword that matches the title, or None."""
    if not title or not compiled:
        return None
    norm = normalize_match_text(title)
    for keyword, pattern in compiled:
        if pattern.search(norm):
            return keyword
    return None


def title_words(title: str) -> list[str]:
    norm = normalize_match_text(title)
    return re.findall(rf"{_LETTER}+(?:'{_LETTER}+)*", norm, flags=re.UNICODE)


def near_misses(title: str, compiled: list[tuple[str, re.Pattern[str]]], min_stem: int = 5) -> list[str]:
    """Words in the title that look like a declined form of a keyword but did not match.

    Used only for reporting missing grammatical forms (e.g. Харківщини vs Харківщина).
    """
    if title_match(title, compiled):
        return []
    words = title_words(title)
    norms = [(kw, normalize_match_text(kw)) for kw, _ in compiled]
    found: list[str] = []
    seen: set[str] = set()
    for word in words:
        if len(word) < min_stem:
            continue
        best = None
        best_score = 0
        for kw, nkw in norms:
            if word == nkw:
                continue
            score = 0
            if len(nkw) >= min_stem and (word.startswith(nkw) or nkw.startswith(word)):
                score = min(len(word), len(nkw))
            else:
                stem = min(len(word), len(nkw), 8)
                if stem >= min_stem and word[:stem] == nkw[:stem]:
                    score = stem
            if score > best_score:
                best = kw
                best_score = score
        if best:
            key = f"{word} ~ {best}"
            if key not in seen:
                seen.add(key)
                found.append(key)
    return found
