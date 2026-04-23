# 声音库同步完善实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 ElevenLabs 共享音色库同步的分页 bug 与 use_case 丢失 bug；每个启用语种按 300 条上限同步元数据 + 生成声纹；显示远端总量；然后代为执行 7 个语种的同步。

**Architecture:** ElevenLabs `/v1/shared-voices` 真实分页用 `page`（整数）而非 `next_page_token`，首次响应返回 `total_count`，据此简化为 2 阶段流程（pull_metadata 顺便记总量 → embed）。新增 `elevenlabs_voice_library_stats` 表存远端总量，`elevenlabs_voices` 加 `use_case` 列，`upsert_voice` 写入并用完整响应替换 `labels_json`。前端表格新增一列"远端总量"。

**Tech Stack:** Python 3 / Flask / MySQL（pymysql）/ unittest mock / pytest / SocketIO / resemblyzer

**参考文档（engineer 需要看的）:**
- Spec：[docs/superpowers/specs/2026-04-19-voice-library-sync-completion-design.md](../specs/2026-04-19-voice-library-sync-completion-design.md)
- CLAUDE.md（项目级）：commit message 用中文；发布流程；测试发布流程
- ElevenLabs probe 数据：`scratch/probe_elevenlabs_shared_voices.py` 的输出（spec 背景章节有摘要）

---

## File Structure

### 新增

- `db/migrations/2026_04_19_voice_library_sync_completion.sql` — 建 stats 表 + 给 elevenlabs_voices 加 use_case 列
- `scratch/probe_elevenlabs_shared_voices.py`（已存在）— 一次性探测脚本，不参与运行时

### 修改

- `pipeline/voice_library_sync.py` — 修分页、加 max_voices / on_total_count、upsert_voice 写 use_case + 完整 labels_json、新增 upsert_library_stats
- `appcore/voice_library_sync_task.py` — 新增 MAX_VOICES_PER_LANGUAGE=300；_run_sync_sync 传 max_voices + on_total_count；summarize 联表
- `appcore/voice_library_browse.py` — `use_case` 筛选改走独立列（保留其他字段的兼容逻辑）
- `web/templates/admin_settings.html` — 表格新增 `<th>远端总量</th>`
- `web/static/admin_settings.js` — render 新增一列渲染
- `tests/test_voice_library_sync.py` — 重写大部分用例匹配新签名
- `tests/test_voice_library_sync_task.py` — 补 summarize / _run_sync_sync 新逻辑
- `tests/test_voice_library_browse.py` — use_case 走独立列的回归

---

## Task 1: DB 迁移 — stats 表 + use_case 列

**Files:**
- Create: `db/migrations/2026_04_19_voice_library_sync_completion.sql`

- [ ] **Step 1: 写迁移 SQL**

创建 `db/migrations/2026_04_19_voice_library_sync_completion.sql`：

```sql
-- 新建 ElevenLabs 共享库远端总量统计表
CREATE TABLE IF NOT EXISTS `elevenlabs_voice_library_stats` (
  `language`        VARCHAR(32) NOT NULL PRIMARY KEY,
  `total_available` INT          NOT NULL DEFAULT 0,
  `last_counted_at` DATETIME     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 给 elevenlabs_voices 加 use_case 独立列（之前 use_case 只在 labels_json 里，新 API 已不再提供 labels 嵌套字段）
ALTER TABLE `elevenlabs_voices`
  ADD COLUMN `use_case` VARCHAR(64) DEFAULT NULL AFTER `descriptive`,
  ADD INDEX `idx_use_case` (`use_case`);
```

- [ ] **Step 2: 本地执行迁移**

按项目现有方式执行（查看 `README.md` / `docs` 或 `db/migrations/` 里其他迁移的运行方式）。若项目用手工执行：

```bash
mysql -u <user> -p <db> < db/migrations/2026_04_19_voice_library_sync_completion.sql
```

如果项目有自动迁移脚本（例如 `python -m appcore.db migrate` 或类似），用该脚本。确认当前目录有 `appcore/db.py`：

```bash
ls appcore/db.py
```

- [ ] **Step 3: 验证**

```bash
mysql -u <user> -p <db> -e "DESCRIBE elevenlabs_voices" | grep use_case
mysql -u <user> -p <db> -e "DESCRIBE elevenlabs_voice_library_stats"
```

期望：`elevenlabs_voices` 包含 `use_case VARCHAR(64)`；`elevenlabs_voice_library_stats` 有 `language / total_available / last_counted_at` 三列。

- [ ] **Step 4: Commit**

```bash
git add db/migrations/2026_04_19_voice_library_sync_completion.sql
git commit -m "feat(db): 新增 elevenlabs_voice_library_stats 表与 voices.use_case 列"
```

---

## Task 2: 重写 `fetch_shared_voices_page` 为 page 分页

**Files:**
- Modify: `pipeline/voice_library_sync.py:22-53`
- Test: `tests/test_voice_library_sync.py`

- [ ] **Step 1: 写失败测试**

