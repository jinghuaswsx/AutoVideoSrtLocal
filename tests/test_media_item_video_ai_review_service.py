from __future__ import annotations


def test_media_item_video_ai_review_flask_response_returns_payload_and_status(authed_client_no_db):
    from web.services.media_item_video_ai_review import (
        MediaItemVideoAiReviewOutcome,
        media_item_video_ai_review_flask_response,
    )

    outcome = MediaItemVideoAiReviewOutcome({"review": {"score": 88}}, 207)

    with authed_client_no_db.application.app_context():
        response, status_code = media_item_video_ai_review_flask_response(outcome)

    assert status_code == 207
    assert response.get_json() == {"review": {"score": 88}}


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


def test_start_media_item_video_ai_review_triggers_review():
    from web.services.media_item_video_ai_review import start_media_item_video_ai_review

    review = _FakeReviewModule(run_id="run-123")
    outcome = start_media_item_video_ai_review(
        42,
        user_id=7,
        review_module=review,
    )

    assert outcome.status_code == 200
    assert outcome.payload == {
        "status": "started",
        "run_id": "run-123",
        "channel": "test-channel",
        "model": "test-model",
    }
    assert review.trigger_calls == [
        {
            "source_type": "media_item",
            "source_id": "42",
            "user_id": 7,
            "triggered_by": "manual",
        }
    ]


def test_start_media_item_video_ai_review_returns_conflict_when_running():
    from web.services.media_item_video_ai_review import start_media_item_video_ai_review

    review = _FakeReviewModule(fail_with=_ReviewInProgressError("run-active"))
    outcome = start_media_item_video_ai_review(42, user_id=7, review_module=review)

    assert outcome.status_code == 409
    assert outcome.payload["in_flight_run_id"] == "run-active"
    assert "正在运行" in outcome.payload["error"]


def test_start_media_item_video_ai_review_returns_error_payload_on_failure():
    from web.services.media_item_video_ai_review import start_media_item_video_ai_review

    review = _FakeReviewModule(fail_with=RuntimeError("boom"))
    outcome = start_media_item_video_ai_review(42, user_id=7, review_module=review)

    assert outcome.status_code == 500
    assert outcome.payload == {"error": "boom"}


def test_get_media_item_video_ai_review_returns_latest_review():
    from web.services.media_item_video_ai_review import get_media_item_video_ai_review

    review = _FakeReviewModule(latest={"score": 88})
    outcome = get_media_item_video_ai_review(42, review_module=review)

    assert outcome.status_code == 200
    assert outcome.payload == {"review": {"score": 88}}
    assert review.latest_calls == [("media_item", "42")]
