from __future__ import annotations

from types import SimpleNamespace


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


def test_request_payload_from_json_body():
    from web.services.task_start_inputs import request_payload_from

    request_obj = SimpleNamespace(
        is_json=True,
        get_json=lambda silent=False: {"source_language": "en", "interactive_review": True},
        form=SimpleNamespace(to_dict=lambda flat=True: {"ignored": "form"}),
    )

    assert request_payload_from(request_obj) == {
        "source_language": "en",
        "interactive_review": True,
    }


def test_request_payload_from_form_body():
    from web.services.task_start_inputs import request_payload_from

    request_obj = SimpleNamespace(
        is_json=False,
        get_json=lambda silent=False: {"ignored": "json"},
        form=SimpleNamespace(to_dict=lambda flat=True: {"source_language": "zh", "interactive_review": "on"}),
    )

    assert request_payload_from(request_obj) == {
        "source_language": "zh",
        "interactive_review": "on",
    }


def test_json_payload_from_silent_json_body():
    from web.services.task_start_inputs import json_payload_from

    request_obj = SimpleNamespace(get_json=lambda silent=False: {"index": 1})

    assert json_payload_from(request_obj) == {"index": 1}


def test_json_payload_from_empty_or_invalid_body_defaults_to_empty_dict():
    from web.services.task_start_inputs import json_payload_from

    request_obj = SimpleNamespace(get_json=lambda silent=False: None)

    assert json_payload_from(request_obj) == {}
