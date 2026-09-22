from __future__ import annotations

from itertools import pairwise

from .models import QAResult, TranscriptSegment


def assess_transcript(segments: list[TranscriptSegment], is_complete: bool) -> QAResult:
    issues: list[str] = []
    flags: list[str] = []
    low: list[str] = []
    if not segments:
        return QAResult(status="fail", issues=["Transcript contains no segments."],
                        manual_review_flags=["Transcription must be rerun."])
    if not is_complete:
        flags.append("Transcript provider marked the transcript incomplete.")
    for previous, current in pairwise(segments):
        if current.start_seconds < previous.start_seconds:
            issues.append(f"Segments out of order at {current.segment_id}.")
        if current.start_seconds - previous.end_seconds > 30:
            issues.append(f"Gap over 30 seconds before {current.segment_id}.")
    values = [s.confidence for s in segments if s.confidence is not None]
    confidence = round(sum(values) / len(values), 3) if values else None
    for segment in segments:
        if segment.confidence is not None and segment.confidence < 0.7:
            low.append(f"{segment.segment_id} ({segment.confidence:.2f})")
    if confidence is None:
        flags.append("Provider did not supply segment confidence; spot-check audio manually.")
    status = "fail" if any("out of order" in issue for issue in issues) else (
        "warning" if issues or flags or low else "pass"
    )
    return QAResult(status=status, transcription_confidence=confidence, issues=issues,
                    low_confidence_items=low, manual_review_flags=flags)
