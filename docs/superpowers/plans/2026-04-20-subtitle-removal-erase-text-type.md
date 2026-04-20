# 字幕移除「擦除类型」进阶选项 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给字幕移除模块增加可选的「擦除类型」：默认仅擦除字幕（Subtitle），进阶模式擦除所有渲染文本（Text，含水印/标题等）。

**Architecture:** 三层改动——(1) Provider 适配层 `submit_task()` 接收 `erase_text_type` 并按需附加 `operation.task.erase.auto.type=Text` payload；(2) Runtime 从 task state 读字段透传；(3) 路由 + 前端 UI 新增 radio 选项和状态展示。字段仅存 `state_json`，无 DB schema 变更。

**Tech Stack:** Python 3.14 / Flask / requests / pytest / Jinja2 / 原生 JS（无 React）

**Spec:** [docs/superpowers/specs/2026-04-20-subtitle-removal-erase-text-type-design.md](../specs/2026-04-20-subtitle-removal-erase-text-type-design.md)

---

## File Structure

| 文件 | 操作 | 职责 |
| --- | --- | --- |
| `appcore/subtitle_removal_provider.py` | Modify | `submit_task()` 增加 `erase_text_type` 参数，按值构造 payload |
| `appcore/subtitle_removal_runtime.py` | Modify | `SubtitleRemovalRuntime._submit()` 从 task state 读 `erase_text_type` 并透传 |
| `web/routes/subtitle_removal.py` | Modify | `_submit_locked()` 校验并写入 state；`_subtitle_removal_state_payload()`、`list_tasks()` 返回字段 |
| `web/templates/subtitle_removal_detail.html` | Modify | 新增 radio group、状态面板第三格 |
| `web/templates/_subtitle_removal_styles.html` | Modify | 新增 `.sr-erase-type-group` / `.sr-erase-type-option` 样式 |
| `web/templates/_subtitle_removal_scripts.html` | Modify | 读取 radio value 带入提交 body；state 同步；重提解禁；文案展示 |
| `web/templates/subtitle_removal_list.html` | Modify | 表头新增一列、行内展示中文文案 |
| `tests/test_subtitle_removal_provider.py` | Modify | 追加 3 个测试（subtitle 无 operation、text 有 operation、非法值抛 ValueError）|
| `tests/test_subtitle_removal_runtime.py` | Modify | 追加 2 个测试（显式 text 透传、缺省 subtitle）|
| `tests/test_subtitle_removal_routes.py` | Modify | 追加 5 个测试（submit 接收、非法值 400、resubmit 覆盖、GET 返回、list 返回）|

---

## Task 1: Provider 层 — 默认 `subtitle` 时 payload 保持兼容

**Files:**
- Modify: `appcore/subtitle_removal_provider.py`
- Test: `tests/test_subtitle_removal_provider.py`

- [ ] **Step 1: 在 `tests/test_subtitle_removal_provider.py` 末尾追加失败测试**

```python
def test_subtitle_removal_provider_subtitle_mode_omits_operation(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-1"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.example/api")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "TOKEN")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_NOTIFY_URL", "")

    provider.submit_task(
        file_size_mb=1.0,
        duration_seconds=1.0,
        resolution="720x1280",
        video_name="demo",
        source_url="https://tos.example/s.mp4",
        erase_text_type="subtitle",
    )

    assert "operation" not in captured["json"], "subtitle 模式不应下发 operation 字段"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd G:/Code/AutoVideoSrt/.worktrees/sr-erase-type && python -m pytest tests/test_subtitle_removal_provider.py::test_subtitle_removal_provider_subtitle_mode_omits_operation -v`
Expected: FAIL with `TypeError: submit_task() got an unexpected keyword argument 'erase_text_type'`

- [ ] **Step 3: 在 `appcore/subtitle_removal_provider.py` 里给 `submit_task` 加参数**

把 `submit_task` 函数签名改成：

