# Image Translate Language Unification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让图片翻译模块的目标语言、prompt 获取和后台 prompt 配置全部统一改为从 `media_languages` 读取，并在无可用目标语言时提供明确空态。

**Architecture:** 保留 `appcore.image_translate_settings.py` 作为图片翻译 prompt 的唯一入口，但把“支持语种”从静态常量切换成运行时查询 `media_languages`。用户侧 `/api/image-translate/system-prompts`、后台 `/admin/api/image-translate/prompts` 和前端 pills 全部改为依赖动态语言列表；现有 6 个内置语种继续使用专用默认 prompt，新增启用语种走通用兜底 prompt。

**Tech Stack:** Flask blueprints, Jinja 模板, 内联浏览器脚本, pytest, `appcore.medias`, `system_settings`

---

## File Structure

- Modify: `appcore/image_translate_settings.py`
  把图片翻译 prompt 的语种来源从静态常量切到 `media_languages`，新增动态语言查询、动态校验和通用兜底 prompt 生成。

- Modify: `web/routes/image_translate.py`
  让 `/api/image-translate/system-prompts` 使用动态语种校验，而不是 `SUPPORTED_LANGS`。

- Modify: `web/routes/admin.py`
  让后台图片翻译 prompt 接口返回动态语言对象列表，并使用动态语种校验保存请求。

- Modify: `web/templates/image_translate_list.html`
  为用户侧图片翻译页补上“无可用目标语言”的空态容器。

- Modify: `web/templates/_image_translate_scripts.html`
  让用户侧图片翻译页在语言列表为空时显示空态、清空 prompt 并禁用提交。

- Modify: `web/templates/admin_settings.html`
  为后台图片翻译 prompt 卡片补空态容器，并把语言 pills / 标签从硬编码切到接口返回的动态语言对象。

- Modify: `tests/test_image_translate_settings.py`
  覆盖动态语言列表、通用兜底 prompt、动态校验和动态 prompt 列表。

- Modify: `tests/test_image_translate_routes.py`
  覆盖 `/api/image-translate/system-prompts` 的动态语言行为，以及用户页空态容器渲染。

- Modify: `tests/test_admin_image_translate_routes.py`
  覆盖后台 prompt 接口返回动态语言对象列表、动态保存和后台页空态容器渲染。

## Task 1: 让图片翻译 prompt 层改用动态语言列表

**Files:**
- Modify: `appcore/image_translate_settings.py`
- Test: `tests/test_image_translate_settings.py`

- [ ] **Step 1: 先写动态语言与兜底 prompt 的失败测试**

在 `tests/test_image_translate_settings.py` 追加这些用例：

```python
def test_list_image_translate_languages_filters_out_en(monkeypatch):
    from appcore import image_translate_settings as its

    monkeypatch.setattr(its.medias, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语", "sort_order": 0, "enabled": 1},
        {"code": "de", "name_zh": "德语", "sort_order": 1, "enabled": 1},
        {"code": "nl", "name_zh": "荷兰语", "sort_order": 2, "enabled": 1},
    ])

    langs = its.list_image_translate_languages()

    assert langs == [
        {"code": "de", "name_zh": "德语", "sort_order": 1, "enabled": 1},
        {"code": "nl", "name_zh": "荷兰语", "sort_order": 2, "enabled": 1},
    ]


def test_get_prompt_bootstraps_generic_prompt_for_dynamic_lang(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}

    monkeypatch.setattr(its.medias, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语", "sort_order": 0, "enabled": 1},
        {"code": "nl", "name_zh": "荷兰语", "sort_order": 2, "enabled": 1},
    ])
    monkeypatch.setattr(
        its,
        "query_one",
        lambda sql, params: {"value": store[params[0]]} if params[0] in store else None,
    )
    monkeypatch.setattr(its, "execute", lambda sql, params: store.__setitem__(params[0], params[1]))

    value = its.get_prompt("cover", "nl")

    assert "荷兰语" in value
    assert "image_translate.prompt_cover_nl" in store


def test_list_all_prompts_uses_dynamic_languages(monkeypatch):
    from appcore import image_translate_settings as its

    store = {}

    monkeypatch.setattr(its.medias, "list_languages", lambda: [
        {"code": "en", "name_zh": "英语", "sort_order": 0, "enabled": 1},
        {"code": "de", "name_zh": "德语", "sort_order": 1, "enabled": 1},
        {"code": "nl", "name_zh": "荷兰语", "sort_order": 2, "enabled": 1},
    ])
    monkeypatch.setattr(
        its,
        "query_one",
        lambda sql, params: {"value": store[params[0]]} if params[0] in store else None,
    )
    monkeypatch.setattr(its, "execute", lambda sql, params: store.__setitem__(params[0], params[1]))

    data = its.list_all_prompts()

    assert set(data.keys()) == {"de", "nl"}
    assert set(data["de"].keys()) == set(its.PRESETS)
    assert set(data["nl"].keys()) == set(its.PRESETS)
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_image_translate_settings.py -q -k "filters_out_en or generic_prompt_for_dynamic_lang or uses_dynamic_languages"`

