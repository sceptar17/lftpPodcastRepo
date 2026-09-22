from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .models import TranscriptionResult, TranscriptSegment


class TranscriptionProvider(ABC):
    @abstractmethod
    def transcribe(self, audio_path: Path, episode_id: str) -> TranscriptionResult: ...


class FixtureTranscriptionProvider(TranscriptionProvider):
    def __init__(self, fixture_dir: Path):
        self.fixture_dir = fixture_dir

    def transcribe(self, audio_path: Path, episode_id: str) -> TranscriptionResult:
        payload = json.loads(
            (self.fixture_dir / f"{episode_id}.json").read_text(encoding="utf-8")
        )
        return TranscriptionResult.model_validate(payload)


class OpenAITranscriptionProvider(TranscriptionProvider):
    """Timestamp-first provider. whisper-1 is intentional: it supports segment timestamps."""

    def __init__(self, model: str = "whisper-1"):
        from openai import OpenAI
        self.client = OpenAI()
        self.model = model

    def transcribe(self, audio_path: Path, episode_id: str) -> TranscriptionResult:
        with audio_path.open("rb") as audio:
            response = self.client.audio.transcriptions.create(
                model=self.model,
                file=audio,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        raw = response.model_dump(mode="json")
        segments = [TranscriptSegment(
            segment_id=f"seg-{index:05d}", start_seconds=float(segment["start"]),
            end_seconds=float(segment["end"]), text=segment["text"].strip(),
            confidence=None,
        ) for index, segment in enumerate(raw.get("segments", []), start=1)]
        return TranscriptionResult(
            text=raw.get("text", ""), segments=segments, provider="openai",
            model=self.model, provider_version=None, raw=raw, is_complete=True,
        )
