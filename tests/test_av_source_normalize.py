from __future__ import annotations

from pipeline import av_source_normalize


SCRIPT_SEGMENTS = [
    {
        "index": 0,
        "start_time": 0.0,
        "end_time": 2.4,
        "text": "uh this ze lip zee model no key",
    },
    {
        "index": 1,
        "start_time": 2.4,
        "end_time": 5.1,
        "text": "and then carbon zee laws flu gubbe",
    },
]


def test_normalize_source_segments_uses_llm_contract(monkeypatch):
    captured = {}

    def fake_invoke_chat(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        return {
            "json": {
                "sentences": [
                    {
                        "asr_index": 0,
                        "normalized_text": "This Z Lip Z model does not need a key.",
                        "changed": True,
                        "cleanup_note": "Removed filler and repaired ASR wording.",
                    },
                    {
                        "asr_index": 1,
                        "normalized_text": "Then it shows carbon Z laws and the flu gubbe.",
                        "changed": True,
                        "cleanup_note": "Kept uncertain product terms instead of inventing facts.",
                    },
                ]
            }
        }

    monkeypatch.setattr(av_source_normalize.llm_client, "invoke_chat", fake_invoke_chat)

    result = av_source_normalize.normalize_source_segments(
        script_segments=SCRIPT_SEGMENTS,
        source_language="en",
        av_inputs={"target_language": "de", "target_market": "OTHER"},
        user_id=7,
        project_id="task-1",
    )

    assert captured["use_case_code"] == "video_translate.source_normalize"
    assert captured["kwargs"]["temperature"] == 0.2
    assert captured["kwargs"]["response_format"]["type"] == "json_schema"
    system_prompt = captured["kwargs"]["messages"][0]["content"]
    assert "Do not translate" in system_prompt
    assert "Do not merge, split, reorder, or skip sentences" in system_prompt

    assert result["summary"] == {"total_sentences": 2, "changed_sentences": 2}
    assert result["segments"][0]["text"] == "This Z Lip Z model does not need a key."
    assert result["segments"][0]["original_text"] == "uh this ze lip zee model no key"
    assert result["segments"][0]["start_time"] == 0.0
    assert result["segments"][1]["asr_index"] == 1
    assert result["sentences"][1]["cleanup_note"] == "Kept uncertain product terms instead of inventing facts."


def test_normalize_source_segments_preserves_original_when_llm_omits_sentence(monkeypatch):
    monkeypatch.setattr(
        av_source_normalize.llm_client,
        "invoke_chat",
        lambda *args, **kwargs: {
            "json": {
                "sentences": [
                    {
                        "asr_index": 0,
                        "normalized_text": "This Z Lip Z model does not need a key.",
                        "changed": True,
                        "cleanup_note": "Cleaned.",
                    }
                ]
            }
        },
    )

    result = av_source_normalize.normalize_source_segments(
        script_segments=SCRIPT_SEGMENTS,
        source_language="en",
        av_inputs={"target_language": "de", "target_market": "OTHER"},
    )

    assert result["segments"][0]["text"] == "This Z Lip Z model does not need a key."
    assert result["segments"][1]["text"] == "and then carbon zee laws flu gubbe"
    assert result["segments"][1]["source_normalization_status"] == "unchanged"
    assert result["summary"] == {"total_sentences": 2, "changed_sentences": 1}
