from __future__ import annotations

import importlib.util
import json
import os
import re
import urllib.parse
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

from .models import DiscoveredEpisode, Episode
from .repository import Repository

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".wma", ".mp4"}


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    name: str
    kind: str
    availability: str
    configured: bool
    timestamp_support: str
    speaker_support: str
    cost_note: str
    adapter_status: str


@dataclass(frozen=True)
class LocalAudioItem:
    path: str
    filename: str
    size_mb: float
    guessed_episode_number: int | None
    match_episode_id: str | None
    match_reason: str | None
    match_confidence: float | None = None
    match_status: str = "unmatched"


@dataclass(frozen=True)
class InventorySnapshot:
    discovered: list[DiscoveredEpisode]
    processed: list[Episode]
    missing_from_repository: list[DiscoveredEpisode]
    local_audio: list[LocalAudioItem]
    unmatched_local_audio: list[LocalAudioItem]
    uncertain_local_audio: list[LocalAudioItem]
    feed_only_audio: list[DiscoveredEpisode]
    matched_feed_count: int
    local_root_configured: bool
    local_root_exists: bool


def load_discovered(repository: Repository) -> list[DiscoveredEpisode]:
    path = repository.root / "state" / "discovered.json"
    if not path.exists():
        return []
    return [
        DiscoveredEpisode.model_validate(item)
        for item in json.loads(path.read_text(encoding="utf-8"))
    ]


def provider_profiles() -> list[ProviderProfile]:
    return [
        ProviderProfile(
            provider_id="openai-whisper", name="OpenAI Whisper", kind="Cloud",
            availability="Available", configured=bool(os.getenv("OPENAI_API_KEY")),
            timestamp_support="Segment + word", speaker_support="No native labels",
            cost_note="Paid per audio minute; verify current rate", adapter_status="Implemented",
        ),
        ProviderProfile(
            provider_id="openai-diarize", name="OpenAI diarized transcription", kind="Cloud",
            availability="Available", configured=bool(os.getenv("OPENAI_API_KEY")),
            timestamp_support="Segment", speaker_support="Native diarization",
            cost_note="Benchmark before archive use", adapter_status="Planned",
        ),
        ProviderProfile(
            provider_id="muse", name="Muse", kind="Cloud",
            availability="Research needed", configured=bool(os.getenv("MUSE_API_KEY")),
            timestamp_support="To verify", speaker_support="To verify",
            cost_note="Potential lower-cost archive option", adapter_status="Placeholder",
        ),
        ProviderProfile(
            provider_id="faster-whisper", name="faster-whisper", kind="Local / open source",
            availability="Ready" if importlib.util.find_spec("faster_whisper") else "Install locally",
            configured=importlib.util.find_spec("faster_whisper") is not None,
            timestamp_support="Segment + word", speaker_support="Add-on diarization",
            cost_note="$0 API cost; compute time and hardware matter", adapter_status="Implemented",
        ),
        ProviderProfile(
            provider_id="whisper-cpp", name="whisper.cpp", kind="Local / open source",
            availability="Install locally", configured=False,
            timestamp_support="Segment + word", speaker_support="Limited / add-on",
            cost_note="$0 API cost; slower without suitable hardware", adapter_status="Planned",
        ),
    ]


def _episode_number(filename: str) -> int | None:
    patterns = (r"(?:episode|ep|e)[ _.-]?(\d{1,4})\b", r"@(\d{1,4})\b")
    for pattern in patterns:
        if match := re.search(pattern, filename, re.IGNORECASE):
            return int(match.group(1))
    return None


