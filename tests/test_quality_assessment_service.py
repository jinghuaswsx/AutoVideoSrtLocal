"""Tests for the async quality-assessment service."""
from __future__ import annotations

from unittest.mock import patch

from web.services import quality_assessment as svc


def _fake_assessment_result():
    return {
        "translation_score": 88,
        "tts_score": 92,
        "translation_dimensions": {"semantic_fidelity": 90, "completeness": 88, "naturalness": 86},
        "tts_dimensions": {"text_recall": 95, "pronunciation_fidelity": 90, "rhythm_match": 91},
        "translation_issues": [],
        "translation_highlights": ["clear"],
        "tts_issues": [],
        "tts_highlights": ["smooth"],
        "verdict": "recommend",
        "verdict_reason": "high scores",
        "raw_response": {},
        "usage": {},
        "elapsed_ms": 1234,
    }


def test_build_inputs_extracts_three_texts():
    task = {
        "utterances": [{"text": "hola amigos"}, {"text": "que tal"}],
        "localized_translation": {"full_text": "hi friends, what's up"},
        "english_asr_result": {"full_text": "hi friends what's up here"},
        "source_language": "es",
        "target_lang": "en",
    }
    inputs = svc._build_inputs(task)
    assert inputs["original_asr"] == "hola amigos que tal"
    assert inputs["translation"] == "hi friends, what's up"
    assert inputs["tts_recognition"] == "hi friends what's up here"
    assert inputs["source_language"] == "es"
    assert inputs["target_language"] == "en"


def test_build_inputs_handles_missing_full_text():
    task = {
        "utterances": [{"text": "hola"}],
        "localized_translation": {"sentences": [{"text": "hi"}, {"text": "world"}]},
        "english_asr_result": {"utterances": [{"text": "hi"}, {"text": "world"}]},
        "source_language": "es",
        "target_lang": "en",
    }
    inputs = svc._build_inputs(task)
    assert inputs["translation"] == "hi world"
    assert inputs["tts_recognition"] == "hi world"


def test_trigger_inserts_pending_row(db_clean):
    with patch("web.services.quality_assessment._run_assessment_job"):
        run_id = svc.trigger_assessment(
            task_id="task-x", project_type="omni_translate",
            triggered_by="auto", user_id=1, run_in_thread=False,
        )
    assert run_id == 1
    row = db_clean.query_one(
        "SELECT status, triggered_by FROM translation_quality_assessments WHERE task_id=%s",
        ("task-x",),
    )
    assert row["status"] == "pending"
    assert row["triggered_by"] == "auto"


def test_second_trigger_when_first_pending_returns_409(db_clean):
    with patch("web.services.quality_assessment._run_assessment_job"):
        first = svc.trigger_assessment(
            task_id="task-y", project_type="omni_translate",
            triggered_by="auto", user_id=1, run_in_thread=False,
        )
        try:
            svc.trigger_assessment(
                task_id="task-y", project_type="omni_translate",
                triggered_by="manual", user_id=1, run_in_thread=False,
            )
            assert False, "expected error"
        except svc.AssessmentInProgressError as exc:
            assert exc.run_id == first


def test_run_assessment_writes_done_row(db_clean):
    db_clean.execute(
        "INSERT INTO translation_quality_assessments "
        "(task_id, project_type, run_id, model, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("task-z", "omni_translate", 1, "gemini-3.1-flash-lite-preview", "pending"),
    )
    fake_task = {
        "utterances": [{"text": "hola"}],
        "localized_translation": {"full_text": "hi"},
        "english_asr_result": {"full_text": "hi"},
        "source_language": "es",
        "target_lang": "en",
    }
    with patch("appcore.task_state.get", return_value=fake_task), \
         patch("pipeline.translation_quality.assess", return_value=_fake_assessment_result()):
        svc._run_assessment_job(task_id="task-z", project_type="omni_translate", run_id=1, user_id=1)
    row = db_clean.query_one(
        "SELECT status, translation_score, tts_score, verdict FROM translation_quality_assessments "
        "WHERE task_id=%s AND run_id=%s",
        ("task-z", 1),
    )
    assert row["status"] == "done"
    assert row["translation_score"] == 88
    assert row["tts_score"] == 92
    assert row["verdict"] == "recommend"


def test_run_assessment_writes_failed_row_on_exception(db_clean):
    db_clean.execute(
        "INSERT INTO translation_quality_assessments "
        "(task_id, project_type, run_id, model, status) "
        "VALUES (%s, %s, %s, %s, %s)",
        ("task-fail", "omni_translate", 1, "gemini-3.1-flash-lite-preview", "pending"),
    )
    fake_task = {
        "utterances": [{"text": "hola"}],
        "localized_translation": {"full_text": "hi"},
        "english_asr_result": {"full_text": "hi"},
        "source_language": "es", "target_lang": "en",
    }
    with patch("appcore.task_state.get", return_value=fake_task), \
         patch("pipeline.translation_quality.assess", side_effect=RuntimeError("boom")):
        svc._run_assessment_job(task_id="task-fail", project_type="omni_translate", run_id=1, user_id=1)
    row = db_clean.query_one(
        "SELECT status, error_text FROM translation_quality_assessments "
        "WHERE task_id=%s AND run_id=%s",
        ("task-fail", 1),
    )
    assert row["status"] == "failed"
    assert "boom" in (row["error_text"] or "")
