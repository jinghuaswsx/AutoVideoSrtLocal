# Medias Translation Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把素材管理里的“翻译”升级为产品级一键翻译编排器与任务管理页，支持文案、详情图、视频封面、视频四类任务，视频子任务统一停在选声音步骤，且字幕配置在单个真实视频框里所见即所得。

**Architecture:** 继续复用 `bulk_translate` 作为父任务容器，但把它从“同步串行执行器”升级为“派发 + 观察 + 回填”的异步编排器；素材页负责任务创建，产品页负责任务管理。字幕配置抽成共享预览组件，素材创建弹窗和 `multi_translate` 详情页共用一套 270x480 真视频叠加预览逻辑。

**Tech Stack:** Flask, Jinja2, vanilla JavaScript, appcore task/runtime helpers, MySQL migrations, pytest, Playwright

---

## File Map

### Orchestration Core

- Create: `appcore/bulk_translate_projection.py`
  责任：把 `bulk_translate` 父任务 state 投影为“产品 -> 批次 -> 4 类任务 -> 单项任务”视图，供任务管理页直接消费。
- Create: `appcore/bulk_translate_backfill.py`
  责任：在子任务完成后把文案、详情图、视频封面、视频结果回填到素材表，并统一写自动翻译关联字段。
- Create: `appcore/bulk_translate_recovery.py`
  责任：启动时只把 `bulk_translate` 的运行中任务标记为 `interrupted`，绝不自动续跑。
- Modify: `appcore/bulk_translate_plan.py`
  责任：把旧的 `copy/detail/cover/video` plan 扩成 `copywriting/detail_images/video_covers/videos`，并补齐新状态字段与派发节流元数据。
- Modify: `appcore/bulk_translate_runtime.py`
  责任：父任务状态机、异步子任务派发、轮询观察、状态汇总、人工继续/重试。
- Modify: `appcore/bulk_translate_associations.py`
  责任：允许新表 `media_raw_source_translations` 复用 `source_ref_id / bulk_task_id / auto_translated / manually_edited_at` 标记逻辑。

### Medias Domain / Persistence

- Create: `appcore/subtitle_preview_payload.py`
  责任：统一挑选预览视频源、归一化字幕预览 payload，供素材页和多语种视频页共用。
- Create: `appcore/medias_translation_bootstrap.py`
  责任：为素材翻译创建弹窗组装 bootstrap 数据，包括原始视频、默认语种、默认内容类型、默认字幕配置、任务管理入口和预览 payload。
- Modify: `appcore/medias.py`
  责任：增加原始视频封面翻译结果的 DAO，提供 `upsert/list raw source translations` 能力。
- Create: `db/migrations/2026_04_22_medias_raw_source_translations.sql`
  责任：新增原始视频多语种封面结果表，保证“视频封面翻译”能独立于商品主图和最终视频成品持久化。

### Web Routes / Templates / Scripts

- Modify: `web/routes/medias.py`
  责任：新增创建弹窗 bootstrap API、产品任务页页面/API 路由，升级 `/medias/api/products/<pid>/translate` 提交契约。
- Modify: `web/routes/bulk_translate.py`
  责任：兼容新的父任务状态、细化单项 retry/resume 响应、支持任务管理页调用。
- Modify: `web/routes/multi_translate.py`
  责任：增加共享字幕预览 API，并让“去选声音”入口能稳定落到多语种任务详情页。
- Modify: `web/app.py`
  责任：在已有 `recover_all_interrupted_tasks()` 之后调用 `mark_interrupted_bulk_translate_tasks()`。
- Modify: `web/templates/medias_list.html`
  责任：增加“翻译任务管理”入口、重建翻译创建弹窗结构、挂载共享字幕预览组件。
- Modify: `web/static/medias.js`
  责任：从旧的“原始视频 + 语言”简版弹窗升级为 4 类内容 + 多语种 + 共享预览 + 管理入口的完整创建流。
- Create: `web/templates/medias_translation_tasks.html`
  责任：产品级翻译任务管理页。
- Create: `web/static/medias_translation_tasks.js`
  责任：任务管理页的数据拉取、状态渲染、失败/中断/去选声音动作按钮。
- Create: `web/templates/_subtitle_preview_panel.html`
  责任：共享 270x480 真实视频字幕叠加预览块。
- Create: `web/static/subtitle_preview.js`
  责任：驱动共享字幕预览块的拖拽、字体、字号、位置同步。
- Modify: `web/templates/multi_translate_detail.html`
  责任：挂载共享字幕预览块，替换旧的独立预览入口。
- Modify: `web/templates/_task_workbench.html`
  责任：移除老的手机位置选择器内联结构，换成共享预览 mount。
- Modify: `web/templates/_task_workbench_scripts.html`
  责任：把旧的 `subtitle_font / subtitle_size / subtitle_position_y` 内联逻辑切到共享 `subtitle_preview.js` 控制器。

### Tests

- Modify: `tests/test_medias_raw_sources_translate.py`
  责任：覆盖素材翻译创建接口的新请求体与返回字段。
- Create: `tests/test_medias_translation_tasks_routes.py`
  责任：覆盖产品任务页、任务管理 API、bootstrap API。
- Create: `tests/test_medias_translation_assets.py`
  责任：直接校验模板和前端脚本中存在新入口、新弹窗结构和共享预览挂载点。
- Create: `tests/test_subtitle_preview_payload.py`
  责任：覆盖预览视频选择优先级和 payload 结构。
- Modify: `tests/test_bulk_translate_plan.py`
  责任：覆盖 4 类新 plan 项、视频封面批次和派发节流字段。
- Modify: `tests/test_bulk_translate_runtime.py`
  责任：覆盖新的父/子状态机、节流派发、`awaiting_voice`、结果同步与继续执行。
- Create: `tests/test_bulk_translate_backfill.py`
  责任：覆盖回填文案、详情图、视频封面、视频时的表写入和关联字段。
- Create: `tests/test_bulk_translate_projection.py`
  责任：覆盖任务管理页的产品级聚合、按钮动作映射、链接生成。
- Create: `tests/test_bulk_translate_recovery.py`
  责任：覆盖启动时 bulk_translate 中断标记逻辑。
- Create: `tests/test_db_migration_medias_raw_source_translations.py`
  责任：校验新迁移文件包含新表和关联字段。
- Modify: `tests/test_multi_translate_routes.py`
  责任：覆盖共享字幕预览 API 和“去选声音”落地页需要的数据字段。
- Create: `tests/e2e/test_medias_translation_orchestration_flow.py`
  责任：覆盖素材列表页两个入口、创建弹窗默认值、共享预览显示、任务管理跳转。

---

### Task 1: 建立产品级任务页路由和投影视图骨架

**Files:**
- Create: `appcore/bulk_translate_projection.py`
- Create: `tests/test_medias_translation_tasks_routes.py`
- Create: `web/templates/medias_translation_tasks.html`
- Modify: `web/routes/medias.py`

- [ ] **Step 1: 先写失败的路由测试，锁定产品任务页和产品任务 API**

在 `tests/test_medias_translation_tasks_routes.py` 里先写两个最小失败用例：

```python
import json


def _stub_product(monkeypatch):
    from web.routes import medias as r

    monkeypatch.setattr(
        r.medias,
        "get_product",
        lambda pid: {"id": pid, "name": "smart-ball", "user_id": 1},
    )
    monkeypatch.setattr(r, "_can_access_product", lambda product: product is not None)
    return r


def test_product_translation_tasks_page_renders(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)

    resp = authed_client_no_db.get("/medias/products/123/translation-tasks")

    assert resp.status_code == 200
    assert "翻译任务管理".encode("utf-8") in resp.data
    assert b"translationTasksApp" in resp.data


def test_product_translation_tasks_api_returns_projection(authed_client_no_db, monkeypatch):
    _stub_product(monkeypatch)
    monkeypatch.setattr(
        "web.routes.medias.build_product_task_payload",
        lambda user_id, product_id: {
            "product": {"id": product_id, "name": "smart-ball"},
            "batches": [{
                "task_id": "bt-1",
                "status": "running",
                "created_at": "2026-04-22T12:00:00",
                "groups": {
                    "videos": [{
                        "idx": 3,
                        "label": "原视频 #1001 · 德语",
                        "status": "awaiting_voice",
                        "action": {"label": "去选声音", "href": "/multi-translate/child-1"},
                    }],
                },
            }],
        },
    )

    resp = authed_client_no_db.get("/medias/api/products/123/translation-tasks")

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["product"]["id"] == 123
    assert payload["batches"][0]["task_id"] == "bt-1"
    assert payload["batches"][0]["groups"]["videos"][0]["action"]["label"] == "去选声音"
```

- [ ] **Step 2: 跑路由测试，确认现在先以 404 / import error 失败**

Run:

```bash
pytest tests/test_medias_translation_tasks_routes.py -q
```

Expected:

```text
FAILED tests/test_medias_translation_tasks_routes.py::test_product_translation_tasks_page_renders
FAILED tests/test_medias_translation_tasks_routes.py::test_product_translation_tasks_api_returns_projection
```

- [ ] **Step 3: 创建 `appcore/bulk_translate_projection.py`，先把产品级 payload 骨架搭出来**

写出最小可用读模型，先只负责把 `projects.type='bulk_translate'` 拉出来并序列化基本字段：

```python
from __future__ import annotations

import json

from appcore import medias
from appcore.db import query


def build_product_task_payload(user_id: int, product_id: int) -> dict:
    product = medias.get_product(product_id)
    if not product:
        raise ValueError(f"product {product_id} not found")

    rows = query(
        "SELECT id, status, state_json, created_at "
        "FROM projects "
        "WHERE user_id = %s AND type = 'bulk_translate' AND deleted_at IS NULL "
        "  AND JSON_EXTRACT(state_json, '$.product_id') = %s "
        "ORDER BY created_at DESC LIMIT 50",
        (user_id, product_id),
    )

    return {
        "product": {
            "id": product["id"],
            "name": product.get("name") or "",
            "product_code": product.get("product_code") or "",
        },
        "batches": [_serialize_batch(row) for row in rows],
    }


def _serialize_batch(row: dict) -> dict:
    raw_state = row.get("state_json") or "{}"
    state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state)
    return {
        "task_id": row["id"],
        "status": row.get("status") or "",
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "target_langs": list(state.get("target_langs") or []),
        "content_types": list(state.get("content_types") or []),
        "rollup": dict(state.get("rollup") or {}),
        "groups": dict(state.get("groups") or {}),
    }
```

- [ ] **Step 4: 在 `web/routes/medias.py` 里增加页面/API 路由，并新建占位模板**

把页面和 JSON API 都先打通，模板先用最小壳子：

