from __future__ import annotations

from datetime import UTC, date, datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProcessingStatus(str, Enum):
    NEW = "new"
    TRANSCRIBED = "transcribed"
    ANALYZED = "analyzed"
    DRAFT_CREATED = "draft-created"
    NEEDS_REVIEW = "needs-review"
    FAILED = "failed"
    COMPLETE = "complete"


class EvidencePointer(StrictModel):
    segment_ids: list[str] = Field(min_length=1)
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(ge=0)]
    excerpt: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def ordered(self) -> EvidencePointer:
        if self.end_seconds < self.start_seconds:
            raise ValueError("evidence end_seconds precedes start_seconds")
        return self


class SupportedText(StrictModel):
    text: str = Field(min_length=1)
    evidence: list[EvidencePointer] = Field(min_length=1)
    confidence: Annotated[float, Field(ge=0, le=1)]


class TranscriptSegment(StrictModel):
    segment_id: str
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(ge=0)]
    text: str = Field(min_length=1)
    speaker: str | None = None
    confidence: Annotated[float | None, Field(ge=0, le=1)] = None

    @model_validator(mode="after")
    def ordered(self) -> TranscriptSegment:
        if self.end_seconds < self.start_seconds:
            raise ValueError("transcript segment end_seconds precedes start_seconds")
        return self


class Transcript(StrictModel):
    text: str
    segments: list[TranscriptSegment]
    provider: str
    model: str
    provider_version: str | None = None
    raw_output_path: str
    is_complete: bool = True


class SemanticSection(StrictModel):
    section_id: str
    title: str
    start_seconds: Annotated[float, Field(ge=0)]
    end_seconds: Annotated[float, Field(ge=0)]
    summary: SupportedText
    topics: list[SupportedText] = []
    scripture_references: list[SupportedText] = []
    people: list[SupportedText] = []
    works_media: list[SupportedText] = []
    questions: list[SupportedText] = []
    claims: list[SupportedText] = []


class RelatedEpisode(StrictModel):
    episode_id: str
    score: Annotated[float, Field(ge=0, le=1)]
    reasons: list[str] = Field(min_length=1)
    shared_topics: list[str] = []
    shared_references: list[str] = []


class ModelRun(StrictModel):
    stage: str
    provider: str
    model: str
    version: str | None = None
    processed_at: datetime
    input_units: int | None = None
    output_units: int | None = None


class QAResult(StrictModel):
    status: Literal["pass", "warning", "fail"]
    transcription_confidence: Annotated[float | None, Field(ge=0, le=1)] = None
    issues: list[str] = []
    low_confidence_items: list[str] = []
    manual_review_flags: list[str] = []


class WordPressRecord(StrictModel):
    post_id: int | None = None
    draft_url: str | None = None
    status: Literal["not-created", "draft"] = "not-created"
    content_hash: str | None = None


class Episode(StrictModel):
    schema_version: str = "1.0.0"
    episode_id: str
    title: str
    episode_number: int | None = None
    publication_date: datetime
    source_rss_url: str
    source_page_url: str | None = None
    audio_url: str
    artwork_url: str | None = None
    original_rss_description: str
    transcript: Transcript
    semantic_sections: list[SemanticSection]
    episode_summary: SupportedText
    primary_topics: list[SupportedText]
    secondary_topics: list[SupportedText] = []
    related_episodes: list[RelatedEpisode] = []
    model_runs: list[ModelRun]
    processing_date: datetime
    qa: QAResult
    status: ProcessingStatus
    wordpress: WordPressRecord = WordPressRecord()

    @model_validator(mode="after")
    def evidence_is_internal(self) -> Episode:
        segments = {s.segment_id: s for s in self.transcript.segments}
        supported: list[SupportedText] = [self.episode_summary]
        supported.extend(self.primary_topics)
        supported.extend(self.secondary_topics)
        for section in self.semantic_sections:
            supported.append(section.summary)
            supported.extend(section.topics)
            supported.extend(section.scripture_references)
            supported.extend(section.people)
            supported.extend(section.works_media)
            supported.extend(section.questions)
            supported.extend(section.claims)
        for item in supported:
            for pointer in item.evidence:
                missing = set(pointer.segment_ids) - segments.keys()
                if missing:
                    raise ValueError(f"evidence references unknown segments: {sorted(missing)}")
                selected = [segments[sid] for sid in pointer.segment_ids]
                if pointer.start_seconds < min(s.start_seconds for s in selected) - 0.01:
                    raise ValueError("evidence starts before referenced transcript segment")
                if pointer.end_seconds > max(s.end_seconds for s in selected) + 0.01:
                    raise ValueError("evidence ends after referenced transcript segment")
                source = " ".join(s.text for s in selected)
                normalize = lambda value: " ".join(value.lower().split())
                if normalize(pointer.excerpt) not in normalize(source):
                    raise ValueError(
                        "evidence excerpt is not present in referenced transcript segments"
                    )
        return self