替换 `tests/test_voice_library_sync.py` 里 `test_fetch_shared_voices_page_returns_voices_and_next_token` 和 `test_fetch_shared_voices_page_returns_none_when_no_more` 为：

```python
def test_fetch_shared_voices_page_uses_page_param():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [
            {"voice_id": "v1", "name": "Rachel", "gender": "female",
             "language": "en", "preview_url": "http://a.mp3",
             "use_case": "news", "category": "professional"}
        ],
        "has_more": True,
        "total_count": 6308,
    }
    with patch("pipeline.voice_library_sync.requests.get",
               return_value=mock_response) as getter:
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key="dummy", page=2, page_size=100, language="en",
        )
    # 验证 page 参数被传给 API
    call_kwargs = getter.call_args.kwargs
    assert call_kwargs["params"]["page"] == 2
    assert call_kwargs["params"]["page_size"] == 100
    assert call_kwargs["params"]["language"] == "en"
    assert voices[0]["voice_id"] == "v1"
    assert has_more is True
    assert total_count == 6308


def test_fetch_shared_voices_page_stops_when_no_more():
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "voices": [],
        "has_more": False,
        "total_count": 100,
    }
    with patch("pipeline.voice_library_sync.requests.get",
               return_value=mock_response):
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key="dummy", page=4,
        )
    assert voices == []
    assert has_more is False
    assert total_count == 100
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py::test_fetch_shared_voices_page_uses_page_param -v
```

期望：FAIL（签名不匹配或返回值 tuple 长度不同）。

- [ ] **Step 3: 改实现**

`pipeline/voice_library_sync.py:22-53` 替换为：

```python
def fetch_shared_voices_page(
    api_key: str,
    page: int = 0,
    page_size: int = DEFAULT_PAGE_SIZE,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, int]:
    """抓取一页共享音色。返回 (voices, has_more, total_count)。

    page: 0-based。ElevenLabs API 用 page 整数参数翻页，不是 next_page_token。
    total_count: 该 filter 下的远端总量（每次请求都会返回；首次请求取值写 stats 表）。
    """
    headers = {"xi-api-key": api_key}
    params: Dict[str, Any] = {"page": int(page), "page_size": int(page_size)}
    if language:
        params["language"] = language
    if gender:
        params["gender"] = gender
    if category:
        params["category"] = category

    resp = requests.get(
        SHARED_VOICES_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    voices = data.get("voices") or []
    has_more = bool(data.get("has_more"))
    total_count = int(data.get("total_count") or 0)
    return voices, has_more, total_count
```

- [ ] **Step 4: 跑测试确认通过**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py::test_fetch_shared_voices_page_uses_page_param tests/test_voice_library_sync.py::test_fetch_shared_voices_page_stops_when_no_more -v
```

期望：PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_library_sync.py pipeline/voice_library_sync.py
git commit -m "fix(voice-sync): ElevenLabs 共享库分页参数改为 page（整数）并返回 total_count"
```

---

## Task 3: 改 `upsert_voice` 写 use_case + 完整 labels_json

**Files:**
- Modify: `pipeline/voice_library_sync.py:56-91`
- Test: `tests/test_voice_library_sync.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_voice_library_sync.py` 末尾追加：

```python
def test_upsert_voice_writes_use_case_from_top_level():
    from pipeline.voice_library_sync import upsert_voice
    captured = {}

    def fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params

    voice = {
        "voice_id": "v1",
        "name": "Rachel",
        "gender": "female",
        "age": "middle_aged",
        "language": "en",
        "accent": "american",
        "category": "professional",
        "descriptive": "calm",
        "use_case": "informative_educational",   # 顶层
        "preview_url": "http://a.mp3",
        "public_owner_id": "abc",
    }
    with patch("pipeline.voice_library_sync.execute", side_effect=fake_execute):
        upsert_voice(voice)

    # params 顺序与 SQL 对齐：第 9 个 (index 8) 是 use_case
    params = captured["params"]
    # 重新列 params 位置（按 Task 3 实现里的顺序）：
    # (voice_id, name, gender, age, language, accent, category,
    #  descriptive, use_case, preview_url, labels_json, public_owner_id,
    #  synced_at, updated_at)
    assert params[0] == "v1"
    assert params[8] == "informative_educational"
    # labels_json 应该存整条原始 voice dict（便于未来扩展）
    import json as _json
    labels_json_str = params[10]
    payload = _json.loads(labels_json_str)
    assert payload["voice_id"] == "v1"
    assert payload["use_case"] == "informative_educational"


def test_upsert_voice_fallback_use_case_from_labels():
    """老版 API 若 use_case 在 labels 嵌套里，也能被回写到独立列。"""
    from pipeline.voice_library_sync import upsert_voice
    captured = {}
    def fake_execute(sql, params):
        captured["params"] = params
    voice = {
        "voice_id": "v2",
        "name": "Bob",
        "labels": {"use_case": "narration"},  # 旧格式
    }
    with patch("pipeline.voice_library_sync.execute", side_effect=fake_execute):
        upsert_voice(voice)
    assert captured["params"][8] == "narration"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py::test_upsert_voice_writes_use_case_from_top_level tests/test_voice_library_sync.py::test_upsert_voice_fallback_use_case_from_labels -v
```