```python
from appcore.bulk_translate_projection import build_product_task_payload


@bp.route("/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def translation_tasks_page(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)
    return render_template(
        "medias_translation_tasks.html",
        product=product,
        product_id=pid,
    )


@bp.route("/api/products/<int:pid>/translation-tasks", methods=["GET"])
@login_required
def api_product_translation_tasks(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)
    payload = build_product_task_payload(current_user.id, pid)
    return jsonify(payload)
```

模板先放成：

```html
{% extends "layout.html" %}
{% block title %}翻译任务管理{% endblock %}
{% block content %}
<section class="oc" id="translationTasksApp" data-product-id="{{ product_id }}">
  <header class="oc-header">
    <div>
      <h1 class="title">翻译任务管理</h1>
      <div class="subtitle">{{ product.name }}</div>
    </div>
  </header>
</section>
{% endblock %}
```

- [ ] **Step 5: 重新跑路由测试，确认页面和 API 已打通**

Run:

```bash
pytest tests/test_medias_translation_tasks_routes.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: 提交产品任务页骨架**

Run:

```bash
git add appcore/bulk_translate_projection.py tests/test_medias_translation_tasks_routes.py web/routes/medias.py web/templates/medias_translation_tasks.html
git commit -m "feat: add product translation task routes"
```

### Task 2: 抽离共享字幕预览 payload 和前端组件

**Files:**
- Create: `appcore/subtitle_preview_payload.py`
- Create: `tests/test_subtitle_preview_payload.py`
- Create: `web/templates/_subtitle_preview_panel.html`
- Create: `web/static/subtitle_preview.js`
- Modify: `web/routes/multi_translate.py`
- Modify: `web/templates/multi_translate_detail.html`
- Modify: `web/templates/_task_workbench.html`
- Modify: `web/templates/_task_workbench_scripts.html`
- Modify: `tests/test_multi_translate_routes.py`

- [ ] **Step 1: 先写失败的 payload / route 测试，锁定预览视频优先级和共享 API**

新增 `tests/test_subtitle_preview_payload.py`：

```python
from appcore.subtitle_preview_payload import build_product_preview_payload


def test_product_preview_prefers_first_english_video_with_source_raw():
    payload = build_product_preview_payload(
        product_id=123,
        items=[
            {"id": 9, "lang": "en", "object_key": "1/medias/123/en-final.mp4", "source_raw_id": 88},
        ],
        raw_sources=[
            {"id": 88, "video_url": "/medias/raw-sources/88/video"},
            {"id": 89, "video_url": "/medias/raw-sources/89/video"},
        ],
        video_params={"subtitle_font": "Impact", "subtitle_size": 14, "subtitle_position_y": 0.88},
    )

    assert payload["video_url"] == "/medias/object?object_key=1%2Fmedias%2F123%2Fen-final.mp4"
    assert payload["subtitle_font"] == "Impact"
    assert payload["subtitle_position_y"] == 0.88
    assert payload["sample_lines"] == [
        "Tiktok and facebook shot videos!",
        "Tiktok and facebook shot videos!",
    ]


def test_product_preview_falls_back_to_first_english_raw_source():
    payload = build_product_preview_payload(
        product_id=123,
        items=[],
        raw_sources=[
            {"id": 88, "video_url": "/medias/raw-sources/88/video"},
            {"id": 89, "video_url": "/medias/raw-sources/89/video"},
        ],
        video_params={"subtitle_font": "Anton", "subtitle_size": 18, "subtitle_position_y": 0.72},
    )

    assert payload["video_url"] == "/medias/raw-sources/88/video"
    assert payload["subtitle_size"] == 18
```

在 `tests/test_multi_translate_routes.py` 里追加：

```python
def test_multi_translate_subtitle_preview_route(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.multi_translate.build_multi_translate_preview_payload",
        lambda task_id, user_id: {
            "video_url": "/media/demo.mp4",
            "subtitle_font": "Impact",
            "subtitle_size": 14,
            "subtitle_position_y": 0.68,
            "sample_lines": ["Tiktok and facebook shot videos!", "Tiktok and facebook shot videos!"],
        },
    )
    monkeypatch.setattr(
        "web.routes.multi_translate.db_query_one",
        lambda sql, args: {"state_json": "{}", "id": args[0]},
    )

    resp = authed_client_no_db.get("/api/multi-translate/task-1/subtitle-preview")

    assert resp.status_code == 200
    assert resp.get_json()["video_url"] == "/media/demo.mp4"
```

- [ ] **Step 2: 跑测试，确认因为模块/路由不存在而失败**

Run:

```bash
pytest tests/test_subtitle_preview_payload.py tests/test_multi_translate_routes.py -q
```

Expected:

```text
FAILED tests/test_subtitle_preview_payload.py::test_product_preview_prefers_first_english_video_with_source_raw
FAILED tests/test_multi_translate_routes.py::test_multi_translate_subtitle_preview_route
```

- [ ] **Step 3: 创建 `appcore/subtitle_preview_payload.py`，统一输出共享预览结构**

实现最小 helper：

```python
from __future__ import annotations

from urllib.parse import quote


SAMPLE_LINES = [
    "Tiktok and facebook shot videos!",
    "Tiktok and facebook shot videos!",
]


def build_product_preview_payload(
    *,
    product_id: int,
    items: list[dict],
    raw_sources: list[dict],
    video_params: dict,
) -> dict:
    video_url = _pick_product_video_url(items, raw_sources)
    return _build_payload(video_url, video_params)


def build_multi_translate_preview_payload(task_id: str, user_id: int) -> dict:
    from web import store

    task = store.get(task_id) or {}
    video_url = (
        ((task.get("preview_files") or {}).get("source_video") or "").strip()
        or (task.get("video_url") or "").strip()
        or ""
    )
    return _build_payload(video_url, task)


def _pick_product_video_url(items: list[dict], raw_sources: list[dict]) -> str:
    for item in items:
        if (item.get("lang") or "") == "en" and item.get("source_raw_id"):
            object_key = (item.get("object_key") or "").strip()
            if object_key:
                return "/medias/object?object_key=" + quote(object_key, safe="")
    for row in raw_sources:
        video_url = (row.get("video_url") or "").strip()
        if video_url:
            return video_url
    return ""


def _build_payload(video_url: str, source: dict) -> dict:
    return {
        "video_url": video_url,
        "subtitle_font": (source.get("subtitle_font") or "Impact").strip(),
        "subtitle_size": int(source.get("subtitle_size") or 14),
        "subtitle_position_y": float(source.get("subtitle_position_y") or 0.68),
        "sample_lines": list(SAMPLE_LINES),
    }
```

- [ ] **Step 4: 创建共享 partial 和 `subtitle_preview.js` 控制器**

先把结构和 JS API 固定住，后续素材页和多语种页都只挂载这个组件：

```html
<template id="subtitlePreviewTemplate">
  <section class="oc-subtitle-preview" data-subtitle-preview>
    <div class="oc-subtitle-preview__frame">
      <video class="oc-subtitle-preview__video" data-role="video" playsinline muted controls></video>
      <div class="oc-subtitle-preview__overlay" data-role="overlay">
        <div class="oc-subtitle-preview__line" data-role="line-a"></div>
        <div class="oc-subtitle-preview__line" data-role="line-b"></div>
      </div>
    </div>
    <div class="oc-subtitle-preview__controls">
      <div id="subtitleFontGrid"></div>
      <div id="subtitleSizeGroup"></div>
      <input type="hidden" data-role="subtitle-position-y" value="0.68">
    </div>
  </section>
</template>
```

```js
window.createSubtitlePreviewController = function createSubtitlePreviewController(root, initialPayload) {
  const video = root.querySelector('[data-role="video"]');
  const overlay = root.querySelector('[data-role="overlay"]');
  const lineA = root.querySelector('[data-role="line-a"]');
  const lineB = root.querySelector('[data-role="line-b"]');
  const positionInput = root.querySelector('[data-role="subtitle-position-y"]');

  let state = {
    video_url: '',
    subtitle_font: 'Impact',
    subtitle_size: 14,
    subtitle_position_y: 0.68,
    sample_lines: [],
  };

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function render() {
    video.src = state.video_url || '';
    overlay.style.top = (clamp(state.subtitle_position_y, 0.05, 0.95) * 100).toFixed(1) + '%';
    overlay.style.fontFamily = state.subtitle_font;
    overlay.style.fontSize = String(state.subtitle_size) + 'px';
    lineA.textContent = state.sample_lines[0] || '';
    lineB.textContent = state.sample_lines[1] || '';
    positionInput.value = String(state.subtitle_position_y);
  }

  function setPayload(nextPayload) {
    state = { ...state, ...(nextPayload || {}) };
    render();
  }

  function getValue() {
    return {
      subtitle_font: state.subtitle_font,
      subtitle_size: state.subtitle_size,
      subtitle_position_y: state.subtitle_position_y,
    };
  }

  overlay.addEventListener('pointerdown', (event) => {
    const frame = root.querySelector('.oc-subtitle-preview__frame');
    const rect = frame.getBoundingClientRect();

    function onMove(moveEvent) {
      const pct = clamp((moveEvent.clientY - rect.top) / rect.height, 0.05, 0.95);
      state.subtitle_position_y = Number(pct.toFixed(4));
      render();
    }

    function onUp() {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    }

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    event.preventDefault();
  });

  setPayload(initialPayload);
  return { setPayload, getValue };
};
```

- [ ] **Step 5: 给 `multi_translate` 增加共享预览 API，并在详情页挂载共享组件**

`web/routes/multi_translate.py` 新增：

```python
from appcore.subtitle_preview_payload import build_multi_translate_preview_payload


@bp.route("/api/multi-translate/<task_id>/subtitle-preview", methods=["GET"])
@login_required
def subtitle_preview(task_id: str):
    row = db_query_one(
        "SELECT id FROM projects WHERE id = %s AND user_id = %s AND deleted_at IS NULL",
        (task_id, current_user.id),
    )
    if not row:
        return jsonify({"error": "Task not found"}), 404
    payload = build_multi_translate_preview_payload(task_id, current_user.id)
    return jsonify(payload)
```

`web/templates/multi_translate_detail.html` 加：

```html
{% include "_subtitle_preview_panel.html" %}
<script src="{{ url_for('static', filename='subtitle_preview.js') }}"></script>
```

`web/templates/_task_workbench.html` 把旧的手机位置弹窗入口替换成挂载点：

```html
<div class="config-item config-item--subtitle">
  <label>字幕预览</label>
  <div id="sharedSubtitlePreviewMount"></div>
</div>
```

`web/templates/_task_workbench_scripts.html` 里先删掉 `openPhonePickerBtn / phoneFrame / pfSubtitleBar` 那组内联事件，换成：

```html
<script>
  let _subtitlePreviewController = null;

  async function bootSharedSubtitlePreview() {
    const mount = document.getElementById("sharedSubtitlePreviewMount");
    if (!mount) return;
    const resp = await fetch(_apiUrl("/subtitle-preview"));
    const payload = await resp.json();
    mount.innerHTML = document.getElementById("subtitlePreviewTemplate").innerHTML;
    _subtitlePreviewController = createSubtitlePreviewController(mount.firstElementChild, payload);
  }
