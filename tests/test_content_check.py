from lftp_kb.content_check import content_similarity


def test_content_similarity_recognizes_same_topic_and_wording():
    local = (
        "Today we are discussing pastoral authority, Hebrews thirteen, and how church leaders "
        "should guide people without controlling their decisions."
    )
    remote = (
        "Today we're discussing pastoral authority and Hebrews thirteen and how church leaders "
        "can guide people without controlling every decision."
    )

    assert content_similarity(local, remote) >= 0.55


def test_content_similarity_rejects_unrelated_samples():
    local = "Pastoral authority in Hebrews and the responsibility of leaders in the church."
    remote = "A movie review about spaceships, alien planets, actors, and visual effects."

    assert content_similarity(local, remote) <= 0.08
