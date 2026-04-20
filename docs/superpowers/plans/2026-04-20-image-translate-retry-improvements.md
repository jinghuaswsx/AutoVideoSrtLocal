# 图片翻译重试按钮改版实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让图片翻译详情页在主进程重启后能手动走出"僵尸 running"死锁，并允许对 `done` 图单张重跑；全局重试按钮放大 2×。

**Architecture:** 后端 `_state_payload` 新增 `is_running` 字段（runtime 内存互斥），`/retry/<idx>` 校验放宽到任意状态（仅禁 `is_running`），新增 `/retry-unfinished` 将所有非 `done` item 重置为 pending；前端依据 `is_running` 决定按钮显隐/禁用，全局按钮放大 + 换主色，单图按钮对所有状态显示。不动启动期 `resume_inflight_tasks`。

**Tech Stack:** Flask、原生 JS、pytest、Python mock/monkeypatch。

**Spec:** `docs/superpowers/specs/2026-04-20-image-translate-retry-improvements-design.md`

---

## 文件结构

- **Modify:** `web/routes/image_translate.py`
  - `_state_payload()` 新增 `is_running` 字段
  - `api_retry_item()`（`/retry/<idx>`）校验放宽
  - 新增 `api_retry_unfinished()`（`/retry-unfinished`）
  - `api_retry_failed()` 保留不动（兼容）
- **Modify:** `web/templates/image_translate_detail.html`
  - 按钮 id `itRetryFailed` → `itRetryUnfinished`，文案改为「重试未完成的图片」，class 加 `it-retry-main`
- **Modify:** `web/templates/_image_translate_scripts.html`
  - `retryFailedBtn` → `retryUnfinishedBtn`
  - 显示条件：`done < total`
  - 启用条件：`!is_running`
  - 单图重试按钮：对所有 item 状态显示，文案随状态变化
  - 按钮 disabled 状态处理
- **Modify:** `web/templates/_image_translate_styles.html`
  - 新增 `.it-retry-main`（2× 大小、海洋蓝主色）
  - 新增 `.it-retry-item`（64×32px、描边）
  - disabled 态样式
- **Modify:** `tests/test_image_translate_routes.py`
  - 更新 `test_retry_rejects_non_failed_item`（语义已变）
  - 新增测试覆盖 `is_running` 字段、`/retry/<idx>` 新校验、`/retry-unfinished`

---

## Task 1: `_state_payload` 新增 `is_running` 字段

**Files:**
- Modify: `web/routes/image_translate.py`（`_state_payload`，约行 115-132）
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_image_translate_routes.py` 末尾追加：

```python
def test_state_payload_includes_is_running_false_when_no_runner(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    # 确保内存中没有这个 task_id
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}")
    assert resp.status_code == 200
    assert resp.get_json()["is_running"] is False


