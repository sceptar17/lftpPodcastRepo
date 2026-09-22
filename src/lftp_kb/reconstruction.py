from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import defaultdict
from datetime import UTC, date, datetime
from difflib import SequenceMatcher
from pathlib import Path

from .models import (
    AssetClaim,
    CatalogAsset,
    DiscoveredEpisode,
    DuplicateProposal,
    EpisodeMatchProposal,
    ReconstructionReport,
)


def build_reconstruction_report(
    observations: list[dict], rss: list[DiscoveredEpisode]
) -> ReconstructionReport:
    assets = [_asset_from_observation(item) for item in observations]
    _flag_repeated_titles(assets)
    return ReconstructionReport(
        generated_at=datetime.now(UTC),
        assets=assets,
        duplicate_proposals=_duplicate_proposals(assets),
        match_proposals=_match_proposals(assets, rss),
    )


def _asset_from_observation(item: dict) -> CatalogAsset:
    relative = item["relative_path"]
    folder_year = _folder_year(relative)
    path_dates = _path_dates(relative)
    annual_numbers, overall_numbers = _path_numbers(relative)
    title_numbers = _title_numbers(item.get("title"))
    annual_numbers.update(title_numbers)
    claims: list[AssetClaim] = []
    issues: list[str] = []
    for value in sorted(path_dates):
        claims.append(
            AssetClaim(
                field="recording_date",
                value=value,
                source="file-path",
                confidence=0.9,
                reason="Complete calendar date appears in the file path.",
            )
        )
    if folder_year:
        claims.append(
            AssetClaim(
                field="episode_year",
                value=str(folder_year),
                source="folder-name",
                confidence=0.75,
                reason="File is stored in a four-digit year folder.",
            )
        )
    if item.get("date") and item.get("date_source") != "folder-name":
        claims.append(
            AssetClaim(
                field="embedded_date",
                value=item["date"],
                source="embedded-metadata",
                confidence=0.55,
                reason="Date is present in the audio tags but may be stale.",
            )
        )
        if folder_year and not str(item["date"]).startswith(str(folder_year)):
            issues.append("Embedded date conflicts with folder year")
    if item.get("title") and _is_placeholder_title(item["title"]):
        claims.append(
            AssetClaim(
                field="embedded_title_placeholder",
                value=item["title"],
                source="embedded-metadata",
                confidence=0.1,
                reason="The tag looks like recording/setup metadata, not an episode title.",
            )
        )
        issues.append("Embedded title looks like a default recording/setup tag")
    elif item.get("title"):
        claims.append(
            AssetClaim(
                field="title",
                value=item["title"],
                source="embedded-metadata",
                confidence=0.6,
                reason="Title is present in the audio tags and requires corroboration.",
            )
        )
    for number in sorted(annual_numbers):
        number_source = "file-path" if number in _path_numbers(relative)[0] else "embedded-title"
        claims.append(
            AssetClaim(
                field="annual_episode_number",
                value=str(number),
                source=number_source,
                confidence=0.9 if number_source == "file-path" else 0.7,
                reason=(
                    "Explicit E/EP/Episode notation appears in the file path."
                    if number_source == "file-path"
                    else "Explicit E/EP/Episode notation appears in the embedded title."
                ),
            )
        )
    for number in sorted(overall_numbers):
        claims.append(
            AssetClaim(
                field="overall_episode_number",
                value=str(number),
                source="file-path",
                confidence=0.9,
                reason="A three-digit ep number appears separately in the file path.",
            )
        )
    if item.get("episode_number") is not None:
        claims.append(
            AssetClaim(
                field="embedded_track_number",
                value=str(item["episode_number"]),
                source="embedded-metadata",
                confidence=0.4,
                reason="Track-number tags are useful evidence but are not assumed to be episode numbers.",
            )
        )
    if item.get("metadata_error"):
        issues.append(item["metadata_error"])
    issues.extend(item.get("file_errors", []))
    identifier = hashlib.sha256(relative.lower().encode()).hexdigest()[:12]
    return CatalogAsset(
        asset_id=f"asset-{identifier}",
        relative_path=relative,
        filename=item["filename"],
        size_bytes=item["size_bytes"],
        modified_at=item.get("modified_at"),
        duration_seconds=item.get("duration_seconds"),
        sha256=item["sha256"],
        hash_status=item.get("hash_status", "not-required"),
        format=item["format"],
        embedded_title=item.get("title"),
        embedded_track_number=item.get("episode_number"),
        embedded_date=(item.get("date") if item.get("date_source") != "folder-name" else None),
        folder_year=folder_year,
        path_dates=sorted(path_dates),
        annual_episode_numbers=sorted(annual_numbers),
        overall_episode_numbers=sorted(overall_numbers),
        claims=claims,
        metadata_issues=issues,
    )