```python
def submit_task(
    *,
    file_size_mb: float,
    duration_seconds: float,
    resolution: str,
    video_name: str,
    source_url: str,
    cover_url: str = "",
    erase_text_type: str = "subtitle",
) -> str:
    if erase_text_type not in {"subtitle", "text"}:
        raise ValueError(
            f"erase_text_type must be 'subtitle' or 'text', got {erase_text_type!r}"
        )
    payload = {
        "biz": "aiRemoveSubtitleSubmitTask",
        "fileSize": round(file_size_mb, 2),
        "duration": round(duration_seconds, 2),
        "resolution": resolution,
        "videoName": video_name,
        "coverUrl": cover_url,
        "url": source_url,
        "notifyUrl": config.SUBTITLE_REMOVAL_NOTIFY_URL,
    }
    if erase_text_type == "text":
        payload["operation"] = {
            "type": "Task",
            "task": {
                "type": "Erase",
                "erase": {
                    "mode": "Auto",
                    "auto": {"type": "Text"},
                },
            },
        }
    data = _post(payload)
    payload_result = data.get("data")
    if isinstance(payload_result, dict) and payload_result.get("taskId"):
        return str(payload_result["taskId"])
    if isinstance(payload_result, list) and payload_result and isinstance(payload_result[0], dict) and payload_result[0].get("taskId"):
        return str(payload_result[0]["taskId"])
    if isinstance(payload_result, str) and payload_result.strip():
        return payload_result.strip()
    raise SubtitleRemovalProviderError("Provider submit response missing taskId")
```

注意：原函数体内的 `_post(...)` 调用改成对 `payload` 变量的 `_post(payload)` 调用；返回值解析逻辑保持不变（把原来的 `payload = data.get("data")` 局部变量重命名为 `payload_result` 避免与 request payload 重名）。

- [ ] **Step 4: 再跑测试确认通过**

Run: `python -m pytest tests/test_subtitle_removal_provider.py -v`
Expected: 全部 PASS（原 7 个 + 新 1 个 = 8 通过）

- [ ] **Step 5: Commit**

```bash
git add tests/test_subtitle_removal_provider.py appcore/subtitle_removal_provider.py
git commit -m "feat(subtitle_removal): provider 支持 erase_text_type 参数（subtitle 保留默认 payload）"
```

---

## Task 2: Provider 层 — `text` 模式下发 `operation` payload

**Files:**
- Test: `tests/test_subtitle_removal_provider.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_subtitle_removal_provider_text_mode_adds_operation(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0, "msg": "ok", "data": {"taskId": "provider-task-2"}}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return FakeResponse()

    monkeypatch.setattr(provider.requests, "post", fake_post)
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.example/api")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "TOKEN")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_NOTIFY_URL", "")

    provider.submit_task(
        file_size_mb=1.0,
        duration_seconds=1.0,
        resolution="720x1280",
        video_name="demo",
        source_url="https://tos.example/s.mp4",
        erase_text_type="text",
    )

    operation = captured["json"].get("operation")
    assert operation == {
        "type": "Task",
        "task": {
            "type": "Erase",
            "erase": {
                "mode": "Auto",
                "auto": {"type": "Text"},
            },
        },
    }
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_subtitle_removal_provider.py::test_subtitle_removal_provider_text_mode_adds_operation -v`
Expected: 在 Task 1 完成前会 FAIL；由于 Task 1 已经把 `text` 分支写进了 `submit_task`，这个测试实际应该一次通过。如果 PASS 直接跳到 Step 5 提交。

- [ ] **Step 3: 如 Step 2 FAIL，补实现**

回到 `appcore/subtitle_removal_provider.py`，确保 `if erase_text_type == "text":` 分支按 Task 1 Step 3 写入 `payload["operation"]`。

- [ ] **Step 4: 再跑一次确认通过**

Run: `python -m pytest tests/test_subtitle_removal_provider.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add tests/test_subtitle_removal_provider.py
git commit -m "test(subtitle_removal): 覆盖 provider text 模式 operation payload"
```

---

## Task 3: Provider 层 — 非法枚举抛 ValueError

**Files:**
- Test: `tests/test_subtitle_removal_provider.py`

- [ ] **Step 1: 追加测试**

```python
def test_subtitle_removal_provider_rejects_invalid_erase_text_type(monkeypatch):
    import appcore.subtitle_removal_provider as provider

    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_URL", "https://goodline.example/api")
    monkeypatch.setattr(provider.config, "SUBTITLE_REMOVAL_PROVIDER_TOKEN", "TOKEN")

    with pytest.raises(ValueError, match="erase_text_type"):
        provider.submit_task(
            file_size_mb=1.0,
            duration_seconds=1.0,
            resolution="720x1280",
            video_name="demo",
            source_url="https://tos.example/s.mp4",
            erase_text_type="bogus",
        )
```

