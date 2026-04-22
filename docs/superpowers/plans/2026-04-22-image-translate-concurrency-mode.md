# 图片翻译串行/并行模式选择 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在创建图片翻译任务时选择串行（现行 1 并发）或并行（10 并发分批）模式，两个入口（图片翻译菜单、素材编辑「从英语版一键翻译」）都支持。

**Architecture:** 任务 state 加 `concurrency_mode` 字段（默认 `sequential`，兼容老任务）。Runtime 里把现有 `for idx ... _process_one(...)` 抽成 `_run_sequential()`，新增 `_run_parallel()` 用 `ThreadPoolExecutor(max_workers=10)` 分批跑。共享状态（progress / store.update / rate-limit deque）全部加 `self._state_lock` 保护。熔断逻辑不改（5 次/60s）。

**Tech Stack:** Python 3.11 + Flask + pytest + concurrent.futures；前端纯 JS（无 React）+ Jinja2 模板 + SocketIO。

**Working directory:** 本 plan 及所有实现在 worktree `G:/Code/AutoVideoSrtLocal-image-translate-concurrency` 分支 `feature/image-translate-concurrency` 完成。请先 `cd` 到该目录。

**Spec reference:** [docs/superpowers/specs/2026-04-22-image-translate-concurrency-mode-design.md](../specs/2026-04-22-image-translate-concurrency-mode-design.md)

---

## 文件结构总览

**修改**：
- `appcore/task_state.py`：`create_image_translate(...)` 加 kwarg；state 新字段
- `appcore/image_translate_runtime.py`：重构 `start()`，拆分串/并行路径，加 `_state_lock`
- `web/routes/image_translate.py`：`api_upload_complete` 接受并校验 `concurrency_mode`
- `web/routes/medias.py`：`api_detail_images_translate_from_en` 接受并校验 `concurrency_mode`
- `web/templates/image_translate_list.html`：加「处理模式」pill HTML
- `web/templates/_image_translate_scripts.html`：绑 pill，submit body 带字段
- `web/templates/_medias_edit_detail_modal.html`：modal 分配置态 / 结果态
- `web/static/medias.js`：modal 两态切换、chip 选择、按钮逻辑

**测试**：
- `tests/test_image_translate_runtime.py`：并行路径、分批、熔断新用例（现有用例保持不变）
- `tests/test_image_translate_routes.py`：`api_upload_complete` 三个 concurrency_mode 用例
- `tests/test_medias_routes.py`：素材一键翻译路由三个 concurrency_mode 用例

---

## Task 1：task_state 支持 `concurrency_mode` 字段

**Files:**
- Modify: `appcore/task_state.py`（`create_image_translate` 在 547 行附近）

- [ ] **Step 1：加一个失败的测试**

追加到 `tests/test_image_translate_runtime.py` 末尾：

```python
def test_create_image_translate_stores_concurrency_mode():
    """task_state.create_image_translate 接受 concurrency_mode 并写入 state；默认 sequential。"""
    from appcore import task_state as ts
    from unittest.mock import patch

    with patch.object(ts, "_db_upsert"):  # 不走 DB
        # 1) 默认
        t1 = ts.create_image_translate(
            "t-cm-1", "/tmp/x",
            user_id=1, preset="cover", target_language="de",
            target_language_name="德语", model_id="gemini-x",
            prompt="p", items=[],
        )
        assert t1["concurrency_mode"] == "sequential"

        # 2) 显式 parallel
        t2 = ts.create_image_translate(
            "t-cm-2", "/tmp/x",
            user_id=1, preset="cover", target_language="de",
            target_language_name="德语", model_id="gemini-x",
            prompt="p", items=[],
            concurrency_mode="parallel",
        )
        assert t2["concurrency_mode"] == "parallel"

    # cleanup
    with ts._lock:
        ts._tasks.pop("t-cm-1", None)
        ts._tasks.pop("t-cm-2", None)
```

- [ ] **Step 2：跑测试，确认失败**

```bash
pytest tests/test_image_translate_runtime.py::test_create_image_translate_stores_concurrency_mode -xvs
```

预期：`TypeError: create_image_translate() got an unexpected keyword argument 'concurrency_mode'`（或 KeyError）

- [ ] **Step 3：最小实现**

编辑 `appcore/task_state.py`，找到 `create_image_translate(...)` 签名（547 行附近），加 kwarg：

```python
def create_image_translate(task_id: str, task_dir: str, *,
                            user_id: int,
                            preset: str,
                            target_language: str,
                            target_language_name: str,
                            model_id: str,
                            prompt: str,
                            items: list[dict],
                            product_name: str = "",
                            project_name: str = "",
                            medias_context: dict | None = None,
                            concurrency_mode: str = "sequential") -> dict:
```

然后在 `task = { ... }` 字典里 `"error": "",` 行的**上方**增加一行（约 597 行附近）：

```python
        "concurrency_mode": concurrency_mode if concurrency_mode in {"sequential", "parallel"} else "sequential",
```

- [ ] **Step 4：跑测试，确认通过**

```bash
pytest tests/test_image_translate_runtime.py::test_create_image_translate_stores_concurrency_mode -xvs
```

预期：PASS

- [ ] **Step 5：跑现有 image_translate 测试，确认零回归**

```bash
pytest tests/test_image_translate_runtime.py tests/test_image_translate_routes.py -x
```

预期：全 PASS

- [ ] **Step 6：commit**

