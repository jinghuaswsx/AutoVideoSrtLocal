# 声音仓库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增"声音仓库"独立菜单（浏览 + 视频匹配两个 Tab），外加管理员后台的 ElevenLabs 共享音色同步入口。

**Architecture:** 独立 `voice_library` blueprint 读全局 `elevenlabs_voices` 表；匹配任务走 TOS 直传 + 内存任务状态机 + 轮询；管理员同步借助已存在的 `pipeline/voice_library_sync.py`，叠加进度回调 + SocketIO 推送。

**Tech Stack:** Flask + flask-socketio + resemblyzer + TOS(火山云) + MySQL (JSON_EXTRACT) + 原生 JS（无框架）。

**Spec:** `docs/superpowers/specs/2026-04-17-voice-library-design.md`

---

## 任务总览

| # | 阶段 | 产物 |
|---|---|---|
| Phase 1 | 浏览后端 | `appcore/voice_library_browse.py` + `web/routes/voice_library.py`（filters/list 接口） |
| Phase 2 | 浏览前端 | `voice_library.html` + `voice_library.js` 的 Tab 1（筛选/分页/试听） |
| Phase 3 | 匹配任务基础 | `appcore/voice_match_tasks.py`（状态机 + TTL 清理） |
| Phase 4 | 匹配接口 | `/match/upload-url` + `/match/start` + `/match/status` |
| Phase 5 | 匹配前端 | `voice_library.js` 的 Tab 2（上传/轮询/结果） |
| Phase 6 | 菜单接入 | `layout.html` 新菜单项 |
| Phase 7 | 管理员同步 | `appcore/voice_library_sync_task.py` + `pipeline/voice_library_sync.py` 回调 + admin 接口 + admin UI |
| Phase 8 | 收官 | 全量 pytest + 手测 checklist |

---

## Phase 1 · 浏览后端

### Task 1.1 · 启用语种 helper

**Files:**
- Test: `tests/test_medias_languages.py`（已存在就追加一条用例）
- Modify: `appcore/medias.py`

- [ ] **Step 1：写失败测试**

追加到 `tests/test_medias_languages.py`（不存在则新建）：

```python
from appcore import medias


def test_list_enabled_language_codes_returns_only_enabled(tmp_db):
    medias.create_language("de", "德语", 1, True)
    medias.create_language("fr", "法语", 2, True)
    medias.create_language("es", "西班牙语", 3, False)
    codes = medias.list_enabled_language_codes()
    assert codes == ["de", "fr"]
```

- [ ] **Step 2：运行确认失败**

```
pytest tests/test_medias_languages.py::test_list_enabled_language_codes_returns_only_enabled -v
```

期望 FAIL：`AttributeError: module 'appcore.medias' has no attribute 'list_enabled_language_codes'`

- [ ] **Step 3：实现**

在 `appcore/medias.py` 追加：

```python
def list_enabled_language_codes() -> list[str]:
    """按 sort_order / code 返回所有 enabled=1 语种的 code 列表。"""
    rows = query(
        "SELECT code FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [r["code"] for r in rows]
```

- [ ] **Step 4：跑测试**

```
pytest tests/test_medias_languages.py -v
```

期望 PASS。

- [ ] **Step 5：commit**

```
git add appcore/medias.py tests/test_medias_languages.py
git commit -m "feat(medias): 新增 list_enabled_language_codes 辅助函数"
```

---

### Task 1.2 · `voice_library_browse.py` 查询 service

**Files:**
- Create: `appcore/voice_library_browse.py`
- Create: `tests/test_voice_library_browse.py`

- [ ] **Step 1：写失败测试**

`tests/test_voice_library_browse.py`：

```python
import json
import pytest

from appcore.db import execute
from appcore.voice_library_browse import list_voices, list_filter_options


def _seed_voice(voice_id, language, gender="male", labels=None, name=None):
    labels = labels or {}
    execute(
        "INSERT INTO elevenlabs_voices "
        "(voice_id, name, gender, language, age, accent, category, "
        " descriptive, preview_url, labels_json, synced_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())",
        (voice_id, name or voice_id, gender, language,
         labels.get("age"), labels.get("accent"), "professional",
         labels.get("descriptive"), "https://preview/" + voice_id,
         json.dumps(labels))
    )


@pytest.mark.usefixtures("tmp_db")
class TestListVoices:
    def test_language_required(self):
        with pytest.raises(ValueError):
            list_voices(language="")

    def test_filter_by_language_and_gender(self):
        _seed_voice("a", "de", "male")
        _seed_voice("b", "de", "female")
        _seed_voice("c", "fr", "male")
        result = list_voices(language="de", gender="male")
        assert result["total"] == 1
        assert result["items"][0]["voice_id"] == "a"

    def test_multi_select_use_case(self):
        _seed_voice("a", "de", labels={"use_case": "narrative"})
        _seed_voice("b", "de", labels={"use_case": "advertisement"})
        _seed_voice("c", "de", labels={"use_case": "podcast"})
        result = list_voices(
            language="de", use_cases=["narrative", "advertisement"],
        )
        assert {x["voice_id"] for x in result["items"]} == {"a", "b"}

    def test_search_q_matches_name(self):
        _seed_voice("a", "de", name="Marcus the Storyteller")
        _seed_voice("b", "de", name="Anna")
        result = list_voices(language="de", q="storyteller")
        assert [x["voice_id"] for x in result["items"]] == ["a"]

    def test_pagination(self):
        for i in range(10):
            _seed_voice(f"v{i}", "de")
        result = list_voices(language="de", page=2, page_size=3)
        assert result["total"] == 10
        assert result["page"] == 2
        assert len(result["items"]) == 3


@pytest.mark.usefixtures("tmp_db")
class TestListFilterOptions:
    def test_only_values_present_in_language(self):
        _seed_voice("a", "de", labels={
            "use_case": "narrative", "accent": "german",
            "age": "middle_aged", "descriptive": "deep",
        })
        _seed_voice("b", "fr", labels={"use_case": "advertisement"})
        opts = list_filter_options(language="de")
        assert opts["use_cases"] == ["narrative"]
        assert opts["accents"] == ["german"]
        assert opts["ages"] == ["middle_aged"]
        assert opts["descriptives"] == ["deep"]
```

测试依赖项目已有的 `tmp_db` fixture（在 `tests/conftest.py` 中）。如果项目内没有，先用 grep 确认 fixture 名；如无，改用现有 `tests/test_voice_library.py` 的 fixture 复用方式。

- [ ] **Step 2：跑测试确认失败**

```
pytest tests/test_voice_library_browse.py -v
```

期望 FAIL：`ModuleNotFoundError: No module named 'appcore.voice_library_browse'`

- [ ] **Step 3：实现 service**

`appcore/voice_library_browse.py`：

```python
"""
声音仓库浏览服务：查询 elevenlabs_voices 表，支持筛选 / 分页 / 枚举。
"""
from __future__ import annotations

import json
from typing import Optional

from appcore.db import query, query_one


_SELECT_FIELDS = (
    "voice_id, name, gender, language, age, accent, category, "
    "descriptive, preview_url, labels_json"
)


def _row_to_dict(row: dict) -> dict:
    labels = row.get("labels_json")
    if isinstance(labels, str):
        try:
            labels = json.loads(labels)
        except (json.JSONDecodeError, TypeError):
            labels = {}
    row = dict(row)
    row["labels"] = labels or {}
    row.pop("labels_json", None)
    row["use_case"] = (labels or {}).get("use_case")
    row["description"] = (labels or {}).get("description") or row.get("descriptive") or ""
    return row


def list_voices(
    *,
    language: str,
    gender: Optional[str] = None,
    use_cases: Optional[list[str]] = None,
    accents: Optional[list[str]] = None,
    ages: Optional[list[str]] = None,
    descriptives: Optional[list[str]] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 48,
) -> dict:
    if not language:
        raise ValueError("language is required")
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))

    where = ["language = %s"]
    params: list = [language]
    if gender in ("male", "female"):
        where.append("gender = %s")
        params.append(gender)

    def _json_in(field: str, values: list[str]):
        marks = ",".join(["%s"] * len(values))
        where.append(
            f"JSON_UNQUOTE(JSON_EXTRACT(labels_json, '$.{field}')) IN ({marks})"
        )
        params.extend(values)

    if use_cases:
        _json_in("use_case", use_cases)
    if accents:
        _json_in("accent", accents)
    if ages:
        _json_in("age", ages)
    if descriptives:
        _json_in("descriptive", descriptives)
    if q:
        like = f"%{q}%"
        where.append("(name LIKE %s OR descriptive LIKE %s)")
        params.extend([like, like])

    where_sql = " AND ".join(where)
    total_row = query_one(
        f"SELECT COUNT(*) AS c FROM elevenlabs_voices WHERE {where_sql}",
        tuple(params),
    )
    total = int(total_row["c"]) if total_row else 0

    offset = (page - 1) * page_size
    rows = query(
        f"SELECT {_SELECT_FIELDS} FROM elevenlabs_voices "
        f"WHERE {where_sql} "
        f"ORDER BY (category='professional') DESC, synced_at DESC, voice_id ASC "
        f"LIMIT %s OFFSET %s",
        tuple(params) + (page_size, offset),
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_row_to_dict(r) for r in rows],
    }


def list_filter_options(*, language: str) -> dict:
    if not language:
        raise ValueError("language is required")
    rows = query(
        "SELECT labels_json FROM elevenlabs_voices WHERE language = %s",
        (language,),
    )
    use_cases: set[str] = set()
    accents: set[str] = set()
    ages: set[str] = set()
    descriptives: set[str] = set()
    for r in rows:
        labels = r.get("labels_json") or {}
        if isinstance(labels, str):
            try:
                labels = json.loads(labels)
            except (json.JSONDecodeError, TypeError):
                labels = {}
        if v := labels.get("use_case"):
            use_cases.add(v)
        if v := labels.get("accent"):
            accents.add(v)
        if v := labels.get("age"):
            ages.add(v)
        if v := labels.get("descriptive"):
            descriptives.add(v)
    return {
        "use_cases": sorted(use_cases),
        "accents": sorted(accents),
        "ages": sorted(ages),
        "descriptives": sorted(descriptives),
    }
```

