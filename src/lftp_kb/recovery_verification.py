from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile

from .content_check import content_similarity
from .fingerprinting import _fingerprint_similarity, _fpcalc
from .transcription import FasterWhisperTranscriptionProvider

YOUTUBE_LEADS = (
    {
        "source_id": "youtube-xhpowT-hmmU",
        "media_id": "xhpowT-hmmU",
        "date": "2011-05-21",
        "episode_hint": None,
        "title": "Judgment Day? Follow Up Discussion",
        "url": "https://www.youtube.com/watch?v=xhpowT-hmmU",
    },
    {
        "source_id": "youtube-A_K_ejrKBns",
        "media_id": "A_K_ejrKBns",
        "date": "2011-05-21",
        "episode_hint": None,
        "title": "Judgment Day? Interview with Steve Brooks",
        "url": "https://www.youtube.com/watch?v=A_K_ejrKBns",
    },
    {
        "source_id": "youtube-PqNW44cXLHw",
        "media_id": "PqNW44cXLHw",
        "date": "2017-07-10",
        "episode_hint": 21,
        "title": "Live From The Path: 2017 E21",
        "url": "https://www.youtube.com/watch?v=PqNW44cXLHw",
    },
    {
        "source_id": "youtube-UrSIDWmZOBc",
        "media_id": "UrSIDWmZOBc",
        "date": "2018-05-21",
        "episode_hint": 10,
        "title": "Live From The Path: 2018 E10",
        "url": "https://www.youtube.com/watch?v=UrSIDWmZOBc",
    },
)


@dataclass(frozen=True)
class CatalogCandidate:
    canonical_date: str
    proposed_episode_number: int | None
    title: str
    status: str
    preferred_local_file: str
    rss_episode_id: str


def read_catalog(path: Path) -> list[CatalogCandidate]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return [
        CatalogCandidate(
            canonical_date=row.get("canonical_date", ""),
            proposed_episode_number=_integer(row.get("proposed_episode_number")),
            title=row.get("title", ""),
            status=row.get("status", ""),
            preferred_local_file=row.get("preferred_local_file", ""),
            rss_episode_id=row.get("rss_episode_id", ""),
        )
        for row in rows
    ]


def nearby_candidates(
    rows: list[CatalogCandidate], lead_date: str, episode_hint: int | None, day_window: int = 21
) -> list[CatalogCandidate]:
    """Return a deliberately narrow comparison set, not a whole-archive scan."""
    target = date.fromisoformat(lead_date)
    ranked: list[tuple[tuple[int, int, str], CatalogCandidate]] = []
    for row in rows:
        if not row.preferred_local_file or not row.canonical_date:
            continue
        try:
            candidate_date = date.fromisoformat(row.canonical_date)
        except ValueError:
            continue
        if candidate_date.year != target.year:
            continue
        delta = abs((candidate_date - target).days)
        number_match = episode_hint is not None and row.proposed_episode_number == episode_hint
        if delta > day_window and not number_match:
            continue
        ranked.append(((0 if number_match else 1, delta, row.preferred_local_file), row))
    return [row for _, row in sorted(ranked)[:3]]


def classify_comparison(
    acoustic_similarity: float | None,
    transcript_similarity: float | None,
    duration_ratio: float | None,
) -> str:
    if acoustic_similarity is not None and acoustic_similarity >= 0.82:
        return "same-recording"
    if transcript_similarity is not None and transcript_similarity >= 0.55:
        return "same-show-content"
    if (
        acoustic_similarity is not None
        and acoustic_similarity < 0.58
        and transcript_similarity is not None
        and transcript_similarity <= 0.08
    ):
        return "different-content"
    if duration_ratio is not None and duration_ratio < 0.2:
        return "unlikely-full-match"
    return "manual-review"