期望：FAIL（现有 upsert_voice 没有 use_case 列写入）。

- [ ] **Step 3: 改实现**

`pipeline/voice_library_sync.py:56-91` 重写 `upsert_voice`：

```python
def upsert_voice(voice: Dict[str, Any]) -> None:
    """将单条音色写入（或更新）elevenlabs_voices 表。

    兼容两种 API 响应格式：
    - 新版：所有字段（use_case/accent/age/descriptive/gender/language）都在顶层
    - 旧版：嵌套在 `labels` 对象里
    labels_json 列存储整条原始 voice dict，便于未来扩展（verified_languages 等）。
    """
    labels = voice.get("labels") or {}
    now = datetime.utcnow()
    execute(
        """
        INSERT INTO elevenlabs_voices
          (voice_id, name, gender, age, language, accent, category,
           descriptive, use_case, preview_url, labels_json, public_owner_id,
           synced_at, updated_at)
        VALUES
          (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          name=VALUES(name), gender=VALUES(gender), age=VALUES(age),
          language=VALUES(language), accent=VALUES(accent),
          category=VALUES(category), descriptive=VALUES(descriptive),
          use_case=VALUES(use_case), preview_url=VALUES(preview_url),
          labels_json=VALUES(labels_json), public_owner_id=VALUES(public_owner_id),
          synced_at=VALUES(synced_at)
        """,
        (
            voice["voice_id"],
            voice.get("name") or "",
            voice.get("gender") or labels.get("gender"),
            voice.get("age") or labels.get("age"),
            voice.get("language") or labels.get("language"),
            voice.get("accent") or labels.get("accent"),
            voice.get("category"),
            voice.get("descriptive") or labels.get("descriptive"),
            voice.get("use_case") or labels.get("use_case"),
            voice.get("preview_url"),
            json.dumps(voice, ensure_ascii=False),
            voice.get("public_owner_id"),
            now,
            now,
        ),
    )
```

- [ ] **Step 4: 跑测试**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py -v
```

期望：全部 PASS（包括前面 Task 2 的用例）。

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_library_sync.py pipeline/voice_library_sync.py
git commit -m "fix(voice-sync): upsert_voice 写入 use_case 独立列，labels_json 存整条原始响应"
```

---

## Task 4: `sync_all_shared_voices` 加 `max_voices` / `on_total_count`

**Files:**
- Modify: `pipeline/voice_library_sync.py:94-133`
- Test: `tests/test_voice_library_sync.py`

- [ ] **Step 1: 更新现有 mock page 测试并补新用例**

`tests/test_voice_library_sync.py` 里原来的 `test_sync_all_iterates_pages_until_no_more`、`test_sync_all_calls_on_page_callback` 等需要更新（mock 返回值从 `next_page_token` 改为 `has_more` + `total_count`）。追加：

```python
def test_sync_all_respects_max_voices():
    """max_voices=300 时，翻 3 页 × 每页 150 条，应在达到 300 时停止。"""
    from pipeline.voice_library_sync import sync_all_shared_voices
    pages = [
        ([{"voice_id": f"p0_{i}", "name": "x"} for i in range(150)], True, 500),
        ([{"voice_id": f"p1_{i}", "name": "x"} for i in range(150)], True, 500),
        ([{"voice_id": f"p2_{i}", "name": "x"} for i in range(150)], False, 500),
    ]
    calls = {"i": 0}

    def fake_fetch(**kw):
        i = calls["i"]; calls["i"] += 1
        return pages[i]

    with patch("pipeline.voice_library_sync.fetch_shared_voices_page",
               side_effect=fake_fetch), \
         patch("pipeline.voice_library_sync.upsert_voice") as upsert:
        total = sync_all_shared_voices(api_key="k", language="en", max_voices=300)
    assert total == 300
    assert upsert.call_count == 300


def test_sync_all_invokes_on_total_count_first_page():
    from pipeline.voice_library_sync import sync_all_shared_voices
    pages = [([{"voice_id": "v1"}], False, 42)]
    calls = {"i": 0}

    def fake_fetch(**kw):
        i = calls["i"]; calls["i"] += 1
        return pages[i]

    received = {}
    def on_total(n): received["n"] = n

    with patch("pipeline.voice_library_sync.fetch_shared_voices_page",
               side_effect=fake_fetch), \
         patch("pipeline.voice_library_sync.upsert_voice"):
        sync_all_shared_voices(api_key="k", language="en",
                               on_total_count=on_total)
    assert received["n"] == 42


def test_sync_all_stops_when_has_more_false():
    from pipeline.voice_library_sync import sync_all_shared_voices
    pages = [
        ([{"voice_id": "v1"}, {"voice_id": "v2"}], True, 4),
        ([{"voice_id": "v3"}, {"voice_id": "v4"}], False, 4),
    ]
    calls = {"i": 0}
    def fake_fetch(**kw):
        i = calls["i"]; calls["i"] += 1
        return pages[i]

    with patch("pipeline.voice_library_sync.fetch_shared_voices_page",
               side_effect=fake_fetch), \
         patch("pipeline.voice_library_sync.upsert_voice") as upsert:
        total = sync_all_shared_voices(api_key="k")
    assert total == 4
    assert upsert.call_count == 4
```

