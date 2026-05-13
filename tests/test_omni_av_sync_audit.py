from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from appcore import task_state


@pytest.fixture(autouse=True)
def _clean_task_state():
    task_state._tasks.clear()
    yield
    task_state._tasks.clear()


class _FakeProfile:
    def __init__(self, tts_engine):
        self._tts_engine = tts_engine

    def get_tts_engine(self):
        return self._tts_engine


class _FakeRunner:
    user_id = 9

    def __init__(self, tts_engine=None):
        self.profile = _FakeProfile(tts_engine)
        self.step_calls = []

    def _set_step(self, task_id, step, status, message=""):
        self.step_calls.append((task_id, step, status, message))

    def _resolve_av_inputs(self, task):
        return {"target_language": "de", "target_language_name": "德语"}

    def _target_language_name(self, av_inputs):
        return av_inputs["target_language_name"]

    def _resolve_av_voice(self, task):
        return {"id": "voice-1"}, "voice-1", None


class _FakeTtsEngine:
    def __init__(self, tmp_path: Path, durations: list[float]):
        self.tmp_path = tmp_path
        self.durations = list(durations)
        self.calls = []

    def synthesize_full(self, segments, voice_id, output_dir, **kwargs):
        self.calls.append((segments, voice_id, output_dir, kwargs))
        duration = self.durations.pop(0)
        path = self.tmp_path / f"{kwargs.get('variant', 'fix')}.mp3"
        path.write_bytes(b"mp3")
        segment = dict(segments[0])
        segment["tts_path"] = str(path)
        segment["tts_duration"] = duration
        segment.setdefault("speed", 1.0)
        return {"full_audio_path": str(path), "segments": [segment]}


def _create_task(tmp_path, *, mode="report_only", sentences=None):
    task_id = "omni-av-audit"
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    task = task_state.create(task_id, str(video_path), str(tmp_path), "video.mp4")
    task.update({
        "source_language": "en",
        "plugin_config": {
            "asr_post": "asr_normalize",
            "shot_decompose": False,
            "translate_algo": "av_sentence",
            "source_anchored": False,
            "tts_strategy": "sentence_reconcile",
            "subtitle": "sentence_units",
            "voice_separation": True,
            "loudness_match": True,
            "av_sync_audit": mode,
        },
        "variants": {
            "av": {
                "sentences": sentences if sentences is not None else [
                    {
                        "asr_index": 0,
                        "start_time": 0.0,
                        "end_time": 2.0,
                        "target_duration": 2.0,
                        "text": "This is the original translated sentence.",
                        "tts_duration": 2.2,
                        "duration_ratio": 1.1,
                        "tts_path": str(tmp_path / "s0.mp3"),
                        "speed": 1.0,
                    },
                    {
                        "asr_index": 1,
                        "start_time": 2.0,
                        "end_time": 4.0,
                        "target_duration": 2.0,
                        "text": "Second sentence.",
                        "tts_duration": 2.0,
                        "duration_ratio": 1.0,
                        "tts_path": str(tmp_path / "s1.mp3"),
                        "speed": 1.0,
                    },
                ],
            },
        },
    })
    for sentence in task["variants"]["av"].get("sentences") or []:
        path = sentence.get("tts_path")
        if path:
            Path(path).write_bytes(b"mp3")
    return task_id, str(video_path)


