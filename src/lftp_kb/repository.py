from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .models import Episode, ProcessingEvent, Topic


class Repository:
    DIRECTORIES = (
        "episodes", "topics", "raw/transcripts", "raw/provider-output",
        "raw/rss", "logs", "reports", "audio-cache", "state",
    )

    def __init__(self, root: Path):
        self.root = root
        for directory in self.DIRECTORIES:
            (root / directory).mkdir(parents=True, exist_ok=True)

    def atomic_json(self, relative: str, payload: dict | list) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def atomic_text(self, relative: str, content: str) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def save_episode(self, episode: Episode) -> Path:
        return self.atomic_json(f"episodes/{episode.episode_id}.json", episode.model_dump(mode="json"))

    def save_topic(self, topic: Topic) -> Path:
        return self.atomic_json(f"topics/{topic.slug}.json", topic.model_dump(mode="json"))

    def save_state(self, episode_id: str, status: str, stage: str, message: str = "") -> Path:
        return self.atomic_json(f"state/{episode_id}.json", {
            "episode_id": episode_id, "status": status, "stage": stage, "message": message,
        })

    def load_episode(self, episode_id: str) -> Episode | None:
        path = self.root / "episodes" / f"{episode_id}.json"
        return Episode.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def episodes(self) -> Iterable[Episode]:
        for path in sorted((self.root / "episodes").glob("*.json")):
            yield Episode.model_validate_json(path.read_text(encoding="utf-8"))

    def log(self, event: ProcessingEvent) -> None:
        path = self.root / "logs" / "processing.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")

    @staticmethod
    def sha256(content: str | bytes) -> str:
        raw = content.encode() if isinstance(content, str) else content
        return hashlib.sha256(raw).hexdigest()
