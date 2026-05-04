"""Admin /admin/tts-speedup-evaluations 路由测试。
覆盖：列表页渲染（模板缺失时走 JSON 兜底）/ 重跑接口 / CSV 导出。
"""
from __future__ import annotations

from unittest.mock import patch
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADMIN_USER_ROW = {
    "id": 1,
    "username": "admin",
    "role": "superadmin",
    "is_active": 1,
    "permissions": None,
}

_FAKE_ROW = {
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
    "audio_pre_path": "/path/to/pre.mp3",
    "audio_post_path": "/path/to/post.mp3",
    "evaluated_at": "2026-05-04 10:00:30",
    "llm_input_tokens": 100, "llm_output_tokens": 50,
    "error_text": None,
}

_FAKE_SUMMARY = {
    "total": 1, "hit_final_pct": 100.0, "avg_overall": 4.0,
    "top_flags": [],
}


def _build_client(monkeypatch):
    """创建 Flask test client，patch 掉所有数据库/启动操作，以 admin 身份登录。"""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: _ADMIN_USER_ROW)

    from web.app import create_app

    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(_ADMIN_USER_ROW["id"])
        sess["_fresh"] = True
    return client, app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_shutdown(monkeypatch):
    from appcore import shutdown_coordinator
    shutdown_coordinator.reset()
    yield
    shutdown_coordinator.reset()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_list_page_renders(monkeypatch):
    client, _app = _build_client(monkeypatch)
    with patch("web.routes.tts_speedup_eval._fetch_rows", return_value=[_FAKE_ROW]), \
         patch("web.routes.tts_speedup_eval._fetch_summary", return_value=_FAKE_SUMMARY):
        resp = client.get("/admin/tts-speedup-evaluations/")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8", errors="ignore")
    # 容忍 JSON 或 HTML 输出（Task 10 之前是 JSON 兜底，之后是模板）
    assert "t-aaa" in body or "rows" in body


def test_retry_endpoint_calls_orchestrator(monkeypatch):
    client, _app = _build_client(monkeypatch)
    with patch("appcore.tts_speedup_eval.retry_evaluation",
               return_value=True) as fake_retry:
        resp = client.post(
            "/admin/tts-speedup-evaluations/42/retry",
            headers={"Accept": "application/json"},
        )
    assert resp.status_code == 200
    fake_retry.assert_called_once()
    assert fake_retry.call_args.kwargs["eval_id"] == 42


def test_export_csv_returns_csv_content(monkeypatch):
    client, _app = _build_client(monkeypatch)
    with patch("web.routes.tts_speedup_eval._fetch_rows", return_value=[_FAKE_ROW]):
        resp = client.get("/admin/tts-speedup-evaluations.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers.get("Content-Type", "")
    body = resp.data.decode("utf-8-sig")
    assert "task_id" in body
    assert "t-aaa" in body
