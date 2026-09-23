from __future__ import annotations

import importlib.metadata
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
        payload = json.loads((self.fixture_dir / f"{episode_id}.json").read_text(encoding="utf-8"))
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
        segments = [
            TranscriptSegment(
                segment_id=f"seg-{index:05d}",
                start_seconds=float(segment["start"]),
                end_seconds=float(segment["end"]),
                text=segment["text"].strip(),
                confidence=None,
            )
            for index, segment in enumerate(raw.get("segments", []), start=1)
        ]
        return TranscriptionResult(
            text=raw.get("text", ""),
            segments=segments,
            provider="openai",
            model=self.model,
            provider_version=None,
            raw=raw,
            is_complete=True,
        )


class FasterWhisperTranscriptionProvider(TranscriptionProvider):
    """Local CPU/GPU transcription with no per-minute API charge."""

    def __init__(
        self,
        model: str = "small.en",
        device: str = "cpu",
        compute_type: str = "int8",
        sample_seconds: int | None = None,
        sample_start_seconds: int = 0,
    ):
        try:
            from faster_whisper import WhisperModel
        except ImportError as error:
            raise RuntimeError(
                "Local transcription is not installed. Run: "
                'python -m pip install -e ".[local-transcription]"'
            ) from error
        self.model_name = model
        self.device = device
        self.compute_type = compute_type
        self.sample_seconds = sample_seconds
        self.sample_start_seconds = sample_start_seconds
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: Path, episode_id: str) -> TranscriptionResult:
        options: dict[str, object] = {
            "beam_size": 5,
            "vad_filter": True,
            "word_timestamps": True,
        }
        if self.sample_seconds:
            sample_end = self.sample_start_seconds + self.sample_seconds
            options["clip_timestamps"] = f"{self.sample_start_seconds},{sample_end}"
        generated, info = self._model.transcribe(str(audio_path), **options)
        provider_segments = list(generated)
        segments = [
            TranscriptSegment(
                segment_id=f"seg-{index:05d}",
                start_seconds=float(segment.start),
                end_seconds=float(segment.end),
                text=segment.text.strip(),
                confidence=_confidence(segment.avg_logprob),
            )
            for index, segment in enumerate(provider_segments, start=1)
            if segment.text.strip()
        ]
        text = " ".join(segment.text for segment in segments)
        raw = {
            "episode_id": episode_id,
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "duration_after_vad": getattr(info, "duration_after_vad", None),
            "device": self.device,
            "compute_type": self.compute_type,
            "sample_seconds": self.sample_seconds,
            "sample_start_seconds": self.sample_start_seconds,
            "segments": [
                {
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text,
                    "avg_logprob": segment.avg_logprob,
                    "no_speech_prob": segment.no_speech_prob,
                    "words": [
                        {
                            "start": word.start,
                            "end": word.end,
                            "word": word.word,
                            "probability": word.probability,
                        }
                        for word in (segment.words or [])
                    ],
                }
                for segment in provider_segments
            ],
        }
        try:
            version = importlib.metadata.version("faster-whisper")
        except importlib.metadata.PackageNotFoundError:
            version = None
        return TranscriptionResult(
            text=text,
            segments=segments,
            provider="faster-whisper",
            model=self.model_name,
            provider_version=version,
            raw=raw,
            is_complete=self.sample_seconds is None,
        )


def _confidence(avg_logprob: float | None) -> float | None:
    """Expose a review hint, not a calibrated probability."""
    if avg_logprob is None:
        return None
    import math

    return round(max(0.0, min(1.0, math.exp(avg_logprob))), 4)
