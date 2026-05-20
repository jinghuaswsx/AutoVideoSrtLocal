# Task Center Niuma Status Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a summarized Niuma subtitle-removal status, a `字幕移除任务页` link, and a two-column source/result video comparison inside the task-center raw-video step.

**Architecture:** Enrich `appcore.tasks.list_task_events()` with a backward-compatible `payload_context.subtitle_removal` object derived from existing `task_events` payloads and `projects.state_json` for subtitle-removal tasks. Keep the task-center UI as the summary surface and use existing subtitle-removal artifact routes for video playback and detail-page navigation.

**Tech Stack:** Python 3.12, Flask, existing `projects.state_json` task store, Jinja template JavaScript, pytest.

---

## File Structure

- Modify `tests/test_appcore_tasks_supporting_data.py`: service-level regression tests for subtitle-removal context, labels, detail URL, elapsed timestamps, and comparison URLs.
- Modify `tests/test_task_raw_video_processing.py`: verify automatic Niuma submission events include `subtitle_backend: "niuma"`.
- Modify `tests/test_task_center_closure_assets.py`: template regression tests for `字幕移除任务页` and the two-column comparison renderer.
- Modify `appcore/task_raw_video_processing.py`: include `subtitle_backend` in `raw_niuma_submitted` payload.
- Modify `appcore/tasks.py`: add small helpers that extract subtitle-removal task IDs from Niuma events, load subtitle-removal task state from `projects`, summarize status, and attach `payload_context.subtitle_removal`.
- Modify `web/templates/tasks_list.html`: render the context as a compact summary block, required link button, and two-column `<video>` comparison when result media exists.

## Task 1: Backend Event Context

**Files:**
- Modify: `tests/test_appcore_tasks_supporting_data.py`
- Modify: `appcore/tasks.py`

- [ ] **Step 1: Write failing service test for subtitle-removal context**

Add this test near the existing `list_task_events` tests:

```python
def test_list_task_events_enriches_niuma_subtitle_removal_context(monkeypatch):
    import json
    from appcore import tasks

    monkeypatch.setattr(tasks, "_user_display_name_expr", lambda alias: f"{alias}.username", raising=False)
    monkeypatch.setattr(tasks, "_load_user_display_context", lambda user_ids: {})

    def fake_query_all(sql, args=()):
        if "FROM task_events" in sql:
            return [
                {
                    "id": 8,
                    "task_id": 44,
                    "event_type": "raw_niuma_submitted",
                    "actor_user_id": 7,
                    "actor_username": "raw-user",
                    "actor_display_name": "蔡靖华",
                    "payload_json": '{"subtitle_task_id": "tcraw-44-a", "timeout_seconds": 600}',
                    "created_at": datetime(2026, 5, 20, 23, 30, 24),
                }
            ]
        if "FROM projects" in sql:
            assert args == ("tcraw-44-a",)
            return [
                {
                    "id": "tcraw-44-a",
                    "status": "done",
                    "state_json": json.dumps(
                        {
                            "status": "done",
                            "video_path": "/tmp/source.mp4",
                            "result_video_path": "/tmp/result.mp4",
                            "provider_status": "success",
                            "last_polled_at": "2026-05-20T23:33:10",
                        }
                    ),
                    "created_at": datetime(2026, 5, 20, 23, 30, 24),
                    "updated_at": datetime(2026, 5, 20, 23, 33, 10),
                }
            ]
        raise AssertionError(sql)

    monkeypatch.setattr(tasks, "query_all", fake_query_all)

    event = tasks.list_task_events(44)[0]

    subtitle = event["payload_context"]["subtitle_removal"]
    assert subtitle["task_id"] == "tcraw-44-a"
    assert subtitle["detail_url"] == "/subtitle-removal/tcraw-44-a"
    assert subtitle["summary_status"] == "done"
    assert subtitle["summary_label"] == "已完成"
    assert subtitle["submitted_at"] == "2026-05-20T23:30:24"
    assert subtitle["last_updated_at"] == "2026-05-20T23:33:10"
    assert subtitle["comparison"] == {
        "source_video_url": "/api/subtitle-removal/tcraw-44-a/artifact/source-video",
        "result_video_url": "/api/subtitle-removal/tcraw-44-a/artifact/result",
        "source_label": "原始英文视频",
        "result_label": "字幕移除结果",
    }
```