- [ ] **Step 4：跑测试**

```
pytest tests/test_voice_library_browse.py -v
```

期望 PASS。

- [ ] **Step 5：commit**

```
git add appcore/voice_library_browse.py tests/test_voice_library_browse.py
git commit -m "feat(voice-library): 新增浏览查询 service（list_voices / list_filter_options）"
```

---

### Task 1.3 · `voice_library` blueprint 的 filters / list 接口

**Files:**
- Create: `web/routes/voice_library.py`
- Create: `tests/test_voice_library_routes.py`

- [ ] **Step 1：写失败测试**

`tests/test_voice_library_routes.py`：

```python
import json
import pytest

from appcore.db import execute


@pytest.mark.usefixtures("tmp_db", "login_user")
class TestFiltersAndList:
    def _seed(self, voice_id, lang, gender="male", labels=None):
        labels = labels or {}
        execute(
            "INSERT INTO elevenlabs_voices "
            "(voice_id, name, gender, language, synced_at, updated_at, labels_json)"
            " VALUES (%s,%s,%s,%s,NOW(),NOW(),%s)",
            (voice_id, voice_id, gender, lang, json.dumps(labels))
        )

    def test_filters_requires_language(self, client):
        # language 可选 → 返回 languages 列表和空枚举
        resp = client.get("/voice-library/api/filters")
        assert resp.status_code == 200

    def test_filters_scoped_by_language(self, client):
        self._seed("a", "de", labels={"use_case": "narrative"})
        self._seed("b", "fr", labels={"use_case": "advertisement"})
        resp = client.get("/voice-library/api/filters?language=de")
        data = resp.get_json()
        assert "narrative" in data["use_cases"]
        assert "advertisement" not in data["use_cases"]

    def test_list_language_required(self, client):
        resp = client.get("/voice-library/api/list")
        assert resp.status_code == 400

    def test_list_returns_items(self, client):
        self._seed("a", "de")
        resp = client.get("/voice-library/api/list?language=de")
        data = resp.get_json()
        assert data["total"] == 1
        assert data["items"][0]["voice_id"] == "a"

    def test_auth_required(self, client_unauth):
        resp = client_unauth.get("/voice-library/api/list?language=de")
        assert resp.status_code in (302, 401)
```

（fixture 名按项目 `tests/conftest.py` 现有定义：`client`、`client_unauth`、`login_user`、`tmp_db`；如有差异按项目约定替换。）

- [ ] **Step 2：跑测试确认失败**

```
pytest tests/test_voice_library_routes.py -v
```

期望 FAIL（404）。

- [ ] **Step 3：实现 blueprint**

`web/routes/voice_library.py`：

```python
"""
声音仓库 blueprint：浏览全局 elevenlabs_voices + 匹配任务入口。
仅做 HTTP 边界；查询逻辑在 appcore/voice_library_browse.py。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from appcore import medias
from appcore.voice_library_browse import list_voices, list_filter_options

log = logging.getLogger(__name__)

bp = Blueprint("voice_library", __name__, url_prefix="/voice-library")


@bp.route("", methods=["GET"])
@bp.route("/", methods=["GET"])
@login_required
def page():
    return render_template("voice_library.html")


@bp.route("/api/filters", methods=["GET"])
@login_required
def api_filters():
    language = (request.args.get("language") or "").strip().lower()
    languages = [
        {"code": code, "name_zh": name_zh}
        for code, name_zh in medias.list_enabled_languages_kv()
    ]
    payload = {
        "languages": languages,
        "genders": ["male", "female"],
        "use_cases": [], "accents": [], "ages": [], "descriptives": [],
    }
    if language:
        payload.update(list_filter_options(language=language))
    return jsonify(payload)


def _split_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [x for x in (s.strip() for s in raw.split(",")) if x]


@bp.route("/api/list", methods=["GET"])
@login_required
def api_list():
    language = (request.args.get("language") or "").strip().lower()
    if not language:
        return jsonify({"error": "language is required"}), 400
    try:
        result = list_voices(
            language=language,
            gender=(request.args.get("gender") or "").strip() or None,
            use_cases=_split_csv(request.args.get("use_case")),
            accents=_split_csv(request.args.get("accent")),
            ages=_split_csv(request.args.get("age")),
            descriptives=_split_csv(request.args.get("descriptive")),
            q=(request.args.get("q") or "").strip() or None,
            page=int(request.args.get("page") or 1),
            page_size=int(request.args.get("page_size") or 48),
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)
```

需要 `medias.list_enabled_languages_kv()` 返回 `[(code, name_zh), ...]`。如无该函数，追加到 `appcore/medias.py`：

```python
def list_enabled_languages_kv() -> list[tuple[str, str]]:
    rows = query(
        "SELECT code, name_zh FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )
    return [(r["code"], r["name_zh"]) for r in rows]
```

- [ ] **Step 4：创建极简 `voice_library.html`（确保页面路由不 500）**

`web/templates/voice_library.html`：

```html
{% extends "layout.html" %}
{% block title %}声音仓库{% endblock %}
{% block content %}
<div class="oc voice-library-root" data-current-user-id="{{ current_user.id }}">
  <div class="oc-tabs">
    <a href="#browse" class="oc-tab">浏览试听</a>
    <a href="#match" class="oc-tab">视频匹配</a>
  </div>
  <section id="tab-browse" class="oc-tab-panel"></section>
  <section id="tab-match" class="oc-tab-panel" hidden></section>
</div>
{% include "_voice_library_styles.html" %}
{% include "_voice_library_scripts.html" %}
{% endblock %}
```

占位 include 文件（后续 Phase 2 / 5 填充）：

`web/templates/_voice_library_styles.html`：
```html
<style>/* voice library styles */</style>
```

`web/templates/_voice_library_scripts.html`：
```html
<script src="{{ url_for('static', filename='voice_library.js') }}"></script>
```

`web/static/voice_library.js`：
```js
// voice library entrypoint — populated in Phase 2 / 5
(function () {})();
```

- [ ] **Step 5：注册 blueprint 到 `web/app.py`**

在 `web/app.py` 现有 blueprint 导入段追加：

```python
from web.routes.voice_library import bp as voice_library_bp
```

在 `create_app()` 的 `register_blueprint` 序列里（`voice_bp` 附近）追加：

```python
app.register_blueprint(voice_library_bp)
```

- [ ] **Step 6：跑测试**

```
pytest tests/test_voice_library_routes.py tests/test_voice_library_browse.py -v
```

期望 PASS。

- [ ] **Step 7：commit**

```
git add appcore/medias.py web/routes/voice_library.py web/templates/voice_library.html \
        web/templates/_voice_library_styles.html web/templates/_voice_library_scripts.html \
        web/static/voice_library.js web/app.py tests/test_voice_library_routes.py
git commit -m "feat(voice-library): 新增 blueprint + filters/list 接口 + 页面骨架"
```

---

## Phase 2 · 浏览前端 Tab 1

### Task 2.1 · Tab 1 UI（筛选抽屉 + 卡片网格 + 分页）

前端无自动化测试，按"实现 → 手测 → commit"。

**Files:**
- Modify: `web/templates/voice_library.html`
- Modify: `web/templates/_voice_library_styles.html`
- Modify: `web/static/voice_library.js`

- [ ] **Step 1：填充 Tab 1 HTML 骨架**

将 `voice_library.html` 里 `#tab-browse` 替换为：

```html
<section id="tab-browse" class="oc-tab-panel vl-browse">
  <aside class="vl-filter-panel">
    <div class="vl-filter-group">
      <label>语言</label>
      <div id="vl-browse-languages" class="vl-pill-row"></div>
    </div>
    <div class="vl-filter-group">
      <label>性别</label>
      <div id="vl-browse-gender" class="vl-pill-row"></div>
    </div>
    <div class="vl-filter-group"><label>用途</label><select id="vl-use-case" multiple></select></div>
    <div class="vl-filter-group"><label>口音</label><select id="vl-accent" multiple></select></div>
    <div class="vl-filter-group"><label>年龄</label><select id="vl-age" multiple></select></div>
    <div class="vl-filter-group"><label>音色</label><select id="vl-descriptive" multiple></select></div>
    <div class="vl-filter-group">
      <label>关键词</label>
      <input id="vl-q" type="text" placeholder="搜索名字或描述" />
    </div>
    <button id="vl-reset-filters" class="oc-btn-ghost">重置筛选</button>
  </aside>
  <main class="vl-main">
    <div id="vl-grid" class="vl-grid"></div>
    <div id="vl-empty" class="vl-empty" hidden></div>
    <nav class="vl-pager">
      <button id="vl-prev" class="oc-btn-secondary">上一页</button>
      <span id="vl-page-info"></span>
      <button id="vl-next" class="oc-btn-secondary">下一页</button>
    </nav>
  </main>
</section>
```

- [ ] **Step 2：样式（遵循 Ocean Blue token）**

替换 `_voice_library_styles.html`（用 `--space-*` `--radius-*` `--accent` 等 token）：