</script>
```

- [ ] **Step 6: 跑共享预览相关测试，确认 helper 和 route 都通过**

Run:

```bash
pytest tests/test_subtitle_preview_payload.py tests/test_multi_translate_routes.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 7: 提交共享字幕预览组件**

Run:

```bash
git add appcore/subtitle_preview_payload.py tests/test_subtitle_preview_payload.py web/templates/_subtitle_preview_panel.html web/static/subtitle_preview.js web/routes/multi_translate.py web/templates/multi_translate_detail.html web/templates/_task_workbench.html web/templates/_task_workbench_scripts.html tests/test_multi_translate_routes.py
git commit -m "feat: add shared subtitle preview component"
```

### Task 3: 升级素材页翻译创建弹窗和 bootstrap 接口

**Files:**
- Create: `appcore/medias_translation_bootstrap.py`
- Modify: `web/routes/medias.py`
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`
- Modify: `tests/test_medias_raw_sources_translate.py`
- Create: `tests/test_medias_translation_assets.py`
- Modify: `tests/test_medias_translation_tasks_routes.py`
- Create: `tests/e2e/test_medias_translation_orchestration_flow.py`

- [ ] **Step 1: 先写失败测试，锁定 bootstrap 返回结构和创建请求体**

先扩充 `tests/test_medias_raw_sources_translate.py`：

```python
def test_translate_ok_with_full_payload(authed_client_no_db, pid, monkeypatch, patch_bt):
    _stub_product(monkeypatch, pid, raw_sources=[{"id": 88}], valid_langs={"de", "fr"})
    fake_create, fake_start = patch_bt

    resp = authed_client_no_db.post(
        f"/medias/api/products/{pid}/translate",
        json={
            "raw_ids": [88],
            "target_langs": ["de", "fr"],
            "content_types": ["copywriting", "detail_images", "video_covers", "videos"],
            "video_params": {
                "subtitle_font": "Impact",
                "subtitle_size": 18,
                "subtitle_position_y": 0.72,
            },
        },
    )

    assert resp.status_code == 202
    payload = resp.get_json()
    assert payload["task_id"] == "task-xyz"
    assert payload["manage_url"] == f"/medias/products/{pid}/translation-tasks"
    _args, kwargs = fake_create.call_args
    assert kwargs["content_types"] == ["copywriting", "detail_images", "video_covers", "videos"]
    assert kwargs["video_params"]["subtitle_position_y"] == 0.72
    fake_start.assert_called_once_with("task-xyz", 1)
```

再给 `tests/test_medias_translation_tasks_routes.py` 追加 bootstrap 用例：

```python
def test_translation_bootstrap_defaults_missing_video_langs(authed_client_no_db, monkeypatch):
    monkeypatch.setattr(
        "web.routes.medias.build_translation_bootstrap_payload",
        lambda user_id, product_id: {
            "raw_sources": [{"id": 88, "display_name": "EN raw"}],
            "target_langs": [{"code": "de", "name_zh": "德语"}, {"code": "fr", "name_zh": "法语"}],
            "defaults": {
                "raw_ids": [88],
                "target_langs": ["fr"],
                "content_types": ["copywriting", "detail_images", "video_covers", "videos"],
                "video_params": {"subtitle_font": "Impact", "subtitle_size": 14, "subtitle_position_y": 0.88},
            },
            "preview": {"video_url": "/medias/raw-sources/88/video"},
            "manage_url": "/medias/products/123/translation-tasks",
        },
    )
    monkeypatch.setattr("web.routes.medias.medias.get_product", lambda pid: {"id": pid, "name": "demo"})
    monkeypatch.setattr("web.routes.medias._can_access_product", lambda product: product is not None)

    resp = authed_client_no_db.get("/medias/api/products/123/translation-bootstrap")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["defaults"]["raw_ids"] == [88]
    assert data["defaults"]["target_langs"] == ["fr"]
    assert data["manage_url"].endswith("/translation-tasks")
```

新增 `tests/test_medias_translation_assets.py`：

```python
from pathlib import Path


def test_medias_list_contains_translation_management_entry():
    root = Path(__file__).resolve().parents[1]
    template = (root / "web" / "templates" / "medias_list.html").read_text(encoding="utf-8")

    assert "翻译任务管理" in template
    assert 'id="rstContentTypes"' in template
    assert 'id="rstManageLink"' in template
    assert 'id="rstSubtitlePreviewMount"' in template


def test_medias_js_uses_translation_bootstrap_and_manage_url():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias.js").read_text(encoding="utf-8")

    assert "/medias/api/products/${pid}/translation-bootstrap" in script
    assert "content_types" in script
    assert "video_params" in script
    assert "manage_url" in script
```

- [ ] **Step 2: 跑测试，确认现在先因 bootstrap API / 新模板结构不存在而失败**

Run:

```bash
pytest tests/test_medias_raw_sources_translate.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py -q
```

Expected:

```text
FAILED tests/test_medias_raw_sources_translate.py::test_translate_ok_with_full_payload
FAILED tests/test_medias_translation_tasks_routes.py::test_translation_bootstrap_defaults_missing_video_langs
FAILED tests/test_medias_translation_assets.py::test_medias_list_contains_translation_management_entry
```

- [ ] **Step 3: 创建 `appcore/medias_translation_bootstrap.py`，集中组装默认勾选和共享预览数据**

实现 bootstrap helper：

```python
from __future__ import annotations

from appcore import medias
from appcore.subtitle_preview_payload import build_product_preview_payload
from appcore.video_translate_defaults import load_effective_params


DEFAULT_CONTENT_TYPES = ["copywriting", "detail_images", "video_covers", "videos"]


def build_translation_bootstrap_payload(user_id: int, product_id: int) -> dict:
    raw_sources = medias.list_raw_sources(product_id)
    items = medias.list_items(product_id)
    languages = [row for row in medias.list_languages() if (row.get("code") or "") != "en"]
    default_raw_ids = [int(row["id"]) for row in raw_sources]

    existing_video_langs = {
        (row.get("lang") or "").strip().lower()
        for row in items
        if row.get("source_raw_id")
    }
    default_target_langs = [
        row["code"] for row in languages
        if row["code"] not in existing_video_langs
    ]

    video_params = load_effective_params(user_id, product_id, None)
    preview = build_product_preview_payload(
        product_id=product_id,
        items=items,
        raw_sources=raw_sources,
        video_params=video_params,
    )

    return {
        "raw_sources": [{
            "id": row["id"],
            "display_name": row.get("display_name") or f"原始视频 #{row['id']}",
            "cover_url": row.get("cover_url") or f"/medias/raw-sources/{row['id']}/cover",
            "video_url": row.get("video_url") or f"/medias/raw-sources/{row['id']}/video",
            "duration_seconds": row.get("duration_seconds"),
            "file_size": row.get("file_size"),
        } for row in raw_sources],
        "target_langs": [{
            "code": row["code"],
            "name_zh": row.get("name_zh") or row["code"],
        } for row in languages],
        "defaults": {
            "raw_ids": default_raw_ids,
            "target_langs": default_target_langs,
            "content_types": list(DEFAULT_CONTENT_TYPES),
            "video_params": {
                "subtitle_font": video_params.get("subtitle_font") or "Impact",
                "subtitle_size": int(video_params.get("subtitle_size") or 14),
                "subtitle_position_y": float(video_params.get("subtitle_position_y") or 0.68),
            },
        },
        "preview": preview,
    }
```

- [ ] **Step 4: 在 `web/routes/medias.py` 里新增 bootstrap API，并升级创建接口契约**

新增 bootstrap API：

```python
from appcore.medias_translation_bootstrap import DEFAULT_CONTENT_TYPES, build_translation_bootstrap_payload


@bp.route("/api/products/<int:pid>/translation-bootstrap", methods=["GET"])
@login_required
def api_translation_bootstrap(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)
    payload = build_translation_bootstrap_payload(current_user.id, pid)
    payload["manage_url"] = f"/medias/products/{pid}/translation-tasks"
    return jsonify(payload)
```

升级创建接口：

```python
@bp.route("/api/products/<int:pid>/translate", methods=["POST"])
@login_required
def api_product_translate(pid: int):
    product = medias.get_product(pid)
    if not _can_access_product(product):
        abort(404)

    body = request.get_json(silent=True) or {}
    raw_ids = body.get("raw_ids") or []
    target_langs = body.get("target_langs") or []
    content_types = body.get("content_types") or list(DEFAULT_CONTENT_TYPES)
    video_params = body.get("video_params") or {}

    allowed_content_types = set(DEFAULT_CONTENT_TYPES)
    if not raw_ids:
        return jsonify({"error": "raw_ids 不能为空"}), 400
    if not target_langs:
        return jsonify({"error": "target_langs 不能为空"}), 400
    if not content_types or any(item not in allowed_content_types for item in content_types):
        return jsonify({"error": "content_types 不合法"}), 400

    task_id = create_bulk_translate_task(
        user_id=current_user.id,
        product_id=pid,
        target_langs=target_langs,
        content_types=content_types,
        force_retranslate=bool(body.get("force_retranslate")),
        video_params=video_params,
        initiator=initiator,
        raw_source_ids=raw_ids_int,
    )
    start_task(task_id, current_user.id)
    return jsonify({
        "task_id": task_id,
        "manage_url": f"/medias/products/{pid}/translation-tasks",
    }), 202
```

- [ ] **Step 5: 重做 `medias_list.html` 和 `medias.js`，把创建弹窗升级为 4 类任务 + 共享预览**

模板里的操作区改成：

```html
<div class="oc-rst-actions">
  <button type="button" class="oc-btn primary sm" data-action="translate">翻译</button>
  <a class="oc-btn ghost sm" data-action="translate-manage" href="/medias/products/__PID__/translation-tasks">翻译任务管理</a>