```bash
git add appcore/task_state.py tests/test_image_translate_runtime.py
git commit -m "feat(image_translate): task_state 加 concurrency_mode 字段"
```

---

## Task 2：API — `/api/image-translate/upload/complete` 接受 `concurrency_mode`

**Files:**
- Modify: `web/routes/image_translate.py`（`api_upload_complete` 在约 195 行）
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1：加三个失败的测试**

追加到 `tests/test_image_translate_routes.py` 末尾：

```python
def _post_complete(client, body_extra=None):
    """共用：提交一张图走完 bootstrap → complete 的 happy path，返回 complete 响应。"""
    bootstrap = client.post("/api/image-translate/upload/bootstrap", json={
        "files": [{"filename": "a.jpg"}],
    })
    assert bootstrap.status_code == 200
    bd = bootstrap.get_json()
    body = {
        "task_id": bd["task_id"],
        "product_name": "灯",
        "preset": "cover",
        "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "p",
        "uploaded": [{
            "idx": bd["uploads"][0]["idx"],
            "object_key": bd["uploads"][0]["object_key"],
            "filename": "a.jpg",
        }],
    }
    if body_extra:
        body.update(body_extra)
    return client.post("/api/image-translate/upload/complete", json=body)


def test_upload_complete_defaults_to_sequential(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)

    resp = _post_complete(authed_client_no_db)
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task_id"]
    assert mem[task_id]["concurrency_mode"] == "sequential"


def test_upload_complete_accepts_parallel(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    mem = _patch_task_state(monkeypatch)

    resp = _post_complete(authed_client_no_db, {"concurrency_mode": "parallel"})
    assert resp.status_code == 201, resp.get_json()
    task_id = resp.get_json()["task_id"]
    assert mem[task_id]["concurrency_mode"] == "parallel"


def test_upload_complete_rejects_invalid_mode(authed_client_no_db, monkeypatch):
    _patch_tos_and_runner(monkeypatch)
    _patch_lang(monkeypatch)
    _patch_task_state(monkeypatch)

    resp = _post_complete(authed_client_no_db, {"concurrency_mode": "fast"})
    assert resp.status_code == 400
    assert "concurrency_mode" in resp.get_json()["error"]
```

- [ ] **Step 2：跑测试，确认失败**

```bash
pytest tests/test_image_translate_routes.py::test_upload_complete_defaults_to_sequential tests/test_image_translate_routes.py::test_upload_complete_accepts_parallel tests/test_image_translate_routes.py::test_upload_complete_rejects_invalid_mode -xvs
```

预期：第 1、2 个测试失败于 `KeyError: 'concurrency_mode'`（因为 fake_create 存字段但路由不传）；第 3 个失败因为 201 而非 400

- [ ] **Step 3：修改路由**

编辑 `web/routes/image_translate.py` 的 `api_upload_complete()`。在 `product_name` 校验之后、`if not uploaded:` 之前（约 223 行）插入：

```python
    mode_raw = (body.get("concurrency_mode") or "sequential").strip().lower()
    if mode_raw not in {"sequential", "parallel"}:
        return jsonify({"error": "concurrency_mode 必须是 sequential 或 parallel"}), 400
```

然后在 `task_state.create_image_translate(...)` 调用里（约 259 行）追加 kwarg：

```python
    task_state.create_image_translate(
        task_id,
        task_dir,
        user_id=current_user.id,
        preset=preset,
        target_language=lang_code,
        target_language_name=lang_name,
        model_id=model_id,
        prompt=final_prompt,
        items=items,
        product_name=product_name,
        project_name=project_name,
        concurrency_mode=mode_raw,
    )
```

- [ ] **Step 4：跑三个测试，确认通过**

```bash
pytest tests/test_image_translate_routes.py::test_upload_complete_defaults_to_sequential tests/test_image_translate_routes.py::test_upload_complete_accepts_parallel tests/test_image_translate_routes.py::test_upload_complete_rejects_invalid_mode -xvs
```

预期：全 PASS

- [ ] **Step 5：跑全量 routes 测试确认零回归**

```bash
pytest tests/test_image_translate_routes.py -x
```

预期：全 PASS

- [ ] **Step 6：commit**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image_translate): upload/complete 接受 concurrency_mode 参数"
```

---

## Task 3：API — `/medias/api/products/<pid>/detail-images/translate-from-en` 接受 `concurrency_mode`

**Files:**
- Modify: `web/routes/medias.py`（`api_detail_images_translate_from_en` 在约 1620 行）
- Test: `tests/test_medias_routes.py`

- [ ] **Step 1：加三个失败的测试**

追加到 `tests/test_medias_routes.py` 末尾：

```python
def _setup_detail_translate(monkeypatch):
    """公用的 fixture patch：让 detail-images/translate-from-en 跑通但不触发真实 IO。"""
    from web.routes import medias as r

    monkeypatch.setattr(r.tos_clients, "is_media_bucket_configured", lambda: True)
    monkeypatch.setattr(r.medias, "get_product", lambda pid: {"id": pid, "user_id": 1, "name": "灯"})
    monkeypatch.setattr(r, "_can_access_product", lambda product: True)
    monkeypatch.setattr(
        r.medias, "list_detail_images",
        lambda pid, lang: [{"id": 11, "object_key": "1/medias/1/a.jpg"}] if lang == "en" else [],
    )
    monkeypatch.setattr(r.medias, "is_valid_language", lambda code: code in {"en", "de"})
    monkeypatch.setattr(r.medias, "get_language_name", lambda lang: "德语")
    monkeypatch.setattr(r.its, "get_prompts_for_lang", lambda lang: {"detail": "翻"})
    monkeypatch.setattr("appcore.api_keys.resolve_extra", lambda uid, svc: {})
    monkeypatch.setattr(r, "_start_image_translate_runner", lambda task_id, user_id: True)

    created = {}
    monkeypatch.setattr(
        r.task_state, "create_image_translate",
        lambda task_id, task_dir, **kw: created.update(kw) or {"id": task_id},
    )
    return created


