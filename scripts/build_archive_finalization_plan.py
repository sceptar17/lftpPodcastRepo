"""Build a non-destructive master-archive plan from a catalog audit export.

This is intentionally a one-time reconstruction tool rather than web-app
functionality.  It never moves or downloads audio.  It combines the reviewed
ledger with filename/folder evidence and writes a plan for human inspection.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path


PLACEHOLDER_TITLES = {
    "broadcast setup",
    "untitled",
    "live from the path",
}


def _load_root(source: Path) -> tuple[Path, tempfile.TemporaryDirectory | None]:
    if source.is_dir():
        return source, None
    temporary = tempfile.TemporaryDirectory(prefix="lftp-audit-")
    with zipfile.ZipFile(source) as archive:
        archive.extractall(temporary.name)
    root = Path(temporary.name)
    required = root / "master-ledger.json"
    if not required.exists():
        matches = list(root.rglob("master-ledger.json"))
        if len(matches) != 1:
            raise ValueError("Audit ZIP must contain exactly one master-ledger.json")
        root = matches[0].parent
    return root, temporary


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _date_candidates(value: str | None) -> list[str]:
    """Extract plausible full dates without treating backup UTC stamps as episodes."""
    if not value:
        return []
    cleaned = re.sub(r"\(20\d{2}_\d{2}_\d{2}[^)]*UTC\)", "", value, flags=re.I)
    results: set[str] = set()
    patterns = (
        r"(?<!\d)((?:19|20)\d{2})[-_. ]?(\d{2})[-_. ]?(\d{2})(?!\d)",
        r"(?<!\d)(\d{1,2})[-_./ ](\d{1,2})[-_./ ]((?:19|20)\d{2})(?!\d)",
        r"(?<!\d)(\d{2})(\d{2})((?:19|20)\d{2})(?!\d)",
    )
    for index, pattern in enumerate(patterns):
        for match in re.finditer(pattern, cleaned):
            parts = [int(item) for item in match.groups()]
            year, month, day = parts if index == 0 else (parts[2], parts[0], parts[1])
            try:
                results.add(date(year, month, day).isoformat())
            except ValueError:
                pass
    month_names = (
        "January|February|March|April|May|June|July|August|September|"
        "October|November|December"
    )
    for match in re.finditer(
        rf"\b({month_names})\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+((?:19|20)\d{{2}})\b",
        cleaned,
        re.I,
    ):
        try:
            parsed = datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
            results.add(parsed.isoformat())
        except ValueError:
            pass
    return sorted(results)


def _meaningful_title(value: str | None) -> bool:
    if not value:
        return False
    normalized = " ".join(re.findall(r"[a-z0-9]+", value.lower()))
    if normalized in PLACEHOLDER_TITLES:
        return False
    if _date_candidates(value):
        remainder = re.sub(r"live from the path", "", value, flags=re.I)
        remainder = re.sub(
            r"\b(?:January|February|March|April|May|June|July|August|September|"
            r"October|November|December)\s+\d{1,2}(?:st|nd|rd|th)?[,]?\s+"
            r"(?:19|20)\d{2}\b",
            "",
            remainder,
            flags=re.I,
        )
        remainder = re.sub(
            r"(?<!\d)(?:(?:19|20)\d{2}[-_./ ]\d{1,2}[-_./ ]\d{1,2}|"
            r"\d{1,2}[-_./ ]\d{1,2}[-_./ ](?:19|20)\d{2})(?!\d)",
            "",
            remainder,
        )
        if not re.search(r"[a-z0-9]", remainder, re.I):
            return False
    return True


def _choose_date(candidate: dict, asset: dict | None, observation: dict | None) -> tuple[str | None, str, list[str]]:
    flags: list[str] = []
    rss_id = candidate["sources"].get("rss_episode_id")
    if rss_id and candidate.get("date_precision") == "day":
        return candidate["publication_date"][:10], "rss", flags

    folder_year = asset.get("folder_year") if asset else None
    modified_year = None
    if observation and observation.get("modified_at"):
        modified_year = int(observation["modified_at"][:4])
    filename = asset.get("filename") if asset else ""
    title = (observation or {}).get("title") or candidate.get("title")
    filename_dates = _date_candidates(filename)
    title_dates = _date_candidates(title)
    path_dates = (asset or {}).get("path_dates", [])
    filename_dates = sorted(set(filename_dates) | set(path_dates))

    evidence: dict[str, int] = defaultdict(int)
    origins: dict[str, set[str]] = defaultdict(set)
    for item in filename_dates:
        evidence[item] += 5
        origins[item].add("filename")
    for item in title_dates:
        evidence[item] += 3
        origins[item].add("embedded-title")
    for item in list(evidence):
        year = int(item[:4])
        if folder_year == year:
            evidence[item] += 2
            origins[item].add("year-folder")
        if modified_year == year:
            evidence[item] += 1
            origins[item].add("file-modified-year")
    if evidence:
        ranked = sorted(evidence, key=lambda item: (evidence[item], item), reverse=True)
        winner = ranked[0]
        if len(ranked) > 1 and evidence[ranked[0]] == evidence[ranked[1]]:
            flags.append("Conflicting date evidence requires review")
        source = "+".join(sorted(origins[winner]))
        return winner, source, flags

    existing = candidate.get("publication_date")
    if existing and candidate.get("date_precision") == "day":
        return existing[:10], "embedded-metadata", flags
    flags.append("Exact episode date unresolved")
    return None, "unresolved", flags


def _slug(value: str | None) -> str:
    text = "-".join(re.findall(r"[a-z0-9]+", (value or "episode").lower()))
    return text[:70] or "episode"


def build_plan(source: Path) -> dict:
    root, temporary = _load_root(source)
    try:
        ledger = _json(root / "master-ledger.json")
        reconstruction = _json(root / "reconstruction-report.json")
        decisions = _json(root / "reconstruction-decisions.json").get("decisions", {})
        observations = _json(root / "local-files.json")
        rss_items = _json(root / "rss.json")
        assets_by_path = {item["relative_path"]: item for item in reconstruction["assets"]}
        assets_by_id = {item["asset_id"]: item for item in reconstruction["assets"]}
        observations_by_path = {item["relative_path"]: item for item in observations}
        rss_by_id = {item["episode_id"]: item for item in rss_items}
        local_recovery_by_rss: dict[str, str] = {}
        for proposal in reconstruction["match_proposals"]:
            decision = decisions.get(proposal["proposal_id"], {})
            explicitly_local = decision.get("audio_source_preference") == "local-recovery"
            legacy_failed_recovery = (
                decision.get("decision") == "confirmed"
                and proposal.get("verification_status") == "failed"
            )
            if explicitly_local or legacy_failed_recovery:
                asset = assets_by_id.get(proposal["asset_id"])
                if asset:
                    local_recovery_by_rss[proposal["rss_episode_id"]] = asset["relative_path"]
        records = []
        for candidate in ledger["candidates"]:
            sources = candidate["sources"]
            rss_id = sources.get("rss_episode_id")
            recovery_file = local_recovery_by_rss.get(rss_id)
            preferred = recovery_file or sources.get("preferred_local_file")
            if preferred is None and not rss_id and sources.get("local_files"):
                preferred = sources["local_files"][0]
            asset = assets_by_path.get(preferred)
            observation = observations_by_path.get(preferred)
            canonical_date, date_source, flags = _choose_date(candidate, asset, observation)
            if observation and observation.get("metadata_error"):
                flags.append("Preferred local audio is unreadable or has invalid media metadata")
            if preferred and re.search(r"(?i)part[ _.-]*[12]\b", Path(preferred).stem):
                flags.append("File appears to be one part of a multi-part episode")
            folder_year = asset.get("folder_year") if asset else None
            year = (
                int(canonical_date[:4]) if canonical_date else folder_year
                or (int(candidate["publication_date"][:4]) if candidate.get("publication_date") else None)
            )
            title = candidate.get("title")
            if not _meaningful_title(title):
                title = None
            if title is None and observation and _meaningful_title(observation.get("title")):
                title = observation["title"]
            reported_number = candidate.get("episode_number")
            rss_item = rss_by_id.get(rss_id)
            if rss_id and recovery_file:
                master_audio_source = "local-recovery"
                master_source_reference = recovery_file
            elif rss_id:
                master_audio_source = "rss"
                master_source_reference = (rss_item or {}).get("audio_url")
            else:
                master_audio_source = "local"
                master_source_reference = preferred
            local_dispositions = []
            for local_file in sources.get("local_files", []):
                keep_as_master = master_audio_source in {"local", "local-recovery"} and local_file == preferred
                local_dispositions.append({
                    "relative_path": local_file,
                    "disposition": "master" if keep_as_master else "archive",
                    "reason": (
                        "Reviewer selected local recovery because RSS audio is unavailable"
                        if keep_as_master and master_audio_source == "local-recovery"
                        else "Only available local episode audio"
                        if keep_as_master
                        else "RSS enclosure is authoritative for this matched episode"
                        if rss_id
                        else "Superseded local duplicate or alternate master"
                    ),
                })
            record = {
                "candidate_id": candidate["candidate_id"],
                "canonical_year": year,
                "canonical_date": canonical_date,
                "date_source": date_source,
                "season": year,
                "proposed_episode_number": None,
                "reported_episode_number": reported_number,
                "reported_number_confidence": candidate.get("episode_number_confidence"),
                "title": title,
                "status": "confirmed" if candidate["status"] == "uncertain" else candidate["status"],
                "rss_episode_id": rss_id,
                "rss_guid": sources.get("rss_guid"),
                "rss_audio_url": (rss_item or {}).get("audio_url"),
                "preferred_local_file": preferred,
                "superseded_local_files": sources.get("superseded_local_files", []),
                "master_audio_source": master_audio_source,
                "master_source_reference": master_source_reference,
                "local_dispositions": local_dispositions,
                "duration_seconds": candidate.get("duration_seconds"),
                "file_size_bytes": candidate.get("file_size_bytes"),
                "metadata_flags": candidate.get("manual_review_flags", []),
                "review_flags": flags,
            }
            records.append(record)

        by_year: dict[int | None, list[dict]] = defaultdict(list)
        for record in records:
            by_year[record["canonical_year"]].append(record)
        for year, items in by_year.items():
            same_date: dict[str, list[dict]] = defaultdict(list)
            for item in items:
                if item["canonical_date"]:
                    same_date[item["canonical_date"]].append(item)
            for dated_items in same_date.values():
                if len(dated_items) > 1 and any(
                    "File appears to be one part of a multi-part episode" in item["review_flags"]
                    for item in dated_items
                ):
                    for item in dated_items:
                        if "File appears to be one part of a multi-part episode" not in item["review_flags"]:
                            item["review_flags"].append(
                                "Shares a date with an explicitly labeled multi-part file"
                            )
                numbered_suffixes = {
                    match.group(1)
                    for item in dated_items
                    if item["preferred_local_file"]
                    for match in [re.search(r"\(([12])\)\s*$", Path(item["preferred_local_file"]).stem)]
                    if match
                }
                if numbered_suffixes == {"1", "2"}:
                    for item in dated_items:
                        if item["preferred_local_file"] and re.search(
                            r"\([12]\)\s*$", Path(item["preferred_local_file"]).stem
                        ):
                            item["review_flags"].append(
                                "Same-date numbered files may be parts of one episode"
                            )
            items.sort(key=lambda item: (
                item["canonical_date"] or "9999-99-99",
                item["reported_episode_number"] if item["reported_episode_number"] is not None else 99999,
                item["candidate_id"],
            ))
            for index, item in enumerate(items, 1):
                item["proposed_episode_number"] = index
                month = item["canonical_date"][5:7] if item["canonical_date"] else "00-unknown-date"
                extension = Path(item["preferred_local_file"] or ".mp3").suffix.lower() or ".mp3"
                day = item["canonical_date"] or f"{year or 'unknown'}-00-00"
                season = str(year) if year else "unknown"
                filename = f"{day}__S{season}E{index:03d}__{_slug(item['title'])}{extension}"
                item["master_relative_path"] = f"Master/{season}/{month}/{filename}"
                item["archive_relative_paths"] = [
                    f"Archive/{season}/{Path(entry['relative_path']).name}"
                    for entry in item["local_dispositions"]
                    if entry["disposition"] == "archive"
                ]
                item["number_conflict"] = (
                    item["reported_episode_number"] is not None
                    and item["reported_episode_number"] != index
                )
                if item["number_conflict"]:
                    item["metadata_flags"].append(
                        "Reported episode number differs from date-order sequence"
                    )

        records.sort(key=lambda item: (
            item["canonical_year"] if item["canonical_year"] is not None else 9999,
            item["proposed_episode_number"],
        ))
        year_summary = []
        for year in sorted(by_year, key=lambda value: value if value is not None else 9999):
            items = by_year[year]
            year_summary.append({
                "year": year,
                "episode_count": len(items),
                "exact_date_count": sum(item["canonical_date"] is not None for item in items),
                "rss_count": sum(item["rss_episode_id"] is not None for item in items),
                "local_count": sum(item["preferred_local_file"] is not None for item in items),
                "review_count": sum(bool(item["review_flags"]) for item in items),
            })
        return {
            "schema_version": "1.0.0",
            "source_generated_at": ledger.get("generated_at"),
            "rules": {
                "unreviewed_local_files_are_legitimate_episodes": True,
                "only_confirmed_duplicate_or_rss_links_are_collapsed": True,
                "matched_audio_defaults_to_rss": True,
                "confirmed_unavailable_rss_audio_uses_local_recovery": True,
                "embedded_tags_do_not_override_stronger_filename_folder_evidence": True,
                "numbering": "season is publication year; proposed episode number is chronological within year",
            },
            "episode_count": len(records),
            "years": year_summary,
            "episodes": records,
        }
    finally:
        if temporary:
            temporary.cleanup()


def write_outputs(plan: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "master-archive-plan.json").write_text(
        json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    columns = [
        "canonical_year", "proposed_episode_number", "canonical_date", "date_source",
        "reported_episode_number", "title", "status", "rss_episode_id",
        "master_audio_source", "master_source_reference", "preferred_local_file",
        "master_relative_path", "metadata_flags", "review_flags",
    ]
    with (output / "master-archive-plan.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for item in plan["episodes"]:
            row = dict(item)
            row["metadata_flags"] = " | ".join(row["metadata_flags"])
            row["review_flags"] = " | ".join(row["review_flags"])
            writer.writerow(row)
    review = [item for item in plan["episodes"] if item["review_flags"]]
    with (output / "review-queue.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for item in review:
            row = dict(item)
            row["metadata_flags"] = " | ".join(row["metadata_flags"])
            row["review_flags"] = " | ".join(row["review_flags"])
            writer.writerow(row)

    status = Counter(item["status"] for item in plan["episodes"])
    master_sources = Counter(item["master_audio_source"] for item in plan["episodes"])
    blocking_review_count = sum(bool(item["review_flags"]) for item in plan["episodes"])
    archived_local_count = sum(len(item["archive_relative_paths"]) for item in plan["episodes"])
    lines = [
        "# Master archive finalization report", "",
        f"Episodes in the reviewed union: **{plan['episode_count']}**", "",
        "This report treats every uncollapsed local recording as a legitimate episode. "
        "Only reviewer-confirmed duplicates and RSS links have been collapsed.", "",
        "## Current disposition", "",
    ]
    lines.extend(f"- {name}: {count}" for name, count in sorted(status.items()))
    lines.extend(["", "## Master audio selection", ""])
    lines.extend(f"- {name}: {count}" for name, count in sorted(master_sources.items()))
    lines.extend([
        f"- Local copies retained in the archive instead of used as masters: {archived_local_count}",
        f"- Records requiring date/orientation review before final numbering: {blocking_review_count}",
    ])
    lines.extend(["", "## By year", "", "| Year | Episodes | Exact dates | RSS | Local | Review |", "|---:|---:|---:|---:|---:|---:|"])
    for item in plan["years"]:
        lines.append(
            f"| {item['year'] or 'Unknown'} | {item['episode_count']} | "
            f"{item['exact_date_count']} | {item['rss_count']} | {item['local_count']} | "
            f"{item['review_count']} |"
        )
    lines.extend([
        "", "## Interpretation", "",
        "The episode count is the current working baseline, not a claim that the archive is complete. "
        "Web and YouTube research should add candidates only when a dated episode-like item "
        "does not already correspond to one of these records.", "",
        "The proposed annual numbers are chronological placeholders. Existing explicit episode "
        "numbers remain in the plan as evidence and must not be overwritten when they disagree.", "",
        "No audio has been moved, renamed, downloaded, deleted, or uploaded by this tool.", "",
    ])
    (output / "FINALIZATION_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("audit", type=Path, help="Audit ZIP or extracted audit directory")
    parser.add_argument("--output", type=Path, default=Path("catalog/finalization"))
    args = parser.parse_args()
    plan = build_plan(args.audit)
    write_outputs(plan, args.output)
    print(f"Wrote {plan['episode_count']} episode candidates to {args.output}")


if __name__ == "__main__":
    main()