Expected: 失败，原因会是 `AttributeError: module 'appcore.image_translate_settings' has no attribute 'list_image_translate_languages'`，或者 `list_all_prompts()` 仍然只返回静态 6 个语种。

- [ ] **Step 3: 在 prompt 层实现动态语言查询、动态校验和通用兜底 prompt**

在 `appcore/image_translate_settings.py` 里做这些改动：

```python
from appcore import medias
from appcore.db import execute, query_one


BUILTIN_PROMPT_LANGS: frozenset[str] = frozenset({"de", "fr", "es", "it", "ja", "pt"})
PRESETS: tuple[str, ...] = ("cover", "detail")


def list_image_translate_languages() -> list[dict]:
    return [row for row in medias.list_languages() if row.get("code") != "en"]


def is_image_translate_language_supported(code: str) -> bool:
    normalized = (code or "").strip().lower()
    return any(row.get("code") == normalized for row in list_image_translate_languages())


def _language_meta(code: str) -> dict:
    normalized = (code or "").strip().lower()
    for row in list_image_translate_languages():
        if row.get("code") == normalized:
            return row
    raise ValueError(f"unsupported lang: {code}")


def _fallback_cover_prompt(lang: str, lang_name: str) -> str:
    return (
        f"Task: Localize this English video cover image into {lang_name} ({lang}).\\n\\n"
        "1. Keep all non-text visuals unchanged.\\n"
        "2. Replace only the English text with natural, platform-appropriate localized text.\\n"
        "3. Keep layout, font weight, color, emphasis and visual hierarchy aligned with the original.\\n"
        "4. If localized text is longer, reduce font size slightly to fit without overflow."
    )


def _fallback_detail_prompt(lang: str, lang_name: str) -> str:
    return (
        f"你是一位专业的产品详情图本地化专家，请把图中的英文说明文案翻译成{lang_name}（{lang}）。\\n\\n"
        "要求：\\n"
        "- 仅替换文字，不修改产品、背景、图标和配色\\n"
        "- 保持原图布局、字号层级、文字位置和视觉重点\\n"
        "- 译文要自然地道，不要逐词硬译\\n"
        "- 若译文更长，可轻微缩小字体，但不能溢出"
    )


def _default_prompt(preset: str, lang: str) -> str:
    if (preset, lang) in _DEFAULTS:
        return _DEFAULTS[(preset, lang)]
    meta = _language_meta(lang)
    lang_name = meta.get("name_zh") or lang
    if preset == "cover":
        return _fallback_cover_prompt(lang, lang_name)
    return _fallback_detail_prompt(lang, lang_name)


def get_prompt(preset: str, lang: str) -> str:
    if preset not in PRESETS:
        raise ValueError("preset must be cover or detail")
    if not is_image_translate_language_supported(lang):
        raise ValueError(f"unsupported lang: {lang}")
    key = _key(preset, lang)
    value = _read(key)
    if value is None or value == "":
        default = _default_prompt(preset, lang)
        _write(key, default)
        return default
    return value


def update_prompt(preset: str, lang: str, value: str) -> None:
    if preset not in PRESETS:
        raise ValueError("preset must be cover or detail")
    if not is_image_translate_language_supported(lang):
        raise ValueError(f"unsupported lang: {lang}")
    _write(_key(preset, lang), value)


def list_all_prompts() -> dict[str, dict[str, str]]:
    return {
        row["code"]: get_prompts_for_lang(row["code"])
        for row in list_image_translate_languages()
    }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_image_translate_settings.py -q -k "filters_out_en or generic_prompt_for_dynamic_lang or uses_dynamic_languages"`

Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add appcore/image_translate_settings.py tests/test_image_translate_settings.py
git commit -m "feat: use dynamic image translate languages for prompts"
```

## Task 2: 让用户侧 prompt 接口使用动态语言校验

**Files:**
- Modify: `web/routes/image_translate.py`
- Test: `tests/test_image_translate_routes.py`

- [ ] **Step 1: 先写 `/api/image-translate/system-prompts` 的失败测试**

在 `tests/test_image_translate_routes.py` 里追加这两个用例：

```python
def test_system_prompts_endpoint_uses_dynamic_languages(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(
        r.its,
        "get_prompts_for_lang",
        lambda code: {"cover": f"cover-{code}", "detail": f"detail-{code}"},
    )

    resp = authed_client_no_db.get("/api/image-translate/system-prompts?lang=nl")

    assert resp.status_code == 200
    assert resp.get_json() == {"cover": "cover-nl", "detail": "detail-nl"}


def test_system_prompts_rejects_disabled_or_en_lang(authed_client_no_db, monkeypatch):
    from web.routes import image_translate as r

    monkeypatch.setattr(r.its, "is_image_translate_language_supported", lambda code: False)

    resp = authed_client_no_db.get("/api/image-translate/system-prompts?lang=en")

    assert resp.status_code == 400
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_image_translate_routes.py -q -k "uses_dynamic_languages or rejects_disabled_or_en_lang"`

Expected: 失败，原因会是 `web.routes.image_translate` 还没有 `its` 模块引用，或者接口仍按静态 `SUPPORTED_LANGS` 返回 400。

- [ ] **Step 3: 修改用户侧 prompt 接口的校验来源**

在 `web/routes/image_translate.py` 里把顶部导入和 `api_system_prompts()` 改成下面这样：

```python
from appcore import image_translate_settings as its
from appcore import medias, task_state, tos_clients
from appcore.db import execute as db_execute
from appcore.db import query_one as db_query_one
from appcore.gemini_image import IMAGE_MODELS, is_valid_image_model
from web import store
from web.services import image_translate_runner
```

```python
@bp.route("/api/image-translate/system-prompts", methods=["GET"])
@login_required
def api_system_prompts():
    lang = (request.args.get("lang") or "").strip().lower()
    if not its.is_image_translate_language_supported(lang):
        return jsonify({"error": "lang 不支持"}), 400
    return jsonify(its.get_prompts_for_lang(lang))
```

不要动 `upload/complete` 里现有的：

```python
if not medias.is_valid_language(lang_code) or lang_code == "en":
    return jsonify({"error": "目标语言不支持"}), 400
```

因为这段本来就已经是基于 `media_languages.enabled=1` 的动态校验。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_image_translate_routes.py -q -k "uses_dynamic_languages or rejects_disabled_or_en_lang"`

Expected: `2 passed`

- [ ] **Step 5: 提交**

```bash
git add web/routes/image_translate.py tests/test_image_translate_routes.py
git commit -m "feat: use dynamic language validation in image translate prompt api"
```

## Task 3: 让后台图片翻译 prompt 接口返回动态语言对象

**Files:**
- Modify: `web/routes/admin.py`
- Test: `tests/test_admin_image_translate_routes.py`

- [ ] **Step 1: 先写后台 prompt 接口的失败测试**

把 `tests/test_admin_image_translate_routes.py` 中的静态语言断言改成动态语言对象断言，并追加一个动态保存用例：

```python
def test_admin_get_all_prompts_returns_dynamic_languages(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    monkeypatch.setattr(its, "list_image_translate_languages", lambda: [
        {"code": "de", "name_zh": "德语", "sort_order": 1, "enabled": 1},
        {"code": "nl", "name_zh": "荷兰语", "sort_order": 2, "enabled": 1},
    ])
    monkeypatch.setattr(
        its,
        "list_all_prompts",
        lambda: {
            "de": {"cover": "cover-de", "detail": "detail-de"},
            "nl": {"cover": "cover-nl", "detail": "detail-nl"},
        },
    )

    r = authed_client_no_db.get("/admin/api/image-translate/prompts")

    assert r.status_code == 200
    data = r.get_json()
    assert data["languages"] == [
        {"code": "de", "name_zh": "德语"},
        {"code": "nl", "name_zh": "荷兰语"},
    ]
    assert set(data["prompts"].keys()) == {"de", "nl"}


def test_admin_get_prompts_for_dynamic_lang(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    monkeypatch.setattr(its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(
        its,
        "get_prompts_for_lang",
        lambda code: {"cover": f"cover-{code}", "detail": f"detail-{code}"},
    )

    r = authed_client_no_db.get("/admin/api/image-translate/prompts?lang=nl")

    assert r.status_code == 200
    assert r.get_json() == {"cover": "cover-nl", "detail": "detail-nl"}


def test_admin_post_prompt_accepts_dynamic_lang(authed_client_no_db, monkeypatch):
    from appcore import image_translate_settings as its

    calls = []

    monkeypatch.setattr(its, "is_image_translate_language_supported", lambda code: code == "nl")
    monkeypatch.setattr(its, "update_prompt", lambda preset, lang, value: calls.append((preset, lang, value)))

    r = authed_client_no_db.post(
        "/admin/api/image-translate/prompts",
        json={"preset": "cover", "lang": "nl", "value": "新的荷兰语封面 prompt"},
    )

    assert r.status_code == 200
    assert calls == [("cover", "nl", "新的荷兰语封面 prompt")]
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_admin_image_translate_routes.py -q -k "returns_dynamic_languages or get_prompts_for_dynamic_lang or accepts_dynamic_lang"`

Expected: 失败，当前接口返回的 `languages` 仍是静态字符串数组，且保存接口仍按静态 `SUPPORTED_LANGS` 校验。

- [ ] **Step 3: 修改后台 prompt 接口的返回结构和动态校验**

在 `web/routes/admin.py` 里把这两个函数改成下面这样：

```python
@bp.route("/api/image-translate/prompts", methods=["GET"])
@login_required
@admin_required
def get_image_translate_prompts():
    from appcore import image_translate_settings as its

    lang = (request.args.get("lang") or "").strip().lower()
    if lang:
        if not its.is_image_translate_language_supported(lang):
            return jsonify({"error": f"unsupported lang: {lang}"}), 400
        return jsonify(its.get_prompts_for_lang(lang))

    languages = [
        {"code": row["code"], "name_zh": row["name_zh"]}
        for row in its.list_image_translate_languages()
    ]
    return jsonify({
        "languages": languages,
        "presets": list(its.PRESETS),
        "prompts": its.list_all_prompts(),
    })


@bp.route("/api/image-translate/prompts", methods=["POST"])
@login_required
@admin_required
def set_image_translate_prompt():
    from appcore import image_translate_settings as its

    body = request.get_json(silent=True) or {}
    preset = (body.get("preset") or "").strip().lower()
    lang = (body.get("lang") or "").strip().lower()
    value = (body.get("value") or "").strip()

    if preset not in its.PRESETS:
        return jsonify({"error": "preset must be cover or detail"}), 400
    if not its.is_image_translate_language_supported(lang):
        return jsonify({"error": f"unsupported lang: {lang}"}), 400
    if not value:
        return jsonify({"error": "value required"}), 400

    its.update_prompt(preset, lang, value)
    return jsonify({"ok": True})
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_admin_image_translate_routes.py -q -k "returns_dynamic_languages or get_prompts_for_dynamic_lang or accepts_dynamic_lang"`

Expected: `3 passed`

- [ ] **Step 5: 提交**

```bash
git add web/routes/admin.py tests/test_admin_image_translate_routes.py
git commit -m "feat: use dynamic languages in admin image translate prompts api"
```

## Task 4: 给用户页和后台页补上语言空态容器

**Files:**
- Modify: `web/templates/image_translate_list.html`
- Modify: `web/templates/admin_settings.html`
- Test: `tests/test_image_translate_routes.py`
- Test: `tests/test_admin_image_translate_routes.py`

- [ ] **Step 1: 先写模板回归测试**

在 `tests/test_image_translate_routes.py` 追加：

```python
def test_image_translate_list_page_includes_language_empty_state_container(authed_client_no_db):
    resp = authed_client_no_db.get("/image-translate")
    assert resp.status_code == 200
    assert b'id="itLanguageEmpty"' in resp.data
```

在 `tests/test_admin_image_translate_routes.py` 追加：

```python
def test_admin_settings_page_includes_image_translate_empty_state_container(
    authed_client_no_db,
    monkeypatch,
):
    monkeypatch.setattr("web.routes.admin.get_all_retention_settings", lambda: {})
    monkeypatch.setattr("web.routes.admin.medias.list_languages_for_admin", lambda: [])

    resp = authed_client_no_db.get("/admin/settings")

    assert resp.status_code == 200
    assert b'id="imgTransPromptEmpty"' in resp.data
```

- [ ] **Step 2: 运行测试确认当前失败**

Run: `pytest tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py -q -k "empty_state_container"`

Expected: 失败，因为两个模板里都还没有对应的 DOM 容器。

- [ ] **Step 3: 在模板里增加空态容器**

在 `web/templates/image_translate_list.html` 的语言 pills 下方追加：

```html
<div id="itLanguageEmpty" class="inline-error" style="display:none"></div>
```

在 `web/templates/admin_settings.html` 的图片翻译 prompt 卡片里、语言 pills 下方追加：

```html
<div id="imgTransPromptEmpty" class="field-hint" hidden></div>
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py -q -k "empty_state_container"`

Expected: `2 passed`

- [ ] **Step 5: 提交**

```bash
git add web/templates/image_translate_list.html web/templates/admin_settings.html tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py
git commit -m "feat: add empty state containers for image translate language ui"
```

## Task 5: 接上前端动态语言对象和空态交互

**Files:**
- Modify: `web/templates/_image_translate_scripts.html`
- Modify: `web/templates/admin_settings.html`

- [ ] **Step 1: 修改用户侧图片翻译页脚本，处理无可用目标语言的空态**

在 `web/templates/_image_translate_scripts.html` 里加入这些变量和逻辑：

```javascript
var langEmptyEl = document.getElementById("itLanguageEmpty");

function setLanguageEmpty(msg) {
  if (!langEmptyEl) return;
  langEmptyEl.textContent = msg || "";
  langEmptyEl.style.display = msg ? "block" : "none";
}

function loadPromptForCurrent(){
  var lang = langEl.value;
  if (!lang) {
    promptEl.value = "";
    return Promise.resolve();
  }
  return fetch("/api/image-translate/system-prompts?lang=" + encodeURIComponent(lang),
               {credentials:"same-origin"})
    .then(function(r){return r.json();})
    .then(function(d){
      if (d && d[presetEl.value]) {
        promptEl.value = d[presetEl.value];
      }
    });
}

function loadLanguages(){
  return fetch("/api/languages",{credentials:"same-origin"})
    .then(function(r){return r.json();})
    .then(function(d){
      var items = (d.items || [])
        .filter(function(lang){ return lang.code !== "en"; })
        .map(function(lang){ return {value: lang.code, label: lang.name_zh}; });

      if (!items.length) {
        langEl.value = "";
        langPills.innerHTML = '<div class="empty">暂无可用目标语言，请先到系统设置启用小语种</div>';
        setLanguageEmpty("暂无可用目标语言，请先到系统设置启用小语种");
        promptEl.value = "";
        submitBtn.disabled = true;
        return [];
      }

      setLanguageEmpty("");
      renderPills(langPills, items);
      langEl.value = items[0].value;
      submitBtn.disabled = false;
      return items;
    });
}

loadLanguages().then(function(items){
  if (!items.length) return;
  bindPillGroup(langPills, langEl, function(){ loadPromptForCurrent(); });
  loadPromptForCurrent();
});
```

- [ ] **Step 2: 修改后台 prompt 卡片脚本，改用动态语言对象并处理空态**

把 `web/templates/admin_settings.html` 里图片翻译 prompt 卡片的脚本改成下面这种结构：

```javascript
var languages = [];
var currentLang = "";
var allPrompts = {};
var emptyEl = document.getElementById("imgTransPromptEmpty");
var saveCoverBtn = document.getElementById("imgTransSaveCover");
var saveDetailBtn = document.getElementById("imgTransSaveDetail");

function langName(code){
  var row = languages.find(function(item){ return item.code === code; });
  return row ? row.name_zh : code;
}

function setEmptyState(msg){
  var empty = !!msg;
  emptyEl.hidden = !empty;
  emptyEl.textContent = msg || "";
  coverTa.disabled = empty;
  detailTa.disabled = empty;
  saveCoverBtn.disabled = empty;
  saveDetailBtn.disabled = empty;
}

function renderPills(){
  pillsWrap.innerHTML = "";
  languages.forEach(function(item){
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "it-admin-pill" + (item.code === currentLang ? " is-active" : "");
    btn.setAttribute("data-lang", item.code);
    btn.textContent = item.name_zh;
    btn.onclick = function(){ switchLang(item.code); };
    pillsWrap.appendChild(btn);
  });
}

function fillTextareas(){
  if (!currentLang) {
    coverTa.value = "";
    detailTa.value = "";
    coverLabel.textContent = "-";
    detailLabel.textContent = "-";
    return;
  }
  var entry = allPrompts[currentLang] || {};
  coverTa.value = entry.cover || "";
  detailTa.value = entry.detail || "";
  coverLabel.textContent = langName(currentLang);
  detailLabel.textContent = langName(currentLang);
}

function fetchAllPrompts(){
  return fetch('/admin/api/image-translate/prompts', {credentials:'same-origin'})
    .then(function(r){return r.json();})
    .then(function(data){
      languages = Array.isArray(data.languages) ? data.languages : [];
      allPrompts = data.prompts || {};

      if (!languages.length) {
        currentLang = "";
        pillsWrap.innerHTML = "";
        setEmptyState("当前没有启用的小语种可供图片翻译使用，请先在上方素材语种配置中启用语种");
        fillTextareas();
        return;
      }

      setEmptyState("");
      currentLang = languages[0].code;
      renderPills();
      fillTextareas();
    });
}

function save(preset){
  if (!currentLang) return Promise.resolve();
  var ta = preset === "cover" ? coverTa : detailTa;
  return fetch('/admin/api/image-translate/prompts', {
    method:'POST',
    credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({preset: preset, lang: currentLang, value: ta.value}),
  }).then(function(r){return r.json().then(function(d){return {ok: r.ok, body: d};});})
    .then(function(res){
      if (!res.ok || (res.body && res.body.error)) {
        alert((res.body && res.body.error) || "保存失败");
        return;
      }
      if (!allPrompts[currentLang]) allPrompts[currentLang] = {};
      allPrompts[currentLang][preset] = ta.value;
      alert('已保存 ' + langName(currentLang) + ' 的' + (preset === "cover" ? "封面图" : "产品详情图") + " prompt");
    });
}
```

- [ ] **Step 3: 跑一轮目标测试，确认模板语法和接口契约没有回归**

Run: `pytest tests/test_image_translate_settings.py tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py -q`

Expected: 全部通过，没有模板渲染错误，也没有因为接口返回结构变化导致测试断言失败。

- [ ] **Step 4: 按下面步骤做手工联调**

Run: `python main.py`

在浏览器里验证两组场景：

1. 只有 `en` 启用时

- 打开 `/image-translate`
- 目标语言区域显示“暂无可用目标语言，请先到系统设置启用小语种”
- 提交按钮为禁用状态
- 打开 `/admin/settings`
- 图片翻译 prompt 卡片不显示语言 pills
- `封面 prompt`、`详情 prompt` 文本框和保存按钮为禁用状态

2. 新增并启用 `nl` 后

- 在 `/admin/settings` 的素材语种配置里新增 `nl / 荷兰语`
- 刷新 `/image-translate`
- 能看到“荷兰语” pill，切换后会加载一条通用兜底 prompt
- 刷新 `/admin/settings`
- 图片翻译 prompt 卡片出现“荷兰语” pill
- 修改荷兰语 `cover` prompt 并保存
- 请求 `/admin/api/image-translate/prompts?lang=nl`，返回值里 `cover` 是刚保存的内容

- [ ] **Step 5: 提交**

```bash
git add web/templates/_image_translate_scripts.html web/templates/admin_settings.html
git commit -m "feat: wire dynamic languages into image translate ui"
```

## Task 6: 做收口验证并检查改动边界

**Files:**
- Modify: `appcore/image_translate_settings.py`
- Modify: `web/routes/image_translate.py`
- Modify: `web/routes/admin.py`
- Modify: `web/templates/image_translate_list.html`
- Modify: `web/templates/_image_translate_scripts.html`
- Modify: `web/templates/admin_settings.html`
- Modify: `tests/test_image_translate_settings.py`
- Modify: `tests/test_image_translate_routes.py`
- Modify: `tests/test_admin_image_translate_routes.py`

- [ ] **Step 1: 运行完整目标测试集**

Run: `pytest tests/test_image_translate_settings.py tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py -q`

Expected: 所有相关测试通过。

- [ ] **Step 2: 检查工作区只包含这次特性的预期改动**

Run: `git status --short`

Expected: 只看到这 9 个文件的修改，或者已经是干净工作区。

- [ ] **Step 3: 检查最终差异，确认没有残留静态语种写法**

Run: `git diff -- appcore/image_translate_settings.py web/routes/image_translate.py web/routes/admin.py web/templates/image_translate_list.html web/templates/_image_translate_scripts.html web/templates/admin_settings.html tests/test_image_translate_settings.py tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py`

Expected: 不再新增依赖 `SUPPORTED_LANGS` 作为图片翻译模块的唯一语种来源，后台脚本不再硬编码 `LANG_LABELS/SUPPORTED`。

- [ ] **Step 4: 如果 Step 1-3 都满足，再做最终提交**

```bash
git add appcore/image_translate_settings.py web/routes/image_translate.py web/routes/admin.py web/templates/image_translate_list.html web/templates/_image_translate_scripts.html web/templates/admin_settings.html tests/test_image_translate_settings.py tests/test_image_translate_routes.py tests/test_admin_image_translate_routes.py
git commit -m "feat: unify image translate languages with media language settings"
```