def test_detail_translate_defaults_to_sequential(authed_client_no_db, monkeypatch):
    created = _setup_detail_translate(monkeypatch)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/detail-images/translate-from-en",
        json={"lang": "de"},
    )
    assert resp.status_code == 201, resp.get_json()
    assert created["concurrency_mode"] == "sequential"


def test_detail_translate_accepts_parallel(authed_client_no_db, monkeypatch):
    created = _setup_detail_translate(monkeypatch)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/detail-images/translate-from-en",
        json={"lang": "de", "concurrency_mode": "parallel"},
    )
    assert resp.status_code == 201, resp.get_json()
    assert created["concurrency_mode"] == "parallel"


def test_detail_translate_rejects_invalid_mode(authed_client_no_db, monkeypatch):
    _setup_detail_translate(monkeypatch)
    resp = authed_client_no_db.post(
        "/medias/api/products/1/detail-images/translate-from-en",
        json={"lang": "de", "concurrency_mode": "fast"},
    )
    assert resp.status_code == 400
    assert "concurrency_mode" in resp.get_json()["error"]
```

- [ ] **Step 2：跑测试，确认失败**

```bash
pytest tests/test_medias_routes.py::test_detail_translate_defaults_to_sequential tests/test_medias_routes.py::test_detail_translate_accepts_parallel tests/test_medias_routes.py::test_detail_translate_rejects_invalid_mode -xvs
```

预期：前 2 个 `KeyError: 'concurrency_mode'`；第 3 个 201 而非 400。

- [ ] **Step 3：修改路由**

编辑 `web/routes/medias.py`，在 `api_detail_images_translate_from_en()` 里，找到 `if lang == "en": return ...` 之后、`source_rows = medias.list_detail_images(...)` 之前（约 1632 行）插入：

```python
    mode_raw = (body.get("concurrency_mode") or "sequential").strip().lower()
    if mode_raw not in {"sequential", "parallel"}:
        return jsonify({"error": "concurrency_mode 必须是 sequential 或 parallel"}), 400
```

然后在 `task_state.create_image_translate(...)` 调用里（约 1678 行），在 `medias_context=medias_context,` 后加一行：

```python
        concurrency_mode=mode_raw,
```

- [ ] **Step 4：跑三个测试，确认通过**

```bash
pytest tests/test_medias_routes.py::test_detail_translate_defaults_to_sequential tests/test_medias_routes.py::test_detail_translate_accepts_parallel tests/test_medias_routes.py::test_detail_translate_rejects_invalid_mode -xvs
```

预期：全 PASS

- [ ] **Step 5：跑全量 medias routes 测试确认零回归**

```bash
pytest tests/test_medias_routes.py -x
```

预期：全 PASS

- [ ] **Step 6：commit**

```bash
git add web/routes/medias.py tests/test_medias_routes.py
git commit -m "feat(medias): 一键翻译接受 concurrency_mode 参数"
```

---

## Task 4：Runtime 加 `_state_lock` + 拆出 `_run_sequential()`（零行为变更）

目的：把现有 `start()` 里的主循环抽成 `_run_sequential`，为后面加 `_run_parallel` 铺路；同时引入 `self._state_lock` 保护共享状态。此步**不改变**任何外部行为。

**Files:**
- Modify: `appcore/image_translate_runtime.py`

- [ ] **Step 1：跑现有测试记录基线**

```bash
pytest tests/test_image_translate_runtime.py -x
```

预期：全 PASS（记录数量，例如 13 passed）

- [ ] **Step 2：加 `_state_lock` 并重构**

编辑 `appcore/image_translate_runtime.py`：

① 在文件顶部 `import time` 行后加：
```python
import threading
```

② 在 `ImageTranslateRuntime.__init__` 里（45-48 行）把 `self._rate_limit_hits: deque[float] = deque()` 之后加一行：
```python
        self._state_lock = threading.Lock()
```

③ `_record_rate_limit_hit` 里操作 deque 的部分包进 `with self._state_lock:`：

```python
    def _record_rate_limit_hit(self) -> bool:
        """记一次可重试错误（429/5xx），返回 True 表示应熔断整任务。"""
        now = time.monotonic()
        cutoff = now - _RATE_LIMIT_WINDOW_SEC
        with self._state_lock:
            while self._rate_limit_hits and self._rate_limit_hits[0] < cutoff:
                self._rate_limit_hits.popleft()
            self._rate_limit_hits.append(now)
            return len(self._rate_limit_hits) >= _RATE_LIMIT_THRESHOLD
