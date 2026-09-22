from lftp_kb import repository as repository_module
from lftp_kb.repository import Repository


def test_atomic_json_retries_transient_windows_file_lock(tmp_path, monkeypatch):
    repository = Repository(tmp_path)
    actual_replace = repository_module.os.replace
    calls = 0

    def temporarily_locked(source, target):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise PermissionError(13, "Permission denied", str(target))
        actual_replace(source, target)

    monkeypatch.setattr(repository_module.os, "replace", temporarily_locked)
    monkeypatch.setattr(repository_module.time, "sleep", lambda _seconds: None)

    repository.atomic_json("state/catalog-build.json", {"status": "running"})

    assert calls == 3
    assert (tmp_path / "state" / "catalog-build.json").read_text(encoding="utf-8")