同时**删除**原 `test_sync_all_iterates_pages_until_no_more`、`test_sync_all_calls_on_page_callback` 两个用例（它们的语义被上面的新用例覆盖 + 原来依赖的 `next_page_token` 已废弃）。若有其他依赖旧签名的测试，按新签名 `(voices, has_more, total_count)` 改 mock 返回值，或删除重写。

- [ ] **Step 2: 跑测试确认新用例失败**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py::test_sync_all_respects_max_voices tests/test_voice_library_sync.py::test_sync_all_invokes_on_total_count_first_page -v
```

期望：FAIL。

- [ ] **Step 3: 改实现**

`pipeline/voice_library_sync.py:94-133` 替换为：

```python
def sync_all_shared_voices(
    api_key: str,
    *,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_voices: Optional[int] = None,
    on_page: Optional[Callable[[int, List[Dict[str, Any]]], None]] = None,
    on_total_count: Optional[Callable[[int], None]] = None,
) -> int:
    """翻页 upsert 到数据库。返回实际 upsert 的条目数。

    - page 用 0-based 整数递增（ElevenLabs API 真实分页方式）。
    - max_voices 达到即 break，超量不再 upsert。
    - on_total_count：仅在 page_index=0 时回调一次，传远端 total_count。
    - on_page：每处理完一页后回调 (page_index, voices)。回调异常仅记 warning。
    """
    total = 0
    page_index = 0
    while True:
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key=api_key,
            page=page_index,
            page_size=page_size,
            language=language,
            gender=gender,
            category=category,
        )
        if page_index == 0 and on_total_count is not None:
            try:
                on_total_count(total_count)
            except Exception as exc:
                log.warning("on_total_count callback failed: %s", exc)

        reached_cap = False
        for voice in voices:
            if not voice.get("voice_id"):
                continue
            upsert_voice(voice)
            total += 1
            if max_voices is not None and total >= max_voices:
                reached_cap = True
                break

        if on_page is not None:
            try:
                on_page(page_index, voices)
            except Exception as exc:
                log.warning("on_page callback failed at page %s: %s",
                            page_index, exc)

        if reached_cap:
            break
        if not has_more:
            break
        page_index += 1
    return total
```

- [ ] **Step 4: 跑全部 sync 测试**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py -v
```

期望：全部 PASS。若 `test_sync_all_*` 旧用例仍存在且因新签名失败，删除它们（在 Step 1 已计划删除）。

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_library_sync.py pipeline/voice_library_sync.py
git commit -m "feat(voice-sync): sync_all_shared_voices 支持 max_voices 上限与 on_total_count 回调"
```

---

## Task 5: 新增 `upsert_library_stats`

**Files:**
- Modify: `pipeline/voice_library_sync.py`（在文件末尾追加）
- Test: `tests/test_voice_library_sync.py`

- [ ] **Step 1: 写失败测试**

`tests/test_voice_library_sync.py` 末尾追加：

```python
def test_upsert_library_stats_calls_execute_with_upsert_sql():
    from pipeline.voice_library_sync import upsert_library_stats
    captured = {}
    def fake_execute(sql, params):
        captured["sql"] = sql
        captured["params"] = params
    with patch("pipeline.voice_library_sync.execute", side_effect=fake_execute):
        upsert_library_stats("en", 6308)
    assert "elevenlabs_voice_library_stats" in captured["sql"]
    assert "ON DUPLICATE KEY UPDATE" in captured["sql"]
    assert captured["params"][0] == "en"
    assert captured["params"][1] == 6308
    # params[2] 是 now datetime
    from datetime import datetime
    assert isinstance(captured["params"][2], datetime)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py::test_upsert_library_stats_calls_execute_with_upsert_sql -v
```

期望：FAIL（函数未定义）。

- [ ] **Step 3: 实现**

在 `pipeline/voice_library_sync.py` 文件末尾追加：

```python
def upsert_library_stats(language: str, total_available: int) -> None:
    """写入/更新某语种的远端共享库总量（来自 API total_count）。"""
    execute(
        """
        INSERT INTO elevenlabs_voice_library_stats
          (language, total_available, last_counted_at)
        VALUES (%s, %s, %s)
        ON DUPLICATE KEY UPDATE
          total_available=VALUES(total_available),
          last_counted_at=VALUES(last_counted_at)
        """,
        (language, int(total_available), datetime.utcnow()),
    )
```

- [ ] **Step 4: 跑测试**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync.py -v
```

