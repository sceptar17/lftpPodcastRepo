from __future__ import annotations

import hashlib
import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC
from email.utils import parsedate_to_datetime

from .models import DiscoveredEpisode

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


def _text(node: ET.Element, *names: str) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def _plain(value: str) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return re.sub(r"\s+", " ", value).strip()


def stable_episode_id(guid: str, title: str, published: str, episode_number: int | None) -> str:
    if episode_number is not None:
        prefix = f"ep-{episode_number:03d}"
    else:
        prefix = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "episode"
    digest = hashlib.sha256(f"{guid}|{published}".encode()).hexdigest()[:10]
    return f"{prefix}-{digest}"


def parse_rss(xml: bytes | str, source_url: str) -> list[DiscoveredEpisode]:
    root = ET.fromstring(xml)
    items = root.findall("./channel/item")
    result: list[DiscoveredEpisode] = []
    channel_image = root.find("./channel/image/url")
    channel_art = channel_image.text.strip() if channel_image is not None and channel_image.text else None
    itunes_channel_image = root.find(f"./channel/{ITUNES}image")
    if itunes_channel_image is not None:
        channel_art = itunes_channel_image.attrib.get("href", channel_art)
    for item in items:
        enclosure = item.find("enclosure")
        audio_url = enclosure.attrib.get("url", "") if enclosure is not None else ""
        if not audio_url:
            continue
        title = _text(item, "title")
        guid = _text(item, "guid") or audio_url
        pub_raw = _text(item, "pubDate")
        published = parsedate_to_datetime(pub_raw)
        if published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        number_raw = _text(item, f"{ITUNES}episode")
        number = int(number_raw) if number_raw.isdigit() else None
        artwork = channel_art
        image = item.find(f"{ITUNES}image")
        if image is not None:
            artwork = image.attrib.get("href", artwork)
        description = _text(item, "description", f"{CONTENT}encoded", f"{ITUNES}summary")
        result.append(DiscoveredEpisode(
            episode_id=stable_episode_id(guid, title, pub_raw, number), guid=guid, title=title,
            episode_number=number, publication_date=published, source_rss_url=source_url,
            source_page_url=_text(item, "link") or None, audio_url=audio_url,
            artwork_url=artwork, description=_plain(description),
        ))
    return result


def fetch_rss(url: str, timeout: int = 60) -> tuple[bytes, list[DiscoveredEpisode]]:
    request = urllib.request.Request(url, headers={"User-Agent": "lftp-knowledge/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return body, parse_rss(body, url)

