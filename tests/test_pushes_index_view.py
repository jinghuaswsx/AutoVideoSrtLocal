"""验证 /pushes/ 视图把 push-module 直连配置注入模板。"""
import importlib

import pytest


@pytest.fixture
def index_client(monkeypatch):
    """Admin flask client，跳过启动恢复，避免 DB 依赖。"""
    monkeypatch.setenv("AUTOVIDEO_BASE_URL", "http://test-upstream:8888")
    monkeypatch.setenv("AUTOVIDEO_API_KEY", "test-key-42")
    monkeypatch.setenv("PUSH_MEDIAS_TARGET", "http://test-downstream/medias")

    import config
    importlib.reload(config)
    from web.routes import pushes
    importlib.reload(pushes)

    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)

    from web.app import create_app

    fake_user = {"id": 1, "username": "test-admin", "role": "admin", "is_active": 1}
    monkeypatch.setattr(
        "web.auth.get_by_id",
        lambda user_id: fake_user if int(user_id) == 1 else None,
    )

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True
    return client


def test_index_renders_push_direct_config(index_client):
    resp = index_client.get("/pushes/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "PUSH_DIRECT_CONFIG" in html
    assert "http://test-upstream:8888" in html
    assert "test-key-42" in html
    assert "http://test-downstream/medias" in html
