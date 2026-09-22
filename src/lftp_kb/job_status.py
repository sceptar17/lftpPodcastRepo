from __future__ import annotations

from datetime import UTC, datetime

ACTIVE_STATUSES = {"queued", "running"}


def job_view(payload: dict | None) -> dict | None:
    """Add display-only progress timing without changing persisted job state."""
    if payload is None:
        return None
    result = dict(payload)
    total = int(result.get("total") or 0)
    processed = result.get("processed")
    if processed is None:
        processed = sum(int(result.get(field) or 0) for field in ("completed", "skipped", "failed"))
    processed = int(processed)
    result["processed"] = processed
    result["progress_percent"] = round(processed / total * 100) if total else 0

    started = _parse_time(result.get("started_at") or result.get("created_at"))
    finished = _parse_time(result.get("finished_at"))
    elapsed = max(0, int(((finished or datetime.now(UTC)) - started).total_seconds())) if started else 0
    result["display_elapsed"] = elapsed

    estimated = result.get("estimated_seconds")
    if estimated is None and result.get("status") == "running" and processed and total > processed:
        estimated = elapsed / processed * total
    result["display_remaining"] = (
        max(0, round(float(estimated) - elapsed)) if estimated is not None else None
    )
    return result


def duration_label(seconds: float | None) -> str:
    if seconds is None:
        return "Estimating…"
    seconds = max(0, round(seconds))
    if seconds < 60:
        return f"{seconds} sec"
    hours, remainder = divmod(seconds, 3600)
    minutes = round(remainder / 60)
    if hours:
        return f"{hours} hr {minutes} min"
    return f"{minutes} min"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)
