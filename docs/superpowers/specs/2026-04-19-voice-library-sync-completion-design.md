# 声音库同步完善（ElevenLabs）设计

**日期**：2026-04-19  
**状态**：已与用户确认，待进入实施计划

## 背景与问题

管理后台"系统设置 → 声音库同步（ElevenLabs）"功能当前呈现的状态：

- 7 个启用小语种（en/de/fr/es/it/ja/pt），每个语种都只有 **100 条**条目，且其中英语只有 **14%** 声纹覆盖，其余 6 个语种 100%。
- 仅英语低覆盖的表面原因：`preview_url` 下载或 embedding 生成失败；
- 所有语种只到 100 条的深层原因：**分页 bug**（见下）；
- `use_case` 字段在 DB 中丢失：**labels 存储 bug**（见下）。

### Bug 1 — 分页参数用错

[pipeline/voice_library_sync.py](../../pipeline/voice_library_sync.py) 里 `fetch_shared_voices_page` 的分页用了 `next_page_token`，但实测 ElevenLabs `/v1/shared-voices` API：

- 真实分页参数是 `page`（整数，0-based）。
- 响应里没有 `next_page_token`；`last_sort_id` 默认 `null`（是 search 场景的游标）。
- `has_more` 字段可用。

证据（`scratch/probe_elevenlabs_shared_voices.py` 运行结果）：

| 请求参数 | 返回的前 5 个 voice_id |
|---|---|
| `language=en, page_size=5`（默认 page=0） | `fIoDqeVKE9jI9AGvEvZU, Qwt5fMS1MhNERgnc6Nhf, …` |
| `language=en, page_size=5, page=1` | `TVlVy5MpBMAQl7kIoUnj, JjFExtCYfBGn1nn478bh, …` ← 不同，真翻页 |
| `language=en, page_size=5, page_index=1` | 和默认一样 ← 参数无效 |

且响应顶层还有 `total_count`（`language=en` 返回 6308）——意味着**首次请求即可拿到该语种远端总量**，不需要单独的"计数阶段"。

`page_size` 上限 100（>100 返回 HTTP 400）。

### Bug 2 — `use_case` / labels 字段丢失

[pipeline/voice_library_sync.py](../../pipeline/voice_library_sync.py) 的 `upsert_voice`：

```python
labels = voice.get("labels") or {}
# voice.get("gender") or labels.get("gender"),  # 等
json.dumps(labels),  # 存到 labels_json
```

但 ElevenLabs 新版 API **已把 labels 对象平铺到顶层**（probe 响应里没有 `labels` 字段）。结果：

- `gender / age / accent / descriptive / language` — 因为 `voice.get(...)` 双保险，独立列仍正确写入；
- **`use_case` 没有独立列**，只依赖 `labels_json` → 整个 `labels_json` 是 `{}` → `use_case` 彻底丢失；
- [appcore/voice_library_browse.py](../../appcore/voice_library_browse.py) 的 `use_case` 筛选读 `labels_json.use_case`，**永远命中 0**。

## 目标

1. 修分页 bug，让 `sync_all_shared_voices` 真能翻页。
2. 每个启用语种按 **最多 300 条** upsert 元数据 + 对这批生成声纹。
3. 首次响应从 `total_count` 写入新表 `elevenlabs_voice_library_stats`，在管理表格里显示"远端总量"。
4. `elevenlabs_voices` 增加 `use_case` 独立列，upsert 正确写入；`labels_json` 保留一份原始 voice 响应（含 `verified_languages` 等），便于未来扩展。
5. `voice_library_browse` 的 `use_case` 筛选改读独立列。
6. 保留原有"单语种同步"按钮，不加"同步所有"按钮。
7. 实现+部署后，由 Claude 代为串行触发所有启用语种的同步。

## 非目标

- 不做"同步所有语种"一键按钮。
- 不放开单任务并行锁。
- 不做语速模型、不改声音匹配逻辑。
- 不迁移已有 `labels_json` 数据（历史数据 `labels_json = {}`，新一轮同步重写即可）。

## 架构总览

改动集中在 4 个模块：

| 模块 | 文件 | 变更 |
|---|---|---|
| DB 迁移 | `db/migrations/2026_04_19_voice_library_sync_completion.sql`（新） | 建 stats 表 + 给 `elevenlabs_voices` 加 `use_case` 列 |
| pipeline | [pipeline/voice_library_sync.py](../../pipeline/voice_library_sync.py) | 修分页 + `max_voices` 上限 + 首次响应回写 stats + `upsert_voice` 写 `use_case` 和完整 `labels_json` |
| task 编排 | [appcore/voice_library_sync_task.py](../../appcore/voice_library_sync_task.py) | `summarize()` 联表读 stats；保持 2 阶段 |
| browse | [appcore/voice_library_browse.py](../../appcore/voice_library_browse.py) | `use_case` 筛选改读独立列 |
| 前端 | [web/templates/admin_settings.html](../../web/templates/admin_settings.html) + [web/static/admin_settings.js](../../web/static/admin_settings.js) | 表格新增"远端总量"列 |