```html
<style>
  .voice-library-root { display:flex; flex-direction:column; gap:var(--space-5); padding:var(--content-pad); }
  .oc-tabs { display:flex; gap:var(--space-6); border-bottom:1px solid var(--border); }
  .oc-tab { padding:var(--space-3) var(--space-4); color:var(--fg-muted); text-decoration:none; border-bottom:2px solid transparent; }
  .oc-tab.is-active { color:var(--accent); border-bottom-color:var(--accent); }
  .vl-browse { display:grid; grid-template-columns:260px 1fr; gap:var(--space-5); }
  .vl-filter-panel { display:flex; flex-direction:column; gap:var(--space-4); padding:var(--space-5); background:var(--bg-subtle); border-radius:var(--radius-lg); }
  .vl-filter-group label { display:block; font-size:var(--text-xs); color:var(--fg-muted); margin-bottom:var(--space-2); text-transform:uppercase; letter-spacing:.05em; }
  .vl-filter-group select, .vl-filter-group input[type=text] { width:100%; height:32px; border:1px solid var(--border-strong); border-radius:var(--radius); padding:0 var(--space-3); }
  .vl-filter-group select[multiple] { height:auto; min-height:80px; padding:var(--space-2); }
  .vl-pill-row { display:flex; flex-wrap:wrap; gap:var(--space-2); }
  .vl-pill { padding:4px var(--space-3); border:1px solid var(--border-strong); border-radius:var(--radius-full); background:#fff; cursor:pointer; font-size:var(--text-sm); }
  .vl-pill.is-active { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  .vl-grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); gap:var(--space-4); }
  .vl-card { padding:var(--space-5); background:#fff; border:1px solid var(--border); border-radius:var(--radius-lg); display:flex; flex-direction:column; gap:var(--space-3); }
  .vl-card-title { font-size:var(--text-md); font-weight:600; display:flex; justify-content:space-between; align-items:center; }
  .vl-chip-row { display:flex; flex-wrap:wrap; gap:var(--space-2); }
  .vl-chip { padding:2px var(--space-2); background:var(--accent-subtle); color:var(--accent); border-radius:var(--radius-sm); font-size:var(--text-xs); }
  .vl-chip.is-female { background:var(--cyan-subtle); color:var(--cyan); }
  .vl-desc { color:var(--fg-muted); font-size:var(--text-sm); line-height:var(--leading); }
  .vl-play-btn { align-self:flex-end; padding:var(--space-2) var(--space-3); border-radius:var(--radius); border:1px solid var(--border-strong); background:#fff; cursor:pointer; }
  .vl-play-btn.is-playing { background:var(--accent); color:var(--accent-fg); border-color:var(--accent); }
  .vl-pager { display:flex; justify-content:center; align-items:center; gap:var(--space-4); margin-top:var(--space-6); }
  .vl-empty { padding:var(--space-8); text-align:center; color:var(--fg-muted); }
</style>
```

- [ ] **Step 3：JS 实现 Tab 1**

`web/static/voice_library.js`：

```js
(function () {
  const state = {
    tab: "browse",
    language: "", gender: "", q: "",
    use_case: [], accent: [], age: [], descriptive: [],
    page: 1, pageSize: 48,
  };
  const audio = new Audio();
  let playingVoiceId = null;

  function $(sel) { return document.querySelector(sel); }
  function $all(sel) { return [...document.querySelectorAll(sel)]; }

  function setActiveTab(name) {
    state.tab = name;
    $all(".oc-tab").forEach(el => el.classList.toggle("is-active", el.getAttribute("href") === "#" + name));
    $("#tab-browse").hidden = name !== "browse";
    $("#tab-match").hidden = name !== "match";
  }

  function syncTabFromHash() {
    const want = (location.hash || "#browse").slice(1);
    setActiveTab(want === "match" ? "match" : "browse");
  }

  async function fetchJSON(url) {
    const resp = await fetch(url, {credentials: "same-origin"});
    if (!resp.ok) throw new Error(await resp.text());
    return resp.json();
  }

  function buildListUrl() {
    const q = new URLSearchParams();
    q.set("language", state.language);
    if (state.gender) q.set("gender", state.gender);
    if (state.q) q.set("q", state.q);
    for (const k of ["use_case", "accent", "age", "descriptive"]) {
      if (state[k].length) q.set(k, state[k].join(","));
    }
    q.set("page", String(state.page));
    q.set("page_size", String(state.pageSize));
    return "/voice-library/api/list?" + q.toString();
  }

  function renderLangPills(langs) {
    const box = $("#vl-browse-languages");
    box.innerHTML = "";
    langs.forEach(l => {
      const b = document.createElement("button");
      b.className = "vl-pill";
      b.textContent = l.name_zh;
      b.dataset.code = l.code;
      if (l.code === state.language) b.classList.add("is-active");
      b.addEventListener("click", () => {
        state.language = l.code;
        state.page = 1;
        refreshFiltersAndList();
      });
      box.appendChild(b);
    });
  }

  function renderGenderPills() {
    const box = $("#vl-browse-gender");
    box.innerHTML = "";
    [["", "全部"], ["male", "男"], ["female", "女"]].forEach(([val, label]) => {
      const b = document.createElement("button");
      b.className = "vl-pill";
      b.textContent = label;
      if (val === state.gender) b.classList.add("is-active");
      b.addEventListener("click", () => {
        state.gender = val; state.page = 1; loadList();
        renderGenderPills();
      });
      box.appendChild(b);
    });
  }

  function renderMultiSelect(id, values, stateKey) {
    const sel = $("#" + id);
    sel.innerHTML = "";
    values.forEach(v => {
      const opt = document.createElement("option");
      opt.value = v; opt.textContent = v;
      if (state[stateKey].includes(v)) opt.selected = true;
      sel.appendChild(opt);
    });
    sel.onchange = () => {
      state[stateKey] = [...sel.selectedOptions].map(o => o.value);
      state.page = 1; loadList();
    };
  }

  function renderCard(v) {
    const card = document.createElement("article");
    card.className = "vl-card";
    const chips = [];
    if (v.accent) chips.push(["accent", v.accent]);
    if (v.age) chips.push(["age", v.age]);
    if (v.descriptive) chips.push(["descriptive", v.descriptive]);
    if (v.use_case) chips.push(["use_case", v.use_case]);
    const chipsHtml = chips.map(([k, val]) =>
      `<span class="vl-chip">${escapeHtml(val)}</span>`).join("");
    card.innerHTML = `
      <div class="vl-card-title">
        <span>${escapeHtml(v.name)}</span>
        <span class="vl-chip ${v.gender === "female" ? "is-female" : ""}">${v.gender || ""}</span>
      </div>
      <div class="vl-chip-row">${chipsHtml}</div>
      <div class="vl-desc">${escapeHtml((v.description || "").slice(0, 80))}</div>
      <button class="vl-play-btn" data-voice="${v.voice_id}" data-url="${v.preview_url || ""}">▶ 试听</button>
    `;
    card.querySelector(".vl-play-btn").addEventListener("click", (e) => togglePlay(e.currentTarget));
    return card;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  }

  function togglePlay(btn) {
    const url = btn.dataset.url;
    const vid = btn.dataset.voice;
    if (!url) return;
    if (playingVoiceId === vid) {
      audio.pause(); audio.src = "";
      btn.classList.remove("is-playing");
      btn.textContent = "▶ 试听";
      playingVoiceId = null;
      return;
    }
    $all(".vl-play-btn.is-playing").forEach(b => {
      b.classList.remove("is-playing");
      b.textContent = "▶ 试听";
    });
    audio.src = url; audio.play();
    btn.classList.add("is-playing"); btn.textContent = "■ 停止";
    playingVoiceId = vid;
  }

  async function loadList() {
    const data = await fetchJSON(buildListUrl());
    const grid = $("#vl-grid"); grid.innerHTML = "";
    $("#vl-empty").hidden = data.total > 0;
    if (data.total === 0) $("#vl-empty").textContent = "没有匹配当前筛选的音色";
    data.items.forEach(v => grid.appendChild(renderCard(v)));
    const pages = Math.max(1, Math.ceil(data.total / data.page_size));
    $("#vl-page-info").textContent = `第 ${data.page} / ${pages} 页（共 ${data.total}）`;
    $("#vl-prev").disabled = data.page <= 1;
    $("#vl-next").disabled = data.page >= pages;
  }

  async function refreshFiltersAndList() {
    if (!state.language) return;
    const opts = await fetchJSON("/voice-library/api/filters?language=" + encodeURIComponent(state.language));
    renderLangPills(opts.languages);
    renderGenderPills();
    renderMultiSelect("vl-use-case", opts.use_cases, "use_case");
    renderMultiSelect("vl-accent", opts.accents, "accent");
    renderMultiSelect("vl-age", opts.ages, "age");
    renderMultiSelect("vl-descriptive", opts.descriptives, "descriptive");
    await loadList();
  }

  async function bootstrap() {
    const opts = await fetchJSON("/voice-library/api/filters");
    if (!opts.languages.length) {
      $("#vl-empty").hidden = false;
      $("#vl-empty").textContent = "系统尚未配置任何启用的小语种";
      return;
    }
    state.language = opts.languages[0].code;
    await refreshFiltersAndList();
  }

  function bindEvents() {
    $all(".oc-tab").forEach(a => a.addEventListener("click", () => {
      history.replaceState(null, "", a.getAttribute("href"));
      syncTabFromHash();
    }));
    window.addEventListener("hashchange", syncTabFromHash);
    $("#vl-prev").addEventListener("click", () => { state.page--; loadList(); });
    $("#vl-next").addEventListener("click", () => { state.page++; loadList(); });
    $("#vl-reset-filters").addEventListener("click", () => {
      state.gender = ""; state.q = "";
      state.use_case = []; state.accent = []; state.age = []; state.descriptive = [];
      state.page = 1;
      refreshFiltersAndList();
    });
    const qInput = $("#vl-q");
    let qTimer = null;
    qInput.addEventListener("input", () => {
      clearTimeout(qTimer);
      qTimer = setTimeout(() => { state.q = qInput.value.trim(); state.page = 1; loadList(); }, 300);
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncTabFromHash();
    bindEvents();
    bootstrap();
  });
})();
```

