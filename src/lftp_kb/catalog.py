from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mutagen import File as MutagenFile

from .inventory import InventorySnapshot
from .models import (
    CatalogAsset,
    CatalogEvidence,
    CatalogSources,
    CatalogYearSummary,
    DiscoveredEpisode,
    EpisodeCandidate,
    EpisodeLedger,
    ReconstructionReport,
)
from .reconstruction import build_reconstruction_report
from .repository import Repository

CatalogProgress = Callable[[int, int, str, str | None], None]


def build_episode_ledger(
    repository: Repository,
    snapshot: InventorySnapshot,
    archive_root: Path,
    progress: CatalogProgress | None = None,
) -> EpisodeLedger:
    total_steps = len(snapshot.local_audio) + 3
    local_observations = []
    if progress:
        progress(0, total_steps, "Hashing files and reading metadata", None)
    for index, item in enumerate(snapshot.local_audio, start=1):
        if progress:
            progress(index - 1, total_steps, "Hashing files and reading metadata", item.filename)
        local_observations.append(
            inspect_audio_file(Path(item.path), archive_root, item.guessed_episode_number)
        )
        if progress and (index % 5 == 0 or index == len(snapshot.local_audio)):
            progress(index, total_steps, "Hashing files and reading metadata", item.filename)
    if progress:
        progress(len(local_observations) + 1, total_steps, "Combining RSS and local evidence", None)
    reconstruction = build_reconstruction_report(local_observations, snapshot.discovered)
    decisions = _load_reconstruction_decisions(repository)
    observations_by_path = {item["relative_path"]: item for item in local_observations}
    discovered = {episode.episode_id: episode for episode in snapshot.discovered}
    inventory_by_path = {
        str(Path(item.path).relative_to(archive_root)).replace("\\", "/"): item
        for item in snapshot.local_audio
    }
    local_by_rss: dict[str, set[str]] = defaultdict(set)
    for item in snapshot.local_audio:
        if (
            item.match_status == "matched"
            and item.match_episode_id
            and item.match_reason == "Exact RSS enclosure filename"
        ):
            relative = str(Path(item.path).relative_to(archive_root)).replace("\\", "/")
            local_by_rss[item.match_episode_id].add(relative)
    assets = {asset.asset_id: asset for asset in reconstruction.assets}
    assigned = {path: episode_id for episode_id, paths in local_by_rss.items() for path in paths}
    for proposal in reconstruction.match_proposals:
        relative = assets[proposal.asset_id].relative_path
        decision = decisions.get(proposal.proposal_id, {}).get("decision")
        should_link = decision == "confirmed" or (
            proposal.recommendation == "auto-link" and decision != "rejected"
        )
        if should_link and relative not in assigned:
            local_by_rss[proposal.rss_episode_id].add(relative)
            assigned[relative] = proposal.rss_episode_id
    duplicate_components = _duplicate_components(reconstruction, decisions)
    for component in duplicate_components:
        targets = {assigned.get(assets[asset_id].relative_path) for asset_id in component}
        targets.discard(None)
        if len(targets) == 1:
            episode_id = next(iter(targets))
            for asset_id in component:
                relative = assets[asset_id].relative_path
                local_by_rss[episode_id].add(relative)
                assigned[relative] = episode_id

    candidates: list[EpisodeCandidate] = []
    for episode in snapshot.discovered:
        observations = [
            observations_by_path[path]
            for path in sorted(local_by_rss.get(episode.episode_id, set()))
        ]
        candidates.append(_rss_candidate(episode, observations))

    exact_groups = {}
    for component in duplicate_components:
        paths = [assets[asset_id].relative_path for asset_id in component]
        for path in paths:
            exact_groups[path] = paths
    handled: set[str] = set(assigned)
    for relative, item in inventory_by_path.items():
        if relative in handled:
            continue
        group_paths = [
            path for path in exact_groups.get(relative, [relative]) if path not in assigned
        ]
        handled.update(group_paths)
        observations = [observations_by_path[path] for path in group_paths]
        primary = max(
            observations,
            key=lambda value: (
                value.get("title") is not None,
                value.get("date") is not None,
                value["size_bytes"],
            ),
        )
        primary_asset = next(
            (
                asset
                for asset in reconstruction.assets
                if asset.relative_path == primary["relative_path"]
            ),
            None,
        )
        candidate = _local_candidate(
            primary,
            item.match_status,
            discovered.get(item.match_episode_id) if item.match_episode_id else None,
            primary_asset,
        )
        candidate.sources.local_files = sorted(group_paths)
        if len(group_paths) > 1:
            candidate.evidence.append(
                CatalogEvidence(
                    source="reconstruction",
                    field="grouped_audio_assets",
                    value=str(len(group_paths)),
                    confidence="confirmed",
                    locator=primary["relative_path"],
                )
            )
        candidates.append(candidate)

    if progress:
        progress(
            len(local_observations) + 2, total_steps, "Assigning provisional year sequence", None
        )
    _assign_provisional_sequence(candidates)
    candidates.sort(key=_candidate_sort_key)
    years = _year_summaries(candidates)
    ledger = EpisodeLedger(
        generated_at=datetime.now(UTC),
        local_archive_root=archive_root.name,
        candidate_count=len(candidates),
        candidates=candidates,
        years=years,
    )
    if progress:
        progress(total_steps - 1, total_steps, "Writing canonical ledger", None)
    _save_catalog(repository, ledger, local_observations, snapshot.discovered, reconstruction)
    if progress:
        progress(total_steps, total_steps, "Catalog complete", None)
    return ledger


