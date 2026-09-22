from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import DeterministicAnalysisProvider, OpenAIAnalysisProvider
from .audio import download_audio
from .config import Settings
from .models import DiscoveredEpisode
from .pipeline import Pipeline, refresh_relationships
from .repository import Repository
from .rss import fetch_rss
from .transcription import FixtureTranscriptionProvider, OpenAITranscriptionProvider
from .wordpress import WordPressClient


def _providers(settings: Settings, fixture_dir: Path | None):
    if fixture_dir:
        return FixtureTranscriptionProvider(fixture_dir), DeterministicAnalysisProvider()
    transcriber = OpenAITranscriptionProvider(settings.openai_transcription_model)
    analyzer = OpenAIAnalysisProvider(settings.openai_analysis_model)
    return transcriber, analyzer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lftp-kb")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover", help="Fetch and parse the RSS feed")
    discover.add_argument("--limit", type=int, default=5)
    process = sub.add_parser("process", help="Process selected feed episodes")
    process.add_argument("--limit", type=int, default=3)
    process.add_argument("--force", action="store_true")
    sample = sub.add_parser("sample", help="Run bundled, no-cost fixture episodes")
    sample.add_argument("--fixture-dir", type=Path, default=Path("fixtures/transcripts"))
    compare = sub.add_parser("compare-transcripts", help="Compare normalized provider output")
    compare.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    repo = Repository(settings.root)
    if args.command == "discover":
        body, episodes = fetch_rss(settings.rss_url, settings.http_timeout_seconds)
        repo.atomic_text("raw/rss/latest.xml", body.decode(errors="replace"))
        repo.atomic_json("state/discovered.json", [e.model_dump(mode="json") for e in episodes])
        print(json.dumps([e.model_dump(mode="json") for e in episodes[:args.limit]], indent=2))
        return 0
    if args.command == "compare-transcripts":
        from .transcript_compare import compare_files
        print(json.dumps(compare_files(args.files), indent=2))
        return 0
    fixture_dir = args.fixture_dir if args.command == "sample" else None
    transcriber, analyzer = _providers(settings, fixture_dir)
    wordpress = None
    if settings.create_wordpress_drafts:
        wordpress = WordPressClient(settings.wordpress_base_url, settings.wordpress_username,
                                    settings.wordpress_application_password, settings.http_timeout_seconds)
    pipeline = Pipeline(repo, transcriber, analyzer, wordpress)
    if args.command == "sample":
        manifest = json.loads(
            (fixture_dir.parent / "sample-episodes.json").read_text(encoding="utf-8")
        )
        selected = [DiscoveredEpisode.model_validate(item) for item in manifest]
    else:
        _, selected = fetch_rss(settings.rss_url, settings.http_timeout_seconds)
        selected = selected[:args.limit]
    for metadata in selected:
        audio = Path("fixture.audio") if fixture_dir else download_audio(
            metadata.audio_url, settings.root / "audio-cache", settings.http_timeout_seconds
        )
        episode = pipeline.process(metadata, audio, getattr(args, "force", False))
        print(f"{episode.episode_id}: {episode.status.value}")
    refresh_relationships(repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