```

④ 在 `start()` 里（59 行起），把 `try: for idx in range(len(items)): ... self._process_one(...)` 那段替换成调度分发。把原来的 for 循环**整块**挪到新方法 `_run_sequential(...)`。最终 `start()` 长这样（只列 try 之前的新增和 try 块，其它保留）：

```python
    def start(self, task_id: str) -> None:
        task = store.get(task_id)
        if not task or task.get("type") != "image_translate":
            logger.warning("image_translate runtime: task not found: %s", task_id)
            return

        task["status"] = "running"
        task["steps"]["process"] = "running"
        _it_model = task.get("model_id") or "gemini-2.5-flash"
        task.setdefault("step_model_tags", {})["process"] = f"gemini · {_it_model}"
        store.update(task_id, status="running", steps=task["steps"],
                     step_model_tags=task.get("step_model_tags", {}))

        mode = (task.get("concurrency_mode") or "sequential").strip().lower()
        circuit_msg = ""
        try:
            if mode == "parallel":
                self._run_parallel(task, task_id)
            else:
                self._run_sequential(task, task_id)
        except _CircuitOpen as exc:
            circuit_msg = str(exc) or "上游持续限流，已熔断"
            logger.warning(
                "[image_translate] circuit breaker opened for task %s: %s",
                task_id, circuit_msg,
            )
            self._abort_remaining_items(task, task_id, circuit_msg)

        if circuit_msg:
            task["status"] = "error"
            task["steps"]["process"] = "error"
            task["error"] = circuit_msg
        else:
            task["status"] = "done"
            task["steps"]["process"] = "done"
        try:
            self._finalize_auto_apply(task)
        except Exception as exc:
            ctx = dict(task.get("medias_context") or {})
            if ctx:
                ctx["apply_status"] = "apply_error"
                ctx["last_apply_error"] = str(exc)
                task["medias_context"] = ctx
        _update_progress(task)
        store.update(
            task_id,
            status=task["status"],
            steps=task["steps"],
            progress=task["progress"],
            items=task["items"],
            medias_context=task.get("medias_context") or {},
        )
        self.bus.publish(Event(
            type="image_translate:task_done",
            task_id=task_id,
            payload={"task_id": task_id, "status": task["status"]},
        ))

    def _run_sequential(self, task: dict, task_id: str) -> None:
        items = task.get("items") or []
        for idx in range(len(items)):
            if items[idx]["status"] in {"done", "failed"}:
                continue
            self._process_one(task, task_id, idx)

    def _run_parallel(self, task: dict, task_id: str) -> None:
        # 占位：下一 Task 实现
        raise NotImplementedError("_run_parallel 将在 Task 5 实现")
```

- [ ] **Step 3：跑全量 runtime 测试，确认零回归**

```bash
pytest tests/test_image_translate_runtime.py -x
```

预期：数量与 Step 1 一致，全 PASS。

- [ ] **Step 4：commit**

```bash
git add appcore/image_translate_runtime.py
git commit -m "refactor(image_translate): 拆出 _run_sequential 并引入 _state_lock"
```

---

## Task 5：Runtime 实现 `_run_parallel()` 并分批跑

**Files:**
- Modify: `appcore/image_translate_runtime.py`
- Test: `tests/test_image_translate_runtime.py`

- [ ] **Step 1：加并行基本行为测试**

追加到 `tests/test_image_translate_runtime.py`：

```python
def test_parallel_runs_all_items_and_is_faster_than_sequential(tmp_path):
    """并行模式：20 张图每张 sleep 50ms，总耗时 << 串行 1s。"""
    import time as _time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(20)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        _time.sleep(0.05)
        return b"OUT", "image/png"

    t0 = _time.monotonic()
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")
    elapsed = _time.monotonic() - t0

    # 串行 20 × 50ms = 1000ms；并行分 2 批 × 50ms = 100ms + 开销
    assert elapsed < 0.5, f"parallel should be fast, got {elapsed:.2f}s"
    for it in task["items"]:
        assert it["status"] == "done", (it["idx"], it)


def test_parallel_runs_in_batches_of_10(tmp_path):
    """21 个 item：前 10 个并发（启动时差 < 50ms），第 11-20 个在第一批后启动，第 21 个自成一批。"""
    import time as _time
    import threading
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(21)])
    task["concurrency_mode"] = "parallel"

    starts = {}
    lock = threading.Lock()

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        idx = kw.get("project_id")  # task_id 不含 idx；用 start 时间排序即可
        with lock:
            starts[_time.monotonic()] = True
        _time.sleep(0.1)
        return b"OUT", "image/png"

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    start_times = sorted(starts.keys())
    assert len(start_times) == 21
    # 前 10 个启动时间应聚集在 100ms 内
    assert start_times[9] - start_times[0] < 0.1, f"first batch spread: {start_times[9]-start_times[0]:.3f}s"
    # 第 11 个启动必在前 10 个 gen 结束之后（至少 80ms 之后）
    assert start_times[10] - start_times[0] > 0.08, f"batch 2 gap: {start_times[10]-start_times[0]:.3f}s"
    # 所有 item 完成
    for it in task["items"]:
        assert it["status"] == "done"


def test_parallel_skips_already_terminal_items(tmp_path):
    """已 done/failed 的 item 在并行模式下也不重跑。"""
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(12)])
    task["concurrency_mode"] = "parallel"
    task["items"][0]["status"] = "done"
    task["items"][1]["status"] = "failed"

    call_count = [0]

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        call_count[0] += 1
        return b"OUT", "image/png"

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    # 12 - 2 = 10 次 generate_image
    assert call_count[0] == 10
    assert task["items"][0]["status"] == "done"
    assert task["items"][1]["status"] == "failed"
    for i in range(2, 12):
        assert task["items"][i]["status"] == "done"
