# 后台素材语种配置 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让后台“系统设置”页可以前端管理素材语种，并让素材页继续从同一份数据库配置读取启用语种。

**Architecture:** 保持 `media_languages` 作为唯一配置源，把语种管理 DAO 扩展到 `appcore/medias.py`。后台系统设置页通过新的管理员 JSON 接口读写语种，页面用一小段原生 JS 做增删改和行内错误提示；素材页已有 `/medias/api/languages` 继续只返回启用语种，因此不需要额外同步逻辑。

**Tech Stack:** Python 3 / Flask / Jinja2 / 原生 JavaScript / pytest / MySQL

**Spec reference:** `docs/superpowers/specs/2026-04-15-admin-media-languages-settings-design.md`

---

## File Structure

**Modify:**
- `appcore/medias.py`：新增语种配置 CRUD、编码校验、使用情况统计、删除/停用保护规则
- `web/routes/admin.py`：新增素材语种管理接口，并把系统设置页返回语种初始化数据
- `web/templates/admin_settings.html`：新增“素材语种配置”卡片和容器
- `tests/test_appcore_medias_multi_lang.py`：补 DAO 层行为测试
- `tests/test_web_routes.py`：补系统设置页和管理员接口回归测试

**Create:**
- `web/static/admin_settings.js`：负责语种列表渲染、增删改交互、行内报错和成功提示

---

### Task 1: 先用测试锁定语种配置数据层

**Files:**
- Modify: `tests/test_appcore_medias_multi_lang.py`
- Modify: `appcore/medias.py`

- [ ] **Step 1: 在 `tests/test_appcore_medias_multi_lang.py` 追加失败测试**

```python
def test_list_languages_for_admin_includes_disabled_and_usage(monkeypatch):
    rows = [
        {"code": "en", "name_zh": "英语", "sort_order": 1, "enabled": 1},
        {"code": "pt", "name_zh": "葡萄牙语", "sort_order": 7, "enabled": 0},
    ]
    monkeypatch.setattr(medias, "query", lambda sql, args=(): rows if "FROM media_languages" in sql else [])
    monkeypatch.setattr(
        medias,
        "query_one",
        lambda sql, args=(): {"items_count": 0, "copy_count": 0, "cover_count": 0},
    )

    langs = medias.list_languages_for_admin()

    assert [item["code"] for item in langs] == ["en", "pt"]
    assert langs[1]["enabled"] == 0
    assert langs[1]["in_use"] is False


def test_create_language_normalizes_code_and_rejects_duplicates(monkeypatch):
    monkeypatch.setattr(medias, "query_one", lambda sql, args=(): {"code": "pt"} if args == ("pt",) else None)

    with pytest.raises(ValueError, match="已存在"):
        medias.create_language("PT", "葡萄牙语", 7, True)


def test_update_language_rejects_disabling_en():
    with pytest.raises(ValueError, match="默认语种"):
        medias.validate_language_update("en", enabled=False)


def test_delete_language_rejects_in_use_language(monkeypatch):
    monkeypatch.setattr(
        medias,
        "get_language_usage",
        lambda code: {"items_count": 1, "copy_count": 0, "cover_count": 0, "in_use": True},
    )

    with pytest.raises(ValueError, match="已有关联数据"):
        medias.delete_language("de")
```

- [ ] **Step 2: 运行数据层测试并确认它先失败**

Run: `pytest tests/test_appcore_medias_multi_lang.py -k "list_languages_for_admin or create_language_normalizes_code or update_language_rejects_disabling_en or delete_language_rejects_in_use_language" -q`

Expected: `AttributeError` 或 `NameError`，因为管理语种的 DAO 还不存在。

- [ ] **Step 3: 在 `appcore/medias.py` 增加编码规范化和使用情况查询**