def inspect_audio_file(
    path: Path, archive_root: Path, filename_episode_number: int | None = None
) -> dict:
    relative = path.relative_to(archive_root).as_posix()
    file_errors = []
    try:
        size_bytes = path.stat().st_size
    except OSError as error:
        size_bytes = 0
        file_errors.append(f"File stat failed: {type(error).__name__}: {error}")
    try:
        digest = _sha256_file(path)
    except OSError as error:
        digest = None
        file_errors.append(f"Content hash failed: {type(error).__name__}: {error}")
    observation = {
        "source": "local-file",
        "relative_path": relative,
        "filename": path.name,
        "size_bytes": size_bytes,
        "format": path.suffix.lower().lstrip("."),
        "duration_seconds": None,
        "title": None,
        "episode_number": None,
        "date": None,
        "date_precision": "unknown",
        "album": None,
        "artist": None,
        "comments": [],
        "metadata_error": None,
        "filename_episode_number": filename_episode_number,
        "embedded_track_raw": None,
        "sha256": digest,
        "file_errors": file_errors,
    }
    try:
        media = MutagenFile(path, easy=True)
        if (
            media is not None
            and getattr(media, "info", None)
            and getattr(media.info, "length", None)
        ):
            observation["duration_seconds"] = round(float(media.info.length), 3)
        tags = media.tags or {} if media is not None else {}
        observation["title"] = _first(tags, "title")
        observation["album"] = _first(tags, "album")
        observation["artist"] = _first(tags, "artist", "albumartist")
        observation["comments"] = _values(tags, "comment", "description")
        track = _first(tags, "tracknumber", "track")
        observation["embedded_track_raw"] = track
        observation["episode_number"] = _leading_int(track)
        raw_date = _first(tags, "date", "originaldate", "year")
        parsed_date, precision = _parse_partial_date(raw_date)
        observation["date"], observation["date_precision"] = parsed_date, precision
    except Exception as error:  # noqa: BLE001 - corrupt tags must become review evidence
        observation["metadata_error"] = f"{type(error).__name__}: {error}"
    if not observation["date"]:
        folder_year = next(
            (
                part
                for part in reversed(Path(relative).parts[:-1])
                if re.fullmatch(r"(?:19|20)\d{2}", part)
            ),
            None,
        )
        if folder_year:
            observation["date"] = folder_year
            observation["date_precision"] = "year"
            observation["date_source"] = "folder-name"
    return observation


def load_ledger(repository: Repository) -> EpisodeLedger | None:
    path = repository.root / "catalog" / "master-ledger.json"
    return (
        EpisodeLedger.model_validate_json(path.read_text(encoding="utf-8"))
        if path.exists()
        else None
    )


def load_reconstruction(repository: Repository) -> ReconstructionReport | None:
    path = repository.root / "catalog" / "reconstruction-report.json"
    return (
        ReconstructionReport.model_validate_json(path.read_text(encoding="utf-8"))
        if path.exists()
        else None
    )


def _rss_candidate(episode: DiscoveredEpisode, observations: list[dict]) -> EpisodeCandidate:
    evidence = [
        CatalogEvidence(
            source="rss",
            field="title",
            value=episode.title,
            confidence="confirmed",
            locator=episode.source_rss_url,
        ),
        CatalogEvidence(
            source="rss",
            field="publication_date",
            value=episode.publication_date.isoformat(),
            confidence="confirmed",
            locator=episode.source_rss_url,
        ),
    ]
    if episode.episode_number is not None:
        evidence.append(
            CatalogEvidence(
                source="rss",
                field="episode_number",
                value=str(episode.episode_number),
                confidence="confirmed",
                locator=episode.source_rss_url,
            )
        )
    for item in observations:
        if item["episode_number"] is not None:
            evidence.append(
                CatalogEvidence(
                    source="embedded-metadata",
                    field="track_number",
                    value=str(item["episode_number"]),
                    confidence="low",
                    locator=item["relative_path"],
                )
            )
    return EpisodeCandidate(
        candidate_id=f"candidate-rss-{episode.episode_id}",
        title=episode.title,
        episode_number=episode.episode_number,
        episode_number_confidence="confirmed" if episode.episode_number is not None else "unknown",
        publication_date=episode.publication_date.date().isoformat(),
        date_precision="day",
        duration_seconds=_single_value(observations, "duration_seconds"),
        file_size_bytes=_single_value(observations, "size_bytes"),
        sources=CatalogSources(
            local_files=[item["relative_path"] for item in observations],
            rss_guid=episode.guid,
            rss_episode_id=episode.episode_id,
        ),
        evidence=evidence,
        status="confirmed" if observations else "rss-only",
        overall_confidence="confirmed",
    )