- [ ] **Step 2: Run the test to verify RED**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_list_task_events_enriches_niuma_subtitle_removal_context -q`

Expected: FAIL because `payload_context.subtitle_removal` does not exist.

- [ ] **Step 3: Implement context helpers in `appcore/tasks.py`**

Add focused helpers near the existing event-payload helpers:

```python
RAW_NIUMA_EVENT_TYPES = {
    "raw_niuma_submitted",
    "raw_niuma_done",
    "raw_niuma_failed",
    "raw_niuma_timeout",
}

def _event_subtitle_removal_task_id(event_type: str, payload: dict) -> str:
    if event_type not in RAW_NIUMA_EVENT_TYPES:
        return ""
    raw = payload.get("subtitle_task_id") or payload.get("task_id") or ""
    task_id = str(raw or "").strip()
    return task_id
```

Then add helpers that query `projects` by subtitle task IDs, parse `state_json`, build `/subtitle-removal/<id>`, summarize status, and include comparison URLs only when both a source marker and a result marker exist.

- [ ] **Step 4: Attach context in `list_task_events()`**

Within `list_task_events()`, collect subtitle task IDs while parsing payloads, load the context map once, and merge it with the existing `payload_context.users` dict:

```python
payload_subtitle_task_ids: set[str] = set()
...
subtitle_task_id = _event_subtitle_removal_task_id(str(row["event_type"] or ""), payload)
if subtitle_task_id:
    payload_subtitle_task_ids.add(subtitle_task_id)
...
subtitle_context = _load_subtitle_removal_context(payload_subtitle_task_ids)
...
context = {}
if event_user_context:
    context["users"] = event_user_context
subtitle_task_id = _event_subtitle_removal_task_id(str(row["event_type"] or ""), payload)
if subtitle_task_id and subtitle_task_id in subtitle_context:
    context["subtitle_removal"] = _event_subtitle_removal_context_for_event(
        subtitle_context[subtitle_task_id],
        event_type=str(row["event_type"] or ""),
        submitted_at=item["created_at"],
        payload=payload,
    )
elif subtitle_task_id:
    context["subtitle_removal"] = _fallback_subtitle_removal_context(subtitle_task_id, item["created_at"], payload)
if context:
    item["payload_context"] = context