```python
_LANG_CODE_RE = re.compile(r"^[a-z0-9-]{2,8}$")


def normalize_language_code(code: str) -> str:
    normalized = (code or "").strip().lower()
    if not _LANG_CODE_RE.match(normalized):
        raise ValueError("语言编码格式不合法")
    return normalized


def get_language_usage(code: str) -> dict:
    items_row = query_one(
        "SELECT COUNT(*) AS c FROM media_items WHERE lang=%s AND deleted_at IS NULL",
        (code,),
    ) or {}
    copy_row = query_one(
        "SELECT COUNT(*) AS c FROM media_copywritings WHERE lang=%s",
        (code,),
    ) or {}
    cover_row = query_one(
        "SELECT COUNT(*) AS c FROM media_product_covers WHERE lang=%s",
        (code,),
    ) or {}
    items_count = int(items_row.get("c") or 0)
    copy_count = int(copy_row.get("c") or 0)
    cover_count = int(cover_row.get("c") or 0)
    return {
        "items_count": items_count,
        "copy_count": copy_count,
        "cover_count": cover_count,
        "in_use": any((items_count, copy_count, cover_count)),
    }
```

- [ ] **Step 4: 在 `appcore/medias.py` 增加语种管理 CRUD**

```python
def list_languages_for_admin() -> list[dict]:
    rows = query(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages "
        "ORDER BY sort_order ASC, code ASC"
    )
    items = []
    for row in rows:
        usage = get_language_usage(row["code"])
        items.append({**row, **usage})
    return items


def create_language(code: str, name_zh: str, sort_order: int, enabled: bool) -> None:
    normalized = normalize_language_code(code)
    if normalized == "en" or query_one("SELECT code FROM media_languages WHERE code=%s", (normalized,)):
        raise ValueError("语言编码已存在")
    execute(
        "INSERT INTO media_languages (code, name_zh, sort_order, enabled) VALUES (%s,%s,%s,%s)",
        (normalized, name_zh.strip(), int(sort_order), 1 if enabled else 0),
    )


def validate_language_update(code: str, enabled: bool | None = None) -> None:
    if code == "en" and enabled is False:
        raise ValueError("默认语种 en 不能停用")


def update_language(code: str, name_zh: str, sort_order: int, enabled: bool) -> None:
    normalized = normalize_language_code(code)
    validate_language_update(normalized, enabled=enabled)
    execute(
        "UPDATE media_languages SET name_zh=%s, sort_order=%s, enabled=%s WHERE code=%s",
        (name_zh.strip(), int(sort_order), 1 if enabled else 0, normalized),
    )


def delete_language(code: str) -> None:
    normalized = normalize_language_code(code)
    if normalized == "en":
        raise ValueError("默认语种 en 不能删除")
    usage = get_language_usage(normalized)
    if usage["in_use"]:
        raise ValueError("该语种已有关联数据，只能停用")
    execute("DELETE FROM media_languages WHERE code=%s", (normalized,))
```

- [ ] **Step 5: 回跑数据层测试**

Run: `pytest tests/test_appcore_medias_multi_lang.py -k "list_languages_for_admin or create_language_normalizes_code or update_language_rejects_disabling_en or delete_language_rejects_in_use_language" -q`

Expected: all selected tests pass.

- [ ] **Step 6: 提交这一层**

Run: `git add appcore/medias.py tests/test_appcore_medias_multi_lang.py && git commit -m "feat: add media language settings dao"`

---

### Task 2: 用接口测试锁定管理员 API

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `web/routes/admin.py`

- [ ] **Step 1: 在 `tests/test_web_routes.py` 追加管理员接口失败测试**

