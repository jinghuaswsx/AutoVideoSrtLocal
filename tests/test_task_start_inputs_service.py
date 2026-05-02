from __future__ import annotations


def test_parse_bool_accepts_form_truthy_values():
    from web.services.task_start_inputs import parse_bool

    assert parse_bool(True) is True
    assert parse_bool("1") is True
    assert parse_bool(" true ") is True
    assert parse_bool("YES") is True
    assert parse_bool("on") is True
    assert parse_bool("manual") is True


def test_parse_bool_rejects_falsey_form_values():
    from web.services.task_start_inputs import parse_bool

    assert parse_bool(False) is False
    assert parse_bool("") is False
    assert parse_bool("0") is False
    assert parse_bool("false") is False
    assert parse_bool(None) is False