- [ ] **Step 4：手测**

1. `python main.py` 启动
2. 登录后访问 `/voice-library`
3. 确认：语言 pill 可切换；筛选器联动；分页按钮正常；试听同时只播一个；重置筛选 OK。

- [ ] **Step 5：commit**

```
git add web/templates/voice_library.html web/templates/_voice_library_styles.html \
        web/static/voice_library.js
git commit -m "feat(voice-library): 浏览试听 Tab（筛选 / 分页 / 单例试听）"
```

---

## Phase 3 · 匹配任务基础设施

### Task 3.1 · `voice_match_tasks.py`

**Files:**
- Create: `appcore/voice_match_tasks.py`
- Create: `tests/test_voice_match_tasks.py`

- [ ] **Step 1：写失败测试**

`tests/test_voice_match_tasks.py`：

```python
import time

import numpy as np
import pytest

from appcore import voice_match_tasks as vmt


class _Boom(Exception):
    pass


@pytest.fixture(autouse=True)
def reset_tasks():
    vmt._TASKS.clear()
    yield
    vmt._TASKS.clear()


def test_create_task_returns_pending():
    tid = vmt.create_task(user_id=1, object_key="voice_match/1/x/demo.mp4",
                          language="de", gender="male")
    t = vmt.get_task(tid, user_id=1)
    assert t["status"] == "pending"
    assert t["progress"] == 0


def test_get_task_other_user_returns_none():
    tid = vmt.create_task(user_id=1, object_key="voice_match/1/x/demo.mp4",
                          language="de", gender="male")
    assert vmt.get_task(tid, user_id=2) is None


def test_run_task_success(monkeypatch, tmp_path):
    monkeypatch.setattr(vmt, "_download_tos_to_local", lambda key, path: path)
    monkeypatch.setattr(vmt, "_extract_sample_clip",
                        lambda p, out_dir: str(tmp_path / "clip.wav"))
    monkeypatch.setattr(vmt, "_embed_audio_file", lambda p: np.ones(256, dtype=np.float32))
    monkeypatch.setattr(vmt, "_match_candidates", lambda vec, **_: [
        {"voice_id": "x", "similarity": 0.9, "name": "X", "gender": "male",
         "language": "de", "preview_url": "https://x"}
    ])
    monkeypatch.setattr(vmt, "_upload_to_tos_signed",
                        lambda path, key: "https://signed.clip.wav")

    tid = vmt.create_task(user_id=1, object_key="voice_match/1/x/demo.mp4",
                          language="de", gender="male")
    vmt._run_task_sync(tid)
    t = vmt.get_task(tid, user_id=1)
    assert t["status"] == "done"
    assert t["result"]["candidates"][0]["voice_id"] == "x"
    assert t["result"]["sample_audio_url"] == "https://signed.clip.wav"


def test_run_task_failure_marks_failed(monkeypatch):
    def boom(*a, **kw): raise _Boom("ffmpeg missing")
    monkeypatch.setattr(vmt, "_download_tos_to_local", boom)
    tid = vmt.create_task(user_id=1, object_key="voice_match/1/x/demo.mp4",
                          language="de", gender="male")
    vmt._run_task_sync(tid)
    t = vmt.get_task(tid, user_id=1)
    assert t["status"] == "failed"
    assert "ffmpeg missing" in t["error"]


def test_ttl_cleanup_removes_old_tasks(monkeypatch):
    tid = vmt.create_task(user_id=1, object_key="voice_match/1/x/demo.mp4",
                          language="de", gender="male")
    vmt._TASKS[tid]["_expires_at"] = time.time() - 1
    vmt._cleanup_expired()
    assert vmt.get_task(tid, user_id=1) is None
```

- [ ] **Step 2：跑测试确认失败**

```
pytest tests/test_voice_match_tasks.py -v
```

期望 FAIL（`ModuleNotFoundError`）。

- [ ] **Step 3：实现**

`appcore/voice_match_tasks.py`：

```python
"""
视频匹配任务管理（内存态，带 TTL 清理）。

任务只在进程内存里保存，用户关页面或进程重启即作废。
重度依赖（ffmpeg / resemblyzer / TOS）以可 mock 的方式注入，便于测试。
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

TTL_SECONDS = 30 * 60
_CLEANUP_INTERVAL = 60
_MAX_WORKERS = 2

_TASKS: dict[str, dict[str, Any]] = {}
_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS)


# --------------- 注入点（测试 monkeypatch 用） ---------------

def _download_tos_to_local(object_key: str, dest_path: str) -> str:
    from appcore.tos_clients import download_file
    download_file(object_key, dest_path)
    return dest_path


def _upload_to_tos_signed(local_path: str, object_key: str) -> str:
    from appcore.tos_clients import upload_file, generate_signed_download_url
    upload_file(local_path, object_key)
    return generate_signed_download_url(object_key, expires=3600)


def _extract_sample_clip(video_path: str, out_dir: str) -> str:
    from pipeline.voice_match import extract_sample_clip
    return extract_sample_clip(video_path, out_dir=out_dir)


def _embed_audio_file(path: str):
    from pipeline.voice_embedding import embed_audio_file
    return embed_audio_file(path)


def _match_candidates(vec, *, language, gender, top_k):
    from pipeline.voice_match import match_candidates
    return match_candidates(vec, language=language, gender=gender, top_k=top_k)


# --------------- Public API ---------------

def create_task(*, user_id: int, object_key: str,
                language: str, gender: str) -> str:
    task_id = "vm_" + uuid.uuid4().hex
    with _LOCK:
        _TASKS[task_id] = {
            "task_id": task_id,
            "user_id": user_id,
            "object_key": object_key,
            "language": language,
            "gender": gender,
            "status": "pending",
            "progress": 0,
            "error": None,
            "result": None,
            "_expires_at": time.time() + TTL_SECONDS,
        }
    _EXECUTOR.submit(_run_task_sync, task_id)
    return task_id


def get_task(task_id: str, *, user_id: int) -> Optional[dict]:
    with _LOCK:
        t = _TASKS.get(task_id)
        if t and t.get("user_id") == user_id:
            return {k: v for k, v in t.items() if not k.startswith("_")}
    return None


# --------------- 执行 ---------------

def _set(task_id: str, **updates) -> None:
    with _LOCK:
        if task_id in _TASKS:
            _TASKS[task_id].update(updates)


def _run_task_sync(task_id: str) -> None:
    with _LOCK:
        t = _TASKS.get(task_id)
        if not t:
            return
        task = dict(t)

    work_dir = Path("uploads") / "voice_match" / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    src_mp4 = work_dir / "src.mp4"
    clip_key = f"voice_match/{task['user_id']}/clips/{task_id}.wav"

    try:
        _set(task_id, status="sampling", progress=10)
        _download_tos_to_local(task["object_key"], str(src_mp4))
        clip_wav = _extract_sample_clip(str(src_mp4), out_dir=str(work_dir))

        _set(task_id, status="embedding", progress=40)
        vec = _embed_audio_file(clip_wav)

        _set(task_id, status="matching", progress=70)
        candidates = _match_candidates(
            vec, language=task["language"],
            gender=task["gender"], top_k=3,
        )
        if not candidates:
            raise RuntimeError("该语种声音库尚未同步，请联系管理员")

        signed_url = _upload_to_tos_signed(clip_wav, clip_key)
        _set(task_id, status="done", progress=100, result={
            "sample_audio_url": signed_url,
            "candidates": candidates,
        })
    except Exception as exc:
        log.exception("voice match task %s failed", task_id)
        _set(task_id, status="failed", progress=100, error=str(exc))
    finally:
        # 本地临时文件清理；TOS 清理由 TTL 清理器或 cleanup.py 统一兜底
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        except Exception:
            pass


# --------------- TTL 清理 ---------------

def _cleanup_expired() -> None:
    now = time.time()
    to_purge: list[str] = []
    with _LOCK:
        for tid, t in _TASKS.items():
            if t.get("_expires_at", 0) <= now:
                to_purge.append(tid)
        for tid in to_purge:
            _TASKS.pop(tid, None)


def _cleanup_loop() -> None:
    while True:
        time.sleep(_CLEANUP_INTERVAL)
        try:
            _cleanup_expired()
        except Exception:
            log.warning("voice match TTL cleanup failed", exc_info=True)


_cleanup_thread = threading.Thread(target=_cleanup_loop, daemon=True, name="vmt-cleanup")
_cleanup_thread.start()
```

- [ ] **Step 4：跑测试**

```
pytest tests/test_voice_match_tasks.py -v
```

期望 PASS。

- [ ] **Step 5：commit**

```
git add appcore/voice_match_tasks.py tests/test_voice_match_tasks.py
git commit -m "feat(voice-library): 匹配任务内存状态机 + TTL 清理"
```

---

## Phase 4 · 匹配接口

### Task 4.1 · `/match/upload-url`

**Files:**
- Modify: `web/routes/voice_library.py`
- Modify: `tests/test_voice_library_routes.py`

- [ ] **Step 1：写失败测试**

在 `tests/test_voice_library_routes.py` 追加：