```

- [ ] **Step 5: Verify GREEN**

Run: `pytest tests/test_appcore_tasks_supporting_data.py::test_list_task_events_enriches_niuma_subtitle_removal_context -q`

Expected: PASS.

## Task 2: Submission Payload Contract

**Files:**
- Modify: `tests/test_task_raw_video_processing.py`
- Modify: `appcore/task_raw_video_processing.py`

- [ ] **Step 1: Write failing test expectation**

In `test_start_niuma_processing_prepares_subtitle_task_and_watcher`, add:

```python
assert events[0][3]["subtitle_backend"] == "niuma"
```

- [ ] **Step 2: Run the test to verify RED**

Run: `pytest tests/test_task_raw_video_processing.py::test_start_niuma_processing_prepares_subtitle_task_and_watcher -q`

Expected: FAIL with `KeyError: 'subtitle_backend'`.

- [ ] **Step 3: Add the payload field**

Change the `raw_niuma_submitted` payload in `start_niuma_processing_for_parent_task()` to:

```python
{
    "subtitle_task_id": subtitle_task_id,
    "timeout_seconds": WATCH_TIMEOUT_SECONDS,
    "subtitle_backend": "niuma",
}
```

- [ ] **Step 4: Verify GREEN**

Run: `pytest tests/test_task_raw_video_processing.py::test_start_niuma_processing_prepares_subtitle_task_and_watcher -q`

Expected: PASS.

## Task 3: Task-Center Timeline Rendering

**Files:**
- Modify: `tests/test_task_center_closure_assets.py`
- Modify: `web/templates/tasks_list.html`

- [ ] **Step 1: Write failing template test**

Append assertions to `test_task_center_timeline_renders_review_assets_in_steps`:

```python
assert "function tcRenderSubtitleRemovalContext" in source
assert "字幕移除任务页" in source
assert "tc-niuma-comparison" in source
assert "<video class=\"tc-niuma-video\"" in source
assert "原始英文视频" in source
assert "字幕移除结果" in source
```

- [ ] **Step 2: Run the test to verify RED**

Run: `pytest tests/test_task_center_closure_assets.py::test_task_center_timeline_renders_review_assets_in_steps -q`

Expected: FAIL because the renderer and CSS classes do not exist.

- [ ] **Step 3: Add CSS**

Add compact styles near existing `.tc-review-*` rules:

```css
.tc-niuma-panel { margin-top:10px; padding:10px; border:1px solid var(--tc-border); border-radius:var(--tc-r-md); background:var(--tc-bg-subtle); }
.tc-niuma-head { display:flex; align-items:center; justify-content:space-between; gap:8px; flex-wrap:wrap; margin-bottom:8px; }
.tc-niuma-title { font-size:12px; font-weight:700; color:var(--tc-fg-muted); }
.tc-niuma-meta { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:6px; margin-top:8px; }
.tc-niuma-comparison { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin-top:10px; }
.tc-niuma-video-card { min-width:0; border:1px solid var(--tc-border); border-radius:var(--tc-r); background:var(--tc-bg); padding:8px; }
.tc-niuma-video-label { font-size:12px; font-weight:700; margin-bottom:6px; color:var(--tc-fg-muted); }
.tc-niuma-video { display:block; width:100%; max-height:360px; background:oklch(18% 0.015 230); border-radius:var(--tc-r); }
@media (max-width: 760px) {
  .tc-niuma-meta,
  .tc-niuma-comparison { grid-template-columns:1fr; }
}
```

- [ ] **Step 4: Add renderer functions**

Add `tcRenderSubtitleRemovalContext(event)` and helper functions near the existing review-asset renderers. The renderer reads `event.payload_context.subtitle_removal`, shows the `字幕移除任务页` link, summary status, submitted/elapsed/updated fields, error text, and the two video cards when `comparison.result_video_url` exists.

- [ ] **Step 5: Embed renderer in timeline cards**

In `tcRenderEventTimeline()`, insert:

```javascript
const subtitleRemovalHtml = tcRenderSubtitleRemovalContext(event);
```

and render it after `facts` and before `reviewAssetsHtml`.

- [ ] **Step 6: Verify GREEN**

Run: `pytest tests/test_task_center_closure_assets.py::test_task_center_timeline_renders_review_assets_in_steps -q`

Expected: PASS.

## Task 4: Focused Verification

**Files:**
- Verify only.

- [ ] **Step 1: Run focused tests**

Run: `pytest tests/test_appcore_tasks_supporting_data.py tests/test_task_raw_video_processing.py tests/test_task_center_closure_assets.py tests/test_tasks_routes.py -q`

Expected: PASS.

- [ ] **Step 2: Compile changed Python modules**

Run: `python3 -m compileall appcore/tasks.py appcore/task_raw_video_processing.py web/routes/tasks.py`

Expected: exit code 0.

- [ ] **Step 3: Review git diff**

Run: `git diff -- appcore/tasks.py appcore/task_raw_video_processing.py web/templates/tasks_list.html tests/test_appcore_tasks_supporting_data.py tests/test_task_raw_video_processing.py tests/test_task_center_closure_assets.py`

Expected: diff only covers the approved spec.
