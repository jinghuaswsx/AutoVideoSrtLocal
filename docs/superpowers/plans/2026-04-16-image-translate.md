# 图片翻译功能实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 接入 Gemini Nano Banana 系列图像生成模型，新增"图片翻译"菜单，支持批量（≤20 张）翻译图中文字为目标语言并保持布局不变。

**Architecture:** 采用与 `subtitle_removal` 同构的模式：`projects` 表存单一批次任务，`state_json` 内嵌每张图 item 状态；独立 runtime 后台串行处理，Socket.IO 推送实时进度；TOS 存储原图和译图；系统级默认 prompt 存 `system_settings` 表。

**Tech Stack:** Flask 3 + Flask-SocketIO + PyMySQL + `google-genai` SDK + TOS SDK + eventlet。

**Spec:** [docs/superpowers/specs/2026-04-16-image-translate-design.md](../specs/2026-04-16-image-translate-design.md)

---

## 说明

**每个 Task 代表一个可独立提交的小单元。**
遵循 TDD：先写失败测试 → 运行确认失败 → 最小实现让测试通过 → 提交。
每个 Task 完成后立刻 commit（中文信息）。
所有路径都是从项目根目录 `g:/Code/AutoVideoSrt` 开始的相对路径。

---

## Task 1: 数据库迁移与项目类型标签

**Files:**
- Create: `db/migrations/2026_04_16_add_image_translate_project_type.sql`
- Modify: `db/schema.sql:24-38`（projects 表 enum）
- Modify: `appcore/settings.py:10-22`（PROJECT_TYPE_LABELS）
- Test: `tests/test_appcore_settings.py`（已有则增量；没有则新建）

- [ ] **Step 1: 编写迁移 SQL**

Create `db/migrations/2026_04_16_add_image_translate_project_type.sql`:
```sql
ALTER TABLE projects 
  MODIFY COLUMN type ENUM(
    'translation','copywriting','video_creation','video_review',
    'text_translate','de_translate','fr_translate',
    'subtitle_removal','translate_lab','image_translate'
  ) NOT NULL DEFAULT 'translation';
```

> ⚠️ 执行前先 `mysql -u root -p auto_video -e "SHOW COLUMNS FROM projects LIKE 'type'"` 确认现有 enum 值，把本次 migration 的 enum 列表与现有对齐（只新增 `image_translate`，不要动其他值）。

- [ ] **Step 2: 更新 schema.sql**

Modify `db/schema.sql` line 27，把 `type` 的 ENUM 列表末尾追加 `'image_translate'`，保证新环境建库即有。

- [ ] **Step 3: 更新 PROJECT_TYPE_LABELS**

Modify `appcore/settings.py`，在 `PROJECT_TYPE_LABELS` 字典里追加：
```python
"image_translate": "图片翻译",
```

- [ ] **Step 4: 写测试验证 label 存在**

Create or modify `tests/test_appcore_settings.py`:
```python
from appcore.settings import PROJECT_TYPE_LABELS


def test_image_translate_label_present():
    assert PROJECT_TYPE_LABELS.get("image_translate") == "图片翻译"
```

- [ ] **Step 5: 运行测试**

Run: `pytest tests/test_appcore_settings.py -q`
Expected: PASS

- [ ] **Step 6: 本地执行 migration**

Run: `python db/migrate.py`（schema.sql 走 IF NOT EXISTS，不会破坏数据），或手动 `mysql ... < db/migrations/2026_04_16_add_image_translate_project_type.sql`。
验证：`mysql -e "SHOW COLUMNS FROM projects LIKE 'type'"` 含 `image_translate`。

- [ ] **Step 7: 提交**

```bash
git add db/migrations/2026_04_16_add_image_translate_project_type.sql db/schema.sql appcore/settings.py tests/test_appcore_settings.py
git commit -m "feat(image-translate): 新增 projects.type=image_translate 枚举"
```

---

## Task 2: task_state.create_image_translate 工厂函数

**Files:**
- Modify: `appcore/task_state.py`（在 `create_subtitle_removal` 之后加 `create_image_translate`）
- Test: `tests/test_appcore_task_state.py`（增量加 cases）

- [ ] **Step 1: 写失败测试**

Add to `tests/test_appcore_task_state.py`:
```python
def test_create_image_translate_minimal(tmp_path):
    from appcore import task_state as ts
    task_id = "tid-img-1"
    task_dir = str(tmp_path / task_id)
    task = ts.create_image_translate(
        task_id,
        task_dir,
        user_id=1,
        preset="cover",
        target_language="de",
        target_language_name="德语",
        model_id="gemini-3-pro-image-preview",
        prompt="把图中文字翻译成德语",
        items=[
            {"idx": 0, "filename": "a.jpg", "src_tos_key": "src/0.jpg"},
            {"idx": 1, "filename": "b.png", "src_tos_key": "src/1.png"},
        ],
    )
    assert task["type"] == "image_translate"
    assert task["status"] == "queued"
    assert task["preset"] == "cover"
    assert task["target_language"] == "de"
    assert task["model_id"] == "gemini-3-pro-image-preview"
    assert len(task["items"]) == 2
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["attempts"] == 0
    assert task["progress"] == {"total": 2, "done": 0, "failed": 0, "running": 0}
    assert task["steps"]["process"] == "pending"
    # 读回来状态一致
    got = ts.get(task_id)
    assert got["preset"] == "cover"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_appcore_task_state.py::test_create_image_translate_minimal -v`
Expected: FAIL (AttributeError: no create_image_translate)

- [ ] **Step 3: 实现工厂函数**

Add to `appcore/task_state.py`（紧跟 `create_subtitle_removal` 之后）:
```python
def create_image_translate(task_id: str, task_dir: str, *,
                            user_id: int,
                            preset: str,
                            target_language: str,
                            target_language_name: str,
                            model_id: str,
                            prompt: str,
                            items: list[dict]) -> dict:
    normalized_items = []
    for idx, raw in enumerate(items):
        normalized_items.append({
            "idx": int(raw.get("idx", idx)),
            "filename": str(raw.get("filename") or ""),
            "src_tos_key": str(raw.get("src_tos_key") or ""),
            "dst_tos_key": "",
            "status": "pending",
            "attempts": 0,
            "error": "",
        })
    task = {
        "id": task_id,
        "type": "image_translate",
        "status": "queued",
        "task_dir": task_dir,
        "preset": preset,
        "target_language": target_language,
        "target_language_name": target_language_name,
        "model_id": model_id,
        "prompt": prompt,
        "display_name": "",
        "original_filename": "",
        "steps": {"prepare": "done", "process": "pending"},
        "step_messages": {"prepare": "", "process": ""},
        "progress": {
            "total": len(normalized_items),
            "done": 0,
            "failed": 0,
            "running": 0,
        },
        "items": normalized_items,
        "error": "",
        "_user_id": user_id,
    }
    with _lock:
        _tasks[task_id] = task
    _db_upsert(task_id, user_id, task, "")
    return task
```

（注：如果项目当前版本的 `_db_upsert` 名字不同，参考 `create_subtitle_removal` 最后一行，改为相同的持久化调用。）

- [ ] **Step 4: 更新 web/store.py 的 facade**

Modify `web/store.py`：导入清单追加 `create_image_translate`，`__all__` 也追加。

- [ ] **Step 5: 运行测试通过**

Run: `pytest tests/test_appcore_task_state.py::test_create_image_translate_minimal -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add appcore/task_state.py web/store.py tests/test_appcore_task_state.py
git commit -m "feat(image-translate): task_state.create_image_translate 工厂"
```

---

## Task 3: Gemini 图像生成封装

**Files:**
- Create: `appcore/gemini_image.py`
- Test: `tests/test_gemini_image.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_gemini_image.py`:
```python
import pytest
from unittest.mock import MagicMock, patch


def _fake_response(image_bytes: bytes, mime: str = "image/png"):
    inline = MagicMock()
    inline.data = image_bytes
    inline.mime_type = mime
    part = MagicMock()
    part.inline_data = inline
    part.text = None
    content = MagicMock()
    content.parts = [part]
    cand = MagicMock()
    cand.content = content
    cand.finish_reason = "STOP"
    resp = MagicMock()
    resp.candidates = [cand]
    resp.usage_metadata = MagicMock(prompt_token_count=10, candidates_token_count=0)
    return resp


def test_generate_image_returns_bytes_and_mime():
    from appcore import gemini_image

    client = MagicMock()
    client.models.generate_content.return_value = _fake_response(b"PNG-BYTES", "image/png")
    with patch.object(gemini_image, "_get_image_client", return_value=client), \
         patch.object(gemini_image, "resolve_config", return_value=("KEY", "gemini-3-pro-image-preview")):
        out, mime = gemini_image.generate_image(
            prompt="翻译",
            source_image=b"RAW",
            source_mime="image/jpeg",
            model="gemini-3-pro-image-preview",
        )
    assert out == b"PNG-BYTES"
    assert mime == "image/png"


def test_generate_image_raises_when_no_image_part():
    from appcore import gemini_image

    # 构造无 inline_data 的响应（触发安全过滤）
    part = MagicMock()
    part.inline_data = None
    part.text = "I can't help with that."
    content = MagicMock()
    content.parts = [part]
    cand = MagicMock()
    cand.content = content
    cand.finish_reason = "SAFETY"
    resp = MagicMock()
    resp.candidates = [cand]
    resp.usage_metadata = None

    client = MagicMock()
    client.models.generate_content.return_value = resp
    with patch.object(gemini_image, "_get_image_client", return_value=client), \
         patch.object(gemini_image, "resolve_config", return_value=("KEY", "gemini-3-pro-image-preview")):
        with pytest.raises(gemini_image.GeminiImageError) as exc:
            gemini_image.generate_image(
                prompt="翻译",
                source_image=b"RAW",
                source_mime="image/jpeg",
                model="gemini-3-pro-image-preview",
            )
        assert "SAFETY" in str(exc.value)
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_gemini_image.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现模块**

Create `appcore/gemini_image.py`:
```python
"""Gemini 图像生成封装（Nano Banana 系列）。

对外暴露 generate_image()，内部按全局 GEMINI_BACKEND 构造 client。
响应里取第一个 inline_data part 作为译图返回。
"""
from __future__ import annotations