```python
class TestMatchUploadUrl:
    def test_returns_signed_url(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.voice_library.generate_signed_upload_url",
            lambda key, expires=600: "https://signed",
        )
        resp = client.post("/voice-library/api/match/upload-url",
                           json={"filename": "demo.mp4", "content_type": "video/mp4"})
        data = resp.get_json()
        assert resp.status_code == 200
        assert data["upload_url"] == "https://signed"
        assert data["object_key"].startswith("voice_match/")
        assert data["expires_in"] == 600

    def test_rejects_bad_content_type(self, client):
        resp = client.post("/voice-library/api/match/upload-url",
                           json={"filename": "x.exe", "content_type": "application/x-msdownload"})
        assert resp.status_code == 400
```

- [ ] **Step 2：实现**

在 `web/routes/voice_library.py` 顶部追加：

```python
import uuid as _uuid
from flask_login import current_user
from appcore.tos_clients import generate_signed_upload_url
```

追加路由（TOS 的 PUT 预签 URL 不需要绑 `Content-Type`，但我们仍在服务端白名单里挡掉明显非法的类型）：

```python
_ALLOWED_VIDEO_CT = {"video/mp4", "video/quicktime", "video/x-matroska", "video/webm"}


@bp.route("/api/match/upload-url", methods=["POST"])
@login_required
def api_match_upload_url():
    body = request.get_json(silent=True) or {}
    filename = (body.get("filename") or "").strip()
    content_type = (body.get("content_type") or "").strip().lower()
    if content_type not in _ALLOWED_VIDEO_CT:
        return jsonify({"error": "unsupported content_type"}), 400
    safe_name = filename.replace("/", "_").replace("\\", "_") or "demo.mp4"
    object_key = f"voice_match/{current_user.id}/{_uuid.uuid4().hex}/{safe_name}"
    upload_url = generate_signed_upload_url(object_key, expires=600)
    return jsonify({
        "upload_url": upload_url,
        "object_key": object_key,
        "expires_in": 600,
    })
```

- [ ] **Step 3：跑测试**

```
pytest tests/test_voice_library_routes.py::TestMatchUploadUrl -v
```

期望 PASS。

- [ ] **Step 4：commit**

```
git add web/routes/voice_library.py appcore/tos_clients.py tests/test_voice_library_routes.py
git commit -m "feat(voice-library): 匹配视频 TOS 预签 PUT 接口"
```

---

### Task 4.2 · `/match/start`

**Files:**
- Modify: `web/routes/voice_library.py`
- Modify: `tests/test_voice_library_routes.py`

- [ ] **Step 1：写失败测试**

追加：

```python
class TestMatchStart:
    def test_start_returns_task_id(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.voice_library.vmt.create_task",
            lambda **kw: "vm_fake",
        )
        monkeypatch.setattr(
            "appcore.medias.list_enabled_language_codes",
            lambda: ["de", "fr"],
        )
        resp = client.post("/voice-library/api/match/start", json={
            "object_key": "voice_match/1/abc/demo.mp4",
            "language": "de", "gender": "male",
        })
        assert resp.status_code == 202
        assert resp.get_json()["task_id"] == "vm_fake"

    def test_rejects_foreign_object_key(self, client, login_user):
        # login_user 固定为 id=1
        resp = client.post("/voice-library/api/match/start", json={
            "object_key": "voice_match/2/abc/demo.mp4",
            "language": "de", "gender": "male",
        })
        assert resp.status_code == 403

    def test_rejects_disabled_language(self, client, monkeypatch):
        monkeypatch.setattr(
            "appcore.medias.list_enabled_language_codes",
            lambda: ["de"],
        )
        resp = client.post("/voice-library/api/match/start", json={
            "object_key": "voice_match/1/abc/demo.mp4",
            "language": "fr", "gender": "male",
        })
        assert resp.status_code == 400

    def test_rejects_invalid_gender(self, client, monkeypatch):
        monkeypatch.setattr(
            "appcore.medias.list_enabled_language_codes",
            lambda: ["de"],
        )
        resp = client.post("/voice-library/api/match/start", json={
            "object_key": "voice_match/1/abc/demo.mp4",
            "language": "de", "gender": "other",
        })
        assert resp.status_code == 400
```

- [ ] **Step 2：实现**

顶部追加：

```python
from appcore import voice_match_tasks as vmt
```

路由：

```python
@bp.route("/api/match/start", methods=["POST"])
@login_required
def api_match_start():
    body = request.get_json(silent=True) or {}
    object_key = (body.get("object_key") or "").strip()
    language = (body.get("language") or "").strip().lower()
    gender = (body.get("gender") or "").strip().lower()

    if not object_key.startswith(f"voice_match/{current_user.id}/"):
        return jsonify({"error": "forbidden object_key"}), 403
    if language not in medias.list_enabled_language_codes():
        return jsonify({"error": "language not enabled"}), 400
    if gender not in ("male", "female"):
        return jsonify({"error": "gender must be male or female"}), 400

    task_id = vmt.create_task(
        user_id=current_user.id, object_key=object_key,
        language=language, gender=gender,
    )
    return jsonify({"task_id": task_id}), 202
```

- [ ] **Step 3：跑测试 + commit**

```
pytest tests/test_voice_library_routes.py::TestMatchStart -v
git add web/routes/voice_library.py tests/test_voice_library_routes.py
git commit -m "feat(voice-library): 匹配任务启动接口（鉴权 + 参数校验）"
```

---

### Task 4.3 · `/match/status/<task_id>`

**Files:**
- Modify: `web/routes/voice_library.py`
- Modify: `tests/test_voice_library_routes.py`

- [ ] **Step 1：写失败测试**

```python
class TestMatchStatus:
    def test_returns_task_state(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.voice_library.vmt.get_task",
            lambda tid, user_id: {"task_id": tid, "status": "done",
                                   "progress": 100, "error": None,
                                   "result": {"candidates": []}},
        )
        resp = client.get("/voice-library/api/match/status/vm_x")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "done"

    def test_missing_returns_404(self, client, monkeypatch):
        monkeypatch.setattr(
            "web.routes.voice_library.vmt.get_task",
            lambda *a, **kw: None,
        )
        resp = client.get("/voice-library/api/match/status/vm_nope")
        assert resp.status_code == 404
```

- [ ] **Step 2：实现**

```python
@bp.route("/api/match/status/<task_id>", methods=["GET"])
@login_required
def api_match_status(task_id: str):
    t = vmt.get_task(task_id, user_id=current_user.id)
    if not t:
        return jsonify({"error": "not found"}), 404
    return jsonify(t)
```

- [ ] **Step 3：跑测试 + commit**

```
pytest tests/test_voice_library_routes.py::TestMatchStatus -v
git add web/routes/voice_library.py tests/test_voice_library_routes.py
git commit -m "feat(voice-library): 匹配任务状态查询接口"
```

---

## Phase 5 · 匹配前端 Tab 2

### Task 5.1 · Tab 2 UI + 上传 + 轮询

**Files:**
- Modify: `web/templates/voice_library.html`
- Modify: `web/templates/_voice_library_styles.html`
- Modify: `web/static/voice_library.js`

- [ ] **Step 1：Tab 2 HTML**

替换 `voice_library.html` 的 `#tab-match` 为：

```html
<section id="tab-match" class="oc-tab-panel vl-match" hidden>
  <div class="vl-match-step" id="vl-match-step1">
    <h3>1. 选择目标语种与性别</h3>
    <div class="vl-filter-group"><label>目标语种</label><div id="vl-match-languages" class="vl-pill-row"></div></div>
    <div class="vl-filter-group"><label>性别（必选）</label><div id="vl-match-gender" class="vl-pill-row"></div></div>
  </div>

  <div class="vl-match-step" id="vl-match-step2">
    <h3>2. 上传视频</h3>
    <div class="vl-upload-zone" id="vl-upload-zone">
      <input type="file" id="vl-upload-input" accept="video/*" hidden />
      <button class="oc-btn-primary" id="vl-upload-pick">选择视频</button>
      <span class="vl-upload-hint">拖拽视频到这里，或点击选择（≤ 500MB）</span>
    </div>
    <div class="vl-progress" id="vl-progress" hidden>
      <div class="vl-progress-bar"><div id="vl-progress-fill"></div></div>
      <div id="vl-progress-label"></div>
    </div>
  </div>

  <div class="vl-match-step" id="vl-match-step3" hidden>
    <h3>3. 匹配结果</h3>
    <div class="vl-sample-audio">
      <span>源视频采样 10s：</span>
      <audio id="vl-sample-audio" controls></audio>
    </div>
    <div class="vl-grid" id="vl-result-grid"></div>
    <button class="oc-btn-secondary" id="vl-match-reset">重新上传一个视频</button>
  </div>
</section>
```

- [ ] **Step 2：样式追加**

在 `_voice_library_styles.html` `</style>` 前追加：

```css
.vl-match { display:flex; flex-direction:column; gap:var(--space-6); }
.vl-match-step { padding:var(--space-5); background:var(--bg-subtle); border-radius:var(--radius-lg); }
.vl-match-step h3 { margin:0 0 var(--space-4); font-size:var(--text-md); }
.vl-upload-zone { display:flex; gap:var(--space-4); align-items:center; padding:var(--space-6); border:2px dashed var(--border-strong); border-radius:var(--radius-lg); background:#fff; }
.vl-upload-hint { color:var(--fg-muted); font-size:var(--text-sm); }
.vl-progress-bar { height:6px; background:var(--bg-muted); border-radius:var(--radius-full); overflow:hidden; margin-top:var(--space-3); }
#vl-progress-fill { height:100%; width:0%; background:var(--accent); transition:width var(--duration) var(--ease); }
#vl-progress-label { margin-top:var(--space-2); font-size:var(--text-sm); color:var(--fg-muted); }
.vl-sample-audio { display:flex; align-items:center; gap:var(--space-4); margin-bottom:var(--space-5); }
.vl-similarity { background:var(--success-bg); color:var(--success-fg); padding:2px var(--space-2); border-radius:var(--radius-sm); font-size:var(--text-xs); }
```