- [ ] **Step 2: 运行**

Run: `python -m pytest tests/test_subtitle_removal_provider.py::test_subtitle_removal_provider_rejects_invalid_erase_text_type -v`
Expected: PASS（Task 1 Step 3 已实现校验；若 FAIL 则补实现）

- [ ] **Step 3: Commit**

```bash
git add tests/test_subtitle_removal_provider.py
git commit -m "test(subtitle_removal): 非法 erase_text_type 抛 ValueError"
```

---

## Task 4: Runtime — 从 task state 透传 `erase_text_type`

**Files:**
- Modify: `appcore/subtitle_removal_runtime.py`
- Test: `tests/test_subtitle_removal_runtime.py`

先读一下 `tests/test_subtitle_removal_runtime.py` 现有 `_submit`/`submit_task` 相关的 mock 风格，照着写新测试。

- [ ] **Step 1: 追加测试（沿用现有 runtime 测试的 mock 风格）**

现有 `tests/test_subtitle_removal_runtime.py` 的 pattern 是 `task_state.create_subtitle_removal(...)` + `task_state.update(...)` + `monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", ...)`。打开文件，在末尾追加：

```python
def test_runtime_submit_passes_erase_text_type_text(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_state.create_subtitle_removal(
        "sr-runtime-text",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-text",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime-text/source.mp4",
        erase_text_type="text",
    )

    captured = {}

    def fake_submit_task(**kwargs):
        captured.update(kwargs)
        return "provider-task-text"

    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", fake_submit_task)
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-runtime-text")

    assert captured.get("erase_text_type") == "text"


def test_runtime_submit_defaults_to_subtitle_when_field_missing(monkeypatch, tmp_path):
    from appcore.subtitle_removal_runtime import SubtitleRemovalRuntime

    task_state.create_subtitle_removal(
        "sr-runtime-default",
        str(tmp_path / "source.mp4"),
        str(tmp_path),
        original_filename="source.mp4",
        user_id=1,
    )
    task_state.update(
        "sr-runtime-default",
        status="queued",
        remove_mode="full",
        selection_box={"x1": 0, "y1": 0, "x2": 720, "y2": 1280},
        position_payload={"l": 0, "t": 0, "w": 720, "h": 1280},
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0, "file_size_mb": 2.09},
        source_tos_key="uploads/1/sr-runtime-default/source.mp4",
    )

    captured = {}

    def fake_submit_task(**kwargs):
        captured.update(kwargs)
        return "provider-task-default"

    monkeypatch.setattr("appcore.subtitle_removal_runtime.submit_task", fake_submit_task)
    monkeypatch.setattr(
        "appcore.subtitle_removal_runtime.tos_clients.generate_signed_download_url",
        lambda key, expires=None: "https://tos.example/source.mp4",
    )

    runner = SubtitleRemovalRuntime(bus=EventBus(), user_id=1)
    runner._submit("sr-runtime-default")

    assert captured.get("erase_text_type") == "subtitle"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_subtitle_removal_runtime.py -k "erase_text_type" -v`
Expected: FAIL，`captured.get("erase_text_type")` 为 None（因为 runtime 还没传）

- [ ] **Step 3: 修改 `appcore/subtitle_removal_runtime.py::_submit`**

找到 `_submit()` 里调用 `submit_task(...)` 的地方（约第 165 行），改为：

```python
erase_text_type = (task.get("erase_text_type") or "subtitle").strip().lower()
if erase_text_type not in {"subtitle", "text"}:
    erase_text_type = "subtitle"

try:
    provider_task_id = submit_task(
        file_size_mb=float(media_info.get("file_size_mb") or 0.0),
        duration_seconds=float(media_info.get("duration") or 0.0),
        resolution=media_info.get("resolution") or "",
        video_name=video_name,
        source_url=source_url,
        erase_text_type=erase_text_type,
    )
except Exception as exc:
    self._set_step(task_id, "submit", "error", f"提交失败: {exc}")
    raise
```

- [ ] **Step 4: 再跑测试确认通过**

