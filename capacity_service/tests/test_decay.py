from app.main import calculate_decayed_confidence


def test_decay_starts_at_initial_confidence():
    assert calculate_decayed_confidence(1.0, 0.05, 0) == 1.0


def test_decay_drops_over_time():
    early = calculate_decayed_confidence(1.0, 0.05, 10)
    late = calculate_decayed_confidence(1.0, 0.05, 40)
    assert late < early < 1.0


def test_decay_never_goes_negative():
    assert calculate_decayed_confidence(1.0, 0.5, 1000) >= 0.0
