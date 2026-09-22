from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from lftp_kb.models import (
    Episode,
    EvidencePointer,
    ModelRun,
    ProcessingStatus,
    QAResult,
    SemanticSection,
    SupportedText,
    Transcript,
    TranscriptSegment,
)


def test_canonical_record_rejects_unknown_evidence_segment():
    segment = TranscriptSegment(segment_id="s1", start_seconds=0, end_seconds=10, text="Evidence")
    bad = SupportedText(text="Claim", confidence=.8, evidence=[EvidencePointer(
        segment_ids=["missing"], start_seconds=0, end_seconds=10, excerpt="Evidence")])
    with pytest.raises(ValidationError):
        Episode(
            episode_id="e1", title="Title", publication_date=datetime.now(UTC),
            source_rss_url="https://example.com/feed", audio_url="https://example.com/a.mp3",
            original_rss_description="", transcript=Transcript(text="Evidence", segments=[segment],
                provider="test", model="test", raw_output_path="raw.json"),
            semantic_sections=[SemanticSection(section_id="x", title="X", start_seconds=0,
                end_seconds=10, summary=bad)], episode_summary=bad, primary_topics=[],
            model_runs=[ModelRun(stage="analysis", provider="test", model="test",
                processed_at=datetime.now(UTC))], processing_date=datetime.now(UTC),
            qa=QAResult(status="pass"), status=ProcessingStatus.ANALYZED)