</div>
```

翻译弹窗结构改成：

```html
<div id="rsTranslateMask" class="oc-modal-mask oc" hidden>
  <div id="rsTranslateDialog" class="oc-modal oc-rst-modal" role="dialog" aria-modal="true" aria-labelledby="rsTranslateTitle">
    <div class="oc-modal-head">
      <div>
        <h3 id="rsTranslateTitle">一键翻译<span id="rstTitleMeta" class="muted"></span></h3>
        <p class="oc-rs-summary">文案、详情图、视频封面、视频统一编排</p>
      </div>
      <div class="oc-rst-head-actions">
        <a id="rstManageLink" class="oc-btn ghost sm" href="#">去任务管理</a>
        <button type="button" id="rstClose" class="oc-icon-btn" aria-label="关闭">
          <svg width="16" height="16"><use href="#ic-close"/></svg>
        </button>
      </div>
    </div>
    <div class="oc-modal-body">
      <section class="oc-rst-grid">
        <div class="oc-rst-panel">
          <h4 class="oc-rst-heading">选择原始视频</h4>
          <ul id="rstRsList" class="oc-rst-list"></ul>
        </div>
        <div class="oc-rst-panel">
          <h4 class="oc-rst-heading">翻译内容</h4>
          <div id="rstContentTypes" class="oc-rst-langs"></div>
          <h4 class="oc-rst-heading">目标语言</h4>
          <div id="rstLangs" class="oc-rst-langs"></div>
        </div>
      </section>
      <section class="oc-rst-preview-panel">
        <h4 class="oc-rst-heading">视频翻译配置</h4>
        <div id="rstSubtitlePreviewMount"></div>
      </section>
    </div>
    <div class="oc-modal-foot">
      <span id="rstPreview" class="oc-rst-preview">请选择原始视频和目标语言</span>
      <div class="oc-rst-actions">
        <button type="button" id="rstCancel" class="oc-btn ghost">取消</button>
        <button type="button" id="rstSubmit" class="oc-btn primary" disabled>创建任务</button>
      </div>
    </div>
  </div>
</div>
```

脚本里把 `openTranslateDialog()` 改成用 bootstrap 接口一次拿全量数据：

```js
const translateContentTypes = document.getElementById('rstContentTypes');
const rstManageLink = document.getElementById('rstManageLink');
const rstSubtitlePreviewMount = document.getElementById('rstSubtitlePreviewMount');
let subtitlePreviewController = null;

function setCheckedValues(container, values) {
  const wanted = new Set((values || []).map((value) => String(value)));
  container.querySelectorAll('input[type="checkbox"]').forEach((input) => {
    input.checked = wanted.has(String(input.value));
  });
}

function collectCheckedStrings(container) {
  return Array.from(
    container.querySelectorAll('input[type="checkbox"]:checked'),
    (input) => String(input.value),
  );
}

function collectCheckedNumbers(container) {
  return collectCheckedStrings(container)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
}

async function openTranslateDialog(pid, name) {
  uiState.translatePid = String(pid);
  uiState.translateName = name || '';
  translateMask.hidden = false;
  translateTitleMeta.textContent = uiState.translateName ? ` · ${uiState.translateName}` : '';
  translateSubmit.disabled = true;
  translatePreview.textContent = '加载中…';

  const data = await requestJSON(`/medias/api/products/${pid}/translation-bootstrap`);
  rstManageLink.href = data.manage_url;
  translateRsList.innerHTML = data.raw_sources.map(renderTranslateRawSourceChoice).join('');
  translateLangs.innerHTML = data.target_langs.map(renderTranslateLanguageChoice).join('');
  translateContentTypes.innerHTML = [
    { value: 'copywriting', label: '文案翻译' },
    { value: 'detail_images', label: '商品详情图翻译' },
    { value: 'video_covers', label: '视频封面翻译' },
    { value: 'videos', label: '视频翻译' },
  ].map(renderTranslateContentTypeChoice).join('');

  setCheckedValues(translateRsList, data.defaults.raw_ids.map(String));
  setCheckedValues(translateLangs, data.defaults.target_langs);
  setCheckedValues(translateContentTypes, data.defaults.content_types);

  rstSubtitlePreviewMount.innerHTML = document.getElementById('subtitlePreviewTemplate').innerHTML;
  subtitlePreviewController = createSubtitlePreviewController(
    document.getElementById('rstSubtitlePreviewMount').firstElementChild,
    {
      ...data.preview,
      ...data.defaults.video_params,
    },
  );
  updateTranslatePreview();
}