import logging
from typing import Any

from google.genai import types as genai_types

from appcore.gemini import _get_client, resolve_config
from appcore.usage_log import record as _record_usage

logger = logging.getLogger(__name__)


IMAGE_MODELS: list[tuple[str, str]] = [
    ("gemini-3-pro-image-preview",   "Nano Banana Pro（高保真）"),
    ("gemini-3.1-flash-image",       "Nano Banana 2（快速）"),
]


def is_valid_image_model(model_id: str) -> bool:
    return any(m[0] == model_id for m in IMAGE_MODELS)


class GeminiImageError(RuntimeError):
    """不可重试的图像生成错误（安全过滤、鉴权、格式等）。"""


class GeminiImageRetryable(RuntimeError):
    """可重试的图像生成错误（网络、429、5xx）。"""


def _get_image_client(api_key: str):
    # 薄包装便于 monkeypatch
    return _get_client(api_key)


def _extract_image_part(resp: Any) -> tuple[bytes, str] | None:
    for cand in getattr(resp, "candidates", None) or []:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data, (getattr(inline, "mime_type", "") or "image/png")
    return None


def _finish_reason(resp: Any) -> str:
    for cand in getattr(resp, "candidates", None) or []:
        reason = getattr(cand, "finish_reason", "")
        if reason:
            return str(reason)
    return ""


def generate_image(
    prompt: str,
    *,
    source_image: bytes,
    source_mime: str,
    model: str,
    user_id: int | None = None,
    project_id: str | None = None,
    service: str = "image_translate",
) -> tuple[bytes, str]:
    """调用 Gemini 图像模型，返回 (译图 bytes, mime)。

    可重试错误抛 GeminiImageRetryable；不可重试抛 GeminiImageError。
    """
    api_key, resolved_model = resolve_config(user_id, service=service, default_model=model)
    if not api_key:
        raise GeminiImageError("Gemini API key 未配置")
    model_id = model or resolved_model

    client = _get_image_client(api_key)
    contents = [
        genai_types.Part.from_bytes(data=source_image, mime_type=source_mime),
        genai_types.Part.from_text(text=prompt),
    ]
    try:
        resp = client.models.generate_content(model=model_id, contents=contents)
    except Exception as e:
        code = getattr(e, "code", None) or getattr(e, "status_code", None)
        if isinstance(code, int) and code in {429, 500, 502, 503, 504}:
            raise GeminiImageRetryable(str(e)) from e
        msg = str(e).lower()
        if "timeout" in msg or "temporarily" in msg:
            raise GeminiImageRetryable(str(e)) from e
        raise GeminiImageError(str(e)) from e

    got = _extract_image_part(resp)
    if got is None:
        reason = _finish_reason(resp) or "NO_IMAGE_RETURNED"
        raise GeminiImageError(f"模型未返回图像（finish_reason={reason}）")

    image_bytes, mime = got

    # usage_logs 记录（容错，失败不冒泡）
    if user_id is not None:
        try:
            meta = getattr(resp, "usage_metadata", None)
            input_tokens = int(getattr(meta, "prompt_token_count", 0) or 0) if meta else None
            output_tokens = int(getattr(meta, "candidates_token_count", 0) or 0) if meta else None
            _record_usage(
                user_id, project_id, service,
                model_name=model_id, success=True,
                input_tokens=input_tokens, output_tokens=output_tokens,
                extra_data={"bytes": len(image_bytes)},
            )
        except Exception:
            logger.debug("gemini_image usage_log 记录失败", exc_info=True)
    return image_bytes, mime
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_gemini_image.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add appcore/gemini_image.py tests/test_gemini_image.py
git commit -m "feat(image-translate): Gemini 图像生成封装（Nano Banana）"
```

---

## Task 4: 系统默认 Prompt 管理

**Files:**
- Create: `appcore/image_translate_settings.py`
- Test: `tests/test_image_translate_settings.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_image_translate_settings.py`:
```python
def test_get_default_prompts_bootstraps_defaults(db):
    from appcore import image_translate_settings as its
    prompts = its.get_default_prompts()
    assert "cover" in prompts and "detail" in prompts
    assert "{target_language_name}" in prompts["cover"]
    assert "{target_language_name}" in prompts["detail"]


def test_update_and_read_cover_prompt(db):
    from appcore import image_translate_settings as its
    its.update_prompt("cover", "自定义封面 prompt {target_language_name}")
    assert its.get_default_prompts()["cover"] == "自定义封面 prompt {target_language_name}"


def test_render_prompt_replaces_language_name():
    from appcore import image_translate_settings as its
    out = its.render_prompt(
        "把文字翻译成 {target_language_name}，保持布局。{other}",
        target_language_name="日语",
    )
    assert out.startswith("把文字翻译成 日语")
    assert "{other}" in out  # 其他占位符保留
```

（`db` fixture 已有，见 `tests/conftest.py`。）

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_image_translate_settings.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现模块**

Create `appcore/image_translate_settings.py`:
```python
"""图片翻译默认 prompt 管理，使用 system_settings 表。"""
from __future__ import annotations

from appcore.db import execute, query_one


_KEY_COVER = "image_translate.prompt_cover"
_KEY_DETAIL = "image_translate.prompt_detail"

_DEFAULT_TEMPLATE = (
    "把图中出现的所有文字翻译成 {target_language_name}，"
    "保持原有布局、字体风格、颜色、图像内容不变，"
    "只替换文字本身。对于装饰性排版或特殊字体，尽量保持视觉一致。"
)

_DEFAULTS = {
    "cover": _DEFAULT_TEMPLATE,
    "detail": _DEFAULT_TEMPLATE,
}


def _read(key: str) -> str | None:
    row = query_one("SELECT `value` FROM system_settings WHERE `key`=%s", (key,))
    return (row.get("value") or "") if row else None


def _write(key: str, value: str) -> None:
    execute(
        "INSERT INTO system_settings (`key`, `value`) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE `value`=VALUES(`value`)",
        (key, value),
    )


def get_default_prompts() -> dict[str, str]:
    """返回 {cover, detail} 两条默认 prompt；不存在则写入内置默认后返回。"""
    cover = _read(_KEY_COVER)
    if cover is None:
        _write(_KEY_COVER, _DEFAULTS["cover"])
        cover = _DEFAULTS["cover"]
    detail = _read(_KEY_DETAIL)
    if detail is None:
        _write(_KEY_DETAIL, _DEFAULTS["detail"])
        detail = _DEFAULTS["detail"]
    return {"cover": cover, "detail": detail}


def update_prompt(preset: str, value: str) -> None:
    if preset not in _DEFAULTS:
        raise ValueError("preset must be cover or detail")
    key = _KEY_COVER if preset == "cover" else _KEY_DETAIL
    _write(key, value)


def render_prompt(template: str, *, target_language_name: str) -> str:
    """仅替换 {target_language_name}；其他占位符原样保留。"""
    return template.replace("{target_language_name}", target_language_name)
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_image_translate_settings.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add appcore/image_translate_settings.py tests/test_image_translate_settings.py
git commit -m "feat(image-translate): 系统默认 prompt 管理"
```

---

## Task 5: Admin 页面 prompt 编辑

**Files:**
- Modify: `web/routes/admin.py`（新增 2 个 API）
- Modify: `web/templates/admin.html`（新增一块区域）
- Test: `tests/test_admin_routes.py`（增量）

- [ ] **Step 1: 写失败测试**

Add to `tests/test_admin_routes.py`:
```python
def test_admin_get_image_translate_prompts_requires_admin(client, user_login):
    resp = client.get("/api/admin/image-translate/prompts")
    assert resp.status_code == 403


def test_admin_image_translate_prompts_read_and_write(client, admin_login):
    r = client.get("/api/admin/image-translate/prompts")
    assert r.status_code == 200
    data = r.get_json()
    assert "cover" in data and "detail" in data

    r2 = client.post(
        "/api/admin/image-translate/prompts",
        json={"preset": "cover", "value": "新的封面模板 {target_language_name}"},
    )
    assert r2.status_code == 200
    r3 = client.get("/api/admin/image-translate/prompts")
    assert r3.get_json()["cover"] == "新的封面模板 {target_language_name}"
```

> `admin_login` / `user_login` fixture 若不存在，参考 `tests/conftest.py` 里已有同类 fixture 自行补齐（登录一个 admin / 普通 user）。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_admin_routes.py -v -k image_translate`
Expected: FAIL

- [ ] **Step 3: 实现 API**

Add to `web/routes/admin.py`（注意模块内已有 `@login_required` + `admin_required` 装饰器；沿用现有模式）:
```python
@bp.route("/api/admin/image-translate/prompts", methods=["GET"])
@login_required
@admin_required
def get_image_translate_prompts():
    from appcore.image_translate_settings import get_default_prompts
    return jsonify(get_default_prompts())


@bp.route("/api/admin/image-translate/prompts", methods=["POST"])
@login_required
@admin_required
def set_image_translate_prompt():
    from appcore.image_translate_settings import update_prompt
    body = request.get_json(silent=True) or {}
    preset = (body.get("preset") or "").strip().lower()
    value = (body.get("value") or "").strip()
    if preset not in {"cover", "detail"}:
        return jsonify({"error": "preset must be cover or detail"}), 400
    if not value:
        return jsonify({"error": "value required"}), 400
    update_prompt(preset, value)
    return jsonify({"ok": True})
```

