# Interrupted Task Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让服务重启后失联的后台任务自动回落为失败状态，恢复用户的重新生成、重新评估和继续执行能力。

**Architecture:** 新增一个公共 `appcore.task_recovery` 服务，统一管理“活跃任务注册”和“僵尸 running 恢复”。后台任务启动/结束时更新注册表，应用启动和路由读取前触发恢复，将无活任务支撑的 `running` 状态回落为内部 `error`，并保留已有产物。

**Tech Stack:** Python, Flask, Flask-SocketIO, eventlet, threading, pytest

---

### Task 1: 写失败测试锁定恢复边界

**Files:**
- Create: `tests/test_task_recovery.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: 写公共恢复服务的失败测试**

```python
from appcore import task_recovery


def test_recover_video_creation_marks_orphan_running_as_error():
    state = {
        "steps": {"generate": "running"},
        "prompt": "demo",
        "result_video_path": "/tmp/out.mp4",
    }

    changed, recovered = task_recovery.recover_project_state(
        project_type="video_creation",
        task_id="vc-1",
        state=state,
        active=False,
    )

    assert changed is True
    assert recovered["steps"]["generate"] == "error"
    assert recovered["result_video_path"] == "/tmp/out.mp4"
    assert "服务重启" in recovered["error"]
```

- [ ] **Step 2: 运行单测并确认先失败**

Run: `pytest tests/test_task_recovery.py -q`

Expected: `FAIL`，提示 `appcore.task_recovery` 或 `recover_project_state` 尚不存在。

- [ ] **Step 3: 写路由层失败测试，锁定按钮恢复场景**

```python
def test_video_creation_detail_resets_orphan_running_before_render(client, monkeypatch):
    state = {"steps": {"generate": "running"}, "prompt": "x"}
    monkeypatch.setattr("web.routes.video_creation.db_query_one", lambda *a, **k: {
        "id": "vc-1",
        "user_id": 1,
        "type": "video_creation",
        "state_json": json.dumps(state, ensure_ascii=False),
    })
    monkeypatch.setattr("web.routes.video_creation.recover_project_if_needed", fake_recover)

    resp = client.get("/video-creation/vc-1")

    assert resp.status_code == 200
    assert fake_recover.called == [("vc-1", "video_creation")]
```

- [ ] **Step 4: 运行路由回归并确认先失败**

Run: `pytest tests/test_web_routes.py -k "orphan_running or interrupted_task" -q`

Expected: `FAIL`，提示恢复钩子尚未接入页面或接口。

- [ ] **Step 5: 提交测试骨架**

```bash
git add tests/test_task_recovery.py tests/test_web_routes.py
git commit -m "test: cover interrupted task recovery"
```

### Task 2: 实现公共恢复服务

**Files:**
- Create: `appcore/task_recovery.py`
- Modify: `tests/test_task_recovery.py`

- [ ] **Step 1: 写最小实现骨架**

```python
from __future__ import annotations

import copy
import threading

RECOVERY_ERROR_MESSAGE = "任务因服务重启或后台执行中断，已自动标记为失败，请重新发起。"

_active_tasks: set[tuple[str, str]] = set()
_lock = threading.Lock()
```

- [ ] **Step 2: 实现活跃任务注册接口**

```python
def register_active_task(project_type: str, task_id: str) -> None:
    with _lock:
        _active_tasks.add((project_type, task_id))


def unregister_active_task(project_type: str, task_id: str) -> None:
    with _lock:
        _active_tasks.discard((project_type, task_id))


def is_task_active(project_type: str, task_id: str) -> bool:
    with _lock:
        return (project_type, task_id) in _active_tasks
```

- [ ] **Step 3: 实现按项目类型回落 `running -> error` 的核心逻辑**

```python
def recover_project_state(project_type: str, task_id: str, state: dict, active: bool | None = None) -> tuple[bool, dict]:
    active = is_task_active(project_type, task_id) if active is None else active
    if active:
        return False, state

    recovered = copy.deepcopy(state or {})
    steps = recovered.setdefault("steps", {})
    changed = False

    if project_type == "video_creation" and steps.get("generate") == "running":
        steps["generate"] = "error"
        recovered["error"] = RECOVERY_ERROR_MESSAGE
        changed = True

    return changed, recovered
```

- [ ] **Step 4: 扩展到 `video_review`、`copywriting`、工作台流水线**

```python
def _mark_running_steps_as_error(state: dict) -> bool:
    changed = False
    for step, status in (state.get("steps") or {}).items():
        if status == "running":
            state["steps"][step] = "error"
            state.setdefault("step_messages", {})[step] = RECOVERY_ERROR_MESSAGE
            changed = True
    return changed