def _create_multi_task(tmp_path, *, segments=None):
    task_id = "multi-av-audit"
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    task_state.create(task_id, str(video_path), str(tmp_path), "video.mp4")
    tts_segments = segments if segments is not None else [
        {
            "index": 0,
            "text": "Grab the handle and pull.",
            "translated": "Zieh am Griff.",
            "tts_text": "Zieh am Griff.",
            "source_segment_indices": [0],
            "tts_duration": 2.4,
            "tts_path": str(tmp_path / "multi-s0.mp3"),
        },
        {
            "index": 1,
            "text": "The light flashes.",
            "translated": "Das Licht blinkt.",
            "tts_text": "Das Licht blinkt.",
            "source_segment_indices": [1],
            "tts_duration": 1.6,
            "tts_path": str(tmp_path / "multi-s1.mp3"),
        },
    ]
    for segment in tts_segments:
        path = segment.get("tts_path")
        if path:
            Path(path).write_bytes(b"mp3")
    task_state.update(
        task_id,
        source_language="en",
        target_lang="de",
        script_segments=[
            {"index": 0, "start_time": 0.0, "end_time": 2.0, "text": "Grab the handle and pull."},
            {"index": 1, "start_time": 2.0, "end_time": 4.0, "text": "The light flashes."},
        ],
        variants={
            "normal": {
                "segments": tts_segments,
                "tts_audio_path": str(tmp_path / "tts_full.normal.mp3"),
                "localized_translation": {
                    "full_text": "Zieh am Griff. Das Licht blinkt.",
                    "sentences": [
                        {"index": 0, "text": "Zieh am Griff.", "source_segment_indices": [0]},
                        {"index": 1, "text": "Das Licht blinkt.", "source_segment_indices": [1]},
                    ],
                },
            },
        },
    )
    return task_id, str(video_path)


