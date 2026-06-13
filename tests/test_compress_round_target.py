from appcore.runtime._helpers import compute_compress_round_target


def test_compress_targets_half_second_under_video():
    d, w, direction = compute_compress_round_target(35.0, 2.5, 30.0)
    assert d == 29.5 and w == 74 and direction == "shrink"


def test_expand_when_audio_too_short():
    _, _, direction = compute_compress_round_target(25.0, 2.5, 30.0)
    assert direction == "expand"


def test_target_words_floor_at_three():
    _, w, _ = compute_compress_round_target(35.0, 0.01, 1.0)
    assert w == 3


def test_target_duration_floor():
    d, _, _ = compute_compress_round_target(35.0, 2.5, 0.2)
    assert d == 0.5