class TopicEvidence(StrictModel):
    episode_id: str
    section_id: str
    evidence: list[EvidencePointer] = Field(min_length=1)


class Topic(StrictModel):
    schema_version: str = "1.0.0"
    topic_id: str
    slug: str
    canonical_name: str
    aliases: list[str] = []
    description: str
    related_topics: list[str] = []
    episode_sections: list[TopicEvidence] = []
    date_first_encountered: date
    date_last_updated: date


class ProcessingEvent(StrictModel):
    episode_id: str
    stage: str
    status: Literal["started", "succeeded", "failed", "skipped"]
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    message: str = ""
    attempt: int = 1
    details: dict[str, str | int | float | bool | None] = {}


class DiscoveredEpisode(StrictModel):
    episode_id: str
    guid: str
    title: str
    episode_number: int | None = None
    publication_date: datetime
    source_rss_url: str
    source_page_url: str | None = None
    audio_url: str
    artwork_url: str | None = None
    description: str = ""


class TranscriptionResult(StrictModel):
    text: str
    segments: list[TranscriptSegment]
    provider: str
    model: str
    provider_version: str | None = None
    raw: dict
    is_complete: bool = True


class AnalysisResult(StrictModel):
    sections: list[SemanticSection]
    episode_summary: SupportedText
    primary_topics: list[SupportedText]
    secondary_topics: list[SupportedText] = []
    qa_issues: list[str] = []
    low_confidence_items: list[str] = []
    model_runs: list[ModelRun]


class CatalogEvidence(StrictModel):
    source: str
    field: str
    value: str
    confidence: Literal["confirmed", "high", "medium", "low"]
    locator: str


class CatalogSources(StrictModel):
    local_files: list[str] = []
    rss_guid: str | None = None
    rss_episode_id: str | None = None
    r2_object_key: str | None = None
    wordpress_post_id: int | None = None


class EpisodeCandidate(StrictModel):
    schema_version: str = "1.0.0"
    candidate_id: str
    title: str | None = None
    episode_number: int | None = None
    episode_number_confidence: Literal["confirmed", "high", "medium", "low", "unknown"] = "unknown"
    overall_episode_number: int | None = None
    overall_episode_number_confidence: Literal["confirmed", "high", "medium", "low", "unknown"] = (
        "unknown"
    )
    publication_date: str | None = None
    date_precision: Literal["day", "month", "year", "unknown"] = "unknown"
    known_sequence_in_year: int | None = None
    sequence_is_provisional: bool = True
    duration_seconds: float | None = None
    file_size_bytes: int | None = None
    sources: CatalogSources = CatalogSources()
    evidence: list[CatalogEvidence] = []
    status: Literal["confirmed", "rss-only", "local-only", "uncertain", "conflict"]
    overall_confidence: Literal["confirmed", "high", "medium", "low"]
    possible_rss_episode_id: str | None = None
    conflicts: list[str] = []
    manual_review_flags: list[str] = []


class CatalogYearSummary(StrictModel):
    year: int | None
    candidate_count: int
    local_file_count: int
    rss_count: int
    numbered_count: int
    exact_date_count: int
    conflict_count: int


class EpisodeLedger(StrictModel):
    schema_version: str = "1.0.0"
    generated_at: datetime
    local_archive_root: str
    candidate_count: int
    candidates: list[EpisodeCandidate]
    years: list[CatalogYearSummary]


class AssetClaim(StrictModel):
    field: str
    value: str
    source: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    reason: str


class CatalogAsset(StrictModel):
    asset_id: str
    relative_path: str
    filename: str
    size_bytes: int
    duration_seconds: float | None = None
    sha256: str | None = None
    hash_status: Literal["complete", "not-required", "failed"] = "not-required"
    format: str
    embedded_title: str | None = None
    embedded_track_number: int | None = None
    embedded_date: str | None = None
    folder_year: int | None = None
    path_dates: list[str] = []
    annual_episode_numbers: list[int] = []
    overall_episode_numbers: list[int] = []
    claims: list[AssetClaim] = []
    metadata_issues: list[str] = []


class DuplicateProposal(StrictModel):
    proposal_id: str
    relationship: Literal["exact-copy", "likely-same-recording"]
    asset_ids: list[str] = Field(min_length=2)
    confidence: Annotated[float, Field(ge=0, le=1)]
    reasons: list[str] = Field(min_length=1)
    requires_listening: bool


class EpisodeMatchProposal(StrictModel):
    proposal_id: str
    asset_id: str
    rss_episode_id: str
    confidence: Annotated[float, Field(ge=0, le=1)]
    reasons: list[str] = Field(min_length=1)
    competing_confidence: Annotated[float | None, Field(ge=0, le=1)] = None
    competing_rss_episode_id: str | None = None
    recommendation: Literal["auto-link", "review", "insufficient"]


class ReconstructionReport(StrictModel):
    schema_version: str = "1.0.0"
    generated_at: datetime
    assets: list[CatalogAsset]
    duplicate_proposals: list[DuplicateProposal]
    match_proposals: list[EpisodeMatchProposal]
