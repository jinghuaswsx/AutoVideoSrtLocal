from __future__ import annotations


def test_deploy_task_capcut_project_updates_default_exports():
    from web.services.task_capcut import deploy_task_capcut_project

    calls = []
    task = {"exports": {"capcut_project": "/task/capcut", "capcut_archive": "/task/capcut.zip"}}

    result = deploy_task_capcut_project(
        "task-1",
        task,
        variant=None,
        resolve_safe_dir=lambda payload, path: calls.append(("safe", payload, path)) or "/safe/capcut",
        deploy_project=lambda path: calls.append(("deploy", path)) or "/jianying/capcut",
        update_task=lambda task_id, **updates: calls.append(("update_task", task_id, updates)),
        update_variant=lambda *args, **kwargs: calls.append(("update_variant", args, kwargs)),
    )

    assert result == {"deployed_project_dir": "/jianying/capcut"}
    assert calls == [
        ("safe", task, "/task/capcut"),
        ("deploy", "/safe/capcut"),
        (
            "update_task",
            "task-1",
            {"exports": {"capcut_project": "/task/capcut", "capcut_archive": "/task/capcut.zip", "jianying_project_dir": "/jianying/capcut"}},
        ),
    ]


def test_deploy_task_capcut_project_updates_variant_exports():
    from web.services.task_capcut import deploy_task_capcut_project

    calls = []
    task = {
        "variants": {
            "hook_cta": {
                "exports": {
                    "capcut_project": "/task/hook_cta",
                    "capcut_archive": "/task/hook_cta.zip",
                }
            }
        }
    }

    result = deploy_task_capcut_project(
        "task-1",
        task,
        variant="hook_cta",
        resolve_safe_dir=lambda payload, path: "/safe/hook_cta",
        deploy_project=lambda path: "/jianying/hook_cta",
        update_task=lambda *args, **kwargs: calls.append(("update_task", args, kwargs)),
        update_variant=lambda task_id, variant, **updates: calls.append(("update_variant", task_id, variant, updates)),
    )

    assert result == {"deployed_project_dir": "/jianying/hook_cta"}
    assert calls == [
        (
            "update_variant",
            "task-1",
            "hook_cta",
            {
                "exports": {
                    "capcut_project": "/task/hook_cta",
                    "capcut_archive": "/task/hook_cta.zip",
                    "jianying_project_dir": "/jianying/hook_cta",
                }
            },
        )
    ]


def test_deploy_task_capcut_project_returns_none_for_missing_safe_project_dir():
    from web.services.task_capcut import deploy_task_capcut_project

    calls = []
    task = {"exports": {"capcut_project": "/outside/capcut"}}

    result = deploy_task_capcut_project(
        "task-1",
        task,
        variant=None,
        resolve_safe_dir=lambda payload, path: None,
        deploy_project=lambda path: calls.append(("deploy", path)),
        update_task=lambda *args, **kwargs: calls.append(("update_task", args, kwargs)),
        update_variant=lambda *args, **kwargs: calls.append(("update_variant", args, kwargs)),
    )

    assert result is None
    assert calls == []