- [ ] **Step 3：JS 追加**

在 `voice_library.js` 的 IIFE 内部追加（`bootstrap()` 之前合适位置）：

```js
  const match = { language: "", gender: "", taskId: null, pollTimer: null };

  function renderMatchLangs(langs) {
    const box = $("#vl-match-languages");
    box.innerHTML = "";
    langs.forEach(l => {
      const b = document.createElement("button");
      b.className = "vl-pill"; b.textContent = l.name_zh;
      if (l.code === match.language) b.classList.add("is-active");
      b.addEventListener("click", () => {
        match.language = l.code; renderMatchLangs(langs);
      });
      box.appendChild(b);
    });
  }

  function renderMatchGender() {
    const box = $("#vl-match-gender"); box.innerHTML = "";
    [["male", "男"], ["female", "女"]].forEach(([v, t]) => {
      const b = document.createElement("button");
      b.className = "vl-pill"; b.textContent = t;
      if (v === match.gender) b.classList.add("is-active");
      b.addEventListener("click", () => { match.gender = v; renderMatchGender(); });
      box.appendChild(b);
    });
  }

  function setProgress(pct, label) {
    $("#vl-progress").hidden = false;
    $("#vl-progress-fill").style.width = pct + "%";
    $("#vl-progress-label").textContent = label;
  }

  async function uploadViaSignedPut(file) {
    const pre = await fetch("/voice-library/api/match/upload-url", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({filename: file.name, content_type: file.type || "video/mp4"}),
    }).then(r => r.json());
    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", pre.upload_url);
      xhr.setRequestHeader("Content-Type", file.type || "video/mp4");
      xhr.upload.onprogress = e => {
        if (e.lengthComputable) setProgress((e.loaded / e.total) * 90, `上传中 ${Math.round(e.loaded/e.total*100)}%`);
      };
      xhr.onload = () => xhr.status < 300 ? resolve() : reject(new Error("upload " + xhr.status));
      xhr.onerror = () => reject(new Error("upload network error"));
      xhr.send(file);
    });
    return pre.object_key;
  }

  const PHASE_LABEL = {
    pending: "等待中", sampling: "采样音频",
    embedding: "计算声纹", matching: "匹配声音库",
    done: "完成", failed: "失败",
  };

  async function pollMatchStatus() {
    try {
      const resp = await fetch(`/voice-library/api/match/status/${match.taskId}`,
        {credentials: "same-origin"});
      if (!resp.ok) throw new Error("status " + resp.status);
      const t = await resp.json();
      setProgress(t.progress || 90, PHASE_LABEL[t.status] || t.status);
      if (t.status === "done") { clearInterval(match.pollTimer); match.pollTimer = null; renderMatchResult(t.result); }
      else if (t.status === "failed") { clearInterval(match.pollTimer); match.pollTimer = null; setProgress(100, "失败：" + (t.error || "未知错误")); }
    } catch (e) {
      clearInterval(match.pollTimer); match.pollTimer = null;
      setProgress(100, "网络错误：" + e.message);
    }
  }

  function renderMatchResult(result) {
    $("#vl-match-step3").hidden = false;
    $("#vl-sample-audio").src = result.sample_audio_url;
    const grid = $("#vl-result-grid"); grid.innerHTML = "";
    (result.candidates || []).forEach(v => {
      const card = renderCard(v);
      const tag = document.createElement("span");
      tag.className = "vl-similarity";
      tag.textContent = `相似度 ${(v.similarity * 100).toFixed(1)}%`;
      card.querySelector(".vl-card-title").appendChild(tag);
      grid.appendChild(card);
    });
  }

  async function startMatch(file) {
    if (!match.language || !match.gender) {
      alert("请先选择目标语种和性别"); return;
    }
    setProgress(0, "准备上传");
    try {
      const objectKey = await uploadViaSignedPut(file);
      setProgress(92, "任务启动中");
      const r = await fetch("/voice-library/api/match/start", {
        method: "POST", headers: {"Content-Type": "application/json"},
        credentials: "same-origin",
        body: JSON.stringify({object_key: objectKey, language: match.language, gender: match.gender}),
      });
      if (!r.ok) throw new Error("start " + r.status);
      match.taskId = (await r.json()).task_id;
      match.pollTimer = setInterval(pollMatchStatus, 1500);
      setProgress(95, "采样中");
    } catch (e) {
      setProgress(100, "失败：" + e.message);
    }
  }

  function bindMatchEvents() {
    $("#vl-upload-pick").addEventListener("click", () => $("#vl-upload-input").click());
    $("#vl-upload-input").addEventListener("change", (e) => {
      if (e.target.files.length) startMatch(e.target.files[0]);
    });
    const zone = $("#vl-upload-zone");
    zone.addEventListener("dragover", e => { e.preventDefault(); });
    zone.addEventListener("drop", e => {
      e.preventDefault();
      if (e.dataTransfer.files.length) startMatch(e.dataTransfer.files[0]);
    });
    $("#vl-match-reset").addEventListener("click", () => {
      match.taskId = null;
      if (match.pollTimer) { clearInterval(match.pollTimer); match.pollTimer = null; }
      $("#vl-match-step3").hidden = true;
      $("#vl-progress").hidden = true;
      $("#vl-upload-input").value = "";
    });
  }
```

并在 `bootstrap()` 内 `await refreshFiltersAndList();` 之后追加：

```js
    renderMatchLangs(opts.languages);
    renderMatchGender();
    if (opts.languages.length) match.language = opts.languages[0].code;
```

并在 `bindEvents()` 最后追加：

```js
    bindMatchEvents();
```

- [ ] **Step 4：手测**

1. Tab 切到"视频匹配"
2. 选择目标语种 + 男/女 → 上传一个小 mp4 → 观察进度条变化
3. 结果页能试听采样片段 + 看到 3 张候选卡片 + 相似度
4. "重新上传" 按钮回到上传态

- [ ] **Step 5：commit**

```
git add web/templates/voice_library.html web/templates/_voice_library_styles.html \
        web/static/voice_library.js
git commit -m "feat(voice-library): 视频匹配 Tab（TOS 直传 + 轮询 + 结果展示）"
```

---

## Phase 6 · 菜单接入

### Task 6.1 · `layout.html` 菜单项 + 页面访问测试

**Files:**
- Modify: `web/templates/layout.html`
- Modify: `tests/test_web_routes.py`

- [ ] **Step 1：写失败测试**

在 `tests/test_web_routes.py` 追加：

```python
def test_voice_library_menu_rendered(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"/voice-library" in resp.data

def test_voice_library_page_ok(client):
    resp = client.get("/voice-library")
    assert resp.status_code == 200
```

- [ ] **Step 2：在 `layout.html` 顶部导航的合适位置插入菜单项**

用 grep 找到"文案翻译"或"图片翻译"的 `<a>`，在其旁边插入：

```html
<a href="/voice-library" class="oc-nav-item{% if request.path.startswith('/voice-library') %} is-active{% endif %}">声音仓库</a>
```

（具体标签按 `layout.html` 现有导航元素的 class 和结构匹配。）

- [ ] **Step 3：跑测试 + commit**

```
pytest tests/test_web_routes.py -k voice_library -v
git add web/templates/layout.html tests/test_web_routes.py
git commit -m "feat(voice-library): 顶部导航新增『声音仓库』菜单项"
```

---

## Phase 7 · 管理员同步

### Task 7.1 · `voice_library_sync.py` 进度回调支持

**Files:**
- Modify: `pipeline/voice_library_sync.py`
- Modify: `tests/test_voice_library_sync.py`

- [ ] **Step 1：写失败测试**

追加到 `tests/test_voice_library_sync.py`：

```python
def test_sync_all_invokes_on_page_callback(monkeypatch):
    calls = []
    def fake_fetch(**kw):
        # 两页，每页 1 条
        page = kw.get("next_page_token")
        if page is None:
            return [{"voice_id": "a", "name": "A"}], "tok"
        return [{"voice_id": "b", "name": "B"}], None
    monkeypatch.setattr("pipeline.voice_library_sync.fetch_shared_voices_page", fake_fetch)
    monkeypatch.setattr("pipeline.voice_library_sync.upsert_voice", lambda v: None)
    from pipeline.voice_library_sync import sync_all_shared_voices
    total = sync_all_shared_voices(
        api_key="k", language="de",
        on_page=lambda idx, voices: calls.append((idx, [v["voice_id"] for v in voices])),
    )
    assert total == 2
    assert calls == [(0, ["a"]), (1, ["b"])]


def test_embed_missing_invokes_on_progress(monkeypatch, tmp_path):
    from pipeline import voice_library_sync as vls
    monkeypatch.setattr(vls, "_list_voices_without_embedding",
                        lambda limit=None: [{"voice_id": "a", "preview_url": "https://x"},
                                             {"voice_id": "b", "preview_url": "https://y"}])
    monkeypatch.setattr(vls, "_download_preview", lambda url, dest: str(dest))
    import numpy as np
    monkeypatch.setattr(vls, "embed_audio_file", lambda p: np.zeros(256, dtype=np.float32))
    monkeypatch.setattr(vls, "_update_embedding", lambda vid, blob: None)

    progress = []
    count = vls.embed_missing_voices(
        str(tmp_path),
        on_progress=lambda done, total, vid, ok: progress.append((done, total, vid, ok)),
    )
    assert count == 2
    assert progress == [(1, 2, "a", True), (2, 2, "b", True)]
```

- [ ] **Step 2：改实现**

在 `pipeline/voice_library_sync.py`：

