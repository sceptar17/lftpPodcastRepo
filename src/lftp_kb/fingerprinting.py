from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from .models import ReconstructionReport
from .repository import Repository

MAX_FINGERPRINT_VALUES = 6_000


def verify_alternate_masters(
    report: ReconstructionReport, archive_root: Path, repository: Repository
) -> None:
    proposals = [
        proposal
        for proposal in report.duplicate_proposals
        if proposal.relationship == "alternate-master"
    ]
    if not proposals:
        return
    assets = {asset.asset_id: asset for asset in report.assets}
    configured = os.getenv("LFTP_FPCALC_PATH")
    executable = configured if configured and Path(configured).is_file() else shutil.which("fpcalc")
    if executable is None:
        for proposal in proposals:
            proposal.acoustic_status = "unavailable"
            for asset_id in proposal.asset_ids:
                assets[asset_id].acoustic_fingerprint_status = "unavailable"
        return

    fingerprints: dict[str, list[int]] = {}
    for asset_id in {asset_id for proposal in proposals for asset_id in proposal.asset_ids}:
        asset = assets[asset_id]
        try:
            fingerprint = _fpcalc(executable, archive_root / asset.relative_path)
            fingerprints[asset_id] = fingerprint
            relative = f"catalog/fingerprints/{asset_id}.json"
            repository.atomic_json(
                relative,
                {
                    "provider": "chromaprint-fpcalc",
                    "source_relative_path": asset.relative_path,
                    "fingerprint_sha256": hashlib.sha256(
                        ",".join(str(value) for value in fingerprint).encode()
                    ).hexdigest(),
                    "fingerprint": fingerprint,
                },
            )
            asset.acoustic_fingerprint_status = "complete"
            asset.acoustic_fingerprint_path = relative
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            asset.acoustic_fingerprint_status = "failed"
            asset.metadata_issues.append(
                f"Acoustic fingerprint failed: {type(error).__name__}: {error}"
            )

    for proposal in proposals:
        left = fingerprints.get(proposal.asset_ids[0])
        right = fingerprints.get(proposal.asset_ids[1])
        if left is None or right is None:
            proposal.acoustic_status = "failed"
            continue
        similarity = _fingerprint_similarity(left, right)
        proposal.acoustic_status = "complete"
        proposal.acoustic_similarity = similarity
        proposal.reasons.append(f"Chromaprint acoustic similarity is {similarity:.1%}.")
        if similarity >= 0.82:
            proposal.confidence = max(proposal.confidence, 0.98)
            proposal.requires_listening = False


def _fpcalc(executable: str, path: Path) -> list[int]:
    result = subprocess.run(
        [executable, "-raw", "-length", "600", str(path)],
        capture_output=True,
        check=True,
        text=True,
        timeout=180,
    )
    line = next(
        (line for line in result.stdout.splitlines() if line.startswith("FINGERPRINT=")), None
    )
    if not line:
        raise ValueError("fpcalc returned no fingerprint")
    values = [int(value) for value in line.removeprefix("FINGERPRINT=").split(",") if value]
    if len(values) < 20:
        raise ValueError("fpcalc fingerprint was too short")
    # fpcalc versions occasionally ignore or mishandle the requested duration. The comparison is
    # deliberately based on approximately the first ten minutes; bounding the raw values prevents
    # a malformed or full-program fingerprint from turning the shift comparison into a runaway job.
    return values[:MAX_FINGERPRINT_VALUES]


def _fingerprint_similarity(left: list[int], right: list[int], max_shift: int = 40) -> float:
    left = left[:MAX_FINGERPRINT_VALUES]
    right = right[:MAX_FINGERPRINT_VALUES]
    best = 0.0
    for shift in range(-max_shift, max_shift + 1):
        left_start, right_start = max(0, shift), max(0, -shift)
        overlap = min(len(left) - left_start, len(right) - right_start)
        if overlap < 20:
            continue
        differing_bits = sum(
            (
                (left[left_start + index] & 0xFFFFFFFF) ^ (right[right_start + index] & 0xFFFFFFFF)
            ).bit_count()
            for index in range(overlap)
        )
        best = max(best, 1 - differing_bits / (32 * overlap))
    return round(best, 4)