async function submitTranslateTask() {
  const pid = uiState.translatePid;
  const raw_ids = collectCheckedNumbers(translateRsList);
  const target_langs = collectCheckedStrings(translateLangs);
  const content_types = collectCheckedStrings(translateContentTypes);
  const video_params = subtitlePreviewController.getValue();

  const data = await requestJSON(`/medias/api/products/${pid}/translate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_ids, target_langs, content_types, video_params }),
  });
  window.location.href = data.manage_url;
}
```

- [ ] **Step 6: 补一个最小 e2e，锁定“两个入口 + 默认全选 + 共享预览出现”**

新增 `tests/e2e/test_medias_translation_orchestration_flow.py`：

```python
def test_medias_translation_dialog_defaults(page):
    page.goto("/medias")
    page.get_by_role("button", name="翻译").first.click()

    expect(page.get_by_text("一键翻译")).to_be_visible()
    expect(page.get_by_text("翻译任务管理")).to_be_visible()
    expect(page.locator("#rstContentTypes input:checked")).to_have_count(4)
    expect(page.locator("#rstSubtitlePreviewMount video")).to_be_visible()
```

- [ ] **Step 7: 跑素材翻译创建相关测试，确认接口、模板、脚本和最小 e2e 全部通过**

Run:

```bash
pytest tests/test_medias_raw_sources_translate.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py -q
pytest tests/e2e/test_medias_translation_orchestration_flow.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 8: 提交素材翻译创建弹窗升级**

Run:

```bash
git add appcore/medias_translation_bootstrap.py web/routes/medias.py web/templates/medias_list.html web/static/medias.js tests/test_medias_raw_sources_translate.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py tests/e2e/test_medias_translation_orchestration_flow.py
git commit -m "feat: upgrade medias translation create dialog"
```

### Task 4: 扩展 bulk_translate plan schema，覆盖 4 类任务和节流元数据

**Files:**
- Modify: `appcore/bulk_translate_plan.py`
- Modify: `tests/test_bulk_translate_plan.py`
- Modify: `tests/test_bulk_translate_plan_raw_sources.py`

- [ ] **Step 1: 先写失败测试，锁定新 kind、视频封面批次和 `dispatch_after_seconds`**

在 `tests/test_bulk_translate_plan.py` 里追加：

```python
def test_video_cover_batch_one_per_lang(monkeypatch):
    _patch(monkeypatch, _FakeDB(raw_sources=[{"id": 11}, {"id": 12}]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(
        1,
        77,
        ["de", "fr"],
        ["video_covers"],
        False,
        raw_source_ids=[11, 12],
    )

    assert len(plan) == 2
    assert {item["kind"] for item in plan} == {"video_covers"}
    assert plan[0]["ref"]["source_raw_ids"] == [11, 12]


def test_videos_have_two_minute_dispatch_spacing(monkeypatch):
    _patch(monkeypatch, _FakeDB(raw_sources=[{"id": 1}, {"id": 2}]))

    from appcore.bulk_translate_plan import generate_plan
    plan = generate_plan(
        1,
        77,
        ["de", "fr"],
        ["videos"],
        False,
        raw_source_ids=[1, 2],
    )

    assert [item["dispatch_after_seconds"] for item in plan] == [0, 120, 240, 360]
    assert all(item["kind"] == "videos" for item in plan)


def test_plan_item_schema_uses_new_child_fields(monkeypatch):
    _patch(monkeypatch, _FakeDB(copies=[{"id": 10}]))

    from appcore.bulk_translate_plan import generate_plan
    item = generate_plan(1, 77, ["de"], ["copywriting"], False)[0]

    required = {
        "idx",
        "kind",
        "lang",
        "ref",
        "child_task_id",
        "child_task_type",
        "status",
        "dispatch_after_seconds",
        "result_synced",
        "error",
        "started_at",
        "finished_at",
    }
    assert required.issubset(item.keys())
    assert item["status"] == "pending"
    assert item["result_synced"] is False
```

- [ ] **Step 2: 跑 plan 测试，确认旧 schema 先失败**

Run:

```bash
pytest tests/test_bulk_translate_plan.py tests/test_bulk_translate_plan_raw_sources.py -q
```

Expected:

```text
FAILED tests/test_bulk_translate_plan.py::test_video_cover_batch_one_per_lang
FAILED tests/test_bulk_translate_plan.py::test_videos_have_two_minute_dispatch_spacing
FAILED tests/test_bulk_translate_plan.py::test_plan_item_schema_uses_new_child_fields
```

- [ ] **Step 3: 在 `appcore/bulk_translate_plan.py` 里切换到新 kind 命名和新 item schema**

把核心工厂改成：

```python
def _new_item(idx: int, kind: str, lang: str, ref: dict, dispatch_after_seconds: int) -> dict:
    return {
        "idx": idx,
        "kind": kind,
        "lang": lang,
        "ref": ref,
        "child_task_id": None,
        "child_task_type": None,
        "status": "pending",
        "dispatch_after_seconds": dispatch_after_seconds,
        "result_synced": False,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }
```

`generate_plan()` 改成：

```python
if "copywriting" in content_types:
    for row in copy_rows:
        for lang in target_langs:
            plan.append(_new_item(
                idx_counter.next(),
                "copywriting",
                lang,
                {"source_copy_id": row["id"]},
                0,
            ))

if "detail_images" in content_types and detail_ids:
    for lang_index, lang in enumerate(target_langs):
        plan.append(_new_item(
            idx_counter.next(),
            "detail_images",
            lang,
            {"source_detail_ids": detail_ids},
            lang_index * 30,
        ))

if "video_covers" in content_types:
    raw_rows = _list_selected_raw_sources(product_id, raw_source_ids)
    raw_ids = [int(row["id"]) for row in raw_rows]
    for lang in target_langs:
        plan.append(_new_item(
            idx_counter.next(),
            "video_covers",
            lang,
            {"source_raw_ids": raw_ids},
            0,
        ))

if "videos" in content_types:
    raw_rows = _list_selected_raw_sources(product_id, raw_source_ids)
    dispatch_slot = 0
    for row in raw_rows:
        for lang in target_langs:
            if lang not in VIDEO_SUPPORTED_LANGS:
                continue
            plan.append(_new_item(
                idx_counter.next(),
                "videos",
                lang,
                {"source_raw_id": row["id"]},
                dispatch_slot * 120,
            ))
            dispatch_slot += 1
```

- [ ] **Step 4: 跑 plan 相关测试，确认新 schema 和节流字段都稳定**

Run:

```bash
pytest tests/test_bulk_translate_plan.py tests/test_bulk_translate_plan_raw_sources.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 5: 提交 plan schema 升级**

Run:

```bash
git add appcore/bulk_translate_plan.py tests/test_bulk_translate_plan.py tests/test_bulk_translate_plan_raw_sources.py
git commit -m "feat: expand bulk translate plan schema"
```

### Task 5: 重构 bulk_translate runtime 为“派发 + 观察”状态机

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Modify: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，锁定新父/子状态、节流派发和 `waiting_manual`**

在 `tests/test_bulk_translate_runtime.py` 里追加：

```python
def test_create_initializes_rollup_and_dispatch_state(fake_db, monkeypatch):
    from appcore import bulk_translate_runtime as mod

    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: [
        {
            "idx": 0,
            "kind": "videos",
            "lang": "de",
            "ref": {"source_raw_id": 301},
            "child_task_id": None,
            "child_task_type": None,
            "status": "pending",
            "dispatch_after_seconds": 0,
            "result_synced": False,
            "error": None,
            "started_at": None,
            "finished_at": None,
        }
    ])

    tid = mod.create_bulk_translate_task(
        user_id=1,
        product_id=77,
        target_langs=["de"],
        content_types=["videos"],
        force_retranslate=False,
        video_params={"subtitle_size": 16},
        initiator={"user_id": 1, "user_name": "", "ip": "", "user_agent": ""},
        raw_source_ids=[301],
    )

    task = mod.get_task(tid)
    assert task["state"]["rollup"]["waiting_manual"] == 0
    assert set(task["state"]["dispatch_state"].keys()) == {"copywriting", "detail_images", "video_covers", "videos"}


def test_scheduler_only_dispatches_items_whose_delay_has_elapsed(fake_db, monkeypatch):
    from appcore import bulk_translate_runtime as mod

    plan = [
        {"idx": 0, "kind": "detail_images", "lang": "de", "ref": {"source_detail_ids": [1]}, "child_task_id": None, "child_task_type": None, "status": "pending", "dispatch_after_seconds": 0, "result_synced": False, "error": None, "started_at": None, "finished_at": None},
        {"idx": 1, "kind": "detail_images", "lang": "fr", "ref": {"source_detail_ids": [1]}, "child_task_id": None, "child_task_type": None, "status": "pending", "dispatch_after_seconds": 30, "result_synced": False, "error": None, "started_at": None, "finished_at": None},
    ]

    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: plan)
    tid = _prepare_running_task(fake_db, monkeypatch, plan)
    calls = []

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: calls.append(item["idx"]) or ("child-" + str(item["idx"]), "image_translate", "running"),
    )
    monkeypatch.setattr(mod, "_poll_child_tasks", lambda *a, **k: False)

    current_ts = {"value": 0}
    mod.run_scheduler(
        tid,
        now_provider=lambda: current_ts["value"],
        sleep_fn=lambda _seconds: None,
        max_loops=1,
    )
    assert calls == [0]


def test_scheduler_sets_waiting_manual_when_video_child_awaits_voice(fake_db, monkeypatch):
    from appcore import bulk_translate_runtime as mod

    plan = [{
        "idx": 0,
        "kind": "videos",
        "lang": "de",
        "ref": {"source_raw_id": 301},
        "child_task_id": None,
        "child_task_type": None,
        "status": "pending",
        "dispatch_after_seconds": 0,
        "result_synced": False,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }]
    monkeypatch.setattr(mod, "generate_plan", lambda *a, **kw: plan)
    tid = _prepare_running_task(fake_db, monkeypatch, plan)

    monkeypatch.setattr(
        mod,
        "_create_child_task",
        lambda parent_id, item, parent_state: ("multi-1", "multi_translate", "awaiting_voice"),
    )
    monkeypatch.setattr(mod, "_poll_child_tasks", lambda *a, **k: False)

    mod.run_scheduler(tid, now_provider=lambda: 0, sleep_fn=lambda _seconds: None, max_loops=1)

    task = mod.get_task(tid)
    assert task["status"] == "waiting_manual"
    assert task["state"]["plan"][0]["status"] == "awaiting_voice"
```

- [ ] **Step 2: 跑 runtime 测试，确认旧串行执行器模型先失败**

Run:

```bash
pytest tests/test_bulk_translate_runtime.py -q
```

Expected:

```text
newly added runtime tests fail with missing dispatch_state / _create_child_task / waiting_manual assertions
```

- [ ] **Step 3: 在 `create_bulk_translate_task()` 里初始化 `dispatch_state / child_index / rollup`**

把初始化 state 改成：

```python
state = {
    "product_id": product_id,
    "source_lang": "en",
    "target_langs": target_langs,
    "content_types": content_types,
    "force_retranslate": force_retranslate,
    "raw_source_ids": raw_source_ids or [],
    "video_params_snapshot": video_params or {},
    "initiator": initiator,
    "plan": plan,
    "dispatch_state": {
        "copywriting": {"next_dispatch_at": 0},
        "detail_images": {"next_dispatch_at": 0},
        "video_covers": {"next_dispatch_at": 0},
        "videos": {"next_dispatch_at": 0},
    },
    "child_index": {},
    "rollup": {
        "total": len(plan),
        "pending": len(plan),
        "running": 0,
        "waiting_manual": 0,
        "failed": 0,
        "interrupted": 0,
        "done": 0,
        "skipped": 0,
    },
    "progress": compute_progress(plan),
    "cancel_requested": False,
    "audit_events": [
        _audit(user_id, "create", {
            "target_langs": target_langs,
            "content_types": content_types,
            "force_retranslate": force_retranslate,
        }),
    ],
    "cost_tracking": {
        "estimate": {
            "copy_tokens": 0,
            "image_count": 0,
            "video_minutes": 0.0,
            "estimated_cost_cny": 0.0,
        },
        "actual": {
            "copy_tokens_used": 0,
            "image_processed": 0,
            "video_minutes_processed": 0.0,
            "actual_cost_cny": 0.0,
        },
    },
}
```

- [ ] **Step 4: 重写 `run_scheduler()`，把同步阻塞派发换成“派发 ready 项 + 轮询已建子任务”**

把调度主循环定型为：

```python
def run_scheduler(
    task_id: str,
    bus: EventBus | None = None,
    *,
    now_provider=lambda: 0,
    sleep_fn=lambda _seconds: None,
    max_loops: int | None = None,
) -> None:
    loops = 0
    while True:
        task = get_task(task_id)
        if not task:
            return
        state = task["state"]
        state["progress"] = compute_progress(state["plan"])

        if state.get("cancel_requested"):
            _save_state(task_id, state, status="failed")
            return

        now_value = now_provider()
        dispatched = _dispatch_ready_items(task_id, state, now_value)
        changed = _poll_child_tasks(task_id, state) or dispatched
        parent_status = _derive_parent_status(state)
        _save_state(task_id, state, status=parent_status)
        _emit(bus, EVT_BT_PROGRESS if parent_status != "done" else EVT_BT_DONE, task_id, state, parent_status)

        if parent_status in {"done", "failed", "waiting_manual", "interrupted"} and not _has_pending_ready_items(state, now_value):
            return

        loops += 1
        if max_loops is not None and loops >= max_loops:
            return
        sleep_fn(1)
```

同步把 `compute_progress()` 扩成新状态集：

```python
def compute_progress(plan: list[dict]) -> dict:
    progress = {
        "total": len(plan),
        "pending": 0,
        "dispatching": 0,
        "running": 0,
        "syncing_result": 0,
        "awaiting_voice": 0,
        "failed": 0,
        "interrupted": 0,
        "done": 0,
        "skipped": 0,
    }
    for item in plan:
        status = item["status"]
        progress[status] = progress.get(status, 0) + 1
    return progress
```

再补这一组 helper，把“创建子任务”“轮询子任务”“父任务是否还要继续挂住”三个环节一次补齐：

```python
from threading import Thread

from appcore import task_state
from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
from appcore.image_translate_runtime import ImageTranslateRuntime
from appcore.runtime_multi import MultiTranslateRunner


def _run_async(target, *args, **kwargs) -> None:
    Thread(target=target, args=args, kwargs=kwargs, daemon=True).start()


def _dispatch_ready_items(task_id: str, state: dict, now_value: int) -> bool:
    changed = False
    for item in state["plan"]:
        if item["status"] != "pending":
            continue
        if now_value < int(item.get("dispatch_after_seconds") or 0):
            continue
        child_task_id, child_task_type, child_status = _create_child_task(task_id, item, state)
        item["child_task_id"] = child_task_id
        item["child_task_type"] = child_task_type
        item["status"] = child_status
        item["started_at"] = _now_iso()
        changed = True
    return changed


def _create_child_task(parent_id: str, item: dict, parent_state: dict) -> tuple[str, str, str]:
    user_id = int(parent_state["initiator"]["user_id"])
    product_id = int(parent_state["product_id"])
    lang = item["lang"]
    kind = item["kind"]

    if kind == "copywriting":
        child_id = str(uuid.uuid4())
        child_state = {
            "product_id": product_id,
            "source_lang": "en",
            "target_lang": lang,
            "source_copy_id": int(item["ref"]["source_copy_id"]),
            "parent_task_id": parent_id,
        }
        execute(
            """
            INSERT INTO projects (id, user_id, type, status, state_json)
            VALUES (%s, %s, 'copywriting_translate', 'queued', %s)
            """,
            (child_id, user_id, json.dumps(child_state, ensure_ascii=False)),
        )
        _run_async(CopywritingTranslateRunner(child_id, bus=EventBus()).start)
        return child_id, "copywriting_translate", "running"

    if kind in {"detail_images", "video_covers"}:
        child_id = str(uuid.uuid4())
        task_dir = os.path.join(OUTPUT_DIR, child_id)
        os.makedirs(task_dir, exist_ok=True)

        if kind == "detail_images":
            wanted_ids = {int(v) for v in item["ref"]["source_detail_ids"]}
            rows = [
                row
                for row in medias.list_detail_images(product_id, "en")
                if int(row["id"]) in wanted_ids
            ]
            src_items = [
                {
                    "idx": idx,
                    "filename": Path(row["object_key"]).name,
                    "src_tos_key": row["object_key"],
                    "source_bucket": "media",
                    "source_detail_image_id": int(row["id"]),
                }
                for idx, row in enumerate(rows)
            ]
            preset = "detail"
            medias_context = {
                "product_id": product_id,
                "target_lang": lang,
                "auto_apply_detail_images": False,
            }
        else:
            raw_rows = [
                medias.get_raw_source(int(raw_id))
                for raw_id in item["ref"]["source_raw_ids"]
            ]
            src_items = [
                {
                    "idx": idx,
                    "filename": Path(row["cover_object_key"]).name,
                    "src_tos_key": row["cover_object_key"],
                    "source_bucket": "media",
                }
                for idx, row in enumerate(raw_rows)
                if row and row.get("cover_object_key")
            ]
            preset = "cover"
            medias_context = {
                "product_id": product_id,
                "target_lang": lang,
                "auto_apply_detail_images": False,
                "source_raw_ids": list(item["ref"]["source_raw_ids"]),
            }

        task_state.create_image_translate(
            child_id,
            task_dir,
            user_id=user_id,
            preset=preset,
            target_language=lang,
            target_language_name=medias.get_language_name(lang) or lang.upper(),
            model_id=_default_image_translate_model_id(user_id),
            prompt=f"Translate {preset} assets into {lang}",
            items=src_items,
            product_name=str(parent_state.get("product_name") or ""),
            project_name=f"{product_id}-{kind}-{lang}",
            medias_context=medias_context,
            concurrency_mode="parallel",
        )
        _run_async(ImageTranslateRuntime(bus=EventBus(), user_id=user_id).start, child_id)
        return child_id, "image_translate", "running"

    if kind == "videos":
        raw = medias.get_raw_source(int(item["ref"]["source_raw_id"]))
        if not raw:
            raise ValueError(f"raw source {item['ref']['source_raw_id']} missing")

        child_id = str(uuid.uuid4())
        task_dir = os.path.join(OUTPUT_DIR, child_id)
        os.makedirs(task_dir, exist_ok=True)
        local_video = _download_media_to_tmp(
            raw["video_object_key"],
            suffix=_suffix_from_key(raw["video_object_key"], default=".mp4"),
        )
        task_state.create_translate_lab(
            child_id,
            local_video,
            task_dir,
            original_filename=Path(local_video).name,
            user_id=user_id,
            source_language="en",
            target_lang=lang,
            source_tos_key=raw["video_object_key"],
            source_raw_id=int(raw["id"]),
            parent_task_id=parent_id,
            display_name=f"{raw.get('display_name') or 'Raw Video'} · {lang.upper()}",
            **dict(parent_state.get("video_params_snapshot") or {}),
        )
        execute("UPDATE projects SET type = 'multi_translate' WHERE id = %s", (child_id,))
        _run_async(MultiTranslateRunner(bus=EventBus()).start, child_id, user_id=user_id)
        return child_id, "multi_translate", "running"

    raise ValueError(f"unknown kind: {kind}")


def _load_child_snapshot(task_type: str, task_id: str) -> dict:
    row = query_one(
        "SELECT status, state_json FROM projects WHERE id = %s",
        (task_id,),
    ) or {}
    state_json = row.get("state_json") or {}
    state = state_json if isinstance(state_json, dict) else json.loads(state_json or "{}")
    state["_project_status"] = row.get("status") or ""
    state["_task_type"] = task_type
    return state


def _map_child_status(kind: str, child_snapshot: dict) -> str:
    project_status = str(child_snapshot.get("_project_status") or "").lower()
    if project_status in {"error", "failed"}:
        return "failed"
    if kind == "videos":
        current_review_step = str(child_snapshot.get("current_review_step") or "").lower()
        voice_status = str((child_snapshot.get("steps") or {}).get("voice_match") or "").lower()
        if current_review_step == "voice_match" or voice_status == "waiting":
            return "awaiting_voice"
    if project_status in {"done", "completed"}:
        return "syncing_result"
    if project_status in {"queued", "planning", "uploaded"}:
        return "dispatching"
    return "running"


def _poll_child_tasks(task_id: str, state: dict) -> bool:
    changed = False
    for item in state["plan"]:
        if item["status"] not in {"dispatching", "running", "awaiting_voice", "syncing_result"}:
            continue
        child_snapshot = _load_child_snapshot(item["child_task_type"], item["child_task_id"])
        next_status = _map_child_status(item["kind"], child_snapshot)
        if next_status != item["status"]:
            item["status"] = next_status
            changed = True
        if next_status == "syncing_result":
            _sync_child_result(task_id, item, state, child_snapshot)
            item["finished_at"] = _now_iso()
            changed = True
    return changed


def _has_pending_ready_items(state: dict, now_value: int) -> bool:
    for item in state["plan"]:
        status = item["status"]
        if status in {"dispatching", "running", "syncing_result", "awaiting_voice"}:
            return True
        if status == "pending" and now_value >= int(item.get("dispatch_after_seconds") or 0):
            return True
    return False


def _derive_parent_status(state: dict) -> str:
    statuses = [item["status"] for item in state["plan"]]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "interrupted" for status in statuses):
        return "interrupted"
    if any(status in {"dispatching", "running", "syncing_result"} for status in statuses):
        return "running"
    if any(status == "awaiting_voice" for status in statuses):
        return "waiting_manual"
    if all(status in {"done", "skipped"} for status in statuses):
        return "done"
    return "running"
```

`_derive_parent_status()` 固定为：

```python
def _derive_parent_status(state: dict) -> str:
    statuses = [item["status"] for item in state["plan"]]
    if any(status == "failed" for status in statuses):
        return "failed"
    if any(status == "interrupted" for status in statuses):
        return "interrupted"
    if any(status in {"dispatching", "running", "syncing_result"} for status in statuses):
        return "running"
    if any(status == "awaiting_voice" for status in statuses):
        return "waiting_manual"
    if all(status in {"done", "skipped"} for status in statuses):
        return "done"
    return "running"
```

- [ ] **Step 5: 把 retry / resume 逻辑改成新状态集，只重置 `failed` 和 `interrupted`**

`resume_task()` 改成只恢复中断类：

```python
def resume_task(task_id: str, user_id: int) -> None:
    task = get_task(task_id)
    if not task:
        raise ValueError(f"Task {task_id} not found")
    state = task["state"]
    for item in state["plan"]:
        if item["status"] == "interrupted":
            item["status"] = "pending"
            item["child_task_id"] = None
            item["child_task_type"] = None
            item["error"] = None
            item["started_at"] = None
            item["finished_at"] = None
    _append_audit(state, user_id, "resume")
    _save_state(task_id, state, status="running")
```

`retry_failed_items()` 改成：

```python
for item in state["plan"]:
    if item["status"] in {"failed", "interrupted"}:
        item["status"] = "pending"
        item["child_task_id"] = None
        item["child_task_type"] = None
        item["error"] = None
        item["started_at"] = None
        item["finished_at"] = None
        item["result_synced"] = False
```

- [ ] **Step 6: 跑 runtime 全量单测，确认新状态机稳定**

Run:

```bash
pytest tests/test_bulk_translate_runtime.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 7: 提交 runtime 状态机重构**

Run:

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_runtime.py
git commit -m "feat: refactor bulk translate runtime orchestration"
```

### Task 6: 增加视频封面持久化表并实现四类结果回填

**Files:**
- Create: `db/migrations/2026_04_22_medias_raw_source_translations.sql`
- Modify: `appcore/medias.py`
- Modify: `appcore/bulk_translate_associations.py`
- Create: `appcore/bulk_translate_backfill.py`
- Create: `tests/test_db_migration_medias_raw_source_translations.py`
- Create: `tests/test_bulk_translate_backfill.py`
- Modify: `appcore/bulk_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，锁定新表、DAO 和回填函数**

新增 `tests/test_db_migration_medias_raw_source_translations.py`：

```python
from pathlib import Path


def test_raw_source_translation_migration_contains_new_table():
    root = Path(__file__).resolve().parents[1]
    body = (root / "db" / "migrations" / "2026_04_22_medias_raw_source_translations.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS media_raw_source_translations" in body
    assert "source_ref_id" in body
    assert "cover_object_key" in body
    assert "bulk_task_id" in body
    assert "auto_translated" in body
```

新增 `tests/test_bulk_translate_backfill.py`：

```python
def test_sync_video_cover_result_marks_auto_translated(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    captured = {}
    monkeypatch.setattr(
        mod.medias,
        "upsert_raw_source_translation",
        lambda product_id, source_ref_id, lang, cover_object_key: captured.update({
            "product_id": product_id,
            "source_ref_id": source_ref_id,
            "lang": lang,
            "cover_object_key": cover_object_key,
        }) or 901,
    )
    monkeypatch.setattr(
        mod,
        "mark_auto_translated",
        lambda table, target_id, source_ref_id, bulk_task_id: captured.update({
            "table": table,
            "target_id": target_id,
            "bulk_task_id": bulk_task_id,
        }) or 1,
    )

    mod.sync_video_cover_result(
        parent_task_id="bt-1",
        product_id=77,
        lang="de",
        source_raw_id=301,
        cover_object_key="1/medias/77/cover_de_raw301.png",
    )

    assert captured["table"] == "media_raw_source_translations"
    assert captured["target_id"] == 901
    assert captured["bulk_task_id"] == "bt-1"


def test_sync_copywriting_result_uses_existing_association_helper(monkeypatch):
    from appcore import bulk_translate_backfill as mod

    created = {}
    monkeypatch.setattr(
        mod.medias,
        "create_or_update_copywriting",
        lambda product_id, lang, content, source_ref_id=None: created.update({
            "product_id": product_id,
            "lang": lang,
            "content": content,
            "source_ref_id": source_ref_id,
        }) or 700,
    )
    monkeypatch.setattr(mod, "mark_auto_translated", lambda *args, **kwargs: 1)

    mod.sync_copywriting_result(
        parent_task_id="bt-1",
        product_id=77,
        lang="fr",
        source_copy_id=11,
        translated_text="Bonjour",
    )

    assert created["lang"] == "fr"
    assert created["content"] == "Bonjour"
    assert created["source_ref_id"] == 11
```

- [ ] **Step 2: 跑新测试，确认因为迁移文件/模块不存在而失败**

Run:

```bash
pytest tests/test_db_migration_medias_raw_source_translations.py tests/test_bulk_translate_backfill.py -q
```

Expected:

```text
FAILED tests/test_db_migration_medias_raw_source_translations.py::test_raw_source_translation_migration_contains_new_table
FAILED tests/test_bulk_translate_backfill.py::test_sync_video_cover_result_marks_auto_translated
```

- [ ] **Step 3: 新建迁移和 DAO，让“视频封面翻译”有独立持久化位置**

迁移文件写成：

```sql
CREATE TABLE IF NOT EXISTS media_raw_source_translations (
  id INT AUTO_INCREMENT PRIMARY KEY,
  product_id INT NOT NULL,
  source_ref_id INT NOT NULL,
  lang VARCHAR(8) NOT NULL,
  cover_object_key VARCHAR(500) NOT NULL,
  bulk_task_id VARCHAR(64) NULL,
  auto_translated TINYINT(1) NOT NULL DEFAULT 0,
  manually_edited_at TIMESTAMP NULL DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  deleted_at DATETIME NULL DEFAULT NULL,
  UNIQUE KEY uniq_source_lang (source_ref_id, lang),
  KEY idx_product_lang (product_id, lang),
  KEY idx_bulk_task_id (bulk_task_id)
);
```

`appcore/medias.py` 增加：

```python
def create_or_update_copywriting(product_id: int, lang: str, content: str, source_ref_id: int | None = None) -> int:
    rows = list_copywritings(product_id, lang=lang)
    if rows:
        execute(
            "UPDATE media_copywritings SET content = %s, updated_at = NOW() WHERE id = %s",
            (content, rows[0]["id"]),
        )
        return int(rows[0]["id"])
    execute(
        "INSERT INTO media_copywritings (product_id, lang, content, idx) VALUES (%s, %s, %s, %s)",
        (product_id, lang, content, 0),
    )
    row = query_one(
        "SELECT id FROM media_copywritings WHERE product_id = %s AND lang = %s ORDER BY id DESC LIMIT 1",
        (product_id, lang),
    )
    return int(row["id"])


def upsert_raw_source_translation(product_id: int, source_ref_id: int, lang: str, cover_object_key: str) -> int:
    execute(
        "INSERT INTO media_raw_source_translations "
        "(product_id, source_ref_id, lang, cover_object_key) "
        "VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE "
        "  product_id = VALUES(product_id), "
        "  cover_object_key = VALUES(cover_object_key), "
        "  deleted_at = NULL, "
        "  updated_at = CURRENT_TIMESTAMP",
        (product_id, source_ref_id, lang, cover_object_key),
    )
    row = query_one(
        "SELECT id FROM media_raw_source_translations WHERE source_ref_id = %s AND lang = %s LIMIT 1",
        (source_ref_id, lang),
    )
    return int(row["id"])


def list_raw_source_translations(product_id: int, lang: str | None = None) -> list[dict]:
    sql = (
        "SELECT id, product_id, source_ref_id, lang, cover_object_key, bulk_task_id, "
        "       auto_translated, manually_edited_at, created_at, updated_at "
        "FROM media_raw_source_translations "
        "WHERE product_id = %s AND deleted_at IS NULL"
    )
    args = [product_id]
    if lang:
        sql += " AND lang = %s"
        args.append(lang)
    sql += " ORDER BY source_ref_id ASC, lang ASC"
    return query(sql, tuple(args))
```

- [ ] **Step 4: 扩展关联辅助和回填模块，让四类结果统一走同一套自动翻译标记**

`appcore/bulk_translate_associations.py` 把白名单加上：

```python
_ALLOWED_TABLES = {
    "media_copywritings",
    "media_product_detail_images",
    "media_items",
    "media_product_covers",
    "media_raw_source_translations",
}
```

新建 `appcore/bulk_translate_backfill.py`：

```python
from __future__ import annotations

from pathlib import Path

from appcore import medias
from appcore.bulk_translate_associations import mark_auto_translated
from appcore.db import execute
from appcore.image_translate_runtime import apply_translated_detail_images_from_task
from appcore import task_state


def sync_copywriting_result(parent_task_id: str, product_id: int, lang: str, source_copy_id: int, translated_text: str) -> int:
    target_id = medias.create_or_update_copywriting(
        product_id=product_id,
        lang=lang,
        content=translated_text,
        source_ref_id=source_copy_id,
    )
    mark_auto_translated("media_copywritings", target_id, source_copy_id, parent_task_id)
    return target_id


def sync_detail_images_result(parent_task_id: str, child_task_id: str) -> list[int]:
    child_task = task_state.get(child_task_id)
    if not child_task:
        raise ValueError(f"image_translate task missing: {child_task_id}")

    apply_result = apply_translated_detail_images_from_task(
        child_task,
        allow_partial=False,
        user_id=child_task.get("_user_id"),
    )
    applied_ids = [int(value) for value in (apply_result.get("applied_ids") or [])]
    if not applied_ids:
        return []

    ctx = child_task.get("medias_context") or {}
    rows = [
        row
        for row in medias.list_detail_images(int(ctx["product_id"]), str(ctx["target_lang"]))
        if int(row["id"]) in set(applied_ids)
    ]
    for row in rows:
        mark_auto_translated(
            "media_product_detail_images",
            int(row["id"]),
            int(row.get("source_detail_image_id") or 0),
            parent_task_id,
        )
    return applied_ids


def sync_video_cover_result(parent_task_id: str, product_id: int, lang: str, source_raw_id: int, cover_object_key: str) -> int:
    target_id = medias.upsert_raw_source_translation(
        product_id=product_id,
        source_ref_id=source_raw_id,
        lang=lang,
        cover_object_key=cover_object_key,
    )
    mark_auto_translated("media_raw_source_translations", target_id, source_raw_id, parent_task_id)
    return target_id


def sync_video_result(
    parent_task_id: str,
    product_id: int,
    lang: str,
    source_raw_id: int,
    video_object_key: str,
    cover_object_key: str | None,
) -> int:
    source_row = medias.get_raw_source(source_raw_id)
    if not source_row:
        raise ValueError(f"raw source missing: {source_raw_id}")

    target_id = medias.create_item(
        product_id=product_id,
        user_id=int(source_row["user_id"]),
        filename=Path(video_object_key).name,
        object_key=video_object_key,
        display_name=f"{source_row.get('display_name') or Path(video_object_key).stem} · {lang.upper()}",
        duration_seconds=source_row.get("duration_seconds"),
        file_size=None,
        cover_object_key=cover_object_key or source_row.get("cover_object_key"),
        lang=lang,
    )
    execute("UPDATE media_items SET source_raw_id=%s WHERE id=%s", (source_raw_id, target_id))
    mark_auto_translated("media_items", target_id, source_raw_id, parent_task_id)
    return target_id
```

- [ ] **Step 5: 在 runtime 的轮询里接入回填 hook，只在完成后写业务结果**

在 `appcore/bulk_translate_runtime.py` 里补：

```python
from appcore.bulk_translate_backfill import (
    sync_copywriting_result,
    sync_detail_images_result,
    sync_video_cover_result,
    sync_video_result,
)


def _sync_child_result(parent_id: str, item: dict, parent_state: dict, child_state: dict) -> None:
    product_id = int(parent_state["product_id"])
    lang = item["lang"]

    if item["kind"] == "copywriting":
        sync_copywriting_result(
            parent_task_id=parent_id,
            product_id=product_id,
            lang=lang,
            source_copy_id=int(item["ref"]["source_copy_id"]),
            translated_text=child_state["translated_text"],
        )
        item["result_synced"] = True
        item["status"] = "done"
        return

    if item["kind"] == "detail_images":
        sync_detail_images_result(
            parent_task_id=parent_id,
            child_task_id=str(item["child_task_id"]),
        )
        item["result_synced"] = True
        item["status"] = "done"
        return

    if item["kind"] == "video_covers":
        for raw_id, cover_key in child_state["translated_cover_map"].items():
            sync_video_cover_result(
                parent_task_id=parent_id,
                product_id=product_id,
                lang=lang,
                source_raw_id=int(raw_id),
                cover_object_key=cover_key,
        )
        item["result_synced"] = True
        item["status"] = "done"
        return

    if item["kind"] == "videos":
        video_object_key = (
            child_state.get("result_tos_key")
            or child_state.get("result_object_key")
            or ""
        ).strip()
        if not video_object_key:
            raise ValueError("multi_translate result video key missing")

        cover_object_key = (
            child_state.get("translated_cover_object_key")
            or child_state.get("cover_object_key")
            or ""
        ).strip()
        if cover_object_key:
            sync_video_cover_result(
                parent_task_id=parent_id,
                product_id=product_id,
                lang=lang,
                source_raw_id=int(item["ref"]["source_raw_id"]),
                cover_object_key=cover_object_key,
            )

        sync_video_result(
            parent_task_id=parent_id,
            product_id=product_id,
            lang=lang,
            source_raw_id=int(item["ref"]["source_raw_id"]),
            video_object_key=video_object_key,
            cover_object_key=cover_object_key or None,
        )
        item["result_synced"] = True
        item["status"] = "done"
        return
```

- [ ] **Step 6: 跑迁移、回填和 runtime 聚焦测试**

Run:

```bash
pytest tests/test_db_migration_medias_raw_source_translations.py tests/test_bulk_translate_backfill.py tests/test_bulk_translate_runtime.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 7: 提交回填与视频封面持久化**

Run:

```bash
git add db/migrations/2026_04_22_medias_raw_source_translations.sql appcore/medias.py appcore/bulk_translate_associations.py appcore/bulk_translate_backfill.py tests/test_db_migration_medias_raw_source_translations.py tests/test_bulk_translate_backfill.py appcore/bulk_translate_runtime.py
git commit -m "feat: add medias translation result backfill"
```

### Task 7: 完成产品级任务管理页和动作按钮

**Files:**
- Modify: `appcore/bulk_translate_projection.py`
- Create: `web/static/medias_translation_tasks.js`
- Modify: `web/templates/medias_translation_tasks.html`
- Modify: `tests/test_medias_translation_tasks_routes.py`
- Modify: `tests/test_medias_translation_assets.py`
- Create: `tests/test_bulk_translate_projection.py`

- [ ] **Step 1: 先写失败测试，锁定状态映射和动作按钮链接**

新增 `tests/test_bulk_translate_projection.py`：

```python
def test_projection_maps_failed_interrupted_and_awaiting_voice_actions():
    from appcore.bulk_translate_projection import build_task_action

    assert build_task_action(
        {"task_id": "bt-1", "idx": 2, "status": "failed", "child_task_id": None}
    ) == {
        "label": "重新启动",
        "method": "POST",
        "endpoint": "/api/bulk-translate/bt-1/retry-item",
        "payload": {"idx": 2},
    }

    assert build_task_action(
        {"task_id": "bt-1", "idx": 3, "status": "interrupted", "child_task_id": None}
    ) == {
        "label": "从中断点继续",
        "method": "POST",
        "endpoint": "/api/bulk-translate/bt-1/resume",
        "payload": {},
    }

    assert build_task_action(
        {"task_id": "bt-1", "idx": 4, "status": "awaiting_voice", "child_task_id": "multi-9"}
    ) == {
        "label": "去选声音",
        "href": "/multi-translate/multi-9",
    }
```

给 `tests/test_medias_translation_assets.py` 追加：

```python
def test_task_management_script_contains_expected_actions():
    root = Path(__file__).resolve().parents[1]
    script = (root / "web" / "static" / "medias_translation_tasks.js").read_text(encoding="utf-8")

    assert "重新启动" in script
    assert "从中断点继续" in script
    assert "去选声音" in script
    assert "/medias/api/products/${productId}/translation-tasks" in script
```

- [ ] **Step 2: 跑测试，确认因为动作映射和前端页面未完成而失败**

Run:

```bash
pytest tests/test_bulk_translate_projection.py tests/test_medias_translation_assets.py tests/test_medias_translation_tasks_routes.py -q
```

Expected:

```text
FAILED tests/test_bulk_translate_projection.py::test_projection_maps_failed_interrupted_and_awaiting_voice_actions
FAILED tests/test_medias_translation_assets.py::test_task_management_script_contains_expected_actions
```

- [ ] **Step 3: 在 `appcore/bulk_translate_projection.py` 里把批次明细和动作映射补齐**

补两个函数：

```python
def _build_label(item: dict) -> str:
    lang = str(item.get("lang") or "").upper()
    ref = item.get("ref") or {}
    kind = item.get("kind") or ""

    if kind == "copywriting":
        return f"{lang} 文案"
    if kind == "detail_images":
        count = len(ref.get("source_detail_ids") or [])
        return f"{lang} 详情图 x{count}"
    if kind == "video_covers":
        count = len(ref.get("source_raw_ids") or [])
        return f"{lang} 视频封面 x{count}"
    if kind == "videos":
        raw_id = ref.get("source_raw_id")
        return f"{lang} 视频 #{raw_id}" if raw_id else f"{lang} 视频"
    return f"{lang} 任务"


def build_task_action(item: dict) -> dict:
    status = item.get("status") or ""
    task_id = item.get("task_id") or ""
    idx = int(item.get("idx") or 0)
    child_task_id = (item.get("child_task_id") or "").strip()

    if status == "failed":
        return {
            "label": "重新启动",
            "method": "POST",
            "endpoint": f"/api/bulk-translate/{task_id}/retry-item",
            "payload": {"idx": idx},
        }
    if status == "interrupted":
        return {
            "label": "从中断点继续",
            "method": "POST",
            "endpoint": f"/api/bulk-translate/{task_id}/resume",
            "payload": {},
        }
    if status == "awaiting_voice" and child_task_id:
        return {
            "label": "去选声音",
            "href": f"/multi-translate/{child_task_id}",
        }
    return {}


def _group_items(task_id: str, plan: list[dict]) -> dict:
    groups = {
        "copywriting": [],
        "detail_images": [],
        "video_covers": [],
        "videos": [],
    }
    for item in plan:
        normalized = {
            "task_id": task_id,
            "idx": item["idx"],
            "label": _build_label(item),
            "status": item["status"],
            "lang": item["lang"],
            "child_task_id": item.get("child_task_id"),
        }
        normalized["action"] = build_task_action(normalized)
        groups[item["kind"]].append(normalized)
    return groups
```

- [ ] **Step 4: 写 `medias_translation_tasks.js` 和页面模板，把四类任务渲染成产品内批次卡片**

模板改成：

```html
{% extends "layout.html" %}
{% block title %}翻译任务管理{% endblock %}
{% block content %}
<section class="oc" id="translationTasksApp" data-product-id="{{ product_id }}">
  <header class="oc-header">
    <div>
      <h1 class="title">翻译任务管理</h1>
      <div class="subtitle">{{ product.name }}</div>
    </div>
    <a class="oc-btn ghost" href="/medias">返回素材管理</a>
  </header>
  <div id="translationTaskSummary"></div>
  <div id="translationTaskBatches"></div>
</section>
<script src="{{ url_for('static', filename='medias_translation_tasks.js') }}"></script>
{% endblock %}
```

脚本写成：

```js
(function () {
  const root = document.getElementById('translationTasksApp');
  if (!root) return;

  const productId = root.dataset.productId;
  const summary = document.getElementById('translationTaskSummary');
  const batches = document.getElementById('translationTaskBatches');

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    }[ch]));
  }

  function renderAction(action) {
    if (!action || (!action.href && !action.endpoint)) return '';
    if (action.href) {
      return `<a class="oc-btn primary sm" href="${escapeHtml(action.href)}">${escapeHtml(action.label)}</a>`;
    }
    return `<button type="button" class="oc-btn primary sm js-task-action" data-endpoint="${escapeHtml(action.endpoint)}" data-payload='${escapeHtml(JSON.stringify(action.payload || {}))}'>${escapeHtml(action.label)}</button>`;
  }

  function renderGroup(title, items) {
    return `
      <section class="oc-task-group">
        <h3>${escapeHtml(title)}</h3>
        ${items.map((item) => `
          <article class="oc-task-item" data-status="${escapeHtml(item.status)}">
            <div>
              <div class="name">${escapeHtml(item.label)}</div>
              <div class="meta">${escapeHtml(item.status)} · ${escapeHtml(item.lang || '')}</div>
            </div>
            <div class="actions">${renderAction(item.action)}</div>
          </article>
        `).join('')}
      </section>
    `;
  }

  async function load() {
    const resp = await fetch(`/medias/api/products/${productId}/translation-tasks`, { credentials: 'same-origin' });
    const data = await resp.json();
    summary.textContent = `${data.product.name} · ${data.batches.length} 个批次`;
    batches.innerHTML = data.batches.map((batch) => `
      <section class="oc-task-batch">
        <header class="oc-task-batch__head">
          <h2>${escapeHtml(batch.task_id)}</h2>
          <span class="status">${escapeHtml(batch.status)}</span>
        </header>
        ${renderGroup('文案翻译', batch.groups.copywriting || [])}
        ${renderGroup('商品详情图翻译', batch.groups.detail_images || [])}
        ${renderGroup('视频封面翻译', batch.groups.video_covers || [])}
        ${renderGroup('视频翻译', batch.groups.videos || [])}
      </section>
    `).join('');
  }

  batches.addEventListener('click', async (event) => {
    const btn = event.target.closest('.js-task-action');
    if (!btn) return;
    const endpoint = btn.dataset.endpoint;
    const payload = JSON.parse(btn.dataset.payload || '{}');
    await fetch(endpoint, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    await load();
  });

  load();
})();
```

- [ ] **Step 5: 跑投影和任务管理页相关测试**

Run:

```bash
pytest tests/test_bulk_translate_projection.py tests/test_medias_translation_assets.py tests/test_medias_translation_tasks_routes.py -q
```

Expected:

```text
all selected tests passed
```

- [ ] **Step 6: 提交产品级任务管理页**

Run:

```bash
git add appcore/bulk_translate_projection.py web/templates/medias_translation_tasks.html web/static/medias_translation_tasks.js tests/test_bulk_translate_projection.py tests/test_medias_translation_assets.py tests/test_medias_translation_tasks_routes.py
git commit -m "feat: add medias translation task management page"
```

### Task 8: 增加启动中断标记并完成聚焦验证

**Files:**
- Create: `appcore/bulk_translate_recovery.py`
- Create: `tests/test_bulk_translate_recovery.py`
- Modify: `web/app.py`
- Modify: `tests/test_bulk_translate_runtime.py`

- [ ] **Step 1: 先写失败测试，锁定 bulk_translate 启动恢复只标中断、不自动续跑**

新增 `tests/test_bulk_translate_recovery.py`：

```python
import json


def test_mark_interrupted_bulk_translate_tasks_marks_running_items(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    rows = [{
        "id": "bt-1",
        "status": "running",
        "state_json": json.dumps({
            "plan": [
                {"idx": 0, "status": "running", "child_task_id": "img-1"},
                {"idx": 1, "status": "awaiting_voice", "child_task_id": "multi-1"},
            ],
        }, ensure_ascii=False),
    }]
    updates = []

    monkeypatch.setattr(mod, "query", lambda sql, args=None: rows)
    monkeypatch.setattr(mod, "execute", lambda sql, args=None: updates.append(args) or 1)

    count = mod.mark_interrupted_bulk_translate_tasks()

    assert count == 1
    status, payload, task_id = updates[0]
    state = json.loads(payload)
    assert status == "interrupted"
    assert state["plan"][0]["status"] == "interrupted"
    assert state["plan"][1]["status"] == "awaiting_voice"
    assert task_id == "bt-1"


def test_mark_interrupted_bulk_translate_tasks_does_not_resume(monkeypatch):
    from appcore import bulk_translate_recovery as mod

    monkeypatch.setattr(mod, "query", lambda sql, args=None: [])
    called = {"resume": 0}
    monkeypatch.setattr(mod, "resume_task", lambda *args, **kwargs: called.update({"resume": called["resume"] + 1}), raising=False)

    assert mod.mark_interrupted_bulk_translate_tasks() == 0
    assert called["resume"] == 0
```

- [ ] **Step 2: 跑恢复测试，确认新模块现在还不存在**

Run:

```bash
pytest tests/test_bulk_translate_recovery.py -q
```

Expected:

```text
FAILED tests/test_bulk_translate_recovery.py::test_mark_interrupted_bulk_translate_tasks_marks_running_items
```

- [ ] **Step 3: 创建 `appcore/bulk_translate_recovery.py`，只做“标中断”不做“自动恢复”**

实现：

```python
from __future__ import annotations

import json

from appcore.db import execute, query


INTERRUPTIBLE_ITEM_STATUSES = {"dispatching", "running", "syncing_result"}


def mark_interrupted_bulk_translate_tasks() -> int:
    rows = query(
        "SELECT id, status, state_json "
        "FROM projects "
        "WHERE type = 'bulk_translate' AND deleted_at IS NULL "
        "  AND status IN ('planning', 'running', 'waiting_manual')",
        (),
    )
    changed_count = 0
    for row in rows:
        raw_state = row.get("state_json") or "{}"
        state = raw_state if isinstance(raw_state, dict) else json.loads(raw_state)
        changed = False
        for item in state.get("plan") or []:
            if item.get("status") in INTERRUPTIBLE_ITEM_STATUSES:
                item["status"] = "interrupted"
                item["error"] = "任务因服务重启中断，请手工继续。"
                changed = True
        if changed:
            execute(
                "UPDATE projects SET status = %s, state_json = %s WHERE id = %s",
                ("interrupted", json.dumps(state, ensure_ascii=False), row["id"]),
            )
            changed_count += 1
    return changed_count
```

- [ ] **Step 4: 在 `web/app.py` 里接入新恢复模块，并保留原 `task_recovery` 不碰 bulk_translate**

在 `web/app.py` 顶部新增导入：

```python
from appcore.bulk_translate_recovery import mark_interrupted_bulk_translate_tasks
```

在 `create_app()` 里接到：

```python
recover_all_interrupted_tasks()
mark_interrupted_bulk_translate_tasks()
```

- [ ] **Step 5: 跑完整聚焦验证，并做静态检查**

Run:

```bash
pytest tests/test_medias_raw_sources_translate.py tests/test_medias_translation_tasks_routes.py tests/test_medias_translation_assets.py tests/test_subtitle_preview_payload.py tests/test_bulk_translate_plan.py tests/test_bulk_translate_runtime.py tests/test_bulk_translate_backfill.py tests/test_bulk_translate_projection.py tests/test_bulk_translate_recovery.py tests/test_db_migration_medias_raw_source_translations.py tests/test_multi_translate_routes.py -q
pytest tests/e2e/test_medias_translation_orchestration_flow.py -q
git diff --check
```

Expected:

```text
all selected tests passed
git diff --check has no output
```

- [ ] **Step 6: 做最终人工核对，确认验收点全部命中**

逐项检查：

```text
1. 素材列表每行同时有“翻译”和“翻译任务管理”
2. 创建弹窗默认勾选四类内容，默认勾选缺少视频成品的语种
3. 字幕预览是单个真实视频框，不再是左右双框
4. 视频子任务在选声音处显示“去选声音”
5. 失败显示“重新启动”，中断显示“从中断点继续”
6. 服务器重启后 bulk_translate 只被标记 interrupted，不自动续跑
7. 视频封面结果写入独立的原始视频翻译结果表，不混入商品主图
```

- [ ] **Step 7: 提交启动恢复与最终收口**

Run:

```bash
git add appcore/bulk_translate_recovery.py tests/test_bulk_translate_recovery.py web/app.py
git commit -m "feat: mark interrupted medias translation batches on startup"
```