def _local_candidate(
    observation: dict,
    match_status: str,
    possible_episode: DiscoveredEpisode | None,
    asset: CatalogAsset | None = None,
) -> EpisodeCandidate:
    evidence = []
    title_is_unreliable = bool(
        asset and any(issue.startswith("Embedded title") for issue in asset.metadata_issues)
    )
    resolved_title = None if title_is_unreliable else observation["title"]
    number = None
    number_confidence = "unknown"
    if asset and len(asset.annual_episode_numbers) == 1:
        number = asset.annual_episode_numbers[0]
        number_confidence = "high"
    if observation["episode_number"] is not None:
        evidence.append(
            CatalogEvidence(
                source="embedded-metadata",
                field="track_number",
                value=str(observation["episode_number"]),
                confidence="low",
                locator=observation["relative_path"],
            )
        )
    if number is not None:
        evidence.append(
            CatalogEvidence(
                source="file-path",
                field="annual_episode_number",
                value=str(number),
                confidence="high",
                locator=observation["relative_path"],
            )
        )
    overall_number = None
    overall_number_confidence = "unknown"
    if asset and len(asset.overall_episode_numbers) == 1:
        overall_number = asset.overall_episode_numbers[0]
        overall_number_confidence = "high"
        evidence.append(
            CatalogEvidence(
                source="file-path",
                field="overall_episode_number",
                value=str(overall_number),
                confidence="high",
                locator=observation["relative_path"],
            )
        )
    for field in ("title", "date"):
        if observation[field]:
            source = (
                observation.get("date_source", "embedded-metadata")
                if field == "date"
                else "embedded-metadata"
            )
            evidence.append(
                CatalogEvidence(
                    source=source,
                    field=(
                        "publication_date"
                        if field == "date"
                        else "untrusted_embedded_title"
                        if title_is_unreliable
                        else field
                    ),
                    value=str(observation[field]),
                    confidence=(
                        "low"
                        if field == "title" and title_is_unreliable
                        else "medium"
                        if source == "folder-name"
                        else "high"
                    ),
                    locator=observation["relative_path"],
                )
            )
    flags = []
    if observation["metadata_error"]:
        flags.append(f"Metadata read issue: {observation['metadata_error']}")
    if title_is_unreliable:
        flags.append("Embedded title appears to be a repeated/default tag; title unresolved")
    elif not observation["title"]:
        flags.append("Title missing from embedded metadata")
    if not observation["date"]:
        flags.append("Publication date unresolved")
    identifier = hashlib.sha256(observation["relative_path"].lower().encode()).hexdigest()[:12]
    return EpisodeCandidate(
        candidate_id=f"candidate-local-{identifier}",
        title=resolved_title,
        episode_number=number,
        episode_number_confidence=number_confidence,
        overall_episode_number=overall_number,
        overall_episode_number_confidence=overall_number_confidence,
        publication_date=observation["date"],
        date_precision=observation["date_precision"],
        duration_seconds=observation["duration_seconds"],
        file_size_bytes=observation["size_bytes"],
        sources=CatalogSources(local_files=[observation["relative_path"]]),
        evidence=evidence,
        status="uncertain" if match_status == "uncertain" else "local-only",
        overall_confidence="high" if number is not None else "medium",
        possible_rss_episode_id=possible_episode.episode_id if possible_episode else None,
        manual_review_flags=flags,
    )


def _assign_provisional_sequence(candidates: list[EpisodeCandidate]) -> None:
    by_year: dict[int, list[EpisodeCandidate]] = defaultdict(list)
    for candidate in candidates:
        if year := _candidate_year(candidate):
            by_year[year].append(candidate)
    for items in by_year.values():
        for index, candidate in enumerate(sorted(items, key=_candidate_sort_key), start=1):
            candidate.known_sequence_in_year = index


