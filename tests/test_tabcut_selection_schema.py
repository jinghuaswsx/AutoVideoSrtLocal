from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tabcut_schema_defines_required_tables_and_indexes():
    sql = (
        ROOT / "db" / "migrations" / "2026_05_12_tabcut_selection.sql"
    ).read_text(encoding="utf-8")

    for table in [
        "tabcut_crawl_runs",
        "tabcut_videos",
        "tabcut_video_snapshots",
        "tabcut_goods",
        "tabcut_goods_snapshots",
        "tabcut_video_candidates",
    ]:
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql

    assert "uniq_tabcut_video_snapshot" in sql
    assert "uniq_tabcut_goods_snapshot" in sql
    assert "uniq_tabcut_video_candidate" in sql


def test_tabcut_daily_selection_registered():
    from appcore import scheduled_tasks

    task = scheduled_tasks.get_task_definition("tabcut_daily_selection")

    assert task["runner"] == "tools/tabcut_crawler/main.py"
    assert "08:00" in task["schedule"]
    assert task["log_table"] == "scheduled_task_runs"


def test_tabcut_deploy_units_use_cjh_and_daily_8am_timer():
    service = (ROOT / "deploy" / "tabcut-daily-selection.service").read_text(
        encoding="utf-8"
    )
    timer = (ROOT / "deploy" / "tabcut-daily-selection.timer").read_text(
        encoding="utf-8"
    )

    assert "User=cjh" in service
    assert "WorkingDirectory=/opt/autovideosrt" in service
    assert "python -m tools.tabcut_crawler.main --days 7" in service
    assert "OnCalendar=*-*-* 08:00:00" in timer