```

- [ ] **Step 2：跑新测试，确认失败**

```bash
pytest tests/test_image_translate_runtime.py::test_parallel_runs_all_items_and_is_faster_than_sequential -xvs
```

预期：`NotImplementedError: _run_parallel 将在 Task 5 实现`

- [ ] **Step 3：实现 `_run_parallel()` 并加 `_BATCH_SIZE` 常量**

编辑 `appcore/image_translate_runtime.py`：

① 顶部 import 区加：
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
```

② 模块级常量区（`_BACKOFF_BASE` 之后）加：
```python
_BATCH_SIZE = 10  # 并行模式单批最大并发数
```

③ 把 Task 4 里的 `_run_parallel` 占位体替换成真实实现：

```python
    def _run_parallel(self, task: dict, task_id: str) -> None:
        items = task.get("items") or []
        pending_idxs = [
            i for i, it in enumerate(items)
            if it["status"] not in {"done", "failed"}
        ]
        for batch_start in range(0, len(pending_idxs), _BATCH_SIZE):
            batch = pending_idxs[batch_start : batch_start + _BATCH_SIZE]
            with ThreadPoolExecutor(max_workers=_BATCH_SIZE) as pool:
                futures = [pool.submit(self._process_one, task, task_id, idx)
                           for idx in batch]
                # _process_one 吞掉所有业务异常；只有 _CircuitOpen 会向上传播
                for fut in as_completed(futures):
                    fut.result()
```

- [ ] **Step 4：跑 3 个新测试**

```bash
pytest tests/test_image_translate_runtime.py::test_parallel_runs_all_items_and_is_faster_than_sequential tests/test_image_translate_runtime.py::test_parallel_runs_in_batches_of_10 tests/test_image_translate_runtime.py::test_parallel_skips_already_terminal_items -xvs
```

预期：全 PASS

- [ ] **Step 5：跑全量 runtime 测试确认零回归**

```bash
pytest tests/test_image_translate_runtime.py -x
```

预期：全 PASS。

- [ ] **Step 6：commit**

```bash
git add appcore/image_translate_runtime.py tests/test_image_translate_runtime.py
git commit -m "feat(image_translate): runtime 加并行路径（单批 10 并发）"
```

---

## Task 6：Runtime 并发安全 — 用 `_state_lock` 保护 `_process_one` 的共享写

上一 Task 并行已经工作，但 `_process_one` 里每步"改 item 状态 → `_update_progress(task)` → `store.update(...)` → `bus.publish(...)`"涉及的读写在并行下会有竞争（progress 数读到不一致中间态、store.update 串行性假设被打破、deque 已在 Task 4 保护）。本 Task 给 `_process_one` 里这些"状态推进"的相邻操作加锁。

**Files:**
- Modify: `appcore/image_translate_runtime.py`（`_process_one` 128-223 行）

- [ ] **Step 1：加一个并行熔断测试（检查多线程下熔断仍正确）**

追加到 `tests/test_image_translate_runtime.py`：

```python
def test_parallel_circuit_breaker_aborts_remaining(tmp_path):
    """并行下若上游持续 429，_CircuitOpen 穿透后剩余 items 全部标 failed。"""
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(i) for i in range(15)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        raise GeminiImageRetryable("429 Too Many Requests")

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    # 所有 15 个 item 都应是 failed；至少一个 error 里带"限流"或"熔断"
    for it in task["items"]:
        assert it["status"] == "failed", (it["idx"], it)
    reasons = [it.get("error", "") for it in task["items"]]
    assert any("限流" in r or "熔断" in r for r in reasons), reasons
    assert task["status"] == "error"


def test_parallel_progress_is_consistent(tmp_path):
    """并行跑完后 progress 自洽：total=sum(done+failed+running+pending)，running=0。"""
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(15)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    p = task["progress"]
    assert p["total"] == 15
    assert p["done"] == 15
    assert p["failed"] == 0
    assert p["running"] == 0
```

- [ ] **Step 2：跑测试**

```bash
pytest tests/test_image_translate_runtime.py::test_parallel_circuit_breaker_aborts_remaining tests/test_image_translate_runtime.py::test_parallel_progress_is_consistent -xvs
```

预期：**都应该通过**（Task 5 的实现已让它们跑通），但这两个测试会**作为长期保护**锁定行为。如果哪个失败，说明并发安全确实有 bug，需要修。

> 如果两个都 PASS → 进 Step 3 加一个真并发压力测试进一步保护。

- [ ] **Step 3：补一个并发写压力测试**

追加到 `tests/test_image_translate_runtime.py`：

```python
def test_parallel_no_lost_updates_under_contention(tmp_path):
    """20 个 item 并发 done，最终 items 列表每个 status=done，无丢失更新。"""
    import time as _time
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(i) for i in range(20)])
    task["concurrency_mode"] = "parallel"

    def fake_download(key, lp):
        open(lp, "wb").write(b"IMG")
        return lp

    def fake_gen(*a, **kw):
        _time.sleep(0.02)  # 制造线程切换机会
        return b"OUT", "image/png"

    store_updates = []

    def rec_update(task_id, **kw):
        # 记录每次 store.update 时 progress 快照（并发下应保持自洽）
        if "progress" in kw:
            p = kw["progress"]
            store_updates.append(dict(p))

    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update", side_effect=rec_update), \
         patch.object(rt.tos_clients, "download_file", side_effect=fake_download), \
         patch.object(rt.tos_clients, "upload_file", lambda lp, key: None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=fake_gen):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    # 所有 items 应 done
    assert all(it["status"] == "done" for it in task["items"])
    # 每次快照的 done+failed+running 永远 <= total，且最终 done=total
    for p in store_updates:
        assert p["done"] + p["failed"] + p["running"] <= p["total"], p
    assert task["progress"]["done"] == 20
```

