"""Collect legacy public evidence and compare it with the master archive plan.

This is intentionally a one-time research utility rather than web-app behavior.
It keeps raw responses and writes a small, auditable candidate ledger. A failed
source never erases successful results from another source.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin
from urllib.request import Request, urlopen

USER_AGENT = "LFTP-Archive-Research/1.0 (+https://livefromthepath.org/)"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value[:100] or "item"


def fetch(url: str, timeout: int = 45, attempts: int = 3) -> bytes:
    error: Exception | None = None
    for attempt in range(attempts):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            error = exc
            if attempt + 1 < attempts:
                time.sleep(1.5 * (attempt + 1))
    assert error is not None
    raise error


class LinkTextParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self.text: list[str] = []
        self.title: list[str] = []
        self._anchor: dict[str, Any] | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "a" and values.get("href"):
            self._anchor = {
                "url": urljoin(self.base_url, values["href"]),
                "label_parts": [],
            }
        if tag.lower() == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._anchor is not None:
            label = " ".join(" ".join(self._anchor["label_parts"]).split())
            self.links.append({"url": self._anchor["url"], "label": label})
            self._anchor = None
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        self.text.append(cleaned)
        if self._anchor is not None:
            self._anchor["label_parts"].append(cleaned)
        if self._in_title:
            self.title.append(cleaned)


def parse_html(data: bytes, url: str) -> dict[str, Any]:
    parser = LinkTextParser(url)
    parser.feed(data.decode("utf-8", errors="replace"))
    return {
        "title": " ".join(parser.title),
        "text": "\n".join(parser.text),
        "links": parser.links,
    }


def extract_des_moines_episodes(data: bytes, source_url: str) -> list[dict[str, Any]]:
    """Extract the dated show cards from the preserved showid=56 page."""
    markup = data.decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<a href="(show_date\.asp\?showid=56&amp;id=(\d+))"[^>]*>'
        r"(.*?)</a>\s*<br>\s*<span[^>]*>(\d{1,2}/\d{1,2}/\d{4})</span>(.*?)</td>",
        flags=re.DOTALL | re.IGNORECASE,
    )
    episodes: list[dict[str, Any]] = []
    for relative_url, archive_id, title_markup, raw_date, remainder in pattern.findall(markup):
        title = " ".join(parse_html(title_markup.encode(), source_url)["text"].split())
        description = " ".join(
            parse_html(remainder.encode(), source_url)["text"].replace("�", " ").split()
        )
        month, day, year = (int(part) for part in raw_date.split("/"))
        broadcast_date = date(year, month, day)
        episodes.append(
            {
                "source": "des-moines-amplified",
                "source_show_id": "56",
                "source_episode_id": archive_id,
                "broadcast_date": broadcast_date.isoformat(),
                "title": title,
                "description": description,
                "source_url": urljoin(source_url, relative_url.replace("&amp;", "&")),
            }
        )
    return episodes


def cdx_urls(domain: str, start: str, end: str) -> list[tuple[str, str]]:
    target = quote(f"{domain}/*", safe="")
    fields = "timestamp,original,statuscode,mimetype,digest"
    return [
        (
            "wayback-cdx",
            (
                "https://web.archive.org/cdx/search/cdx?"
                f"url={target}&output=json&filter=statuscode:200&collapse=urlkey&"
                f"from={start}&to={end}&fl={fields}"
            ),
        ),
        (
            "arquivo-cdx",
            (
                "https://arquivo.pt/wayback/cdx?"
                f"url={target}&output=json&filter=statuscode:200&collapse=urlkey&"
                f"from={start}&to={end}&fl={fields}"
            ),
        ),
    ]


def run_ytdlp(url: str, timeout: int, *, insecure_tls: bool = False) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--ignore-errors",
        "--no-warnings",
        url,
    ]
    if insecure_tls:
        command.insert(4, "--no-check-certificates")
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if completed.returncode:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(message[-2000:] or f"yt-dlp exited {completed.returncode}")
    return json.loads(completed.stdout)


def flatten_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    queue = list(payload.get("entries") or [])
    while queue:
        entry = queue.pop(0)
        if not isinstance(entry, dict):
            continue
        nested = entry.get("entries")
        if isinstance(nested, list):
            queue.extend(nested)
        else:
            result.append(entry)
    return result


def normalized_title(value: str) -> str:
    value = re.sub(
        r"\b(live from the path|lftp|episode|show segment)\b",
        " ",
        value,
        flags=re.IGNORECASE,
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def title_year_episode(value: str) -> tuple[int, int] | None:
    match = re.search(
        r"\b(20\d{2})\s*(?:[-|:]\s*)?(?:episode|ep\.?|e)[ #_-]*(\d{1,4})\b",
        value,
        flags=re.IGNORECASE,
    )
    return (int(match.group(1)), int(match.group(2))) if match else None


def entry_date(entry: dict[str, Any]) -> date | None:
    raw = entry.get("upload_date") or entry.get("release_date")
    if isinstance(raw, str) and re.fullmatch(r"\d{8}", raw):
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    stamp = entry.get("timestamp") or entry.get("release_timestamp")
    if isinstance(stamp, (int, float)):
        return datetime.fromtimestamp(stamp, tz=UTC).date()
    title = str(entry.get("title") or "")
    compact = re.search(r"(?<!\d)(20\d{2})(0[1-9]|1[0-2])([0-3]\d)(?!\d)", title)
    if compact:
        try:
            return date(*(int(part) for part in compact.groups()))
        except ValueError:
            pass
    month_names = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    named = re.search(
        r"\b(" + "|".join(month_names) + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(20\d{2})\b",
        title,
        flags=re.IGNORECASE,
    )
    if named:
        try:
            return date(
                int(named.group(3)),
                month_names[named.group(1).lower()],
                int(named.group(2)),
            )
        except ValueError:
            pass
    match = re.search(r"(?<!\d)(1[0-2]|0?[1-9])[/-]([0-3]?\d)(?:[/-](\d{2,4}))?", title)
    if match and match.group(3):
        year = int(match.group(3))
        year += 2000 if year < 100 else 0
        try:
            return date(year, int(match.group(1)), int(match.group(2)))
        except ValueError:
            return None
    return None


@dataclass
class MasterIndex:
    episodes: list[dict[str, Any]]

    @classmethod
    def load(cls, path: Path) -> MasterIndex:
        if not path.exists():
            return cls([])
        return cls(json.loads(path.read_text(encoding="utf-8"))["episodes"])

    def match(self, title: str, day: date | None) -> tuple[str, str, float]:
        best: tuple[str, str, float] = ("", "", 0.0)
        needle = normalized_title(title)
        numbered = title_year_episode(title)
        for item in self.episodes:
            canonical = item.get("canonical_date")
            item_title = str(item.get("title") or "")
            date_score = 0.0
            if day and canonical:
                try:
                    delta = abs((date.fromisoformat(canonical) - day).days)
                    date_score = 1.0 if delta <= 1 else 0.85 if delta <= 2 else 0.0
                except ValueError:
                    pass
            title_date = entry_date({"title": item_title})
            if day and title_date and abs((title_date - day).days) <= 1:
                date_score = 1.0
            title_score = (
                SequenceMatcher(None, needle, normalized_title(item_title)).ratio()
                if needle
                else 0.0
            )
            number_score = 0.0
            if numbered:
                item_year = item.get("canonical_year")
                if not item_year and canonical:
                    item_year = int(str(canonical)[:4])
                reported = item.get("reported_episode_number")
                item_numbered = title_year_episode(item_title)
                explicit_number_match = item_numbered == numbered or (
                    item_year == numbered[0] and str(reported) == str(numbered[1])
                )
                if explicit_number_match:
                    number_score = 1.0
            score = max(date_score, title_score, number_score)
            if score > best[2]:
                best = (
                    str(item.get("episode_key") or item.get("rss_episode_id") or ""),
                    str(item.get("canonical_date") or ""),
                    score,
                )
        return best

    def date_candidates(self, day: date, *, maximum_days: int = 7) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        for item in self.episodes:
            canonical = item.get("canonical_date")
            if not canonical:
                continue
            try:
                delta = abs((date.fromisoformat(canonical) - day).days)
            except ValueError:
                continue
            if delta <= maximum_days:
                candidates.append(
                    {
                        "candidate_id": item.get("candidate_id") or "",
                        "day_delta": delta,
                        "canonical_date": canonical,
                        "title": item.get("title") or "",
                        "preferred_local_file": item.get("preferred_local_file") or "",
                        "rss_episode_id": item.get("rss_episode_id") or "",
                        "status": item.get("status") or "",
                    }
                )
        return sorted(
            candidates,
            key=lambda item: (
                item["day_delta"],
                item["canonical_date"],
                item["preferred_local_file"],
            ),
        )


def reconcile_legacy_episodes(
    episodes: list[dict[str, Any]], masters: MasterIndex
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for episode in episodes:
        day = date.fromisoformat(episode["broadcast_date"])
        nearby = masters.date_candidates(day)
        direct = [item for item in nearby if item["day_delta"] <= 1]
        if len(direct) == 1:
            disposition = "matched-one-master"
        elif len(direct) > 1:
            disposition = "matched-multiple-masters-review"
        else:
            disposition = "source-only-candidate"
        results.append(
            {
                **episode,
                "disposition": disposition,
                "matched_masters": direct,
                "nearest_masters_within_7_days": nearby[:5],
            }
        )
    return results


def write_legacy_master_proposal(
    master_plan_path: Path, output: Path, reconciled: list[dict[str, Any]]
) -> None:
    """Apply source-confirmed legacy evidence without collapsing ambiguous audio."""
    plan = copy.deepcopy(json.loads(master_plan_path.read_text(encoding="utf-8")))
    episodes = plan["episodes"]
    by_id = {item["candidate_id"]: item for item in episodes}

    for evidence in reconciled:
        direct = evidence["matched_masters"]
        evidence_pointer = {
            "source": evidence["source"],
            "source_show_id": evidence["source_show_id"],
            "source_episode_id": evidence["source_episode_id"],
            "broadcast_date": evidence["broadcast_date"],
            "title": evidence["title"],
            "source_url": evidence["source_url"],
        }
        if evidence["disposition"] == "matched-one-master":
            target = by_id[direct[0]["candidate_id"]]
            target["legacy_evidence"] = [evidence_pointer]
            target["canonical_date"] = evidence["broadcast_date"]
            target["canonical_year"] = int(evidence["broadcast_date"][:4])
            target["season"] = target["canonical_year"]
            target["date_source"] = "des-moines-amplified-archived-broadcast-date"
            if not target.get("title"):
                target["title"] = evidence["title"]
            target["metadata_flags"] = [
                flag
                for flag in target.get("metadata_flags", [])
                if flag != "Title missing from embedded metadata"
            ]
            target["metadata_flags"].append(
                "Historical title/date confirmed by Des Moines Amplified archive"
            )
        elif evidence["disposition"] == "matched-multiple-masters-review":
            for match in direct:
                target = by_id[match["candidate_id"]]
                target.setdefault("legacy_evidence", []).append(evidence_pointer)
                target.setdefault("review_flags", []).append(
                    "One historical broadcast matches multiple retained master candidates"
                )
        else:
            digest = hashlib.sha1(f"dma-56-{evidence['source_episode_id']}".encode()).hexdigest()[
                :12
            ]
            flags = ["Audio not yet recovered from RSS, local files, or public media"]
            if evidence["title"] == "Tyson vs. Holyfield":
                flags.append(
                    "Content-compare with local 2010-10-15 recording before declaring audio missing"
                )
            episodes.append(
                {
                    "candidate_id": f"candidate-legacy-{digest}",
                    "canonical_year": int(evidence["broadcast_date"][:4]),
                    "canonical_date": evidence["broadcast_date"],
                    "date_source": "des-moines-amplified-archived-broadcast-date",
                    "season": int(evidence["broadcast_date"][:4]),
                    "proposed_episode_number": None,
                    "reported_episode_number": None,
                    "reported_number_confidence": "unknown",
                    "title": evidence["title"],
                    "status": "source-confirmed-audio-missing",
                    "rss_episode_id": None,
                    "rss_guid": None,
                    "rss_audio_url": None,
                    "preferred_local_file": None,
                    "superseded_local_files": [],
                    "master_audio_source": "missing",
                    "master_source_reference": evidence["source_url"],
                    "local_dispositions": [],
                    "duration_seconds": None,
                    "file_size_bytes": None,
                    "metadata_flags": [
                        "Episode existence, title, and date confirmed by archived broadcaster page"
                    ],
                    "review_flags": flags,
                    "master_relative_path": None,
                    "archive_relative_paths": [],
                    "number_conflict": False,
                    "legacy_evidence": [evidence_pointer],
                }
            )

    episodes.sort(
        key=lambda item: (
            item.get("canonical_year") or 9999,
            item.get("canonical_date") or "9999-12-31",
            item.get("candidate_id") or "",
        )
    )
    number_by_year: dict[int, int] = {}
    for item in episodes:
        year = item.get("canonical_year")
        if not year:
            item["proposed_episode_number"] = None
            continue
        number_by_year[year] = number_by_year.get(year, 0) + 1
        item["proposed_episode_number"] = number_by_year[year]

    plan["episode_count"] = len(episodes)
    plan["legacy_evidence_applied_at"] = utc_now()
    plan["legacy_evidence_source"] = "research/des-moines-amplified-episodes.json"
    years: list[dict[str, Any]] = []
    for year in sorted({item.get("canonical_year") for item in episodes}, key=lambda x: x or 9999):
        members = [item for item in episodes if item.get("canonical_year") == year]
        years.append(
            {
                "year": year,
                "episode_count": len(members),
                "exact_date_count": sum(bool(item.get("canonical_date")) for item in members),
                "rss_count": sum(bool(item.get("rss_episode_id")) for item in members),
                "local_count": sum(bool(item.get("preferred_local_file")) for item in members),
                "review_count": sum(bool(item.get("review_flags")) for item in members),
            }
        )
    plan["years"] = years
    (output / "master-catalog-proposal.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "canonical_year",
        "proposed_episode_number",
        "canonical_date",
        "title",
        "status",
        "master_audio_source",
        "preferred_local_file",
        "rss_episode_id",
        "review_flags",
    ]
    with (output / "master-catalog-proposal.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for item in episodes:
            row = dict(item)
            row["review_flags"] = " | ".join(row.get("review_flags", []))
            writer.writerow(row)


def write_legacy_evidence(
    output: Path, reconciled: list[dict[str, Any]], snapshot_url: str
) -> None:
    reconciled = sorted(reconciled, key=lambda item: item["broadcast_date"])
    (output / "des-moines-amplified-episodes.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0.0",
                "source_snapshot": snapshot_url,
                "episode_count": len(reconciled),
                "episodes": reconciled,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    fields = [
        "broadcast_date",
        "source_episode_id",
        "title",
        "description",
        "disposition",
        "matched_master_dates",
        "matched_local_files",
        "nearest_master_dates",
        "source_url",
    ]
    with (output / "des-moines-amplified-episodes.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in reconciled:
            direct = item["matched_masters"]
            nearest = item["nearest_masters_within_7_days"]
            writer.writerow(
                {
                    "broadcast_date": item["broadcast_date"],
                    "source_episode_id": item["source_episode_id"],
                    "title": item["title"],
                    "description": item["description"],
                    "disposition": item["disposition"],
                    "matched_master_dates": " | ".join(
                        candidate["canonical_date"] for candidate in direct
                    ),
                    "matched_local_files": " | ".join(
                        candidate["preferred_local_file"]
                        for candidate in direct
                        if candidate["preferred_local_file"]
                    ),
                    "nearest_master_dates": " | ".join(
                        f"{candidate['canonical_date']} ({candidate['day_delta']}d)"
                        for candidate in nearest
                    ),
                    "source_url": item["source_url"],
                }
            )

    counts: dict[str, int] = {}
    for item in reconciled:
        counts[item["disposition"]] = counts.get(item["disposition"], 0) + 1
    source_only = [item for item in reconciled if item["disposition"] == "source-only-candidate"]
    lines = [
        "# Des Moines Amplified reconciliation",
        "",
        f"Preserved broadcasts found: **{len(reconciled)}**",
        "",
        "## Result",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(counts.items()))
    lines.extend(
        [
            "",
            (
                "A match means the preserved Monday broadcast date is within one day of a "
                "master-catalog date. It proposes the historical title and broadcast date; "
                "it does not prove byte identity."
            ),
            "",
            "## Source-only candidates",
            "",
            "| Broadcast | Historical title | Nearest current master |",
            "|---:|---|---|",
        ]
    )
    for item in source_only:
        nearest = item["nearest_masters_within_7_days"]
        nearest_text = (
            ", ".join(
                f"{candidate['canonical_date']} ({candidate['day_delta']} days)"
                for candidate in nearest
            )
            or "None within 7 days"
        )
        lines.append(f"| {item['broadcast_date']} | {item['title']} | {nearest_text} |")
    lines.extend(
        [
            "",
            (
                "`Tyson vs. Holyfield` (2010-10-11) should be content-compared with the "
                "local 2010-10-15 recording before either is treated as a missing show."
            ),
            "",
        ]
    )
    (output / "DES_MOINES_RECONCILIATION.md").write_text("\n".join(lines), encoding="utf-8")


def media_row(platform: str, entry: dict[str, Any], masters: MasterIndex) -> dict[str, Any]:
    title = str(entry.get("title") or "")
    day = entry_date(entry)
    duration_value = entry.get("duration")
    duration = float(duration_value) if isinstance(duration_value, (int, float)) else None
    master_id, master_date, match_score = masters.match(title, day)
    if match_score >= 0.85:
        disposition = "likely-existing-episode-or-supporting-source"
    elif duration is not None and duration < 20 * 60:
        disposition = "supporting-clip-needs-episode-link"
    elif duration is not None and duration >= 20 * 60 and day is not None:
        disposition = "possible-missing-full-show"
    elif duration is not None and duration >= 20 * 60:
        disposition = "long-form-needs-date-reconciliation"
    else:
        disposition = "manual-review-insufficient-metadata"
    webpage_url = entry.get("webpage_url") or entry.get("url") or ""
    if isinstance(webpage_url, str) and not webpage_url.startswith("http") and entry.get("id"):
        webpage_url = (
            f"https://www.youtube.com/watch?v={entry['id']}"
            if platform == "youtube"
            else f"https://vimeo.com/{entry['id']}"
        )
    return {
        "platform": platform,
        "media_id": entry.get("id") or "",
        "title": title,
        "publication_date": day.isoformat() if day else "",
        "duration_seconds": int(duration) if duration is not None else "",
        "url": webpage_url,
        "candidate_disposition": disposition,
        "matched_master_id": master_id if match_score >= 0.85 else "",
        "matched_master_date": master_date if match_score >= 0.85 else "",
        "match_score": round(match_score, 3),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "platform",
        "media_id",
        "title",
        "publication_date",
        "duration_seconds",
        "url",
        "candidate_disposition",
        "matched_master_id",
        "matched_master_date",
        "match_score",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def write_media_report(output: Path, rows: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row["candidate_disposition"])
        counts[status] = counts.get(status, 0) + 1
    possible = [row for row in rows if row["candidate_disposition"] == "possible-missing-full-show"]
    unresolved = [
        row for row in rows if row["candidate_disposition"] == "long-form-needs-date-reconciliation"
    ]
    possible_dates = {row["publication_date"] for row in possible if row["publication_date"]}
    lines = [
        "# Public media reconciliation",
        "",
        f"Media records inventoried: **{len(rows)}**",
        "",
        "## Classification",
        "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(counts.items()))
    lines.extend(
        [
            "",
            (
                "These are recovery leads, not automatic episode insertions. Short items remain "
                "supporting evidence. A long-form item becomes a possible missing show only when "
                "its title supplies a date that does not match the current master catalog."
            ),
            "",
            "## Possible missing full shows",
            "",
            (
                f"The {len(possible)} media items below represent up to "
                f"{len(possible_dates)} dated broadcasts; multiple items on one date may be "
                "parts of the same show."
            ),
            "",
            "| Date | Duration | Title | Source |",
            "|---:|---:|---|---|",
        ]
    )
    for row in possible:
        duration = int(row["duration_seconds"])
        safe_title = str(row["title"]).replace("|", "\\|")
        lines.append(
            f"| {row['publication_date']} | {duration // 60}:{duration % 60:02d} | "
            f"{safe_title} | [YouTube]({row['url']}) |"
        )
    lines.extend(
        [
            "",
            f"Long-form records still needing a precise date or number: **{len(unresolved)}**.",
            "Those remain in `media-candidates.csv` and are not counted as missing episodes.",
            "",
        ]
    )
    (output / "MEDIA_RECONCILIATION.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seeds",
        type=Path,
        default=Path("catalog/finalization/research-seeds.json"),
    )
    parser.add_argument(
        "--master-plan",
        type=Path,
        default=Path("catalog/finalization/master-archive-plan.json"),
    )
    parser.add_argument("--output", type=Path, default=Path("catalog/finalization/research"))
    parser.add_argument("--media-timeout", type=int, default=1800)
    parser.add_argument(
        "--insecure-media-tls",
        action="store_true",
        help="Disable yt-dlp certificate checks only for constrained test environments.",
    )
    parser.add_argument(
        "--reuse-media-raw",
        action="store_true",
        help="Rebuild reconciliation from saved media JSON without querying providers.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Rebuild all outputs from saved raw files without network access.",
    )
    args = parser.parse_args()

    config = json.loads(args.seeds.read_text(encoding="utf-8"))
    raw = args.output / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {
        "started_at": utc_now(),
        "pages": [],
        "indexes": [],
        "media": [],
        "errors": [],
    }
    legacy_episodes: list[dict[str, Any]] = []
    legacy_snapshot_url = ""

    for item in config.get("wayback_pages", []):
        try:
            name = f"wayback-{slug(item['source_id'])}.html"
            if args.offline:
                data = (raw / name).read_bytes()
            else:
                data = fetch(item["url"])
                (raw / name).write_bytes(data)
            parsed = parse_html(data, item["url"])
            parsed.update(
                {
                    "source_id": item["source_id"],
                    "url": item["url"],
                    "raw_file": f"raw/{name}",
                }
            )
            results["pages"].append(parsed)
            if item["source_id"] == "des-moines-amplified":
                legacy_episodes.extend(extract_des_moines_episodes(data, item["url"]))
                legacy_snapshot_url = item["url"]
        except Exception as exc:  # noqa: BLE001 - source errors are audit data
            results["errors"].append({"source": item["url"], "error": str(exc)})

    for item in config.get("archive_domains", []):
        for provider, url in cdx_urls(item["domain"], item["from"], item["to"]):
            try:
                name = f"{provider}-{slug(item['domain'])}.json"
                if args.offline:
                    data = (raw / name).read_bytes()
                else:
                    data = fetch(url)
                payload = json.loads(data.decode("utf-8", errors="replace"))
                if not args.offline:
                    (raw / name).write_bytes(data)
                results["indexes"].append(
                    {
                        "source_id": item["source_id"],
                        "provider": provider,
                        "query_url": url,
                        "row_count": (
                            max(0, len(payload) - 1) if isinstance(payload, list) else None
                        ),
                        "raw_file": f"raw/{name}",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - source errors are audit data
                results["errors"].append({"source": url, "error": str(exc)})

    masters = MasterIndex.load(args.master_plan)
    if legacy_episodes:
        reconciled = reconcile_legacy_episodes(legacy_episodes, masters)
        write_legacy_evidence(args.output, reconciled, legacy_snapshot_url)
        write_legacy_master_proposal(args.master_plan, args.output, reconciled)
        results["legacy_episode_count"] = len(reconciled)
        results["legacy_source_only_count"] = sum(
            item["disposition"] == "source-only-candidate" for item in reconciled
        )
    rows: list[dict[str, Any]] = []
    for item in config.get("youtube_channels", []):
        try:
            name = f"youtube-{slug(item['source_id'])}.json"
            if args.reuse_media_raw or args.offline:
                payload = json.loads((raw / name).read_text(encoding="utf-8"))
            else:
                payload = run_ytdlp(
                    item["url"],
                    args.media_timeout,
                    insecure_tls=args.insecure_media_tls,
                )
                (raw / name).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            entries = flatten_entries(payload)
            rows.extend(media_row("youtube", entry, masters) for entry in entries)
            results["media"].append(
                {
                    "source": item["url"],
                    "platform": "youtube",
                    "count": len(entries),
                    "raw_file": f"raw/{name}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - source errors are audit data
            results["errors"].append({"source": item["url"], "error": str(exc)})

    for query in config.get("vimeo_searches", []):
        url = f"https://vimeo.com/search?q={quote(query)}"
        try:
            name = f"vimeo-{slug(query)}.json"
            if args.reuse_media_raw or args.offline:
                payload = json.loads((raw / name).read_text(encoding="utf-8"))
            else:
                payload = run_ytdlp(url, args.media_timeout, insecure_tls=args.insecure_media_tls)
                (raw / name).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            entries = flatten_entries(payload)
            rows.extend(media_row("vimeo", entry, masters) for entry in entries)
            results["media"].append(
                {
                    "source": url,
                    "platform": "vimeo",
                    "count": len(entries),
                    "raw_file": f"raw/{name}",
                }
            )
        except Exception as exc:  # noqa: BLE001 - source errors are audit data
            results["errors"].append({"source": url, "error": str(exc)})

    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["platform"]), str(row["media_id"] or row["url"]))
        deduplicated[key] = row
    rows = sorted(
        deduplicated.values(),
        key=lambda row: (row["publication_date"], row["platform"], row["title"]),
    )
    write_csv(args.output / "media-candidates.csv", rows)
    write_media_report(args.output, rows)
    results["completed_at"] = utc_now()
    results["candidate_count"] = len(rows)
    (args.output / "research-result.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Research complete: {len(rows)} media records; {len(results['errors'])} source errors.")
    print(f"Review {args.output / 'media-candidates.csv'}")
    return 0 if rows or results["pages"] or results["indexes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
