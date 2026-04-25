"""Integration tests for _step_asr source-language dispatch.

Verifies that PipelineRunner._step_asr routes ASR work to the correct backend:
- zh / en → Doubao SeedASR (with TOS upload)
- es / pt / etc → ElevenLabs Scribe (local file, no TOS)

Network calls and side-effects (DB, billing, storage) are all mocked.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import appcore.task_state as task_state
from appcore.events import EventBus
from appcore.runtime import PipelineRunner


def _make_task(task_id: str, source_language: str = "zh") -> None:
    task_state.create(task_id, "/video.mp4", "/task_dir", "video.mp4")
    task_state.update(
        task_id,
        audio_path="/task_dir/audio.wav",
        source_language=source_language,
    )


def _make_runner() -> PipelineRunner:
    runner = PipelineRunner(bus=EventBus())
    return runner


def _patch_common_dependencies():
    """Returns a list of patcher context managers covering shared deps."""
    return [
        patch("appcore.runtime.ai_billing.log_request"),
        patch("appcore.runtime.build_asr_artifact", return_value={}),
        patch("appcore.runtime._save_json"),
        patch("appcore.runtime._resolve_original_video_passthrough", return_value={
            "enabled": False,
            "source_full_text": "hello world",
            "reason": None,
            "source_chars": 100,
        }),
        patch("appcore.runtime._seconds_to_request_units", return_value=1),
        patch("pipeline.extract.get_video_duration", return_value=30.0),
        patch("appcore.api_keys.resolve_key", return_value="fake-key"),
    ]


def _enter(patches):
    return [p.__enter__() for p in patches]


def _exit(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


@pytest.fixture
def setup_task_zh(tmp_path, monkeypatch):
    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **k: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **k: None)
    task_id = "test_asr_dispatch_zh"
    _make_task(task_id, source_language="zh")
    return task_id


@pytest.fixture
def setup_task_es(tmp_path, monkeypatch):
    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **k: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **k: None)
    task_id = "test_asr_dispatch_es"
    _make_task(task_id, source_language="es")
    return task_id


@pytest.fixture
def setup_task_en(tmp_path, monkeypatch):
    monkeypatch.setattr(task_state, "_db_upsert", lambda *a, **k: None)
    monkeypatch.setattr(task_state, "_sync_task_to_db", lambda *a, **k: None)
    task_id = "test_asr_dispatch_en"
    _make_task(task_id, source_language="en")
    return task_id


_FAKE_DOUBAO_UTTERANCES = [
    {"text": "hello", "start_time": 0.0, "end_time": 1.0, "words": []},
]
_FAKE_SCRIBE_UTTERANCES = [
    {"text": "hola", "start_time": 0.0, "end_time": 1.0, "words": []},
]


class TestSourceLanguageRouting:
    def test_zh_routes_to_doubao_with_tos_upload(self, setup_task_zh):
        runner = _make_runner()
        common = _patch_common_dependencies()
        try:
            _enter(common)
            with patch("pipeline.asr.transcribe", return_value=_FAKE_DOUBAO_UTTERANCES) as m_doubao, \
                 patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe, \
                 patch("pipeline.storage.upload_file", return_value="https://tos.example/audio.wav") as m_upload, \
                 patch("pipeline.storage.delete_file") as m_delete:
                runner._step_asr(setup_task_zh, "/task_dir")
            m_doubao.assert_called_once()
            m_scribe.assert_not_called()
            m_upload.assert_called_once()
            m_delete.assert_called_once()
        finally:
            _exit(common)

    def test_en_routes_to_doubao_with_tos_upload(self, setup_task_en):
        runner = _make_runner()
        common = _patch_common_dependencies()
        try:
            _enter(common)
            with patch("pipeline.asr.transcribe", return_value=_FAKE_DOUBAO_UTTERANCES) as m_doubao, \
                 patch("pipeline.asr_scribe.transcribe_local_audio") as m_scribe, \
                 patch("pipeline.storage.upload_file", return_value="https://tos.example/audio.wav"), \
                 patch("pipeline.storage.delete_file"):
                runner._step_asr(setup_task_en, "/task_dir")
            m_doubao.assert_called_once()
            m_scribe.assert_not_called()
        finally:
            _exit(common)

    def test_es_routes_to_scribe_no_tos(self, setup_task_es):
        runner = _make_runner()
        common = _patch_common_dependencies()
        try:
            _enter(common)
            with patch("pipeline.asr.transcribe") as m_doubao, \
                 patch("pipeline.asr_scribe.transcribe_local_audio", return_value=_FAKE_SCRIBE_UTTERANCES) as m_scribe, \
                 patch("pipeline.storage.upload_file") as m_upload, \
                 patch("pipeline.storage.delete_file") as m_delete:
                runner._step_asr(setup_task_es, "/task_dir")
            m_doubao.assert_not_called()
            m_scribe.assert_called_once()
            # Scribe path must not touch TOS
            m_upload.assert_not_called()
            m_delete.assert_not_called()
            # Verify language_code passed correctly
            call_kwargs = m_scribe.call_args.kwargs
            assert call_kwargs.get("language_code") == "es"
        finally:
            _exit(common)

    def test_es_billing_records_scribe_provider(self, setup_task_es):
        """The ai_billing log must reflect the actual ASR engine used."""
        runner = _make_runner()
        common = _patch_common_dependencies()
        try:
            _enter(common)
            with patch("appcore.runtime.ai_billing.log_request") as m_billing, \
                 patch("pipeline.asr.transcribe"), \
                 patch("pipeline.asr_scribe.transcribe_local_audio", return_value=_FAKE_SCRIBE_UTTERANCES), \
                 patch("pipeline.storage.upload_file"), \
                 patch("pipeline.storage.delete_file"), \
                 patch("appcore.runtime.build_asr_artifact", return_value={}), \
                 patch("appcore.runtime._save_json"), \
                 patch("appcore.runtime._resolve_original_video_passthrough", return_value={
                     "enabled": False, "source_full_text": "hola", "reason": None, "source_chars": 100,
                 }), \
                 patch("appcore.runtime._seconds_to_request_units", return_value=1), \
                 patch("pipeline.extract.get_video_duration", return_value=30.0), \
                 patch("appcore.api_keys.resolve_key", return_value="fake-key"):
                runner._step_asr(setup_task_es, "/task_dir")
            m_billing.assert_called_once()
            kwargs = m_billing.call_args.kwargs
            assert kwargs["provider"] == "elevenlabs_scribe"
            assert kwargs["model"] == "scribe_v2"
        finally:
            _exit(common)
