from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from .models import (
    AnalysisResult,
    EvidencePointer,
    ModelRun,
    SemanticSection,
    SupportedText,
    TranscriptSegment,
)


def evidence(segment: TranscriptSegment) -> EvidencePointer:
    return EvidencePointer(segment_ids=[segment.segment_id], start_seconds=segment.start_seconds,
                           end_seconds=segment.end_seconds, excerpt=segment.text[:500])


class AnalysisProvider(ABC):
    @abstractmethod
    def analyze(self, title: str, segments: list[TranscriptSegment]) -> AnalysisResult: ...


TOPIC_RULES = {
    "pastoral-authority": ("Pastoral authority", {"pastor", "authority", "leader", "hebrews"}),
    "discernment": ("Discernment", {"discern", "guidance", "spirit", "scripture"}),
    "church-community": ("Church community", {"church", "serve", "community", "encouragement"}),
    "relationships": ("Relationships", {"relationship", "romance", "wife", "husband"}),
    "perseverance": ("Perseverance", {"perseverance", "endure", "continue"}),
}


class DeterministicAnalysisProvider(AnalysisProvider):
    """Conservative test provider: extracts only explicit, evidenced material."""

    def analyze(self, title: str, segments: list[TranscriptSegment]) -> AnalysisResult:
        if not segments:
            raise ValueError("cannot analyze an empty transcript")
        topics: dict[str, SupportedText] = {}
        scripture: list[SupportedText] = []
        claims: list[SupportedText] = []
        questions: list[SupportedText] = []
        for segment in segments:
            lower = segment.text.lower()
            for slug, (name, words) in TOPIC_RULES.items():
                if any(word in lower for word in words) and slug not in topics:
                    topics[slug] = SupportedText(text=name, evidence=[evidence(segment)], confidence=0.72)
            for match in re.finditer(r"\b(?:[1-3]\s*)?[A-Z][a-z]+\s+\d{1,3}(?::\d{1,3}(?:[-–]\d{1,3})?)?\b", segment.text):
                scripture.append(SupportedText(text=match.group(0), evidence=[evidence(segment)], confidence=0.75))
            if "?" in segment.text:
                questions.append(SupportedText(text=segment.text, evidence=[evidence(segment)], confidence=0.65))
            else:
                claims.append(SupportedText(text=segment.text, evidence=[evidence(segment)], confidence=0.65))
        section = SemanticSection(
            section_id="section-001", title="Episode overview",
            start_seconds=segments[0].start_seconds, end_seconds=segments[-1].end_seconds,
            summary=SupportedText(text=" ".join(s.text for s in segments),
                                  evidence=[evidence(s) for s in segments], confidence=0.65),
            topics=list(topics.values()), scripture_references=scripture,
            questions=questions, claims=claims,
        )
        return AnalysisResult(
            sections=[section], episode_summary=section.summary,
            primary_topics=list(topics.values())[:3], secondary_topics=list(topics.values())[3:],
            qa_issues=["Deterministic fixture analysis is not a substitute for full LLM review."],
            model_runs=[ModelRun(stage="analysis", provider="deterministic", model="rules-v1",
                                 processed_at=datetime.now(UTC))],
        )


class _AnalysisEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[SemanticSection]
    episode_summary: SupportedText
    primary_topics: list[SupportedText]
    secondary_topics: list[SupportedText]
    qa_issues: list[str]
    low_confidence_items: list[str]


class OpenAIAnalysisProvider(AnalysisProvider):
    """Focused structured pass. Verification remains deterministic in Episode validation."""

    def __init__(self, model: str = "gpt-6-astra"):
        from openai import OpenAI
        self.client = OpenAI()
        self.model = model

    def analyze(self, title: str, segments: list[TranscriptSegment]) -> AnalysisResult:
        transcript = "\n".join(
            f"[{s.segment_id} {s.start_seconds:.2f}-{s.end_seconds:.2f}] {s.text}" for s in segments
        )
        prompt = f"""Analyze this podcast transcript conservatively.
Episode: {title}

Every summary, topic, reference, person, work, question, and claim MUST quote evidence and
reference only segment IDs supplied below. Do not infer theological conclusions beyond what
the speakers explicitly say. Preserve questions as questions. Use one or more semantic sections.

TRANSCRIPT\n{transcript}"""
        completion = self.client.chat.completions.parse(
            model=self.model,
            messages=[
                {"role": "system", "content": "You create auditable podcast knowledge records."},
                {"role": "user", "content": prompt},
            ],
            response_format=_AnalysisEnvelope,
        )
        parsed = completion.choices[0].message.parsed
        if parsed is None:
            raise ValueError("analysis model returned no parsed result")
        return AnalysisResult(
            **parsed.model_dump(),
            model_runs=[ModelRun(stage="analysis", provider="openai", model=self.model,
                                 processed_at=datetime.now(UTC))],
        )

