from __future__ import annotations

from types import SimpleNamespace


class _ReviewInProgressError(RuntimeError):
    def __init__(self, run_id: str):
        super().__init__(run_id)
        self.run_id = run_id


class _FakeReviewModule:
    CHANNEL = "test-channel"
    MODEL = "test-model"
    ReviewInProgressError = _ReviewInProgressError

    def __init__(self, *, run_id: str = "run-1", latest=None, fail_with=None):
        self.run_id = run_id
        self.latest = latest
        self.fail_with = fail_with
        self.trigger_calls = []
        self.latest_calls = []

    def trigger_review(self, **kwargs):
        self.trigger_calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        return self.run_id

    def latest_review(self, source_type, source_id):
        self.latest_calls.append((source_type, source_id))
        return self.latest


def test_start_task_video_ai_review_returns_not_found_when_task_missing():
    from web.services.task_video_ai_review import start_task_video_ai_review

    review = _FakeReviewModule()
    outcome = start_task_video_ai_review(
        "task-1",
        user=SimpleNamespace(id=7, is_admin=False),
        load_task=lambda task_id: None,
        review_module=review,
    )

    assert outcome.not_found is True
    assert outcome.status_code == 404
    assert review.trigger_calls == []


def test_start_task_video_ai_review_rejects_task_owned_by_another_user():
    from web.services.task_video_ai_review import start_task_video_ai_review

    review = _FakeReviewModule()
    outcome = start_task_video_ai_review(
        "task-1",
        user=SimpleNamespace(id=7, is_admin=False),
        load_task=lambda task_id: {"_user_id": 8},
        review_module=review,
    )

    assert outcome.not_found is True
    assert outcome.status_code == 404
    assert review.trigger_calls == []


def test_start_task_video_ai_review_triggers_review_for_owner():
    from web.services.task_video_ai_review import start_task_video_ai_review

    review = _FakeReviewModule(run_id="run-123")
    outcome = start_task_video_ai_review(
        "task-1",
        user=SimpleNamespace(id=7, is_admin=False),
        load_task=lambda task_id: {"_user_id": 7},
        review_module=review,
    )

    assert outcome.not_found is False
    assert outcome.status_code == 200
    assert outcome.payload == {
        "status": "started",
        "run_id": "run-123",
        "channel": "test-channel",
        "model": "test-model",
    }
    assert review.trigger_calls == [
        {
            "source_type": "av_sync_task",
            "source_id": "task-1",
            "user_id": 7,
            "triggered_by": "manual",
        }
    ]


def test_start_task_video_ai_review_returns_conflict_when_review_is_running():
    from web.services.task_video_ai_review import start_task_video_ai_review

    review = _FakeReviewModule(fail_with=_ReviewInProgressError("run-active"))
    outcome = start_task_video_ai_review(
        "task-1",
        user=SimpleNamespace(id=7, is_admin=True),
        load_task=lambda task_id: {"_user_id": 8},
        review_module=review,
    )

    assert outcome.not_found is False
    assert outcome.status_code == 409
    assert outcome.payload["in_flight_run_id"] == "run-active"
    assert "正在运行" in outcome.payload["error"]


def test_get_task_video_ai_review_returns_latest_review_and_invalidation_marker():
    from web.services.task_video_ai_review import get_task_video_ai_review

    review = _FakeReviewModule(latest={"score": 91})
    task_state = SimpleNamespace(get=lambda task_id: {"evals_invalidated_at": "2026-05-05T10:00:00"})

    outcome = get_task_video_ai_review(
        "task-1",
        user=SimpleNamespace(id=7, is_admin=False),
        load_task=lambda task_id: {"_user_id": 7},
        review_module=review,
        task_state_module=task_state,
    )

    assert outcome.not_found is False
    assert outcome.status_code == 200
    assert outcome.payload == {
        "review": {"score": 91},
        "task_evals_invalidated_at": "2026-05-05T10:00:00",
    }
    assert review.latest_calls == [("av_sync_task", "task-1")]
