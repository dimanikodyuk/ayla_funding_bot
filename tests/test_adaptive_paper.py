from ayla.core import zscore

def test_six_hour_threshold():
    assert zscore(0, [0]*287, min_samples=288) is None
    assert zscore(0, [0]*288, min_samples=288) == 0

def test_legacy_default_unchanged():
    assert zscore(0, [0]*288) is None
