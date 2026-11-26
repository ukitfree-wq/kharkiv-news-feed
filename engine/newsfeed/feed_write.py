"""Write a valid RSS 2.0 feed with content: and media: namespaces."""

from __future__ import annotations

import html
import json
import logging
import shutil
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from dateutil import parser as dateparser

from newsfeed.extract import guess_image_type
from newsfeed.feed_html import sanitize_feed_html

log = logging.getLogger(__name__)

CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
MEDIA_NS = "http://search.yahoo.com/mrss/"
DC_NS = "http://purl.org/dc/elements/1.1/"
ATOM_NS = "http://www.w3.org/2005/Atom"


def _rfc822(value: str | None) -> str:
    if value:
        try:
            dt = dateparser.parse(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return format_datetime(dt.astimezone(timezone.utc))
        except Exception:
            pass
    return format_datetime(datetime.now(timezone.utc))


def _text(value: str | None) -> str:
    return xml_escape(value or "", {"'": "&apos;", '"': "&quot;"})


def _images_of(row: Any) -> tuple[str, list[str]]:
    lead = (row["lead_image"] if "lead_image" in row.keys() else "") or ""
    raw = row["images_json"] if "images_json" in row.keys() else "[]"
    try:
        images = json.loads(raw or "[]")
    except json.JSONDecodeError:
        images = []
    if not isinstance(images, list):
        images = []
    images = [u for u in images if isinstance(u, str) and u]
    if lead and lead not in images:
        images = [lead] + images
    elif not lead and images:
        lead = images[0]
    return lead, images


def _plain_description(row: Any) -> str:
    text = (row["content_text"] if "content_text" in row.keys() else "") or ""
    if not text:
        html_src = (row["content_html"] if "content_html" in row.keys() else "") or ""
        text = re_strip_tags(html_src)
    text = " ".join(text.split())
    if len(text) > 400:
        text = text[:397].rstrip() + "…"
    status = row["extraction_status"] if "extraction_status" in row.keys() else ""
    if status and status != "ok":
        flag = f"[{status}] "
        text = flag + text
    return text


def re_strip_tags(value: str) -> str:
    import re
    from bs4 import BeautifulSoup

    if not value:
        return ""
    try:
        return BeautifulSoup(value, "lxml").get_text(" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", value)


def render_feed(channel: dict[str, str], items: list[Any], self_link: str | None = None, ttl_minutes: int = 5) -> str:
    now = format_datetime(datetime.now(timezone.utc))
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"',
        f'    xmlns:content="{CONTENT_NS}"',
        f'    xmlns:media="{MEDIA_NS}"',
        f'    xmlns:dc="{DC_NS}"',
        f'    xmlns:atom="{ATOM_NS}">',
        "<channel>",
        f"<title>{_text(channel.get('title'))}</title>",
        f"<link>{_text(channel.get('link'))}</link>",
        f"<description>{_text(channel.get('description'))}</description>",
        f"<language>{_text(channel.get('language') or 'pl')}</language>",
        f"<lastBuildDate>{now}</lastBuildDate>",
        f"<ttl>{int(ttl_minutes)}</ttl>",
        "<generator>pl-news-feed/1.0</generator>",
    ]
    if self_link:
        lines.append(f'<atom:link href="{_text(self_link)}" rel="self" type="application/rss+xml"/>')

    for row in items:
        title = row["title"] or ""
        link = row["canonical_url"] or ""
        publisher = row["publisher"] or ""
        category = row["category"] or ""
        status = row["extraction_status"] or ""
        pub = _rfc822(row["published_at"] if "published_at" in row.keys() else None)
        lead, images = _images_of(row)
        content_html = (row["content_html"] if "content_html" in row.keys() else "") or ""
        content_html, lead, images = sanitize_feed_html(content_html, base_url=link, lead_image=lead)
        if not content_html:
            fallback = _plain_description(row)
            if fallback:
                content_html = f"<p>{html.escape(fallback)}</p>"
        # Same full HTML in both places (CDATA) so readers that only look at
        # <description> still get paragraphs, images, and embeds.
        safe_html = (content_html or "").replace("]]>", "]]&gt;")

        lines.append("<item>")
        lines.append(f"<title>{_text(title)}</title>")
        lines.append(f"<link>{_text(link)}</link>")
        lines.append(f'<guid isPermaLink="true">{_text(link)}</guid>')
        lines.append(f"<pubDate>{pub}</pubDate>")
        if publisher:
            lines.append(f"<dc:creator>{_text(publisher)}</dc:creator>")
            lines.append(f"<source url=\"{_text(link)}\">{_text(publisher)}</source>")
        if category:
            lines.append(f"<category>{_text(category)}</category>")
        if status:
            lines.append(f"<category>{_text('extraction:' + status)}</category>")
        lines.append(f"<description><![CDATA[{safe_html}]]></description>")
        lines.append(f"<content:encoded><![CDATA[{safe_html}]]></content:encoded>")
        if lead:
            typ = guess_image_type(lead)
            lines.append(f'<enclosure url="{_text(lead)}" type="{_text(typ)}" length="0"/>')
            lines.append(
                f'<media:thumbnail url="{_text(lead)}"/>'
            )
        for img in images:
            typ = guess_image_type(img)
            lines.append(f'<media:content url="{_text(img)}" medium="image" type="{_text(typ)}"/>')
        lines.append("</item>")

    lines.append("</channel>")
    lines.append("</rss>")
    return "\n".join(lines) + "\n"


def write_feed(xml_text: str, primary: str, extra: str | None = None) -> None:
    primary_path = Path(primary)
    primary_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = primary_path.with_name(primary_path.name + ".tmp")
    tmp.write_text(xml_text, encoding="utf-8")
    tmp.replace(primary_path)
    if extra:
        extra_path = Path(extra)
        extra_path.parent.mkdir(parents=True, exist_ok=True)
        tmp2 = extra_path.with_name(extra_path.name + ".tmp")
        shutil.copyfile(primary_path, tmp2)
        tmp2.replace(extra_path)
    log.info("wrote %s%s", primary, f" and {extra}" if extra else "")