def test_run_skips_when_av_sentences_missing(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(tmp_path, sentences=[])
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(side_effect=AssertionError("should not call Doubao")),
    )

    omni_av_sync_audit.run(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    report = task["artifacts"]["av_sync_audit"]
    assert report["status"] == "skipped_missing_av_sentences"
    assert task["variants"]["av"]["av_sync_audit"]["status"] == "skipped_missing_av_sentences"


def test_multi_report_only_writes_audit_without_mutating_normal_segments(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_multi_task(tmp_path)
    task_state.update(task_id, llm_debug_refs={"av_sync_audit": [
        {"id": "av_sync_audit.diagnose", "path": "old-diagnose.json"},
        {"id": "av_sync_audit.verify", "path": "old-verify.json"},
    ]})
    before = list(task_state.get(task_id)["variants"]["normal"]["segments"])
    generate = MagicMock(return_value={
        "text": "00:00-00:02 画面中有人拉动把手，字幕显示 Zieh am Griff，音频结尾略拖到下一镜头。",
    })
    chat = MagicMock(return_value={
        "json": {
            "issues": [{
                "asr_index": 0,
                "severity": "medium",
                "problem_type": "audio_too_long",
                "evidence": "程序候选显示音频比目标画面长 0.40 秒，Doubao 视频笔记也提到结尾略拖。",
                "safe_action": "shorten_text",
                "suggested_text": "Zieh am Griff.",
                "confidence": 0.84,
            }],
            "summary": "发现 1 个候选同步问题",
        },
    })
    monkeypatch.setattr(omni_av_sync_audit.llm_client, "invoke_generate", generate)
    monkeypatch.setattr(omni_av_sync_audit.llm_client, "invoke_chat", chat)

    omni_av_sync_audit.run_report_only(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    assert task["variants"]["normal"]["segments"] == before
    report = task["artifacts"]["av_sync_audit"]
    assert report["mode"] == "report_only"
    assert report["summary"]["diagnosed"] == 1
    assert report["summary"]["accepted"] == 0
    assert report["summary"]["applied"] == 0
    assert report["analysis_only"] is True
    assert "辅助分析" in report["verification"]["summary"]
    assert report["items"], "workbench should be able to render the report"
    assert task["variants"]["normal"]["av_sync_audit"]["status"] == "done"

    assert generate.call_args.args[0] == "omni_av_sync.understand"
    prompt = generate.call_args.kwargs["prompt"]
    assert "已经合成的视频" in prompt
    assert "不要输出 JSON" in prompt
    assert "只输出 JSON" not in prompt
    assert "Grab the handle and pull." not in prompt
    assert generate.call_args.kwargs.get("response_schema") is None
    assert generate.call_args.kwargs["media"] == [video_path]

    assess_call = chat.call_args_list[0]
    assert assess_call.args[0] == "omni_av_sync.assess"
    assess_payload = assess_call.kwargs["messages"][1]["content"]
    assert "video_understanding" in assess_payload
    assert "program_candidates" in assess_payload
    assert "音频太长" in assess_payload
    assert "必须使用中文表述" in assess_call.kwargs["messages"][0]["content"]
    assert "处理建议" in assess_call.kwargs["messages"][0]["content"]
    assert chat.call_count == 1
    refs = task["llm_debug_refs"]["av_sync_audit"]
    assert [ref["id"] for ref in refs] == [
        "av_sync_audit.understand",
        "av_sync_audit.assess",
    ]


def test_multi_report_only_retains_program_candidate_when_assess_misses(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_multi_task(tmp_path, segments=[
        {
            "index": 0,
            "text": "Grab the handle and pull.",
            "translated": "Zieh am Griff.",
            "tts_text": "Zieh am Griff.",
            "source_segment_indices": [0],
            "tts_duration": 2.7,
            "tts_path": str(tmp_path / "multi-s0.mp3"),
        },
        {
            "index": 1,
            "text": "The light flashes.",
            "translated": "Das Licht blinkt.",
            "tts_text": "Das Licht blinkt.",
            "source_segment_indices": [1],
            "tts_duration": 2.0,
            "tts_path": str(tmp_path / "multi-s1.mp3"),
        },
    ])
    for segment in task_state.get(task_id)["variants"]["normal"]["segments"]:
        Path(segment["tts_path"]).write_bytes(b"mp3")

    generate = MagicMock(return_value={"text": "画面理解：第一句对应拉把手动作，随后切到灯闪。"})
    chat = MagicMock(return_value={"json": {"issues": [], "summary": "Gemini 未返回结构化问题"}})
    monkeypatch.setattr(omni_av_sync_audit.llm_client, "invoke_generate", generate)
    monkeypatch.setattr(omni_av_sync_audit.llm_client, "invoke_chat", chat)

    omni_av_sync_audit.run_report_only(_FakeRunner(), task_id, video_path, str(tmp_path))

    report = task_state.get(task_id)["artifacts"]["av_sync_audit"]
    issue = report["diagnosis"]["issues"][0]
    assert report["summary"]["diagnosed"] == 1
    assert issue["problem_type"] == "audio_too_long"
    assert issue["asr_index"] == 0
    assert "音频太长" in issue["evidence"]
    assert "0.70s" in issue["timing_detail"]
    assert "重写" in issue["recommendation"]

    assess_payload = chat.call_args_list[0].kwargs["messages"][1]["content"]
    assert "program_candidates" in assess_payload
    assert "duration_delta" in assess_payload
    assert "duration_ratio" in assess_payload
    assert chat.call_count == 1


def test_multi_report_only_includes_final_subtitle_context_in_assess_prompt(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_multi_task(tmp_path)
    srt_path = tmp_path / "subtitle.normal.srt"
    srt_path.write_text(
        "1\n00:00:00,000 --> 00:00:02,000\nFINAL SRT UNIQUE LINE\n",
        encoding="utf-8",
    )
    task = task_state.get(task_id)
    variants = dict(task["variants"])
    normal = dict(variants["normal"])
    normal["srt_path"] = str(srt_path)
    normal["corrected_subtitle"] = {"srt_content": srt_path.read_text(encoding="utf-8")}
    variants["normal"] = normal
    task_state.update(
        task_id,
        variants=variants,
        srt_path=str(srt_path),
        corrected_subtitle=normal["corrected_subtitle"],
    )
    generate = MagicMock(return_value={"text": "视频理解笔记：字幕与动作基本对应。"})
    chat = MagicMock(return_value={"json": {"issues": [], "summary": "ok"}})
    monkeypatch.setattr(omni_av_sync_audit.llm_client, "invoke_generate", generate)
    monkeypatch.setattr(omni_av_sync_audit.llm_client, "invoke_chat", chat)

    runner = _FakeRunner()
    omni_av_sync_audit.run_report_only(runner, task_id, video_path, str(tmp_path))

    prompt = chat.call_args_list[0].kwargs["messages"][1]["content"]
    assert "subtitle_srt" in prompt
    assert "FINAL SRT UNIQUE LINE" in prompt


def test_multi_report_only_accepts_unstructured_doubao_video_notes(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_multi_task(tmp_path, segments=[
        {
            "index": 0,
            "text": "Grab the handle and pull.",
            "translated": "Zieh am Griff.",
            "tts_text": "Zieh am Griff.",
            "source_segment_indices": [0],
            "tts_duration": 2.0,
            "tts_path": str(tmp_path / "multi-s0.mp3"),
        },
        {
            "index": 1,
            "text": "The light flashes.",
            "translated": "Das Licht blinkt.",
            "tts_text": "Das Licht blinkt.",
            "source_segment_indices": [1],
            "tts_duration": 2.0,
            "tts_path": str(tmp_path / "multi-s1.mp3"),
        },
    ])
    for segment in task_state.get(task_id)["variants"]["normal"]["segments"]:
        Path(segment["tts_path"]).write_bytes(b"mp3")

    notes = "视频理解笔记：画面先拉把手，再出现灯闪；没有观察到明显口型或字幕错位。"
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={"text": notes, "json": None}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={"json": {"issues": [], "summary": "未发现明显同步问题"}}),
    )

    runner = _FakeRunner()
    omni_av_sync_audit.run_report_only(runner, task_id, video_path, str(tmp_path))

    report = task_state.get(task_id)["artifacts"]["av_sync_audit"]
    assert report["status"] == "done"
    assert report["diagnosis"]["video_understanding"]["summary"] == notes
    assert "parse_error" not in report["diagnosis"]
    assert runner.step_calls[-1][1:3] == ("av_sync_audit", "done")


def test_report_only_builds_chinese_actionable_human_report(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_multi_task(tmp_path)
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={"text": "画面理解：第一句音频略拖，后半句压到下一镜头。"}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={
            "json": {
                "issues": [{
                    "asr_index": 0,
                    "severity": "high",
                    "problem_type": "audio_too_long",
                    "evidence": "TTS duration exceeds the visual window.",
                    "safe_action": "shorten_text",
                    "confidence": 0.92,
                }],
                "summary": "发现 1 个同步风险",
            },
        }),
    )

    omni_av_sync_audit.run_report_only(_FakeRunner(), task_id, video_path, str(tmp_path))

    report = task_state.get(task_id)["artifacts"]["av_sync_audit"]
    issue = report["diagnosis"]["issues"][0]
    assert report["verification"]["accepted_issues"] == []
    assert issue["sync_point"] == "ASR 0（00:00.00-00:02.00）"
    assert issue["sentence_text"] == "Zieh am Griff."
    assert issue["timing_detail"] == "目标画面 2.00s，TTS 音频 2.40s，音频太长 0.40s（120%）"
    assert "不建议只靠音频变速" in issue["recommendation"]
    assert "重写/压缩文案后重新生成音频" in issue["recommendation"]
    human_report = report["human_report"]
    assert "问题同步点：ASR 0（00:00.00-00:02.00）" in human_report
    assert "问题句子：Zieh am Griff." in human_report
    assert "音频太长 0.40s" in human_report
    assert "画面对不上" in human_report
    assert "处理建议：" in human_report
    assert "重写/压缩文案后重新生成音频" in human_report
    assert report["items"][0]["label"] == "中文审计结论"
    assert report["items"][0]["content"] == human_report


def test_multi_report_only_skips_when_normal_segments_missing(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_multi_task(tmp_path, segments=[])
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(side_effect=AssertionError("should not call diagnosis")),
    )

    omni_av_sync_audit.run_report_only(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    assert task["artifacts"]["av_sync_audit"]["status"] == "skipped_missing_report_sentences"
    assert task["variants"]["normal"]["av_sync_audit"]["status"] == "skipped_missing_report_sentences"


def test_report_only_writes_report_without_mutating_sentences(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(tmp_path, mode="report_only")
    before = list(task_state.get(task_id)["variants"]["av"]["sentences"])
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={"text": "画面理解：第一句配音晚于动作，需要缩短。"}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(side_effect=[
            {
                "json": {
                    "issues": [{
                        "asr_index": 0,
                        "severity": "high",
                        "problem_type": "speech_late",
                        "evidence": "配音晚于动作",
                        "safe_action": "shorten_text",
                        "suggested_text": "Short sentence.",
                        "confidence": 0.9,
                    }],
                    "summary": "发现 1 个问题",
                },
            },
            {
                "json": {
                    "accepted_issues": [{
                        "asr_index": 0,
                        "severity": "high",
                        "problem_type": "speech_late",
                        "accepted": True,
                        "reason": "成立",
                        "safe_action": "shorten_text",
                        "final_text": "Short sentence.",
                    }],
                    "rejected_count": 0,
                    "summary": "复核通过",
                },
            },
        ]),
    )

    omni_av_sync_audit.run(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    assert task["variants"]["av"]["sentences"] == before
    report = task["artifacts"]["av_sync_audit"]
    assert report["mode"] == "report_only"
    assert report["summary"]["diagnosed"] == 1
    assert report["summary"]["accepted"] == 1
    assert report["summary"]["applied"] == 0


def test_report_contains_chinese_readable_av_sync_findings(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(
        tmp_path,
        mode="report_only",
        sentences=[
            {
                "asr_index": 6,
                "source_text": "Show how the handle locks into place.",
                "start_time": 12.0,
                "end_time": 14.64,
                "target_duration": 2.64,
                "text": "This handle locks securely into place with one smooth motion.",
                "tts_duration": 5.381,
                "duration_ratio": 2.04,
                "tts_path": str(tmp_path / "s6.mp3"),
                "speed": 1.0,
            },
        ],
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={
            "json": {
                "issues": [{
                    "asr_index": 6,
                    "severity": "high",
                    "problem_type": "duration_risk",
                    "evidence": (
                        "TTS duration 5.381s exceeds allocated target duration 2.640s, "
                        "duration ratio 2.04 is far above the safe range upper bound 1.05."
                    ),
                    "safe_action": "shorten_text",
                    "confidence": 0.92,
                }],
                "summary": "There is one high-risk TTS duration mismatch issue.",
            },
        }),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={
            "json": {
                "accepted_issues": [{
                    "asr_index": 6,
                    "severity": "high",
                    "problem_type": "duration_risk",
                    "accepted": True,
                    "reason": "The generated audio is more than twice the available visual slot.",
                    "safe_action": "shorten_text",
                    "final_text": "The handle locks in smoothly.",
                }],
                "rejected_count": 0,
                "summary": "Retained one high-risk issue.",
            },
        }),
    )

    omni_av_sync_audit.run(_FakeRunner(), task_id, video_path, str(tmp_path))

    report = task_state.get(task_id)["artifacts"]["av_sync_audit"]
    finding = report["readable_findings"][0]
    assert "中文审计结论" in report["readable_summary"]
    assert finding["severity_label"] == "高风险"
    assert "ASR 6" in finding["sync_point"]
    assert "12.00" in finding["sync_point"]
    assert "14.64" in finding["sync_point"]
    assert "音频太长" in finding["problem"]
    assert "5.38s" in finding["timing"]
    assert "2.64s" in finding["timing"]
    assert "This handle locks securely" in finding["sentence_text"]
    assert "不建议只" in finding["recommendation"]
    assert "音频变速" in finding["recommendation"]
    assert "重写" in finding["recommendation"]
    assert "文案" in finding["recommendation"]
    assert "重新生成音频" in finding["recommendation"]


def test_report_only_registers_prompt_debug_refs(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(tmp_path, mode="report_only")
    monkeypatch.setattr(
        "appcore.llm_bindings.resolve",
        lambda use_case: {"provider": "test-provider", "model": f"{use_case}-model"},
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={"text": "视频理解笔记：画面与字幕基本对应。"}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(side_effect=[
            {"json": {"issues": [], "summary": "评估完成"}},
            {"json": {"accepted_issues": [], "rejected_count": 0, "summary": "复核完成"}},
        ]),
    )

    omni_av_sync_audit.run(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    refs = task["llm_debug_refs"]["av_sync_audit"]
    assert [ref["id"] for ref in refs] == [
        "av_sync_audit.understand",
        "av_sync_audit.assess",
        "av_sync_audit.verify",
    ]
    understand_payload = json.loads((tmp_path / refs[0]["path"]).read_text(encoding="utf-8"))
    assess_payload = json.loads((tmp_path / refs[1]["path"]).read_text(encoding="utf-8"))
    verify_payload = json.loads((tmp_path / refs[2]["path"]).read_text(encoding="utf-8"))
    assert understand_payload["request_payload"]["type"] == "generate"
    assert understand_payload["request_payload"]["use_case_code"] == "omni_av_sync.understand"
    assert understand_payload["request_payload"].get("response_schema") is None
    assert assess_payload["request_payload"]["type"] == "chat"
    assert assess_payload["request_payload"]["use_case_code"] == "omni_av_sync.assess"
    assert verify_payload["request_payload"]["type"] == "chat"
    assert verify_payload["request_payload"]["use_case_code"] == "omni_av_sync.verify"


def test_safe_auto_applies_only_accepted_medium_high_issue(
    monkeypatch, tmp_path,
):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(tmp_path, mode="safe_auto")
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={"text": "视频理解：第一句可通过缩短音频修正。"}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(side_effect=[
            {
                "json": {
                    "issues": [{
                        "asr_index": 0,
                        "severity": "high",
                        "problem_type": "duration_risk",
                        "evidence": "第一句音频略长。",
                        "safe_action": "shorten_text",
                    }],
                    "summary": "发现 1 个问题",
                },
            },
            {
                "json": {
                    "accepted_issues": [
                        {
                            "asr_index": 0,
                            "severity": "high",
                            "problem_type": "duration_risk",
                            "accepted": True,
                            "reason": "更贴近 2 秒",
                            "safe_action": "shorten_text",
                            "final_text": "Short sentence.",
                        },
                        {
                            "asr_index": 1,
                            "severity": "low",
                            "problem_type": "subtitle_risk",
                            "accepted": True,
                            "reason": "低风险",
                            "safe_action": "shorten_text",
                            "final_text": "Ignored.",
                        },
                    ],
                    "rejected_count": 0,
                    "summary": "复核完成",
                },
            },
        ]),
    )
    monkeypatch.setattr(
        omni_av_sync_audit,
        "_rebuild_tts_full_audio_from_segments",
        lambda task_dir, segments, variant="av": str(tmp_path / "full.mp3"),
    )
    runner = _FakeRunner(_FakeTtsEngine(tmp_path, [2.0]))

    omni_av_sync_audit.run(runner, task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    sentences = task["variants"]["av"]["sentences"]
    assert sentences[0]["text"] == "Short sentence."
    assert sentences[0]["tts_duration"] == 2.0
    assert sentences[0]["duration_ratio"] == 1.0
    assert sentences[1]["text"] == "Second sentence."
    report = task["artifacts"]["av_sync_audit"]
    assert report["summary"]["applied"] == 1
    assert report["applied_fixes"][0]["status"] == "applied"
    assert task["localized_translation"]["full_text"].startswith("Short sentence.")


def test_safe_auto_rolls_back_when_duration_gets_worse(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(tmp_path, mode="safe_auto")
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={"text": "视频理解：第一句需要缩短，但重新生成后可能变差。"}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(side_effect=[
            {
                "json": {
                    "issues": [{
                        "asr_index": 0,
                        "severity": "medium",
                        "problem_type": "duration_risk",
                        "evidence": "第一句音频略长。",
                        "safe_action": "shorten_text",
                    }],
                    "summary": "发现 1 个问题",
                },
            },
            {
                "json": {
                    "accepted_issues": [{
                        "asr_index": 0,
                        "severity": "medium",
                        "problem_type": "duration_risk",
                        "accepted": True,
                        "reason": "尝试缩短",
                        "safe_action": "shorten_text",
                        "final_text": "Short sentence.",
                    }],
                    "rejected_count": 0,
                    "summary": "复核完成",
                },
            },
        ]),
    )
    monkeypatch.setattr(
        omni_av_sync_audit,
        "_rebuild_tts_full_audio_from_segments",
        lambda task_dir, segments, variant="av": str(tmp_path / "full.mp3"),
    )
    runner = _FakeRunner(_FakeTtsEngine(tmp_path, [3.0]))

    omni_av_sync_audit.run(runner, task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    sentences = task["variants"]["av"]["sentences"]
    assert sentences[0]["text"] == "This is the original translated sentence."
    assert sentences[0]["tts_duration"] == 2.2
    report = task["artifacts"]["av_sync_audit"]
    assert report["summary"]["applied"] == 0
    assert report["summary"]["rolled_back"] == 1
    assert report["applied_fixes"][0]["status"] == "rolled_back_not_safer"
