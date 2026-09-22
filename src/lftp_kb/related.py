from __future__ import annotations

import math
import re
from collections import Counter

from .models import Episode, RelatedEpisode


def _tokens(episode: Episode) -> Counter[str]:
    text = " ".join([episode.title, episode.episode_summary.text] +
                    [topic.text for topic in episode.primary_topics + episode.secondary_topics])
    return Counter(t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 3)


def _cosine(a: Counter[str], b: Counter[str]) -> float:
    common = sum(a[k] * b[k] for k in a.keys() & b.keys())
    denominator = math.sqrt(sum(v * v for v in a.values()) * sum(v * v for v in b.values()))
    return common / denominator if denominator else 0.0


def find_related(target: Episode, corpus: list[Episode], limit: int = 5) -> list[RelatedEpisode]:
    target_topics = {t.text.lower() for t in target.primary_topics + target.secondary_topics}
    target_refs = {r.text.lower() for s in target.semantic_sections for r in s.scripture_references}
    found: list[RelatedEpisode] = []
    for candidate in corpus:
        if candidate.episode_id == target.episode_id:
            continue
        candidate_topics = {t.text.lower() for t in candidate.primary_topics + candidate.secondary_topics}
        candidate_refs = {r.text.lower() for s in candidate.semantic_sections for r in s.scripture_references}
        shared_topics = sorted(target_topics & candidate_topics)
        shared_refs = sorted(target_refs & candidate_refs)
        semantic = _cosine(_tokens(target), _tokens(candidate))
        score = min(1.0, 0.25 * len(shared_topics) + 0.35 * len(shared_refs) + 0.4 * semantic)
        reasons = []
        if shared_topics:
            reasons.append("Shared substantial topics: " + ", ".join(shared_topics))
        if shared_refs:
            reasons.append("Shared Scripture references: " + ", ".join(shared_refs))
        if semantic >= 0.2:
            reasons.append(f"Transcript/topic semantic overlap ({semantic:.2f})")
        if reasons and score >= 0.2:
            found.append(RelatedEpisode(episode_id=candidate.episode_id, score=round(score, 3),
                                        reasons=reasons, shared_topics=shared_topics,
                                        shared_references=shared_refs))
    return sorted(found, key=lambda item: (-item.score, item.episode_id))[:limit]

