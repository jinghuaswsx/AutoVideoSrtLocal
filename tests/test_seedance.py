from pipeline import seedance


def test_seedance_poll_timeout_is_tripled():
    assert seedance.POLL_TIMEOUT == 1800