期望：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_library_sync.py pipeline/voice_library_sync.py
git commit -m "feat(voice-sync): 新增 upsert_library_stats 写入远端总量"
```

---

## Task 6: 改 `voice_library_sync_task.py` — 上限 / 回调 / summarize 联表

**Files:**
- Modify: `appcore/voice_library_sync_task.py`
- Test: `tests/test_voice_library_sync_task.py`

- [ ] **Step 1: 写失败测试**

`tests/test_voice_library_sync_task.py` 追加用例（保留原有用例）：

```python
def test_summarize_includes_total_available(monkeypatch):
    """summarize 应联表 elevenlabs_voice_library_stats 拿 total_available。"""
    from appcore import voice_library_sync_task as vlst
    voices_rows = [
        {"language": "en", "total_rows": 100, "embedded_rows": 14,
         "last_synced_at": None}
    ]
    stats_rows = [
        {"language": "en", "total_available": 6308, "last_counted_at": None}
    ]
    call_count = {"i": 0}
    def fake_query(sql, *args):
        call_count["i"] += 1
        if "elevenlabs_voice_library_stats" in sql:
            return stats_rows
        return voices_rows
    monkeypatch.setattr(vlst, "query", fake_query)
    monkeypatch.setattr(
        "appcore.medias.list_enabled_languages_kv",
        lambda: [("en", "英语")],
    )
    out = vlst.summarize()
    assert out[0]["language"] == "en"
    assert out[0]["total_available"] == 6308
    assert out[0]["total_rows"] == 100


def test_max_voices_per_language_constant():
    from appcore.voice_library_sync_task import MAX_VOICES_PER_LANGUAGE
    assert MAX_VOICES_PER_LANGUAGE == 300
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync_task.py::test_summarize_includes_total_available tests/test_voice_library_sync_task.py::test_max_voices_per_language_constant -v
```

期望：FAIL。

- [ ] **Step 3: 改 summarize + 加常量 + 改 _run_sync_sync**

`appcore/voice_library_sync_task.py` 在顶部常量区加：

```python
MAX_VOICES_PER_LANGUAGE = 300
```

替换 `summarize()`（原 line 70-91）：

```python
def summarize() -> list[dict]:
    from appcore import medias
    voice_rows = query(
        "SELECT language, "
        "  COUNT(*) AS total_rows, "
        "  SUM(CASE WHEN audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded_rows, "
        "  MAX(synced_at) AS last_synced_at "
        "FROM elevenlabs_voices GROUP BY language"
    )
    stats_rows = query(
        "SELECT language, total_available, last_counted_at "
        "FROM elevenlabs_voice_library_stats"
    )
    voice_stats = {r["language"]: r for r in voice_rows}
    avail_stats = {r["language"]: r for r in stats_rows}
    out: list[dict] = []
    for code, name in medias.list_enabled_languages_kv():
        s = voice_stats.get(code, {}) or {}
        a = avail_stats.get(code, {}) or {}
        last_synced = s.get("last_synced_at")
        out.append({
            "language": code,
            "name_zh": name,
            "total_rows": int(s.get("total_rows") or 0),
            "embedded_rows": int(s.get("embedded_rows") or 0),
            "total_available": int(a.get("total_available") or 0),
            "last_synced_at": last_synced.isoformat() if last_synced else None,
        })
    return out
```

替换 `_run_sync_sync()`（原 line 105-129）：

```python
def _run_sync_sync(sync_id: str, language: str, api_key: str) -> None:
    from pipeline.voice_library_sync import (
        sync_all_shared_voices,
        embed_missing_voices,
        upsert_library_stats,
    )
    try:
        total_pulled = [0]
        total_available_holder = [0]

        def on_total_count(n: int) -> None:
            total_available_holder[0] = int(n)
            try:
                upsert_library_stats(language, int(n))
            except Exception as exc:
                log.warning("upsert_library_stats failed: %s", exc)
            cap = min(MAX_VOICES_PER_LANGUAGE, int(n)) if n else 0
            _set(phase="pull_metadata", done=total_pulled[0], total=cap)

        def on_page(idx, voices):
            total_pulled[0] += len(voices)
            cap = min(
                MAX_VOICES_PER_LANGUAGE,
                total_available_holder[0] or total_pulled[0],
            )
            _set(phase="pull_metadata", done=total_pulled[0], total=cap)

        sync_all_shared_voices(
            api_key=api_key,
            language=language,
            max_voices=MAX_VOICES_PER_LANGUAGE,
            on_page=on_page,
            on_total_count=on_total_count,
        )

        def on_progress(done, total, voice_id, ok):
            _set(phase="embed", done=done, total=total)

        cache_dir = os.path.join("uploads", "voice_preview_cache")
        embed_missing_voices(
            cache_dir, on_progress=on_progress, language=language,
        )

        _set(status="done", phase="done")
        _emit("voice_library.sync.summary", {"summary": summarize()})
    except Exception as exc:
        log.exception("voice sync %s failed", sync_id)
        _set(status="failed", error=str(exc))
