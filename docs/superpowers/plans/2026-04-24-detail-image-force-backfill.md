# 商品详情图翻译强制回填 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在批量翻译任务管理页和父任务详情页里，为商品详情图翻译子项增加 `强制回填`，允许运营把已成功图片部分回填并将该子项标记为完成。

**Architecture:** 以后端父任务动作为主线，复用现有 `apply_translated_detail_images_from_task(..., allow_partial=True)` 完成部分回填，再在父任务状态机里补齐子项状态、父任务汇总状态、费用累计和审计日志。投影层负责把“是否可强制回填”和“强制回填结果摘要”序列化给前端，两个任务管理入口只做渲染和触发。

**Tech Stack:** Python / Flask / MySQL state_json / pytest / vanilla JS

---

### Task 1: 为父任务状态机增加“强制回填商品详情图子项”

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，锁定状态修正、成本补记和审计行为**

```python
def test_force_backfill_detail_image_item_marks_item_done_and_rolls_up_cost(monkeypatch):
    from appcore import bulk_translate_runtime as mod

    task_id = "bt-force-1"
    child_task_id = "img-child-1"
    state = {
        "product_id": 100,
        "cost_tracking": {"actual": {"copy_tokens_used": 0, "image_processed": 0, "video_minutes_processed": 0.0, "actual_cost_cny": 0.0}},
        "audit_events": [],
        "plan": [{
            "idx": 0,
            "kind": "detail_images",
            "lang": "de",
            "status": "failed",
            "error": "image_translate child failed (1 items): unsupported mime",
            "child_task_id": child_task_id,
            "sub_task_id": child_task_id,
            "child_task_type": "image_translate",
            "result_synced": False,
            "ref": {"source_detail_ids": [11, 12, 13]},
        }],
    }

    monkeypatch.setattr(mod, "get_task", lambda _task_id: {"id": task_id, "user_id": 7, "status": "failed", "state": state, "created_at": None, "updated_at": None})
    monkeypatch.setattr(mod, "_load_child_snapshot", lambda task_type, task_id: {
        "_project_status": "done",
        "_user_id": 7,
        "id": child_task_id,
        "type": "image_translate",
        "items": [{"idx": 0, "status": "done"}, {"idx": 1, "status": "failed", "error": "unsupported mime"}],
        "medias_context": {"apply_status": "pending"},
    })

    applied = {}
    monkeypatch.setattr(mod, "_force_backfill_detail_image_child", lambda item, child_state, user_id: {
        "applied_ids": [901, 902],
        "skipped_failed_indices": [1],
        "apply_status": "applied_partial",
    })
    saved = {}
    monkeypatch.setattr(mod, "_save_state", lambda task_id, state, status=None: saved.update({"task_id": task_id, "state": state, "status": status}))

    mod.force_backfill_item(task_id, idx=0, user_id=7)

    item = saved["state"]["plan"][0]
    assert item["status"] == "done"
    assert item["result_synced"] is True
    assert item["forced_backfill"] is True
    assert item["forced_backfill_applied_count"] == 2
    assert item["forced_backfill_skipped_failed_count"] == 1
    assert saved["status"] == "done"
    assert saved["state"]["cost_tracking"]["actual"]["image_processed"] == 3
    assert saved["state"]["audit_events"][-1]["action"] == "force_backfill_item"
```

- [ ] **Step 2: 跑单测，确认当前确实失败**

Run: `pytest tests/test_bulk_translate_runtime.py -q`

Expected: 新增的 `force_backfill_item` 测试失败，提示函数不存在或状态未按预期更新。

- [ ] **Step 3: 实现父任务动作和辅助函数**

在 `appcore/bulk_translate_runtime.py` 增加以下结构：

```python
def force_backfill_item(task_id: str, idx: int, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")

    state = task["state"]
    plan = [_normalize_item(item) for item in state.get("plan") or []]
    state["plan"] = plan
    if idx < 0 or idx >= len(plan):
        raise ValueError(f"Invalid idx={idx}, plan has {len(plan)} items")

    item = plan[idx]
    _validate_force_backfill_target(item)
    child_state = _load_child_snapshot(item.get("child_task_type"), item.get("child_task_id"))
    if not child_state:
        raise ValueError("image translate child task not found")

    result = _force_backfill_detail_image_child(item, child_state, user_id)
    _mark_item_force_backfilled(item, result)
    _roll_up_cost_once_for_item(state, item, child_state)
    _append_audit(state, user_id, "force_backfill_item", {
        "idx": idx,
        "child_task_id": item.get("child_task_id"),
        "applied_count": len(result["applied_ids"]),
        "skipped_failed_count": len(result["skipped_failed_indices"]),
        "apply_status": result["apply_status"],
    })
    _save_state(task_id, state, status=_derive_parent_status(plan, "running"))
```