- [ ] **Step 4：跑测试**

```bash
pytest tests/test_image_translate_runtime.py::test_parallel_no_lost_updates_under_contention -xvs
```

若 FAIL（例如 progress 有不一致快照）→ Step 5 加锁。

若 PASS → 跳 Step 5、直接 Step 6 commit（说明 Task 4 引入的锁＋Python GIL 足够）。

- [ ] **Step 5：加锁（如果 Step 4 失败）**

编辑 `appcore/image_translate_runtime.py` 的 `_process_one`，把每处 "`_update_progress(task)` → `store.update(...)` → `self._emit_item(...)` / `self._emit_progress(...)`" 的"状态推进三件套"包进 `with self._state_lock:`。**注意**：
- `_emit_*` 走 SocketIO，是 I/O；放在锁内**不影响正确性**但影响并发吞吐。保守起见把 emit 挪到锁外
- 套用的地方包括：`item.status = "running"` 后的那段、done 分支、各失败分支，共 5 处

示意重构（done 分支为例）：

```python
                # done 分支
                item["status"] = "done"
                item["dst_tos_key"] = dst_key
                item["error"] = ""
                with self._state_lock:
                    _update_progress(task)
                    store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
```

对其它 4 处（running 态起手、`GeminiImageError` 分支、`GeminiImageRetryable` 最终失败分支、通用 Exception 最终失败分支）做相同处理。**不动**重试中继的 sleep。

再跑 Step 4 的测试验证通过。

- [ ] **Step 6：跑全量 runtime 测试**

```bash
pytest tests/test_image_translate_runtime.py -x
```

预期：全 PASS（现有用例 + 新增 3 个）。

- [ ] **Step 7：commit**

```bash
git add appcore/image_translate_runtime.py tests/test_image_translate_runtime.py
git commit -m "feat(image_translate): 并行路径熔断与并发一致性测试保护"
```

---

## Task 7：UI — 图片翻译菜单页加「处理模式」pill

**Files:**
- Modify: `web/templates/image_translate_list.html`
- Modify: `web/templates/_image_translate_scripts.html`

- [ ] **Step 1：加 HTML pill**

编辑 `web/templates/image_translate_list.html`，找到「使用模型」的 form-row（41-44 行），在其 `</div>` 之后、「提示词」form-row 之前插入：

```html
    <div class="form-row">
      <label>处理模式</label>
      <div id="itConcurrencyPills" class="it-pill-group" role="radiogroup">
        <button type="button" class="it-pill is-active" data-value="sequential" role="radio" aria-checked="true">串行（默认）</button>
        <button type="button" class="it-pill" data-value="parallel" role="radio" aria-checked="false">并行</button>
      </div>
      <p class="hint">串行：一张一张跑，稳。并行：单批最多 10 张同时跑，快但对上游限流更敏感。</p>
      <input type="hidden" id="itConcurrencyMode" value="sequential">
    </div>
```

- [ ] **Step 2：绑 pill + 提交带 concurrency_mode**

编辑 `web/templates/_image_translate_scripts.html`：

① 在变量声明区（11-17 行附近）加：
```javascript
    var concurrencyEl = document.getElementById("itConcurrencyMode");
    var concurrencyPills = document.getElementById("itConcurrencyPills");
```

② 在 `bindPillGroup(presetPills, presetEl, ...)`（约 127 行）所在块的后面加一行：
```javascript
    bindPillGroup(concurrencyPills, concurrencyEl, null);
```

③ 在 `upload/complete` 的 `JSON.stringify({...})`（约 196-206 行）里 `prompt: promptEl.value,` 之后加一行：
```javascript
            concurrency_mode: concurrencyEl.value,
```

- [ ] **Step 3：启动 dev server，人工验证**

```bash
# worktree 根目录
python -m web.app  # 或项目约定的启动方式
```

用浏览器访问 `/image-translate`：
- 看到「处理模式」那栏，默认「串行」激活
- 点「并行」，激活状态切换
- 提交任务后，DB 里 projects 表的 state_json 含 `"concurrency_mode":"parallel"`

（DB 可用 mysql 或 sqlite CLI 查：`SELECT state_json FROM projects WHERE type='image_translate' ORDER BY created_at DESC LIMIT 1;`）

- [ ] **Step 4：commit**

```bash
git add web/templates/image_translate_list.html web/templates/_image_translate_scripts.html
git commit -m "feat(image_translate): 前端加串行/并行 pill"
```

---

## Task 8：UI — 素材编辑「从英语版一键翻译」modal 加配置态

modal ID `edDetailTranslateTaskMask` 目前是"零确认"直接提交 + 展示结果。改成：点按钮先弹 modal（配置态），用户选模式后点「开始翻译」才调 API，成功/失败切到结果态。

