import json

from pipeline.localization import build_localized_translation_messages
from pipeline.translate import _generate_localized_translation_batched


def test_build_messages_without_batch_context_matches_existing_user_content():
    segments = [{"index": 0, "text": "seg 0"}]

    messages = build_localized_translation_messages(
        "full text",
        segments,
        source_language="en",
    )

    assert messages[1]["content"] == (
        "Source English full text:\n"
        "full text\n\n"
        "Source English segments:\n"
        f"{json.dumps(segments, ensure_ascii=False, indent=2)}"
    )
    assert "GLOBAL CONTEXT" not in messages[1]["content"]


def test_build_messages_appends_batch_context_when_present():
    messages = build_localized_translation_messages(
        "full text",
        [{"index": 0, "text": "seg 0"}],
        source_language="en",
        batch_context="GLOBAL CONTEXT\nPrevious batch translation: translated 0",
    )

    assert messages[1]["content"].endswith(
        "\n\nGLOBAL CONTEXT\nPrevious batch translation: translated 0"
    )


def test_second_batch_gets_global_context(monkeypatch):
    calls = []

    def fake_single(source, batch, **kwargs):
        calls.append(kwargs.get("batch_context"))
        first_index = int(batch[0]["index"])
        return {
            "full_text": f"translated {first_index}",
            "sentences": [
                {
                    "index": 0,
                    "text": f"translated {first_index}",
                    "source_segment_indices": [first_index],
                }
            ],
            "_messages": [],
        }

    monkeypatch.setattr(
        "pipeline.translate._generate_localized_translation_single",
        fake_single,
    )

    segs = [{"index": i, "text": f"seg {i}"} for i in range(24)]
    result = _generate_localized_translation_batched(
        "full text",
        segs,
        variant="normal",
        custom_system_prompt=None,
        use_case="video_translate.localize",
        user_id=None,
        batch_size=12,
    )

    assert result["full_text"] == "translated 0 translated 12"
    assert calls[0] is None
    assert "GLOBAL CONTEXT" in calls[1]
    assert "Full source script:\nfull text" in calls[1]
    assert "Previous batch translation" in calls[1]
    assert "translated 0" in calls[1]


def test_global_context_truncates_very_long_source(monkeypatch):
    calls = []

    def fake_single(source, batch, **kwargs):
        calls.append(kwargs.get("batch_context"))
        first_index = int(batch[0]["index"])
        return {
            "full_text": f"translated {first_index}",
            "sentences": [
                {
                    "index": 0,
                    "text": f"translated {first_index}",
                    "source_segment_indices": [first_index],
                }
            ],
            "_messages": [],
        }

    monkeypatch.setattr(
        "pipeline.translate._generate_localized_translation_single",
        fake_single,
    )

    source = "".join(str(i % 10) for i in range(4500))
    segs = [{"index": i, "text": f"seg {i}"} for i in range(24)]
    _generate_localized_translation_batched(
        source,
        segs,
        variant="normal",
        custom_system_prompt=None,
        use_case="video_translate.localize",
        user_id=None,
        batch_size=12,
    )

    expected_source = source[:2000] + "\n...\n" + source[-1000:]
    assert f"Full source script:\n{expected_source}" in calls[1]