配套辅助函数至少包括：

```python
def _validate_force_backfill_target(item: dict) -> None: ...
def _force_backfill_detail_image_child(item: dict, child_state: dict, user_id: int) -> dict: ...
def _mark_item_force_backfilled(item: dict, result: dict) -> None: ...
def _roll_up_cost_once_for_item(parent_state: dict, item: dict, child_state: dict) -> None: ...
```

实现约束：
- 只允许 `detail_images + image_translate`
- 只允许失败态子项执行
- 子任务运行中拒绝
- 没有成功图片拒绝
- 成本只补记一次，可用 `item["cost_rolled_up"] = True` 防重复

- [ ] **Step 4: 跑单测验证通过**

Run: `pytest tests/test_bulk_translate_runtime.py -q`

Expected: 新增强制回填相关测试通过，且未破坏既有 runtime 测试。

- [ ] **Step 5: 提交这一组后端状态机改动**

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_runtime.py
git commit -m "feat: support force backfill for detail image tasks"
```

---

### Task 2: 增加 HTTP 接口和投影字段

**Files:**
- Modify: `web/routes/bulk_translate.py`
- Modify: `appcore/bulk_translate_projection.py`
- Test: `tests/test_bulk_translate_routes.py`
- Test: `tests/test_bulk_translate_projection.py`

- [ ] **Step 1: 先补接口和投影测试**

```python
def test_force_backfill_item_endpoint_calls_runtime(monkeypatch, phase5_client):
    called = {}
    monkeypatch.setattr("web.routes.bulk_translate._load_and_check_ownership", lambda task_id: ({}, None))
    monkeypatch.setattr("web.routes.bulk_translate.force_backfill_item", lambda task_id, idx, user_id: called.update({
        "task_id": task_id,
        "idx": idx,
        "user_id": user_id,
    }))

    resp = phase5_client.post("/api/bulk-translate/bt_xxx/force-backfill-item", json={"idx": 1})

    assert resp.status_code == 202
    assert called == {"task_id": "bt_xxx", "idx": 1, "user_id": 1}


def test_projection_marks_failed_detail_image_item_force_backfillable(monkeypatch):
    from appcore import bulk_translate_projection as mod

    monkeypatch.setattr(mod, "_load_image_translate_projection", lambda child_task_id: {
        "is_running": False,
        "done_count": 2,
        "failed_count": 1,
    })

    item = mod._serialize_item({
        "idx": 0,
        "kind": "detail_images",
        "lang": "de",
        "status": "failed",
        "child_task_id": "img-child-1",
        "child_task_type": "image_translate",
        "ref": {"source_detail_ids": [11, 12, 13]},
    }, parent_detail_url="/tasks/bt-1")

    assert item["force_backfillable"] is True
```

- [ ] **Step 2: 跑测试确认当前失败**

Run: `pytest tests/test_bulk_translate_routes.py tests/test_bulk_translate_projection.py -q`

Expected: 新增 endpoint / projection 断言失败。

- [ ] **Step 3: 实现接口和投影序列化**

在 `web/routes/bulk_translate.py` 新增：

```python
@bp.post("/<task_id>/force-backfill-item")
@login_required
def force_backfill_item_endpoint(task_id):
    _, err = _load_and_check_ownership(task_id)
    if err:
        return err
    payload = request.get_json(force=True, silent=True) or {}
    idx = payload.get("idx")
    if not isinstance(idx, int):
        return jsonify({"error": "idx 必填且为 int"}), 400
    try:
        force_backfill_item(task_id, idx=idx, user_id=current_user.id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"ok": True}), 202