`sync_all_shared_voices` 加 `on_page` 参数：

```python
def sync_all_shared_voices(
    api_key: str,
    *,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    on_page: Optional[callable] = None,
) -> int:
    total = 0
    next_token: Optional[str] = None
    page_index = 0
    while True:
        voices, next_token = fetch_shared_voices_page(
            api_key=api_key, page_size=page_size,
            next_page_token=next_token, language=language,
            gender=gender, category=category,
        )
        for voice in voices:
            if voice.get("voice_id"):
                upsert_voice(voice)
                total += 1
        if on_page:
            try: on_page(page_index, voices)
            except Exception: log.warning("on_page callback failed", exc_info=True)
        page_index += 1
        if not next_token:
            break
    return total
```

`embed_missing_voices` 加 `on_progress` 参数并把 `print` 换成 `log.warning`：

```python
def embed_missing_voices(cache_dir: str, limit=None, on_progress=None) -> int:
    cache_path = Path(cache_dir); cache_path.mkdir(parents=True, exist_ok=True)
    rows = _list_voices_without_embedding(limit=limit)
    total = len(rows); count = 0
    for i, row in enumerate(rows, 1):
        voice_id = row["voice_id"]
        url = row.get("preview_url")
        ok = False
        if url:
            try:
                file_name = hashlib.sha1(voice_id.encode("utf-8")).hexdigest() + ".mp3"
                dest = cache_path / file_name
                _download_preview(url, dest)
                vec = embed_audio_file(str(dest))
                _update_embedding(voice_id, serialize_embedding(vec))
                ok = True
                count += 1
            except Exception as exc:
                log.warning("embed_missing %s failed: %s", voice_id, exc)
        if on_progress:
            try: on_progress(i, total, voice_id, ok)
            except Exception: log.warning("on_progress callback failed", exc_info=True)
    return count
```

文件顶部：`import logging; log = logging.getLogger(__name__)`（若无）。

- [ ] **Step 3：跑测试 + commit**

```
pytest tests/test_voice_library_sync.py -v
git add pipeline/voice_library_sync.py tests/test_voice_library_sync.py
git commit -m "feat(voice-library): sync 增加 on_page / on_progress 回调"
```

---

### Task 7.2 · `voice_library_sync_task.py`

**Files:**
- Create: `appcore/voice_library_sync_task.py`
- Create: `tests/test_voice_library_sync_task.py`

- [ ] **Step 1：写失败测试**

`tests/test_voice_library_sync_task.py`：

```python
import pytest
from unittest.mock import MagicMock

from appcore import voice_library_sync_task as vlst


@pytest.fixture(autouse=True)
def reset():
    vlst._CURRENT["task"] = None
    vlst._CURRENT["summary"] = {}
    yield
    vlst._CURRENT["task"] = None


def test_start_when_idle(monkeypatch):
    emit = MagicMock()
    monkeypatch.setattr(vlst, "_emit", emit)
    monkeypatch.setattr(vlst, "_run_sync_sync", lambda tid, lang, api_key: None)
    monkeypatch.setattr(vlst, "_get_api_key", lambda: "k")
    tid = vlst.start_sync(language="de")
    assert tid is not None
    assert vlst.get_current()["language"] == "de"


def test_start_raises_when_busy(monkeypatch):
    vlst._CURRENT["task"] = {"sync_id": "x", "language": "de", "status": "running"}
    with pytest.raises(RuntimeError, match="another sync"):
        vlst.start_sync(language="fr")


def test_summary_counts_from_db(tmp_db, monkeypatch):
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("de", "德语"), ("fr", "法语")],
    )
    from appcore.db import execute
    execute("INSERT INTO elevenlabs_voices (voice_id, name, language, synced_at, updated_at, audio_embedding) VALUES ('a','A','de',NOW(),NOW(),%s)", (b"abc",))
    execute("INSERT INTO elevenlabs_voices (voice_id, name, language, synced_at, updated_at) VALUES ('b','B','de',NOW(),NOW())")
    s = vlst.summarize()
    de = next(x for x in s if x["language"] == "de")
    assert de["total_rows"] == 2
    assert de["embedded_rows"] == 1
```

- [ ] **Step 2：实现**

`appcore/voice_library_sync_task.py`：

```python
"""
管理员触发的 ElevenLabs 声音库同步任务（全局单任务）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from typing import Any, Optional

from appcore.db import query

log = logging.getLogger(__name__)

_CURRENT: dict[str, Any] = {"task": None, "summary": {}}
_LOCK = threading.Lock()


def _get_api_key() -> str:
    key = os.getenv("ELEVENLABS_API_KEY") or ""
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY 未配置")
    return key


def _emit(event: str, payload: dict) -> None:
    try:
        from web.extensions import socketio
        socketio.emit(event, payload, to="admin")
    except Exception:
        log.warning("socketio emit failed: %s", event, exc_info=True)


def start_sync(*, language: str) -> str:
    with _LOCK:
        if _CURRENT["task"] and _CURRENT["task"]["status"] == "running":
            raise RuntimeError("another sync is running")
        sync_id = "sync_" + uuid.uuid4().hex
        _CURRENT["task"] = {
            "sync_id": sync_id, "language": language,
            "phase": "pull_metadata", "done": 0, "total": 0,
            "status": "running", "error": None,
        }
    api_key = _get_api_key()
    threading.Thread(target=_run_sync_sync, args=(sync_id, language, api_key),
                     daemon=True, name="voice-sync").start()
    return sync_id


def get_current() -> Optional[dict]:
    with _LOCK:
        return dict(_CURRENT["task"]) if _CURRENT["task"] else None


def summarize() -> list[dict]:
    from appcore import medias
    rows = query(
        "SELECT language, "
        "  COUNT(*) AS total_rows, "
        "  SUM(CASE WHEN audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded_rows, "
        "  MAX(synced_at) AS last_synced_at "
        "FROM elevenlabs_voices GROUP BY language"
    )
    stats = {r["language"]: r for r in rows}
    out = []
    for code, name in medias.list_enabled_languages_kv():
        s = stats.get(code, {})
        out.append({
            "language": code,
            "name_zh": name,
            "total_rows": int(s.get("total_rows") or 0),
            "embedded_rows": int(s.get("embedded_rows") or 0),
            "last_synced_at": s["last_synced_at"].isoformat() if s.get("last_synced_at") else None,
        })
    return out


def _set(**updates) -> None:
    with _LOCK:
        if _CURRENT["task"]:
            _CURRENT["task"].update(updates)
            _emit("voice_library.sync.progress", dict(_CURRENT["task"]))


def _run_sync_sync(sync_id: str, language: str, api_key: str) -> None:
    from pipeline.voice_library_sync import (
        sync_all_shared_voices, embed_missing_voices,
    )
    try:
        total_pulled = [0]
        def on_page(idx, voices):
            total_pulled[0] += len(voices)
            _set(phase="pull_metadata", done=total_pulled[0], total=total_pulled[0])

        sync_all_shared_voices(api_key=api_key, language=language, on_page=on_page)

        def on_progress(done, total, voice_id, ok):
            _set(phase="embed", done=done, total=total)

        cache_dir = os.path.join("uploads", "voice_preview_cache")
        embed_missing_voices(cache_dir, on_progress=on_progress)

        _set(status="done", phase="done")
        _emit("voice_library.sync.summary", {"summary": summarize()})
    except Exception as exc:
        log.exception("voice sync %s failed", sync_id)
        _set(status="failed", error=str(exc))
```

- [ ] **Step 3：跑测试 + commit**

```
pytest tests/test_voice_library_sync_task.py -v
git add appcore/voice_library_sync_task.py tests/test_voice_library_sync_task.py
git commit -m "feat(voice-library): 管理员同步任务 runner（全局单任务 + SocketIO 广播）"
```

---

### Task 7.3 · SocketIO admin 房间

**Files:**
- Modify: `web/app.py`
- Modify: `tests/test_web_routes.py`（或新建 `tests/test_socketio_admin_room.py`）

- [ ] **Step 1：写失败测试**

`tests/test_socketio_admin_room.py`：

```python
import pytest

from web.app import create_app
from web.extensions import socketio


@pytest.mark.usefixtures("tmp_db")
def test_admin_joins_admin_room(admin_user):
    app = create_app()
    client = socketio.test_client(app, flask_test_client=admin_user.http_client)
    client.emit("join_admin")
    # 我们没有直接的 API 断言 "已加入 room"，但无异常即通过；
    # 更严的做法可在 appcore 里发一个 admin-only 事件验证。
    assert client.is_connected()
```

（实际项目里若已有更完整的 socketio 测试辅助，按约定调整。）

- [ ] **Step 2：在 `web/app.py` 追加 SocketIO 事件**

```python
    @socketio.on("join_admin")
    def on_join_admin():
        from flask_login import current_user
        if not current_user.is_authenticated:
            return
        if getattr(current_user, "role", None) == "admin":
            join_room("admin")
```

- [ ] **Step 3：commit**

```
git add web/app.py tests/test_socketio_admin_room.py
git commit -m "feat(voice-library): SocketIO admin 房间（同步进度仅管理员接收）"
```

---

### Task 7.4 · admin sync HTTP 接口

**Files:**
- Modify: `web/routes/admin.py`
- Create: `tests/test_voice_library_sync_admin.py`

- [ ] **Step 1：写失败测试**

`tests/test_voice_library_sync_admin.py`：

