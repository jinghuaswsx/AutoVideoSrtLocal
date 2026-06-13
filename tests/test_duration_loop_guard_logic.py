from appcore.runtime._helpers import resolve_guarded_candidate


def test_first_passed_candidate_wins():
    rows = [(1, {"full_text": "a"}, {"passed": False, "fidelity": 60}),
            (2, {"full_text": "b"}, {"passed": True, "fidelity": 90})]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 2 and degraded is False


def test_all_rejected_falls_back_to_highest_fidelity():
    rows = [(1, {"full_text": "a"}, {"passed": False, "fidelity": 60}),
            (3, {"full_text": "c"}, {"passed": False, "fidelity": 72})]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 3 and degraded is True


def test_no_guard_means_first_in_window():
    rows = [(2, {"full_text": "b"}, None)]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 2 and degraded is False


def test_empty_returns_none():
    idx, degraded = resolve_guarded_candidate([])
    assert idx is None and degraded is False


def test_passed_wins_over_higher_fidelity_rejected():
    rows = [(1, {"full_text": "a"}, {"passed": False, "fidelity": 99}),
            (2, {"full_text": "b"}, {"passed": True, "fidelity": 80})]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 2 and degraded is False


def test_none_guard_counts_as_accepted():
    # A candidate with guard skipped (None) is treated as accepted, not degraded.
    rows = [(1, {"full_text": "a"}, {"passed": False, "fidelity": 50}),
            (2, {"full_text": "b"}, None)]
    idx, degraded = resolve_guarded_candidate(rows)
    assert idx == 2 and degraded is False
