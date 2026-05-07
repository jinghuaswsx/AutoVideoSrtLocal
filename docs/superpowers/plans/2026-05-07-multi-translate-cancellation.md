# Multi-Translate Per-Task Cancellation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 bulk_translate 父任务 cancel 时能级联停掉正在运行的 multi_translate 子任务（轻量方案 A1：在 step 交界处响应，慢调用本身不中断）。

**Architecture:** 复用现有 `PipelineRunner._run()` 已有的 `throw_if_cancel_requested` step 钩子，在它旁边并行加 **per-task cancel 检查**（读 task_state 字段 `_cancel_requested`）。bulk_translate `cancel_task` 时遍历父任务 plan 中处于 active 状态的子任务，给每个子任务的 task_state 设 `_cancel_requested=True`。子任务 runner 在下一个 step 边界检测到标志，抛 `OperationCancelled`，被现有 except 块捕获，新方法 `_mark_pipeline_cancelled` 把任务状态收尾为 `cancelled`（区别于 systemd SIGTERM 的 `interrupted`）。父任务 sync 子任务时识别 `cancelled` 状态（`_FAILURE_CHILD_STATUSES` 已包含），plan item 标 `cancelled`。

**Tech Stack:** Python 3.14、pytest、appcore.task_state（task 内存/DB 状态层）、appcore.cancellation（已有 OperationCancelled 异常）、bulk_translate scheduler greenthread 模型。

---

## File Structure

| File | 责任 | 改/新 |
|------|------|-------|
| `appcore/runtime/_pipeline_runner.py` | base PipelineRunner._run() 主 loop 加 per-task cancel check + 新增 `_mark_pipeline_cancelled` | 改 |
| `appcore/bulk_translate_runtime.py` | `cancel_task` 级联 `_cancel_requested` 到 active 子任务 | 改 |
| `tests/test_pipeline_runner_cancellation.py` | 测 base runner per-task cancel 行为 | 新 |
| `tests/test_bulk_translate_runtime.py` | 加 1 条测 cancel_task 级联子任务 | 改 |

**为何不动 `appcore/runtime_multi.py`**：本次改动落在基类 `PipelineRunner._run()` 主 loop，multi/de/fr/ja/v2 所有 runner 自动获得 per-task cancel 能力，无需各自改子类。

---

## Task 1: base runner 主 loop 加 per-task cancel 检查 + cancelled 收尾

**Files:**
- Modify: `appcore/runtime/_pipeline_runner.py:1481`（step 钩子）, `:1492`（except OperationCancelled）, `:1544`（_mark_pipeline_interrupted 旁边新增 _mark_pipeline_cancelled）
- Test: `tests/test_pipeline_runner_cancellation.py`（新文件）

- [ ] **Step 1.1：写第 1 个失败测试——主 loop 检测 `_cancel_requested=True` 抛 OperationCancelled 并把状态收为 cancelled**

