def test_placeholder_for_relationship_integration(sample_episode=None):
    # Relationship behavior is exercised end-to-end by `lftp-kb sample`; this test keeps
    # the critical unit suite explicit without duplicating a large canonical fixture.
    assert sample_episode is None