```python
def test_admin_media_languages_api_lists_all_rows(logged_in_client, monkeypatch):
    monkeypatch.setattr("web.routes.admin.medias.list_languages_for_admin", lambda: [
        {"code": "en", "name_zh": "英语", "sort_order": 1, "enabled": 1, "items_count": 0, "copy_count": 0, "cover_count": 0, "in_use": False},
        {"code": "pt", "name_zh": "葡萄牙语", "sort_order": 7, "enabled": 0, "items_count": 0, "copy_count": 0, "cover_count": 0, "in_use": False},
    ])

    response = logged_in_client.get("/admin/api/media-languages")

    assert response.status_code == 200
    assert [item["code"] for item in response.get_json()["items"]] == ["en", "pt"]


def test_admin_media_languages_api_rejects_deleting_in_use_language(logged_in_client, monkeypatch):
    monkeypatch.setattr("web.routes.admin.medias.delete_language", lambda code: (_ for _ in ()).throw(ValueError("该语种已有关联数据，只能停用")))

    response = logged_in_client.delete("/admin/api/media-languages/de")

    assert response.status_code == 400
    assert "只能停用" in response.get_json()["error"]
```

- [ ] **Step 2: 运行接口测试并确认先失败**

Run: `pytest tests/test_web_routes.py -k "admin_media_languages_api_" -q`

Expected: `AttributeError` 或 404，因为管理员语种接口还没加。

- [ ] **Step 3: 在 `web/routes/admin.py` 引入语种 DAO，并补 JSON 接口**

```python
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from appcore import medias


@bp.route("/api/media-languages", methods=["GET"])
@login_required
@admin_required
def api_media_languages():
    return jsonify({"items": medias.list_languages_for_admin()})


@bp.route("/api/media-languages", methods=["POST"])
@login_required
@admin_required
def api_create_media_language():
    body = request.get_json(silent=True) or {}
    try:
        medias.create_language(
            body.get("code", ""),
            body.get("name_zh", ""),
            body.get("sort_order", 0),
            bool(body.get("enabled", True)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True}), 201
```

- [ ] **Step 4: 继续补更新和删除接口**

```python
@bp.route("/api/media-languages/<code>", methods=["PUT"])
@login_required
@admin_required
def api_update_media_language(code: str):
    body = request.get_json(silent=True) or {}
    try:
        medias.update_language(
            code,
            body.get("name_zh", ""),
            body.get("sort_order", 0),
            bool(body.get("enabled", True)),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})


@bp.route("/api/media-languages/<code>", methods=["DELETE"])
@login_required
@admin_required
def api_delete_media_language(code: str):
    try:
        medias.delete_language(code)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})
```

- [ ] **Step 5: 回跑管理员接口测试**

Run: `pytest tests/test_web_routes.py -k "admin_media_languages_api_" -q`

Expected: selected tests pass.

---

### Task 3: 做系统设置页的前端可配置语种卡片

**Files:**
- Modify: `web/templates/admin_settings.html`
- Create: `web/static/admin_settings.js`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: 在页面回归测试里先锁定新 UI 骨架**

```python
def test_admin_settings_page_contains_media_languages_config(logged_in_client):
    response = logged_in_client.get("/admin/settings")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "素材语种配置" in body
    assert 'id="mediaLanguagesCard"' in body
    assert 'id="mediaLanguagesTableBody"' in body
    assert 'admin_settings.js' in body
```

- [ ] **Step 2: 运行页面测试并确认先失败**

Run: `pytest tests/test_web_routes.py -k "admin_settings_page_contains_media_languages_config" -q`

Expected: assertion failure，因为页面还没有语种配置卡片。

- [ ] **Step 3: 在 `admin_settings.html` 新增语种配置卡片和脚本入口**

```html
<div class="settings-card" id="mediaLanguagesCard">
  <div class="settings-card-head">
    <div>
      <h2>素材语种配置</h2>
      <p class="field-hint">管理素材页和后续模块共用的小语种列表。</p>
    </div>
    <button type="button" class="btn btn-ghost" id="addMediaLanguageBtn">新增语种</button>
  </div>
  <div id="mediaLanguagesFlash" class="success-msg" hidden></div>
  <div class="media-language-table-wrap">
    <table class="media-language-table">
      <thead>
        <tr>
          <th>语言编码</th>
          <th>语言名称</th>
          <th>排序</th>
          <th>启用</th>
          <th>使用情况</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody id="mediaLanguagesTableBody"></tbody>
    </table>
  </div>
</div>
```

