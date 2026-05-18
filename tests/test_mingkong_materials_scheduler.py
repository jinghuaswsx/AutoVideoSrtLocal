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
    assert "06:00" in task["schedule"]
    assert "2026-05-18-mingkong-daily-material-snapshot-top100-design.md" in task["description"]
    assert enriched["control_strategy"] == "systemd"
    assert enriched["log_source"] == "db:scheduled_task_runs"
    assert enriched["log_link_available"] is True


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
    assert "OnCalendar=*-*-* 06:00:00" in timer
    assert "Persistent=true" in timer
    assert "systemctl enable --now" in installer