```

在 `appcore/bulk_translate_projection.py` 为 item 增加：

```python
"force_backfillable": _is_force_backfillable(item, child_task_type, child_task_id),
"force_backfill_summary": _force_backfill_summary(item),
```

建议新增两个辅助函数：

```python
def _is_force_backfillable(item: dict, child_task_type: str, child_task_id: str | None) -> bool: ...
def _force_backfill_summary(item: dict) -> str: ...
```

- [ ] **Step 4: 跑测试验证接口和投影都通过**

Run: `pytest tests/test_bulk_translate_routes.py tests/test_bulk_translate_projection.py -q`

Expected: endpoint、权限校验和投影字段测试通过。

- [ ] **Step 5: 提交接口和投影改动**

```bash
git add web/routes/bulk_translate.py appcore/bulk_translate_projection.py tests/test_bulk_translate_routes.py tests/test_bulk_translate_projection.py
git commit -m "feat: expose force backfill action for bulk translate items"
```

---

### Task 3: 在两个任务管理入口接入“强制回填”按钮

**Files:**
- Modify: `web/static/medias_translation_tasks.js`
- Modify: `web/static/bulk_translate_detail.js`
- Test: `tests/test_medias_translation_assets.py`
- Test: `tests/test_bulk_translate_detail_assets.py`

- [ ] **Step 1: 先补前端静态资源测试**

```python
def test_medias_translation_tasks_mentions_force_backfill():
    script = (ROOT / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")
    assert "强制回填" in script
    assert "force-backfill-item" in script
    assert "将把该图片任务中已成功的图片立即回填" in script


def test_bulk_translate_detail_mentions_force_backfill():
    script = (ROOT / "web" / "static" / "bulk_translate_detail.js").read_text(encoding="utf-8")
    assert "强制回填" in script
    assert "force-backfill-item" in script
```

- [ ] **Step 2: 跑前端静态测试，确认当前失败**

Run: `pytest tests/test_medias_translation_assets.py tests/test_bulk_translate_detail_assets.py -q`

Expected: 因脚本里还没有 `强制回填` 文案和动作而失败。

- [ ] **Step 3: 实现按钮渲染、确认文案和动作触发**

在两个脚本里统一加入：

```javascript
if (item.force_backfillable) {
  actions.push(
    '<button type="button" class="bt-btn bt-btn--ghost" data-task-action="force-backfill-item" data-task-id="' +
      esc(task.id) + '" data-item-idx="' + esc(item.idx) + '">强制回填</button>'
  );
}
```

动作映射新增：

```javascript
'force-backfill-item': '将把该图片任务中已成功的图片立即回填，并忽略失败图片；当前子项会被标记为已完成。确定继续吗？'
```

请求新增：

```javascript
url = `/api/bulk-translate/${taskId}/force-backfill-item`;
payload = { idx: Number(itemIdx) };
```

摘要展示优先读取后端投影结果：

```javascript
if (item.force_backfill_summary) {
  meta.push(`<span>${esc(item.force_backfill_summary)}</span>`);
}
```

- [ ] **Step 4: 跑前端静态测试验证通过**

Run: `pytest tests/test_medias_translation_assets.py tests/test_bulk_translate_detail_assets.py -q`

Expected: 两个入口的脚本文案和动作断言都通过。

- [ ] **Step 5: 提交前端接入**

```bash
git add web/static/medias_translation_tasks.js web/static/bulk_translate_detail.js tests/test_medias_translation_assets.py tests/test_bulk_translate_detail_assets.py
git commit -m "feat: add force backfill action to translate task UIs"
```

---

### Task 4: 跑回归验证并记录基线差异

**Files:**
- Modify: `docs/superpowers/specs/2026-04-24-detail-image-force-backfill-design.md`（仅在需要补充实现期备注时）

- [ ] **Step 1: 跑本次功能相关回归集**

Run:

```bash
pytest tests/test_bulk_translate_runtime.py tests/test_bulk_translate_routes.py tests/test_bulk_translate_projection.py tests/test_medias_translation_assets.py tests/test_bulk_translate_detail_assets.py tests/test_medias_translation_tasks_routes.py tests/test_medias_routes.py -q
```

Expected: 新增功能相关测试全部通过。

- [ ] **Step 2: 复核既有基线问题是否被触发**

Run:

```bash
pytest tests/test_image_translate_runtime.py -q
```

Expected: 若仍然失败，失败原因应继续集中在既有的 `rt.tos_clients` patch 问题，而不是本次强制回填逻辑。

- [ ] **Step 3: 查看工作区变更并整理提交**

Run:

```bash
git status --short
git log --oneline -5
```

Expected: 只包含本次功能涉及文件；历史上一步步提交清晰可读。

- [ ] **Step 4: 最终提交**

```bash
git add appcore/bulk_translate_runtime.py appcore/bulk_translate_projection.py web/routes/bulk_translate.py web/static/medias_translation_tasks.js web/static/bulk_translate_detail.js tests/test_bulk_translate_runtime.py tests/test_bulk_translate_routes.py tests/test_bulk_translate_projection.py tests/test_medias_translation_assets.py tests/test_bulk_translate_detail_assets.py
git commit -m "feat: support force backfill for detail image translate tasks"
```