- [ ] **Step 4: 在 `admin_settings.html` 的 `scripts` block 挂上初始化数据和静态脚本**

```html
{% block scripts %}
  {{ super() }}
  <script>
    window.ADMIN_MEDIA_LANGUAGES_BOOTSTRAP = {{ media_languages|tojson }};
  </script>
  <script src="{{ url_for('static', filename='admin_settings.js') }}"></script>
{% endblock %}
```

- [ ] **Step 5: 新建 `web/static/admin_settings.js`，实现列表渲染和 CRUD 交互**

```javascript
(function () {
  const state = { rows: window.ADMIN_MEDIA_LANGUAGES_BOOTSTRAP || [] };
  const tbody = document.getElementById("mediaLanguagesTableBody");
  const flash = document.getElementById("mediaLanguagesFlash");

  function render() {
    tbody.innerHTML = state.rows.map(renderRow).join("");
    bindRowActions();
  }

  async function saveRow(code, payload, isCreate) {
    const url = isCreate ? "/admin/api/media-languages" : `/admin/api/media-languages/${code}`;
    const method = isCreate ? "POST" : "PUT";
    const response = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "保存失败");
    await reloadRows();
  }
})();
```

- [ ] **Step 6: 回跑系统设置页回归测试**

Run: `pytest tests/test_web_routes.py -k "admin_settings_page_contains_media_languages_config" -q`

Expected: test passes.

---

### Task 4: 确认素材页只跟随启用语种，不受后台页实现方式影响

**Files:**
- Modify: `tests/test_web_routes.py`
- Modify: `appcore/medias.py`（只在必要时微调已有 `list_languages`）

- [ ] **Step 1: 先补素材语言接口回归测试**

```python
def test_medias_languages_api_returns_enabled_languages_only(logged_in_client, monkeypatch):
    monkeypatch.setattr("web.routes.medias.medias.list_languages", lambda: [
        {"code": "en", "name_zh": "英语", "sort_order": 1, "enabled": 1},
        {"code": "pt", "name_zh": "葡萄牙语", "sort_order": 7, "enabled": 1},
    ])

    response = logged_in_client.get("/medias/api/languages")

    assert response.status_code == 200
    assert [item["code"] for item in response.get_json()["items"]] == ["en", "pt"]
```

- [ ] **Step 2: 运行相关回归测试**

Run: `pytest tests/test_web_routes.py -k "admin_media_languages_api_ or admin_settings_page_contains_media_languages_config or medias_languages_api_returns_enabled_languages_only" -q`

Expected: 全部通过。

- [ ] **Step 3: 只在需要时微调 `list_languages` 保持排序和启用过滤**

```python
def list_languages() -> list[dict]:
    return query(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
```

---

### Task 5: 最终验证并整理分支

**Files:**
- Modify: `appcore/medias.py`
- Modify: `web/routes/admin.py`
- Modify: `web/templates/admin_settings.html`
- Create: `web/static/admin_settings.js`
- Modify: `tests/test_appcore_medias_multi_lang.py`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1: 运行数据层和路由层相关测试**

Run: `pytest tests/test_appcore_medias_multi_lang.py tests/test_web_routes.py -k "media_language or admin_settings_page_contains_media_languages_config or medias_languages_api_returns_enabled_languages_only" -q`

Expected: all selected tests pass.

- [ ] **Step 2: 运行系统设置页既有回归，确认保留周期功能没被带坏**

Run: `pytest tests/test_settings.py -q`

Expected: pass.

- [ ] **Step 3: 检查工作树改动**

Run: `git status --short`

Expected: 只看到本计划涉及的 6 个文件，没有把主干工作树里的草稿带进来。

- [ ] **Step 4: 提交实现**

Run: `git add appcore/medias.py web/routes/admin.py web/templates/admin_settings.html web/static/admin_settings.js tests/test_appcore_medias_multi_lang.py tests/test_web_routes.py && git commit -m "feat: add configurable media languages in admin settings"`