def _duplicate_proposals(assets: list[CatalogAsset]) -> list[DuplicateProposal]:
    result: list[DuplicateProposal] = []
    exact: dict[str, list[CatalogAsset]] = defaultdict(list)
    for asset in assets:
        if asset.sha256:
            exact[asset.sha256].append(asset)
    exact_pairs: set[frozenset[str]] = set()
    for digest, group in exact.items():
        if len(group) < 2:
            continue
        ids = sorted(asset.asset_id for asset in group)
        exact_pairs.update(
            frozenset((left, right)) for i, left in enumerate(ids) for right in ids[i + 1 :]
        )
        result.append(
            DuplicateProposal(
                proposal_id=f"duplicate-exact-{digest[:12]}",
                relationship="exact-copy",
                asset_ids=ids,
                confidence=1.0,
                reasons=["Files have the same complete SHA-256 content hash."],
                requires_listening=False,
            )
        )

    by_year: dict[int | None, list[CatalogAsset]] = defaultdict(list)
    for asset in assets:
        by_year[asset.folder_year].append(asset)
    for group in by_year.values():
        for index, left in enumerate(group):
            for right in group[index + 1 :]:
                if frozenset((left.asset_id, right.asset_id)) in exact_pairs:
                    continue
                reasons = _near_duplicate_reasons(left, right)
                alternate_master = _alternate_master_evidence(left, right)
                if alternate_master and any(reason.startswith("Duration") for reason in reasons):
                    preferred, preference_reasons = _preferred_master(left, right)
                    token = hashlib.sha256(
                        f"{left.asset_id}:{right.asset_id}".encode()
                    ).hexdigest()[:12]
                    result.append(
                        DuplicateProposal(
                            proposal_id=f"duplicate-master-{token}",
                            relationship="alternate-master",
                            asset_ids=[left.asset_id, right.asset_id],
                            confidence=0.9,
                            reasons=[*reasons, alternate_master],
                            requires_listening=True,
                            preferred_asset_id=preferred.asset_id,
                            preference_reasons=preference_reasons,
                        )
                    )
                    continue
                if len(reasons) < 2 or not any(reason.startswith("Duration") for reason in reasons):
                    continue
                confidence = min(0.97, 0.62 + 0.11 * len(reasons))
                token = hashlib.sha256(f"{left.asset_id}:{right.asset_id}".encode()).hexdigest()[
                    :12
                ]
                result.append(
                    DuplicateProposal(
                        proposal_id=f"duplicate-likely-{token}",
                        relationship="likely-same-recording",
                        asset_ids=[left.asset_id, right.asset_id],
                        confidence=confidence,
                        reasons=reasons,
                        requires_listening=True,
                    )
                )
    return sorted(result, key=lambda item: (-item.confidence, item.proposal_id))


