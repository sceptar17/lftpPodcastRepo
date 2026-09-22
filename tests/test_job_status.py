from datetime import UTC, datetime, timedelta

from lftp_kb.job_status import duration_label, job_view


def test_job_view_calculates_progress_and_eta():
    started = datetime.now(UTC) - timedelta(seconds=20)
    result = job_view({
        "status": "running", "started_at": started.isoformat(),
        "total": 10, "processed": 2,
    })

    assert result["progress_percent"] == 20
    assert 19 <= result["display_elapsed"] <= 21
    assert 79 <= result["display_remaining"] <= 81


def test_duration_label_is_human_readable():
    assert duration_label(None) == "Estimating…"
    assert duration_label(42) == "42 sec"
    assert duration_label(150) == "2 min"
    assert duration_label(3900) == "1 hr 5 min"
