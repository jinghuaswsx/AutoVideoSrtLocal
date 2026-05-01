from __future__ import annotations


def test_pre_restart_without_active_tasks_exits_zero(monkeypatch, capsys):
    from appcore.ops import active_tasks as cli

    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", lambda max_age_seconds: [])
    monkeypatch.setattr(cli.active_tasks, "snapshot_active_tasks", lambda reason, tasks=None: {"count": 0})

    assert cli.main(["pre-restart"]) == 0
    assert "no active tasks" in capsys.readouterr().out


def test_pre_restart_with_safe_interrupt_task_warns_and_exits_zero(monkeypatch, capsys):
    from appcore.active_tasks import ActiveTask
    from appcore.ops import active_tasks as cli

    task = ActiveTask(project_type="link_check", task_id="lc-1")
    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [task])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", lambda max_age_seconds: [])
    monkeypatch.setattr(cli.active_tasks, "snapshot_active_tasks", lambda reason, tasks=None: {"count": len(tasks or [])})

    assert cli.main(["pre-restart"]) == 0
    out = capsys.readouterr().out
    assert "warning" in out
    assert "link_check:lc-1" in out


def test_pre_restart_with_blocking_task_exits_two(monkeypatch, capsys):
    from appcore.active_tasks import ActiveTask
    from appcore.ops import active_tasks as cli

    task = ActiveTask(project_type="multi_translate", task_id="mt-1")
    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [task])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", lambda max_age_seconds: [])
    monkeypatch.setattr(cli.active_tasks, "snapshot_active_tasks", lambda reason, tasks=None: {"count": len(tasks or [])})

    assert cli.main(["pre-restart"]) == 2
    out = capsys.readouterr().out
    assert "blocked" in out
    assert "multi_translate:mt-1" in out


def test_pre_restart_snapshot_failure_still_reports_blocking_tasks(monkeypatch, capsys):
    from appcore.active_tasks import ActiveTask
    from appcore.ops import active_tasks as cli

    task = ActiveTask(project_type="multi_translate", task_id="mt-snapshot-fail")
    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [task])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", lambda max_age_seconds: [])

    def fail_snapshot(reason, tasks=None):
        raise RuntimeError("snapshot offline")

    monkeypatch.setattr(cli.active_tasks, "snapshot_active_tasks", fail_snapshot)

    assert cli.main(["pre-restart"]) == 2
    out = capsys.readouterr().out
    assert "warning: failed to snapshot active tasks before restart" in out
    assert "snapshot offline" in out
    assert "blocked" in out
    assert "multi_translate:mt-snapshot-fail" in out


def test_pre_restart_force_allows_blocking_task(monkeypatch, capsys):
    from appcore.active_tasks import ActiveTask
    from appcore.ops import active_tasks as cli

    task = ActiveTask(project_type="video_creation", task_id="vc-1")
    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [task])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", lambda max_age_seconds: [])
    monkeypatch.setattr(cli.active_tasks, "snapshot_active_tasks", lambda reason, tasks=None: {"count": len(tasks or [])})

    assert cli.main(["pre-restart", "--force"]) == 0
    out = capsys.readouterr().out
    assert "force" in out
    assert "video_creation:vc-1" in out


def test_pre_restart_missing_runtime_tables_exits_two_with_first_deploy_guidance(monkeypatch, capsys):
    from appcore.ops import active_tasks as cli

    class MissingRuntimeTableError(Exception):
        pass

    def raise_missing_table(_max_age_seconds):
        raise MissingRuntimeTableError("(1146, \"Table 'auto_video_test.runtime_active_tasks' doesn't exist\")")

    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", raise_missing_table)

    assert cli.main(["pre-restart"]) == 2
    out = capsys.readouterr().out
    assert "runtime active task tables are missing" in out
    assert "first deploy" in out
    assert "--force" in out


def test_pre_restart_force_allows_missing_runtime_tables_with_guidance(monkeypatch, capsys):
    from appcore.ops import active_tasks as cli

    class MissingRuntimeTableError(Exception):
        pass

    def raise_missing_table(_max_age_seconds):
        raise MissingRuntimeTableError("(1146, \"Table 'auto_video_test.runtime_active_tasks' doesn't exist\")")

    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", raise_missing_table)

    assert cli.main(["pre-restart", "--force"]) == 0
    out = capsys.readouterr().out
    assert "runtime active task tables are missing" in out
    assert "first deploy" in out
    assert "force" in out


def test_list_missing_runtime_tables_exits_two_with_guidance(monkeypatch, capsys):
    from appcore.ops import active_tasks as cli

    class MissingRuntimeTableError(Exception):
        pass

    def raise_missing_table(_max_age_seconds):
        raise MissingRuntimeTableError("(1146, \"Table 'auto_video_test.runtime_active_tasks' doesn't exist\")")

    monkeypatch.setattr(cli.active_tasks, "list_active_tasks", lambda: [])
    monkeypatch.setattr(cli.active_tasks, "load_persisted_active_tasks", raise_missing_table)

    assert cli.main(["list"]) == 2
    out = capsys.readouterr().out
    assert "runtime active task tables are missing" in out
    assert "first deploy" in out