def _near_duplicate_reasons(left: CatalogAsset, right: CatalogAsset) -> list[str]:
    reasons = []
    if left.duration_seconds is not None and right.duration_seconds is not None:
        delta = abs(left.duration_seconds - right.duration_seconds)
        if delta <= 5:
            reasons.append(f"Duration differs by only {delta:.2f} seconds.")
    if set(left.path_dates) & set(right.path_dates):
        reasons.append("The same complete date appears in both file paths.")
    if set(left.annual_episode_numbers) & set(right.annual_episode_numbers):
        reasons.append("The same explicit annual episode number appears in both paths.")
    left_title, right_title = _normalize(left.embedded_title), _normalize(right.embedded_title)
    if left_title and left_title == right_title:
        reasons.append("Embedded titles match exactly after normalization.")
    if left.size_bytes == right.size_bytes:
        reasons.append("File sizes are identical despite different byte hashes.")
    return reasons


def _alternate_master_evidence(left: CatalogAsset, right: CatalogAsset) -> str | None:
    left_base, _, left_label = _versioned_stem(left.filename)
    right_base, _, right_label = _versioned_stem(right.filename)
    if left_base != right_base or not (left_label or right_label):
        return None
    labels = " and ".join(label for label in (left_label, right_label) if label)
    return f"Filenames share the same base with version suffix evidence ({labels})."


def _preferred_master(left: CatalogAsset, right: CatalogAsset) -> tuple[CatalogAsset, list[str]]:
    _, left_rank, left_label = _versioned_stem(left.filename)
    _, right_rank, right_label = _versioned_stem(right.filename)
    if left_rank != right_rank:
        preferred = left if left_rank > right_rank else right
        label = left_label if preferred is left else right_label
        return preferred, [f"The appended version marker {label!r} has the higher version rank."]
    if left.modified_at and right.modified_at and left.modified_at != right.modified_at:
        preferred = left if left.modified_at > right.modified_at else right
        return preferred, ["File modified time is later; this is a fallback preference, not proof."]
    preferred = max((left, right), key=lambda asset: asset.filename.lower())
    return preferred, ["Filename sorts later; this is a fallback preference, not proof."]


def _versioned_stem(filename: str) -> tuple[str, int, str | None]:
    stem = Path(filename).stem.strip()
    patterns = (
        (r"(?i)[\s._-]+(?:version|ver|v)[\s._-]*(\d+)$", 100),
        (r"(?i)[\s._-]+(final|remastered?|edited|edit|master|mix)[\s._-]*(\d*)$", 200),
        (r"(?:[\s._-]*)([A-Z])$", 10),
    )
    for pattern, base_rank in patterns:
        match = re.search(pattern, stem)
        if not match:
            continue
        label = match.group(0).strip(" ._-")
        suffix_number = next(
            (int(group) for group in match.groups() if group and group.isdigit()), 0
        )
        letter_rank = ord(label[-1]) - ord("A") + 1 if len(label) == 1 and label.isupper() else 0
        base = _normalize(stem[: match.start()])
        return base, base_rank + suffix_number + letter_rank, label
    return _normalize(stem), 0, None


