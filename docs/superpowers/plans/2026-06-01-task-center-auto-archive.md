# Task Center Auto Archive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自动归档任务中心中已完成且对应素材已推送成功的任务，并每天 06:00 执行。

**Architecture:** 归档逻辑放在 `appcore.tasks`，使用既有 `archived_at` / `archived_by` 和 `task_events`。调度封装在独立 scheduler 模块，注册到 APScheduler 和 Web 定时任务登记。

**Tech Stack:** Python 3.12, Flask service layer, APScheduler, pytest.

---

### Task 1: Service Logic

**Files:**
- Modify: `appcore/tasks.py`
- Test: `tests/test_appcore_tasks_supporting_data.py`

- [ ] Add `TASK_AUTO_ARCHIVED_EVENT = "auto_archived"`.
- [ ] Add child candidate query for `done` child tasks with pushed media.
- [ ] Add parent candidate query for `all_done` parent tasks whose done children are all pushed.
- [ ] Add transactional auto archive helper that preserves `status` and writes `task_events.payload_json`.
- [ ] Test child archived, child skipped when unpushed, parent archived, and `raw_done` parent skipped.

### Task 2: Scheduler

**Files:**
- Create: `appcore/task_center_auto_archive_scheduler.py`
- Modify: `appcore/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_task_center_auto_archive_scheduler.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] Add scheduler `tick_once` with `scheduled_tasks.start_run` and `finish_run`.
- [ ] Register cron job at `hour=6, minute=0`.
- [ ] Add scheduled task definition with code `task_center_auto_archive`.
- [ ] Register scheduler from `appcore/scheduler.py`.
- [ ] Test scheduler registration and scheduled task definition.

### Task 3: Verification

**Files:**
- Verify: `appcore/tasks.py`
- Verify: `appcore/task_center_auto_archive_scheduler.py`
- Verify: `appcore/scheduler.py`
- Verify: `appcore/scheduled_tasks.py`

- [ ] Run focused pytest for task service, scheduler, and scheduled task registry.
- [ ] Run compileall on touched Python modules.
- [ ] Record unrelated baseline failure if still present.
