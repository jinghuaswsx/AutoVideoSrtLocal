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