def _normalized(value: str) -> str:
    value = re.sub(r"\b(?:live from the path|lftp|episode|ep)\b", " ", value.lower())
    value = re.sub(r"\d{4}[-_. ]\d{1,2}[-_. ]\d{1,2}", " ", value)
    value = re.sub(r"\b\d{1,4}\b", " ", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _filename_dates(value: str) -> set[date]:
    found: set[date] = set()
    patterns = (
        (r"(?<!\d)(20\d{2})[-_. ](\d{1,2})[-_. ](\d{1,2})(?!\d)", (0, 1, 2)),
        (r"(?<!\d)(\d{1,2})[-_. ](\d{1,2})[-_. ](20\d{2})(?!\d)", (2, 0, 1)),
    )
    for pattern, order in patterns:
        for match in re.findall(pattern, value):
            try:
                found.add(date(int(match[order[0]]), int(match[order[1]]), int(match[order[2]])))
            except ValueError:
                continue
    return found


def scan_local_audio(root: Path | None, discovered: list[DiscoveredEpisode]) -> list[LocalAudioItem]:
    if root is None or not root.exists() or not root.is_dir():
        return []
    by_number: dict[int, list[DiscoveredEpisode]] = {}
    by_audio_name: dict[str, list[DiscoveredEpisode]] = {}
    by_date: dict[date, list[DiscoveredEpisode]] = {}
    for episode in discovered:
        if episode.episode_number is not None:
            by_number.setdefault(episode.episode_number, []).append(episode)
        audio_name = Path(urllib.parse.unquote(urllib.parse.urlparse(episode.audio_url).path)).name.lower()
        if audio_name:
            by_audio_name.setdefault(audio_name, []).append(episode)
        by_date.setdefault(episode.publication_date.date(), []).append(episode)
    result: list[LocalAudioItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        number = _episode_number(path.name)
        exact_names = by_audio_name.get(path.name.lower(), [])
        numbered = by_number.get(number, [])
        dated = {
            episode.episode_id: episode
            for file_date in _filename_dates(path.stem)
            for episode in by_date.get(file_date, [])
        }
        match = exact_names[0] if len(exact_names) == 1 else None
        reason = "Exact RSS enclosure filename" if match else None
        if not match and len(numbered) == 1:
            match, reason = numbered[0], f"Unique episode number {number}"
        if not match and len(dated) == 1:
            match, reason = next(iter(dated.values())), "Unique publication date in filename"
        confidence = 1.0 if match else None
        status = "matched" if match else "unmatched"
        if not match:
            local_title = _normalized(path.stem)
            scored = sorted(
                ((SequenceMatcher(None, local_title, _normalized(episode.title)).ratio(), episode)
                 for episode in discovered if local_title and _normalized(episode.title)),
                key=lambda value: value[0], reverse=True,
            )
            if scored:
                top_score, top_episode = scored[0]
                runner_up = scored[1][0] if len(scored) > 1 else 0.0
                if top_score >= 0.82 and top_score - runner_up >= 0.08:
                    match, confidence, status = top_episode, round(top_score, 3), "matched"
                    reason = "High-confidence normalized title match"
                elif top_score >= 0.58:
                    match, confidence, status = top_episode, round(top_score, 3), "uncertain"
                    reason = "Possible title match; review required"
        result.append(LocalAudioItem(
            path=str(path), filename=path.name, size_mb=round(path.stat().st_size / 1_048_576, 1),
            guessed_episode_number=number, match_episode_id=match.episode_id if match else None,
            match_reason=reason, match_confidence=confidence, match_status=status,
        ))
    return result


def build_inventory(repository: Repository, local_root: Path | None) -> InventorySnapshot:
    discovered = load_discovered(repository)
    processed = list(repository.episodes())
    processed_ids = {episode.episode_id for episode in processed}
    missing = [episode for episode in discovered if episode.episode_id not in processed_ids]
    local_audio = scan_local_audio(local_root, discovered)
    unmatched = [item for item in local_audio if item.match_status == "unmatched"]
    uncertain = [item for item in local_audio if item.match_status == "uncertain"]
    matched_ids = {
        item.match_episode_id for item in local_audio
        if item.match_status == "matched" and item.match_episode_id
    }
    feed_only = [episode for episode in discovered if episode.episode_id not in matched_ids]
    return InventorySnapshot(
        discovered=discovered, processed=processed, missing_from_repository=missing,
        local_audio=local_audio, unmatched_local_audio=unmatched,
        uncertain_local_audio=uncertain, feed_only_audio=feed_only,
        matched_feed_count=len(matched_ids),
        local_root_configured=local_root is not None,
        local_root_exists=bool(local_root and local_root.exists() and local_root.is_dir()),
    )