`tests/test_pipeline_runner_cancellation.py`（新文件）：
```python
"""PipelineRunner per-task cancellation 行为测试。

bulk_translate 父任务 cancel 时给子任务 task_state 设 `_cancel_requested=True`，
子 runner 主 loop 在下一个 step 边界检测到标志，抛 OperationCancelled，状态
收为 'cancelled'（与 systemd SIGTERM 的 'interrupted' 区分）。
"""
from __future__ import annotations

import pytest

from appcore import task_state
from appcore.cancellation import OperationCancelled
from appcore.events import EventBus
from appcore.runtime._pipeline_runner import PipelineRunner


class _StubRunner(PipelineRunner):
    """最小化 PipelineRunner 子类，把 step 序列暴露成可注入。"""

    project_type = "test_translation"

    def __init__(self, bus: EventBus, steps_provider):
        super().__init__(bus)
        self._steps_provider = steps_provider

    def _get_pipeline_steps(self, task_id, video_path, task_dir):
        return self._steps_provider(task_id)


@pytest.fixture
def runner_env(monkeypatch, tmp_path):
    """构建一个内存态 task_state + 跳过 source video 校验的 runner 环境。

    传 user_id=None 让 task_state.create / update 都不写 DB（pure in-memory）。
    详见 appcore/task_state.py:_sync_task_to_db 和 _db_upsert 的 user_id is None 短路。
    """
    monkeypatch.setattr(
        "appcore.source_video.ensure_local_source_video",
        lambda task_id: None,
    )
    bus = EventBus()
    task_id = "task-cancel-1"
    task_state.create(task_id, str(tmp_path / "video.mp4"), str(tmp_path), original_filename="x.mp4", user_id=None)
    return bus, task_id


def test_run_loop_raises_on_per_task_cancel_flag(runner_env, monkeypatch):
    """user 设 _cancel_requested 后，runner 在下一个 step 边界停下，状态 cancelled。"""
    bus, task_id = runner_env
    executed: list[str] = []

    def step_a():
        executed.append("a")
        # step a 跑完后用户 cancel；下一次 step 边界应当抛 OperationCancelled
        task_state.update(task_id, _cancel_requested=True)

    def step_b():
        executed.append("b")  # 不应该执行

    runner = _StubRunner(bus, lambda tid: [("step_a", step_a), ("step_b", step_b)])
    runner._run(task_id, start_step="step_a")

    assert executed == ["a"]
    state = task_state.get(task_id)
    assert state["status"] == "cancelled"
    assert state["error"] == "task cancelled by user"
    # 关键：所有未跑完 step 都标 cancelled，而不是 interrupted
    assert state["steps"]["step_b"] == "cancelled"


def test_run_loop_uses_interrupted_on_global_shutdown(runner_env, monkeypatch):
    """SIGTERM 触发的 OperationCancelled 走 'interrupted' 收尾，与用户 cancel 区分。"""
    from appcore import shutdown_coordinator
    bus, task_id = runner_env

    def step_a():
        # 模拟 systemd 在 step a 中途请求 shutdown
        shutdown_coordinator.request_shutdown("test sigterm")

    def step_b():
        pytest.fail("step_b should not run after shutdown")

    runner = _StubRunner(bus, lambda tid: [("step_a", step_a), ("step_b", step_b)])
    try:
        with pytest.raises(OperationCancelled):
            runner._run(task_id, start_step="step_a")
        state = task_state.get(task_id)
        # SIGTERM 走 _mark_pipeline_interrupted（保留原行为，UI 显示"等服务恢复后重试"）
        assert state["status"] == "interrupted"
    finally:
        # 清理全局 shutdown 标志，避免污染其他测试
        # （appcore/shutdown_coordinator.py 暴露的 API 是 reset()，不是 reset_for_tests）
        shutdown_coordinator.reset()
```

注意：依赖 `shutdown_coordinator.reset_for_tests()` 存在；如果不存在，请先 grep 确认。如果该方法不存在，把 finally 改为直接 `shutdown_coordinator._reset()` 或写一个内联清理。

- [ ] **Step 1.2：跑测试，验证它们失败**

```bash
python -m pytest tests/test_pipeline_runner_cancellation.py -v
```

期望：两条测试都 FAIL，原因可能是：
- `test_run_loop_raises_on_per_task_cancel_flag`：状态是 `interrupted` 而不是 `cancelled`，且 `step_b` 仍然执行（因为现在主 loop 不查 task `_cancel_requested`）
- `test_run_loop_uses_interrupted_on_global_shutdown`：取决于 fixture 行为，可能已经能 pass 也可能因为 reset_for_tests 缺失而 fail

实际 API 已在 [appcore/shutdown_coordinator.py](appcore/shutdown_coordinator.py) grep 确认：`reset()` 和 `request_shutdown(reason)` 都存在。如发现行为不符，**stop & report**——不要硬猜。

- [ ] **Step 1.3：实现 per-task cancel 检测**

修改 `appcore/runtime/_pipeline_runner.py:1480-1482`（在 `throw_if_cancel_requested` 旁边加 per-task 检查）：

把：
```python
                # Cooperative cancellation: graceful-shutdown checkpoint
                # before each step so the worker can drop everything when
                # systemd / Gunicorn hands us SIGTERM.
                throw_if_cancel_requested(f"pipeline step={step_name}")
                step_fn()
```

改成：
```python
                # Cooperative cancellation: graceful-shutdown checkpoint
                # before each step so the worker can drop everything when
                # systemd / Gunicorn hands us SIGTERM.
                throw_if_cancel_requested(f"pipeline step={step_name}")
                # Per-task cancellation: bulk_translate 父任务 cancel 时会在
                # 子 task_state 上设 _cancel_requested=True；下一个 step 边界
                # 抛 OperationCancelled 由下方 except 收尾为 'cancelled'。
                if (task_state.get(task_id) or {}).get("_cancel_requested"):
                    raise OperationCancelled("task cancelled by user")
                step_fn()
```

