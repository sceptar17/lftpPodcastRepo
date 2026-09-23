import sys
from datetime import date
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "research_legacy_sources.py"
SPEC = spec_from_file_location("legacy_research", SCRIPT)
MODULE = module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_parse_archived_html_extracts_text_and_resolves_links():
    parsed = MODULE.parse_html(
        b"<title>Live From The Path</title><a href='episode.asp?id=7'>July show</a>",
        "http://example.test/show.asp?showid=56",
    )
    assert parsed["title"] == "Live From The Path"
    assert parsed["links"] == [
        {"url": "http://example.test/episode.asp?id=7", "label": "July show"}
    ]


def test_extract_and_reconcile_des_moines_episode_card():
    markup = b"""
    <td><p><a href="show_date.asp?showid=56&amp;id=43">Might As Well Get Started</a>
    <br><span>3/20/2010</span></p><p>The first show.</p><br></td>
    """
    episodes = MODULE.extract_des_moines_episodes(
        markup, "https://web.archive.org/web/20101123/http://example.test/show.asp?showid=56"
    )
    assert episodes[0]["broadcast_date"] == "2010-03-20"
    assert episodes[0]["title"] == "Might As Well Get Started"
    assert episodes[0]["description"] == "The first show."
    reconciled = MODULE.reconcile_legacy_episodes(episodes, MODULE.MasterIndex([]))
    assert reconciled[0]["disposition"] == "source-only-candidate"


def test_media_classification_keeps_short_video_as_evidence():
    masters = MODULE.MasterIndex(
        [
            {
                "episode_key": "2010-06-21",
                "canonical_date": "2010-06-21",
                "title": "Art and the Church",
            }
        ]
    )
    row = MODULE.media_row(
        "youtube",
        {
            "id": "abc",
            "title": "Question 6/21/2010 - Art and the Church",
            "duration": 150,
        },
        masters,
    )
    assert MODULE.entry_date({"title": "Question 6/21/2010"}) == date(2010, 6, 21)
    assert row["candidate_disposition"] == ("likely-existing-episode-or-supporting-source")
    assert row["matched_master_id"] == "2010-06-21"


def test_unmatched_long_video_is_only_a_candidate():
    row = MODULE.media_row(
        "vimeo",
        {"id": "99", "title": "Unknown old show", "duration": 5400},
        MODULE.MasterIndex([]),
    )
    assert row["candidate_disposition"] == "long-form-needs-date-reconciliation"


def test_year_and_episode_number_match_without_flat_playlist_date():
    masters = MODULE.MasterIndex(
        [
            {
                "canonical_year": 2021,
                "canonical_date": "2021-06-23",
                "reported_episode_number": 14,
                "rss_episode_id": "ep-014",
                "title": "The Heavy",
            }
        ]
    )
    row = MODULE.media_row(
        "youtube",
        {"id": "abc", "title": "2021 Episode 14 | The Heavy", "duration": 5415},
        masters,
    )
    assert row["candidate_disposition"] == ("likely-existing-episode-or-supporting-source")
    assert row["matched_master_id"] == "ep-014"


def test_dates_are_recovered_from_historical_video_titles():
    assert MODULE.entry_date({"title": "Live From The Path - June 15, 2015"}) == date(2015, 6, 15)
    assert MODULE.entry_date({"title": "20141201 83"}) == date(2014, 12, 1)
    assert MODULE.title_year_episode("Live From The Path | 2020 E22") == (2020, 22)


def test_historical_title_date_overrides_later_publication_date_for_matching():
    masters = MODULE.MasterIndex(
        [
            {
                "canonical_date": "2014-02-16",
                "rss_episode_id": "feb-10",
                "title": "February 10, 2014 Podcast",
            }
        ]
    )
    row = MODULE.media_row(
        "youtube",
        {
            "id": "abc",
            "title": "Live From The Path - February 10, 2014",
            "duration": 5715,
        },
        masters,
    )
    assert row["matched_master_id"] == "feb-10"