- [ ] **Step 4: 加管理页面区域**

Modify `web/templates/admin.html`，在最后一个 `<section>` 后追加：
```html
<section class="card" id="imageTranslatePromptsCard">
  <h3>图片翻译默认提示词</h3>
  <p class="hint">支持占位符 <code>{target_language_name}</code>（提交时替换为用户选择的目标语言中文名）</p>
  <div class="form-row">
    <label>封面图翻译 prompt</label>
    <textarea id="imgTransCover" rows="4"></textarea>
    <button class="btn btn-primary" id="imgTransSaveCover">保存封面 prompt</button>
  </div>
  <div class="form-row" style="margin-top:16px;">
    <label>产品详情图翻译 prompt</label>
    <textarea id="imgTransDetail" rows="4"></textarea>
    <button class="btn btn-primary" id="imgTransSaveDetail">保存详情 prompt</button>
  </div>
</section>
<script>
(function(){
  function fetchPrompts() {
    return fetch('/api/admin/image-translate/prompts', {credentials:'same-origin'})
      .then(r => r.json()).then(data => {
        document.getElementById('imgTransCover').value = data.cover || '';
        document.getElementById('imgTransDetail').value = data.detail || '';
      });
  }
  function save(preset, id) {
    return fetch('/api/admin/image-translate/prompts', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({preset, value: document.getElementById(id).value})
    }).then(r => r.json()).then(data => {
      if (data.error) alert(data.error); else alert('已保存');
    });
  }
  document.getElementById('imgTransSaveCover').onclick = () => save('cover','imgTransCover');
  document.getElementById('imgTransSaveDetail').onclick = () => save('detail','imgTransDetail');
  fetchPrompts();
})();
</script>
```

- [ ] **Step 5: 运行测试通过**

Run: `pytest tests/test_admin_routes.py -v -k image_translate`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add web/routes/admin.py web/templates/admin.html tests/test_admin_routes.py
git commit -m "feat(image-translate): admin 管理页支持 prompt 编辑"
```

---

## Task 6: Runtime（串行处理 + 重试）

**Files:**
- Create: `appcore/image_translate_runtime.py`
- Test: `tests/test_image_translate_runtime.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_image_translate_runtime.py`:
```python
from unittest.mock import patch, MagicMock
import pytest


def _fake_task(items):
    return {
        "id": "t-img-1",
        "type": "image_translate",
        "status": "queued",
        "task_dir": "/tmp/t-img-1",
        "preset": "cover",
        "target_language": "de",
        "target_language_name": "德语",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "...",
        "items": items,
        "progress": {"total": len(items), "done": 0, "failed": 0, "running": 0},
        "steps": {"prepare": "done", "process": "pending"},
        "step_messages": {"prepare": "", "process": ""},
        "error": "",
        "_user_id": 1,
    }


def _item(idx, src="src/0.jpg", status="pending"):
    return {
        "idx": idx, "filename": f"a{idx}.jpg", "src_tos_key": src,
        "dst_tos_key": "", "status": status, "attempts": 0, "error": "",
    }


def test_runtime_processes_all_items_successfully(monkeypatch):
    from appcore import image_translate_runtime as rt
    from web import store

    task = _fake_task([_item(0), _item(1)])
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt.tos_clients, "download_object", return_value=b"IMG"), \
         patch.object(rt.tos_clients, "put_object", return_value=None), \
         patch.object(rt.gemini_image, "generate_image", return_value=(b"OUT", "image/png")):
        bus = MagicMock()
        rt.ImageTranslateRuntime(bus=bus, user_id=1).start("t-img-1")

    # 每张图都触发了 generate_image 一次，progress.done == 2
    assert rt.gemini_image.generate_image.call_count == 2


def test_runtime_retries_on_retryable_error(monkeypatch):
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(0)])
    mock_gen = MagicMock(side_effect=[GeminiImageRetryable("429"), GeminiImageRetryable("500"), (b"OK", "image/png")])
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_object", return_value=b"IMG"), \
         patch.object(rt.tos_clients, "put_object", return_value=None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=mock_gen.side_effect):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "done"
    assert task["items"][0]["attempts"] == 3


def test_runtime_gives_up_after_3_retries(monkeypatch):
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageRetryable

    task = _fake_task([_item(0)])
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_object", return_value=b"IMG"), \
         patch.object(rt.tos_clients, "put_object", return_value=None), \
         patch.object(rt.gemini_image, "generate_image",
                       side_effect=GeminiImageRetryable("timeout")):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "failed"
    assert task["items"][0]["attempts"] == 3


