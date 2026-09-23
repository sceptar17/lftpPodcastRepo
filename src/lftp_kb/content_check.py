from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from .models import DiscoveredEpisode, ReconstructionReport
from .remote_audio import _download_remote, _save_verification
from .repository import Repository
from .transcription import FasterWhisperTranscriptionProvider

ContentProgress = Callable[[int, int, str, str | None], None]
SAMPLE_START_SECONDS = 120
SAMPLE_SECONDS = 180
STOP_WORDS = {
    "a",
    "about",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "do",
    "for",
    "from",
    "had",
    "has",
    "have",
    "he",
    "i",
    "if",
    "in",
    "is",
    "it",
    "just",
    "like",
    "not",
    "of",
    "on",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "there",
    "they",
    "this",
    "to",
    "was",
    "we",
    "were",
    "what",
    "when",
    "with",
    "would",
    "you",
    "your",
}


def run_content_checks(
    repository: Repository,
    archive_root: Path,
    report: ReconstructionReport,
    episodes: list[DiscoveredEpisode],
    decisions: dict,
    progress: ContentProgress | None = None,
) -> ReconstructionReport:
    assets = {asset.asset_id: asset for asset in report.assets}
    rss = {episode.episode_id: episode for episode in episodes}
    candidates = [
        proposal
        for proposal in report.match_proposals
        if decisions.get(proposal.proposal_id, {}).get("decision") not in {"confirmed", "rejected"}
        and proposal.recommendation != "auto-link"
        and proposal.verification_status != "different-recording"
        and proposal.content_check_status not in {"strong-match", "different", "ambiguous"}
    ]
    total = len(candidates)
    provider: FasterWhisperTranscriptionProvider | None = None

    for index, proposal in enumerate(candidates, start=1):
        asset = assets[proposal.asset_id]
        episode = rss[proposal.rss_episode_id]
        if progress:
            progress(index - 1, total, "Checking short audio samples", asset.filename)
        try:
            local_text = _load_sample(repository, "local", asset.asset_id)
            remote_text = _load_sample(repository, "rss", episode.episode_id)
            if local_text is None or remote_text is None:
                if provider is None:
                    if progress:
                        progress(
                            index - 1, total, "Loading tiny.en transcription model", asset.filename
                        )
                    provider = FasterWhisperTranscriptionProvider(
                        model="tiny.en",
                        device="cpu",
                        compute_type="int8",
                        sample_seconds=SAMPLE_SECONDS,
                        sample_start_seconds=SAMPLE_START_SECONDS,
                    )
                if local_text is None:
                    local_text = provider.transcribe(
                        archive_root / asset.relative_path, f"sample-local-{asset.asset_id}"
                    ).text
                    _save_sample(repository, "local", asset.asset_id, local_text)
                if remote_text is None:
                    temporary = _download_remote(episode.audio_url)
                    try:
                        remote_text = provider.transcribe(
                            temporary, f"sample-rss-{episode.episode_id}"
                        ).text
                    finally:
                        temporary.unlink(missing_ok=True)
                    _save_sample(repository, "rss", episode.episode_id, remote_text)

            score = content_similarity(local_text, remote_text)
            proposal.content_similarity = score
            proposal.local_sample_text = local_text[:1200]
            proposal.remote_sample_text = remote_text[:1200]
            proposal.content_check_details = [
                "Compared rough tiny.en transcripts from 2:00 through 5:00 in both recordings.",
                f"Transcript-content similarity is {score:.1%}.",
            ]
            enough_speech = len(_tokens(local_text)) >= 10 and len(_tokens(remote_text)) >= 10
            if not enough_speech:
                proposal.content_check_status = "ambiguous"
                proposal.content_check_details.append(
                    "One sample contained too little recognized speech for an automatic decision."
                )
            elif score >= 0.55:
                proposal.content_check_status = "strong-match"
                proposal.recommendation = "auto-link"
            elif score <= 0.08:
                proposal.content_check_status = "different"
            else:
                proposal.content_check_status = "ambiguous"
        except Exception as error:  # noqa: BLE001 - preserve progress across isolated failures
            proposal.content_check_status = "failed"
            proposal.content_check_details = [f"{type(error).__name__}: {error}"]
        _save_verification(repository, report)
        if progress:
            progress(index, total, "Checking short audio samples", asset.filename)
    return report


def content_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if len(left_tokens) < 10 or len(right_tokens) < 10:
        return 0.0
    left_words, right_words = set(left_tokens), set(right_tokens)
    word_score = len(left_words & right_words) / len(left_words | right_words)
    left_pairs = set(pairwise(left_tokens))
    right_pairs = set(pairwise(right_tokens))
    pair_score = len(left_pairs & right_pairs) / max(1, len(left_pairs | right_pairs))
    return round(0.35 * word_score + 0.65 * pair_score, 4)


def _tokens(text: str) -> list[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9']+", text.lower())
        if token not in STOP_WORDS and len(token) > 1
    ]


def _sample_path(repository: Repository, source: str, identifier: str) -> Path:
    return repository.root / "catalog" / "content-samples" / source / f"{identifier}.json"


def _load_sample(repository: Repository, source: str, identifier: str) -> str | None:
    path = _sample_path(repository, source, identifier)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("text")
    except (OSError, ValueError):
        return None


def _save_sample(repository: Repository, source: str, identifier: str, text: str) -> None:
    repository.atomic_json(
        f"catalog/content-samples/{source}/{identifier}.json",
        {
            "provider": "faster-whisper",
            "model": "tiny.en",
            "sample_start_seconds": SAMPLE_START_SECONDS,
            "sample_seconds": SAMPLE_SECONDS,
            "processed_at": datetime.now(UTC).isoformat(),
            "text": text,
        },
    )