```python
import pytest


@pytest.mark.usefixtures("tmp_db", "login_admin")
class TestSyncEndpoints:
    def test_start_returns_sync_id(self, client, monkeypatch):
        monkeypatch.setattr(
            "appcore.voice_library_sync_task.start_sync",
            lambda language: "sync_fake",
        )
        monkeypatch.setattr(
            "appcore.medias.list_enabled_language_codes",
            lambda: ["de"],
        )
        resp = client.post("/admin/voice-library/sync/de")
        assert resp.status_code == 202
        assert resp.get_json()["sync_id"] == "sync_fake"

    def test_start_409_when_busy(self, client, monkeypatch):
        def busy(**kw): raise RuntimeError("another sync is running")
        monkeypatch.setattr("appcore.voice_library_sync_task.start_sync", busy)
        monkeypatch.setattr(
            "appcore.medias.list_enabled_language_codes",
            lambda: ["de"],
        )
        resp = client.post("/admin/voice-library/sync/de")
        assert resp.status_code == 409

    def test_status_returns_current_and_summary(self, client, monkeypatch):
        monkeypatch.setattr(
            "appcore.voice_library_sync_task.get_current",
            lambda: {"sync_id": "x", "language": "de", "status": "running"},
        )
        monkeypatch.setattr(
            "appcore.voice_library_sync_task.summarize",
            lambda: [{"language": "de", "total_rows": 1, "embedded_rows": 1}],
        )
        resp = client.get("/admin/voice-library/sync-status")
        data = resp.get_json()
        assert data["current"]["language"] == "de"
        assert data["summary"][0]["language"] == "de"

    def test_non_admin_forbidden(self, client, login_user):
        resp = client.post("/admin/voice-library/sync/de")
        assert resp.status_code in (302, 403)
```

- [ ] **Step 2：实现**

在 `web/routes/admin.py` 追加：

```python
from appcore import voice_library_sync_task as vlst


@bp.route("/voice-library/sync/<language>", methods=["POST"])
@login_required
@admin_required
def voice_library_sync(language: str):
    if language not in medias.list_enabled_language_codes():
        return jsonify({"error": "language not enabled"}), 400
    try:
        sync_id = vlst.start_sync(language=language)
    except RuntimeError as exc:
        msg = str(exc)
        if "another sync" in msg:
            return jsonify({"error": msg}), 409
        return jsonify({"error": msg}), 500
    return jsonify({"sync_id": sync_id}), 202


@bp.route("/voice-library/sync-status", methods=["GET"])
@login_required
@admin_required
def voice_library_sync_status():
    return jsonify({
        "current": vlst.get_current(),
        "summary": vlst.summarize(),
    })
```

- [ ] **Step 3：跑测试 + commit**

```
pytest tests/test_voice_library_sync_admin.py -v
git add web/routes/admin.py tests/test_voice_library_sync_admin.py
git commit -m "feat(voice-library): 管理员同步 HTTP 接口（启动 + 状态 + 409 互斥）"
```

---

### Task 7.5 · admin_settings.html 同步区块

**Files:**
- Modify: `web/templates/admin_settings.html`
- Modify: `web/static/admin_settings.js`

前端无自动化测试。

- [ ] **Step 1：在 `admin_settings.html` 语种配置区块下方追加**

```html
<section class="admin-section" id="voice-library-sync">
  <h2>声音库同步（ElevenLabs）</h2>
  <p class="field-hint">为每个启用小语种同步 ElevenLabs 共享音色库（含声纹向量生成）。同一时间只允许一个同步任务。</p>
  <table class="oc-table">
    <thead><tr><th>语种</th><th>条目数</th><th>声纹覆盖</th><th>最后同步</th><th>操作</th></tr></thead>
    <tbody id="voice-sync-tbody"></tbody>
  </table>
  <div id="voice-sync-live" class="vl-sync-live" hidden></div>
</section>
<style>
  #voice-library-sync { margin-top:var(--space-7); }
  .vl-sync-live { margin-top:var(--space-4); padding:var(--space-4); background:var(--accent-subtle); border-radius:var(--radius-md); }
  .vl-sync-live .bar { height:6px; background:var(--bg-muted); border-radius:var(--radius-full); overflow:hidden; margin-top:var(--space-2); }
  .vl-sync-live .fill { height:100%; width:0%; background:var(--accent); transition:width .2s var(--ease); }
</style>
```

- [ ] **Step 2：在 `admin_settings.js` 底部追加 sync 逻辑**

```js
(function () {
  const $ = s => document.querySelector(s);

  async function fetchStatus() {
    const r = await fetch("/admin/voice-library/sync-status", {credentials:"same-origin"});
    return r.json();
  }

  function render(status) {
    const tbody = $("#voice-sync-tbody");
    tbody.innerHTML = "";
    const busy = status.current && status.current.status === "running";
    const busyLang = busy ? status.current.language : null;
    status.summary.forEach(row => {
      const tr = document.createElement("tr");
      const ratio = row.total_rows ? ((row.embedded_rows/row.total_rows*100).toFixed(1)+"%") : "-";
      tr.innerHTML = `
        <td>${row.name_zh} (${row.language})</td>
        <td>${row.total_rows}</td>
        <td>${row.embedded_rows}/${row.total_rows} (${ratio})</td>
        <td>${row.last_synced_at || "未同步"}</td>
        <td><button data-lang="${row.language}" class="oc-btn-primary vl-sync-btn"
              ${busy ? "disabled" : ""}>${busy && busyLang===row.language ? "同步中…" : (busy ? "排队中" : "同步")}</button></td>
      `;
      tbody.appendChild(tr);
    });
    tbody.querySelectorAll(".vl-sync-btn").forEach(btn => {
      btn.addEventListener("click", () => triggerSync(btn.dataset.lang));
    });
    renderLive(status.current);
  }

  function renderLive(cur) {
    const el = $("#voice-sync-live");
    if (!cur) { el.hidden = true; return; }
    el.hidden = false;
    const pct = cur.total ? Math.round(cur.done / cur.total * 100) : 0;
    const phase = cur.phase === "pull_metadata" ? "拉取元数据" :
                  cur.phase === "embed" ? "生成声纹" : cur.phase;
    el.innerHTML = `
      <div>${cur.language} · ${phase} · ${cur.done}/${cur.total || "?"}</div>
      <div class="bar"><div class="fill" style="width:${pct}%"></div></div>
      ${cur.error ? `<div style="color:var(--danger);margin-top:8px">${cur.error}</div>` : ""}
    `;
  }

  async function triggerSync(lang) {
    const r = await fetch(`/admin/voice-library/sync/${lang}`, {method:"POST", credentials:"same-origin"});
    if (r.status === 409) { alert("已有另一个同步任务在运行"); return; }
    if (!r.ok) { alert("启动同步失败"); return; }
    refresh();
  }

  async function refresh() { render(await fetchStatus()); }

  function initSocket() {
    if (!window.io) return;
    const sock = window.io({transports:["websocket","polling"]});
    sock.on("connect", () => sock.emit("join_admin"));
    sock.on("voice_library.sync.progress", p => renderLive(p));
    sock.on("voice_library.sync.summary", () => refresh());
  }

  document.addEventListener("DOMContentLoaded", () => {
    if (!document.getElementById("voice-library-sync")) return;
    refresh();
    initSocket();
    setInterval(refresh, 10000); // 兜底：SocketIO 断了也能看到状态
  });
})();
```

（如果 `admin_settings.html` 没有引入 socket.io 客户端脚本，在页面底部追加 `<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>`，或复用项目已有的 socketio client 加载方式。）

- [ ] **Step 3：手测**

1. 管理员登录 → 打开 `/admin/settings`
2. 滚动到"声音库同步"区块
3. 点一个启用语种的"同步"按钮 → 进度条出现
4. 其他行按钮变"排队中"
5. 完成后统计列刷新

- [ ] **Step 4：commit**

```
git add web/templates/admin_settings.html web/static/admin_settings.js
git commit -m "feat(voice-library): 管理员后台声音库同步 UI"
```

---

## Phase 8 · 收官

### Task 8.1 · 全量测试 + 手测 checklist

- [ ] **Step 1：跑全量 pytest**

```
pytest tests -q
```

期望全部 PASS。如有偶发失败，修复再跑一遍。

- [ ] **Step 2：手测 checklist（需要真实 ElevenLabs API Key + 已启用至少 1 个小语种）**

1. 管理员同步一个语种（看进度、完成、覆盖率刷新）
2. `/voice-library#browse` 切语种 → 卡片正常
3. 7 项筛选独立 + 组合都能命中
4. 多张卡试听 → 只播一张
5. 重置筛选 → 恢复默认
6. `/voice-library#match` 上传一个真实视频（10-60s）→ 看到 Top-3 候选 + 相似度
7. 候选试听 + 源采样试听都 OK
8. 视频无音轨的文件 → 看到"无法从视频提取音频"错误
9. 未同步语种下匹配 → "该语种声音库尚未同步"
10. 普通用户访问 `/admin/voice-library/sync/de` → 被拦截

- [ ] **Step 3：commit 一个空记录点（可选）**

如有小修补，合并到对应阶段 commit；无须产生额外 commit。

---

## 自检对照

**Spec 覆盖**：

- § 4 菜单/Tab → Task 6.1 + Phase 2 + Task 5.1
- § 4.2 Tab 1 浏览 → Phase 2
- § 4.3 Tab 2 匹配 → Phase 5
- § 5 后端 → Phases 1 / 3 / 4
- § 6 管理员同步 → Phase 7
- § 7 鉴权 → Task 4.2（object_key 前缀）+ Task 7.4（@admin_required）
- § 8 错误处理 → 各 route/task 里的错误分支
- § 9 性能 → `idx_gender_language` 已存在，无需额外索引
- § 10 测试 → 每个 Task 有对应 pytest

**未做项**（非目标，不纳入计划）：

- 声音克隆
- 匹配历史持久化
- "一键套用到翻译项目"闭环
- 用户级 `user_voices` 入口改动