def test_state_payload_includes_is_running_true_when_runner_active(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.get(f"/api/image-translate/{tid}")
    assert resp.status_code == 200
    assert resp.get_json()["is_running"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_image_translate_routes.py::test_state_payload_includes_is_running_false_when_no_runner tests/test_image_translate_routes.py::test_state_payload_includes_is_running_true_when_runner_active -v`
Expected: FAIL（`is_running` 字段不存在）

- [ ] **Step 3: 实现**

改 `web/routes/image_translate.py`：

```python
def _state_payload(task: dict) -> dict:
    return {
        "id": task.get("id"),
        "type": "image_translate",
        "status": task.get("status") or "queued",
        "preset": task.get("preset") or "",
        "target_language": task.get("target_language") or "",
        "target_language_name": task.get("target_language_name") or "",
        "model_id": task.get("model_id") or "",
        "prompt": task.get("prompt") or "",
        "product_name": task.get("product_name") or "",
        "project_name": task.get("project_name") or "",
        "progress": dict(task.get("progress") or {}),
        "items": list(task.get("items") or []),
        "medias_context": dict(task.get("medias_context") or {}),
        "steps": dict(task.get("steps") or {}),
        "error": task.get("error") or "",
        "is_running": image_translate_runner.is_running(task.get("id") or ""),
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `pytest tests/test_image_translate_routes.py -k "is_running" -v`
Expected: 2 passed

- [ ] **Step 5: 跑整个文件确认没回归**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全部 passed

- [ ] **Step 6: commit**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): _state_payload 增加 is_running 字段"
```

---

## Task 2: `/retry/<idx>` 校验放宽 —— `is_running` 时 409

**Files:**
- Modify: `web/routes/image_translate.py`（`api_retry_item`，约行 329-354）
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_image_translate_routes.py` 末尾追加：

```python
def test_retry_item_409_when_runner_active(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"][0]["status"] = "failed"
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 409
    assert "正在跑" in resp.get_json().get("error", "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_image_translate_routes.py::test_retry_item_409_when_runner_active -v`
Expected: FAIL（目前 status=failed + is_running=true 会重置并返回 202）

- [ ] **Step 3: 实现**

在 `web/routes/image_translate.py` 的 `api_retry_item` 函数开头、`_get_item` 校验之后插入 `is_running` 保护：

```python
@bp.route("/api/image-translate/<task_id>/retry/<int:idx>", methods=["POST"])
@login_required
def api_retry_item(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    # ... 下面沿用现有逻辑（Task 3 再改）
```

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_image_translate_routes.py::test_retry_item_409_when_runner_active -v`
Expected: PASS

- [ ] **Step 5: 确认老测试不回归**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿（Task 3 前老测试 `test_retry_failed_item_resets_and_triggers_runner` 需要 `is_running=false`，默认即是，无需改）

- [ ] **Step 6: commit**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): /retry/<idx> 在 runner 活跃时返回 409"
```

---

## Task 3: `/retry/<idx>` 校验放宽 —— 任意状态可重试 + 清理旧 dst

**Files:**
- Modify: `web/routes/image_translate.py`（`api_retry_item`）
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_image_translate_routes.py` 末尾追加：

```python
def test_retry_item_allows_done_status_and_deletes_old_dst(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    from web import store
    task = store.get(tid)
    task["items"][0]["dst_tos_key"] = "artifacts/image_translate/1/tid/out_0.png"
    deleted: list[str] = []
    monkeypatch.setattr(r.tos_clients, "delete_object", lambda k: deleted.append(k))
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["dst_tos_key"] == ""
    assert deleted == ["artifacts/image_translate/1/tid/out_0.png"]


def test_retry_item_allows_zombie_running(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"][0]["status"] = "running"
    task["items"][0]["attempts"] = 1
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["attempts"] == 0
```

同时**改写** `test_retry_rejects_non_failed_item`（已在文件中，约行 404）—— 它原本断言 `done` 不可重试，现在语义改了：

```python
def test_retry_rejects_non_failed_item_when_runner_active(authed_client_no_db, monkeypatch):
    """runner 活跃时任何状态都 409（由 Task 2 保证）；runner 不活跃时放开。"""
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 409
```

（把旧的 `test_retry_rejects_non_failed_item` 函数整体替换成上面这个新名字）

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_image_translate_routes.py -k "retry_item_allows or retry_rejects_non_failed_item_when_runner_active" -v`
Expected: 2 FAIL + 1 PASS（新的 `when_runner_active` 与 Task 2 重叠会先 PASS）

- [ ] **Step 3: 实现**

替换 `web/routes/image_translate.py` 的 `api_retry_item` 函数体：

```python
@bp.route("/api/image-translate/<task_id>/retry/<int:idx>", methods=["POST"])
@login_required
def api_retry_item(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    old_dst = (item.get("dst_tos_key") or "").strip()
    if old_dst:
        try:
            tos_clients.delete_object(old_dst)
        except Exception:
            pass
    item["status"] = "pending"
    item["attempts"] = 0
    item["error"] = ""
    item["dst_tos_key"] = ""
    total = len(task["items"])
    done = sum(1 for it in task["items"] if it["status"] == "done")
    failed = sum(1 for it in task["items"] if it["status"] == "failed")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=task["items"],
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "idx": idx, "status": "queued"}), 202
```

同时在 `web/routes/image_translate.py` 文件顶部已有 `from web.services import image_translate_runner`，不需要新 import。

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_image_translate_routes.py -k "retry" -v`
Expected: 全绿（含老的 `test_retry_failed_item_resets_and_triggers_runner`、新增的 3 个用例、改名后的 `test_retry_rejects_non_failed_item_when_runner_active`）

- [ ] **Step 5: 跑整个文件**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿

- [ ] **Step 6: commit**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): /retry/<idx> 支持任意 item 状态+清理旧 dst"
```

---

## Task 4: 新增 `/retry-unfinished` 路由

**Files:**
- Modify: `web/routes/image_translate.py`（在 `api_retry_failed` 附近新增函数）
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_image_translate_routes.py` 末尾追加：

```python
def test_retry_unfinished_resets_all_non_done(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"] = [
        {"idx": 0, "filename": "a.jpg", "src_tos_key": "s/a", "dst_tos_key": "d/a",
         "status": "done", "attempts": 1, "error": ""},
        {"idx": 1, "filename": "b.jpg", "src_tos_key": "s/b", "dst_tos_key": "",
         "status": "failed", "attempts": 3, "error": "timeout"},
        {"idx": 2, "filename": "c.jpg", "src_tos_key": "s/c", "dst_tos_key": "",
         "status": "running", "attempts": 1, "error": ""},
        {"idx": 3, "filename": "d.jpg", "src_tos_key": "s/d", "dst_tos_key": "",
         "status": "pending", "attempts": 0, "error": ""},
    ]
    task["progress"] = {"total": 4, "done": 1, "failed": 1, "running": 1}
    task["status"] = "running"
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    monkeypatch.setattr(r, "_start_runner", lambda tid_, uid: True)
    monkeypatch.setattr(store, "update", lambda *a, **kw: None)

    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 202
    data = resp.get_json()
    assert data["reset"] == 3
    assert task["items"][0]["status"] == "done"      # done 保持
    assert task["items"][1]["status"] == "pending"   # failed → pending
    assert task["items"][2]["status"] == "pending"   # running 僵尸 → pending
    assert task["items"][3]["status"] == "pending"   # pending → pending
    assert all(it["attempts"] == 0 for it in task["items"][1:])
    assert task["progress"]["failed"] == 0
    assert task["progress"]["running"] == 0
    assert task["status"] == "queued"


def test_retry_unfinished_409_when_runner_active(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=False)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: True)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 409
    assert "正在跑" in resp.get_json().get("error", "")


def test_retry_unfinished_409_when_all_done(authed_client_no_db, monkeypatch):
    from web.services import image_translate_runner
    tid = _prep_task(authed_client_no_db, monkeypatch, with_done=True)
    monkeypatch.setattr(image_translate_runner, "is_running", lambda t: False)
    resp = authed_client_no_db.post(f"/api/image-translate/{tid}/retry-unfinished")
    assert resp.status_code == 409
    assert "没有" in resp.get_json().get("error", "")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest tests/test_image_translate_routes.py -k "retry_unfinished" -v`
Expected: 3 FAIL（路由不存在，404）

- [ ] **Step 3: 实现**

在 `web/routes/image_translate.py` 的 `api_retry_failed` 函数**之后**追加：

```python
@bp.route("/api/image-translate/<task_id>/retry-unfinished", methods=["POST"])
@login_required
def api_retry_unfinished(task_id: str):
    """把所有非 done 的 item 重置为 pending 并重启 runner。
    与 retry-failed 的区别：范围不只是 failed，还包含 pending/running 僵尸。
    仅允许在 runner 不活跃时调用，避免与在跑的线程冲突。"""
    task = _get_owned_task(task_id)
    if image_translate_runner.is_running(task_id):
        return jsonify({"error": "任务正在跑，等跑完再重试"}), 409
    items = task.get("items") or []
    reset_count = 0
    for item in items:
        if item.get("status") == "done":
            continue
        old_dst = (item.get("dst_tos_key") or "").strip()
        if old_dst:
            try:
                tos_clients.delete_object(old_dst)
            except Exception:
                pass
        item["status"] = "pending"
        item["attempts"] = 0
        item["error"] = ""
        item["dst_tos_key"] = ""
        reset_count += 1
    if reset_count == 0:
        return jsonify({"error": "没有需要重试的图片"}), 409
    total = len(items)
    done = sum(1 for it in items if it["status"] == "done")
    task["progress"] = {"total": total, "done": done, "failed": 0, "running": 0}
    task["status"] = "queued"
    store.update(
        task_id,
        items=items,
        progress=task["progress"],
        status="queued",
    )
    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id, "reset": reset_count, "status": "queued"}), 202
```

- [ ] **Step 4: 跑测试**

Run: `pytest tests/test_image_translate_routes.py -k "retry_unfinished" -v`
Expected: 3 passed

- [ ] **Step 5: 跑整个文件**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿

- [ ] **Step 6: commit**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): 新增 /retry-unfinished 路由（重试所有非 done 项）"
```

---

## Task 5: 详情页 HTML —— 按钮 id/文案/位置

**Files:**
- Modify: `web/templates/image_translate_detail.html`（约行 37-46 的「进度」卡片）

- [ ] **Step 1: 编辑**

把 `web/templates/image_translate_detail.html` 里进度卡片这一段：

```html
<section class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px">
    <h2 style="margin:0">进度</h2>
    <button id="itRetryFailed" class="btn" type="button" hidden>一键重新生成失败项</button>
  </div>
  <div id="itProgress" class="it-progress">
    <span id="itProgressText">{{ state.progress.done }} / {{ state.progress.total }} 完成，{{ state.progress.failed }} 失败</span>
    <div class="it-progress-bar"><div id="itProgressFill"></div></div>
  </div>
</section>
```

改为：

```html
<section class="card">
  <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px">
    <h2 style="margin:0">进度</h2>
    <button id="itRetryUnfinished" class="btn btn-primary it-retry-main" type="button" hidden>重试未完成的图片</button>
  </div>
  <div id="itProgress" class="it-progress">
    <span id="itProgressText">{{ state.progress.done }} / {{ state.progress.total }} 完成，{{ state.progress.failed }} 失败</span>
    <div class="it-progress-bar"><div id="itProgressFill"></div></div>
  </div>
</section>
```

- [ ] **Step 2: 手工确认**

Run: `git diff web/templates/image_translate_detail.html`
Expected: 仅上述 diff

- [ ] **Step 3: commit**

```bash
git add web/templates/image_translate_detail.html
git commit -m "style(image-translate): 重试按钮改名重试未完成的图片并加显眼 class"
```

---

## Task 6: 详情页 JS —— 全局按钮新 id / 新条件 / 新接口

**Files:**
- Modify: `web/templates/_image_translate_scripts.html`

- [ ] **Step 1: 编辑**

在 `web/templates/_image_translate_scripts.html` 里：

把 `var retryFailedBtn = document.getElementById("itRetryFailed");` 改为：

```js
var retryUnfinishedBtn = document.getElementById("itRetryUnfinished");
```

把 `renderProgress` 函数里的 `retryFailedBtn` 相关块：

```js
if (retryFailedBtn) {
  var hasFailed = (p.failed || 0) > 0 && (state.status || "") !== "queued" && (state.status || "") !== "running";
  retryFailedBtn.hidden = !hasFailed;
}
```

改为：

```js
if (retryUnfinishedBtn) {
  var total = p.total || 0;
  var done = p.done || 0;
  var isRunning = state.is_running === true;
  var showBtn = total > 0 && done < total;
  retryUnfinishedBtn.hidden = !showBtn;
  retryUnfinishedBtn.disabled = isRunning;
  retryUnfinishedBtn.title = isRunning ? "任务正在跑，等跑完再重试" : "";
}
```

把点击事件块：

```js
if (retryFailedBtn) {
  retryFailedBtn.onclick = function(){
    retryFailedBtn.disabled = true;
    fetch("/api/image-translate/"+taskId+"/retry-failed",{method:"POST",credentials:"same-origin"})
      .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, body:d}; }); })
      .then(function(res){
        retryFailedBtn.disabled = false;
        if (!res.ok) { alert(res.body && res.body.error || "重试失败"); return; }
        refresh();
      })
      .catch(function(){ retryFailedBtn.disabled = false; });
  };
}
```

改为：

```js
if (retryUnfinishedBtn) {
  retryUnfinishedBtn.onclick = function(){
    retryUnfinishedBtn.disabled = true;
    fetch("/api/image-translate/"+taskId+"/retry-unfinished",{method:"POST",credentials:"same-origin"})
      .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, body:d}; }); })
      .then(function(res){
        if (!res.ok) { alert(res.body && res.body.error || "重试失败"); retryUnfinishedBtn.disabled = false; return; }
        refresh();
      })
      .catch(function(){ retryUnfinishedBtn.disabled = false; });
  };
}
```

- [ ] **Step 2: 确认后端测试不回归**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿

- [ ] **Step 3: 启动 dev 环境人工验证**

（如果 dev 服务未启动，跳过此步，在最终阶段一次性验证。）

打开一个卡在 `running` 状态的任务详情页（可手动造假：把 DB 里 state_json 的 `status` 改 `running`、某 item.status 改 `running`），刷新页面，观察：
- 「重试未完成的图片」按钮可见
- 按钮可点击（因 runtime 内存里无此 task，`is_running=false`）
- 点击后 runner 启动、状态正常刷新

- [ ] **Step 4: commit**

```bash
git add web/templates/_image_translate_scripts.html
git commit -m "feat(image-translate): 全局重试按钮依 is_running 显隐+调用新接口"
```

---

## Task 7: 详情页 JS —— 单图重试按钮所有状态可见

**Files:**
- Modify: `web/templates/_image_translate_scripts.html`

- [ ] **Step 1: 编辑**

找到 `renderItems` 函数里 items 的循环（约行 237-269），把 actions 块：

```js
var actions = document.createElement("div");
actions.className = "it-item-actions";
if (it.status === "done") {
  var a = document.createElement("a");
  a.className = "btn"; a.href = "/api/image-translate/"+taskId+"/download/result/"+it.idx;
  a.textContent = "下载"; actions.appendChild(a);
} else if (it.status === "failed") {
  var btn = document.createElement("button");
  btn.className = "btn"; btn.textContent = "重试";
  btn.onclick = function(){ retry(it.idx); };
  actions.appendChild(btn);
}
row.appendChild(actions);
```

改为：

```js
var actions = document.createElement("div");
actions.className = "it-item-actions";
var isRunning = state.is_running === true;
if (it.status === "done") {
  var a = document.createElement("a");
  a.className = "btn"; a.href = "/api/image-translate/"+taskId+"/download/result/"+it.idx;
  a.textContent = "下载"; actions.appendChild(a);
}
var retryBtn = document.createElement("button");
retryBtn.className = "btn it-retry-item";
retryBtn.type = "button";
retryBtn.textContent = it.status === "done" ? "重新生成" : "重试";
retryBtn.disabled = isRunning;
retryBtn.title = isRunning ? "任务正在跑，等跑完再重试" : "";
retryBtn.onclick = (function(i){ return function(){ retry(i); }; })(it.idx);
actions.appendChild(retryBtn);
row.appendChild(actions);
```

**注意**：外层 `renderItems` 内没有 `state` 参数，它接的是 `state` 参数，存在于闭包里。再读一遍：

```js
function renderItems(state){
  listEl.innerHTML = "";
  (state.items || []).forEach(function(it){
    // ...
  });
}
```

所以 `state.is_running` 可直接访问。上面的代码片段里 `var isRunning = state.is_running === true;` 是对的。

同时把原来的 `retry` 函数略加强（错误提示）：

```js
function retry(idx){
  fetch("/api/image-translate/"+taskId+"/retry/"+idx,{method:"POST",credentials:"same-origin"})
    .then(function(r){ return r.json().then(function(d){ return {ok:r.ok, body:d}; }); })
    .then(function(res){
      if (!res.ok) { alert(res.body && res.body.error || "重试失败"); return; }
      refresh();
    });
}
```

- [ ] **Step 2: 确认后端测试不回归**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: 全绿

- [ ] **Step 3: commit**

```bash
git add web/templates/_image_translate_scripts.html
git commit -m "feat(image-translate): 单图重试按钮对所有状态可见"
```

---

## Task 8: CSS —— 大按钮 + 单图按钮样式

**Files:**
- Modify: `web/templates/_image_translate_styles.html`

- [ ] **Step 1: 编辑**

在 `</style>` 前追加：

```css
/* 主进度按钮：「重试未完成的图片」——视觉放大 2×，海洋蓝主色 */
.it-retry-main {
  height: 40px;
  padding: 8px 20px;
  font-size: 15px;
  font-weight: 600;
  border-radius: 8px;
  letter-spacing: .01em;
}
.it-retry-main:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 单图重试按钮：紧凑的次级按钮 */
.it-retry-item {
  min-width: 64px;
  height: 32px;
  padding: 0 12px;
  font-size: 13px;
  border: 1.5px solid var(--primary-color, #1d6fe8);
  background: #fff;
  color: var(--primary-color, #1d6fe8);
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 120ms, color 120ms, box-shadow 120ms;
}
.it-retry-item:hover:not(:disabled) {
  background: #f0f6ff;
}
.it-retry-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  border-color: #d1d5db;
  color: #9ca3af;
}
```

- [ ] **Step 2: 手工确认**

Run: `git diff web/templates/_image_translate_styles.html`
Expected: 仅新增 CSS 片段

- [ ] **Step 3: commit**

```bash
git add web/templates/_image_translate_styles.html
git commit -m "style(image-translate): 重试按钮样式（主按钮放大 2×+单图描边按钮）"
```

---

## Task 9: 跑全部测试 + 手工验证

- [ ] **Step 1: 跑图片翻译相关全部测试**

Run: `pytest tests/test_image_translate_routes.py tests/test_image_translate_runner.py -v`
Expected: 全绿

- [ ] **Step 2: 跑 web 路由全部测试**

Run: `pytest tests/test_web_routes.py -v`
Expected: 全绿（若有失败需排查是否与本次改动相关）

- [ ] **Step 3: 启动本地 dev 服务（若未启动）**

（按项目惯例启动，略；或跳过此步让用户自己验证。）

- [ ] **Step 4: 人工验证场景**

场景 A：**正常任务跑完后**
- 进入详情页，全部 done：全局按钮隐藏，单图显示「重新生成」按钮且可点。
- 点一张 done 的「重新生成」：202，旧输出被删，runner 重跑，该张进入 running → done。

场景 B：**服务重启后卡住的任务**
- 手动把某任务 DB 里的 `state_json.status` 改 `running`、某 item.status 改 `running`；前端刷新。
- 全局「重试未完成的图片」按钮可见且可点。
- 单图重试按钮全部可见且可点。
- 点全局按钮：202，所有非 done 的 item 重置，runner 重跑。

场景 C：**runner 正在跑**
- 刚提交新任务，runner 活跃。
- 全局按钮 `disabled`，tooltip 提示"任务正在跑，等跑完再重试"。
- 单图重试按钮 `disabled`。

场景 D：**有失败项的任务**（runner 已结束）
- task.status=`done`、有 `failed` 项。
- 全局按钮可见可点（done < total 成立）。
- 单图 failed 的按钮显示「重试」可点。

- [ ] **Step 5: 确认 spec 所有要求都满足**

对照 `docs/superpowers/specs/2026-04-20-image-translate-retry-improvements-design.md`：
- ① `_state_payload.is_running` → Task 1 ✓
- ② `/retry/<idx>` 放宽校验 + 删旧 dst → Task 2+3 ✓
- ③ `/retry-unfinished` 新路由 → Task 4 ✓
- ④ `/retry-failed` 兼容保留 → 未动 ✓
- ⑤ 全局按钮放大 + 新 id + 新条件 → Task 5+6+8 ✓
- ⑥ 单图按钮所有状态可见 + `is_running` 禁用 → Task 7+8 ✓
- ⑦ 不做自动 resume → 未触及 `resume_inflight_tasks()` ✓

- [ ] **Step 6: 最终 commit（若上述步骤产生微调）**

若人工验证中发现 CSS/JS 微调，追加 commit；否则无需。

---

## 备注：回滚

`git revert` 每个 commit 即可。DB schema 不变、state_json 结构不变、老接口 `/retry-failed` 保留。