**Files:**
- Modify: `web/templates/_medias_edit_detail_modal.html`（`edDetailTranslateTaskMask` 约 278-294 行）
- Modify: `web/static/medias.js`（`edStartDetailTranslate`、`edOpenDetailTranslateTaskModal` 约 1593-1644 行）

- [ ] **Step 1：改 modal HTML**

编辑 `web/templates/_medias_edit_detail_modal.html`，把 `edDetailTranslateTaskMask` 的 `<div class="oc-modal-body" ...>` 整块（约 286-293 行）替换成：

```html
          <div class="oc-modal-body" style="padding:var(--oc-sp-5);display:grid;gap:var(--oc-sp-3);">
            <!-- 配置态 -->
            <div id="edDetailTranslateTaskConfig" style="display:grid;gap:var(--oc-sp-3);">
              <div>
                <div style="font-size:13px;font-weight:600;margin-bottom:var(--oc-sp-2);">处理模式</div>
                <div id="edDetailTranslateModeGroup" style="display:flex;gap:var(--oc-sp-2);flex-wrap:wrap;">
                  <button type="button" class="oc-chip on" data-mode="sequential" role="radio" aria-checked="true">串行（默认）</button>
                  <button type="button" class="oc-chip" data-mode="parallel" role="radio" aria-checked="false">并行</button>
                </div>
                <p class="oc-hint" style="margin-top:var(--oc-sp-2);">串行稳；并行单批 10 张同时跑，遇限流更容易熔断。</p>
              </div>
              <div style="display:flex;justify-content:flex-end;gap:var(--oc-sp-2);margin-top:var(--oc-sp-2);">
                <button class="oc-btn ghost" id="edDetailTranslateCancelBtn">取消</button>
                <button class="oc-btn primary" id="edDetailTranslateStartBtn">开始翻译</button>
              </div>
            </div>
            <!-- 结果态：沿用原有消息和链接 -->
            <div id="edDetailTranslateTaskResult" hidden style="display:grid;gap:var(--oc-sp-3);">
              <div id="edDetailTranslateTaskMsg" style="font-size:14px;color:var(--oc-fg);">准备中...</div>
              <div id="edDetailTranslateTaskMeta" class="oc-hint"></div>
              <div style="display:flex;justify-content:flex-end;gap:var(--oc-sp-2);flex-wrap:wrap;">
                <a class="oc-btn primary" id="edDetailTranslateTaskLink" href="#" target="_blank" rel="noopener" hidden>查看任务详情</a>
              </div>
            </div>
          </div>
```

- [ ] **Step 2：改 medias.js — 打开 modal 时进入配置态，点「开始翻译」再调 API**

编辑 `web/static/medias.js`。把 `edOpenDetailTranslateTaskModal()`（约 1593-1596 行）改为：

```javascript
  function edOpenDetailTranslateTaskModal(langOverride) {
    const mask = $('edDetailTranslateTaskMask');
    if (!mask) return;
    // 复位到配置态
    const config = $('edDetailTranslateTaskConfig');
    const result = $('edDetailTranslateTaskResult');
    if (config) config.hidden = false;
    if (result) result.hidden = true;
    // chip 复位为 sequential
    const group = $('edDetailTranslateModeGroup');
    if (group) {
      group.querySelectorAll('.oc-chip').forEach(ch => {
        const active = ch.dataset.mode === 'sequential';
        ch.classList.toggle('on', active);
        ch.setAttribute('aria-checked', active ? 'true' : 'false');
      });
    }
    // 记录本次 lang，供「开始翻译」按钮使用
    mask.dataset.lang = (langOverride || edState.activeLang || '').trim().toLowerCase();
    mask.hidden = false;
  }
```

把原来的 `edStartDetailTranslate(langOverride)` 拆成两部分：

```javascript
  // 原 edStartDetailTranslate：现在只负责"打开 modal 进入配置态"
  function edStartDetailTranslate(langOverride) {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const lang = (langOverride || edState.activeLang || '').trim().toLowerCase();
    if (!pid || !lang || lang === 'en') return;
    edOpenDetailTranslateTaskModal(lang);
  }

  // 新：点「开始翻译」后真正调 API，切结果态
  async function edSubmitDetailTranslate() {
    const pid = edState.productData && edState.productData.product && edState.productData.product.id;
    const mask = $('edDetailTranslateTaskMask');
    const lang = mask ? (mask.dataset.lang || '').trim().toLowerCase() : '';
    if (!pid || !lang || lang === 'en') return;

    const langName = (LANGUAGES.find(l => l.code === lang) || {}).name_zh || lang.toUpperCase();
    const group = $('edDetailTranslateModeGroup');
    const active = group ? group.querySelector('.oc-chip.on') : null;
    const mode = active ? active.dataset.mode : 'sequential';

    const config = $('edDetailTranslateTaskConfig');
    const result = $('edDetailTranslateTaskResult');
    const msg = $('edDetailTranslateTaskMsg');
    const meta = $('edDetailTranslateTaskMeta');
    const link = $('edDetailTranslateTaskLink');

    if (config) config.hidden = true;
    if (result) result.hidden = false;
    if (msg) msg.textContent = '正在创建翻译任务...';
    if (meta) meta.textContent = `${langName} · 商品详情图（${mode === 'parallel' ? '并行' : '串行'}）`;
    if (link) {
      link.hidden = true;
      link.removeAttribute('href');
      delete link.dataset.taskId;
    }

    try {
      const data = await fetchJSON(`/medias/api/products/${pid}/detail-images/translate-from-en`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lang, concurrency_mode: mode }),
      });
      if (msg) msg.textContent = '翻译任务已创建，可以留在当前页查看历史记录，也可以打开详情页跟踪进度。';
      if (meta) meta.textContent = `任务 ID：${data.task_id} · ${langName} · ${mode === 'parallel' ? '并行' : '串行'}`;
      if (link) {
        link.href = data.detail_url || `/image-translate/${data.task_id}`;
        link.dataset.taskId = data.task_id || '';
        link.hidden = false;
      }
      await edRefreshDetailImagesPanel(lang);
    } catch (err) {
      if (msg) msg.textContent = '创建翻译任务失败';
      if (meta) meta.textContent = err.message || String(err);
      if (link) {
        link.hidden = true;
        link.removeAttribute('href');
      }
    }
  }
```

