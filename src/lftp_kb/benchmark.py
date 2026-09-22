from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from .repository import Repository
from .transcription import FasterWhisperTranscriptionProvider


def create_run(repository: Repository, audio_path: Path, model: str,
               sample_seconds: int) -> dict:
    run_id = f"local-{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:6]}"
    comparable = [run["realtime_factor"] for run in load_runs(repository)
                  if run.get("model") == model and run.get("realtime_factor") is not None]
    estimated_seconds = round(median(comparable) * sample_seconds) if comparable else None
    payload = {
        "run_id": run_id,
        "status": "queued",
        "provider": "faster-whisper",
        "model": model,
        "device": "cpu",
        "compute_type": "int8",
        "audio_path": str(audio_path),
        "audio_name": audio_path.name,
        "sample_seconds": sample_seconds,
        "created_at": datetime.now(UTC).isoformat(),
        "started_at": None,
        "updated_at": None,
        "finished_at": None,
        "stage": "Waiting to start",
        "current_label": audio_path.name,
        "total": sample_seconds,
        "processed": 0,
        "estimated_seconds": estimated_seconds,
        "elapsed_seconds": None,
        "realtime_factor": None,
        "word_count": None,
        "segment_count": None,
        "transcript_path": None,
        "error": None,
        "speaker_note": (
            "Speaker names are intentionally unassigned. Similar voices, including twins, "
            "require a separate diarization and human-verification step."
        ),
    }
    repository.atomic_json(f"state/transcription-benchmarks/{run_id}.json", payload)
    return payload


def execute_run(repository: Repository, run_id: str) -> None:
    state_path = repository.root / "state" / "transcription-benchmarks" / f"{run_id}.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload.update(status="running", started_at=datetime.now(UTC).isoformat(),
                   updated_at=datetime.now(UTC).isoformat(), stage="Loading transcription model")
    repository.atomic_json(f"state/transcription-benchmarks/{run_id}.json", payload)
    started = time.perf_counter()
    try:
        provider = FasterWhisperTranscriptionProvider(
            model=payload["model"], device="cpu", compute_type="int8",
            sample_seconds=payload["sample_seconds"],
        )
        payload.update(stage="Transcribing audio", updated_at=datetime.now(UTC).isoformat())
        repository.atomic_json(f"state/transcription-benchmarks/{run_id}.json", payload)
        result = provider.transcribe(Path(payload["audio_path"]), run_id)
        elapsed = time.perf_counter() - started
        transcript_relative = f"raw/provider-output/benchmarks/{run_id}.json"
        repository.atomic_json(transcript_relative, result.model_dump(mode="json"))
        audio_seconds = min(
            float(result.raw.get("duration") or payload["sample_seconds"]),
            float(payload["sample_seconds"]),
        )
        payload.update(
            status="complete", finished_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(), stage="Complete",
            processed=payload["sample_seconds"],
            elapsed_seconds=round(elapsed, 2),
            realtime_factor=round(elapsed / audio_seconds, 3) if audio_seconds else None,
            word_count=len(result.text.split()), segment_count=len(result.segments),
            transcript_path=transcript_relative,
        )
    except Exception as error:  # noqa: BLE001 - background failures must be persisted for the UI
        payload.update(
            status="failed", finished_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(), stage="Failed",
            elapsed_seconds=round(time.perf_counter() - started, 2),
            error=f"{type(error).__name__}: {error}",
        )
    repository.atomic_json(f"state/transcription-benchmarks/{run_id}.json", payload)


def load_runs(repository: Repository) -> list[dict]:
    directory = repository.root / "state" / "transcription-benchmarks"
    runs = []
    for path in directory.glob("*.json") if directory.exists() else []:
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(runs, key=lambda value: value["created_at"], reverse=True)


def load_transcript(repository: Repository, run: dict) -> dict | None:
    if not run.get("transcript_path"):
        return None
    path = repository.root / run["transcript_path"]
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
