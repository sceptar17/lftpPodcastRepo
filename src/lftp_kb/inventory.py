from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
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


@dataclass(frozen=True)
class InventorySnapshot:
    discovered: list[DiscoveredEpisode]
    processed: list[Episode]
    missing_from_repository: list[DiscoveredEpisode]
    local_audio: list[LocalAudioItem]
    unmatched_local_audio: list[LocalAudioItem]
    local_root_configured: bool


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
            availability="Install locally", configured=False,
            timestamp_support="Segment + word", speaker_support="Add-on diarization",
            cost_note="$0 API cost; compute time and hardware matter", adapter_status="Planned",
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


def scan_local_audio(root: Path | None, discovered: list[DiscoveredEpisode]) -> list[LocalAudioItem]:
    if root is None or not root.exists() or not root.is_dir():
        return []
    by_number = {episode.episode_number: episode for episode in discovered if episode.episode_number}
    result: list[LocalAudioItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        number = _episode_number(path.name)
        match = by_number.get(number)
        result.append(LocalAudioItem(
            path=str(path), filename=path.name, size_mb=round(path.stat().st_size / 1_048_576, 1),
            guessed_episode_number=number, match_episode_id=match.episode_id if match else None,
            match_reason=f"Episode number {number}" if match else None,
        ))
    return result


def build_inventory(repository: Repository, local_root: Path | None) -> InventorySnapshot:
    discovered = load_discovered(repository)
    processed = list(repository.episodes())
    processed_ids = {episode.episode_id for episode in processed}
    missing = [episode for episode in discovered if episode.episode_id not in processed_ids]
    local_audio = scan_local_audio(local_root, discovered)
    unmatched = [item for item in local_audio if item.match_episode_id is None]
    return InventorySnapshot(
        discovered=discovered, processed=processed, missing_from_repository=missing,
        local_audio=local_audio, unmatched_local_audio=unmatched,
        local_root_configured=local_root is not None,
    )
