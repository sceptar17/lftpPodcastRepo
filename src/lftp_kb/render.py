from __future__ import annotations

import html

from .models import Episode, Topic


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def episode_markdown(episode: Episode) -> str:
    lines = [f"# {episode.title}", "", f"Published: {episode.publication_date.date().isoformat()}",
             f"Status: `{episode.status.value}`", "", episode.episode_summary.text, "", "## In this episode", ""]
    lines.extend(f"- {topic.text}" for topic in episode.primary_topics)
    for section in episode.semantic_sections:
        lines.extend(["", f"## {section.title} ({timestamp(section.start_seconds)}–{timestamp(section.end_seconds)})", "",
                      section.summary.text])
        if section.scripture_references:
            lines.extend(["", "Scripture: " + ", ".join(r.text for r in section.scripture_references)])
    if episode.related_episodes:
        lines.extend(["", "## Related episodes", ""])
        for relation in episode.related_episodes:
            lines.append(f"- `{relation.episode_id}` — {'; '.join(relation.reasons)}")
    lines.extend(["", "## Transcript", ""])
    for segment in episode.transcript.segments:
        speaker = f" **{segment.speaker}:**" if segment.speaker else ""
        lines.append(f"**[{timestamp(segment.start_seconds)}]**{speaker} {segment.text}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def topic_markdown(topic: Topic) -> str:
    lines = [f"# {topic.canonical_name}", "", topic.description, "", f"Slug: `{topic.slug}`", "",
             "## Supporting episode sections", ""]
    lines.extend(f"- `{item.episode_id}` / `{item.section_id}`" for item in topic.episode_sections)
    return "\n".join(lines) + "\n"


def wordpress_html(episode: Episode) -> str:
    parts = [f"<p><strong>Published:</strong> {episode.publication_date.date().isoformat()}</p>",
             f"<p>{html.escape(episode.episode_summary.text)}</p>", "<h2>In this episode</h2><ul>"]
    parts.extend(f"<li>{html.escape(topic.text)}</li>" for topic in episode.primary_topics)
    parts.append("</ul>")
    for section in episode.semantic_sections:
        parts.extend([f"<h2>{html.escape(section.title)}</h2>", f"<p><em>{timestamp(section.start_seconds)}–{timestamp(section.end_seconds)}</em></p>",
                      f"<p>{html.escape(section.summary.text)}</p>"])
    if episode.related_episodes:
        parts.append("<h2>Related episodes</h2><ul>")
        parts.extend(f"<li>{html.escape(r.episode_id)} — {html.escape('; '.join(r.reasons))}</li>" for r in episode.related_episodes)
        parts.append("</ul>")
    parts.extend(["<h2>Complete transcript</h2>"])
    for segment in episode.transcript.segments:
        speaker = f"<strong>{html.escape(segment.speaker)}:</strong> " if segment.speaker else ""
        parts.append(f"<p><strong>[{timestamp(segment.start_seconds)}]</strong> {speaker}{html.escape(segment.text)}</p>")
    parts.append(f'<p><a href="{html.escape(episode.audio_url, quote=True)}">Listen to the original audio</a></p>')
    return "\n".join(parts)


def review_report(episode: Episode) -> str:
    lines = [f"# Review: {episode.title}", "", f"- Status: **{episode.status.value}**",
             f"- Transcription confidence: {episode.qa.transcription_confidence or 'not supplied'}",
             f"- WordPress draft: {episode.wordpress.draft_url or 'not created'}", "",
             "## Manual-review flags", ""]
    lines.extend(f"- {flag}" for flag in episode.qa.manual_review_flags or ["None"])
    lines.extend(["", "## Extracted topics", ""])
    lines.extend(f"- {topic.text} ({topic.confidence:.2f})" for topic in episode.primary_topics + episode.secondary_topics)
    lines.extend(["", "## Scripture references", ""])
    refs = [r for section in episode.semantic_sections for r in section.scripture_references]
    lines.extend(f"- {ref.text}" for ref in refs or [])
    if not refs:
        lines.append("- None extracted")
    lines.extend(["", "## Claims and evidence", ""])
    claims = [c for section in episode.semantic_sections for c in section.claims]
    for claim in claims:
        lines.append(f"- {claim.text}")
        for pointer in claim.evidence:
            lines.append(f"  - {timestamp(pointer.start_seconds)}–{timestamp(pointer.end_seconds)}: {pointer.excerpt}")
    lines.extend(["", "## Related episodes", ""])
    lines.extend(f"- {r.episode_id}: {'; '.join(r.reasons)}" for r in episode.related_episodes)
    if not episode.related_episodes:
        lines.append("- None")
    return "\n".join(lines) + "\n"

