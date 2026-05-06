from __future__ import annotations

from datetime import datetime


def test_quality_assessment_list_route_returns_serialized_payload(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import translation_quality as route

    monkeypatch.setattr(
        route,
        "_load_task",
        lambda task_id, project_type: {
            "id": task_id,
            "user_id": 1,
            "type": project_type,
        },
    )
    monkeypatch.setattr(
        route,
        "db_query",
        lambda sql, args=None: [
            {
                "run_id": 4,
                "translation_dimensions": '{"accuracy": 91}',
                "tts_highlights": '["clear"]',
                "created_at": datetime(2026, 5, 6, 9, 0, 0),
                "completed_at": None,
            }
        ],
    )
    monkeypatch.setattr(
        route.task_state,
        "get",
        lambda task_id: {"evals_invalidated_at": "2026-05-06T08:59:00"},
    )

    resp = authed_client_no_db.get(
        "/api/multi-translate/task-quality/quality-assessments"
    )

    assert resp.status_code == 200
    assert resp.get_json() == {
        "assessments": [
            {
                "run_id": 4,
                "translation_dimensions": {"accuracy": 91},
                "tts_highlights": ["clear"],
                "created_at": "2026-05-06T09:00:00",
                "completed_at": None,
            }
        ],
        "task_evals_invalidated_at": "2026-05-06T08:59:00",
    }


def test_quality_assessment_run_route_rejects_non_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.post(
        "/api/multi-translate/task-quality/quality-assessments/run"
    )

    assert resp.status_code == 403
    assert resp.get_json() == {"error": "admin only"}


def test_quality_assessment_run_route_starts_manual_assessment(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import translation_quality as route

    captured = {}
    monkeypatch.setattr(
        route,
        "_load_task",
        lambda task_id, project_type: {
            "id": task_id,
            "user_id": 1,
            "type": project_type,
        },
    )

    def fake_trigger_assessment(**kwargs):
        captured.update(kwargs)
        return 8

    monkeypatch.setattr(route.svc, "trigger_assessment", fake_trigger_assessment)

    resp = authed_client_no_db.post(
        "/api/omni-translate/task-quality/quality-assessments/run"
    )

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True, "run_id": 8}
    assert captured["task_id"] == "task-quality"
    assert captured["project_type"] == "omni_translate"
    assert captured["triggered_by"] == "manual"
    assert captured["user_id"] == 1
    assert captured["run_in_thread"] is True


def test_quality_assessment_run_route_returns_conflict_when_assessment_is_active(
    authed_client_no_db,
    monkeypatch,
):
    from web.routes import translation_quality as route

    monkeypatch.setattr(
        route,
        "_load_task",
        lambda task_id, project_type: {
            "id": task_id,
            "user_id": 1,
            "type": project_type,
        },
    )

    def fake_trigger_assessment(**kwargs):
        raise route.svc.AssessmentInProgressError(9)

    monkeypatch.setattr(route.svc, "trigger_assessment", fake_trigger_assessment)

    resp = authed_client_no_db.post(
        "/api/omni-translate/task-quality/quality-assessments/run"
    )

    assert resp.status_code == 409
    assert resp.get_json() == {"error": "assessment_in_progress", "run_id": 9}