def _match_proposals(
    assets: list[CatalogAsset], rss: list[DiscoveredEpisode]
) -> list[EpisodeMatchProposal]:
    proposals = []
    for asset in assets:
        ranked = sorted(
            (_match_score(asset, episode) for episode in rss),
            key=lambda value: value[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < 0.35:
            continue
        score, reasons, episode = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else None
        margin = score - (runner_up or 0)
        recommendation = "auto-link" if score >= 0.8 and margin >= 0.15 else "review"
        token = hashlib.sha256(f"{asset.asset_id}:{episode.episode_id}".encode()).hexdigest()[:12]
        proposals.append(
            EpisodeMatchProposal(
                proposal_id=f"match-{token}",
                asset_id=asset.asset_id,
                rss_episode_id=episode.episode_id,
                confidence=score,
                reasons=reasons,
                competing_confidence=runner_up,
                competing_rss_episode_id=ranked[1][2].episode_id if len(ranked) > 1 else None,
                recommendation=recommendation,
            )
        )
    return sorted(proposals, key=lambda item: (-item.confidence, item.proposal_id))


def _match_score(
    asset: CatalogAsset, episode: DiscoveredEpisode
) -> tuple[float, list[str], DiscoveredEpisode]:
    score = 0.0
    reasons = []
    unreliable_title = any(issue.startswith("Embedded title") for issue in asset.metadata_issues)
    title = "" if unreliable_title else _normalize(asset.embedded_title)
    rss_title = _normalize(episode.title)
    if title and rss_title:
        similarity = SequenceMatcher(None, title, rss_title).ratio()
        if similarity == 1 or title.replace(" ", "") == rss_title.replace(" ", ""):
            score += 0.55
            reasons.append("Embedded and RSS titles match exactly after normalization.")
        elif similarity >= 0.84:
            score += 0.38
            reasons.append(f"Embedded and RSS titles are {similarity:.0%} similar.")
    if (
        episode.episode_number is not None
        and episode.episode_number in asset.annual_episode_numbers
    ):
        score += 0.45
        reasons.append("Explicit annual episode number matches the RSS episode number.")
    for value in asset.path_dates:
        delta = (episode.publication_date.date() - date.fromisoformat(value)).days
        if -2 <= delta <= 7:
            date_score = 0.35 if 0 <= delta <= 2 else 0.25
            if date_score > 0:
                score += date_score
                reasons.append(f"RSS publication is {delta:+d} days from the path date.")
                break
    if asset.folder_year == episode.publication_date.year:
        score += 0.05
    return round(min(score, 1.0), 3), reasons, episode


def _flag_repeated_titles(assets: list[CatalogAsset]) -> None:
    by_title: dict[str, list[CatalogAsset]] = defaultdict(list)
    for asset in assets:
        if normalized := _normalize(asset.embedded_title):
            by_title[normalized].append(asset)
    for group in by_title.values():
        if len(group) < 5:
            continue
        for asset in group:
            issue = f"Embedded title repeats across {len(group)} files and may be a stale tag"
            if issue not in asset.metadata_issues:
                asset.metadata_issues.append(issue)


def _is_placeholder_title(value: str) -> bool:
    normalized = _normalize(value)
    return normalized in {
        "broadcast setup",
        "broadcast settings",
        "recording",
        "untitled",
        "new recording",
    }


def _folder_year(relative: str) -> int | None:
    for part in Path(relative).parts:
        if re.fullmatch(r"(?:19|20)\d{2}", part):
            return int(part)
    return None


def _path_dates(value: str) -> set[str]:
    result = set()
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})(?!\d)", value):
        if "UTC" in value[match.end() : match.end() + 20].upper():
            continue
        try:
            result.add(date(int(match[1]), int(match[2]), int(match[3])).isoformat())
        except ValueError:
            continue
    return result


def _path_numbers(value: str) -> tuple[set[int], set[int]]:
    annual = {
        int(match)
        for match in re.findall(
            r"(?i)(?:episode|(?<![a-z0-9])ep|(?<![a-z0-9])e)[ _.-]*0*(\d{1,2})(?!\d)", value
        )
    }
    annual.update(int(match) for match in re.findall(r"(?<!\d)@(\d{1,2})(?!\d)", value))
    overall = {
        int(match)
        for match in re.findall(r"(?i)(?<![a-z0-9])(?:episode|ep)[ _.-]*0*(\d{3,})(?!\d)", value)
    }
    return annual, overall


def _title_numbers(value: str | None) -> set[int]:
    return {
        int(match)
        for match in re.findall(
            r"(?i)(?:episode|(?<![a-z0-9])ep|(?<![a-z0-9])e)[ _.-]*0*(\d{1,2})(?!\d)",
            value or "",
        )
    }


def _normalize(value: str | None) -> str:
    if not value:
        return ""
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value.lower()))
