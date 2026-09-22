import json
from datetime import UTC, datetime, timedelta

from lftp_kb.job_status import duration_label, job_view
from lftp_kb.repository import Repository
from lftp_kb.web import _recover_interrupted_catalog_job


def test_job_view_calculates_progress_and_eta():
    started = datetime.now(UTC) - timedelta(seconds=20)
    result = job_view(
        {
            "status": "running",
            "started_at": started.isoformat(),
            "total": 10,
            "processed": 2,
        }
    )

    assert result["progress_percent"] == 20
    assert 19 <= result["display_elapsed"] <= 21
    assert 79 <= result["display_remaining"] <= 81


def test_duration_label_is_human_readable():
    assert duration_label(None) == "Estimating…"
    assert duration_label(42) == "42 sec"
    assert duration_label(150) == "2 min"
    assert duration_label(3900) == "1 hr 5 min"


def test_interrupted_catalog_job_is_recoverable_after_restart(tmp_path):
    repository = Repository(tmp_path)
    repository.atomic_json(
        "state/catalog-build.json",
        {
            "status": "running",
            "stage": "Hashing",
            "created_at": datetime.now(UTC).isoformat(),
            "total": 100,
            "processed": 8,
        },
    )

    _recover_interrupted_catalog_job(repository)

    payload = job_view(
        json.loads((tmp_path / "state" / "catalog-build.json").read_text(encoding="utf-8"))
    )
    assert payload["status"] == "interrupted"
    assert payload["processed"] == 8
