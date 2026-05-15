import pytest
from pathlib import Path


@pytest.fixture
def superadmin_client_no_db(monkeypatch):
    """Flask client authenticated as the real superadmin shape, without DB IO."""
    monkeypatch.setattr("web.app._run_startup_recovery", lambda: None)
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: None)
    monkeypatch.setattr("web.app.mark_interrupted_bulk_translate_tasks", lambda: None)
    monkeypatch.setattr("web.app._seed_default_prompts", lambda: None)
    monkeypatch.setattr("appcore.db.execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_language_codes",
        lambda: ["de", "fr", "es", "it", "pt", "ja", "nl", "sv", "fi", "en"],
    )
    from web.app import create_app

    fake_user = {
        "id": 1,
        "username": "admin",
        "role": "superadmin",
        "is_active": 1,
    }

    monkeypatch.setattr("web.auth.get_by_id", lambda user_id: fake_user if int(user_id) == 1 else None)

    app = create_app()
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "1"
        session["_fresh"] = True

    return client


def test_tos_file_management_page_requires_superadmin(monkeypatch, authed_client_no_db):
    resp = authed_client_no_db.get("/admin/tos-files")
    assert resp.status_code in {302, 403}


def test_tos_file_management_page_renders_for_superadmin(superadmin_client_no_db, monkeypatch):
    def mock_latest_scan(channel):
        return {
            "total_files": 2,
            "total_bytes": 2048,
            "target_missing_count": 1,
            "local_missing_count": 0,
            "failed_count": 0,
            "modules": [],
        }

    def mock_tos_channel_options():
        return [("tos_main", "3482299@qq.com CJH"), ("tos_wj", "495828376@qq.com WJ")]

    monkeypatch.setattr(
        "web.routes.tos_file_management.tos_file_management.latest_scan_summary",
        mock_latest_scan,
    )
    monkeypatch.setattr(
        "web.routes.tos_file_management.infra_credentials.tos_channel_options",
        mock_tos_channel_options,
    )

    resp = superadmin_client_no_db.get("/admin/tos-files?channel=tos_wj")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "TOS文件管理" in html
    assert "495828376@qq.com WJ" in html


def test_layout_contains_superadmin_tos_file_management_menu():
    source = Path("web/templates/layout.html").read_text(encoding="utf-8")
    assert "TOS文件管理" in source
    assert "current_user.is_superadmin" in source
    assert "tos_file_management.page" in source


def test_scan_post_invokes_inventory_scan(superadmin_client_no_db, monkeypatch):
    calls = []

    def mock_run_scan(channel, triggered_by=None):
        calls.append((channel, triggered_by))
        return {"scan_run_id": 1, "summary": {}}

    monkeypatch.setattr(
        "web.routes.tos_file_management.tos_file_management.run_inventory_scan",
        mock_run_scan,
    )
    monkeypatch.setattr(
        "web.routes.tos_file_management.tos_file_management.latest_scan_summary",
        lambda channel: None,
    )
    monkeypatch.setattr(
        "web.routes.tos_file_management.infra_credentials.tos_channel_options",
        lambda: [("tos_main", "3482299@qq.com CJH"), ("tos_wj", "495828376@qq.com WJ")],
    )

    resp = superadmin_client_no_db.post("/admin/tos-files/scan", data={"channel": "tos_wj"})
    assert resp.status_code in {302, 200}
    assert calls and calls[0][0] == "tos_wj"


def test_sync_post_invokes_dry_run(superadmin_client_no_db, monkeypatch):
    calls = []

    def mock_run_sync(target_channel_code, dry_run=True, module_code=None, triggered_by=None):
        calls.append((target_channel_code, dry_run, module_code, triggered_by))
        return {"sync_run_id": 1, "result": {}}

    monkeypatch.setattr(
        "web.routes.tos_file_management.tos_file_management.run_channel_sync",
        mock_run_sync,
    )
    monkeypatch.setattr(
        "web.routes.tos_file_management.tos_file_management.latest_scan_summary",
        lambda channel: None,
    )
    monkeypatch.setattr(
        "web.routes.tos_file_management.infra_credentials.tos_channel_options",
        lambda: [("tos_main", "3482299@qq.com CJH"), ("tos_wj", "495828376@qq.com WJ")],
    )

    resp = superadmin_client_no_db.post("/admin/tos-files/sync", data={"channel": "tos_wj", "dry_run": "1"})
    assert resp.status_code in {302, 200}
    assert calls[0][0] == "tos_wj"
    assert calls[0][1] is True


def test_tos_file_inventory_scan_task_definition_exists():
    from appcore import scheduled_tasks
    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}
    task = definitions.get("tos_file_inventory_scan")
    assert task is not None
    assert task["name"] == "TOS文件管理资产扫描"
    assert "TOS" in task["description"]