其余（`web/routes/admin.py`、`scripts/sync_voice_libraries.py`）不变。`scripts/sync_voice_libraries.py` 自然受益于 `sync_all_shared_voices` 的修复。

## 详细设计

### 1. DB 迁移

文件：`db/migrations/2026_04_19_voice_library_sync_completion.sql`

```sql
-- 新建远端总量统计表
CREATE TABLE IF NOT EXISTS `elevenlabs_voice_library_stats` (
  `language`        VARCHAR(32) NOT NULL PRIMARY KEY,
  `total_available` INT          NOT NULL DEFAULT 0,
  `last_counted_at` DATETIME     NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 给 elevenlabs_voices 加 use_case 独立列
ALTER TABLE `elevenlabs_voices`
  ADD COLUMN `use_case` VARCHAR(64) DEFAULT NULL AFTER `descriptive`,
  ADD INDEX `idx_use_case` (`use_case`);
```

### 2. pipeline/voice_library_sync.py

**`fetch_shared_voices_page` 重写**：

```python
def fetch_shared_voices_page(
    api_key: str,
    page: int = 0,
    page_size: int = 100,
    language: Optional[str] = None,
    gender: Optional[str] = None,
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], bool, int]:
    """抓取第 page 页。返回 (voices, has_more, total_count)。"""
    headers = {"xi-api-key": api_key}
    params = {"page": page, "page_size": page_size}
    if language:  params["language"]  = language
    if gender:    params["gender"]    = gender
    if category:  params["category"]  = category
    resp = requests.get(SHARED_VOICES_URL, headers=headers, params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    voices      = data.get("voices") or []
    has_more    = bool(data.get("has_more"))
    total_count = int(data.get("total_count") or 0)
    return voices, has_more, total_count
```

**`upsert_voice` 重写**：

```python
def upsert_voice(voice: Dict[str, Any]) -> None:
    labels = voice.get("labels") or {}  # 老响应兼容
    now = datetime.utcnow()
    execute(
        """
        INSERT INTO elevenlabs_voices
          (voice_id, name, gender, age, language, accent, category,
           descriptive, use_case, preview_url, labels_json, public_owner_id,
           synced_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            voice.get("gender")      or labels.get("gender"),
            voice.get("age")         or labels.get("age"),
            voice.get("language")    or labels.get("language"),
            voice.get("accent")      or labels.get("accent"),
            voice.get("category"),
            voice.get("descriptive") or labels.get("descriptive"),
            voice.get("use_case")    or labels.get("use_case"),
            voice.get("preview_url"),
            json.dumps(voice, ensure_ascii=False),  # ← 存整条原始响应
            voice.get("public_owner_id"),
            now,
            now,
        ),
    )
```

**`sync_all_shared_voices` 重写**（加 `max_voices`、首页回写 stats）：

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
    total = 0
    page_index = 0
    while True:
        voices, has_more, total_count = fetch_shared_voices_page(
            api_key=api_key, page=page_index, page_size=page_size,
            language=language, gender=gender, category=category,
        )
        if page_index == 0 and on_total_count is not None:
            try: on_total_count(total_count)
            except Exception as exc:
                log.warning("on_total_count callback failed: %s", exc)
        for voice in voices:
            if not voice.get("voice_id"): continue
            upsert_voice(voice)
            total += 1
            if max_voices is not None and total >= max_voices:
                break
        if on_page is not None:
            try: on_page(page_index, voices)
            except Exception as exc:
                log.warning("on_page callback failed at page %s: %s", page_index, exc)
        if max_voices is not None and total >= max_voices: break
        if not has_more: break
        page_index += 1
    return total
```

**新函数 `upsert_library_stats`**（同一模块，或放到 task 模块，看实施时哪边更顺）：

```python
def upsert_library_stats(language: str, total_available: int) -> None:
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

### 3. appcore/voice_library_sync_task.py

**`_run_sync_sync` 改动**（2 阶段，新增 total_count 回写 + 广播）：