class RecoveryVerifier:
    def __init__(self, repository_root: Path, audio_root: Path):
        self.root = repository_root
        self.audio_root = audio_root
        self.output = self.root / "catalog" / "finalization" / "recovery-verification"
        self.cache = self.root / "raw" / "recovery-cache" / "youtube"
        self.output.mkdir(parents=True, exist_ok=True)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.fpcalc = _find_fpcalc()
        self.transcriber: FasterWhisperTranscriptionProvider | None = None

    def run(self) -> dict[str, Any]:
        catalog_path = (
            self.root / "catalog" / "finalization" / "research" / "master-catalog-proposal.csv"
        )
        rows = read_catalog(catalog_path)
        results: list[dict[str, Any]] = []

        print("[1/3] Comparing the two July 2010 local candidates...", flush=True)
        results.append(
            self._compare_local_pair(
                "dma-2010-07-19",
                "Tattoos, Fistacuffs, and Milk Duds: Guaranteed Regrets",
                Path("2010/20100720.mp3"),
                Path("2010/20100720-03.mp3"),
            )
        )

        print("[2/3] Sampling the possible Tyson vs. Holyfield local recovery...", flush=True)
        results.append(self._historical_identity_check())

        print(
            "[3/3] Checking four YouTube recovery leads against nearby local audio...", flush=True
        )
        for index, lead in enumerate(YOUTUBE_LEADS, start=1):
            print(f"      YouTube lead {index}/4: {lead['title']}", flush=True)
            results.append(self._youtube_check(lead, rows))

        payload = {
            "schema_version": "1.0.0",
            "generated_at": datetime.now(UTC).isoformat(),
            "audio_root": str(self.audio_root),
            "method": {
                "scope": "focused recovery candidates only",
                "chromaprint_available": self.fpcalc is not None,
                "transcription": (
                    "faster-whisper tiny.en; public-source minutes 2-5 are compared with "
                    "local minutes 2-5, 20-23, and 40-43 when available"
                ),
                "mutation": "none",
            },
            "results": results,
        }
        (self.output / "recovery-verification.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._write_csv(results)
        self._write_markdown(payload)
        return payload

    def _compare_local_pair(
        self, source_id: str, title: str, left_relative: Path, right_relative: Path
    ) -> dict[str, Any]:
        left, right = self.audio_root / left_relative, self.audio_root / right_relative
        result: dict[str, Any] = {
            "source_id": source_id,
            "source_type": "historical-page-plus-local-pair",
            "title": title,
            "source_url": (
                "https://web.archive.org/web/20101123145012/"
                "http://www.desmoinesamplified.com/show_date.asp?showid=56&id=259"
            ),
            "candidates": [str(left_relative), str(right_relative)],
            "comparisons": [],
            "notes": [],
        }
        comparison = self._compare_audio(left, right)
        comparison.update({"left": str(left_relative), "right": str(right_relative)})
        result["comparisons"].append(comparison)
        result["classification"] = comparison["classification"]
        if comparison["classification"] in {"same-recording", "same-show-content"}:
            preferred = _preferred_variant(left, right)
            result["recommendation"] = (
                f"Treat these as variants of one broadcast; provisionally retain "
                f"{preferred.relative_to(self.audio_root)} as master and archive the other."
            )
        else:
            result["recommendation"] = "Retain both until the report samples are reviewed."
        return result

    def _historical_identity_check(self) -> dict[str, Any]:
        relative = Path("2010/20101015.mp3")
        path = self.audio_root / relative
        result: dict[str, Any] = {
            "source_id": "dma-2010-10-11",
            "source_type": "historical-page-without-source-audio",
            "title": "Tyson vs. Holyfield",
            "source_url": (
                "https://web.archive.org/web/20101123145012/"
                "http://www.desmoinesamplified.com/show_date.asp?showid=56&id=798"
            ),
            "candidates": [str(relative)],
            "comparisons": [],
            "classification": "identity-hypothesis",
            "recommendation": (
                "Do not create a second missing-audio episode yet; review the 2010-10-15 sample "
                "as the leading recovery candidate. Exact identity cannot be proved because the "
                "archived page has no surviving comparison audio."
            ),
            "notes": [],
        }
        if not path.is_file():
            result["classification"] = "local-candidate-missing"
            result["notes"].append(f"Expected local file was not found: {path}")
            return result
        try:
            text = self._transcribe_sample(path, "local-20101015", 120)
            result["sample_text"] = text[:2400]
            title_mentions = sorted(
                set(re.findall(r"\b(?:tyson|holyfield|boxing|boxer)\b", text, re.IGNORECASE))
            )
            result["title_keyword_mentions"] = title_mentions
            if title_mentions:
                result["classification"] = "plausible-title-content-match"
                result["notes"].append(
                    "The rough sample contains language associated with the historical title."
                )
        except Exception as error:  # noqa: BLE001 - report isolated optional-tool failures
            result["notes"].append(
                f"Sample transcription unavailable: {type(error).__name__}: {error}"
            )
        return result

    def _youtube_check(self, lead: dict[str, Any], rows: list[CatalogCandidate]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source_id": lead["source_id"],
            "source_type": "youtube-audio",
            "title": lead["title"],
            "source_url": lead["url"],
            "publication_date": lead["date"],
            "candidates": [],
            "comparisons": [],
            "notes": [],
        }
        candidates = nearby_candidates(rows, lead["date"], lead["episode_hint"])
        result["candidates"] = [candidate.preferred_local_file for candidate in candidates]
        try:
            remote = self._download_youtube(lead["media_id"], lead["url"])
            result["cached_source_audio"] = str(remote.relative_to(self.root))
        except Exception as error:  # noqa: BLE001 - preserve a useful retry report
            result["classification"] = "source-download-failed"
            result["recommendation"] = (
                "Retry this source later; do not infer that the episode is absent."
            )
            result["notes"].append(f"{type(error).__name__}: {error}")
            return result

        for candidate in candidates:
            local = self.audio_root / Path(candidate.preferred_local_file)
            comparison = self._compare_audio(remote, local, scan_right_windows=True)
            comparison.update(
                {
                    "local_file": candidate.preferred_local_file,
                    "canonical_date": candidate.canonical_date,
                    "catalog_title": candidate.title,
                    "catalog_status": candidate.status,
                    "rss_episode_id": candidate.rss_episode_id,
                }
            )
            result["comparisons"].append(comparison)

        order = {
            "same-recording": 0,
            "same-show-content": 1,
            "manual-review": 2,
            "unlikely-full-match": 3,
            "different-content": 4,
            "comparison-failed": 5,
        }
        result["comparisons"].sort(key=lambda item: order.get(item["classification"], 9))
        best = result["comparisons"][0] if result["comparisons"] else None
        result["classification"] = best["classification"] if best else "no-local-candidate"
        if best and best["classification"] in {"same-recording", "same-show-content"}:
            result["recommendation"] = (
                f"Link this public source to {best['local_file']}; do not create a new episode."
            )
        elif best:
            result["recommendation"] = (
                "Treat this as a possible missing episode until the best comparison is reviewed."
            )
        else:
            result["recommendation"] = "Treat this as a possible missing episode."
        return result

    def _compare_audio(
        self, left: Path, right: Path, *, scan_right_windows: bool = False
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "classification": "manual-review",
            "acoustic_similarity": None,
            "transcript_similarity": None,
            "left_duration_seconds": _duration(left),
            "right_duration_seconds": _duration(right),
            "notes": [],
        }
        if not left.is_file() or not right.is_file():
            result["classification"] = "comparison-failed"
            result["notes"].append(
                f"Missing file(s): {left if not left.is_file() else ''} "
                f"{right if not right.is_file() else ''}".strip()
            )
            return result
        left_duration = result["left_duration_seconds"]
        right_duration = result["right_duration_seconds"]
        duration_ratio = None
        if left_duration and right_duration:
            duration_ratio = min(left_duration, right_duration) / max(left_duration, right_duration)
        result["duration_ratio"] = round(duration_ratio, 4) if duration_ratio else None

        if self.fpcalc:
            try:
                left_fp = _fpcalc(self.fpcalc, left)
                right_fp = _fpcalc(self.fpcalc, right)
                result["acoustic_similarity"] = _fingerprint_similarity(left_fp, right_fp)
            except Exception as error:  # noqa: BLE001
                result["notes"].append(f"Chromaprint failed: {type(error).__name__}: {error}")
        else:
            result["notes"].append("Chromaprint fpcalc was not found.")

        acoustic = result["acoustic_similarity"]
        if acoustic is None or acoustic < 0.82:
            try:
                left_text = self._transcribe_sample(left, f"sample-{_safe_id(left)}-120", 120)
                right_starts = [120]
                if scan_right_windows:
                    duration = result["right_duration_seconds"]
                    right_starts.extend(
                        start
                        for start in (1200, 2400)
                        if duration is None or duration > start + 180
                    )
                right_samples = [
                    (
                        start,
                        self._transcribe_sample(right, f"sample-{_safe_id(right)}-{start}", start),
                    )
                    for start in right_starts
                ]
                scored = [
                    (content_similarity(left_text, text), start, text)
                    for start, text in right_samples
                ]
                best_score, best_start, right_text = max(scored, key=lambda item: item[0])
                result["transcript_similarity"] = best_score
                result["best_right_sample_start_seconds"] = best_start
                result["left_sample_text"] = left_text[:1200]
                result["right_sample_text"] = right_text[:1200]
            except Exception as error:  # noqa: BLE001
                result["notes"].append(
                    f"Rough transcript comparison failed: {type(error).__name__}: {error}"
                )
        result["classification"] = classify_comparison(
            result["acoustic_similarity"], result["transcript_similarity"], duration_ratio
        )
        return result

    def _transcribe_sample(self, path: Path, identifier: str, start_seconds: int) -> str:
        sample_path = self.output / "samples" / f"{identifier}.json"
        if sample_path.exists():
            return json.loads(sample_path.read_text(encoding="utf-8"))["text"]
        if self.transcriber is None:
            print(
                "      Loading the local tiny.en model (first use may take a few minutes)...",
                flush=True,
            )
            self.transcriber = FasterWhisperTranscriptionProvider(
                model="tiny.en",
                device="cpu",
                compute_type="int8",
                sample_seconds=180,
                sample_start_seconds=start_seconds,
            )
        self.transcriber.sample_start_seconds = start_seconds
        transcription = self.transcriber.transcribe(path, identifier)
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        sample_path.write_text(
            json.dumps(
                {
                    "provider": transcription.provider,
                    "model": transcription.model,
                    "sample_start_seconds": start_seconds,
                    "sample_seconds": 180,
                    "source_path": str(path),
                    "text": transcription.text,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return transcription.text

    def _download_youtube(self, media_id: str, url: str) -> Path:
        cached = sorted(self.cache.glob(f"{media_id}.*"))
        cached = [path for path in cached if path.suffix not in {".part", ".ytdl", ".json"}]
        if cached:
            return cached[0]
        template = str(self.cache / f"{media_id}.%(ext)s")
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--no-progress",
            "--format",
            "bestaudio/best",
            "--output",
            template,
            "--print",
            "after_move:filepath",
            url,
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=900, check=False
        )
        if completed.returncode:
            detail = (completed.stderr or completed.stdout).strip()[-2000:]
            raise RuntimeError(detail or f"yt-dlp exited with {completed.returncode}")
        paths = [Path(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
        path = paths[-1] if paths else None
        if path is None or not path.is_file():
            raise RuntimeError("yt-dlp completed without returning a downloaded file")
        return path

    def _write_csv(self, results: list[dict[str, Any]]) -> None:
        path = self.output / "recovery-verification.csv"
        fields = [
            "source_id",
            "title",
            "classification",
            "best_local_candidate",
            "acoustic_similarity",
            "transcript_similarity",
            "recommendation",
            "source_url",
        ]
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            for result in results:
                best = result.get("comparisons", [{}])[0] if result.get("comparisons") else {}
                writer.writerow(
                    {
                        "source_id": result["source_id"],
                        "title": result["title"],
                        "classification": result["classification"],
                        "best_local_candidate": best.get("local_file") or best.get("right", ""),
                        "acoustic_similarity": best.get("acoustic_similarity"),
                        "transcript_similarity": best.get("transcript_similarity"),
                        "recommendation": result.get("recommendation", ""),
                        "source_url": result.get("source_url", ""),
                    }
                )

    def _write_markdown(self, payload: dict[str, Any]) -> None:
        lines = [
            "# Focused recovery verification",
            "",
            f"Generated: `{payload['generated_at']}`",
            "",
            (
                "This is an evidence report, not an archive mutation. No audio was moved, "
                "deleted, renumbered, or inserted into the master catalog."
            ),
            "",
            "## Results",
            "",
            "| Source | Result | Best local candidate | Acoustic | Content |",
            "|---|---|---|---:|---:|",
        ]
        for result in payload["results"]:
            best = result.get("comparisons", [{}])[0] if result.get("comparisons") else {}
            candidate = (
                best.get("local_file")
                or best.get("right")
                or ", ".join(result.get("candidates", []))
            )
            acoustic = _percent(best.get("acoustic_similarity"))
            transcript = _percent(best.get("transcript_similarity"))
            title = str(result["title"]).replace("|", "\\|")
            lines.append(
                f"| [{title}]({result.get('source_url', '')}) | "
                f"`{result['classification']}` | {candidate or '—'} | {acoustic} | {transcript} |"
            )
        for result in payload["results"]:
            lines.extend(
                [
                    "",
                    f"## {result['title']}",
                    "",
                    f"**Assessment:** `{result['classification']}`",
                    "",
                    result.get("recommendation", "No automatic recommendation."),
                ]
            )
            for comparison in result.get("comparisons", []):
                candidate = comparison.get("local_file") or comparison.get("right") or "comparison"
                lines.extend(
                    [
                        "",
                        (
                            f"- `{candidate}` — {comparison['classification']}; acoustic "
                            f"{_percent(comparison.get('acoustic_similarity'))}; content "
                            f"{_percent(comparison.get('transcript_similarity'))}; duration ratio "
                            f"{_percent(comparison.get('duration_ratio'))}"
                        ),
                    ]
                )
            for note in result.get("notes", []):
                lines.append(f"- Note: {note}")
        lines.extend(
            [
                "",
                "## Interpretation rules",
                "",
                "- `same-recording`: Chromaprint similarity is at least 82%.",
                "- `same-show-content`: rough three-minute transcript similarity is at least 55%.",
                "- `identity-hypothesis`: source audio does not survive, so identity is not provable.",
                "- Download or tool failure never counts as evidence that an episode is missing.",
                "",
            ]
        )
        (self.output / "RECOVERY_VERIFICATION.md").write_text("\n".join(lines), encoding="utf-8")


def configured_audio_root(repository_root: Path) -> Path | None:
    value = os.getenv("LFTP_LOCAL_AUDIO_ROOT", "").strip()
    settings_path = repository_root / "state" / "app-settings.json"
    if not value and settings_path.exists():
        try:
            value = str(
                json.loads(settings_path.read_text(encoding="utf-8")).get("local_audio_root", "")
            ).strip()
        except (OSError, ValueError):
            pass
    return Path(value).expanduser().resolve() if value else None


def _find_fpcalc() -> str | None:
    configured = os.getenv("LFTP_FPCALC_PATH", "").strip()
    if configured and Path(configured).is_file():
        return configured
    return shutil.which("fpcalc")


def _duration(path: Path) -> float | None:
    if not path.is_file():
        return None
    try:
        audio = MutagenFile(path)
        return round(float(audio.info.length), 3) if audio and audio.info else None
    except (OSError, ValueError, AttributeError):
        return None


def _preferred_variant(left: Path, right: Path) -> Path:
    left_time = left.stat().st_mtime
    right_time = right.stat().st_mtime
    if left_time != right_time:
        return left if left_time > right_time else right
    return right if re.search(r"(?:-|_|\s)(?:\d+|[a-z])$", right.stem, re.IGNORECASE) else left


def _safe_id(path: Path) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")


def _integer(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _percent(value: float | None) -> str:
    return f"{value:.1%}" if value is not None else "—"
