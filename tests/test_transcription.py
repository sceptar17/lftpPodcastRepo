from lftp_kb.transcription import _confidence


def test_log_probability_is_exposed_as_bounded_review_hint():
    assert _confidence(None) is None
    assert _confidence(0) == 1.0
    assert 0 < _confidence(-1) < 1