```python
def _run_sync_sync(sync_id: str, language: str, api_key: str) -> None:
    from pipeline.voice_library_sync import (
        sync_all_shared_voices, embed_missing_voices, upsert_library_stats,
    )
    try:
        total_pulled = [0]
        total_available = [0]

        def on_total_count(n):
            total_available[0] = n
            upsert_library_stats(language, n)
            _set(phase="pull_metadata", done=total_pulled[0],
                 total=min(MAX_VOICES_PER_LANGUAGE, n))

        def on_page(idx, voices):
            total_pulled[0] += len(voices)
            cap = min(MAX_VOICES_PER_LANGUAGE, total_available[0] or total_pulled[0])
            _set(phase="pull_metadata", done=total_pulled[0], total=cap)

        sync_all_shared_voices(
            api_key=api_key, language=language,
            max_voices=MAX_VOICES_PER_LANGUAGE,
            on_page=on_page, on_total_count=on_total_count,
        )

        def on_progress(done, total, voice_id, ok):
            _set(phase="embed", done=done, total=total)

        cache_dir = os.path.join("uploads", "voice_preview_cache")
        embed_missing_voices(cache_dir, on_progress=on_progress, language=language)

        _set(status="done", phase="done")
        _emit("voice_library.sync.summary", {"summary": summarize()})
    except Exception as exc:
        log.exception("voice sync %s failed", sync_id)
        _set(status="failed", error=str(exc))
```

`MAX_VOICES_PER_LANGUAGE = 300` 放在模块顶部常量。

**`summarize()` 改动**（联表读 stats，补 `total_available` 字段）：

```python
def summarize() -> list[dict]:
    from appcore import medias
    rows = query(
        "SELECT v.language, "
        "  COUNT(*) AS total_rows, "
        "  SUM(CASE WHEN v.audio_embedding IS NOT NULL THEN 1 ELSE 0 END) AS embedded_rows, "
        "  MAX(v.synced_at) AS last_synced_at "
        "FROM elevenlabs_voices v GROUP BY v.language"
    )
    stats_rows = query(
        "SELECT language, total_available, last_counted_at "
        "FROM elevenlabs_voice_library_stats"
    )
    stats = {r["language"]: r for r in rows}
    avail = {r["language"]: r for r in stats_rows}
    out: list[dict] = []
    for code, name in medias.list_enabled_languages_kv():
        s = stats.get(code, {}) or {}
        a = avail.get(code, {}) or {}
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

### 4. appcore/voice_library_browse.py

把 `use_case` 筛选从 `labels_json` 迁到独立列：

- `_LABEL_FIELDS` 中移除 `use_case`（该集合留给 accent/age/descriptive，但这三个本身也有独立列，需要检查是否已走独立列；若没有，此次可先只迁 `use_case`，其他保持兼容）。
- `list_voices` 里 `use_cases` 参数的 WHERE 改为 `use_case IN (...)` 直接查独立列。
- 筛选枚举 `list_filters` 中 `use_cases` 的来源改为 `SELECT DISTINCT use_case ...`。

保守起见，保留读 `labels_json` 的兜底代码，用独立列优先（若独立列为空再 fallback），保证迁移期老数据不瞎。

### 5. 前端

[web/templates/admin_settings.html](../../web/templates/admin_settings.html) 表头：

```html
<thead><tr>
  <th>语种</th>
  <th>条目数</th>
  <th>远端总量</th>  <!-- 新增 -->
  <th>声纹覆盖</th>
  <th>最后同步</th>
  <th>操作</th>
</tr></thead>
```

[web/static/admin_settings.js](../../web/static/admin_settings.js) `render()` 渲染行：

```js
const totalCell = row.total_available
  ? `${row.total_rows} / ${row.total_available}`
  : `${row.total_rows}`;
const availCell = row.total_available || "-";
tr.innerHTML = `
  <td>${escapeHtml(row.name_zh)} (${escapeHtml(row.language)})</td>
  <td>${totalCell}</td>
  <td>${availCell}</td>
  <td>${row.embedded_rows}/${row.total_rows} (${ratio})</td>
  ...
`;
```

阶段文案无需加 `count`（已合并）。

## 数据流示意

```
用户点 [同步]
    ↓ POST /admin/voice-library/sync/en
start_sync(en) → daemon 线程 _run_sync_sync
    ↓
Phase 1: pull_metadata
  page=0 → fetch → total_count=6308 → upsert_library_stats('en', 6308) → _set total=min(300, 6308)=300
  for voice in voices: upsert_voice(voice)  # 写 use_case + labels_json(raw)
  page=1,2 … 直到 total==300 break
    ↓
Phase 2: embed
  _list_voices_without_embedding(language='en')  # 含补齐英语 86 条历史未成功的
  for row: 下载 preview → embed → UPDATE
    ↓
