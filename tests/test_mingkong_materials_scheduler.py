from __future__ import annotations

from pathlib import Path


def test_mingkong_material_daily_snapshot_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_material_daily_snapshot")
    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}
    enriched = definitions["mingkong_material_daily_snapshot"]

    assert task["code"] == "mingkong_material_daily_snapshot"
    assert task["source_type"] == "systemd"
    assert task["source_ref"] == "autovideosrt-mingkong-material-daily-snapshot.timer"
    assert task["runner"] == "tools/mingkong_material_daily_snapshot.py"
    assert task["log_table"] == "scheduled_task_runs"
    assert "Top500" in task["description"]
    assert "昨日消耗前300" in task["description"]
    assert "05:00" in task["schedule"]
    assert "17:00" in task["schedule"]
    assert "2026-05-20-mingkong-product-local-aggregate-stats-design.md" in task["description"]
    assert enriched["control_strategy"] == "systemd"
    assert enriched["log_source"] == "db:scheduled_task_runs"
    assert enriched["log_link_available"] is True


def test_mingkong_material_ad_status_refresh_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_material_ad_status_refresh")
    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}
    enriched = definitions["mingkong_material_ad_status_refresh"]

    assert task["code"] == "mingkong_material_ad_status_refresh"
    assert task["source_type"] == "apscheduler"
    assert task["source_ref"] == "mingkong_material_ad_status_refresh"
    assert task["runner"] == "appcore.mingkong_materials.refresh_ad_status_cache"
    assert task["log_table"] == "scheduled_task_runs"
    assert "10 分钟" in task["schedule"]
    assert "2026-05-20-mingkong-card-material-ad-status-design.md" in task["description"]
    assert enriched["control_strategy"] == "apscheduler"
    assert enriched["log_source"] == "db:scheduled_task_runs"


def test_mingkong_material_ad_status_scheduler_registered_in_app_scheduler():
    source = (Path(__file__).resolve().parents[1] / "appcore" / "scheduler.py").read_text(encoding="utf-8")

    assert "mingkong_material_ad_status_scheduler" in source
    assert "mingkong_material_ad_status_scheduler.register(_scheduler)" in source


def test_mingkong_fine_ai_auto_evaluation_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("mingkong_fine_ai_auto_evaluation_tick")
    definitions = {item["code"]: item for item in scheduled_tasks.task_definitions()}
    enriched = definitions["mingkong_fine_ai_auto_evaluation_tick"]

    assert task["code"] == "mingkong_fine_ai_auto_evaluation_tick"
    assert task["source_type"] == "systemd"
    assert task["source_ref"] == "autovideosrt-mingkong-fine-ai-worker.service"
    assert task["runner"] == "tools/mingkong_fine_ai_auto_evaluation_worker.py --workers 6"
    assert task["log_table"] == "mingkong_fine_ai_auto_evaluations"
    assert "连续后台任务池" in task["schedule"]
    assert "6 个卡片并发" in task["schedule"]
    assert "Top1000" in task["description"]
    assert "昨天消耗前300" in task["description"]
    assert "2026-05-23-mingkong-fine-ai-auto-evaluation-design.md" in task["description"]
    assert enriched["control_strategy"] == "systemd"
    assert enriched["log_source"] == "db:mingkong_fine_ai_auto_evaluations"


def test_mingkong_fine_ai_auto_evaluation_scheduler_default_limit_is_two(monkeypatch):
    from appcore import mingkong_fine_ai_auto_evaluation_scheduler as scheduler

    captured = {}

    def fake_tick_once(*, limit):
        captured["limit"] = limit
        return {"limit": limit}

    monkeypatch.setattr(scheduler.mingkong_fine_ai_auto_evaluation, "tick_once", fake_tick_once)

    assert scheduler.tick_once() == {"limit": 2}
    assert captured["limit"] == 2


def test_mingkong_fine_ai_auto_evaluation_scheduler_not_registered_in_app_scheduler():
    source = (Path(__file__).resolve().parents[1] / "appcore" / "scheduler.py").read_text(encoding="utf-8")

    assert "mingkong_fine_ai_auto_evaluation_scheduler.register(_scheduler)" not in source


def test_mingkong_fine_ai_worker_systemd_unit():
    root = Path(__file__).resolve().parents[1]
    service_path = root / "deploy" / "server_browser" / "autovideosrt-mingkong-fine-ai-worker.service"
    service = service_path.read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/autovideosrt" in service
    assert "python tools/mingkong_fine_ai_auto_evaluation_worker.py --workers 6" in service
    assert "Restart=always" in service


def test_mingkong_material_daily_snapshot_systemd_units():
    root = Path(__file__).resolve().parents[1]
    service_path = root / "deploy" / "server_browser" / "autovideosrt-mingkong-material-daily-snapshot.service"
    timer_path = root / "deploy" / "server_browser" / "autovideosrt-mingkong-material-daily-snapshot.timer"
    install_path = root / "deploy" / "server_browser" / "install_mingkong_material_daily_snapshot_timer.sh"

    service = service_path.read_text(encoding="utf-8")
    timer = timer_path.read_text(encoding="utf-8")
    installer = install_path.read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/autovideosrt" in service
    assert "python tools/mingkong_material_daily_snapshot.py" in service
    assert "TimeoutStartSec=21600" in service
    assert "OnCalendar=*-*-* 05:00:00" in timer
    assert "OnCalendar=*-*-* 17:00:00" in timer
    assert "Persistent=true" in timer
    assert "systemctl enable --now" in installer