在 medias.js 初始化 / 事件绑定区（搜一下 `edDetailTranslateTaskClose` 的 click 绑定位置），追加：

```javascript
  const edDetailTranslateStartBtn = $('edDetailTranslateStartBtn');
  if (edDetailTranslateStartBtn) {
    edDetailTranslateStartBtn.addEventListener('click', edSubmitDetailTranslate);
  }
  const edDetailTranslateCancelBtn = $('edDetailTranslateCancelBtn');
  if (edDetailTranslateCancelBtn) {
    edDetailTranslateCancelBtn.addEventListener('click', edCloseDetailTranslateTaskModal);
  }
  const edDetailTranslateModeGroup = $('edDetailTranslateModeGroup');
  if (edDetailTranslateModeGroup) {
    edDetailTranslateModeGroup.addEventListener('click', (ev) => {
      const chip = ev.target.closest('.oc-chip');
      if (!chip) return;
      edDetailTranslateModeGroup.querySelectorAll('.oc-chip').forEach(c => {
        const active = c === chip;
        c.classList.toggle('on', active);
        c.setAttribute('aria-checked', active ? 'true' : 'false');
      });
    });
  }
```

> 如果已有类似的事件绑定入口函数（例如 `edBindDetailEvents()`），把这 3 段放进去；否则放在初始化 IIFE 里。

- [ ] **Step 3：人工验证**

启动 dev server 后：
- 打开任一商品的详情图编辑 modal
- 切换到非英语语种，点「从英语版一键翻译」
- 看到 modal 弹出，默认「串行」chip 亮着
- 点「并行」，chip 切换
- 点「取消」或 × → modal 关闭，**不发任何网络请求**（在 DevTools Network 面板确认）
- 重开 modal，点「开始翻译」→ 请求发出，modal 切到结果态显示任务信息
- 任务详情页 state_json 里 `concurrency_mode=parallel`

- [ ] **Step 4：commit**

```bash
git add web/templates/_medias_edit_detail_modal.html web/static/medias.js
git commit -m "feat(medias): 一键翻译 modal 加串行/并行选择"
```

---

## Task 9：端到端 QA + push

- [ ] **Step 1：跑全量测试**

```bash
pytest -x
```

预期：全 PASS。若有失败，回溯对应 Task 修复。

- [ ] **Step 2：手测清单**

- [ ] 图片翻译菜单：选串行 → 创建 5 张任务 → 详情页能看到逐张依次变 running→done（只有一张同时 running）
- [ ] 图片翻译菜单：选并行 → 创建 15 张任务 → 详情页里能同时看到 10 张 running（第一批），第一批 done 完第二批才启动
- [ ] 素材编辑：开 modal、切并行、取消 → 关 modal 不发请求
- [ ] 素材编辑：开 modal、切并行、开始翻译 → 结果态显示任务 ID，`concurrency_mode=parallel` 写入 state_json
- [ ] 服务重启中：并行任务 resume 后仍按并行跑（`resume_inflight_tasks` 调 runner.start，runtime 读 state 的 mode）
- [ ] `/api/image-translate/<tid>/retry-unfinished` 对并行任务依旧按并行跑

- [ ] **Step 3：push 分支**

```bash
git push -u origin feature/image-translate-concurrency
```

- [ ] **Step 4：决定合并方式**

按全局 CLAUDE.md 的 "worktree 完成后：合并到 master → push → 部署 → 清理分支 + worktree"。

提交 PR 或 cherry-pick 到 master（跟项目习惯；看 `git log --oneline origin/master -20` 里 worktree 分支的入主流程），部署后：

```bash
# 在主 worktree
git worktree remove G:/Code/AutoVideoSrtLocal-image-translate-concurrency
git branch -d feature/image-translate-concurrency  # 或 -D 如果已远程合并
```

---

## 自检记录（Plan 作者填）

- ✅ Spec § 1（数据模型）→ Task 1 覆盖
- ✅ Spec § 2（Runtime）→ Task 4/5/6 覆盖（分步：重构、加并行、加锁）
- ✅ Spec § 3（API）→ Task 2/3 覆盖
- ✅ Spec § 4（UI）→ Task 7/8 覆盖
- ✅ 回滚：Task 9 手测包含 resume/retry 验证
- ✅ 无 TBD / TODO / "similar to N"；每步代码/命令都给了完整内容
- ✅ 函数签名 `create_image_translate(..., concurrency_mode="sequential")` 在 Task 1/2/3 引用一致
- ✅ 测试辅助 `_fake_task` / `_item` / `_setup_detail_translate` 复用现有 / 在首次出现处给出完整实现