```

- [ ] **Step 4: 跑测试**

```bash
/c/Python314/python -m pytest tests/test_voice_library_sync_task.py -v
```

期望：全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_library_sync_task.py appcore/voice_library_sync_task.py
git commit -m "feat(voice-sync): 任务流程加 300 上限，summarize 联 stats 表取远端总量"
```

---

## Task 7: `voice_library_browse.py` — use_case 走独立列

**Files:**
- Modify: `appcore/voice_library_browse.py`
- Test: `tests/test_voice_library_browse.py`

- [ ] **Step 1: 写失败测试**

`tests/test_voice_library_browse.py` 追加：

```python
def test_list_voices_filters_use_case_via_column(monkeypatch):
    """use_cases 过滤应使用独立列 use_case = %s 而非 JSON_EXTRACT。"""
    from appcore import voice_library_browse as vlb
    captured = {}

    def fake_query_one(sql, params):
        captured["count_sql"] = sql
        captured["count_params"] = params
        return {"c": 0}

    def fake_query(sql, params):
        captured["list_sql"] = sql
        captured["list_params"] = params
        return []

    monkeypatch.setattr(vlb, "query_one", fake_query_one)
    monkeypatch.setattr(vlb, "query", fake_query)

    vlb.list_voices(language="en", use_cases=["news", "narration"])

    # 新 SQL 应含 "use_case IN" 而非 "JSON_EXTRACT(labels_json, '$.use_case')"
    assert "use_case IN" in captured["count_sql"]
    assert "JSON_EXTRACT(labels_json, '$.use_case')" not in captured["count_sql"]
    # params 里含 two use_case 值
    assert "news" in captured["count_params"]
    assert "narration" in captured["count_params"]


def test_list_filter_options_use_cases_via_distinct_column(monkeypatch):
    from appcore import voice_library_browse as vlb
    captured = {}
    def fake_query(sql, params):
        captured.setdefault("sqls", []).append(sql)
        if "DISTINCT use_case" in sql:
            return [{"use_case": "news"}, {"use_case": "narration"}]
        # accent/age/descriptive 仍走 labels_json（本次不动）
        return []
    monkeypatch.setattr(vlb, "query", fake_query)

    result = vlb.list_filter_options(language="en")
    # 应包含 use_case 独立列的 DISTINCT 查询
    assert any("DISTINCT use_case" in s for s in captured["sqls"])
    assert result["use_cases"] == ["narration", "news"]


def test_row_to_dict_uses_column_use_case_over_labels(monkeypatch):
    """_row_to_dict 读到 row 里的 use_case 独立列时优先于 labels_json。"""
    from appcore import voice_library_browse as vlb
    row = {
        "voice_id": "v1", "name": "x", "gender": "male", "language": "en",
        "age": None, "accent": None, "category": None, "descriptive": "",
        "preview_url": "", "use_case": "news",
        "labels_json": '{"use_case": "ignored"}',
    }
    out = vlb._row_to_dict(row)
    assert out["use_case"] == "news"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
/c/Python314/python -m pytest tests/test_voice_library_browse.py::test_list_voices_filters_use_case_via_column tests/test_voice_library_browse.py::test_list_filter_options_use_cases_via_distinct_column tests/test_voice_library_browse.py::test_row_to_dict_uses_column_use_case_over_labels -v
```

期望：FAIL。

- [ ] **Step 3: 改实现**

`appcore/voice_library_browse.py` 改三处：

**1) `_SELECT_FIELDS`（line 22-25）加上 use_case：**

```python
_SELECT_FIELDS = (
    "voice_id, name, gender, language, age, accent, category, "
    "descriptive, use_case, preview_url, labels_json"
)
```

**2) `_LABEL_FIELDS`（line 27）去掉 use_case（只剩 accent/age/descriptive，仍走 JSON 兜底）：**

```python
_LABEL_FIELDS = frozenset({"accent", "age", "descriptive"})
```

**3) `_row_to_dict`（line 47-54）改读独立列优先：**

```python
def _row_to_dict(row: dict) -> dict:
    labels = _parse_labels(row.get("labels_json"))
    out = dict(row)
    out["labels"] = labels
    out.pop("labels_json", None)
    out["use_case"] = row.get("use_case") or labels.get("use_case")
    out["description"] = labels.get("description") or row.get("descriptive") or ""
    return out
```

**4) `list_voices` 的 use_case 过滤（line 90-91）改独立列：**

原：
```python
    if use_cases:
        _json_in("use_case", use_cases)
```

改为：
```python
    if use_cases:
        marks = ",".join(["%s"] * len(use_cases))
        where.append(f"use_case IN ({marks})")
        params.extend(use_cases)
```

**5) `list_filter_options`（line 129-164）重写 use_cases 的来源**：

