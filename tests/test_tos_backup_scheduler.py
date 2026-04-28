def test_tos_backup_job_registers_daily_one_am_cron():
    from appcore import tos_backup_job

    calls = []

    class FakeScheduler:
        def add_job(self, *args, **kwargs):
            calls.append((args, kwargs))

    tos_backup_job.register(FakeScheduler())

    assert calls == [
        (
            (tos_backup_job.run_scheduled_backup, "cron"),
            {
                "hour": 1,
                "minute": 0,
                "id": "tos_backup",
                "replace_existing": True,
                "max_instances": 1,
            },
        )
    ]


def test_global_scheduler_registers_tos_backup_job():
    from pathlib import Path

    source = Path("appcore/scheduler.py").read_text(encoding="utf-8")

    assert "tos_backup_job" in source
    assert "tos_backup_job.register(_scheduler)" in source
