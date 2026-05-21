import pytest

from appcore.llm_media_optimizer import OptimizedMedia
from pipeline import video_ai_review


def _review_payload():
    return {
        "json": {
            "dimensions": {
                "translation_fidelity": 90,
                "naturalness": 88,
                "tts_consistency": 86,
                "visual_text_alignment": 84,
            },
            "overall_score": 87,
            "verdict": "recommend",
            "verdict_reason": "整体可用",
            "issues": [],
            "highlights": ["节奏自然"],
        },
        "usage": {},
    }


def test_assess_parses_markdown_wrapped_json_text(monkeypatch):
    raw = """```json
{
  "dimensions": {
    "translation_fidelity": 91,
    "naturalness": 89
  },
  "overall_score": 90,
  "verdict": "recommend",
  "verdict_reason": "整体自然",
  "issues": [],
  "highlights": ["表达清晰"]
}
```"""

    monkeypatch.setattr(
        video_ai_review.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: {"text": raw, "usage": {}},
    )

    result = video_ai_review.assess(
        source_language="zh",
        target_language="en",
        source_text="源文案",
        target_text="target script",
        task_id="task-video-review",
        user_id=7,
    )

    assert result["overall_score"] == 90
    assert result["verdict"] == "recommend"
    assert result["raw_response"]["dimensions"]["naturalness"] == 89


def test_assess_reports_stable_error_for_invalid_json_text(monkeypatch):
    raw = '{"dimensions": "unterminated}'

    monkeypatch.setattr(
        video_ai_review.llm_client,
        "invoke_generate",
        lambda *args, **kwargs: {
            "text": raw,
            "json": None,
            "json_parse_error": "Unterminated string starting at: line 1 column 16",
            "usage": {},
        },
    )

    with pytest.raises(video_ai_review.VideoReviewResponseInvalidError) as exc_info:
        video_ai_review.assess(
            source_language="zh",
            target_language="en",
            source_text="源文案",
            target_text="target script",
            task_id="task-video-review",
            user_id=7,
        )

    message = str(exc_info.value)
    assert "AI 视频分析返回内容不是有效 JSON" in message
    assert "Unterminated string" not in message


def test_assess_optimizes_videos_before_llm_and_records_debug(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    target = tmp_path / "target.mp4"
    source_llm = tmp_path / "source.llm.mp4"
    target_llm = tmp_path / "target.llm.mp4"
    source.write_bytes(b"source")
    target.write_bytes(b"target")
    source_llm.write_bytes(b"source-llm")
    target_llm.write_bytes(b"target-llm")
    captured = {"prepare": []}

    def fake_prepare(video_path, policy, output_dir=None):
        captured["prepare"].append((str(video_path), policy.name, output_dir))
        if str(video_path) == str(source):
            return OptimizedMedia(
                original_path=str(source),
                llm_path=str(source_llm),
                optimized=True,
                cleanup_path=str(source_llm),
                original_bytes=6,
                llm_bytes=10,
                command=["ffmpeg", "-i", str(source), str(source_llm)],
                policy_name=policy.name,
            )
        return OptimizedMedia(
            original_path=str(target),
            llm_path=str(target_llm),
            optimized=True,
            cleanup_path=str(target_llm),
            original_bytes=6,
            llm_bytes=10,
            command=["ffmpeg", "-i", str(target), str(target_llm)],
            policy_name=policy.name,
        )

    def fake_invoke(use_case_code, **kwargs):
        captured["use_case_code"] = use_case_code
        captured["kwargs"] = kwargs
        assert source_llm.exists()
        assert target_llm.exists()
        return _review_payload()

    cleanup_calls = []
    monkeypatch.setattr(video_ai_review, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr(video_ai_review, "cleanup_optimized_media", lambda media: cleanup_calls.append(media.llm_path))
    monkeypatch.setattr(video_ai_review.llm_client, "invoke_generate", fake_invoke)

    result = video_ai_review.assess(
        source_language="zh",
        target_language="en",
        source_text="源文案",
        target_text="target script",
        source_video_path=str(source),
        target_video_path=str(target),
        task_id="task-video-review",
        user_id=7,
    )

    assert captured["use_case_code"] == "video_ai_review.assess"
    assert captured["kwargs"]["media"] == [str(source_llm), str(target_llm)]
    assert [entry[1] for entry in captured["prepare"]] == ["vertex_inline_audio", "vertex_inline_audio"]
    debug_call = result["_llm_debug_call"]
    assert debug_call["request_payload"]["media"] == [str(source_llm), str(target_llm)]
    assert debug_call["input_snapshot"][0]["original_video_path"] == str(source)
    assert debug_call["input_snapshot"][1]["original_video_path"] == str(target)
    assert cleanup_calls == [str(source_llm), str(target_llm)]


def test_assess_falls_back_to_original_video_when_optimization_fails(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    captured = {}

    def fake_prepare(video_path, policy, output_dir=None):
        return OptimizedMedia(
            original_path=str(source),
            llm_path=str(source),
            optimized=False,
            cleanup_path=None,
            original_bytes=6,
            llm_bytes=6,
            command=["ffmpeg"],
            error="ffmpeg failed",
            policy_name=policy.name,
        )

    def fake_invoke(use_case_code, **kwargs):
        captured["kwargs"] = kwargs
        return _review_payload()

    monkeypatch.setattr(video_ai_review, "prepare_video_for_llm", fake_prepare)
    monkeypatch.setattr(video_ai_review.llm_client, "invoke_generate", fake_invoke)

    result = video_ai_review.assess(
        source_language="zh",
        target_language="en",
        source_text="源文案",
        target_text="target script",
        source_video_path=str(source),
        task_id="task-video-review",
        user_id=7,
    )

    assert captured["kwargs"]["media"] == [str(source)]
    snapshot = result["_llm_debug_call"]["input_snapshot"][0]
    assert snapshot["llm_video_path"] == str(source)
    assert snapshot["optimized"] is False
    assert snapshot["optimization_error"] == "ffmpeg failed"