```python
def list_filter_options(*, language: str) -> dict:
    """返回某语种下所有声音的 label 枚举（去重 + 升序）。"""
    if not language:
        raise ValueError("language is required")

    # use_case 走独立列
    uc_rows = query(
        "SELECT DISTINCT use_case FROM elevenlabs_voices "
        "WHERE language = %s AND use_case IS NOT NULL AND use_case <> ''",
        (language,),
    )
    use_cases: set[str] = {r["use_case"] for r in uc_rows if r.get("use_case")}

    # 其他三个字段仍从 labels_json 读（保留现有兼容逻辑）
    rows = query(
        "SELECT labels_json FROM elevenlabs_voices WHERE language = %s",
        (language,),
    )
    accents: set[str] = set()
    ages: set[str] = set()
    descriptives: set[str] = set()
    for r in rows:
        labels = _parse_labels(r.get("labels_json"))
        v = labels.get("accent")
        if v: accents.add(v)
        v = labels.get("age")
        if v: ages.add(v)
        v = labels.get("descriptive")
        if v: descriptives.add(v)

    return {
        "use_cases": sorted(use_cases),
        "accents": sorted(accents),
        "ages": sorted(ages),
        "descriptives": sorted(descriptives),
    }
```

- [ ] **Step 4: 跑全部 browse 测试**

```bash
/c/Python314/python -m pytest tests/test_voice_library_browse.py -v
```

期望：全部 PASS。若原有某些用例断言 `JSON_EXTRACT(labels_json, '$.use_case')` 字符串存在，这些用例需要更新为断言 `use_case IN`——直接修好。

- [ ] **Step 5: Commit**

```bash
git add tests/test_voice_library_browse.py appcore/voice_library_browse.py
git commit -m "feat(voice-browse): use_case 筛选改走独立列，保留其他 label 兼容"
```

---

## Task 8: 前端表格新增"远端总量"列

**Files:**
- Modify: `web/templates/admin_settings.html:182-186`
- Modify: `web/static/admin_settings.js:295-317`

- [ ] **Step 1: 改模板表头**

`web/templates/admin_settings.html` 里 `<thead><tr>...</tr></thead>` 改为：

```html
<thead><tr>
  <th>语种</th>
  <th>条目数</th>
  <th>远端总量</th>
  <th>声纹覆盖</th>
  <th>最后同步</th>
  <th>操作</th>
</tr></thead>
```

- [ ] **Step 2: 改 JS render**

`web/static/admin_settings.js` 的 `render()` 函数里 `tr.innerHTML = \`...\`` 改为：

```js
const entryCell = row.total_available
  ? `${row.total_rows} / ${row.total_available}`
  : `${row.total_rows}`;
const availCell = row.total_available || "-";
tr.innerHTML = `
  <td>${escapeHtml(row.name_zh)} (${escapeHtml(row.language)})</td>
  <td>${entryCell}</td>
  <td>${availCell}</td>
  <td>${row.embedded_rows}/${row.total_rows} (${ratio})</td>
  <td>${row.last_synced_at ? escapeHtml(row.last_synced_at) : "未同步"}</td>
  <td><button data-lang="${escapeHtml(row.language)}" class="oc-btn-primary vl-sync-btn"
        ${busy ? "disabled" : ""}>${busy && busyLang === row.language ? "同步中…" : (busy ? "排队中" : "同步")}</button></td>
`;
```

- [ ] **Step 3: 手动验证**

启动开发服（按 CLAUDE.md 的本地运行方式；常规 Flask 项目是 `python main.py` 或 `flask run`）：

```bash
/c/Python314/python main.py
```

浏览器打开 `http://localhost:<port>/admin/settings`（或相应路径），滚到"声音库同步"模块，确认：
- 表头多一列"远端总量"
- 每行显示 `<total_rows> / <total_available>` 或占位 `-`（若 stats 表还没数据）

- [ ] **Step 4: Commit**

```bash
git add web/templates/admin_settings.html web/static/admin_settings.js
git commit -m "feat(admin-settings): 声音库同步表格新增“远端总量”列"
```

---

## Task 9: 跑全部 pytest

- [ ] **Step 1: 跑项目全量测试**

```bash
/c/Python314/python -m pytest -q 2>&1 | tail -40
```

期望：全部 PASS。

- [ ] **Step 2: 若有失败**

针对失败用例逐条排查——
- 若是 mock 接口变更导致旧用例挂：按新签名 `(voices, has_more, total_count)` / `voice.get("use_case")` / stats 联表更新 mock。
- 若是 runtime 错误：看 stack trace 定位模块，检查本 plan 是否漏改。

- [ ] **Step 3: Commit 修复（若有）**

```bash
git add -A
git commit -m "fix(tests): 同步测试适配新签名与 stats 联表"
```

---

## Task 10: 部署测试环境 + QA 英语同步

（按 CLAUDE.md 的"测试发布流程"：commit+push+SSH 部署测试环境，端口 8080，数据库 auto_video_test）

- [ ] **Step 1: 推送到远端**

```bash
git push origin master
```

- [ ] **Step 2: 测试环境部署 + 执行迁移**

