"""Timezone-aware publish-date parsing and freshness checks.

Sites are in Europe/Kiev. Datetimes with an explicit offset or Z are honored;
naive values are interpreted as Europe/Kiev. A stamp a few minutes in the
future is treated as fresh (clock skew); a far-future or date-only stamp is
considered unreliable so the page date can be used instead.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from zoneinfo import ZoneInfo

from dateutil import parser as dateparser

try:
    DEFAULT_TZ = ZoneInfo("Europe/Kyiv")
except Exception:
    DEFAULT_TZ = ZoneInfo("Europe/Kiev")

# date-only, or midnight with no meaningful time component in the original string
_DATE_ONLY_RE = re.compile(
    r"^\s*\d{4}[-/.]\d{1,2}[-/.]\d{1,2}\s*$"
    r"|^\s*\d{1,2}[-/.]\d{1,2}[-/.]\d{4}\s*$"
)
_HAS_TIME_RE = re.compile(r"\d{1,2}:\d{2}")
_HAS_TZ_RE = re.compile(
    r"(Z|[+-]\d{2}:?\d{2})\s*$"
    r"|GMT|UTC|EST|EDT|CST|CDT|MST|MDT|PST|PDT"
    r"|[A-Z]{3,5}",
    re.I,
)


def default_zone(name: str | None = None) -> ZoneInfo:
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return DEFAULT_TZ


def parse_datetime(value: Any, default_tz: ZoneInfo | None = None) -> datetime | None:
    """Parse a date/time to an aware UTC datetime, or None if unusable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=default_tz or DEFAULT_TZ)
        return dt.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    tz = default_tz or DEFAULT_TZ
    dt: datetime | None = None
    try:
        dt = parsedate_to_datetime(text)
    except Exception:
        dt = None
    if dt is None:
        try:
            dt = dateparser.parse(text, fuzzy=False)
        except Exception:
            return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def is_date_only(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    if _DATE_ONLY_RE.match(text):
        return True
    if not _HAS_TIME_RE.search(text):
        # e.g. "26 Nov 2026" without a time
        if re.search(r"\d{4}", text) and re.search(r"[A-Za-zА-Яа-я]{3,}|\d{1,2}[-/.]\d{1,2}", text):
            return True
    return False


def classify_feed_date(
    value: str | None,
    now: datetime,
    *,
    max_age_minutes: int = 30,
    future_grace_minutes: int = 5,
    default_tz: ZoneInfo | None = None,
) -> dict:
    """Describe a feed pubDate/updated value.

    status is one of: fresh, too_old, unreliable, missing
    """
    raw = (value or "").strip()
    if not raw:
        return {"status": "missing", "dt": None, "reason": "missing", "raw": raw}
    if is_date_only(raw):
        return {"status": "unreliable", "dt": parse_datetime(raw, default_tz), "reason": "date_only", "raw": raw}
    dt = parse_datetime(raw, default_tz)
    if dt is None:
        return {"status": "unreliable", "dt": None, "reason": "unparseable", "raw": raw}
    return {**_freshness(dt, now, max_age_minutes, future_grace_minutes), "raw": raw}


def _freshness(
    dt: datetime,
    now: datetime,
    max_age_minutes: int,
    future_grace_minutes: int,
) -> dict:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if dt > now + timedelta(minutes=int(future_grace_minutes)):
        return {"status": "unreliable", "dt": dt, "reason": "far_future"}
    if dt < now - timedelta(minutes=int(max_age_minutes)):
        return {"status": "too_old", "dt": dt, "reason": "too_old"}
    return {"status": "fresh", "dt": dt, "reason": "fresh"}


def freshness_of(
    dt: datetime | None,
    now: datetime,
    *,
    max_age_minutes: int = 30,
    future_grace_minutes: int = 5,
) -> dict:
    if dt is None:
        return {"status": "missing", "dt": None, "reason": "missing"}
    return _freshness(dt, now, max_age_minutes, future_grace_minutes)


def to_utc_naive(dt: datetime | None) -> str | None:
    """SQLite-friendly 'YYYY-MM-DD HH:MM:SS' in UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def format_kiev(dt: datetime | None | str, tz: ZoneInfo | None = None) -> str:
    """Format a UTC datetime/string in Europe/Kiev for reports."""
    zone = tz or DEFAULT_TZ
    if dt is None or dt == "":
        return ""
    if isinstance(dt, str):
        parsed = parse_datetime(dt, default_tz=timezone.utc)
        if parsed is None:
            return dt
        dt = parsed
    if dt.tzinfo is None:
        # Stored timestamps are UTC naive.
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(zone)
    offset = local.strftime("%z")
    offset_fmt = f"{offset[:3]}:{offset[3:]}" if offset else ""
    return f"{local.strftime('%Y-%m-%d %H:%M:%S')} Europe/Kiev (UTC{offset_fmt})"