def _year_summaries(candidates: list[EpisodeCandidate]) -> list[CatalogYearSummary]:
    grouped: dict[int | None, list[EpisodeCandidate]] = defaultdict(list)
    for candidate in candidates:
        grouped[_candidate_year(candidate)].append(candidate)
    return [
        CatalogYearSummary(
            year=year,
            candidate_count=len(items),
            local_file_count=sum(len(item.sources.local_files) for item in items),
            rss_count=sum(item.sources.rss_guid is not None for item in items),
            numbered_count=sum(item.episode_number is not None for item in items),
            exact_date_count=sum(item.date_precision == "day" for item in items),
            conflict_count=sum(item.status == "conflict" for item in items),
        )
        for year, items in sorted(grouped.items(), key=lambda value: value[0] or 0, reverse=True)
    ]


def _save_catalog(
    repository: Repository,
    ledger: EpisodeLedger,
    local: list[dict],
    rss: list[DiscoveredEpisode],
    reconstruction: ReconstructionReport,
) -> None:
    repository.atomic_json("catalog/observations/local-files.json", local)
    repository.atomic_json(
        "catalog/observations/rss.json", [item.model_dump(mode="json") for item in rss]
    )
    repository.atomic_json("catalog/master-ledger.json", ledger.model_dump(mode="json"))
    repository.atomic_json(
        "catalog/reconstruction-report.json", reconstruction.model_dump(mode="json")
    )
    for candidate in ledger.candidates:
        repository.atomic_json(
            f"catalog/candidates/{candidate.candidate_id}.json", candidate.model_dump(mode="json")
        )
    repository.atomic_text("catalog/master-ledger.csv", _ledger_csv(ledger))


def _ledger_csv(ledger: EpisodeLedger) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "candidate_id",
            "year",
            "sequence",
            "episode_number",
            "number_confidence",
            "overall_episode_number",
            "overall_number_confidence",
            "date",
            "date_precision",
            "title",
            "status",
            "confidence",
            "local_files",
            "rss_episode_id",
            "conflicts",
            "review_flags",
        ]
    )
    for item in ledger.candidates:
        writer.writerow(
            [
                item.candidate_id,
                _candidate_year(item),
                item.known_sequence_in_year,
                item.episode_number,
                item.episode_number_confidence,
                item.overall_episode_number,
                item.overall_episode_number_confidence,
                item.publication_date,
                item.date_precision,
                item.title,
                item.status,
                item.overall_confidence,
                " | ".join(item.sources.local_files),
                item.sources.rss_episode_id,
                " | ".join(item.conflicts),
                " | ".join(item.manual_review_flags),
            ]
        )
    return output.getvalue()


def _first(tags, *keys) -> str | None:
    for key in keys:
        values = tags.get(key)
        if values:
            return str(values[0]).strip() or None
    return None


def _values(tags, *keys) -> list[str]:
    result = []
    for key in keys:
        result.extend(str(value).strip() for value in (tags.get(key) or []) if str(value).strip())
    return result


def _leading_int(value: str | None) -> int | None:
    match = re.match(r"\s*(\d+)", value or "")
    return int(match.group(1)) if match else None


def _parse_partial_date(value: str | None) -> tuple[str | None, str]:
    if not value:
        return None, "unknown"
    match = re.match(r"^(\d{4})(?:[-/](\d{1,2}))?(?:[-/](\d{1,2}))?", value.strip())
    if not match:
        return None, "unknown"
    year, month, day = match.groups()
    if day:
        return f"{year}-{int(month):02d}-{int(day):02d}", "day"
    if month:
        return f"{year}-{int(month):02d}", "month"
    return year, "year"


def _single_value(observations: list[dict], field: str):
    values = [item[field] for item in observations if item[field] is not None]
    return values[0] if len(values) == 1 else None


def _candidate_year(candidate: EpisodeCandidate) -> int | None:
    return int(candidate.publication_date[:4]) if candidate.publication_date else None


def _candidate_sort_key(candidate: EpisodeCandidate):
    return (
        candidate.publication_date or "9999",
        candidate.episode_number or 999999,
        candidate.title or candidate.candidate_id,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load_reconstruction_decisions(repository: Repository) -> dict:
    path = repository.root / "catalog" / "reconstruction-decisions.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("decisions", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _duplicate_components(reconstruction: ReconstructionReport, decisions: dict) -> list[set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for proposal in reconstruction.duplicate_proposals:
        decision = decisions.get(proposal.proposal_id, {}).get("decision")
        accepted = proposal.relationship == "exact-copy" or decision == "confirmed"
        if not accepted or decision == "rejected":
            continue
        for left in proposal.asset_ids:
            graph[left].update(asset_id for asset_id in proposal.asset_ids if asset_id != left)
    components = []
    unseen = set(graph)
    while unseen:
        start = unseen.pop()
        component, pending = {start}, [start]
        while pending:
            current = pending.pop()
            additions = graph[current] - component
            component.update(additions)
            pending.extend(additions)
            unseen.difference_update(additions)
        components.append(component)
    return components