- [ ] **Step 1.4：实现 cancelled 收尾分支**

修改 `appcore/runtime/_pipeline_runner.py:1492-1501`（except OperationCancelled 块）：

把：
```python
        except OperationCancelled as exc:
            current_step = (task_state.get(task_id) or {}).get("current_step") or "?"
            log.warning(
                "[task %s] pipeline cancelled at step=%s reason=%s",
                task_id, current_step, exc,
            )
            self._mark_pipeline_interrupted(task_id, str(exc))
            # Re-raise so start_tracked_thread's outer handler logs and
            # cleans up _active_tasks; it will not show a traceback.
            raise
```

改成：
```python
        except OperationCancelled as exc:
            current_step = (task_state.get(task_id) or {}).get("current_step") or "?"
            log.warning(
                "[task %s] pipeline cancelled at step=%s reason=%s",
                task_id, current_step, exc,
            )
            # 区分 user cancel（task._cancel_requested）vs systemd SIGTERM。
            # 前者走 cancelled 收尾让 UI 显示"已取消"，后者保留原 interrupted
            # 语义提示用户"服务重启中，请重试"。
            user_cancelled = bool((task_state.get(task_id) or {}).get("_cancel_requested"))
            if user_cancelled:
                self._mark_pipeline_cancelled(task_id, str(exc))
            else:
                self._mark_pipeline_interrupted(task_id, str(exc))
            # Re-raise so start_tracked_thread's outer handler logs and
            # cleans up _active_tasks; it will not show a traceback.
            raise
```

- [ ] **Step 1.5：实现 `_mark_pipeline_cancelled` 方法**

在 `appcore/runtime/_pipeline_runner.py:1577`（`_mark_pipeline_interrupted` 函数末尾后）紧跟着加：

```python
    def _mark_pipeline_cancelled(self, task_id: str, reason: str) -> None:
        """User-initiated cancel 收尾。

        和 _mark_pipeline_interrupted 的区别：status='cancelled'、step 标记
        'cancelled'、错误信息和 UI 文案不再说"服务重启请重试"。语义是用户
        主动取消（典型场景：bulk_translate 父任务 cancel 级联到子任务）。
        """
        task = task_state.get(task_id) or {}
        steps = dict(task.get("steps") or {})
        step_messages = dict(task.get("step_messages") or {})
        changed = False
        for step, status in list(steps.items()):
            if status in {"queued", "running", "pending"}:
                steps[step] = "cancelled"
                step_messages[step] = "task cancelled by user"
                changed = True
        update_kwargs: dict = {
            "status": "cancelled",
            "error": "task cancelled by user",
        }
        if changed:
            update_kwargs["steps"] = steps
            update_kwargs["step_messages"] = step_messages
        task_state.update(task_id, **update_kwargs)
        try:
            self._emit(task_id, EVT_PIPELINE_ERROR, {
                "error": f"cancelled: {reason}",
                "cancelled": True,
                "user_cancelled": True,
            })
        except Exception:
            log.warning("emit pipeline_error during user-cancel failed", exc_info=True)
```

- [ ] **Step 1.6：跑测试验证通过**

```bash
python -m pytest tests/test_pipeline_runner_cancellation.py -v
```

期望：两条测试都 PASS。

如果 `test_run_loop_uses_interrupted_on_global_shutdown` 因为 shutdown_coordinator API 不一致而失败，去查实际 API 再调整测试 + 实现。

- [ ] **Step 1.7：跑相关 runner 的回归测试**

```bash
python -m pytest tests/ -k "runtime or pipeline_runner or runner" -q
```

期望：无回归（新加的 per-task cancel check 默认 false 时和原行为一致）。

- [ ] **Step 1.8：commit**

