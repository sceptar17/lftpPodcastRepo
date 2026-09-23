from scripts.build_archive_finalization_plan import _date_candidates, _meaningful_title


def test_date_candidates_support_archive_filename_formats():
    assert _date_candidates("20100622-03.mp3") == ["2010-06-22"]
    assert _date_candidates("03262012.mp3") == ["2012-03-26"]
    assert _date_candidates("Live From The Path - 03-30-2015.mp3") == ["2015-03-30"]
    assert _date_candidates("Live From The Path - August 24, 2015.mp3") == [
        "2015-08-24"
    ]


def test_backup_timestamp_is_not_treated_as_episode_date():
    assert _date_candidates("Episode (2016_10_07 00_26_59 UTC).mp3") == []


def test_placeholder_and_date_only_titles_are_not_canonical_titles():
    assert not _meaningful_title("Broadcast Setup")
    assert not _meaningful_title("March 2, 2015")
    assert _meaningful_title("Live From The Path w/ Jacey")
