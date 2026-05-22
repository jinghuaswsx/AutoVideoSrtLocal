from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _default_voice_ai_model_selection(monkeypatch):
    from appcore import voice_ai_ranking_task

    monkeypatch.setattr(
        voice_ai_ranking_task,
        "resolve_voice_ai_model_selection",
        lambda: {
            "provider": "openrouter",
            "model": "google/gemini-3.5-flash",
            "source": "default",
        },
    )


def test_queue_voice_ai_ranking_marks_running_and_starts_background(tmp_path):
    from appcore.voice_ai_ranking_task import queue_voice_ai_ranking

    candidates = [{"voice_id": "v1"}, {"voice_id": "v2"}]
    with patch("appcore.voice_ai_ranking_task.task_state.update") as m_update, \
         patch("appcore.voice_ai_ranking_task.runner_lifecycle.start_tracked_thread", return_value=True) as m_start:
        ok = queue_voice_ai_ranking(
            task_id="task-1",
            task={"target_lang": "de"},
            candidates=candidates,
            source_audio_path=tmp_path / "clip.wav",
            task_dir=tmp_path,
            user_id=7,
        )

    assert ok is True
    assert m_update.call_args.kwargs["voice_ai_rank_status"] == "running"
    assert m_update.call_args.kwargs["voice_ai_rank_started_at"]
    assert m_update.call_args.kwargs["voice_ai_rankings"] == []
    assert m_update.call_args.kwargs["voice_ai_rank_model"] == "google/gemini-3.5-flash"
    assert m_update.call_args.kwargs["voice_match_candidates"] == candidates
    assert m_start.call_args.kwargs["project_type"] == "voice_ai_ranking"
    assert m_start.call_args.kwargs["daemon"] is True


def test_run_voice_ai_ranking_job_persists_result_for_current_candidate_signature(tmp_path):
    from appcore.voice_ai_ranking_task import candidate_signature, run_voice_ai_ranking_job

    candidates = [{"voice_id": "v1"}, {"voice_id": "v2"}]
    signature = candidate_signature(candidates)
    enriched = [
        {"voice_id": "v1", "llm_rank": 2, "llm_reason_summary": "略平"},
        {"voice_id": "v2", "llm_rank": 1, "llm_reason_summary": "更贴合"},
    ]
    with patch(
        "appcore.voice_ai_ranking_task.task_state.get",
        return_value={
            "voice_ai_rank_candidate_signature": signature,
            "voice_match_candidates": candidates,
        },
    ), patch(
        "appcore.voice_ai_ranking_task.rank_voice_candidates",
        return_value={
            "status": "done",
            "rankings": [{"voice_id": "v2", "llm_rank": 1, "reason_summary": "更贴合"}],
            "candidates": enriched,
            "model": "google/gemini-3.5-flash",
            "provider": "openrouter",
            "debug": {"status": "done"},
            "usage_log_id": 34567,
        },
    ) as m_rank, patch("appcore.voice_ai_ranking_task.task_state.update") as m_update:
        run_voice_ai_ranking_job(
            task_id="task-1",
            task={"target_lang": "de"},
            candidates=candidates,
            source_audio_path=tmp_path / "clip.wav",
            task_dir=tmp_path,
            user_id=7,
            signature=signature,
        )

    m_rank.assert_called_once()
    payload = m_update.call_args.kwargs
    assert payload["voice_match_candidates"] == enriched
    assert payload["voice_ai_rankings"] == [{"voice_id": "v2", "llm_rank": 1, "reason_summary": "更贴合"}]
    assert payload["voice_ai_rank_status"] == "done"
    assert payload["voice_ai_rank_provider"] == "openrouter"
    assert payload["voice_ai_rank_usage_log_id"] == 34567
    assert payload["voice_ai_rank_cache"]["all"]["candidates"] == enriched
    assert payload["voice_ai_rank_cache"]["all"]["usage_log_id"] == 34567


def test_run_voice_ai_ranking_job_ignores_stale_candidates(tmp_path):
    from appcore.voice_ai_ranking_task import run_voice_ai_ranking_job

    with patch(
        "appcore.voice_ai_ranking_task.task_state.get",
        return_value={"voice_ai_rank_candidate_signature": "newer"},
    ), patch(
        "appcore.voice_ai_ranking_task.rank_voice_candidates",
        side_effect=AssertionError("stale AI ranking should not run"),
    ), patch("appcore.voice_ai_ranking_task.task_state.update") as m_update:
        run_voice_ai_ranking_job(
            task_id="task-1",
            task={"target_lang": "de"},
            candidates=[{"voice_id": "old"}],
            source_audio_path=Path("clip.wav"),
            task_dir=tmp_path,
            user_id=7,
            signature="older",
        )

    m_update.assert_not_called()
