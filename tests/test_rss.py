from lftp_kb.rss import parse_rss


def test_rss_parses_enclosure_and_stable_id():
    xml = b"""<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
    <title>Show</title><item><title>Episode</title><guid>x</guid>
    <pubDate>Tue, 22 Sep 2026 08:00:00 +0000</pubDate><itunes:episode>24</itunes:episode>
    <description><![CDATA[<p>Hello &amp; goodbye</p>]]></description>
    <itunes:duration>1:02:03</itunes:duration>
    <enclosure url="https://example.com/a.mp3" type="audio/mpeg" length="123456"/></item></channel></rss>"""
    first = parse_rss(xml, "https://example.com/feed")
    second = parse_rss(xml, "https://example.com/feed")
    assert first[0].episode_id == second[0].episode_id
    assert first[0].episode_number == 24
    assert first[0].description == "Hello & goodbye"
    assert first[0].audio_size_bytes == 123456
    assert first[0].audio_duration_seconds == 3723