SSH 到服务器（见 [memory/reference_server_deploy.md](../../../memory/reference_server_deploy.md) / `server.md`），在测试环境目录下：

```bash
cd /opt/autovideosrt-test
git pull
# 跑迁移（若项目有 migrate 脚本用它；否则手工 mysql -e）
mysql auto_video_test < db/migrations/2026_04_19_voice_library_sync_completion.sql
# 重启
systemctl restart autovideosrt-test
```

- [ ] **Step 3: QA 英语同步**

通过浏览器登录测试环境管理后台（:8080），进入"系统设置 → 声音库同步"模块，点英语行的"同步"按钮。

- [ ] **Step 4: 等到完成后验证**

预期表格英语行显示：
- 条目数：`300 / 6308`（或 total_available 的真实值）
- 远端总量：`6308`
- 声纹覆盖：`300/300 (100.0%)`（或接近 100%——个别失败允许）

若下面任一不满足，停下来排查：
- 远端总量为 0 或 "-" → `on_total_count` 没触发或 stats 表未写入，查 `appcore/voice_library_sync_task.py`
- 条目数仍是 100 → 分页未生效，查 `fetch_shared_voices_page`
- 声纹覆盖显著低于 100% → 检查 `uploads/voice_preview_cache` 目录与 `embed_missing_voices` 日志，可能是网络问题

- [ ] **Step 5: DB sanity check**

```bash
mysql auto_video_test -e "
  SELECT language, COUNT(*) AS rows_cnt,
         SUM(CASE WHEN use_case IS NOT NULL THEN 1 ELSE 0 END) AS has_use_case,
         SUM(CASE WHEN audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS has_embed
  FROM elevenlabs_voices WHERE language='en' GROUP BY language;
  SELECT * FROM elevenlabs_voice_library_stats WHERE language='en';
"
```

期望：`rows_cnt=300, has_use_case=300, has_embed≈300`；stats 行 `total_available>=300`。

---

## Task 11: 部署正式环境 + Claude 代跑 7 个语种

（按 CLAUDE.md 的"发布流程"：commit+push+SSH pull+restart）

- [ ] **Step 1: 部署正式环境**

```bash
cd /opt/autovideosrt
git pull
mysql auto_video < db/migrations/2026_04_19_voice_library_sync_completion.sql
systemctl restart autovideosrt
```

- [ ] **Step 2: 依次触发 7 个语种同步**

顺序：en → de → fr → es → it → ja → pt

对每个语种，执行：
1. `curl -X POST <base>/admin/voice-library/sync/<lang> -b <admin_cookie>` 或在浏览器里点"同步"按钮
2. 轮询 `GET <base>/admin/voice-library/sync-status` 直到 `current.status == "done"` 或 `current == null`
3. 记录 summary 里该语种的 `total_rows / embedded_rows / total_available`

示例脚本（engineer 可执行）：
```bash
# 假设已有 admin cookie 在 ~/.ave-cookie
for lang in en de fr es it ja pt; do
  echo "=== syncing $lang ==="
  curl -s -X POST -b ~/.ave-cookie "https://<host>/admin/voice-library/sync/$lang"
  while true; do
    status=$(curl -s -b ~/.ave-cookie "https://<host>/admin/voice-library/sync-status")
    running=$(echo "$status" | jq -r '.current.status // "null"')
    echo "[$lang] $status" | head -c 200; echo
    [ "$running" != "running" ] && break
    sleep 15
  done
done
```

- [ ] **Step 3: 汇总报告给用户**

整理一张表：

| 语种 | total_available（远端） | 入库 | 声纹成功 | 覆盖率 |
|---|---|---|---|---|
| en | ? | ? | ? | ? |
| de | ? | ? | ? | ? |
| ... | | | | |

从 `GET /admin/voice-library/sync-status` 的 `summary` 字段或直接 SQL 查：

```bash
mysql auto_video -e "
  SELECT v.language,
         s.total_available,
         COUNT(*) AS in_db,
         SUM(CASE WHEN v.audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded
  FROM elevenlabs_voices v
  LEFT JOIN elevenlabs_voice_library_stats s ON s.language = v.language
  GROUP BY v.language, s.total_available
  ORDER BY v.language;
"
```

- [ ] **Step 4: 把汇总表返回给用户**

用户会用这张表决定是否要扩大每语种上限或关闭某个语种。

---

## 完成条件

- [ ] pytest 全绿
- [ ] 测试环境英语同步：300 条、100% 覆盖、远端总量显示正确
- [ ] 正式环境 7 个语种同步完成
- [ ] 汇总表提交给用户

## 回滚方案

若上线后发现严重问题（同步任务挂死、前端崩溃）：

1. 迁移是**加列 + 加表**，不会破坏旧数据；回滚只需 `git revert <commit>`、重启服务即可。
2. stats 表为空不影响——前端会显示 `-`。
3. use_case 独立列即使不读也不影响老路径（labels_json 兜底仍在）。