```bash
git add appcore/runtime/_pipeline_runner.py tests/test_pipeline_runner_cancellation.py
git commit -m "$(cat <<'EOF'
feat(pipeline-runner): support per-task cancellation propagation

base PipelineRunner._run() 主 loop 在 step 边界并行检查两种 cancel 信号：
全局 shutdown（throw_if_cancel_requested，原行为）和 per-task cancel
（task_state._cancel_requested，新加）。

后者由 bulk_translate 父任务 cancel 时级联设置；子 runner 在下一个 step
边界抛 OperationCancelled，新方法 _mark_pipeline_cancelled 把任务状态
收为 'cancelled'，与 systemd SIGTERM 的 'interrupted' 语义区分。

multi / de / fr / ja / v2 所有继承自 PipelineRunner 的 runner 自动获得
此能力，无需各自改子类。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: bulk_translate.cancel_task 级联 `_cancel_requested` 到 active 子任务

**Files:**
- Modify: `appcore/bulk_translate_runtime.py:297-304`
- Test: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 2.1：写失败测试——cancel_task 给 active 子任务设 `_cancel_requested=True`**

在 `tests/test_bulk_translate_runtime.py` 紧跟现有 `test_create_child_task_drops_orphan_row_when_creator_raises` 之后，加：

```python
def test_cancel_task_cascades_cancel_flag_to_active_children(runtime_env, monkeypatch):
    """父任务 cancel 时，所有 active 子任务的 task_state 必须被设
    `_cancel_requested=True`，让子 runner 在下一个 step 边界自行退出。
    """
    mod, fake_db = runtime_env

    # 构造一个 planning 父任务，加一个 dispatching/running/awaiting_voice/syncing_result
    # 各一条的 plan，再加一条 done 的（不该被级联）
    monkeypatch.setattr(
        mod,
        "generate_plan",
        lambda *args, **kwargs: [
            _item(0, kind="videos", lang="de", status="dispatching"),
            _item(1, kind="videos", lang="fr", status="running"),
            _item(2, kind="videos", lang="es", status="awaiting_voice"),
            _item(3, kind="videos", lang="it", status="syncing_result"),
            _item(4, kind="videos", lang="pt", status="done"),  # 不级联
        ],
    )
    task_id = mod.create_bulk_translate_task(
        user_id=1, product_id=77,
        target_langs=["de", "fr", "es", "it", "pt"],
        content_types=["videos"],
        force_retranslate=False, video_params={},
        initiator={"user_id": 1},
    )
    # 把 plan item 的 child_task_id 填好（mimic scheduler 派发后状态）
    state = json.loads(fake_db.rows[task_id]["state_json"])
    for idx, item in enumerate(state["plan"]):
        item["child_task_id"] = f"child-{idx}"
    fake_db.rows[task_id]["state_json"] = json.dumps(state)

    cancel_calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        mod.store, "update",
        lambda task_id, **fields: cancel_calls.append((task_id, fields)),
    )

    mod.cancel_task(task_id, user_id=1)

    cancelled_ids = {tid for tid, fields in cancel_calls if fields.get("_cancel_requested") is True}
    # idx 0/1/2/3 是 active，必须级联；idx 4 是 done，不能级联
    assert cancelled_ids == {"child-0", "child-1", "child-2", "child-3"}
```

- [ ] **Step 2.2：跑测试验证失败**

```bash
python -m pytest tests/test_bulk_translate_runtime.py::test_cancel_task_cascades_cancel_flag_to_active_children -v
```

期望：FAIL，因为 cancel_task 当前只设父任务 `cancel_requested=True`，不级联子任务。

- [ ] **Step 2.3：实现级联**

修改 `appcore/bulk_translate_runtime.py:297-304` 的 `cancel_task`：

把：
```python
def cancel_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    state["cancel_requested"] = True
    _append_audit(state, user_id, "cancel")
    _save_state(task_id, state)
```

改成：
```python
def cancel_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    state["cancel_requested"] = True
    # 级联 cancel 信号到所有 active 子任务（dispatching / running / syncing_result /
    # awaiting_voice）。子 runner 在下一个 step 边界检测到 _cancel_requested=True
    # 自行退出（轻量方案 A1：慢调用本身不中断，最多再花当前一步的钱）。
    for item in state.get("plan") or []:
        if _normalized_status(item.get("status")) not in _ACTIVE_ITEM_STATUSES:
            continue
        child_task_id = item.get("child_task_id")
        if not child_task_id:
            continue
        try:
            store.update(child_task_id, _cancel_requested=True)
        except Exception:
            log.warning(
                "cascade cancel to child task_id=%s failed",
                child_task_id, exc_info=True,
            )
    _append_audit(state, user_id, "cancel")
    _save_state(task_id, state)