Run: `python -m pytest tests/test_subtitle_removal_runtime.py -v`
Expected: 所有 runtime 测试 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_subtitle_removal_runtime.py appcore/subtitle_removal_runtime.py
git commit -m "feat(subtitle_removal): runtime 从 state 透传 erase_text_type 至 provider"
```

---

## Task 5: 路由 — `/submit` 接收并持久化 `erase_text_type`

**Files:**
- Modify: `web/routes/subtitle_removal.py`
- Test: `tests/test_subtitle_removal_routes.py`

现有 `tests/test_subtitle_removal_routes.py` 的 pattern：fixture 名叫 `authed_client_no_db`；通过 `store.create_subtitle_removal(...)` + `store.update(..., status="ready", media_info=...)` 建任务；用 `monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))` 绕过 404 分支；runner 用 `monkeypatch.setattr("web.routes.subtitle_removal.subtitle_removal_runner.start", lambda ...)` 拦截。

- [ ] **Step 1: 在 `tests/test_subtitle_removal_routes.py` 末尾追加失败测试**

```python
def test_subtitle_removal_submit_persists_erase_text_type_text(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-erase-text",
        "uploads/source.mp4",
        "output/sr-submit-erase-text",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-erase-text",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = {}
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.setdefault("task_id", task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-erase-text/submit",
        json={"remove_mode": "full", "erase_text_type": "text"},
    )

    assert response.status_code == 202
    assert started["task_id"] == "sr-submit-erase-text"
    saved = store.get("sr-submit-erase-text")
    assert saved["erase_text_type"] == "text"


def test_subtitle_removal_submit_defaults_erase_text_type_to_subtitle(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-erase-default",
        "uploads/source.mp4",
        "output/sr-submit-erase-default",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-erase-default",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: None,
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-erase-default/submit",
        json={"remove_mode": "full"},
    )

    assert response.status_code == 202
    saved = store.get("sr-submit-erase-default")
    assert saved["erase_text_type"] == "subtitle"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_subtitle_removal_routes.py -k "erase_text_type or defaults_to_subtitle" -v`
Expected: FAIL（state 里不存在该字段）

- [ ] **Step 3: 修改 `web/routes/subtitle_removal.py::_submit_locked`**

在 `_submit_locked()` 函数顶部解析 `mode` 之后、`store.update(...)` 之前，加入：

```python
erase_text_type = (body.get("erase_text_type") or "subtitle").strip().lower()
if erase_text_type not in {"subtitle", "text"}:
    return jsonify({"error": "erase_text_type must be subtitle or text"}), 400
```

然后在同一个 `store.update(task_id, ...)` 调用里追加一行字段：

```python
store.update(
    task_id,
    status="queued",
    remove_mode=mode,
    selection_box=normalized,
    position_payload=_to_position_payload(normalized),
    erase_text_type=erase_text_type,        # 新增
    provider_task_id="",
    ...
)
```

- [ ] **Step 4: 再跑测试**

Run: `python -m pytest tests/test_subtitle_removal_routes.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_subtitle_removal_routes.py web/routes/subtitle_removal.py
git commit -m "feat(subtitle_removal): submit 接收并持久化 erase_text_type"
```

---

## Task 6: 路由 — `/submit` 拒绝非法 `erase_text_type`

**Files:**
- Test: `tests/test_subtitle_removal_routes.py`

- [ ] **Step 1: 追加测试**

```python
def test_subtitle_removal_submit_rejects_invalid_erase_text_type(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-submit-erase-bogus",
        "uploads/source.mp4",
        "output/sr-submit-erase-bogus",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-submit-erase-bogus",
        status="ready",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    started = []
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: started.append(task_id),
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-submit-erase-bogus/submit",
        json={"remove_mode": "full", "erase_text_type": "bogus"},
    )

    assert response.status_code == 400
    assert "erase_text_type" in (response.get_json() or {}).get("error", "")
    assert started == []
```

- [ ] **Step 2: 运行**

Run: `python -m pytest tests/test_subtitle_removal_routes.py::test_subtitle_removal_submit_rejects_invalid_erase_text_type -v`
Expected: PASS（Task 5 已加校验；若 FAIL 补 `_submit_locked` 校验分支）

- [ ] **Step 3: Commit**

```bash
git add tests/test_subtitle_removal_routes.py
git commit -m "test(subtitle_removal): 覆盖非法 erase_text_type 400 分支"
```

---

## Task 7: 路由 — `/resubmit` 接收 `erase_text_type` 覆盖

**Files:**
- Test: `tests/test_subtitle_removal_routes.py`

- [ ] **Step 1: 追加测试**

```python
def test_subtitle_removal_resubmit_overrides_erase_text_type(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-resubmit-erase",
        "uploads/source.mp4",
        "output/sr-resubmit-erase",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-resubmit-erase",
        status="done",
        erase_text_type="subtitle",
        media_info={
            "width": 720,
            "height": 1280,
            "resolution": "720x1280",
            "duration": 10.0,
            "file_size_mb": 2.09,
        },
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_owned_task", lambda task_id: store.get(task_id))
    monkeypatch.setattr(
        "web.routes.subtitle_removal.subtitle_removal_runner.start",
        lambda task_id, user_id=None: None,
    )

    response = authed_client_no_db.post(
        "/api/subtitle-removal/sr-resubmit-erase/resubmit",
        json={"remove_mode": "full", "erase_text_type": "text"},
    )

    assert response.status_code == 202
    saved = store.get("sr-resubmit-erase")
    assert saved["erase_text_type"] == "text"
```

- [ ] **Step 2: 运行确认**

Run: `python -m pytest tests/test_subtitle_removal_routes.py::test_subtitle_removal_resubmit_overrides_erase_text_type -v`
Expected: PASS（`resubmit()` 路由已调用相同的 `_submit_locked`）

- [ ] **Step 3: Commit**

```bash
git add tests/test_subtitle_removal_routes.py
git commit -m "test(subtitle_removal): resubmit 允许覆盖 erase_text_type"
```

---

## Task 8: 路由 — `GET /<id>` 和 `GET /list` 返回 `erase_text_type`

**Files:**
- Modify: `web/routes/subtitle_removal.py`
- Test: `tests/test_subtitle_removal_routes.py`

- [ ] **Step 1: 追加失败测试**

现有 `test_state_api_returns_detail_payload`（第 361 行起）走的是 `_get_task` 的 happy-path，可参考其风格。追加：

```python
def test_subtitle_removal_state_api_returns_erase_text_type(authed_client_no_db, monkeypatch):
    store.create_subtitle_removal(
        "sr-state-erase",
        "uploads/source.mp4",
        "output/sr-state-erase",
        original_filename="source.mp4",
        user_id=1,
    )
    store.update(
        "sr-state-erase",
        status="ready",
        erase_text_type="text",
        media_info={"width": 720, "height": 1280, "resolution": "720x1280", "duration": 10.0},
    )
    monkeypatch.setattr("web.routes.subtitle_removal._get_task", lambda task_id: store.get(task_id))

    response = authed_client_no_db.get("/api/subtitle-removal/sr-state-erase")

    assert response.status_code == 200
    assert response.get_json().get("erase_text_type") == "text"
```

list 测试不方便直接 mock `db_query`（`list_tasks` 里直接调用 `db_query` 返回 projects 表数据）。改用单元化一点的路径：直接断言 `_subtitle_removal_state_payload()` 返回 erase_text_type——这已经被上面的 state_api 测试覆盖了。对 list 接口额外加一个基于 `monkeypatch` 替换 `db_query` 的测试：

```python
def test_subtitle_removal_list_returns_erase_text_type(authed_client_no_db, monkeypatch):
    import json as _json
    monkeypatch.setattr(
        "web.routes.subtitle_removal.db_query",
        lambda sql, args=None: [
            {
                "id": "sr-list-erase",
                "user_id": 1,
                "status": "done",
                "state_json": _json.dumps({
                    "display_name": "demo",
                    "original_filename": "demo.mp4",
                    "status": "done",
                    "erase_text_type": "text",
                    "media_info": {"resolution": "720x1280", "duration": 10.0},
                    "thumbnail_path": "",
                    "provider_status": "success",
                    "provider_result_url": "",
                }),
                "created_at": None,
                "username": "tester",
            }
        ],
    )

    response = authed_client_no_db.get("/api/subtitle-removal/list")

    assert response.status_code == 200
    items = (response.get_json() or {}).get("items") or []
    assert items, "list 接口应返回至少一条"
    assert items[0]["erase_text_type"] == "text"
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_subtitle_removal_routes.py -k "erase_text_type and (returns or list)" -v`
Expected: FAIL（两个接口都没返回该字段）

- [ ] **Step 3: 修改 `web/routes/subtitle_removal.py`**

(a) `_subtitle_removal_state_payload()` 函数里（约第 304 行起），在 payload 字典里追加一行：

```python
"erase_text_type": task.get("erase_text_type") or "subtitle",
```

放在 `"remove_mode": ...` 附近即可。

(b) `list_tasks()` 里（约第 382 行），在每条 `items.append({...})` 的 dict 里追加：

```python
"erase_text_type": state.get("erase_text_type") or "",
```

（未提交的任务留空字符串，前端渲染为 `—`）

- [ ] **Step 4: 再跑测试**

Run: `python -m pytest tests/test_subtitle_removal_routes.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_subtitle_removal_routes.py web/routes/subtitle_removal.py
git commit -m "feat(subtitle_removal): state 和 list 接口返回 erase_text_type"
```

---

## Task 9: 详情页模板 — radio 组 + 状态面板第三格

**Files:**
- Modify: `web/templates/subtitle_removal_detail.html`

前端改动无直接 pytest 覆盖；手动冒烟在 Task 13 统一做。

- [ ] **Step 1: 修改详情页**

打开 `web/templates/subtitle_removal_detail.html`，在控制面板 `<section class="sr-card sr-control-card">` 的第一行 `<div class="sr-action-row">` **之前**插入 radio 组：

```html
<div class="sr-erase-type-group" role="radiogroup" aria-label="擦除类型">
  <div class="sr-erase-type-label">擦除类型</div>
  <div class="sr-erase-type-options">
    <label class="sr-erase-type-option" data-role="erase-type-option">
      <input type="radio" name="erase_text_type" value="subtitle" checked>
      <div class="sr-erase-type-title">仅字幕</div>
      <div class="sr-erase-type-hint">只擦除识别为字幕的区域</div>
    </label>
    <label class="sr-erase-type-option" data-role="erase-type-option">
      <input type="radio" name="erase_text_type" value="text">
      <div class="sr-erase-type-title">所有渲染文本</div>
      <div class="sr-erase-type-hint">字幕 + 水印、标题等都会被擦除</div>
    </label>
  </div>
</div>
```

然后，在状态面板里现有的两个 `.sr-status-item` 之后加第三个：

```html
<div class="sr-status-item">
  <span class="sr-status-label">擦除类型</span>
  <strong id="srStateEraseType">—</strong>
</div>
```

- [ ] **Step 2: Commit（模板先落地，样式/脚本随后补齐）**

```bash
git add web/templates/subtitle_removal_detail.html
git commit -m "feat(subtitle_removal): 详情页加 erase_text_type radio 与状态格"
```

---

## Task 10: 详情页样式 — radio 卡片

**Files:**
- Modify: `web/templates/_subtitle_removal_styles.html`

- [ ] **Step 1: 追加样式**

在 `_subtitle_removal_styles.html` 已有 `<style>` 块末尾（`</style>` 前）追加：

```css
.sr-erase-type-group {
  margin-bottom: var(--space-4);
}
.sr-erase-type-label {
  font-size: var(--text-sm);
  color: var(--fg-muted);
  margin-bottom: var(--space-2);
}
.sr-erase-type-options {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
.sr-erase-type-option {
  display: block;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  background: #fff;
  transition: border-color var(--duration-fast), background-color var(--duration-fast);
}
.sr-erase-type-option input[type="radio"] {
  margin-right: var(--space-2);
  accent-color: var(--accent);
}
.sr-erase-type-option.is-active,
.sr-erase-type-option:has(input[type="radio"]:checked) {
  border-color: var(--accent);
  background: var(--accent-subtle);
}
.sr-erase-type-option.is-disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.sr-erase-type-title {
  font-size: var(--text-base);
  color: var(--fg);
  margin-top: var(--space-1);
}
.sr-erase-type-hint {
  font-size: var(--text-xs);
  color: var(--fg-subtle);
  margin-top: var(--space-1);
  line-height: var(--leading);
}
@media (max-width: 640px) {
  .sr-erase-type-options {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add web/templates/_subtitle_removal_styles.html
git commit -m "feat(subtitle_removal): erase_text_type radio 卡片样式"
```

---

## Task 11: 详情页脚本 — radio 同步、提交携带、重提解禁

**Files:**
- Modify: `web/templates/_subtitle_removal_scripts.html`

- [ ] **Step 1: 定位脚本内 IIFE，做 4 处改动**

打开 `web/templates/_subtitle_removal_scripts.html`，在 detail page 分支内（`if (!page) return;` 之后）做以下改动：

**(a) 声明新 DOM 引用**（紧跟现有 `var modeInputs = ...; var radioTiles = ...;` 后）：

```javascript
var eraseTypeInputs = Array.prototype.slice.call(document.querySelectorAll("input[name='erase_text_type']"));
var eraseTypeOptions = Array.prototype.slice.call(document.querySelectorAll("[data-role='erase-type-option']"));
var stateEraseType = document.getElementById("srStateEraseType");
```

**(b) 新增工具函数**（放在 `syncMode` 之后即可）：

```javascript
function eraseTypeLabel(value) {
  if (value === "text") return "所有渲染文本";
  if (value === "subtitle") return "仅字幕";
  return "—";
}

function currentEraseType() {
  for (var i = 0; i < eraseTypeInputs.length; i++) {
    if (eraseTypeInputs[i].checked) return eraseTypeInputs[i].value;
  }
  return "subtitle";
}

function syncEraseType(nextValue) {
  eraseTypeInputs.forEach(function (input) {
    input.checked = input.value === nextValue;
  });
  eraseTypeOptions.forEach(function (opt) {
    var input = opt.querySelector("input[name='erase_text_type']");
    opt.classList.toggle("is-active", !!input && input.checked);
  });
  if (stateEraseType) {
    stateEraseType.textContent = eraseTypeLabel(nextValue);
  }
}

function setEraseTypeDisabled(disabled) {
  eraseTypeInputs.forEach(function (input) { input.disabled = !!disabled; });
  eraseTypeOptions.forEach(function (opt) { opt.classList.toggle("is-disabled", !!disabled); });
}
```

**(c) 在 `getActionPayload()` 里把 `erase_text_type` 带上**：

```javascript
function getActionPayload() {
  var payload = {
    remove_mode: selectionState.mode,
    erase_text_type: currentEraseType(),
  };
  if (selectionState.mode === "box") {
    var box = getCurrentSelectionPayload();
    if (box) {
      payload.selection_box = box;
    }
  }
  return payload;
}
```

**(d) 在 `renderSubtitleRemovalState(state)` 里同步 radio 和禁用态**，在函数末尾 `renderResultPanel(state);` **之前** 追加：

```javascript
var nextEraseType = (state && state.erase_text_type) || "subtitle";
syncEraseType(nextEraseType);
var status = (state && state.status) || "";
var lockStates = { queued: 1, running: 1, submitted: 1, done: 1, error: 1 };
setEraseTypeDisabled(!!lockStates[status]);
```

**(e) radio 点击监听**（放在已有 `modeInputs.forEach(...)` 附近）：

```javascript
eraseTypeInputs.forEach(function (input) {
  input.addEventListener("change", function () {
    syncEraseType(input.value);
  });
});
```

**(f) 「重提」按钮点击处理里解禁 radio**，找到 `if (resubmitButton) { resubmitButton.addEventListener("click", function () { ... }); }`，在 `postJson(...)` 调用之前插一行：

```javascript
setEraseTypeDisabled(false);
```

完整代码片段参考（改动后）：

```javascript
if (resubmitButton) {
  resubmitButton.addEventListener("click", function () {
    setEraseTypeDisabled(false);
    var url = (bootstrap.resubmit_url || (taskId ? "/api/subtitle-removal/" + encodeURIComponent(taskId) + "/resubmit" : ""));
    postJson(url, getActionPayload())
      .then(function () { return refreshSubtitleRemovalState(); })
      .catch(function (error) { window.alert(error.message || "重提失败"); });
  });
}
```

- [ ] **Step 2: 启动 Flask 或手动 render 确认 JS 无语法错误**

快速 syntax 检查（Node 可用的话）：

```bash
node -c web/templates/_subtitle_removal_scripts.html 2>&1 | head -5
```

（模板含 `<script>` 标签与 Jinja 无关字段，`node -c` 可能因外层 HTML 标签报错——此时降级只检查是否能被浏览器加载，到 Task 13 一起验证）

- [ ] **Step 3: Commit**

```bash
git add web/templates/_subtitle_removal_scripts.html
git commit -m "feat(subtitle_removal): 详情页脚本支持 erase_text_type 同步/提交/重提"
```

---

## Task 12: 列表页 — 新增「擦除类型」列

**Files:**
- Modify: `web/templates/subtitle_removal_list.html`

- [ ] **Step 1: 改表头 + 数据行 + 网格列宽**

(a) `.sr-list-row` 的 `grid-template-columns` 现在是 `96px 1fr 140px 160px 180px 220px`（6 列），插入一列「擦除类型」，建议宽度 `140px`。改为：

```css
.sr-list-row { display: grid; grid-template-columns: 96px 1fr 140px 140px 160px 180px 220px; align-items: center; gap: 16px; padding: 12px 20px; border-bottom: 1px solid var(--border); }
```

(b) 表头 `<div class="sr-list-row is-header">` 里，在 `<div>状态</div>` 后插入：

```html
<div>擦除类型</div>
```

(c) JS 渲染函数 `render()` 里，在 `'<span class="sr-list-status ...">...</span>'` 那一列 `</div>` 之后、`'<div>' + escapeHtml(it.resolution || "-") + '</div>'` 那一列 `<div>` 之前，插入新列：

```javascript
'<div>' + escapeHtml(eraseTypeLabel(it.erase_text_type)) + '</div>' +
```

并在 `render()` 之前定义 helper：

```javascript
function eraseTypeLabel(value) {
  if (value === "text") return "所有渲染文本";
  if (value === "subtitle") return "仅字幕";
  return "—";
}
```

- [ ] **Step 2: Commit**

```bash
git add web/templates/subtitle_removal_list.html
git commit -m "feat(subtitle_removal): 列表页展示擦除类型列"
```

---

## Task 13: 端到端冒烟验证

**Files:**
- No code change — manual verification

- [ ] **Step 1: 跑全部相关测试**

```bash
cd G:/Code/AutoVideoSrt/.worktrees/sr-erase-type
python -m pytest tests/test_subtitle_removal_provider.py tests/test_subtitle_removal_runtime.py tests/test_subtitle_removal_routes.py -v
```

Expected: 全部 PASS（原有测试 + 新增约 10 个 = 全绿）

- [ ] **Step 2: 启动本地 Flask 服务冒烟**

假设项目用 `flask run` 或 `python -m web`，参考 README 或 `web/__init__.py`；若已有本地运行命令直接沿用。

- [ ] **Step 3: 浏览器手测 checklist**

- [ ] 上传一个测试视频 → 详情页展示 radio 组，默认选中「仅字幕」
- [ ] 切换到「所有渲染文本」→ 点提交 → 状态面板「擦除类型」显示「所有渲染文本」
- [ ] 等任务到 running/done 状态：radio 被禁用（灰色、点不动）
- [ ] 点「重提」→ radio 立即解禁、回填上次的值
- [ ] 回到列表页 → 对应行「擦除类型」列展示中文文案
- [ ] 直接打开老任务详情页（提交之前创建的，state 无该字段）→ 展示「仅字幕」fallback

- [ ] **Step 4: 检查 Provider payload（选做）**

如果能抓包或 Provider 服务端可查 log，提交一个 `text` 模式任务，确认 Provider 收到的 JSON 包含 `operation.task.erase.auto.type=="Text"`。若 Provider 返回 `code != 0` 提示 operation 字段不认识，回到 spec 调整字段大小写（切换成 PascalCase）再跑本测。

- [ ] **Step 5: Commit（无代码变更，不产生 commit）**

若 checklist 跑完全部通过，这一步跳过；若发现 bug，用新 task 修完再提交。

---

## 完成后

- [ ] 在 worktree 内 `git log feat/sr-erase-type --oneline` 复核提交顺序
- [ ] 使用 superpowers:finishing-a-development-branch 决定合入方式（PR 或直接 merge 回 master）
