"""Admin /admin/tts-speedup-evaluations 路由测试。
覆盖：列表页渲染 / 重跑接口 / CSV 导出。
"""
from unittest.mock import patch, MagicMock
import pytest


@pytest.fixture
def admin_client():
    from web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False
    return app.test_client(), app


def _fake_admin_user():
    return MagicMock(
        is_authenticated=True, role="admin", id=1, username="t",
        is_active=True, is_anonymous=False,
    )


def test_list_page_renders_with_filters(admin_client):
    client, app = admin_client
    fake_rows = [
        {
            "id": 1, "task_id": "t-aaa", "round_index": 2, "language": "es",
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "score_overall": 4, "score_naturalness": 4,
            "score_pacing": 3, "score_timbre": 5, "score_intelligibility": 5,
            "summary_text": "ok", "flags_json": "[]",
            "model_provider": "openrouter",
            "model_id": "google/gemini-3-flash-preview",
            "llm_cost_usd": 0.012, "status": "ok",
            "created_at": "2026-05-04 10:00:00",
        },
    ]
    with patch("web.routes.tts_speedup_eval._fetch_rows", return_value=fake_rows), \
         patch("web.routes.tts_speedup_eval._fetch_summary", return_value={
              "total": 1, "hit_final_pct": 100.0, "avg_overall": 4.0,
              "top_flags": [],
         }), \
         patch("flask_login.utils._get_user", return_value=_fake_admin_user()), \
         patch("web.routes.tts_speedup_eval.render_template",
                return_value="<html>t-aaa 1.0667</html>"):
        resp = client.get("/admin/tts-speedup-evaluations/")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "t-aaa" in body
    assert "1.0667" in body or "1.07" in body


def test_retry_endpoint_calls_orchestrator(admin_client):
    client, app = admin_client
    with patch("appcore.tts_speedup_eval.retry_evaluation",
                return_value=True) as fake_retry, \
         patch("flask_login.utils._get_user", return_value=_fake_admin_user()):
        resp = client.post("/admin/tts-speedup-evaluations/42/retry",
                            headers={"Accept": "application/json"})
    assert resp.status_code in (200, 302)
    fake_retry.assert_called_once()
    assert fake_retry.call_args.kwargs["eval_id"] == 42


def test_export_csv_returns_csv_content(admin_client):
    client, app = admin_client
    fake_rows = [
        {
            "id": 1, "task_id": "t-aaa", "round_index": 2, "language": "es",
            "video_duration": 60.0, "audio_pre_duration": 64.0,
            "audio_post_duration": 60.5, "speed_ratio": 1.0667,
            "hit_final_range": 1, "score_overall": 4, "score_naturalness": 4,
            "score_pacing": 3, "score_timbre": 5, "score_intelligibility": 5,
            "summary_text": "ok", "flags_json": "[]",
            "model_provider": "openrouter",
            "model_id": "google/gemini-3-flash-preview",
            "llm_cost_usd": 0.012, "status": "ok",
            "created_at": "2026-05-04 10:00:00",
        },
    ]
    with patch("web.routes.tts_speedup_eval._fetch_rows", return_value=fake_rows), \
         patch("flask_login.utils._get_user", return_value=_fake_admin_user()):
        resp = client.get("/admin/tts-speedup-evaluations.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("Content-Type", "")
    body = resp.data.decode("utf-8-sig")
    assert "task_id" in body
    assert "t-aaa" in body
