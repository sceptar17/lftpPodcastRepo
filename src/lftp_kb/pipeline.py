from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .analysis import AnalysisProvider
from .models import (
    DiscoveredEpisode,
    Episode,
    ModelRun,
    ProcessingEvent,
    ProcessingStatus,
    Topic,
    TopicEvidence,
    Transcript,
    TranscriptionResult,
    WordPressRecord,
)
from .qa import assess_transcript
from .related import find_related
from .render import episode_markdown, review_report, wordpress_html
from .repository import Repository
from .transcription import TranscriptionProvider
from .wordpress import WordPressClient


class Pipeline:
    def __init__(self, repository: Repository, transcriber: TranscriptionProvider,
                 analyzer: AnalysisProvider, wordpress: WordPressClient | None = None):
        self.repo = repository
        self.transcriber = transcriber
        self.analyzer = analyzer
        self.wordpress = wordpress

    def process(self, metadata: DiscoveredEpisode, audio_path: Path, force: bool = False) -> Episode:
        current_stage = "transcription"
        self.repo.save_state(metadata.episode_id, ProcessingStatus.NEW.value, current_stage)
        existing = self.repo.load_episode(metadata.episode_id)
        if existing and existing.status in {ProcessingStatus.COMPLETE, ProcessingStatus.DRAFT_CREATED} and not force:
            self.repo.log(ProcessingEvent(episode_id=metadata.episode_id, stage="pipeline", status="skipped",
                                          message="already processed; use --force to rerun"))
            return existing
        self.repo.log(ProcessingEvent(episode_id=metadata.episode_id, stage="transcription", status="started"))
        try:
            normalized_path = self.repo.root / "raw" / "transcripts" / f"{metadata.episode_id}.json"
            if normalized_path.exists() and not force:
                result = TranscriptionResult.model_validate_json(normalized_path.read_text())
                self.repo.log(ProcessingEvent(episode_id=metadata.episode_id, stage="transcription",
                                              status="skipped", message="using normalized checkpoint"))
            else:
                result = self.transcriber.transcribe(audio_path, metadata.episode_id)
            raw_path = f"raw/provider-output/{metadata.episode_id}-{result.provider}.json"
            self.repo.atomic_json(raw_path, result.raw)
            self.repo.atomic_json(f"raw/transcripts/{metadata.episode_id}.json", result.model_dump(mode="json"))
            self.repo.atomic_text(f"raw/transcripts/{metadata.episode_id}.txt", result.text + "\n")
            self.repo.save_state(metadata.episode_id, ProcessingStatus.TRANSCRIBED.value, "analysis")
            self.repo.log(ProcessingEvent(episode_id=metadata.episode_id, stage="transcription", status="succeeded"))
            current_stage = "analysis"
            analysis = self.analyzer.analyze(metadata.title, result.segments)
            is_fixture = result.provider == "fixture"
            qa = assess_transcript(result.segments, result.is_complete)
            qa.issues.extend(analysis.qa_issues)
            qa.low_confidence_items.extend(analysis.low_confidence_items)
            if analysis.qa_issues and qa.status == "pass":
                qa.status = "warning"
            if is_fixture:
                qa.status = "warning"
                qa.manual_review_flags.append(
                    "Prototype fixture contains only a description-derived excerpt, not a full audio transcript."
                )
            episode = Episode(
                episode_id=metadata.episode_id, title=metadata.title,
                episode_number=metadata.episode_number, publication_date=metadata.publication_date,
                source_rss_url=metadata.source_rss_url, source_page_url=metadata.source_page_url,
                audio_url=metadata.audio_url, artwork_url=metadata.artwork_url,
                original_rss_description=metadata.description,
                transcript=Transcript(text=result.text, segments=result.segments, provider=result.provider,
                                      model=result.model, provider_version=result.provider_version,
                                      raw_output_path=raw_path, is_complete=result.is_complete),
                semantic_sections=analysis.sections, episode_summary=analysis.episode_summary,
                primary_topics=analysis.primary_topics, secondary_topics=analysis.secondary_topics,
                model_runs=[ModelRun(stage="transcription", provider=result.provider, model=result.model,
                                     version=result.provider_version,
                                     processed_at=datetime.now(UTC))] + analysis.model_runs,
                processing_date=datetime.now(UTC), qa=qa,
                status=ProcessingStatus.NEEDS_REVIEW if qa.status != "pass" else ProcessingStatus.ANALYZED,
            )
            corpus = list(self.repo.episodes())
            episode.related_episodes = find_related(episode, corpus)
            self.repo.save_episode(episode)
            self.repo.save_state(metadata.episode_id, episode.status.value, "render")
            self.repo.atomic_text(f"episodes/{episode.episode_id}.md", episode_markdown(episode))
            self.repo.atomic_text(f"reports/{episode.episode_id}-review.md", review_report(episode))
            self.repo.atomic_text(f"state/wordpress-drafts/{episode.episode_id}.html", wordpress_html(episode))
            if self.wordpress:
                current_stage = "wordpress"
                content = wordpress_html(episode)
                response = self.wordpress.create_or_update_draft(
                    episode.title, content, episode.wordpress.post_id,
                )
                episode.wordpress = WordPressRecord(
                    post_id=int(response["id"]), draft_url=response.get("link"), status="draft",
                    content_hash=self.repo.sha256(content),
                )
                episode.status = ProcessingStatus.DRAFT_CREATED
                self.repo.save_episode(episode)
                self.repo.save_state(metadata.episode_id, episode.status.value, "complete")
            self.repo.log(ProcessingEvent(episode_id=metadata.episode_id, stage="pipeline", status="succeeded"))
            return episode
        except Exception as exc:
            self.repo.save_state(metadata.episode_id, ProcessingStatus.FAILED.value, current_stage, str(exc))
            self.repo.log(ProcessingEvent(episode_id=metadata.episode_id, stage="pipeline", status="failed",
                                          message=str(exc)))
            raise


def refresh_relationships(repository: Repository) -> None:
    corpus = list(repository.episodes())
    for episode in corpus:
        episode.related_episodes = find_related(episode, corpus)
        repository.save_episode(episode)
        repository.atomic_text(f"episodes/{episode.episode_id}.md", episode_markdown(episode))
        repository.atomic_text(f"reports/{episode.episode_id}-review.md", review_report(episode))
    refresh_topics(repository, corpus)


def refresh_topics(repository: Repository, corpus: list[Episode] | None = None) -> None:
    from .render import topic_markdown
    corpus = corpus or list(repository.episodes())
    grouped: dict[str, Topic] = {}
    for episode in corpus:
        for section in episode.semantic_sections:
            for item in section.topics:
                slug = "-".join(item.text.lower().split())
                if slug not in grouped:
                    grouped[slug] = Topic(
                        topic_id=slug, slug=slug, canonical_name=item.text, aliases=[],
                        description=f"Canonical topic for discussions of {item.text.lower()}.",
                        date_first_encountered=episode.publication_date.date(),
                        date_last_updated=episode.processing_date.date(),
                    )
                topic = grouped[slug]
                topic.date_first_encountered = min(topic.date_first_encountered, episode.publication_date.date())
                topic.date_last_updated = max(topic.date_last_updated, episode.processing_date.date())
                topic.episode_sections.append(TopicEvidence(
                    episode_id=episode.episode_id, section_id=section.section_id, evidence=item.evidence,
                ))
    for topic in grouped.values():
        repository.save_topic(topic)
        repository.atomic_text(f"topics/{topic.slug}.md", topic_markdown(topic))
