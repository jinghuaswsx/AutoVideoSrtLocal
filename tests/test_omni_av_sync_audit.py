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


def test_report_only_writes_report_without_mutating_sentences(monkeypatch, tmp_path):
    from pipeline import omni_av_sync_audit

    task_id, video_path = _create_task(tmp_path, mode="report_only")
    before = list(task_state.get(task_id)["variants"]["av"]["sentences"])
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_generate",
        MagicMock(return_value={
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
        }),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={
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
        }),
    )

    omni_av_sync_audit.run(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    assert task["variants"]["av"]["sentences"] == before
    report = task["artifacts"]["av_sync_audit"]
    assert report["mode"] == "report_only"
    assert report["summary"]["diagnosed"] == 1
    assert report["summary"]["accepted"] == 1
    assert report["summary"]["applied"] == 0


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
        MagicMock(return_value={"json": {"issues": [], "summary": "诊断完成"}}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={
            "json": {"accepted_issues": [], "rejected_count": 0, "summary": "复核完成"},
        }),
    )

    omni_av_sync_audit.run(_FakeRunner(), task_id, video_path, str(tmp_path))

    task = task_state.get(task_id)
    refs = task["llm_debug_refs"]["av_sync_audit"]
    assert [ref["id"] for ref in refs] == [
        "av_sync_audit.diagnose",
        "av_sync_audit.verify",
    ]
    diagnose_payload = json.loads((tmp_path / refs[0]["path"]).read_text(encoding="utf-8"))
    verify_payload = json.loads((tmp_path / refs[1]["path"]).read_text(encoding="utf-8"))
    assert diagnose_payload["request_payload"]["type"] == "generate"
    assert diagnose_payload["request_payload"]["use_case_code"] == "omni_av_sync.diagnose"
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
        MagicMock(return_value={"json": {"issues": [], "summary": ""}}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={
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
        }),
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
        MagicMock(return_value={"json": {"issues": [], "summary": ""}}),
    )
    monkeypatch.setattr(
        omni_av_sync_audit.llm_client,
        "invoke_chat",
        MagicMock(return_value={
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
        }),
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