def test_runtime_non_retryable_marks_failed_immediately(monkeypatch):
    from appcore import image_translate_runtime as rt
    from web import store
    from appcore.gemini_image import GeminiImageError

    task = _fake_task([_item(0), _item(1)])
    calls = []
    def side(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise GeminiImageError("SAFETY")
        return b"OUT", "image/png"
    with patch.object(store, "get", return_value=task), \
         patch.object(store, "update"), \
         patch.object(rt, "_sleep"), \
         patch.object(rt.tos_clients, "download_object", return_value=b"IMG"), \
         patch.object(rt.tos_clients, "put_object", return_value=None), \
         patch.object(rt.gemini_image, "generate_image", side_effect=side):
        rt.ImageTranslateRuntime(bus=MagicMock(), user_id=1).start("t-img-1")

    assert task["items"][0]["status"] == "failed"
    assert task["items"][0]["attempts"] == 1  # 无重试
    assert task["items"][1]["status"] == "done"  # 下一张继续
```

> 若仓库里 `tos_clients` 实际函数名不是 `download_object` / `put_object`，运行前先搜索确认（下方实现里会以实际函数名为准；测试里 mock 时保持和实现一致）。

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_image_translate_runtime.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 runtime**

Create `appcore/image_translate_runtime.py`:
```python
"""图片翻译后台 runtime：串行处理 items，自动重试 3 次，失败不中断。"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from appcore import gemini_image, tos_clients
from appcore.events import Event, EventBus
from web import store

logger = logging.getLogger(__name__)


_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0  # 秒


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _update_progress(task: dict) -> None:
    total = len(task.get("items") or [])
    done = sum(1 for it in task["items"] if it["status"] == "done")
    failed = sum(1 for it in task["items"] if it["status"] == "failed")
    running = sum(1 for it in task["items"] if it["status"] == "running")
    task["progress"] = {"total": total, "done": done, "failed": failed, "running": running}


class ImageTranslateRuntime:
    def __init__(self, *, bus: EventBus, user_id: int | None = None) -> None:
        self.bus = bus
        self.user_id = user_id

    def start(self, task_id: str) -> None:
        task = store.get(task_id)
        if not task or task.get("type") != "image_translate":
            logger.warning("image_translate runtime: task not found: %s", task_id)
            return

        task["status"] = "running"
        task["steps"]["process"] = "running"
        store.update(task_id, status="running", steps=task["steps"])

        items = task.get("items") or []
        for idx in range(len(items)):
            if items[idx]["status"] in {"done", "failed"}:
                continue  # 已完成或已判失败（恢复场景）
            self._process_one(task, task_id, idx)

        task["status"] = "done"
        task["steps"]["process"] = "done"
        _update_progress(task)
        store.update(
            task_id,
            status="done",
            steps=task["steps"],
            progress=task["progress"],
            items=task["items"],
        )
        self.bus.publish(Event("image_translate:task_done",
                                {"task_id": task_id, "status": "done"}))

    def _process_one(self, task: dict, task_id: str, idx: int) -> None:
        item = task["items"][idx]
        item["status"] = "running"
        _update_progress(task)
        store.update(task_id, items=task["items"], progress=task["progress"])
        self._emit_item(task_id, item)

        attempts = 0
        last_err: Exception | None = None
        while attempts < _MAX_ATTEMPTS:
            attempts += 1
            item["attempts"] = attempts
            try:
                src_bytes = tos_clients.download_object(item["src_tos_key"])
                mime = self._guess_mime_from_key(item["src_tos_key"])
                out_bytes, out_mime = gemini_image.generate_image(
                    prompt=task["prompt"],
                    source_image=src_bytes,
                    source_mime=mime,
                    model=task["model_id"],
                    user_id=task.get("_user_id"),
                    project_id=task_id,
                    service="image_translate",
                )
                dst_key = self._build_dst_key(task, idx, out_mime)
                tos_clients.put_object(dst_key, out_bytes, content_type=out_mime)

                item["status"] = "done"
                item["dst_tos_key"] = dst_key
                item["error"] = ""
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            except gemini_image.GeminiImageError as e:
                item["status"] = "failed"
                item["error"] = str(e)
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return  # 不可重试，直接停
            except gemini_image.GeminiImageRetryable as e:
                last_err = e
                if attempts < _MAX_ATTEMPTS:
                    _sleep(_BACKOFF_BASE * (2 ** (attempts - 1)))
                    continue
                # 重试次数耗尽
                item["status"] = "failed"
                item["error"] = f"重试 {attempts} 次仍失败：{e}"
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return
            except Exception as e:  # 未知 → 当作可重试
                last_err = e
                if attempts < _MAX_ATTEMPTS:
                    _sleep(_BACKOFF_BASE * (2 ** (attempts - 1)))
                    continue
                item["status"] = "failed"
                item["error"] = f"未知错误：{e}"
                _update_progress(task)
                store.update(task_id, items=task["items"], progress=task["progress"])
                self._emit_item(task_id, item)
                self._emit_progress(task_id, task["progress"])
                return

    @staticmethod
    def _build_dst_key(task: dict, idx: int, mime: str) -> str:
        ext = "png"
        if mime == "image/jpeg":
            ext = "jpg"
        elif mime == "image/webp":
            ext = "webp"
        uid = task.get("_user_id") or 0
        return f"artifacts/image_translate/{uid}/{task['id']}/out_{idx}.{ext}"

    @staticmethod
    def _guess_mime_from_key(object_key: str) -> str:
        lower = object_key.lower()
        if lower.endswith(".jpg") or lower.endswith(".jpeg"):
            return "image/jpeg"
        if lower.endswith(".png"):
            return "image/png"
        if lower.endswith(".webp"):
            return "image/webp"
        return "application/octet-stream"

    def _emit_item(self, task_id: str, item: dict) -> None:
        self.bus.publish(Event("image_translate:item_updated", {
            "task_id": task_id,
            "idx": item["idx"],
            "status": item["status"],
            "attempts": item["attempts"],
            "error": item["error"],
            "dst_tos_key": item.get("dst_tos_key") or "",
        }))

    def _emit_progress(self, task_id: str, progress: dict) -> None:
        self.bus.publish(Event("image_translate:progress", {
            "task_id": task_id, **progress,
        }))
```

> 实现时注意两点：
> 1. `tos_clients` 里如果没有 `download_object` / `put_object`，找实际等价函数（参考 `subtitle_removal_runtime.py` 里用的下载/上传），并在测试 mock 时保持一致。
> 2. 如果 `EventBus`/`Event` 的具体 API 与 `subtitle_removal_runtime.py` 不同，按该模块实际用法对齐。

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_image_translate_runtime.py -v`
Expected: PASS（如有 mock 函数名不匹配，修到绿色）

- [ ] **Step 5: 提交**

```bash
git add appcore/image_translate_runtime.py tests/test_image_translate_runtime.py
git commit -m "feat(image-translate): runtime 串行处理与重试"
```

---

## Task 7: Runner service 包装 + 重启恢复

**Files:**
- Create: `web/services/image_translate_runner.py`
- Test: `tests/test_image_translate_runner.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_image_translate_runner.py`:
```python
from unittest.mock import patch, MagicMock


def test_is_running_initially_false():
    from web.services import image_translate_runner as runner
    assert runner.is_running("nope") is False


def test_start_spawns_thread_and_tracks_running():
    from web.services import image_translate_runner as runner
    with patch.object(runner, "ImageTranslateRuntime") as Rt, \
         patch("threading.Thread") as Thr:
        instance = MagicMock()
        Rt.return_value = instance
        thread = MagicMock()
        Thr.return_value = thread
        ok = runner.start("tid-1", user_id=1)
        assert ok is True
        thread.start.assert_called_once()


def test_start_ignores_duplicate():
    from web.services import image_translate_runner as runner
    runner._running_tasks.add("dup-1")
    try:
        assert runner.start("dup-1", user_id=1) is False
    finally:
        runner._running_tasks.discard("dup-1")


def test_resume_picks_up_queued_and_running_rows():
    from web.services import image_translate_runner as runner
    rows = [
        {"id": "a", "user_id": 1, "status": "queued", "state_json": '{"type":"image_translate","status":"queued","items":[{"status":"pending","idx":0}]}'},
        {"id": "b", "user_id": 2, "status": "running", "state_json": '{"type":"image_translate","status":"running","items":[{"status":"done","idx":0},{"status":"pending","idx":1}]}'},
    ]
    with patch.object(runner, "db_query", return_value=rows), \
         patch.object(runner, "start") as st:
        restored = runner.resume_inflight_tasks()
    assert set(restored) == {"a", "b"}
    assert st.call_count == 2
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_image_translate_runner.py -v`
Expected: FAIL

- [ ] **Step 3: 实现 runner**

Create `web/services/image_translate_runner.py`:
```python
from __future__ import annotations

import json
import threading

from appcore.db import query as db_query
from appcore.events import EventBus
from appcore.image_translate_runtime import ImageTranslateRuntime
from web.extensions import socketio

_running_tasks: set[str] = set()
_running_tasks_lock = threading.Lock()


def _make_socketio_handler(task_id: str):
    def handler(event):
        socketio.emit(event.type, event.payload, room=task_id)
    return handler


def is_running(task_id: str) -> bool:
    with _running_tasks_lock:
        return task_id in _running_tasks


def start(task_id: str, user_id: int | None = None) -> bool:
    with _running_tasks_lock:
        if task_id in _running_tasks:
            return False
        _running_tasks.add(task_id)

    bus = EventBus()
    bus.subscribe(_make_socketio_handler(task_id))
    runtime = ImageTranslateRuntime(bus=bus, user_id=user_id)

    def run():
        try:
            runtime.start(task_id)
        finally:
            with _running_tasks_lock:
                _running_tasks.discard(task_id)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return True


def resume_inflight_tasks() -> list[str]:
    """服务重启时扫描未完成的 image_translate 任务并重新拉起。"""
    restored: list[str] = []
    try:
        rows = db_query(
            """
            SELECT id, user_id, status, state_json
            FROM projects
            WHERE type='image_translate'
              AND deleted_at IS NULL
              AND status IN ('queued','running')
            ORDER BY created_at ASC
            """,
            (),
        )
    except Exception:
        return restored

    for row in rows:
        tid = (row.get("id") or "").strip()
        if not tid or is_running(tid):
            continue
        state_json = row.get("state_json") or ""
        try:
            state = json.loads(state_json) if state_json else None
        except Exception:
            state = None
        if not state or state.get("type") != "image_translate":
            continue
        items = state.get("items") or []
        if items and all(it.get("status") in {"done", "failed"} for it in items):
            continue  # 没有 pending
        if start(tid, user_id=row.get("user_id")):
            restored.append(tid)
    return restored
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_image_translate_runner.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web/services/image_translate_runner.py tests/test_image_translate_runner.py
git commit -m "feat(image-translate): runner service 与重启恢复"
```

---

## Task 8: Routes - 上传 bootstrap + complete + 状态查询

**Files:**
- Create: `web/routes/image_translate.py`（本 Task 仅核心 3 个接口）
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 写失败测试**

Create `tests/test_image_translate_routes.py`:
```python
from unittest.mock import patch


def test_bootstrap_returns_signed_urls(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    monkeypatch.setattr(r.tos_clients, "is_tos_configured", lambda: True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_upload_url", lambda k: f"https://tos/{k}?sig=1")
    resp = client.post("/api/image-translate/upload/bootstrap", json={
        "count": 2,
        "files": [
            {"filename": "a.jpg", "size": 100, "content_type": "image/jpeg"},
            {"filename": "b.png", "size": 200, "content_type": "image/png"},
        ],
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["task_id"]
    assert len(data["uploads"]) == 2
    assert data["uploads"][0]["upload_url"].startswith("https://tos/")


def test_bootstrap_rejects_over_20(client, user_login):
    files = [{"filename": f"{i}.jpg", "size":1,"content_type":"image/jpeg"} for i in range(21)]
    resp = client.post("/api/image-translate/upload/bootstrap",
                       json={"count": 21, "files": files})
    assert resp.status_code == 400


def test_complete_creates_task(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    monkeypatch.setattr(r.tos_clients, "is_tos_configured", lambda: True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_upload_url", lambda k: "https://tos/x")
    monkeypatch.setattr(r.tos_clients, "object_exists", lambda k: True)
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: True)

    # 先 bootstrap
    b = client.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename": "a.jpg", "size": 100, "content_type":"image/jpeg"}],
    })
    bd = b.get_json()
    tid = bd["task_id"]
    key = bd["uploads"][0]["object_key"]

    # 再 complete
    resp = client.post("/api/image-translate/upload/complete", json={
        "task_id": tid,
        "preset": "cover",
        "target_language": "de",
        "model_id": "gemini-3-pro-image-preview",
        "prompt": "把图中文字翻译成 {target_language_name}",
        "uploaded": [{"idx": 0, "object_key": key, "filename": "a.jpg", "size": 100}],
    })
    assert resp.status_code == 201
    j = resp.get_json()
    assert j["task_id"] == tid


def test_get_state(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    monkeypatch.setattr(r.tos_clients, "is_tos_configured", lambda: True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_upload_url", lambda k: "https://tos/x")
    monkeypatch.setattr(r.tos_clients, "object_exists", lambda k: True)
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: True)
    b = client.post("/api/image-translate/upload/bootstrap", json={
        "count": 1,
        "files": [{"filename":"a.jpg","size":1,"content_type":"image/jpeg"}],
    })
    tid = b.get_json()["task_id"]
    key = b.get_json()["uploads"][0]["object_key"]
    client.post("/api/image-translate/upload/complete", json={
        "task_id": tid, "preset":"cover", "target_language":"de",
        "model_id":"gemini-3-pro-image-preview",
        "prompt":"... {target_language_name} ...",
        "uploaded":[{"idx":0,"object_key":key,"filename":"a.jpg","size":1}],
    })
    resp = client.get(f"/api/image-translate/{tid}")
    assert resp.status_code == 200
    state = resp.get_json()
    assert state["id"] == tid
    assert state["preset"] == "cover"
    assert state["target_language_name"] == "德语"
    assert len(state["items"]) == 1
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: FAIL

- [ ] **Step 3: 实现核心路由**

Create `web/routes/image_translate.py`:
```python
from __future__ import annotations

import threading
import uuid

from flask import Blueprint, abort, jsonify, render_template, request
from flask_login import current_user, login_required

from appcore import medias, task_state, tos_clients
from appcore.db import query_one as db_query_one
from appcore.gemini_image import IMAGE_MODELS, is_valid_image_model
from appcore.image_translate_settings import get_default_prompts, render_prompt
from web import store
from web.services import image_translate_runner

bp = Blueprint("image_translate", __name__)

_MAX_ITEMS = 20
_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}

_upload_guard = threading.Lock()
_upload_reservations: dict[str, dict] = {}


def _get_owned_task(task_id: str) -> dict:
    task = store.get(task_id)
    if (
        not task
        or task.get("_user_id") != current_user.id
        or task.get("type") != "image_translate"
        or (task.get("status") or "").strip() == "deleted"
        or task.get("deleted_at")
    ):
        abort(404)
    return task


def _target_language_name(code: str) -> str:
    row = db_query_one(
        "SELECT name_zh FROM media_languages WHERE code=%s AND enabled=1",
        (code,),
    )
    if not row:
        return code
    return row["name_zh"] or code


def _start_runner(task_id: str, user_id: int) -> bool:
    return image_translate_runner.start(task_id, user_id=user_id)


def _build_source_object_key(user_id: int, task_id: str, idx: int, ext: str) -> str:
    ext = ext.lower().lstrip(".") or "jpg"
    return f"uploads/image_translate/{user_id}/{task_id}/src_{idx}.{ext}"


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
        "progress": dict(task.get("progress") or {}),
        "items": list(task.get("items") or []),
        "steps": dict(task.get("steps") or {}),
        "error": task.get("error") or "",
    }


@bp.route("/api/image-translate/models", methods=["GET"])
@login_required
def api_models():
    from appcore.api_keys import resolve_extra
    extra = resolve_extra(current_user.id, "image_translate") or {}
    return jsonify({
        "items": [{"id": mid, "name": label} for mid, label in IMAGE_MODELS],
        "default_model_id": (extra.get("default_model_id") or "").strip(),
    })


@bp.route("/api/image-translate/system-prompts", methods=["GET"])
@login_required
def api_system_prompts():
    return jsonify(get_default_prompts())


@bp.route("/api/image-translate/upload/bootstrap", methods=["POST"])
@login_required
def api_upload_bootstrap():
    if not tos_clients.is_tos_configured():
        return jsonify({"error": "TOS 未配置"}), 503
    body = request.get_json(silent=True) or {}
    files = body.get("files") or []
    if not files:
        return jsonify({"error": "files 不能为空"}), 400
    if len(files) > _MAX_ITEMS:
        return jsonify({"error": f"单次最多 {_MAX_ITEMS} 张"}), 400

    task_id = str(uuid.uuid4())
    uploads = []
    with _upload_guard:
        for idx, f in enumerate(files):
            filename = (f.get("filename") or "").strip()
            if not filename:
                return jsonify({"error": f"第 {idx} 张缺少 filename"}), 400
            ext = "." + (filename.rsplit(".", 1)[-1].lower() if "." in filename else "")
            if ext not in _ALLOWED_EXT:
                return jsonify({"error": f"不支持的图片格式: {filename}"}), 400
            key = _build_source_object_key(current_user.id, task_id, idx, ext)
            uploads.append({
                "idx": idx,
                "object_key": key,
                "upload_url": tos_clients.generate_signed_upload_url(key),
            })
        _upload_reservations[task_id] = {
            "user_id": current_user.id,
            "files": [{"idx": u["idx"], "object_key": u["object_key"], "filename": files[u["idx"]].get("filename")} for u in uploads],
        }
    return jsonify({"task_id": task_id, "uploads": uploads})


@bp.route("/api/image-translate/upload/complete", methods=["POST"])
@login_required
def api_upload_complete():
    body = request.get_json(silent=True) or {}
    task_id = (body.get("task_id") or "").strip()
    preset = (body.get("preset") or "").strip().lower()
    lang_code = (body.get("target_language") or "").strip().lower()
    model_id = (body.get("model_id") or "").strip()
    prompt_tpl = (body.get("prompt") or "").strip()
    uploaded = body.get("uploaded") or []

    with _upload_guard:
        rv = _upload_reservations.get(task_id)
    if not rv or rv["user_id"] != current_user.id:
        return jsonify({"error": "task_id 非法或过期"}), 403
    if preset not in {"cover", "detail"}:
        return jsonify({"error": "preset 必须是 cover 或 detail"}), 400
    if not medias.is_valid_language(lang_code) or lang_code == "en":
        return jsonify({"error": "目标语言不支持"}), 400
    if not is_valid_image_model(model_id):
        return jsonify({"error": "模型不支持"}), 400
    if not prompt_tpl:
        return jsonify({"error": "prompt 不能为空"}), 400
    if not uploaded:
        return jsonify({"error": "uploaded 不能为空"}), 400

    # 校验每个上传项对应 bootstrap 里预定的 key，且对象实际存在
    reserved = {f["idx"]: f for f in rv["files"]}
    items = []
    for u in uploaded:
        idx = int(u.get("idx"))
        key = (u.get("object_key") or "").strip()
        filename = (u.get("filename") or reserved.get(idx, {}).get("filename") or "").strip()
        if idx not in reserved or reserved[idx]["object_key"] != key:
            return jsonify({"error": f"上传项不匹配 idx={idx}"}), 400
        if not tos_clients.object_exists(key):
            return jsonify({"error": f"对象不存在 idx={idx}"}), 400
        items.append({"idx": idx, "filename": filename, "src_tos_key": key})

    lang_name = _target_language_name(lang_code)
    final_prompt = render_prompt(prompt_tpl, target_language_name=lang_name)
    task_dir = ""  # 本模块不用本地目录
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
    )
    # 记用户偏好
    try:
        from appcore.api_keys import upsert_extra
        upsert_extra(current_user.id, "image_translate", {"default_model_id": model_id})
    except Exception:
        pass

    with _upload_guard:
        _upload_reservations.pop(task_id, None)

    _start_runner(task_id, current_user.id)
    return jsonify({"task_id": task_id}), 201


@bp.route("/api/image-translate/<task_id>", methods=["GET"])
@login_required
def api_state(task_id: str):
    task = _get_owned_task(task_id)
    return jsonify(_state_payload(task))
```

> 注意：`api_keys.upsert_extra` 如果项目里函数名不同（如 `set_extra` / `update_extra`），按实际 API 名改。

- [ ] **Step 4: 把蓝图注册进 app**

Modify `web/app.py`：在其他 `register_blueprint` 附近追加：
```python
from web.routes.image_translate import bp as image_translate_bp
app.register_blueprint(image_translate_bp)
```

- [ ] **Step 5: 运行测试通过**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: PASS

- [ ] **Step 6: 提交**

```bash
git add web/routes/image_translate.py web/app.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): 上传 bootstrap/complete 与状态查询路由"
```

---

## Task 9: Routes - 产物获取 + 单张下载

**Files:**
- Modify: `web/routes/image_translate.py`
- Test: `tests/test_image_translate_routes.py`（增量）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_image_translate_routes.py`:
```python
def _prep_task(client, user_login, monkeypatch, with_done=True):
    from web.routes import image_translate as r
    monkeypatch.setattr(r.tos_clients, "is_tos_configured", lambda: True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_upload_url", lambda k: "https://tos/x")
    monkeypatch.setattr(r.tos_clients, "object_exists", lambda k: True)
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: True)
    b = client.post("/api/image-translate/upload/bootstrap", json={
        "count":1,
        "files":[{"filename":"a.jpg","size":1,"content_type":"image/jpeg"}],
    }).get_json()
    tid = b["task_id"]
    client.post("/api/image-translate/upload/complete", json={
        "task_id": tid, "preset":"cover","target_language":"de",
        "model_id":"gemini-3-pro-image-preview",
        "prompt":"... {target_language_name} ...",
        "uploaded":[{"idx":0,"object_key":b["uploads"][0]["object_key"],"filename":"a.jpg","size":1}],
    })
    # 手动标 done（模拟 runner 完成）
    from web import store
    task = store.get(tid)
    if with_done:
        task["items"][0]["status"] = "done"
        task["items"][0]["dst_tos_key"] = "artifacts/image_translate/1/{}/out_0.png".format(tid)
        task["progress"]["done"] = 1
    return tid


def test_source_artifact_redirects(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(client, user_login, monkeypatch, with_done=False)
    monkeypatch.setattr(r.tos_clients, "generate_signed_download_url", lambda k, expires=None: f"https://tos-dl/{k}")
    resp = client.get(f"/api/image-translate/{tid}/artifact/source/0", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["Location"].startswith("https://tos-dl/")


def test_result_artifact_404_when_not_done(client, user_login, monkeypatch):
    tid = _prep_task(client, user_login, monkeypatch, with_done=False)
    resp = client.get(f"/api/image-translate/{tid}/artifact/result/0", follow_redirects=False)
    assert resp.status_code == 404


def test_result_download_redirects_when_done(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(client, user_login, monkeypatch, with_done=True)
    monkeypatch.setattr(r.tos_clients, "generate_signed_download_url", lambda k, expires=None: f"https://tos-dl/{k}")
    resp = client.get(f"/api/image-translate/{tid}/download/result/0", follow_redirects=False)
    assert resp.status_code == 302
    assert "out_0.png" in resp.headers["Location"]
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_image_translate_routes.py -v -k "artifact or download"`
Expected: FAIL

- [ ] **Step 3: 实现路由**

Append to `web/routes/image_translate.py`:
```python
from flask import redirect


def _get_item(task: dict, idx: int) -> dict | None:
    for it in task.get("items") or []:
        if int(it.get("idx")) == int(idx):
            return it
    return None


@bp.route("/api/image-translate/<task_id>/artifact/source/<int:idx>", methods=["GET"])
@login_required
def api_source_artifact(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item or not item.get("src_tos_key"):
        abort(404)
    return redirect(tos_clients.generate_signed_download_url(item["src_tos_key"]))


@bp.route("/api/image-translate/<task_id>/artifact/result/<int:idx>", methods=["GET"])
@login_required
def api_result_artifact(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item or item.get("status") != "done" or not item.get("dst_tos_key"):
        abort(404)
    return redirect(tos_clients.generate_signed_download_url(item["dst_tos_key"]))


@bp.route("/api/image-translate/<task_id>/download/result/<int:idx>", methods=["GET"])
@login_required
def api_download_result(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item or item.get("status") != "done" or not item.get("dst_tos_key"):
        abort(404)
    return redirect(tos_clients.generate_signed_download_url(item["dst_tos_key"]))
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_image_translate_routes.py -v -k "artifact or download"`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): 原图/译图 artifact 与单张下载"
```

---

## Task 10: Routes - 单张重试 + zip 打包 + 删除

**Files:**
- Modify: `web/routes/image_translate.py`
- Test: `tests/test_image_translate_routes.py`（增量）

- [ ] **Step 1: 写失败测试**

Append to `tests/test_image_translate_routes.py`:
```python
def test_retry_failed_item_resets_and_triggers_runner(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(client, user_login, monkeypatch, with_done=False)
    from web import store
    task = store.get(tid)
    task["items"][0]["status"] = "failed"
    task["items"][0]["attempts"] = 3
    task["items"][0]["error"] = "timeout"
    called = {}
    monkeypatch.setattr(r, "_start_runner", lambda tid, uid: called.setdefault("ok", True))
    resp = client.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 202
    assert task["items"][0]["status"] == "pending"
    assert task["items"][0]["attempts"] == 0
    assert task["items"][0]["error"] == ""
    assert called.get("ok") is True


def test_retry_rejects_non_failed_item(client, user_login, monkeypatch):
    tid = _prep_task(client, user_login, monkeypatch, with_done=True)  # item status=done
    resp = client.post(f"/api/image-translate/{tid}/retry/0")
    assert resp.status_code == 409


def test_zip_download_contains_done_items(client, user_login, monkeypatch):
    import io, zipfile
    from web.routes import image_translate as r
    tid = _prep_task(client, user_login, monkeypatch, with_done=True)
    monkeypatch.setattr(r.tos_clients, "download_object", lambda k: b"BYTES-" + k.encode())
    resp = client.get(f"/api/image-translate/{tid}/download/zip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.data))
    names = zf.namelist()
    assert len(names) == 1
    assert names[0].endswith(".png")


def test_delete_task(client, user_login, monkeypatch):
    from web.routes import image_translate as r
    tid = _prep_task(client, user_login, monkeypatch, with_done=True)
    monkeypatch.setattr(r.tos_clients, "delete_object", lambda k: None)
    resp = client.delete(f"/api/image-translate/{tid}")
    assert resp.status_code == 204
    # 再查 → 404
    resp2 = client.get(f"/api/image-translate/{tid}")
    assert resp2.status_code == 404
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_image_translate_routes.py -v -k "retry or zip or delete"`
Expected: FAIL

- [ ] **Step 3: 实现路由**

Append to `web/routes/image_translate.py`:
```python
import io
import os
import zipfile
from datetime import datetime

from appcore.db import execute as db_execute
from flask import Response


@bp.route("/api/image-translate/<task_id>/retry/<int:idx>", methods=["POST"])
@login_required
def api_retry_item(task_id: str, idx: int):
    task = _get_owned_task(task_id)
    item = _get_item(task, idx)
    if not item:
        abort(404)
    if item.get("status") != "failed":
        return jsonify({"error": "只有 failed 的图可以重试"}), 409
    item["status"] = "pending"
    item["attempts"] = 0
    item["error"] = ""
    item["dst_tos_key"] = ""
    # 进度复算
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


@bp.route("/api/image-translate/<task_id>/download/zip", methods=["GET"])
@login_required
def api_download_zip(task_id: str):
    task = _get_owned_task(task_id)
    items = [it for it in (task.get("items") or []) if it.get("status") == "done" and it.get("dst_tos_key")]
    if not items:
        abort(404)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for it in items:
            raw = tos_clients.download_object(it["dst_tos_key"])
            base, _ = os.path.splitext(os.path.basename(it.get("filename") or f"out_{it['idx']}"))
            ext = os.path.splitext(it["dst_tos_key"])[1] or ".png"
            zf.writestr(f"{int(it['idx']):02d}_{base or 'image'}{ext}", raw)
    buf.seek(0)

    filename = f"{task_id}-{task.get('target_language') or 'result'}.zip"
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@bp.route("/api/image-translate/<task_id>", methods=["DELETE"])
@login_required
def api_delete_task(task_id: str):
    task = _get_owned_task(task_id)
    for it in task.get("items") or []:
        for k in ("src_tos_key", "dst_tos_key"):
            v = (it.get(k) or "").strip()
            if v:
                try:
                    tos_clients.delete_object(v)
                except Exception:
                    pass
    db_execute(
        "UPDATE projects SET deleted_at = NOW() WHERE id=%s AND user_id=%s",
        (task_id, current_user.id),
    )
    store.update(task_id, status="deleted",
                 deleted_at=datetime.now().isoformat(timespec="seconds"))
    return ("", 204)
```

- [ ] **Step 4: 运行测试通过**

Run: `pytest tests/test_image_translate_routes.py -v`
Expected: PASS（全部路由测试绿）

- [ ] **Step 5: 提交**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat(image-translate): 单张重试、zip 打包、任务删除"
```

---

## Task 11: Routes - 列表页与详情页 HTML shell

**Files:**
- Modify: `web/routes/image_translate.py`
- Create: `web/templates/image_translate_list.html`
- Create: `web/templates/image_translate_detail.html`
- Create: `web/templates/_image_translate_styles.html`

- [ ] **Step 1: 列表页路由 + 历史查询**

Add to `web/routes/image_translate.py`:
```python
from flask import render_template
from appcore.db import query as db_query


@bp.route("/image-translate", methods=["GET"])
@login_required
def page_list():
    rows = db_query(
        """
        SELECT id, created_at, status, state_json
        FROM projects
        WHERE user_id=%s AND type='image_translate' AND deleted_at IS NULL
        ORDER BY created_at DESC
        LIMIT 100
        """,
        (current_user.id,),
    )
    history = []
    import json as _json
    for row in rows:
        state = {}
        try:
            state = _json.loads(row.get("state_json") or "{}")
        except Exception:
            state = {}
        items = state.get("items") or []
        done = sum(1 for it in items if it.get("status") == "done")
        history.append({
            "id": row["id"],
            "created_at": row.get("created_at"),
            "status": row.get("status") or state.get("status") or "",
            "preset": state.get("preset") or "",
            "preset_label": "封面图翻译" if state.get("preset") == "cover" else ("产品详情图翻译" if state.get("preset") == "detail" else ""),
            "target_language_name": state.get("target_language_name") or "",
            "model_id": state.get("model_id") or "",
            "total": len(items),
            "done": done,
        })
    return render_template("image_translate_list.html", history=history)


@bp.route("/image-translate/<task_id>", methods=["GET"])
@login_required
def page_detail(task_id: str):
    task = _get_owned_task(task_id)
    return render_template("image_translate_detail.html",
                           task_id=task_id,
                           state=_state_payload(task))
```

- [ ] **Step 2: 列表页模板**

Create `web/templates/image_translate_list.html`:
```html
{% extends "layout.html" %}
{% block title %}图片翻译{% endblock %}
{% block page_title %}图片翻译{% endblock %}
{% block content %}
<div class="it-shell" data-page="image-translate-list">

  <section class="card">
    <h2>新建图片翻译任务</h2>

    <div class="form-row">
      <label>场景预设</label>
      <select id="itPreset">
        <option value="cover">封面图翻译</option>
        <option value="detail">产品详情图翻译</option>
      </select>
    </div>

    <div class="form-row">
      <label>目标语言 <span class="required">*</span></label>
      <select id="itLanguage"><option value="">请选择</option></select>
    </div>

    <div class="form-row">
      <label>使用模型 <span class="required">*</span></label>
      <select id="itModel"><option value="">请选择</option></select>
    </div>

    <div class="form-row">
      <label>提示词（可编辑；<code>{target_language_name}</code> 会自动替换）</label>
      <textarea id="itPrompt" rows="4" style="width:100%"></textarea>
    </div>

    <div class="form-row">
      <label>图片（最多 20 张）</label>
      <input id="itFileInput" type="file" accept="image/jpeg,image/png,image/webp" multiple>
      <div id="itThumbs" class="it-thumbs"></div>
    </div>

    <div class="form-row">
      <button id="itSubmit" class="btn btn-primary">提交任务</button>
      <span id="itHint" class="hint"></span>
    </div>

    <div id="itError" class="inline-error" style="display:none"></div>
  </section>

  <section class="card">
    <h2>历史任务</h2>
    {% if not history %}
      <div class="empty">暂无任务</div>
    {% else %}
      <table class="table">
        <thead>
          <tr><th>时间</th><th>预设</th><th>目标语言</th><th>模型</th><th>进度</th><th>状态</th></tr>
        </thead>
        <tbody>
          {% for item in history %}
          <tr onclick="location.href='/image-translate/{{ item.id }}'" style="cursor:pointer">
            <td>{{ item.created_at }}</td>
            <td>{{ item.preset_label }}</td>
            <td>{{ item.target_language_name }}</td>
            <td>{{ item.model_id }}</td>
            <td>{{ item.done }} / {{ item.total }}</td>
            <td><span class="chip">{{ item.status }}</span></td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    {% endif %}
  </section>

</div>
{% include "_image_translate_styles.html" %}
{% include "_image_translate_scripts.html" %}
{% endblock %}
```

- [ ] **Step 3: 详情页模板**

Create `web/templates/image_translate_detail.html`:
```html
{% extends "layout.html" %}
{% block title %}图片翻译详情{% endblock %}
{% block page_title %}图片翻译详情{% endblock %}
{% block content %}
<script>
  window.imageTranslateBootstrap = {{ state | tojson }};
</script>
<div class="it-shell" data-page="image-translate-detail" data-task-id="{{ task_id }}">

  <section class="card">
    <h2>任务信息</h2>
    <div class="it-meta-grid">
      <div><label>预设</label><strong id="itMetaPreset">{{ 'cover' if state.preset == 'cover' else 'detail' }}</strong></div>
      <div><label>目标语言</label><strong id="itMetaLang">{{ state.target_language_name }}</strong></div>
      <div><label>模型</label><strong id="itMetaModel">{{ state.model_id }}</strong></div>
    </div>
    <details class="it-prompt-snapshot" style="margin-top:12px">
      <summary>提示词快照</summary>
      <pre>{{ state.prompt }}</pre>
    </details>
  </section>

  <section class="card">
    <h2>进度</h2>
    <div id="itProgress" class="it-progress">
      <span id="itProgressText">{{ state.progress.done }} / {{ state.progress.total }} 完成，{{ state.progress.failed }} 失败</span>
      <div class="it-progress-bar"><div id="itProgressFill"></div></div>
    </div>
  </section>

  <section class="card">
    <h2>图片对比</h2>
    <div id="itItemList" class="it-items"></div>
    <div class="form-row" style="margin-top:16px">
      <button id="itZipDownload" class="btn btn-primary">打包下载全部</button>
      <button id="itDelete" class="btn btn-danger" style="margin-left:8px">删除任务</button>
    </div>
  </section>

</div>
{% include "_image_translate_styles.html" %}
{% include "_image_translate_scripts.html" %}
{% endblock %}
```

- [ ] **Step 4: 样式文件**

Create `web/templates/_image_translate_styles.html`:
```html
<style>
.it-shell { display: grid; gap: 16px; }
.it-shell .card { background: var(--bg-card); border:1px solid var(--border-main);
                  border-radius:12px; padding:20px; }
.it-shell .form-row { margin-bottom:14px; }
.it-shell .form-row label { display:block; font-size:13px; color:var(--text-main);
                             opacity:.8; margin-bottom:6px; }
.it-shell .required { color:#dc2626; }
.it-thumbs { display:grid; grid-template-columns:repeat(auto-fill,minmax(96px,1fr));
             gap:8px; margin-top:8px; }
.it-thumb { position:relative; border:1px solid var(--border-main); border-radius:8px;
            overflow:hidden; aspect-ratio:1/1; }
.it-thumb img { width:100%; height:100%; object-fit:cover; }
.it-thumb .it-thumb-remove { position:absolute; top:4px; right:4px; background:rgba(0,0,0,0.6);
                              color:#fff; border:0; border-radius:50%; width:22px; height:22px;
                              cursor:pointer; line-height:22px; padding:0; }
.it-progress { display:grid; gap:8px; }
.it-progress-bar { height:8px; background:#e5e7eb; border-radius:99px; overflow:hidden; }
#itProgressFill { height:100%; background:linear-gradient(90deg,#1d6fe8,#0ea5e9);
                   width:0%; transition: width 200ms ease; }
.it-items { display:grid; gap:12px; }
.it-item { display:grid; grid-template-columns: 1fr 1fr auto; align-items:center; gap:12px;
           padding:12px; border:1px solid var(--border-main); border-radius:10px; }
.it-item img { max-width:100%; max-height:240px; object-fit:contain; border-radius:6px;
               background:#f8fafc; }
.it-item .it-item-status { font-size:12px; color:#6b7280; }
.it-item .it-item-actions { display:flex; gap:8px; align-items:center; }
.it-meta-grid { display:grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap:12px; }
.it-meta-grid > div { padding:10px 12px; border:1px solid var(--border-main); border-radius:8px; background:#f8fbff; }
.it-meta-grid label { display:block; font-size:12px; color:var(--text-main); opacity:.7; }
.inline-error { margin-top:12px; padding:10px 12px; border-radius:8px;
                background:rgba(220,82,60,.08); color:#a94231; }
.empty { padding:24px; text-align:center; color:#6b7280; }
.chip { display:inline-block; padding:2px 8px; border-radius:999px;
        background:#eef6ff; color:#1d4ed8; font-size:12px; }
</style>
```

- [ ] **Step 5: 提交（JS 占位，用下个 Task 实现）**

Create a stub `web/templates/_image_translate_scripts.html` with just `<script>/* filled in next task */</script>` so the include doesn't 500.

```bash
git add web/routes/image_translate.py \
        web/templates/image_translate_list.html \
        web/templates/image_translate_detail.html \
        web/templates/_image_translate_styles.html \
        web/templates/_image_translate_scripts.html
git commit -m "feat(image-translate): 列表页与详情页模板骨架"
```

---

## Task 12: 前端脚本（上传、提交、Socket.IO 订阅）

**Files:**
- Modify: `web/templates/_image_translate_scripts.html`

- [ ] **Step 1: 写脚本**

Rewrite `web/templates/_image_translate_scripts.html`:
```html
<script>
(function(){
  var pageEl = document.querySelector("[data-page^='image-translate']");
  if (!pageEl) return;
  var pageType = pageEl.getAttribute("data-page");

  // ================= 列表页 =================
  if (pageType === "image-translate-list") {
    var state = { files: [] };

    var presetEl = document.getElementById("itPreset");
    var langEl = document.getElementById("itLanguage");
    var modelEl = document.getElementById("itModel");
    var promptEl = document.getElementById("itPrompt");
    var fileInput = document.getElementById("itFileInput");
    var thumbsEl = document.getElementById("itThumbs");
    var submitBtn = document.getElementById("itSubmit");
    var errEl = document.getElementById("itError");
    var hintEl = document.getElementById("itHint");
    var defaultPrompts = { cover: "", detail: "" };

    function showError(msg){ errEl.textContent = msg; errEl.style.display = msg ? "block" : "none"; }
    function clearError(){ showError(""); }

    function loadLanguages(){
      return fetch("/api/languages",{credentials:"same-origin"})
        .then(function(r){return r.json();})
        .then(function(d){
          (d.items || []).forEach(function(lang){
            if (lang.code === "en") return;  // 源固定英文
            var opt = document.createElement("option");
            opt.value = lang.code; opt.textContent = lang.name_zh;
            langEl.appendChild(opt);
          });
        });
    }
    function loadModels(){
      return fetch("/api/image-translate/models",{credentials:"same-origin"})
        .then(function(r){return r.json();})
        .then(function(d){
          (d.items || []).forEach(function(m){
            var opt = document.createElement("option");
            opt.value = m.id; opt.textContent = m.name;
            modelEl.appendChild(opt);
          });
          if (d.default_model_id) modelEl.value = d.default_model_id;
        });
    }
    function loadPrompts(){
      return fetch("/api/image-translate/system-prompts",{credentials:"same-origin"})
        .then(function(r){return r.json();})
        .then(function(d){
          defaultPrompts = d;
          promptEl.value = d[presetEl.value] || "";
        });
    }

    function renderThumbs(){
      thumbsEl.innerHTML = "";
      state.files.forEach(function(f, idx){
        var wrap = document.createElement("div");
        wrap.className = "it-thumb";
        var img = document.createElement("img");
        img.src = URL.createObjectURL(f);
        var btn = document.createElement("button");
        btn.className = "it-thumb-remove"; btn.textContent = "×";
        btn.onclick = function(){ state.files.splice(idx,1); renderThumbs(); };
        wrap.appendChild(img); wrap.appendChild(btn);
        thumbsEl.appendChild(wrap);
      });
      hintEl.textContent = "已选 " + state.files.length + " 张，最多 20 张";
    }

    presetEl.onchange = function(){ promptEl.value = defaultPrompts[presetEl.value] || ""; };
    fileInput.onchange = function(){
      var chosen = Array.prototype.slice.call(fileInput.files || []);
      state.files = state.files.concat(chosen).slice(0, 20);
      fileInput.value = "";
      renderThumbs();
    };

    submitBtn.onclick = async function(){
      clearError();
      if (!state.files.length) return showError("请选择图片");
      if (!langEl.value) return showError("请选择目标语言");
      if (!modelEl.value) return showError("请选择模型");
      if (!promptEl.value.trim()) return showError("prompt 不能为空");

      submitBtn.disabled = true;
      submitBtn.textContent = "申请上传地址…";
      try {
        // 1. bootstrap
        var bootstrap = await fetch("/api/image-translate/upload/bootstrap",{
          method:"POST", credentials:"same-origin",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({
            count: state.files.length,
            files: state.files.map(function(f){
              return {filename: f.name, size: f.size, content_type: f.type || "application/octet-stream"};
            }),
          }),
        }).then(r => r.json().then(d => { if(!r.ok) throw new Error(d.error||"bootstrap failed"); return d; }));

        // 2. 逐个直传
        for (var i=0; i<state.files.length; i++){
          submitBtn.textContent = "上传 " + (i+1) + "/" + state.files.length + "…";
          var up = bootstrap.uploads[i];
          var res = await fetch(up.upload_url, {method:"PUT", body: state.files[i]});
          if (!res.ok) throw new Error("直传失败 " + (i+1));
        }

        submitBtn.textContent = "建档中…";
        var complete = await fetch("/api/image-translate/upload/complete",{
          method:"POST", credentials:"same-origin",
          headers:{"Content-Type":"application/json"},
          body: JSON.stringify({
            task_id: bootstrap.task_id,
            preset: presetEl.value,
            target_language: langEl.value,
            model_id: modelEl.value,
            prompt: promptEl.value,
            uploaded: bootstrap.uploads.map(function(u,idx){
              return {idx: u.idx, object_key: u.object_key, filename: state.files[idx].name, size: state.files[idx].size};
            }),
          }),
        }).then(r => r.json().then(d => { if(!r.ok) throw new Error(d.error||"complete failed"); return d; }));

        location.href = "/image-translate/" + complete.task_id;
      } catch (e) {
        showError(e.message || "提交失败");
        submitBtn.disabled = false;
        submitBtn.textContent = "提交任务";
      }
    };

    loadLanguages();
    loadModels();
    loadPrompts();
    return;
  }

  // ================= 详情页 =================
  if (pageType === "image-translate-detail") {
    var taskId = pageEl.getAttribute("data-task-id");
    var listEl = document.getElementById("itItemList");
    var progressText = document.getElementById("itProgressText");
    var progressFill = document.getElementById("itProgressFill");
    var zipBtn = document.getElementById("itZipDownload");
    var delBtn = document.getElementById("itDelete");

    function renderItems(state){
      listEl.innerHTML = "";
      (state.items || []).forEach(function(it){
        var row = document.createElement("div");
        row.className = "it-item";
        var left = document.createElement("img");
        left.src = "/api/image-translate/"+taskId+"/artifact/source/"+it.idx;
        row.appendChild(left);
        var right = document.createElement("div");
        if (it.status === "done") {
          var imgOut = document.createElement("img");
          imgOut.src = "/api/image-translate/"+taskId+"/artifact/result/"+it.idx + "?t=" + Date.now();
          right.appendChild(imgOut);
        } else {
          right.innerHTML = '<div class="it-item-status">状态：'+(it.status||"pending")+
                            (it.error ? '<br>错误：'+it.error : '')+'</div>';
        }
        row.appendChild(right);
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
        listEl.appendChild(row);
      });
    }

    function renderProgress(state){
      var p = state.progress || {total:0, done:0, failed:0};
      progressText.textContent = p.done + " / " + p.total + " 完成，" + p.failed + " 失败";
      var pct = p.total ? Math.round((p.done + p.failed) / p.total * 100) : 0;
      progressFill.style.width = pct + "%";
    }

    function renderAll(state){
      renderItems(state);
      renderProgress(state);
    }

    function refresh(){
      return fetch("/api/image-translate/"+taskId,{credentials:"same-origin"})
        .then(function(r){return r.json();})
        .then(renderAll);
    }

    function retry(idx){
      fetch("/api/image-translate/"+taskId+"/retry/"+idx,{method:"POST"})
        .then(refresh);
    }

    zipBtn.onclick = function(){
      location.href = "/api/image-translate/"+taskId+"/download/zip";
    };
    delBtn.onclick = function(){
      if (!confirm("确认删除该任务？")) return;
      fetch("/api/image-translate/"+taskId,{method:"DELETE"})
        .then(function(r){ if (r.status === 204) location.href = "/image-translate"; });
    };

    // Socket.IO
    if (window.io) {
      var socket = window.io();
      var join = function(){ socket.emit("join_image_translate_task", {task_id: taskId}); };
      join();
      socket.on("connect", join);
      socket.on("image_translate:item_updated", function(){ refresh(); });
      socket.on("image_translate:progress", function(){ refresh(); });
      socket.on("image_translate:task_done", function(){ refresh(); });
    }

    renderAll(window.imageTranslateBootstrap || {});
    refresh();
    return;
  }
})();
</script>
```

- [ ] **Step 2: 提交**

```bash
git add web/templates/_image_translate_scripts.html
git commit -m "feat(image-translate): 前端上传/详情页脚本与 Socket.IO 订阅"
```

---

## Task 13: Socket.IO 事件注册 + 启动恢复 + 导航菜单

**Files:**
- Modify: `web/app.py`（Socket.IO 事件 + resume 调用）
- Modify: `web/templates/layout.html`（侧栏菜单项）

- [ ] **Step 1: 注册 Socket.IO 事件**

Add to `web/app.py`（参考现有 `join_subtitle_removal_task` 的位置）:
```python
@socketio.on("join_image_translate_task")
def on_join_image_translate(data):
    from flask_login import current_user
    if not current_user.is_authenticated:
        return
    task_id = (data or {}).get("task_id")
    if not task_id:
        return
    task = store.get(task_id)
    if task and task.get("_user_id") == current_user.id \
            and task.get("type") == "image_translate":
        join_room(task_id)
```

- [ ] **Step 2: 服务启动恢复**

在 `recover_all_interrupted_tasks()` 调用点附近，追加：
```python
from web.services.image_translate_runner import resume_inflight_tasks as resume_image_translate
resume_image_translate()
```
（或者参考现有模块的 recover 接入位置做同构调用。）

- [ ] **Step 3: 侧栏菜单项**

Modify `web/templates/layout.html`，找到"字幕移除"那个 `<a>`（或类似菜单项），在它下方追加：
```html
<a href="{{ url_for('image_translate.page_list') }}"
   class="nav-item{% if request.path.startswith('/image-translate') %} active{% endif %}">
  <span class="nav-icon">🖼️</span>
  <span class="nav-label">图片翻译</span>
</a>
```
（图标与现有菜单风格保持一致——如现有用 SVG，按 SVG 规范写；本脚本给 emoji 占位，实施时可替换为 lucide svg。）

- [ ] **Step 4: 本地冒烟**

```bash
python main.py
```
然后浏览器访问 `http://localhost:5000`，登录 → 看到侧栏"图片翻译"，点进列表页不报 500。

- [ ] **Step 5: 提交**

```bash
git add web/app.py web/templates/layout.html
git commit -m "feat(image-translate): 侧栏菜单、Socket.IO 事件、启动恢复"
```

---

## Task 14: 端到端冒烟 & 发布到测试环境

- [ ] **Step 1: 本地完整回归**

Run: `pytest tests/ -q`
Expected: 全部通过（允许 warnings）。如有红的，回滚到上一个 passing Task 或修复。

- [ ] **Step 2: 本地浏览器冒烟**

启动本地服务：`python main.py`。用测试账号登录，完成以下流程：
1. 打开 `/image-translate`，选择"封面图翻译"预设，目标语言选一个小语种（如德语），模型选 Pro
2. 上传 2-3 张英文封面图（任意英文字样的图），提交
3. 跳到详情页，看到进度条实时更新，Socket.IO 事件刷新列表
4. 全部完成后点"下载"验证单张下载，点"打包下载全部"验证 zip
5. 回到列表页看历史有这条
6. 模拟失败：临时把环境变量 `GEMINI_BACKEND=cloud` 但 key 不授权 → 所有图 failed → 手动重试按钮出现

如果出现 500：查 stderr（eventlet gunicorn 也会打到 stderr）定位修复。

- [ ] **Step 3: 提交任何本地修复**

如果 Step 2 发现问题已修复，提交一个 `fix(image-translate): 端到端冒烟修复` 形式的小 commit。

- [ ] **Step 4: 发布到测试环境**

说 "测试发布" 触发 `bash deploy/publish-test.sh`。若数据库迁移未自动跑，手动在服务器执行：
```bash
ssh openclaw-noobird \
  "cd /opt/autovideosrt-test && source venv/bin/activate && \
   mysql -u \$(grep '^DB_USER=' /opt/autovideosrt/.env | cut -d= -f2) \
         -p\$(grep '^DB_PASSWORD=' /opt/autovideosrt/.env | cut -d= -f2) \
         auto_video_test < db/migrations/2026_04_16_add_image_translate_project_type.sql"
```

- [ ] **Step 5: 测试环境验证**

浏览器打开 `http://14.103.220.208:9999/image-translate`，重复 Step 2 的冒烟流程。

- [ ] **Step 6: 合并到 master**

冒烟通过后：
```bash
git push origin feature/image-translate
gh pr create --title "feat: 图片翻译功能（Nano Banana）" --body "实现设计文档 docs/superpowers/specs/2026-04-16-image-translate-design.md"
```

---

## 自检

**Spec 覆盖对照：**
- §3 信息架构（新菜单 + 两页面）→ Task 11 / 13 ✅
- §4 列表页 / 详情页交互 → Task 11 / 12 ✅
- §5 模型与 Prompt（含用户偏好）→ Task 3 / 4 / 8 ✅
- §6 数据模型（迁移 + state_json）→ Task 1 / 2 ✅
- §7 API 规范（所有端点）→ Task 8 / 9 / 10 ✅
- §8 运行时（串行 + 重试 + 恢复）→ Task 6 / 7 ✅
- §9 Socket.IO → Task 13 / 12 ✅
- §10 Gemini 图像封装 → Task 3 ✅
- §11 Admin 设置 → Task 5 ✅
- §12 错误处理 → Task 6 错误分类 + Task 10 retry ✅
- §13 测试策略（路由 / runtime / gemini_image / admin）→ 对应 Task 3 / 5 / 6 / 7 / 8 / 9 / 10 ✅

**占位符扫描：** 本文档内 "TBD / TODO / …" 仅出现在实现备注说明（提醒核对 API 名），没有留给执行者自由发挥的缺口。
**类型一致性：** Task 6 使用的字段名（`items[*].status/attempts/error/dst_tos_key`、`progress.total/done/failed/running`）与 Task 2 工厂输出完全一致；Task 8/9/10 的路由 payload 字段也一致。