```

- [ ] **Step 5: 运行公共恢复测试并确认转绿**

Run: `pytest tests/test_task_recovery.py -q`

Expected: `PASS`

- [ ] **Step 6: 提交公共恢复服务**

```bash
git add appcore/task_recovery.py tests/test_task_recovery.py
git commit -m "feat: add interrupted task recovery core"
```

### Task 3: 接入后台任务启动/结束与路由懒恢复

**Files:**
- Modify: `web/routes/video_creation.py`
- Modify: `web/routes/video_review.py`
- Modify: `web/routes/copywriting.py`
- Modify: `web/routes/task.py`
- Modify: `web/routes/de_translate.py`
- Modify: `web/routes/fr_translate.py`
- Modify: `web/services/pipeline_runner.py`
- Modify: `web/services/de_pipeline_runner.py`
- Modify: `web/services/fr_pipeline_runner.py`

- [ ] **Step 1: 给 `eventlet.spawn` 模块加注册/清理包装**

```python
from appcore.task_recovery import register_active_task, unregister_active_task


def _run_with_tracking(task_id: str, *args):
    register_active_task("video_creation", task_id)
    try:
        return _do_generate_v2(task_id, *args)
    finally:
        unregister_active_task("video_creation", task_id)
```

- [ ] **Step 2: 给 `threading.Thread` 流水线加注册/清理包装**

```python
def _run_pipeline_with_tracking(task_id: str, runner: PipelineRunner, start_step: str | None = None):
    register_active_task(runner.project_type, task_id)
    try:
        if start_step:
            runner.resume(task_id, start_step)
        else:
            runner.start(task_id)
    finally:
        unregister_active_task(runner.project_type, task_id)
```

- [ ] **Step 3: 在详情页、列表页和阻塞型接口前接入懒恢复**

```python
from appcore.task_recovery import recover_project_if_needed


@bp.route("/video-creation/<task_id>")
def detail_page(task_id: str):
    recover_project_if_needed(task_id, "video_creation")
    row = db_query_one(...)
    ...
```

- [ ] **Step 4: 在工作台读取接口和 `resume` 前接入恢复**

```python
@bp.route("/<task_id>", methods=["GET"])
def get_task(task_id):
    recover_task_state_if_needed(task_id)
    task = store.get(task_id)
    ...
```

- [ ] **Step 5: 运行路由回归并确认通过**

Run: `pytest tests/test_web_routes.py -k "orphan_running or interrupted_task or resume" -q`

Expected: `PASS`

- [ ] **Step 6: 提交路由和后台任务接入**

```bash
git add web/routes/video_creation.py web/routes/video_review.py web/routes/copywriting.py web/routes/task.py web/routes/de_translate.py web/routes/fr_translate.py web/services/pipeline_runner.py web/services/de_pipeline_runner.py web/services/fr_pipeline_runner.py tests/test_web_routes.py
git commit -m "fix: recover interrupted running tasks in routes"
```

### Task 4: 接入启动恢复并完成验证

**Files:**
- Modify: `web/app.py`
- Modify: `tests/test_web_routes.py`
- Modify: `tests/test_task_recovery.py`

- [ ] **Step 1: 在应用初始化后触发一次全量恢复**

```python
from appcore.task_recovery import recover_all_interrupted_tasks


def create_app():
    app = Flask(__name__)
    ...
    with app.app_context():
        recover_all_interrupted_tasks()
    return app
```

- [ ] **Step 2: 补启动恢复测试**

```python
def test_create_app_runs_interrupted_task_recovery(monkeypatch):
    called = []
    monkeypatch.setattr("web.app.recover_all_interrupted_tasks", lambda: called.append(True))

    app = create_app()

    assert app
    assert called == [True]
```

- [ ] **Step 3: 跑最终回归集**

Run: `pytest tests/test_task_recovery.py tests/test_web_routes.py tests/test_appcore_runtime.py tests/test_pipeline_runner.py tests/test_security_ownership.py -q`

Expected: `PASS`

- [ ] **Step 4: 检查工作树干净并提交**

```bash
git status --short
git add appcore/task_recovery.py web/app.py web/routes/video_creation.py web/routes/video_review.py web/routes/copywriting.py web/routes/task.py web/routes/de_translate.py web/routes/fr_translate.py web/services/pipeline_runner.py web/services/de_pipeline_runner.py web/services/fr_pipeline_runner.py tests/test_task_recovery.py tests/test_web_routes.py
git commit -m "fix: mark interrupted running tasks as failed"
```
