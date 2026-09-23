from lftp_kb.recovery_verification import (
    CatalogCandidate,
    classify_comparison,
    nearby_candidates,
)


def candidate(day: str, number: int, path: str) -> CatalogCandidate:
    return CatalogCandidate(day, number, "", "local-only", path, "")


def test_candidate_selection_prefers_episode_hint_then_nearby_dates():
    rows = [
        candidate("2017-06-19", 20, "2017/e20.mp3"),
        candidate("2017-07-25", 21, "2017/e21.mp3"),
        candidate("2017-07-11", 99, "2017/near.mp3"),
        candidate("2018-07-10", 21, "2018/wrong-year.mp3"),
    ]
    selected = nearby_candidates(rows, "2017-07-10", 21)
    assert [item.preferred_local_file for item in selected] == [
        "2017/e21.mp3",
        "2017/near.mp3",
        "2017/e20.mp3",
    ]


def test_comparison_requires_real_audio_or_content_evidence():
    assert classify_comparison(0.91, None, 1.0) == "same-recording"
    assert classify_comparison(0.60, 0.72, 0.9) == "same-show-content"
    assert classify_comparison(0.40, 0.02, 1.0) == "different-content"
    assert classify_comparison(None, None, 0.1) == "unlikely-full-match"
    assert classify_comparison(0.70, 0.20, 0.9) == "manual-review"