```

- [ ] **Step 2.4：跑测试验证通过**

```bash
python -m pytest tests/test_bulk_translate_runtime.py::test_cancel_task_cascades_cancel_flag_to_active_children -v
```

期望：PASS。

- [ ] **Step 2.5：跑全套 bulk_translate runtime 测试无回归**

```bash
python -m pytest tests/test_bulk_translate_runtime.py -q
```

期望：47 passed（46 现有 + 1 新加）。

- [ ] **Step 2.6：commit**

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_runtime.py
git commit -m "$(cat <<'EOF'
feat(bulk-translate): cascade cancel flag to active child tasks

cancel_task 不再只设父任务 cancel_requested 就 return：遍历父任务 plan 中
状态为 dispatching / running / syncing_result / awaiting_voice 的子任务，
给每个子任务 task_state 设 _cancel_requested=True。

子 multi_translate runner 主 loop 在下一个 step 边界检测到该标志，抛
OperationCancelled 并把状态收为 'cancelled'。轻量方案 A1：当前正在执行
的慢调用（30s TTS / 几分钟 ASR）会跑完，但下一个 step 不再启动。

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: 集成回归 + 部署

- [ ] **Step 3.1：跑全套 pytest，确认无跨模块回归**

```bash
python -m pytest tests/ -q --timeout=60 2>&1 | tail -30
```

期望：全部 pass（或仅有与 cancel 改动无关的 pre-existing 失败——若发现，stop & report）。

- [ ] **Step 3.2：worktree commit + merge 主仓 + push**

```bash
# 在 worktree 里
git log --oneline master..HEAD   # 复查 commits
# 切回主仓库 master
cd /g/Code/AutoVideoSrtLocal
git fetch origin master
git pull --ff-only origin master
git merge --no-ff worktree-feature+multi-translate-cancellation -m "Merge feature/multi-translate-cancellation: per-task cancel propagation"
git push origin master
```

- [ ] **Step 3.3：部署到线上**

```bash
ssh -i ~/.ssh/CC.pem -o StrictHostKeyChecking=no root@172.30.254.14 '
set -e
cd /opt/autovideosrt
git pull origin master --ff-only
if ! cmp -s /opt/autovideosrt/deploy/autovideosrt.service /etc/systemd/system/autovideosrt.service; then
  cp /opt/autovideosrt/deploy/autovideosrt.service /etc/systemd/system/autovideosrt.service
  systemctl daemon-reload
  echo "systemd unit synced + daemon-reload"
fi
systemctl restart autovideosrt
sleep 3
systemctl is-active autovideosrt
curl -s -o /dev/null -w "PROD HTTP %{http_code}\n" http://127.0.0.1/
'
```

期望：`active` + `PROD HTTP 302`。

- [ ] **Step 3.4：cleanup worktree**

```bash
cd /g/Code/AutoVideoSrtLocal
git worktree remove .claude/worktrees/feature+multi-translate-cancellation
git branch -d worktree-feature+multi-translate-cancellation
```

---

## 关键风险 & 取舍

1. **慢步骤本身不响应 cancel**（A1 取舍）：当前正在跑的 TTS 调用 / ASR 调用 / FFmpeg 合成会跑完。最多多花一步钱（30s ~ 几分钟）。如果未来需要"立刻停"，再走 A2 深度方案在 LLM/TTS/FFmpeg 调用层注入 OperationCancelled。

2. **基类改动影响所有 runner**：multi/de/fr/ja/v2 都获得 per-task cancel 能力。当前 `cancel_task` 只在 `bulk_translate_runtime` 里有，所以实际触发面只是 multi 子任务。其他 runner 多了能力但没人用，无副作用。

3. **半成品文件不清理**：cancel 后 task_dir 里可能有半成品 vocals.wav / utterances.json / round_*.json。下次 retry 时新 runner 自然覆盖（recovery 机制已有）。

4. **cancelled 与 interrupted 的 UI 区分**：本 plan 没改前端。前端如果想区分这两个状态，需要单独 PR。当前两个状态在 UI 上很可能都显示"已停止"，但 cancelled 的 message 是"task cancelled by user"，interrupted 的 message 是"service restart in progress, please retry"——通过 message 区分。