summarize() → 广播 summary
前端 render 表格： "en 300 / 6308  300/300 (100%) ..."
```

## 错误处理与边界

- **count=0**（某语种库空）：`on_total_count(0)` 仍写 stats（记录尝试过）；`sync_all` 拿到 0 条直接退出；embed 阶段处理 0 条；UI 显示 `0 / 0`。
- **count < 300**：第一个 page 已经 `has_more=false`，`sync_all` 自然退出，upsert 多少存多少，UI 显示 `{n} / {total_count}`。
- **count > 300**：翻到满 300 即 break，UI 显示 `300 / total_count`。
- **preview 下载失败**：`embed_missing_voices` 单条失败不中断；embedded_rows 就是实际成功数。未来再同步会重试（`audio_embedding IS NULL` 条件）。
- **API 4xx / 5xx**：`requests.raise_for_status()` 抛出，`_run_sync_sync` 的 `except` 捕获，`status="failed"` 广播错误消息；已 upsert 的部分保留。
- **并发锁**：`start_sync` 的 `_LOCK` + `status==running` 检查保持，409 给第二次点击。
- **迁移兼容**：应用启动时运行迁移脚本；上线前确保迁移已执行（和项目现有迁移机制一致）。

## 测试策略

### 单元测试（修改 `tests/test_voice_library_sync.py`）

- `test_fetch_shared_voices_page_uses_page_param`：mock `requests.get`，验证 `params["page"]` 为整数，响应解析 `(voices, has_more, total_count)`。
- `test_sync_all_shared_voices_respects_max_voices`：mock 3 页每页 150 条，`max_voices=300` → 退出时 total=300。
- `test_sync_all_shared_voices_invokes_on_total_count`：首次回调拿到 `total_count`。
- `test_upsert_voice_writes_use_case_from_top_level`：voice dict 顶层有 `use_case` → DB 独立列写入。
- `test_upsert_voice_fallback_use_case_from_labels`：旧版响应（`labels.use_case`）也能被写入。
- `test_upsert_library_stats_upsert_semantic`：重复写以 `last_counted_at` 更新为准。

### 集成测试（`tests/test_voice_library_sync_admin.py` 扩展）

- `summarize()` 返回的每行包含 `total_available` 字段。
- 触发 `/admin/voice-library/sync/en`（mock API + embedding），结束后查 stats 表 + voices 表正确。

### browse 回归

- `tests/test_voice_library_browse.py`：`use_case` 筛选走独立列，命中正确。

### 手动 QA

1. 本地跑迁移 + 单测。
2. 部署测试环境（按 CLAUDE.md 发布流程之"测试发布"）。
3. 在测试环境触发英语同步，确认：
   - stats 表有 `en / total_available=6308+` 记录。
   - voices 表英语行 = 300，`use_case` 列有值。
   - 页面表格显示 `300 / 6308  300/300 (100%)`。
4. 正式环境部署，Claude 代为串行触发 7 个语种。

## 实施顺序（将在 plan 里细化）

1. 写迁移 SQL + 执行。
2. 改 `pipeline/voice_library_sync.py`（分页 + upsert + stats）+ 单测。
3. 改 `appcore/voice_library_sync_task.py`（task 流程 + summarize）+ 集成测试。
4. 改 `appcore/voice_library_browse.py`（use_case 走独立列）+ 回归测试。
5. 改前端模板/JS（新增"远端总量"列）。
6. 本地 pytest 全绿。
7. 部署测试环境 → QA 英语同步。
8. 发布正式环境 → Claude 代为逐语种触发同步（en→de→fr→es→it→ja→pt）。
9. 汇总报告给用户：每语种 `total_available / total_rows / embedded_rows`。

## 风险与假设

- **假设 A**：`total_count` 字段在所有 language filter 下都返回。风险低（probe 已覆盖 `language=en` 和无 filter 两种）。
- **假设 B**：ElevenLabs 不会在短期内改 API。若再次变更（labels 重新嵌套、page 换回 token），代码里已有兼容代码（`voice.get(x) or labels.get(x)`）可部分挡一下。
- **风险**：300 条 × 7 语种的同步总时长（主要是下载 preview + resemblyzer）可能 30-90 分钟。Claude 代跑需要等得住；失败可重试，幂等写入安全。

## 变更文件清单

新增：
- `db/migrations/2026_04_19_voice_library_sync_completion.sql`
- `scratch/probe_elevenlabs_shared_voices.py`（已存在，作为一次性探测保留可删）
- `docs/superpowers/specs/2026-04-19-voice-library-sync-completion-design.md`（本文件）
- `docs/superpowers/plans/2026-04-19-voice-library-sync-completion-plan.md`（下一步 writing-plans 产出）

修改：
- `pipeline/voice_library_sync.py`
- `appcore/voice_library_sync_task.py`
- `appcore/voice_library_browse.py`
- `web/templates/admin_settings.html`
- `web/static/admin_settings.js`
- `tests/test_voice_library_sync.py`
- `tests/test_voice_library_sync_admin.py`
- `tests/test_voice_library_browse.py`

不变：
- `web/routes/admin.py`
- `web/routes/voice_library.py`
- `scripts/sync_voice_libraries.py`（自然受益）
