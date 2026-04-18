# 整体素材批量翻译(bulk_translate)实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现"一键从英文翻译到多语言"的整体素材批量翻译功能,涵盖文案/主图/详情图/视频四类素材,父任务编排 + 真实子任务复用模式,严格人工触发恢复。

**Architecture:** 父任务(`bulk_translate` 新增 type)复用现有 `projects` 表,`state_json` 存 plan + 审计 + 费用。父任务调度器串行派发子任务,子任务复用现有三种 type(`translate_lab` / `image_translate`)+ 新增 `copywriting_translate`。子任务完成时回写四张素材表的 `source_ref_id` / `bulk_task_id` / `auto_translated` 字段。UI 三件套(右下角气泡 + `/tasks` 列表 + `/tasks/<id>` 详情)通过 SocketIO 实时推送。

**Tech Stack:**
- Backend: Python 3 + Flask + eventlet + SQLAlchemy + pytest
- Frontend: Jinja2 模板 + 原生 JS(Ocean Blue Design System)+ Socket.IO client
- Data: MySQL + TOS 对象存储
- 参考设计文档: `docs/superpowers/specs/2026-04-18-bulk-translate-design.md`

**Phases 概览:**
- Phase 1 (Task 1-5):数据层 & 迁移 · 里程碑:表结构就绪,参数回填能查
- Phase 2 (Task 6-10):`copywriting_translate` 子任务 runtime · 里程碑:能跑单条英文→德语文案
- Phase 3 (Task 11-14):费用预估器 + estimate API · 里程碑:前端能查预估
- Phase 4 (Task 15-22):父任务调度器 + plan 生成 · 里程碑:能串行跑通小型任务
- Phase 5 (Task 23-32):父任务 API 套件 · 里程碑:curl 能完成全流程
- Phase 6 (Task 33-40):弹窗 + 气泡 UI · 里程碑:能触发任务且气泡展示
- Phase 7 (Task 41-48):任务中心 + 详情页 UI · 里程碑:能查进度、操作按钮
- Phase 8 (Task 49-51):两个入口接入 · 里程碑:素材管理 + 视频翻译详情页可触发
- Phase 9 (Task 52-57):关联标识 UI · 里程碑:徽章、悬浮卡、筛选、"已人工修改"标识
- Phase 10 (Task 58-62):端到端验收 · 里程碑:de/fr 跑通,铁律验证通过

**关键铁律(开发全程必须遵守):**
1. **绝不自动恢复任何 bulk_translate 任务** —— 进程启动不扫描、不对账
2. **子任务失败 → 父任务立即停**,绝不跳过继续跑
3. **"开始翻译"必须过二次确认**,展示预估费用
4. **所有恢复/重跑 API 都记 `audit_events`**
5. **视频翻译仅对 de/fr**,其他语言勾视频项静默 `skipped`
6. **`copywriting_translate` 是新 type**,不要塞进现有 `copywriting` 路由
7. **不新建 ORM 实体,只用一个迁移 SQL 文件**

---

## Phase 1 · 数据层与迁移

### Task 1: 编写迁移 SQL

**Files:**
- Create: `db/migrations/2026_04_18_bulk_translate_schema.sql`
- Test: `tests/test_bulk_translate_migration.py`

- [ ] **Step 1: 编写迁移 SQL 文件**

创建 `db/migrations/2026_04_18_bulk_translate_schema.sql`:

```sql
-- 2026-04-18 bulk translate 设计迁移
-- 1) projects.type 新增枚举值 bulk_translate / copywriting_translate
-- 2) 四张素材表增加关联追踪字段
-- 3) 新增 media_video_translate_profiles 表

-- ========== 1. projects 表 type 扩展 ==========
ALTER TABLE projects
  MODIFY COLUMN type ENUM(
    'translation','de_translate','fr_translate','copywriting',
    'video_creation','video_review','translate_lab',
    'image_translate','subtitle_removal',
    'bulk_translate','copywriting_translate'
  ) NOT NULL;

-- ========== 2. 四张素材表加关联追踪字段 ==========
ALTER TABLE media_copywritings
  ADD COLUMN source_ref_id     VARCHAR(64) NULL COMMENT '指向源英文条目 id',
  ADD COLUMN bulk_task_id      VARCHAR(64) NULL COMMENT '指向父任务 projects.id',
  ADD COLUMN auto_translated   TINYINT(1)  NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP NULL DEFAULT NULL COMMENT '用户手工修改自动翻译结果的时间',
  ADD INDEX idx_cw_source_ref (source_ref_id),
  ADD INDEX idx_cw_bulk_task  (bulk_task_id);

ALTER TABLE media_product_detail_images
  ADD COLUMN source_ref_id     VARCHAR(64) NULL,
  ADD COLUMN bulk_task_id      VARCHAR(64) NULL,
  ADD COLUMN auto_translated   TINYINT(1)  NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP NULL DEFAULT NULL,
  ADD INDEX idx_detail_source_ref (source_ref_id),
  ADD INDEX idx_detail_bulk_task  (bulk_task_id);

ALTER TABLE media_items
  ADD COLUMN source_ref_id     VARCHAR(64) NULL,
  ADD COLUMN bulk_task_id      VARCHAR(64) NULL,
  ADD COLUMN auto_translated   TINYINT(1)  NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP NULL DEFAULT NULL,
  ADD INDEX idx_item_source_ref (source_ref_id),
  ADD INDEX idx_item_bulk_task  (bulk_task_id);

ALTER TABLE media_product_covers
  ADD COLUMN source_ref_id     VARCHAR(64) NULL,
  ADD COLUMN bulk_task_id      VARCHAR(64) NULL,
  ADD COLUMN auto_translated   TINYINT(1)  NOT NULL DEFAULT 0,
  ADD COLUMN manually_edited_at TIMESTAMP NULL DEFAULT NULL,
  ADD INDEX idx_cover_source_ref (source_ref_id),
  ADD INDEX idx_cover_bulk_task  (bulk_task_id);

-- ========== 3. 视频翻译参数持久化 ==========
CREATE TABLE media_video_translate_profiles (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  user_id      VARCHAR(64) NOT NULL,
  product_id   VARCHAR(64) NULL COMMENT 'NULL = 用户级全局默认',
  lang         VARCHAR(8)  NULL COMMENT 'NULL = 产品级全语言默认',
  params_json  JSON NOT NULL,
  created_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uk_scope (user_id, product_id, lang),
  INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='视频翻译 12 项参数三层持久化(user/product/lang)';
```

- [ ] **Step 2: 写迁移应用测试**

创建 `tests/test_bulk_translate_migration.py`:

```python
"""验证 bulk_translate 迁移后 schema 正确。"""
import pytest
from appcore.db import get_engine
from sqlalchemy import inspect, text


def test_projects_type_includes_bulk_translate():
    eng = get_engine()
    with eng.connect() as conn:
        row = conn.execute(text(
            "SHOW COLUMNS FROM projects WHERE Field = 'type'"
        )).first()
        assert row is not None
        col_type = row[1]
        assert 'bulk_translate' in col_type
        assert 'copywriting_translate' in col_type


@pytest.mark.parametrize("table", [
    "media_copywritings",
    "media_product_detail_images",
    "media_items",
    "media_product_covers",
])
def test_material_tables_have_tracking_columns(table):
    eng = get_engine()
    insp = inspect(eng)
    cols = {c['name'] for c in insp.get_columns(table)}
    assert 'source_ref_id' in cols, f"{table} missing source_ref_id"
    assert 'bulk_task_id' in cols, f"{table} missing bulk_task_id"
    assert 'auto_translated' in cols, f"{table} missing auto_translated"
    assert 'manually_edited_at' in cols, f"{table} missing manually_edited_at"


def test_video_translate_profiles_table_exists():
    eng = get_engine()
    insp = inspect(eng)
    assert 'media_video_translate_profiles' in insp.get_table_names()
    cols = {c['name'] for c in insp.get_columns('media_video_translate_profiles')}
    expected = {'id', 'user_id', 'product_id', 'lang', 'params_json',
                'created_at', 'updated_at'}
    assert expected.issubset(cols)
```

- [ ] **Step 3: 运行测试确认失败(迁移未应用)**

```bash
pytest tests/test_bulk_translate_migration.py -v
```

Expected: 三个测试全部 FAIL,提示缺少列/缺少枚举值。

- [ ] **Step 4: 应用迁移到本地数据库**

```bash
# 用项目的迁移方式应用
mysql -u <user> -p <database> < db/migrations/2026_04_18_bulk_translate_schema.sql
```

如果项目有专用迁移工具(比如 `python -m appcore.migrate`),改用:
```bash
python -m appcore.migrate apply 2026_04_18_bulk_translate_schema
```

- [ ] **Step 5: 运行测试确认通过**

```bash
pytest tests/test_bulk_translate_migration.py -v
```

Expected: 6 个测试全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add db/migrations/2026_04_18_bulk_translate_schema.sql \
        tests/test_bulk_translate_migration.py
git commit -m "feat(bulk-translate): 迁移 SQL 新增 projects type 与素材表关联字段"
```

---

### Task 2: 定义 SYSTEM_DEFAULTS 常量

**Files:**
- Create: `appcore/video_translate_defaults.py`
- Test: `tests/test_video_translate_defaults.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_video_translate_defaults.py`:

```python
"""验证视频翻译默认值常量与回填逻辑。"""
from appcore.video_translate_defaults import (
    SYSTEM_DEFAULTS, TTS_VOICE_DEFAULTS, VIDEO_SUPPORTED_LANGS,
)


def test_system_defaults_has_all_12_params():
    required = {
        "subtitle_font", "subtitle_size", "subtitle_position_y",
        "subtitle_color", "subtitle_stroke_color", "subtitle_stroke_width",
        "subtitle_burn_in", "subtitle_export_srt",
        "subtitle_background",
        "tts_speed", "background_audio", "background_audio_db",
        "max_line_width",
        "output_resolution", "output_codec", "output_bitrate_kbps",
        "output_format",
    }
    assert required.issubset(SYSTEM_DEFAULTS.keys())


def test_system_defaults_values():
    assert SYSTEM_DEFAULTS["subtitle_font"] == "Noto Sans"
    assert SYSTEM_DEFAULTS["subtitle_size"] == 14
    assert SYSTEM_DEFAULTS["subtitle_position_y"] == 0.88
    assert SYSTEM_DEFAULTS["subtitle_color"] == "#FFFFFF"
    assert SYSTEM_DEFAULTS["subtitle_stroke_color"] == "#000000"
    assert SYSTEM_DEFAULTS["subtitle_burn_in"] is True
    assert SYSTEM_DEFAULTS["subtitle_export_srt"] is True
    assert SYSTEM_DEFAULTS["tts_speed"] == 1.0
    assert SYSTEM_DEFAULTS["background_audio"] == "keep"


def test_video_supported_langs():
    assert VIDEO_SUPPORTED_LANGS == {"de", "fr"}


def test_tts_voice_defaults_has_de_and_fr():
    assert "de" in TTS_VOICE_DEFAULTS
    assert "fr" in TTS_VOICE_DEFAULTS
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_video_translate_defaults.py -v
```

Expected: ImportError.

- [ ] **Step 3: 实现常量文件**

创建 `appcore/video_translate_defaults.py`:

```python
"""视频翻译 12 项参数默认值与语言支持常量。

设计文档: docs/superpowers/specs/2026-04-18-bulk-translate-design.md 第 3.3 节
"""

# 系统出厂默认值(最终兜底)
SYSTEM_DEFAULTS = {
    # 基础档
    "subtitle_font": "Noto Sans",
    "subtitle_size": 14,
    "subtitle_position_y": 0.88,
    "subtitle_color": "#FFFFFF",
    "subtitle_stroke_color": "#000000",
    "subtitle_stroke_width": 2,
    "subtitle_burn_in": True,
    "subtitle_export_srt": True,
    # 进阶档
    "subtitle_background": "none",
    "tts_speed": 1.0,
    "background_audio": "keep",
    "background_audio_db": -18,
    "max_line_width": 42,
    # 高级档
    "output_resolution": "source",
    "output_codec": "h264",
    "output_bitrate_kbps": 2000,
    "output_format": "mp4",
}

# 每语言的默认 TTS 音色(最终 voice_id 在 Task 8 里根据 TTS 供应商探测覆盖)
TTS_VOICE_DEFAULTS = {
    "de": "Anke",
    "fr": "Céline",
}

# 视频翻译本期支持语言集合(本设计第 0.4 节强约束)
VIDEO_SUPPORTED_LANGS = {"de", "fr"}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_video_translate_defaults.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: 提交**

```bash
git add appcore/video_translate_defaults.py tests/test_video_translate_defaults.py
git commit -m "feat(bulk-translate): SYSTEM_DEFAULTS 12 项视频翻译参数默认值"
```

---

### Task 3: video_translate_profile DAO — 查询回填

**Files:**
- Modify: `appcore/video_translate_defaults.py`
- Test: `tests/test_video_translate_profile_dao.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_video_translate_profile_dao.py`:

```python
"""三层回填逻辑:product×lang → product → user → SYSTEM_DEFAULTS。"""
import pytest
from appcore.video_translate_defaults import (
    SYSTEM_DEFAULTS,
    load_effective_params,
    save_profile,
)


USER_ID = "test_user_1"
PRODUCT_ID = "prod_test_a"


@pytest.fixture
def clean_profiles(db):
    """每个测试前清空该用户的 profiles。"""
    db.execute("DELETE FROM media_video_translate_profiles WHERE user_id = %s",
               (USER_ID,))
    yield
    db.execute("DELETE FROM media_video_translate_profiles WHERE user_id = %s",
               (USER_ID,))


def test_load_returns_system_defaults_when_no_profile(clean_profiles):
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result == SYSTEM_DEFAULTS


def test_user_level_profile_overrides_defaults(clean_profiles):
    save_profile(USER_ID, product_id=None, lang=None,
                 params={"subtitle_size": 18})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 18
    assert result["subtitle_color"] == SYSTEM_DEFAULTS["subtitle_color"]


def test_product_level_profile_overrides_user_level(clean_profiles):
    save_profile(USER_ID, product_id=None, lang=None,
                 params={"subtitle_size": 18, "tts_speed": 1.2})
    save_profile(USER_ID, product_id=PRODUCT_ID, lang=None,
                 params={"subtitle_size": 20})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 20     # 产品级覆盖
    assert result["tts_speed"] == 1.2        # 产品级未设,回退用户级
    assert result["subtitle_color"] == SYSTEM_DEFAULTS["subtitle_color"]


def test_product_lang_level_overrides_product_level(clean_profiles):
    save_profile(USER_ID, product_id=PRODUCT_ID, lang=None,
                 params={"subtitle_size": 20})
    save_profile(USER_ID, product_id=PRODUCT_ID, lang="de",
                 params={"subtitle_size": 22, "tts_speed": 0.9})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 22
    assert result["tts_speed"] == 0.9


def test_fr_does_not_inherit_de_specific_profile(clean_profiles):
    save_profile(USER_ID, product_id=PRODUCT_ID, lang="de",
                 params={"subtitle_size": 22})
    result = load_effective_params(USER_ID, PRODUCT_ID, "fr")
    assert result["subtitle_size"] == SYSTEM_DEFAULTS["subtitle_size"]


def test_save_upsert(clean_profiles):
    save_profile(USER_ID, PRODUCT_ID, "de", {"subtitle_size": 20})
    save_profile(USER_ID, PRODUCT_ID, "de", {"subtitle_size": 24})
    result = load_effective_params(USER_ID, PRODUCT_ID, "de")
    assert result["subtitle_size"] == 24
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_video_translate_profile_dao.py -v
```

Expected: ImportError `load_effective_params` / `save_profile`.

- [ ] **Step 3: 实现 DAO**

在 `appcore/video_translate_defaults.py` 底部追加:

```python
import json
from appcore.db import get_db_session


def _fetch_params(user_id, product_id, lang):
    """查询一条 profile,返回 params dict 或 None。"""
    with get_db_session() as s:
        row = s.execute(
            """
            SELECT params_json
            FROM media_video_translate_profiles
            WHERE user_id = %s
              AND (product_id <=> %s)
              AND (lang <=> %s)
            LIMIT 1
            """,
            (user_id, product_id, lang),
        ).first()
        if row is None:
            return None
        raw = row[0]
        return raw if isinstance(raw, dict) else json.loads(raw)


def load_effective_params(user_id, product_id, lang):
    """三层回填查询。product×lang → product → user → SYSTEM_DEFAULTS。"""
    effective = dict(SYSTEM_DEFAULTS)
    # 按"粗→细"顺序合并,后面的覆盖前面的
    for scope in [(user_id, None, None),
                  (user_id, product_id, None),
                  (user_id, product_id, lang)]:
        params = _fetch_params(*scope)
        if params:
            effective.update(params)
    return effective


def save_profile(user_id, product_id, lang, params):
    """Upsert 一条 profile。"""
    payload = json.dumps(params, ensure_ascii=False)
    with get_db_session() as s:
        s.execute(
            """
            INSERT INTO media_video_translate_profiles
                (user_id, product_id, lang, params_json)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE params_json = VALUES(params_json)
            """,
            (user_id, product_id, lang, payload),
        )
        s.commit()
```

> 注:`<=>` 是 MySQL 的 null-safe 相等运算符,确保 `lang IS NULL` 和 `lang='de'` 都能精确匹配唯一索引。若项目使用 SQLAlchemy ORM,改用 ORM 写法;参考 `appcore/medias.py` 里同类型 DAO 的写法。

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_video_translate_profile_dao.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: 提交**

```bash
git add appcore/video_translate_defaults.py tests/test_video_translate_profile_dao.py
git commit -m "feat(bulk-translate): 视频翻译参数三层回填 DAO"
```

---

### Task 4: TTS 音色探测函数

**Files:**
- Modify: `appcore/video_translate_defaults.py`
- Test: `tests/test_video_translate_defaults.py`(扩展)

- [ ] **Step 1: 探测现有 TTS 模块,找出可用音色 API**

```bash
grep -rn "def.*voice" appcore/ | grep -v test | head -20
```

找到返回 `(code, voice_id, name)` 列表的函数(可能在 `appcore/voice_library_sync_task.py` 或 `appcore/tts*.py`),记下函数签名。

- [ ] **Step 2: 写失败测试**

在 `tests/test_video_translate_defaults.py` 追加:

```python
def test_resolve_default_voice_falls_back_when_unavailable(monkeypatch):
    from appcore import video_translate_defaults as mod

    # 模拟该语种没有任何可用音色
    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [])
    assert mod.resolve_default_voice("de") is None


def test_resolve_default_voice_prefers_mapped(monkeypatch):
    from appcore import video_translate_defaults as mod

    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v_other", "name": "Random"},
        {"voice_id": "v_anke", "name": "Anke"},
    ])
    assert mod.resolve_default_voice("de") == "v_anke"


def test_resolve_default_voice_first_when_mapped_missing(monkeypatch):
    from appcore import video_translate_defaults as mod

    monkeypatch.setattr(mod, "_list_voices_by_lang", lambda lang: [
        {"voice_id": "v_random", "name": "Random Voice"},
    ])
    assert mod.resolve_default_voice("de") == "v_random"
```

- [ ] **Step 3: 实现 `resolve_default_voice`**

在 `appcore/video_translate_defaults.py` 追加:

```python
def _list_voices_by_lang(lang):
    """列出某语种的可用 TTS 音色。返回 [{'voice_id': ..., 'name': ...}]。"""
    # 实际接入时替换为真实 TTS 查询;本期使用 voice_library 查询
    from appcore import voice_library  # 根据项目实际模块名调整
    try:
        return voice_library.list_voices(lang=lang, enabled=True)
    except Exception:
        return []


def resolve_default_voice(lang):
    """给定目标语言,返回默认 voice_id:
    1. 优先匹配 TTS_VOICE_DEFAULTS 里名字(含大小写不敏感 contains 检索)
    2. 若没匹配,取列表第 1 个
    3. 若列表为空,返回 None
    """
    voices = _list_voices_by_lang(lang)
    if not voices:
        return None

    preferred = TTS_VOICE_DEFAULTS.get(lang)
    if preferred:
        for v in voices:
            if preferred.lower() in (v.get("name") or "").lower():
                return v["voice_id"]

    return voices[0]["voice_id"]
```

> 注:若项目的音色查询模块名不是 `voice_library`,改用 `grep -rn "list_voices\|list_tts_voices" appcore/` 找到正确入口。

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_video_translate_defaults.py -v
```

Expected: 7 PASS(含 Task 2 原有的 4 个)。

- [ ] **Step 5: 提交**

```bash
git add appcore/video_translate_defaults.py tests/test_video_translate_defaults.py
git commit -m "feat(bulk-translate): TTS 音色默认探测函数 resolve_default_voice"
```

---

### Task 5: 素材表关联字段的写入辅助函数

**Files:**
- Create: `appcore/bulk_translate_associations.py`
- Test: `tests/test_bulk_translate_associations.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_bulk_translate_associations.py`:

```python
"""测试把父任务 + 源条目 id 写入四张素材表的辅助函数。"""
import pytest
from appcore.bulk_translate_associations import mark_auto_translated


def test_mark_auto_translated_copywriting(db, test_user):
    # 先插一条英文源文案和一条德语译本
    en_id = _insert_copy(db, test_user, lang="en", text="Welcome")
    de_id = _insert_copy(db, test_user, lang="de", text="Willkommen")
    mark_auto_translated(
        table="media_copywritings",
        target_id=de_id,
        source_ref_id=en_id,
        bulk_task_id="task_xxx",
    )
    row = db.execute(
        "SELECT source_ref_id, bulk_task_id, auto_translated FROM media_copywritings WHERE id=%s",
        (de_id,),
    ).first()
    assert row[0] == en_id
    assert row[1] == "task_xxx"
    assert row[2] == 1


def test_mark_auto_translated_idempotent(db, test_user):
    de_id = _insert_copy(db, test_user, lang="de", text="Willkommen")
    mark_auto_translated("media_copywritings", de_id, "src_1", "task_1")
    mark_auto_translated("media_copywritings", de_id, "src_1", "task_1")
    # 没报错即通过


def test_mark_manually_edited(db, test_user):
    from appcore.bulk_translate_associations import mark_manually_edited
    de_id = _insert_copy(db, test_user, lang="de", text="Willkommen")
    mark_auto_translated("media_copywritings", de_id, "src_1", "task_1")

    mark_manually_edited("media_copywritings", de_id)
    row = db.execute(
        "SELECT manually_edited_at FROM media_copywritings WHERE id=%s",
        (de_id,),
    ).first()
    assert row[0] is not None


def _insert_copy(db, user_id, lang, text):
    import uuid
    cid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO media_copywritings (id, user_id, product_id, lang, text) VALUES (%s, %s, 'p1', %s, %s)",
        (cid, user_id, lang, text),
    )
    return cid
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_bulk_translate_associations.py -v
```

Expected: ImportError.

- [ ] **Step 3: 实现辅助函数**

创建 `appcore/bulk_translate_associations.py`:

```python
"""把"自动翻译"关联关系写入四张素材表的辅助函数。

四张表结构一致(都有 source_ref_id / bulk_task_id / auto_translated /
manually_edited_at 四列),用一个白名单限制表名,避免 SQL 注入。
"""
from appcore.db import get_db_session

_ALLOWED_TABLES = {
    "media_copywritings",
    "media_product_detail_images",
    "media_items",
    "media_product_covers",
}


def _check_table(table):
    if table not in _ALLOWED_TABLES:
        raise ValueError(f"Unsupported table: {table}")


def mark_auto_translated(table, target_id, source_ref_id, bulk_task_id):
    """把 target_id 这条素材标记为"由 source_ref_id 自动翻译生成"。"""
    _check_table(table)
    with get_db_session() as s:
        s.execute(
            f"""
            UPDATE {table}
               SET source_ref_id = %s,
                   bulk_task_id  = %s,
                   auto_translated = 1
             WHERE id = %s
            """,
            (source_ref_id, bulk_task_id, target_id),
        )
        s.commit()


def mark_manually_edited(table, target_id):
    """用户手工编辑了自动翻译结果,打上"已人工修改"时间戳。"""
    _check_table(table)
    with get_db_session() as s:
        s.execute(
            f"UPDATE {table} SET manually_edited_at = NOW() WHERE id = %s",
            (target_id,),
        )
        s.commit()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_bulk_translate_associations.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: 提交**

```bash
git add appcore/bulk_translate_associations.py tests/test_bulk_translate_associations.py
git commit -m "feat(bulk-translate): 素材表关联字段写入辅助函数"
```

**🎯 Phase 1 里程碑**:表结构就绪,视频翻译参数三层回填可用,素材关联标记函数可用。

---

## Phase 2 · copywriting_translate 子任务 runtime

### Task 6: 翻译纯文本的工具函数

**Files:**
- Create: `appcore/copywriting_translate_runtime.py`
- Test: `tests/test_copywriting_translate_runtime.py`

- [ ] **Step 1: 探测现有文本翻译函数**

```bash
grep -rn "def.*translate.*text\|translate_plain\|translate_chunk" pipeline/ appcore/ | head -10
```

找到现有"把一段文本翻译到目标语言"的函数(通常在 `pipeline/translate.py`)。记下签名。

- [ ] **Step 2: 写失败测试**

创建 `tests/test_copywriting_translate_runtime.py`:

```python
"""copywriting_translate runtime 单元测试。"""
from unittest.mock import patch
from appcore.copywriting_translate_runtime import translate_copy_text


def test_translate_copy_text_calls_pipeline():
    with patch("appcore.copywriting_translate_runtime._llm_translate") as m:
        m.return_value = ("Willkommen zu unserem Produkt", 42)
        text, tokens = translate_copy_text(
            source_text="Welcome to our product",
            source_lang="en",
            target_lang="de",
        )
        assert text == "Willkommen zu unserem Produkt"
        assert tokens == 42
        m.assert_called_once()


def test_translate_copy_text_empty_input():
    text, tokens = translate_copy_text("", "en", "de")
    assert text == ""
    assert tokens == 0
```

- [ ] **Step 3: 运行测试确认失败**

```bash
pytest tests/test_copywriting_translate_runtime.py -v
```

Expected: ImportError.

- [ ] **Step 4: 实现纯文本翻译**

创建 `appcore/copywriting_translate_runtime.py`:

```python
"""把 media_copywritings 里 lang=en 的英文文案翻译到目标语言。

不要与现有 appcore/copywriting_runtime.py(从视频生成文案)混淆——
本模块只做"英文文本 → 目标语言文本"的纯翻译,复用 pipeline.translate 的 LLM 适配层。
"""
from pipeline.translate import resolve_provider_config, translate_text


def _llm_translate(source_text, source_lang, target_lang):
    """封装 pipeline.translate 的 LLM 文本翻译。返回 (译文, token 数)。"""
    provider_cfg = resolve_provider_config(task_type="copywriting_translate")
    result = translate_text(
        text=source_text,
        source_lang=source_lang,
        target_lang=target_lang,
        provider_cfg=provider_cfg,
    )
    return result["text"], result.get("tokens", 0)


def translate_copy_text(source_text, source_lang, target_lang):
    """翻译一条文案。返回 (译文, 消耗的 token 数)。空输入直接返回。"""
    if not source_text or not source_text.strip():
        return "", 0
    return _llm_translate(source_text, source_lang, target_lang)
```

> 注:`translate_text()` 的真实签名需核对 `pipeline/translate.py`。如果项目中它返回 dict 结构不同,按实际调整。

- [ ] **Step 5: 运行测试确认通过**

```bash
pytest tests/test_copywriting_translate_runtime.py -v
```

Expected: 2 PASS.

- [ ] **Step 6: 提交**

```bash
git add appcore/copywriting_translate_runtime.py tests/test_copywriting_translate_runtime.py
git commit -m "feat(copywriting-translate): 纯文本翻译包装函数"
```

---

### Task 7: copywriting_translate 子任务 runtime 骨架

**Files:**
- Modify: `appcore/copywriting_translate_runtime.py`
- Test: `tests/test_copywriting_translate_runtime.py`

- [ ] **Step 1: 写集成测试**

在 `tests/test_copywriting_translate_runtime.py` 追加:

```python
import pytest
from unittest.mock import patch
from appcore.copywriting_translate_runtime import CopywritingTranslateRunner


def test_runner_happy_path(db, test_user):
    # 准备英文源文案
    src_id = _insert_copy(db, test_user, "en", "Welcome to our product")
    product_id = _get_product_id_of(db, src_id)

    # 创建子任务 project 记录
    task_id = _create_project(
        db, test_user, type="copywriting_translate",
        state_json={
            "product_id": product_id,
            "source_lang": "en",
            "target_lang": "de",
            "source_copy_id": src_id,
            "parent_task_id": "parent_xxx",
        },
    )

    with patch("appcore.copywriting_translate_runtime.translate_copy_text") as m:
        m.return_value = ("Willkommen zu unserem Produkt", 42)
        runner = CopywritingTranslateRunner(task_id)
        runner.start()

    # 验证生成了 lang=de 的文案
    rows = db.execute(
        "SELECT id, text, source_ref_id, bulk_task_id, auto_translated FROM media_copywritings WHERE product_id=%s AND lang='de'",
        (product_id,),
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row[1] == "Willkommen zu unserem Produkt"
    assert row[2] == src_id
    assert row[3] == "parent_xxx"
    assert row[4] == 1

    # 验证任务状态
    state = _get_project_state(db, task_id)
    assert state["status"] == "done"
    assert state["tokens_used"] == 42


def test_runner_failure_marks_error(db, test_user):
    src_id = _insert_copy(db, test_user, "en", "Welcome")
    product_id = _get_product_id_of(db, src_id)
    task_id = _create_project(
        db, test_user, type="copywriting_translate",
        state_json={
            "product_id": product_id,
            "source_lang": "en", "target_lang": "de",
            "source_copy_id": src_id,
            "parent_task_id": "parent_xxx",
        },
    )

    with patch("appcore.copywriting_translate_runtime.translate_copy_text") as m:
        m.side_effect = RuntimeError("LLM timeout")
        runner = CopywritingTranslateRunner(task_id)
        with pytest.raises(RuntimeError):
            runner.start()

    state = _get_project_state(db, task_id)
    assert state["status"] == "error"
    assert "LLM timeout" in state.get("last_error", "")


def _insert_copy(db, user_id, lang, text):
    import uuid
    cid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO media_copywritings (id, user_id, product_id, lang, text) VALUES (%s, %s, 'p_test_1', %s, %s)",
        (cid, user_id, lang, text),
    )
    return cid


def _get_product_id_of(db, copy_id):
    return db.execute("SELECT product_id FROM media_copywritings WHERE id=%s",
                      (copy_id,)).first()[0]


def _create_project(db, user_id, type, state_json):
    import uuid, json
    pid = str(uuid.uuid4())
    db.execute(
        "INSERT INTO projects (id, user_id, type, status, state_json) VALUES (%s, %s, %s, 'queued', %s)",
        (pid, user_id, type, json.dumps(state_json)),
    )
    return pid


def _get_project_state(db, task_id):
    import json
    row = db.execute("SELECT status, state_json FROM projects WHERE id=%s",
                     (task_id,)).first()
    state = json.loads(row[1])
    state["status"] = row[0]
    return state
```

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_copywriting_translate_runtime.py::test_runner_happy_path -v
```

Expected: ImportError `CopywritingTranslateRunner`.

- [ ] **Step 3: 实现 Runner**

在 `appcore/copywriting_translate_runtime.py` 追加:

```python
import json
import uuid
from datetime import datetime

from appcore.db import get_db_session
from appcore import task_state
from appcore.bulk_translate_associations import mark_auto_translated


class CopywritingTranslateRunner:
    """执行 copywriting_translate 子任务:读英文源文案 → 翻译 → 写目标语言条目。"""

    def __init__(self, task_id):
        self.task_id = task_id
        self.state = self._load_state()

    def _load_state(self):
        with get_db_session() as s:
            row = s.execute(
                "SELECT user_id, state_json FROM projects WHERE id=%s",
                (self.task_id,),
            ).first()
            if not row:
                raise ValueError(f"Project {self.task_id} not found")
            state = json.loads(row[1])
            state["user_id"] = row[0]
            return state

    def _save_state(self, patch):
        self.state.update(patch)
        persist = {k: v for k, v in self.state.items() if k != "user_id"}
        with get_db_session() as s:
            s.execute(
                "UPDATE projects SET state_json=%s WHERE id=%s",
                (json.dumps(persist, ensure_ascii=False), self.task_id),
            )
            s.commit()

    def start(self):
        task_state.set_step(self.task_id, "translate", "running")
        try:
            src = self._load_source_copy()
            target_text, tokens = translate_copy_text(
                src["text"], self.state["source_lang"], self.state["target_lang"],
            )
            target_id = self._insert_target_copy(src, target_text)
            mark_auto_translated(
                "media_copywritings",
                target_id=target_id,
                source_ref_id=self.state["source_copy_id"],
                bulk_task_id=self.state.get("parent_task_id"),
            )
            self._save_state({"tokens_used": tokens, "target_copy_id": target_id})
            task_state.set_step(self.task_id, "translate", "done")
            task_state.set_status(self.task_id, "done")
        except Exception as e:
            self._save_state({"last_error": str(e)})
            task_state.set_step(self.task_id, "translate", "error")
            task_state.set_status(self.task_id, "error")
            raise

    def _load_source_copy(self):
        with get_db_session() as s:
            row = s.execute(
                "SELECT id, product_id, text FROM media_copywritings WHERE id=%s AND deleted_at IS NULL",
                (self.state["source_copy_id"],),
            ).first()
            if not row:
                raise ValueError("Source copy not found or deleted")
            return {"id": row[0], "product_id": row[1], "text": row[2]}

    def _insert_target_copy(self, src, translated_text):
        target_id = str(uuid.uuid4())
        with get_db_session() as s:
            s.execute(
                """
                INSERT INTO media_copywritings
                    (id, user_id, product_id, lang, text, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (target_id, self.state["user_id"], src["product_id"],
                 self.state["target_lang"], translated_text, datetime.utcnow()),
            )
            s.commit()
        return target_id
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_copywriting_translate_runtime.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: 提交**

```bash
git add appcore/copywriting_translate_runtime.py tests/test_copywriting_translate_runtime.py
git commit -m "feat(copywriting-translate): Runner 执行骨架与失败处理"
```

---

### Task 8: copywriting_translate 路由注册

**Files:**
- Create: `web/routes/copywriting_translate.py`
- Modify: `web/app.py`(注册 blueprint,按现有模式)
- Test: `tests/test_copywriting_translate_routes.py`

- [ ] **Step 1: 探测现有 blueprint 注册位置**

```bash
grep -n "register_blueprint\|copywriting_bp" web/app.py web/__init__.py 2>/dev/null | head -10
```

- [ ] **Step 2: 写失败测试**

创建 `tests/test_copywriting_translate_routes.py`:

```python
def test_trigger_copywriting_translate_endpoint(client, test_user_auth):
    """POST /api/copywriting-translate/start 触发子任务"""
    resp = client.post(
        "/api/copywriting-translate/start",
        json={
            "source_copy_id": "src_xxx",
            "target_lang": "de",
            "parent_task_id": "parent_xxx",
        },
        headers=test_user_auth,
    )
    assert resp.status_code in (200, 202)
    data = resp.get_json()
    assert "task_id" in data
```

- [ ] **Step 3: 实现路由**

创建 `web/routes/copywriting_translate.py`:

```python
"""copywriting_translate 子任务的 HTTP 入口。"""
import json
import uuid
import eventlet
from flask import Blueprint, request, jsonify, g

from appcore.db import get_db_session
from appcore.copywriting_translate_runtime import CopywritingTranslateRunner

bp = Blueprint("copywriting_translate", __name__, url_prefix="/api/copywriting-translate")


@bp.post("/start")
def start():
    user_id = g.current_user_id  # 项目里获取当前用户的惯用方式
    payload = request.get_json(force=True)
    source_copy_id = payload.get("source_copy_id")
    target_lang = payload.get("target_lang")
    parent_task_id = payload.get("parent_task_id")

    if not source_copy_id or not target_lang:
        return jsonify({"error": "source_copy_id 和 target_lang 必填"}), 400

    task_id = str(uuid.uuid4())
    state = {
        "source_copy_id": source_copy_id,
        "source_lang": "en",
        "target_lang": target_lang,
        "parent_task_id": parent_task_id,
    }
    with get_db_session() as s:
        s.execute(
            """
            INSERT INTO projects (id, user_id, type, status, state_json)
            VALUES (%s, %s, 'copywriting_translate', 'queued', %s)
            """,
            (task_id, user_id, json.dumps(state, ensure_ascii=False)),
        )
        s.commit()

    eventlet.spawn(_run, task_id)
    return jsonify({"task_id": task_id}), 202


def _run(task_id):
    runner = CopywritingTranslateRunner(task_id)
    try:
        runner.start()
    except Exception:
        pass  # 状态已在 runner 内标记 error
```

- [ ] **Step 4: 在主 app.py 注册 blueprint**

```python
# 在 web/app.py 或 web/__init__.py 里:
from web.routes.copywriting_translate import bp as copywriting_translate_bp
app.register_blueprint(copywriting_translate_bp)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
pytest tests/test_copywriting_translate_routes.py -v
```

Expected: PASS.

- [ ] **Step 6: 提交**

```bash
git add web/routes/copywriting_translate.py web/app.py tests/test_copywriting_translate_routes.py
git commit -m "feat(copywriting-translate): 路由入口与 eventlet 派发"
```

---

### Task 9: copywriting_translate SocketIO 进度推送

**Files:**
- Modify: `appcore/copywriting_translate_runtime.py`
- Modify: `appcore/events.py`(如有)加事件常量

- [ ] **Step 1: 探测现有事件常量位置**

```bash
grep -n "EVT_\|EVENT_" appcore/events.py | head -10
```

- [ ] **Step 2: 添加事件常量**

在 `appcore/events.py` 加:
```python
EVT_CT_PROGRESS = "copywriting_translate_progress"
```

- [ ] **Step 3: Runner 在 start/done/error 时发布事件**

Modify `appcore/copywriting_translate_runtime.py::CopywritingTranslateRunner.start()`:

```python
from appcore.events import EventBus, EVT_CT_PROGRESS


# 在 start 方法内加:
def start(self):
    EventBus.publish(EVT_CT_PROGRESS, {
        "task_id": self.task_id, "parent_task_id": self.state.get("parent_task_id"),
        "status": "running",
    })
    task_state.set_step(self.task_id, "translate", "running")
    try:
        # ... (原有逻辑)
        EventBus.publish(EVT_CT_PROGRESS, {
            "task_id": self.task_id, "parent_task_id": self.state.get("parent_task_id"),
            "status": "done", "tokens": tokens,
        })
    except Exception as e:
        EventBus.publish(EVT_CT_PROGRESS, {
            "task_id": self.task_id, "parent_task_id": self.state.get("parent_task_id"),
            "status": "error", "error": str(e),
        })
        raise
```

- [ ] **Step 4: 写 SocketIO 订阅**

找到项目里现有的事件到 SocketIO 桥接代码(通常在 `web/socketio_bridge.py` 或 `web/app.py`),追加:

```python
from appcore.events import EVT_CT_PROGRESS

def _on_ct_progress(payload):
    socketio.emit("copywriting_translate_progress", payload, namespace="/")

EventBus.subscribe(EVT_CT_PROGRESS, _on_ct_progress)
```

- [ ] **Step 5: 验证**

```bash
pytest tests/test_copywriting_translate_runtime.py -v
```

Expected: 全部 PASS(Runner 多了事件发布,不影响核心测试)。

- [ ] **Step 6: 提交**

```bash
git add appcore/events.py appcore/copywriting_translate_runtime.py web/app.py
git commit -m "feat(copywriting-translate): SocketIO 进度推送"
```

---

### Task 10: copywriting_translate 端到端手工验证

- [ ] **Step 1: 启动应用**

```bash
python -m web.app  # 或项目的启动命令,参考 README
```

- [ ] **Step 2: 手动创建英文源文案**

通过 UI 或 SQL:
```sql
INSERT INTO media_copywritings (id, user_id, product_id, lang, text)
VALUES ('copy_en_test', 'your_user', 'product_test', 'en', 'Welcome to our amazing product');
```

- [ ] **Step 3: curl 触发翻译**

```bash
curl -X POST http://localhost:5000/api/copywriting-translate/start \
  -H "Content-Type: application/json" \
  -H "Cookie: <你的登录 cookie>" \
  -d '{"source_copy_id":"copy_en_test","target_lang":"de","parent_task_id":null}'
```

Expected: `{"task_id": "..."}`,HTTP 202.

- [ ] **Step 4: 验证数据库**

```sql
SELECT id, lang, text, source_ref_id, auto_translated
FROM media_copywritings WHERE product_id='product_test';
```

Expected: 有一条 `lang='de'`,`text` 是德语,`source_ref_id='copy_en_test'`,`auto_translated=1`。

- [ ] **Step 5: 验证状态**

```sql
SELECT type, status, state_json FROM projects WHERE type='copywriting_translate' ORDER BY created_at DESC LIMIT 1;
```

Expected: `status='done'`,`state_json` 里 `tokens_used > 0`。

**🎯 Phase 2 里程碑**:能跑通"一条英文文案 → 一条德语文案"的完整链路,入库 + 关联标记 + 状态。

---

## Phase 3 · 费用预估器

### Task 11: estimator 骨架与文案预估

**Files:**
- Create: `appcore/bulk_translate_estimator.py`
- Test: `tests/test_bulk_translate_estimator.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_bulk_translate_estimator.py`:

```python
"""bulk_translate 费用预估器测试。"""
from appcore.bulk_translate_estimator import estimate


def test_copy_only_estimate(db, test_user):
    pid = _create_product(db, test_user)
    _insert_copy(db, test_user, pid, "en", "Welcome to our product")  # 21 chars
    _insert_copy(db, test_user, pid, "en", "Easy to use, highly efficient")  # 29 chars

    result = estimate(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr"],
        content_types=["copy"],
        force_retranslate=False,
    )
    # 2 条 × 50 chars 平均 × 1.3 tokens/char × 1.5 扩展 × 2 语种 ≈ 390 tokens
    assert 200 <= result["copy_tokens"] <= 600
    assert result["image_count"] == 0
    assert result["video_minutes"] == 0
    assert result["estimated_cost_cny"] > 0


def test_video_only_estimate_skips_non_de_fr(db, test_user):
    pid = _create_product(db, test_user)
    _insert_video(db, test_user, pid, lang="en", duration_seconds=120)

    result = estimate(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr", "es", "it"],
        content_types=["video"],
        force_retranslate=False,
    )
    # 视频只对 de/fr 计:1 个英文视频 × 2 分钟 × 2 语种 = 4 分钟
    assert result["video_minutes"] == pytest.approx(4.0, rel=0.01)


def test_skip_already_translated(db, test_user):
    import pytest
    pid = _create_product(db, test_user)
    en_id = _insert_copy(db, test_user, pid, "en", "Welcome")
    _insert_copy(db, test_user, pid, "de", "Willkommen")  # 已翻译

    result = estimate(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr"],
        content_types=["copy"],
        force_retranslate=False,
    )
    # 德语已存在,只算法语 1 条
    assert result["skipped"]["copy"] == 1
    assert result["copy_tokens"] > 0


def test_force_retranslate_counts_all(db, test_user):
    pid = _create_product(db, test_user)
    _insert_copy(db, test_user, pid, "en", "Welcome")
    _insert_copy(db, test_user, pid, "de", "Willkommen")

    result = estimate(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr"],
        content_types=["copy"],
        force_retranslate=True,
    )
    assert result["skipped"]["copy"] == 0
```

(辅助函数 `_create_product`, `_insert_copy`, `_insert_video` 参考 Task 7 的模式。)

- [ ] **Step 2: 运行测试确认失败**

```bash
pytest tests/test_bulk_translate_estimator.py -v
```

Expected: ImportError.

- [ ] **Step 3: 实现预估**

创建 `appcore/bulk_translate_estimator.py`:

```python
"""bulk_translate 费用与资源预估。

精度目标:±20% 以内,用于二次确认弹窗给用户心理预期。
"""
from appcore.db import get_db_session
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS

# 单价(本期硬编码,未来可迁移到 settings)
COST_PER_1K_TOKENS_CNY = 0.60
COST_PER_IMAGE_CNY = 0.18
COST_PER_VIDEO_MINUTE_CNY = 0.95

# 估算系数
CHARS_TO_TOKENS = 1.3
TRANSLATION_EXPANSION = 1.5  # 英译德/法平均文字扩展


def estimate(user_id, product_id, target_langs, content_types, force_retranslate):
    skipped = {"copy": 0, "cover": 0, "detail": 0, "video": 0}

    copy_tokens = _estimate_copy(product_id, target_langs, force_retranslate, skipped) \
        if "copy" in content_types else 0

    image_count = 0
    if "detail" in content_types:
        image_count += _estimate_images(
            "media_product_detail_images", product_id,
            target_langs, force_retranslate, skipped, key="detail",
        )
    if "cover" in content_types:
        image_count += _estimate_images(
            "media_product_covers", product_id,
            target_langs, force_retranslate, skipped, key="cover",
        )

    video_minutes = _estimate_video(
        product_id, target_langs, force_retranslate, skipped,
    ) if "video" in content_types else 0

    copy_cny = (copy_tokens / 1000) * COST_PER_1K_TOKENS_CNY
    image_cny = image_count * COST_PER_IMAGE_CNY
    video_cny = video_minutes * COST_PER_VIDEO_MINUTE_CNY
    total = round(copy_cny + image_cny + video_cny, 2)

    return {
        "copy_tokens": int(copy_tokens),
        "image_count": int(image_count),
        "video_minutes": round(video_minutes, 2),
        "skipped": skipped,
        "estimated_cost_cny": total,
        "breakdown": {
            "copy_cny": round(copy_cny, 2),
            "image_cny": round(image_cny, 2),
            "video_cny": round(video_cny, 2),
        },
    }


def _estimate_copy(product_id, target_langs, force, skipped):
    with get_db_session() as s:
        rows = s.execute(
            "SELECT id, CHAR_LENGTH(text) FROM media_copywritings "
            "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL",
            (product_id,),
        ).fetchall()
    if not rows:
        return 0

    tokens = 0
    for src_id, char_len in rows:
        for lang in target_langs:
            if not force and _translation_exists_copy(product_id, lang, src_id):
                skipped["copy"] += 1
                continue
            tokens += char_len * CHARS_TO_TOKENS * TRANSLATION_EXPANSION
    return tokens


def _translation_exists_copy(product_id, lang, source_ref_id):
    with get_db_session() as s:
        row = s.execute(
            "SELECT 1 FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s AND source_ref_id=%s AND deleted_at IS NULL LIMIT 1",
            (product_id, lang, source_ref_id),
        ).first()
        return row is not None


def _estimate_images(table, product_id, target_langs, force, skipped, key):
    with get_db_session() as s:
        rows = s.execute(
            f"SELECT id FROM {table} WHERE product_id=%s AND lang='en' AND deleted_at IS NULL",
            (product_id,),
        ).fetchall()
    if not rows:
        return 0

    count = 0
    for (src_id,) in rows:
        for lang in target_langs:
            if not force and _translation_exists(table, product_id, lang, src_id):
                skipped[key] += 1
                continue
            count += 1
    return count


def _translation_exists(table, product_id, lang, source_ref_id):
    with get_db_session() as s:
        row = s.execute(
            f"SELECT 1 FROM {table} "
            f"WHERE product_id=%s AND lang=%s AND source_ref_id=%s AND deleted_at IS NULL LIMIT 1",
            (product_id, lang, source_ref_id),
        ).first()
        return row is not None


def _estimate_video(product_id, target_langs, force, skipped):
    with get_db_session() as s:
        rows = s.execute(
            "SELECT id, duration_seconds FROM media_items "
            "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL",
            (product_id,),
        ).fetchall()
    if not rows:
        return 0

    minutes = 0.0
    for src_id, dur_sec in rows:
        dur_min = (dur_sec or 0) / 60.0
        for lang in target_langs:
            # 仅 de/fr 算入
            if lang not in VIDEO_SUPPORTED_LANGS:
                skipped["video"] += 1
                continue
            if not force and _translation_exists("media_items", product_id, lang, src_id):
                skipped["video"] += 1
                continue
            minutes += dur_min
    return minutes
```

- [ ] **Step 4: 运行测试确认通过**

```bash
pytest tests/test_bulk_translate_estimator.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: 提交**

```bash
git add appcore/bulk_translate_estimator.py tests/test_bulk_translate_estimator.py
git commit -m "feat(bulk-translate): 费用与资源预估算法(文案/图片/视频)"
```

---

### Task 12: estimate API 端点

**Files:**
- Create: `web/routes/bulk_translate.py`
- Modify: `web/app.py`
- Test: `tests/test_bulk_translate_routes.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_bulk_translate_routes.py`:

```python
def test_estimate_endpoint(client, test_user_auth, test_product_with_en_copy):
    resp = client.post(
        "/api/bulk-translate/estimate",
        json={
            "product_id": test_product_with_en_copy,
            "target_langs": ["de", "fr"],
            "content_types": ["copy"],
            "force_retranslate": False,
        },
        headers=test_user_auth,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "copy_tokens" in data
    assert "estimated_cost_cny" in data
    assert data["copy_tokens"] > 0
```

- [ ] **Step 2: 实现 estimate endpoint**

创建 `web/routes/bulk_translate.py`:

```python
"""bulk_translate 整体 API 套件。后续 Task 会追加其他端点。"""
from flask import Blueprint, request, jsonify, g

from appcore.bulk_translate_estimator import estimate as do_estimate

bp = Blueprint("bulk_translate", __name__, url_prefix="/api/bulk-translate")


@bp.post("/estimate")
def estimate_endpoint():
    user_id = g.current_user_id
    payload = request.get_json(force=True)
    required = ["product_id", "target_langs", "content_types"]
    for k in required:
        if k not in payload:
            return jsonify({"error": f"{k} 必填"}), 400

    result = do_estimate(
        user_id=user_id,
        product_id=payload["product_id"],
        target_langs=payload["target_langs"],
        content_types=payload["content_types"],
        force_retranslate=payload.get("force_retranslate", False),
    )
    return jsonify(result), 200
```

- [ ] **Step 3: 注册 blueprint**

```python
# web/app.py
from web.routes.bulk_translate import bp as bulk_translate_bp
app.register_blueprint(bulk_translate_bp)
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_bulk_translate_routes.py::test_estimate_endpoint -v
```

Expected: PASS.

- [ ] **Step 5: 提交**

```bash
git add web/routes/bulk_translate.py web/app.py tests/test_bulk_translate_routes.py
git commit -m "feat(bulk-translate): POST /api/bulk-translate/estimate 端点"
```

---

### Task 13: 视频翻译参数 CRUD API

**Files:**
- Create: `web/routes/video_translate_profile.py`
- Modify: `web/app.py`
- Test: `tests/test_video_translate_profile_routes.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_video_translate_profile_routes.py
def test_get_profile_returns_defaults(client, test_user_auth):
    resp = client.get(
        "/api/video-translate-profile?product_id=p1&lang=de",
        headers=test_user_auth,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["subtitle_font"] == "Noto Sans"


def test_put_profile_persists(client, test_user_auth):
    resp = client.put(
        "/api/video-translate-profile",
        json={
            "product_id": "p1", "lang": "de",
            "params": {"subtitle_size": 20},
        },
        headers=test_user_auth,
    )
    assert resp.status_code == 200

    resp2 = client.get(
        "/api/video-translate-profile?product_id=p1&lang=de",
        headers=test_user_auth,
    )
    assert resp2.get_json()["subtitle_size"] == 20
```

- [ ] **Step 2: 实现路由**

创建 `web/routes/video_translate_profile.py`:

```python
from flask import Blueprint, request, jsonify, g
from appcore.video_translate_defaults import load_effective_params, save_profile

bp = Blueprint("video_translate_profile", __name__, url_prefix="/api/video-translate-profile")


@bp.get("")
def get_profile():
    user_id = g.current_user_id
    product_id = request.args.get("product_id") or None
    lang = request.args.get("lang") or None
    return jsonify(load_effective_params(user_id, product_id, lang))


@bp.put("")
def put_profile():
    user_id = g.current_user_id
    payload = request.get_json(force=True)
    product_id = payload.get("product_id")  # 可 None
    lang = payload.get("lang")               # 可 None
    params = payload.get("params", {})
    if not isinstance(params, dict) or not params:
        return jsonify({"error": "params 必填且为 dict"}), 400
    save_profile(user_id, product_id, lang, params)
    return jsonify({"ok": True}), 200
```

- [ ] **Step 3: 注册 blueprint**

```python
# web/app.py
from web.routes.video_translate_profile import bp as video_translate_profile_bp
app.register_blueprint(video_translate_profile_bp)
```

- [ ] **Step 4: 运行测试**

```bash
pytest tests/test_video_translate_profile_routes.py -v
```

Expected: PASS.

- [ ] **Step 5: 提交**

```bash
git add web/routes/video_translate_profile.py web/app.py tests/test_video_translate_profile_routes.py
git commit -m "feat(bulk-translate): 视频翻译参数 GET/PUT 端点"
```

---

### Task 14: estimate 集成手工验证

- [ ] **Step 1: 准备测试数据**

```sql
INSERT INTO media_products (id, user_id, name) VALUES ('p_test', 'u_test', '测试产品');
INSERT INTO media_copywritings (id, user_id, product_id, lang, text) VALUES
  ('c1', 'u_test', 'p_test', 'en', 'Welcome to our product'),
  ('c2', 'u_test', 'p_test', 'en', 'Easy to use');
INSERT INTO media_items (id, user_id, product_id, lang, duration_seconds) VALUES
  ('v1', 'u_test', 'p_test', 'en', 120);
```

- [ ] **Step 2: curl 调用**

```bash
curl -X POST http://localhost:5000/api/bulk-translate/estimate \
  -H "Content-Type: application/json" \
  -H "Cookie: <session>" \
  -d '{"product_id":"p_test","target_langs":["de","fr","es"],"content_types":["copy","video"],"force_retranslate":false}'
```

Expected:
- `copy_tokens` 在 100-300 之间
- `video_minutes` ≈ 4(2 分钟 × de/fr 2 语种,es 跳过)
- `skipped.video` == 1(es)
- `estimated_cost_cny` > 0

**🎯 Phase 3 里程碑**:前端能通过 `/api/bulk-translate/estimate` 拿到预估,能通过 `/api/video-translate-profile` 读写参数。

---

## Phase 4 · 父任务调度器

### Task 15: bulk_translate_plan 生成器

**Files:**
- Create: `appcore/bulk_translate_plan.py`
- Test: `tests/test_bulk_translate_plan.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_bulk_translate_plan.py
from appcore.bulk_translate_plan import generate_plan


def test_plan_copy_only(db, test_user):
    pid = _create_product_with(db, test_user, copies=["Welcome", "Easy"])
    plan = generate_plan(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr"],
        content_types=["copy"],
        force_retranslate=False,
    )
    # 2 条英文 × 2 语种 = 4 项
    copy_items = [p for p in plan if p["kind"] == "copy"]
    assert len(copy_items) == 4
    assert {p["lang"] for p in copy_items} == {"de", "fr"}
    assert all("source_copy_id" in p["ref"] for p in copy_items)


def test_plan_video_only_de_fr_only(db, test_user):
    pid = _create_product_with(db, test_user, videos=["v1"])
    plan = generate_plan(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr", "es", "it"],
        content_types=["video"],
        force_retranslate=False,
    )
    video_items = [p for p in plan if p["kind"] == "video"]
    # 1 个视频 × 2 支持语种 = 2 项(非 de/fr 的不产生 plan 项)
    assert len(video_items) == 2
    assert {p["lang"] for p in video_items} == {"de", "fr"}


def test_plan_image_batch_is_one_task_per_lang(db, test_user):
    pid = _create_product_with(db, test_user, detail_imgs=["i1", "i2", "i3"])
    plan = generate_plan(
        user_id=test_user, product_id=pid,
        target_langs=["de", "fr"],
        content_types=["detail"],
        force_retranslate=False,
    )
    detail_items = [p for p in plan if p["kind"] == "detail"]
    # 3 张图 × 2 语种 = 6 条 plan 项?还是每语种一个 batch 任务?
    # 按设计:每个语种一个 image_translate 子任务处理该语种所有图
    # → 每语种 1 项 = 2 项
    assert len(detail_items) == 2
    assert len(detail_items[0]["ref"]["source_detail_ids"]) == 3


def test_plan_indices_sequential():
    # idx 从 0 开始连续
    ...
```

- [ ] **Step 2: 实现 plan 生成器**

创建 `appcore/bulk_translate_plan.py`:

```python
"""根据产品 + 目标语言 + 内容类型,生成父任务执行计划。

plan 项结构:
    { idx, kind, lang, ref, sub_task_id, status, error, started_at, finished_at }
"""
from appcore.db import get_db_session
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS


def generate_plan(user_id, product_id, target_langs, content_types, force_retranslate):
    plan = []
    idx = 0

    # 1. 文案:每条英文 × 每目标语言 = 一个 plan 项
    if "copy" in content_types:
        with get_db_session() as s:
            rows = s.execute(
                "SELECT id FROM media_copywritings "
                "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL",
                (product_id,),
            ).fetchall()
        for (src_id,) in rows:
            for lang in target_langs:
                plan.append(_new_item(idx, "copy", lang, {"source_copy_id": src_id}))
                idx += 1

    # 2. 详情图:每目标语言 = 一个 plan 项(下挂所有英文详情图 id)
    if "detail" in content_types:
        detail_ids = _list_en_ids(product_id, "media_product_detail_images")
        if detail_ids:
            for lang in target_langs:
                plan.append(_new_item(idx, "detail", lang,
                                       {"source_detail_ids": detail_ids}))
                idx += 1

    # 3. 主图:同详情图,每目标语言一个 plan 项
    if "cover" in content_types:
        cover_ids = _list_en_ids(product_id, "media_product_covers")
        if cover_ids:
            for lang in target_langs:
                plan.append(_new_item(idx, "cover", lang,
                                       {"source_cover_ids": cover_ids}))
                idx += 1

    # 4. 视频:每个视频 × 每 (de|fr) 目标 = 一个 plan 项
    #    其他目标语言直接跳过 plan 生成(不是 skipped,而是"根本不规划")
    if "video" in content_types:
        with get_db_session() as s:
            rows = s.execute(
                "SELECT id FROM media_items "
                "WHERE product_id=%s AND lang='en' AND deleted_at IS NULL",
                (product_id,),
            ).fetchall()
        for (src_id,) in rows:
            for lang in target_langs:
                if lang not in VIDEO_SUPPORTED_LANGS:
                    continue
                plan.append(_new_item(idx, "video", lang,
                                       {"source_item_id": src_id}))
                idx += 1

    return plan


def _new_item(idx, kind, lang, ref):
    return {
        "idx": idx,
        "kind": kind,
        "lang": lang,
        "ref": ref,
        "sub_task_id": None,
        "status": "pending",
        "error": None,
        "started_at": None,
        "finished_at": None,
    }


def _list_en_ids(product_id, table):
    with get_db_session() as s:
        rows = s.execute(
            f"SELECT id FROM {table} WHERE product_id=%s AND lang='en' AND deleted_at IS NULL",
            (product_id,),
        ).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_bulk_translate_plan.py -v
```

Expected: 4 PASS.

- [ ] **Step 4: 提交**

```bash
git add appcore/bulk_translate_plan.py tests/test_bulk_translate_plan.py
git commit -m "feat(bulk-translate): plan 生成器(内容×语言展开)"
```

---

### Task 16: bulk_translate_runtime 状态机骨架

**Files:**
- Create: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_translate_runtime_state.py`

- [ ] **Step 1: 写失败测试 — 创建 planning 状态**

```python
# tests/test_bulk_translate_runtime_state.py
import json
import pytest
from appcore.bulk_translate_runtime import (
    create_bulk_translate_task, start_task, get_task,
)


def test_create_bulk_translate_task_saves_planning(db, test_user, test_product):
    task_id = create_bulk_translate_task(
        user_id=test_user,
        product_id=test_product,
        target_langs=["de", "fr"],
        content_types=["copy"],
        force_retranslate=False,
        video_params={},
        initiator={"user_id": test_user, "user_name": "Test", "ip": "1.2.3.4", "user_agent": "pytest"},
    )
    row = db.execute("SELECT type, status, state_json FROM projects WHERE id=%s",
                     (task_id,)).first()
    assert row[0] == "bulk_translate"
    assert row[1] == "planning"
    state = json.loads(row[2])
    assert state["product_id"] == test_product
    assert state["target_langs"] == ["de", "fr"]
    assert "plan" in state
    assert "cost_tracking" in state
    assert "audit_events" in state
    assert len(state["audit_events"]) == 1
    assert state["audit_events"][0]["action"] == "create"
```

- [ ] **Step 2: 实现 create_bulk_translate_task**

创建 `appcore/bulk_translate_runtime.py`:

```python
"""父任务 bulk_translate 状态机与调度器。

核心铁律:
1. 进程启动不扫描、不对账、不自动恢复
2. 子任务失败 → 父任务立即停(error),绝不跳过
3. 所有恢复/重跑必须用户按按钮触发
"""
import json
import uuid
from datetime import datetime

from appcore.db import get_db_session
from appcore.bulk_translate_plan import generate_plan
from appcore.bulk_translate_estimator import estimate as do_estimate


def create_bulk_translate_task(user_id, product_id, target_langs, content_types,
                               force_retranslate, video_params, initiator):
    """创建父任务,生成 plan,计算预估,初始 status=planning(尚未执行)。"""
    plan = generate_plan(user_id, product_id, target_langs, content_types, force_retranslate)
    cost_estimate = do_estimate(
        user_id, product_id, target_langs, content_types, force_retranslate,
    )

    state = {
        "product_id": product_id,
        "source_lang": "en",
        "target_langs": target_langs,
        "content_types": content_types,
        "force_retranslate": force_retranslate,
        "video_params_snapshot": video_params,
        "initiator": initiator,
        "plan": plan,
        "progress": _compute_progress(plan),
        "current_idx": 0,
        "cancel_requested": False,
        "audit_events": [{
            "ts": datetime.utcnow().isoformat() + "Z",
            "user_id": user_id,
            "action": "create",
            "detail": {
                "target_langs": target_langs,
                "content_types": content_types,
                "force": force_retranslate,
            },
        }],
        "cost_tracking": {
            "estimate": {
                "copy_tokens": cost_estimate["copy_tokens"],
                "image_count": cost_estimate["image_count"],
                "video_minutes": cost_estimate["video_minutes"],
                "estimated_cost_cny": cost_estimate["estimated_cost_cny"],
            },
            "actual": {
                "copy_tokens_used": 0,
                "image_processed": 0,
                "video_minutes_processed": 0,
                "actual_cost_cny": 0.0,
            },
        },
    }

    task_id = str(uuid.uuid4())
    with get_db_session() as s:
        s.execute(
            """
            INSERT INTO projects (id, user_id, type, status, state_json)
            VALUES (%s, %s, 'bulk_translate', 'planning', %s)
            """,
            (task_id, user_id, json.dumps(state, ensure_ascii=False)),
        )
        s.commit()
    return task_id


def get_task(task_id):
    with get_db_session() as s:
        row = s.execute(
            "SELECT id, user_id, status, state_json, created_at, updated_at "
            "FROM projects WHERE id=%s AND type='bulk_translate'",
            (task_id,),
        ).first()
    if not row:
        return None
    return {
        "id": row[0], "user_id": row[1], "status": row[2],
        "state": json.loads(row[3]),
        "created_at": row[4], "updated_at": row[5],
    }


def _compute_progress(plan):
    progress = {"total": len(plan), "done": 0, "running": 0,
                "failed": 0, "skipped": 0, "pending": len(plan)}
    for item in plan:
        st = item["status"]
        if st == "pending":
            continue
        if st in progress:
            progress[st] += 1
        elif st == "error":
            progress["failed"] += 1
        if st != "pending":
            progress["pending"] -= 1
    return progress


def _save_state(task_id, state, status=None):
    with get_db_session() as s:
        if status:
            s.execute(
                "UPDATE projects SET state_json=%s, status=%s WHERE id=%s",
                (json.dumps(state, ensure_ascii=False), status, task_id),
            )
        else:
            s.execute(
                "UPDATE projects SET state_json=%s WHERE id=%s",
                (json.dumps(state, ensure_ascii=False), task_id),
            )
        s.commit()


def _append_audit(state, user_id, action, detail=None):
    state.setdefault("audit_events", []).append({
        "ts": datetime.utcnow().isoformat() + "Z",
        "user_id": user_id,
        "action": action,
        "detail": detail or {},
    })


def start_task(task_id, user_id):
    """把 planning → running,不跑调度器(调度器由路由层在请求返回后 spawn)。"""
    task = get_task(task_id)
    if not task:
        raise ValueError("Task not found")
    if task["status"] != "planning":
        raise ValueError(f"Cannot start task in status={task['status']}")
    state = task["state"]
    _append_audit(state, user_id, "start")
    _save_state(task_id, state, status="running")
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_bulk_translate_runtime_state.py::test_create_bulk_translate_task_saves_planning -v
```

Expected: PASS.

- [ ] **Step 4: 提交**

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_runtime_state.py
git commit -m "feat(bulk-translate): create_bulk_translate_task planning 状态"
```

---

### Task 17: 调度器主循环(串行串行)

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_translate_scheduler.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_bulk_translate_scheduler.py
from unittest.mock import patch, MagicMock
from appcore.bulk_translate_runtime import (
    create_bulk_translate_task, start_task, run_scheduler, get_task,
)


def test_scheduler_runs_all_items_serially(db, test_user, test_product):
    """3 个 plan 项全部 done 后父任务 done。"""
    task_id = create_bulk_translate_task(
        user_id=test_user, product_id=test_product,
        target_langs=["de"], content_types=["copy"],
        force_retranslate=False, video_params={},
        initiator={"user_id": test_user, "user_name": "T", "ip": "x", "user_agent": "y"},
    )
    # 手动填 3 个 plan 项
    with patch("appcore.bulk_translate_runtime._dispatch_sub_task") as disp:
        disp.side_effect = _mock_sub_done
        start_task(task_id, test_user)
        run_scheduler(task_id)

    final = get_task(task_id)
    assert final["status"] == "done"
    for item in final["state"]["plan"]:
        assert item["status"] == "done"


def test_scheduler_stops_on_first_failure(db, test_user, test_product):
    task_id = create_bulk_translate_task(...)  # 3 项计划
    with patch("appcore.bulk_translate_runtime._dispatch_sub_task") as disp:
        # 第 1 项 ok,第 2 项 fail,第 3 项不应该被调用
        disp.side_effect = [_mock_sub_done("sub1"), _mock_sub_error("sub2")]
        start_task(task_id, test_user)
        run_scheduler(task_id)

    final = get_task(task_id)
    assert final["status"] == "error"
    assert final["state"]["plan"][0]["status"] == "done"
    assert final["state"]["plan"][1]["status"] == "error"
    assert final["state"]["plan"][2]["status"] == "pending"  # 未执行
    # 铁律 2 验证:disp 只被调用 2 次而非 3 次
    assert disp.call_count == 2


def test_scheduler_skips_video_for_non_de_fr(db, test_user, test_product):
    """验证视频×非 de/fr 语言项被 skipped 而非跑。"""
    # ...

def _mock_sub_done(sub_id="sub_xxx", tokens=10):
    m = MagicMock()
    m.status = "done"
    m.sub_task_id = sub_id
    m.tokens_used = tokens
    return m


def _mock_sub_error(sub_id="sub_xxx", error="LLM timeout"):
    m = MagicMock()
    m.status = "error"
    m.sub_task_id = sub_id
    m.error = error
    return m
```

- [ ] **Step 2: 实现调度器**

在 `appcore/bulk_translate_runtime.py` 追加:

```python
from appcore.video_translate_defaults import VIDEO_SUPPORTED_LANGS


class SubTaskResult:
    def __init__(self, sub_task_id, status, error=None, tokens_used=0,
                 image_count=0, video_minutes=0):
        self.sub_task_id = sub_task_id
        self.status = status
        self.error = error
        self.tokens_used = tokens_used
        self.image_count = image_count
        self.video_minutes = video_minutes


def run_scheduler(task_id):
    """主调度循环:串行跑 plan 项,失败即停。"""
    while True:
        task = get_task(task_id)
        if not task:
            return
        state = task["state"]

        if state.get("cancel_requested"):
            _save_state(task_id, state, status="cancelled")
            return
        if task["status"] in ("paused", "error", "done", "cancelled"):
            return

        next_item = _find_next_pending(state["plan"])
        if next_item is None:
            state["progress"] = _compute_progress(state["plan"])
            _save_state(task_id, state, status="done")
            return

        # 检查视频是否跳过(非 de/fr 语言)
        if next_item["kind"] == "video" and next_item["lang"] not in VIDEO_SUPPORTED_LANGS:
            _mark_item_skipped(state, next_item, reason="video_lang_not_supported")
            _save_state(task_id, state)
            continue

        # 检查已存在且未选强制重翻
        if not state["force_retranslate"] and _translation_exists_for_item(next_item):
            _mark_item_skipped(state, next_item, reason="already_exists")
            _save_state(task_id, state)
            continue

        # 标 running,派发子任务
        _mark_item_running(state, next_item)
        _save_state(task_id, state)
        try:
            result = _dispatch_sub_task(task_id, next_item, state)
        except Exception as e:
            result = SubTaskResult(sub_task_id=None, status="error", error=str(e))

        if result.status == "done":
            _mark_item_done(state, next_item, result)
            _roll_up_cost(state, result)
            state["progress"] = _compute_progress(state["plan"])
            _save_state(task_id, state)
        else:
            _mark_item_error(state, next_item, result)
            state["progress"] = _compute_progress(state["plan"])
            _save_state(task_id, state, status="error")  # 铁律 2:立即停
            return


def _find_next_pending(plan):
    for item in plan:
        if item["status"] == "pending":
            return item
    return None


def _mark_item_skipped(state, item, reason):
    item["status"] = "skipped"
    item["error"] = reason
    item["finished_at"] = datetime.utcnow().isoformat() + "Z"


def _mark_item_running(state, item):
    item["status"] = "running"
    item["started_at"] = datetime.utcnow().isoformat() + "Z"


def _mark_item_done(state, item, result):
    item["status"] = "done"
    item["sub_task_id"] = result.sub_task_id
    item["finished_at"] = datetime.utcnow().isoformat() + "Z"


def _mark_item_error(state, item, result):
    item["status"] = "error"
    item["error"] = result.error
    item["sub_task_id"] = result.sub_task_id
    item["finished_at"] = datetime.utcnow().isoformat() + "Z"


def _roll_up_cost(state, result):
    actual = state["cost_tracking"]["actual"]
    actual["copy_tokens_used"] += result.tokens_used
    actual["image_processed"] += result.image_count
    actual["video_minutes_processed"] += result.video_minutes
    # 简化:基于单价重算
    from appcore.bulk_translate_estimator import (
        COST_PER_1K_TOKENS_CNY, COST_PER_IMAGE_CNY, COST_PER_VIDEO_MINUTE_CNY,
    )
    actual["actual_cost_cny"] = round(
        (actual["copy_tokens_used"] / 1000) * COST_PER_1K_TOKENS_CNY
        + actual["image_processed"] * COST_PER_IMAGE_CNY
        + actual["video_minutes_processed"] * COST_PER_VIDEO_MINUTE_CNY,
        2,
    )


def _dispatch_sub_task(task_id, item, parent_state):
    """派发真实子任务并同步等待完成。(Task 18 实现不同 kind 分派)"""
    raise NotImplementedError("Implemented in Task 18")


def _translation_exists_for_item(item):
    """查库判断该 plan 项的目标已有译本。(Task 18 实现)"""
    raise NotImplementedError("Implemented in Task 18")
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_bulk_translate_scheduler.py -v
```

Expected:测试调度器主循环流程,派发函数用 mock 替代,至少"skip 非 de/fr 视频"一条应该直接 PASS。`_dispatch_sub_task` 会在 Task 18 实现,目前测试打桩。

- [ ] **Step 4: 提交**

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_scheduler.py
git commit -m "feat(bulk-translate): 调度器主循环 + 串行 + 失败即停"
```

---

### Task 18: 子任务派发器(3 种 kind)

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: 在 `tests/test_bulk_translate_scheduler.py` 追加

- [ ] **Step 1: 写 3 种 kind 的派发测试**

```python
def test_dispatch_copy_creates_copywriting_translate_sub_task(db, test_user, test_product):
    ...

def test_dispatch_detail_creates_image_translate_batch(db, test_user, test_product):
    ...

def test_dispatch_video_creates_translate_lab(db, test_user, test_product):
    ...
```

- [ ] **Step 2: 实现 `_dispatch_sub_task` 与 `_translation_exists_for_item`**

```python
def _dispatch_sub_task(task_id, item, parent_state):
    kind = item["kind"]
    lang = item["lang"]
    product_id = parent_state["product_id"]
    user_id = parent_state["initiator"]["user_id"]
    if kind == "copy":
        return _dispatch_copy(task_id, user_id, product_id, lang, item)
    if kind in ("detail", "cover"):
        return _dispatch_image_batch(task_id, user_id, product_id, lang, item)
    if kind == "video":
        return _dispatch_video(task_id, user_id, product_id, lang, item, parent_state)
    raise ValueError(f"Unknown kind: {kind}")


def _dispatch_copy(parent_id, user_id, product_id, lang, item):
    import json
    from appcore.copywriting_translate_runtime import CopywritingTranslateRunner
    sub_id = str(uuid.uuid4())
    with get_db_session() as s:
        s.execute(
            """
            INSERT INTO projects (id, user_id, type, status, state_json)
            VALUES (%s, %s, 'copywriting_translate', 'queued', %s)
            """,
            (sub_id, user_id, json.dumps({
                "product_id": product_id,
                "source_lang": "en",
                "target_lang": lang,
                "source_copy_id": item["ref"]["source_copy_id"],
                "parent_task_id": parent_id,
            }, ensure_ascii=False)),
        )
        s.commit()
    try:
        runner = CopywritingTranslateRunner(sub_id)
        runner.start()  # 同步阻塞(父任务调度器本身在 eventlet 绿色线程里)
    except Exception as e:
        return SubTaskResult(sub_id, status="error", error=str(e))
    # 加载子任务结果
    sub = _load_sub_task(sub_id)
    return SubTaskResult(
        sub_id, status=sub["status"],
        tokens_used=sub["state"].get("tokens_used", 0),
    )


def _dispatch_image_batch(parent_id, user_id, product_id, lang, item):
    """创建一个 image_translate 子任务,输入该产品该语言全部英文图 id 列表。"""
    import json
    sub_id = str(uuid.uuid4())
    source_ids = item["ref"].get("source_detail_ids") or item["ref"].get("source_cover_ids") or []
    with get_db_session() as s:
        s.execute(
            """
            INSERT INTO projects (id, user_id, type, status, state_json)
            VALUES (%s, %s, 'image_translate', 'queued', %s)
            """,
            (sub_id, user_id, json.dumps({
                "product_id": product_id,
                "target_language": lang,
                "source_ids": source_ids,
                "preset": "detail" if item["kind"] == "detail" else "cover",
                "parent_task_id": parent_id,
            }, ensure_ascii=False)),
        )
        s.commit()
    from appcore.image_translate_runtime import ImageTranslateRuntime
    try:
        ImageTranslateRuntime(sub_id).start()
    except Exception as e:
        return SubTaskResult(sub_id, status="error", error=str(e))
    sub = _load_sub_task(sub_id)
    return SubTaskResult(
        sub_id, status=sub["status"],
        image_count=len(source_ids),
    )


def _dispatch_video(parent_id, user_id, product_id, lang, item, parent_state):
    """创建 translate_lab 子任务。目标语言本期仅 de/fr。"""
    import json
    sub_id = str(uuid.uuid4())
    video_params = parent_state.get("video_params_snapshot", {})
    with get_db_session() as s:
        s.execute(
            """
            INSERT INTO projects (id, user_id, type, status, state_json)
            VALUES (%s, %s, 'translate_lab', 'queued', %s)
            """,
            (sub_id, user_id, json.dumps({
                "product_id": product_id,
                "source_item_id": item["ref"]["source_item_id"],
                "source_language": "en",
                "target_language": lang,
                **video_params,   # 铺平字幕/TTS/编码参数
                "parent_task_id": parent_id,
            }, ensure_ascii=False)),
        )
        s.commit()
    # 复用现有视频翻译 runner(使用 runtime_de.py 或 runtime_fr.py)
    if lang == "de":
        from appcore.runtime_de import run_de_translation as run_vid
    else:
        from appcore.runtime_fr import run_fr_translation as run_vid
    try:
        run_vid(sub_id)
    except Exception as e:
        return SubTaskResult(sub_id, status="error", error=str(e))
    sub = _load_sub_task(sub_id)
    dur_minutes = sub["state"].get("video_duration_seconds", 0) / 60.0
    return SubTaskResult(
        sub_id, status=sub["status"],
        video_minutes=dur_minutes,
    )


def _translation_exists_for_item(item):
    """查库是否已有译本。"""
    kind = item["kind"]
    lang = item["lang"]
    if kind == "copy":
        src = item["ref"]["source_copy_id"]
        with get_db_session() as s:
            row = s.execute(
                "SELECT 1 FROM media_copywritings WHERE source_ref_id=%s AND lang=%s AND deleted_at IS NULL LIMIT 1",
                (src, lang),
            ).first()
            return row is not None
    if kind == "video":
        src = item["ref"]["source_item_id"]
        with get_db_session() as s:
            row = s.execute(
                "SELECT 1 FROM media_items WHERE source_ref_id=%s AND lang=%s AND deleted_at IS NULL LIMIT 1",
                (src, lang),
            ).first()
            return row is not None
    if kind == "detail":
        src_ids = item["ref"]["source_detail_ids"]
        if not src_ids:
            return False
        with get_db_session() as s:
            row = s.execute(
                "SELECT 1 FROM media_product_detail_images WHERE source_ref_id IN %s AND lang=%s AND deleted_at IS NULL LIMIT 1",
                (tuple(src_ids), lang),
            ).first()
            return row is not None
    if kind == "cover":
        src_ids = item["ref"]["source_cover_ids"]
        if not src_ids:
            return False
        with get_db_session() as s:
            row = s.execute(
                "SELECT 1 FROM media_product_covers WHERE source_ref_id IN %s AND lang=%s AND deleted_at IS NULL LIMIT 1",
                (tuple(src_ids), lang),
            ).first()
            return row is not None
    return False


def _load_sub_task(sub_id):
    with get_db_session() as s:
        row = s.execute(
            "SELECT status, state_json FROM projects WHERE id=%s",
            (sub_id,),
        ).first()
    return {"status": row[0], "state": json.loads(row[1])}
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_bulk_translate_scheduler.py -v
```

Expected: 6 PASS。

- [ ] **Step 4: 提交**

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_scheduler.py
git commit -m "feat(bulk-translate): 子任务派发器(copy/image/video 三类)"
```

---

### Task 19: SocketIO 进度推送

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Modify: `appcore/events.py`
- Modify: `web/app.py`(订阅端)

- [ ] **Step 1: 新增事件常量**

`appcore/events.py`:
```python
EVT_BULK_TRANSLATE_PROGRESS = "bulk_translate_progress"
EVT_BULK_TRANSLATE_DONE = "bulk_translate_done"
```

- [ ] **Step 2: 调度器关键节点发布事件**

在 `_save_state` 之后追加:

```python
def _emit_progress(task_id, state, status):
    from appcore.events import EventBus, EVT_BULK_TRANSLATE_PROGRESS, EVT_BULK_TRANSLATE_DONE
    payload = {
        "task_id": task_id, "status": status,
        "progress": state["progress"],
        "current_idx": state["current_idx"],
        "cost_actual": state["cost_tracking"]["actual"],
    }
    if status == "done":
        EventBus.publish(EVT_BULK_TRANSLATE_DONE, payload)
    else:
        EventBus.publish(EVT_BULK_TRANSLATE_PROGRESS, payload)
```

在 `run_scheduler` 的每次 `_save_state(...)` 后调用 `_emit_progress(task_id, state, current_status)`。

- [ ] **Step 3: 在 web/app.py 做 SocketIO 桥接**

```python
from appcore.events import EventBus, EVT_BULK_TRANSLATE_PROGRESS, EVT_BULK_TRANSLATE_DONE

def _on_bulk_progress(p):
    socketio.emit("bulk_translate_progress", p, namespace="/")

def _on_bulk_done(p):
    socketio.emit("bulk_translate_done", p, namespace="/")

EventBus.subscribe(EVT_BULK_TRANSLATE_PROGRESS, _on_bulk_progress)
EventBus.subscribe(EVT_BULK_TRANSLATE_DONE, _on_bulk_done)
```

- [ ] **Step 4: 提交**

```bash
git add appcore/events.py appcore/bulk_translate_runtime.py web/app.py
git commit -m "feat(bulk-translate): SocketIO bulk_translate_progress / done 事件"
```

---

### Task 20: 人工恢复路径 — resume / retry-item / retry-failed

**Files:**
- Modify: `appcore/bulk_translate_runtime.py`
- Test: `tests/test_bulk_translate_recovery.py`

- [ ] **Step 1: 写失败测试 — 三条恢复路径**

```python
# tests/test_bulk_translate_recovery.py
def test_resume_after_error_only_reruns_pending(db, test_user, test_product):
    # 构造一个 plan: [done, error, pending]
    task_id = _mk_task_with_plan([
        {"status": "done"}, {"status": "error"}, {"status": "pending"},
    ])
    from appcore.bulk_translate_runtime import resume_task
    resume_task(task_id, user_id=test_user)
    # 对账 + 继续执行 pending,但不动 error
    state = _load_state(task_id)
    assert state["plan"][0]["status"] == "done"
    assert state["plan"][1]["status"] == "error"  # 保持 error
    assert state["plan"][2]["status"] in ("done", "running")


def test_retry_failed_resets_errors_to_pending(db, test_user, test_product):
    task_id = _mk_task_with_plan([
        {"status": "error"}, {"status": "error"}, {"status": "done"},
    ])
    from appcore.bulk_translate_runtime import retry_failed_items
    retry_failed_items(task_id, user_id=test_user)
    state = _load_state(task_id)
    assert state["plan"][0]["status"] in ("pending", "running", "done")
    assert state["plan"][1]["status"] in ("pending", "running", "done")
    assert state["plan"][2]["status"] == "done"
    # 审计
    audit_actions = [e["action"] for e in state["audit_events"]]
    assert "retry_failed" in audit_actions


def test_retry_single_item_resets_to_pending(db, test_user, test_product):
    task_id = _mk_task_with_plan([
        {"status": "done"}, {"status": "error"}, {"status": "done"},
    ])
    from appcore.bulk_translate_runtime import retry_item
    retry_item(task_id, idx=1, user_id=test_user)
    state = _load_state(task_id)
    assert state["plan"][1]["status"] in ("pending", "running", "done")
```

- [ ] **Step 2: 实现三个函数**

追加到 `appcore/bulk_translate_runtime.py`:

```python
def resume_task(task_id, user_id):
    """对账 + 继续执行(只跑 pending,不动 error)。"""
    task = get_task(task_id)
    if not task:
        raise ValueError("Task not found")
    state = task["state"]
    # 对账:把所有 running 项标为 error(因为 running 但进程已丢失)
    _reconcile_running_items(state)
    _append_audit(state, user_id, "resume")
    _save_state(task_id, state, status="running")
    # 在调用方(路由) spawn scheduler


def retry_failed_items(task_id, user_id):
    task = get_task(task_id)
    state = task["state"]
    _reconcile_running_items(state)
    for item in state["plan"]:
        if item["status"] == "error":
            item["status"] = "pending"
            item["error"] = None
    state["progress"] = _compute_progress(state["plan"])
    _append_audit(state, user_id, "retry_failed")
    _save_state(task_id, state, status="running")


def retry_item(task_id, idx, user_id):
    task = get_task(task_id)
    state = task["state"]
    if idx < 0 or idx >= len(state["plan"]):
        raise ValueError("Invalid idx")
    item = state["plan"][idx]
    item["status"] = "pending"
    item["error"] = None
    item["sub_task_id"] = None
    state["progress"] = _compute_progress(state["plan"])
    _append_audit(state, user_id, "retry_item", detail={"idx": idx})
    _save_state(task_id, state, status="running")


def _reconcile_running_items(state):
    """人工触发的对账:把所有 running 项标 error(进程可能已丢失)。绝不自动运行。"""
    for item in state["plan"]:
        if item["status"] == "running":
            item["status"] = "error"
            item["error"] = "Reconciled: process lost"


def pause_task(task_id, user_id):
    task = get_task(task_id)
    state = task["state"]
    _append_audit(state, user_id, "pause")
    _save_state(task_id, state, status="paused")


def cancel_task(task_id, user_id):
    task = get_task(task_id)
    state = task["state"]
    state["cancel_requested"] = True
    _append_audit(state, user_id, "cancel")
    _save_state(task_id, state)
    # 调度器下一个循环会自动 transition 到 cancelled
```

- [ ] **Step 3: 运行测试**

```bash
pytest tests/test_bulk_translate_recovery.py -v
```

Expected: 3 PASS。

- [ ] **Step 4: 提交**

```bash
git add appcore/bulk_translate_runtime.py tests/test_bulk_translate_recovery.py
git commit -m "feat(bulk-translate): 人工恢复三路径(resume/retry-failed/retry-item)"
```

---

### Task 21: 铁律验证 — 进程启动不自动扫描

**Files:**
- Create: `tests/test_bulk_translate_no_auto_recovery.py`

- [ ] **Step 1: 写铁律测试**

```python
"""验证进程启动时不会自动扫描 / 恢复任何 bulk_translate 任务。
这是项目的关键安全铁律,严禁破坏。"""
def test_import_runtime_does_not_auto_scan(db, test_user):
    """人为造一个 running 状态的父任务,导入 runtime 模块后验证它没被动过。"""
    import json
    task_id = _mk_fake_running_task(db, test_user)

    # 重新导入 runtime 模块,模拟进程启动场景
    import importlib
    from appcore import bulk_translate_runtime as mod
    importlib.reload(mod)

    row = db.execute("SELECT status, state_json FROM projects WHERE id=%s",
                     (task_id,)).first()
    assert row[0] == "running"
    state = json.loads(row[1])
    # 所有 running 项依然 running(没被标 error),说明没做对账
    running_items = [i for i in state["plan"] if i["status"] == "running"]
    assert len(running_items) >= 1


def test_task_recovery_module_does_not_include_bulk_translate():
    """task_recovery.py 里不允许出现 bulk_translate 相关的自动恢复逻辑。"""
    import appcore.task_recovery as tr
    source = open(tr.__file__, encoding="utf-8").read()
    # 要么 task_recovery 里压根不处理 bulk_translate,要么显式排除
    # 以显式排除为强制约束:
    forbidden = ["bulk_translate"]
    for f in forbidden:
        assert f not in source or f"# NO_AUTO: {f}" in source, \
            f"task_recovery 中不得包含 {f} 自动恢复逻辑"
```

- [ ] **Step 2: 验证 task_recovery.py 未引用 bulk_translate**

```bash
grep -n "bulk_translate" appcore/task_recovery.py
```

Expected: 无输出,或仅有注释 `# NO_AUTO: bulk_translate`。

- [ ] **Step 3: 运行铁律测试**

```bash
pytest tests/test_bulk_translate_no_auto_recovery.py -v
```

Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add tests/test_bulk_translate_no_auto_recovery.py
git commit -m "test(bulk-translate): 铁律验证 — 绝不自动恢复"
```

---

### Task 22: Phase 4 端到端手工验证

- [ ] **Step 1: 创建小型测试任务(通过 Python REPL)**

```python
from appcore.bulk_translate_runtime import create_bulk_translate_task, start_task, run_scheduler
tid = create_bulk_translate_task(
    user_id="u_test", product_id="p_test",
    target_langs=["de"], content_types=["copy"],
    force_retranslate=False, video_params={},
    initiator={"user_id": "u_test", "user_name": "Test", "ip": "x", "user_agent": "y"},
)
start_task(tid, "u_test")
run_scheduler(tid)
```

- [ ] **Step 2: 验证 DB 状态**

```sql
SELECT status FROM projects WHERE id='<tid>';          -- 期望 done
SELECT lang, auto_translated FROM media_copywritings WHERE product_id='p_test' AND lang='de';
```

**🎯 Phase 4 里程碑**:调度器能端到端串行跑通小型任务,铁律(失败即停、不自动恢复)通过测试验证。

---

## Phase 5 · 父任务 API 套件

### Task 23: create + start + get + list 端点

**Files:**
- Modify: `web/routes/bulk_translate.py`
- Test: 扩展 `tests/test_bulk_translate_routes.py`

- [ ] **Step 1: 写失败测试**

```python
def test_create_endpoint_returns_planning(client, test_user_auth, test_product):
    resp = client.post("/api/bulk-translate/create", json={
        "product_id": test_product, "target_langs": ["de"],
        "content_types": ["copy"], "force_retranslate": False,
        "video_params": {},
    }, headers=test_user_auth)
    assert resp.status_code == 201
    data = resp.get_json()
    assert "task_id" in data
    assert data["status"] == "planning"


def test_start_endpoint_transitions_to_running(client, test_user_auth, test_product):
    # 先 create
    create_resp = client.post("/api/bulk-translate/create", ...)
    task_id = create_resp.get_json()["task_id"]
    # start
    resp = client.post(f"/api/bulk-translate/{task_id}/start", headers=test_user_auth)
    assert resp.status_code == 202


def test_get_endpoint(client, test_user_auth, test_product):
    ...


def test_list_endpoint_supports_status_filter(client, test_user_auth):
    resp = client.get("/api/bulk-translate/list?status=running", headers=test_user_auth)
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), list)
```

- [ ] **Step 2: 实现端点**

在 `web/routes/bulk_translate.py` 追加:

```python
import eventlet
from appcore.bulk_translate_runtime import (
    create_bulk_translate_task, start_task, get_task, run_scheduler,
    pause_task, resume_task, cancel_task, retry_failed_items, retry_item,
)


@bp.post("/create")
def create_endpoint():
    user_id = g.current_user_id
    payload = request.get_json(force=True)
    initiator = {
        "user_id": user_id,
        "user_name": getattr(g, "current_user_name", ""),
        "ip": request.remote_addr,
        "user_agent": request.headers.get("User-Agent", ""),
    }
    task_id = create_bulk_translate_task(
        user_id=user_id,
        product_id=payload["product_id"],
        target_langs=payload["target_langs"],
        content_types=payload["content_types"],
        force_retranslate=payload.get("force_retranslate", False),
        video_params=payload.get("video_params", {}),
        initiator=initiator,
    )
    return jsonify({"task_id": task_id, "status": "planning"}), 201


@bp.post("/<task_id>/start")
def start_endpoint(task_id):
    user_id = g.current_user_id
    start_task(task_id, user_id)
    eventlet.spawn(run_scheduler, task_id)
    return jsonify({"ok": True}), 202


@bp.get("/<task_id>")
def get_endpoint(task_id):
    task = get_task(task_id)
    if not task:
        return jsonify({"error": "Not found"}), 404
    return jsonify(task)


@bp.get("/list")
def list_endpoint():
    user_id = g.current_user_id
    status = request.args.get("status")
    from appcore.db import get_db_session
    where = "user_id=%s AND type='bulk_translate'"
    params = [user_id]
    if status:
        where += " AND status=%s"
        params.append(status)
    with get_db_session() as s:
        rows = s.execute(
            f"SELECT id, status, state_json, created_at, updated_at "
            f"FROM projects WHERE {where} ORDER BY created_at DESC LIMIT 200",
            tuple(params),
        ).fetchall()
    result = []
    for row in rows:
        import json
        state = json.loads(row[2])
        result.append({
            "id": row[0], "status": row[1],
            "product_id": state.get("product_id"),
            "target_langs": state.get("target_langs"),
            "progress": state.get("progress"),
            "cost_estimate": state["cost_tracking"]["estimate"]["estimated_cost_cny"],
            "cost_actual": state["cost_tracking"]["actual"]["actual_cost_cny"],
            "initiator": state.get("initiator"),
            "created_at": row[3].isoformat() if row[3] else None,
        })
    return jsonify(result)
```

- [ ] **Step 3: 运行测试 + 提交**

```bash
pytest tests/test_bulk_translate_routes.py -v
git add web/routes/bulk_translate.py tests/test_bulk_translate_routes.py
git commit -m "feat(bulk-translate): create/start/get/list 端点"
```

---

### Task 24-28: 其余操作端点(pause/resume/cancel/retry-item/retry-failed/audit)

按 Task 23 的模式,每个端点一个 task,每个 task 都包含:写失败测试 → 运行失败 → 实现 → 运行通过 → 提交。

- [ ] **Task 24: POST /api/bulk-translate/<id>/pause**

```python
@bp.post("/<task_id>/pause")
def pause_endpoint(task_id):
    pause_task(task_id, g.current_user_id)
    return jsonify({"ok": True})
```

- [ ] **Task 25: POST /api/bulk-translate/<id>/resume**

```python
@bp.post("/<task_id>/resume")
def resume_endpoint(task_id):
    resume_task(task_id, g.current_user_id)
    eventlet.spawn(run_scheduler, task_id)
    return jsonify({"ok": True}), 202
```

- [ ] **Task 26: POST /api/bulk-translate/<id>/cancel**

```python
@bp.post("/<task_id>/cancel")
def cancel_endpoint(task_id):
    cancel_task(task_id, g.current_user_id)
    return jsonify({"ok": True})
```

- [ ] **Task 27: POST /api/bulk-translate/<id>/retry-item 和 retry-failed**

```python
@bp.post("/<task_id>/retry-item")
def retry_item_endpoint(task_id):
    payload = request.get_json(force=True)
    retry_item(task_id, payload["idx"], g.current_user_id)
    eventlet.spawn(run_scheduler, task_id)
    return jsonify({"ok": True})


@bp.post("/<task_id>/retry-failed")
def retry_failed_endpoint(task_id):
    retry_failed_items(task_id, g.current_user_id)
    eventlet.spawn(run_scheduler, task_id)
    return jsonify({"ok": True})
```

- [ ] **Task 28: GET /api/bulk-translate/<id>/audit**

```python
@bp.get("/<task_id>/audit")
def audit_endpoint(task_id):
    task = get_task(task_id)
    if not task:
        return jsonify({"error": "Not found"}), 404
    return jsonify(task["state"].get("audit_events", []))
```

每个 Task 都:写测试 → 实现 → 运行 → 提交(`git commit -m "feat(bulk-translate): ... 端点"`)。

---

### Task 29-32: 集成测试 + curl 手工验证

- [ ] **Task 29:** 端到端 curl 跑通整个 API 流(create → start → 等完成 → get → 查 audit)

- [ ] **Task 30:** 对每个操作按钮对应的 API 手工验证(pause 真停住、cancel 不跑新子任务、retry-failed 真重跑失败项)

- [ ] **Task 31:** 验证 resume 时正确对账(构造 running 项 + 人为 kill 进程,然后 POST resume,验证 running 被标 error,pending 继续跑)

- [ ] **Task 32:** 验证 audit_events 完整性(所有操作都留痕)

**🎯 Phase 5 里程碑**:整套后端 API 可通过 curl 完成全流程,铁律验证通过。**此时后端可以上线给前端对接。**

---

## Phase 6 · 弹窗 + 右下角气泡 UI

### Task 33: 弹窗组件 HTML 骨架

**Files:**
- Create: `web/templates/_bulk_translate_dialog.html`
- Create: `web/static/bulk_translate.js`
- Create: `web/static/bulk_translate.css`

- [ ] **Step 1: 创建弹窗模板**

```html
<!-- web/templates/_bulk_translate_dialog.html -->
<div id="bulk-translate-dialog" class="bt-dialog hidden" role="dialog" aria-labelledby="bt-dialog-title">
  <div class="bt-dialog__backdrop"></div>
  <div class="bt-dialog__panel">
    <header class="bt-dialog__header">
      <h2 id="bt-dialog-title">🌐 一键从英文翻译 — <span data-product-name></span></h2>
      <button class="bt-dialog__close" aria-label="关闭">×</button>
    </header>
    <div class="bt-dialog__body">
      <section data-section="source-lang">
        <h3>📍 源语言</h3>
        <span class="bt-badge">🇬🇧 英文(固定)</span>
      </section>
      <section data-section="target-langs">
        <h3>▸ 目标语言</h3>
        <div data-target-langs-box></div>
      </section>
      <section data-section="content-types">
        <h3>▸ 翻译内容</h3>
        <label><input type="checkbox" data-content="copy" checked> 商品文案</label>
        <label><input type="checkbox" data-content="cover"> 商品主图</label>
        <label><input type="checkbox" data-content="detail" checked> 商品详情图</label>
        <label><input type="checkbox" data-content="video" checked> 视频素材</label>
        <p class="bt-hint">ℹ️ 视频翻译仅支持 德语 / 法语,其他语言将跳过视频</p>
      </section>
      <section data-section="force">
        <label><input type="checkbox" data-force> 强制重新翻译全部(默认跳过已存在译本)</label>
      </section>
      <section data-section="video-params" class="bt-collapsed">
        <button type="button" data-toggle-video-params>▾ 视频翻译参数</button>
        <div data-video-params-box></div>
      </section>
      <section data-section="estimate" class="bt-estimate">
        <h3>📊 预估消耗</h3>
        <div data-estimate-box>计算中...</div>
      </section>
    </div>
    <footer class="bt-dialog__footer">
      <button class="bt-btn bt-btn--ghost" data-cancel>取消</button>
      <button class="bt-btn bt-btn--primary" data-start>▶ 开始翻译</button>
    </footer>
  </div>
</div>
```

- [ ] **Step 2: 创建 CSS(Ocean Blue 风格)**

```css
/* web/static/bulk_translate.css */
.bt-dialog { position: fixed; inset: 0; z-index: 1000; }
.bt-dialog.hidden { display: none; }
.bt-dialog__backdrop {
  position: absolute; inset: 0;
  background: oklch(22% 0.02 235 / 0.4);
}
.bt-dialog__panel {
  position: relative; margin: 40px auto;
  max-width: 640px; background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  padding: var(--space-6);
}
.bt-dialog__header { display: flex; justify-content: space-between; align-items: center; margin-bottom: var(--space-5); }
.bt-dialog__header h2 { font-size: var(--text-lg); color: var(--fg); }
.bt-dialog__close { background: none; border: 0; font-size: 22px; cursor: pointer; color: var(--fg-muted); }
.bt-dialog__body > section { margin-bottom: var(--space-5); }
.bt-dialog__body h3 { font-size: var(--text-sm); color: var(--fg-muted); margin-bottom: var(--space-2); }
.bt-badge { display: inline-block; padding: 4px 10px; background: var(--accent-subtle); color: var(--accent); border-radius: var(--radius-md); font-size: var(--text-xs); }
.bt-hint { color: var(--fg-subtle); font-size: var(--text-xs); margin-top: var(--space-2); }
.bt-estimate { background: var(--bg-subtle); padding: var(--space-4); border-radius: var(--radius-md); font-family: var(--font-mono); font-size: var(--text-sm); }
.bt-dialog__footer { display: flex; justify-content: flex-end; gap: var(--space-3); border-top: 1px solid var(--border); padding-top: var(--space-4); }
.bt-btn { height: 32px; padding: 0 16px; border-radius: var(--radius); border: 1px solid var(--border-strong); background: var(--bg); color: var(--fg); font-size: var(--text-sm); cursor: pointer; }
.bt-btn--primary { background: var(--accent); color: var(--accent-fg); border-color: var(--accent); }
.bt-btn--primary:hover { background: var(--accent-hover); }
.bt-btn--ghost { background: var(--bg); }
```

- [ ] **Step 3: 创建 JS 骨架**

```javascript
// web/static/bulk_translate.js
(function(){
  'use strict';

  const dialog = document.getElementById('bulk-translate-dialog');
  let currentContext = null;

  function openDialog(context) {
    currentContext = context;
    dialog.querySelector('[data-product-name]').textContent = context.productName;
    _renderTargetLangs(context);
    _renderVideoParams(context);
    _wireHandlers(context);
    _refreshEstimate(context);
    dialog.classList.remove('hidden');
  }

  function closeDialog() {
    dialog.classList.add('hidden');
    currentContext = null;
  }

  function _renderTargetLangs(ctx) {
    const box = dialog.querySelector('[data-target-langs-box]');
    const isSingle = ctx.mode === 'single-lang';
    if (isSingle) {
      box.innerHTML = `<span class="bt-badge">${_flag(ctx.fixedLang)} ${ctx.langName}</span>`;
      return;
    }
    box.innerHTML = ctx.enabledLangs.map(l =>
      `<label><input type="checkbox" data-lang="${l.code}" checked> ${_flag(l.code)} ${l.name}</label>`
    ).join(' ');
  }

  function _renderVideoParams(ctx) {
    const box = dialog.querySelector('[data-video-params-box]');
    box.innerHTML = '<em>(视频参数分档渲染见 Task 34)</em>';
  }

  async function _refreshEstimate(ctx) {
    const body = _collectFormState();
    const resp = await fetch('/api/bulk-translate/estimate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    _renderEstimate(data);
  }

  function _renderEstimate(d) {
    const box = dialog.querySelector('[data-estimate-box]');
    box.innerHTML = `
      <div>文案 tokens: ${d.copy_tokens.toLocaleString()}</div>
      <div>图片张数: ${d.image_count}</div>
      <div>视频分钟: ${d.video_minutes}</div>
      <div>预估费用: <strong style="color:var(--accent)">¥${d.estimated_cost_cny}</strong></div>
    `;
  }

  function _collectFormState() {
    const ctx = currentContext;
    const targetLangs = ctx.mode === 'single-lang'
      ? [ctx.fixedLang]
      : Array.from(dialog.querySelectorAll('[data-lang]:checked')).map(i => i.dataset.lang);
    const contentTypes = Array.from(dialog.querySelectorAll('[data-content]:checked')).map(i => i.dataset.content);
    const force = dialog.querySelector('[data-force]').checked;
    return {
      product_id: ctx.productId,
      target_langs: targetLangs,
      content_types: contentTypes,
      force_retranslate: force,
    };
  }

  function _wireHandlers(ctx) {
    dialog.querySelector('[data-cancel]').onclick = closeDialog;
    dialog.querySelector('.bt-dialog__close').onclick = closeDialog;
    dialog.querySelector('[data-start]').onclick = _onStart;
    dialog.querySelectorAll('[data-lang], [data-content], [data-force]').forEach(el => {
      el.onchange = () => _debounce(_refreshEstimate, 300)(ctx);
    });
  }

  async function _onStart() {
    const body = _collectFormState();
    const estimate = await (await fetch('/api/bulk-translate/estimate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    })).json();
    if (!confirm(`将启动翻译任务,预估费用 ¥${estimate.estimated_cost_cny},确认?`)) return;

    const createResp = await fetch('/api/bulk-translate/create', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({...body, video_params: _collectVideoParams()}),
    });
    const { task_id } = await createResp.json();

    await fetch(`/api/bulk-translate/${task_id}/start`, {method: 'POST'});
    closeDialog();
    window.dispatchEvent(new CustomEvent('bulk-translate:started', {detail: {task_id}}));
  }

  function _collectVideoParams() {
    return {}; // Task 34 完善
  }

  let _timer;
  function _debounce(fn, ms){ return (...a) => { clearTimeout(_timer); _timer = setTimeout(()=>fn(...a), ms); }; }

  function _flag(code) {
    const map = { de: '🇩🇪', fr: '🇫🇷', es: '🇪🇸', it: '🇮🇹', ja: '🇯🇵', pt: '🇵🇹' };
    return map[code] || '🌐';
  }

  window.BulkTranslateDialog = { open: openDialog, close: closeDialog };
})();
```

- [ ] **Step 4: 在基础模板引入**

在 `web/templates/base.html`(或现有全局模板)引入:

```html
{% include '_bulk_translate_dialog.html' %}
<link rel="stylesheet" href="{{ url_for('static', filename='bulk_translate.css') }}">
<script src="{{ url_for('static', filename='bulk_translate.js') }}" defer></script>
```

- [ ] **Step 5: 手工验证**

在浏览器打开任何页面,打开 DevTools Console,执行:
```javascript
BulkTranslateDialog.open({
  mode: 'multi-lang',
  productId: 'p_test',
  productName: '测试产品',
  enabledLangs: [{code:'de',name:'德语'},{code:'fr',name:'法语'}],
});
```

Expected:弹窗出现,目标语言双勾,预估实时计算。

- [ ] **Step 6: 提交**

```bash
git add web/templates/_bulk_translate_dialog.html web/static/bulk_translate.{js,css} web/templates/base.html
git commit -m "feat(bulk-translate): 弹窗组件骨架 + 预估实时更新"
```

---

### Task 34: 视频翻译参数分档展示

**Files:**
- Modify: `web/static/bulk_translate.js`

- [ ] **Step 1: 实现三档分组渲染**

替换 `_renderVideoParams` 与 `_collectVideoParams`:

```javascript
async function _renderVideoParams(ctx) {
  const box = dialog.querySelector('[data-video-params-box]');
  const lang = ctx.mode === 'single-lang' ? ctx.fixedLang : ctx.enabledLangs[0].code;
  const params = await (await fetch(`/api/video-translate-profile?product_id=${ctx.productId}&lang=${lang}`)).json();

  box.innerHTML = `
    <div class="bt-params-tier bt-params--basic">
      <h4>🟢 基础</h4>
      ${_field('字幕字体', 'subtitle_font', 'text', params.subtitle_font)}
      ${_field('字幕大小', 'subtitle_size', 'number', params.subtitle_size)}
      ${_field('字幕位置 Y', 'subtitle_position_y', 'number', params.subtitle_position_y, 0.01)}
      ${_field('字幕颜色', 'subtitle_color', 'color', params.subtitle_color)}
      ${_field('字幕描边色', 'subtitle_stroke_color', 'color', params.subtitle_stroke_color)}
      ${_field('描边宽度', 'subtitle_stroke_width', 'number', params.subtitle_stroke_width)}
      ${_bool('烧录字幕', 'subtitle_burn_in', params.subtitle_burn_in)}
      ${_bool('导出 .srt', 'subtitle_export_srt', params.subtitle_export_srt)}
    </div>
    <button class="bt-params-toggle" data-tier="advanced">🟡 进阶 ▸</button>
    <div class="bt-params-tier bt-params--advanced hidden">
      ${_select('字幕底条', 'subtitle_background', [['none','无'],['dim','半透明黑']], params.subtitle_background)}
      ${_field('TTS 语速', 'tts_speed', 'number', params.tts_speed, 0.1)}
      ${_select('背景音', 'background_audio', [['keep','保留'],['replace','纯配音'],['mute','静音']], params.background_audio)}
      ${_field('最大行宽', 'max_line_width', 'number', params.max_line_width)}
    </div>
    <button class="bt-params-toggle" data-tier="expert">⚪ 高级 ▸</button>
    <div class="bt-params-tier bt-params--expert hidden">
      ${_field('输出分辨率', 'output_resolution', 'text', params.output_resolution)}
      ${_field('输出编码', 'output_codec', 'text', params.output_codec)}
      ${_field('码率 (kbps)', 'output_bitrate_kbps', 'number', params.output_bitrate_kbps)}
    </div>
  `;
  box.querySelectorAll('.bt-params-toggle').forEach(btn => {
    btn.onclick = () => {
      const tier = btn.dataset.tier;
      box.querySelector(`.bt-params--${tier}`).classList.toggle('hidden');
    };
  });
}


function _collectVideoParams() {
  const box = dialog.querySelector('[data-video-params-box]');
  const out = {};
  box.querySelectorAll('input, select').forEach(el => {
    const key = el.name;
    if (!key) return;
    if (el.type === 'checkbox') out[key] = el.checked;
    else if (el.type === 'number') out[key] = parseFloat(el.value);
    else out[key] = el.value;
  });
  return out;
}


function _field(label, name, type, value, step) {
  return `<label><span>${label}</span><input type="${type}" name="${name}" value="${value}" ${step?`step="${step}"`:''}></label>`;
}
function _bool(label, name, value) {
  return `<label><input type="checkbox" name="${name}" ${value?'checked':''}> ${label}</label>`;
}
function _select(label, name, opts, value) {
  return `<label><span>${label}</span><select name="${name}">${opts.map(o=>`<option value="${o[0]}" ${o[0]===value?'selected':''}>${o[1]}</option>`).join('')}</select></label>`;
}
```

- [ ] **Step 2: 手工验证**

弹窗里点"▾ 视频翻译参数",三档折叠展开正常,参数值正确回填。

- [ ] **Step 3: 提交**

```bash
git add web/static/bulk_translate.js
git commit -m "feat(bulk-translate): 视频参数三档渲染 + 回填"
```

---

### Task 35-37: 弹窗优化(黄色警告条、二次确认样式、保存为默认按钮)

- [ ] **Task 35:** 勾视频但目标语言不含 de/fr 时,显示黄色 `--warning-bg` 提示条

- [ ] **Task 36:** 二次确认改为自定义模态框(不用 `confirm()`),符合 Ocean Blue 风格

- [ ] **Task 37:** 视频参数区加"保存配置 / 保存为该产品默认 / 保存为我的默认"三个按钮,对接 `PUT /api/video-translate-profile`

每个 Task 都: 写测试/验证 → 实现 → 提交。

---

### Task 38: 右下角浮动气泡

**Files:**
- Create: `web/static/bulk_translate_progress_bubble.js`
- Create: `web/static/bulk_translate_progress_bubble.css`

- [ ] **Step 1: 创建气泡 JS**

```javascript
// web/static/bulk_translate_progress_bubble.js
(function(){
  'use strict';

  let activeTasks = new Map();  // task_id -> {status, progress, productName}
  let bubble;
  let expanded = false;

  function ensureBubble() {
    if (bubble) return;
    bubble = document.createElement('div');
    bubble.className = 'bt-bubble hidden';
    bubble.innerHTML = `
      <div class="bt-bubble__compact" data-compact>
        🌐 <span data-compact-summary>0 个任务</span>
      </div>
      <div class="bt-bubble__expanded hidden" data-expanded>
        <header>
          <span>🌐 翻译任务</span>
          <button data-minimize>▾</button>
        </header>
        <div data-task-list></div>
        <footer>
          <a href="/tasks">全部任务 →</a>
        </footer>
      </div>
    `;
    document.body.appendChild(bubble);
    bubble.querySelector('[data-compact]').onclick = () => toggle(true);
    bubble.querySelector('[data-minimize]').onclick = () => toggle(false);
  }

  function toggle(expand) {
    expanded = expand;
    bubble.querySelector('[data-compact]').classList.toggle('hidden', expand);
    bubble.querySelector('[data-expanded]').classList.toggle('hidden', !expand);
  }

  function render() {
    ensureBubble();
    if (activeTasks.size === 0) {
      bubble.classList.add('hidden');
      return;
    }
    bubble.classList.remove('hidden');

    const total = activeTasks.size;
    const completed = Array.from(activeTasks.values()).reduce((s, t) => s + (t.progress.done + t.progress.skipped), 0);
    const totalItems = Array.from(activeTasks.values()).reduce((s, t) => s + t.progress.total, 0);
    const pct = totalItems > 0 ? Math.round(100 * completed / totalItems) : 0;
    bubble.querySelector('[data-compact-summary]').textContent = `${total} 个任务 · ${pct}%`;

    const list = bubble.querySelector('[data-task-list]');
    list.innerHTML = Array.from(activeTasks.entries()).map(([tid, t]) =>
      `<div class="bt-bubble-task">
        <div>📦 ${t.productName}</div>
        <div>${_bar(t.progress)} ${t.progress.done}/${t.progress.total}</div>
        <a href="/tasks/${tid}">查看详情</a>
      </div>`
    ).join('');
  }

  function _bar(p) {
    const done = p.done + p.skipped;
    const total = p.total;
    const filled = Math.round(10 * done / Math.max(total, 1));
    return '■'.repeat(filled) + '□'.repeat(10 - filled);
  }

  function hookSocketIO() {
    const socket = window.io ? window.io() : null;
    if (!socket) return;
    socket.on('bulk_translate_progress', p => {
      const existing = activeTasks.get(p.task_id) || { productName: '任务 ' + p.task_id.slice(0, 8) };
      activeTasks.set(p.task_id, { ...existing, progress: p.progress, status: p.status });
      render();
    });
    socket.on('bulk_translate_done', p => {
      const existing = activeTasks.get(p.task_id);
      if (existing) {
        existing.progress = p.progress;
        existing.status = 'done';
        render();
        setTimeout(() => { activeTasks.delete(p.task_id); render(); }, 10000);
      }
    });
  }

  // 启动任务时主动把 task_id 注册进气泡
  window.addEventListener('bulk-translate:started', e => {
    activeTasks.set(e.detail.task_id, {
      productName: e.detail.productName || '任务',
      progress: { total: 0, done: 0, skipped: 0, running: 0, failed: 0, pending: 0 },
      status: 'running',
    });
    render();
  });

  document.addEventListener('DOMContentLoaded', hookSocketIO);
})();
```

- [ ] **Step 2: CSS(Ocean Blue)**

```css
/* bulk_translate_progress_bubble.css */
.bt-bubble { position: fixed; bottom: 24px; right: 24px; z-index: 900; }
.bt-bubble.hidden { display: none; }
.bt-bubble__compact, .bt-bubble__expanded {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  cursor: pointer;
}
.bt-bubble__compact { min-width: 180px; }
.bt-bubble__expanded { min-width: 360px; padding: var(--space-4); }
.bt-bubble__expanded.hidden, .bt-bubble__compact.hidden { display: none; }
.bt-bubble-task { padding: var(--space-2) 0; border-bottom: 1px solid var(--border); font-family: var(--font-mono); }
.bt-bubble-task:last-child { border-bottom: 0; }
```

- [ ] **Step 3: 在 base.html 引入**

- [ ] **Step 4: 手工验证**

先创建一个任务(`POST /api/bulk-translate/create` + `/start`),观察右下角出现气泡,点击展开,进度更新。

- [ ] **Step 5: 提交**

```bash
git add web/static/bulk_translate_progress_bubble.{js,css}
git commit -m "feat(bulk-translate): 右下角浮动进度气泡(SocketIO 对接)"
```

---

### Task 39-40: 气泡交互完善(失败态、完成消失、刷新后查 active 任务)

- [ ] **Task 39:** 失败任务边框转 `--danger`,不自动消失
- [ ] **Task 40:** 页面加载时通过 `GET /api/bulk-translate/list?status=running,paused,error` 获取未完成任务补齐到气泡(但**不触发任何恢复**,仅展示)

**🎯 Phase 6 里程碑**:弹窗可触发任务,右下角气泡实时展示进度,刷新页面后仍能看到进行中任务。

---

## Phase 7 · 任务中心 + 详情页 UI

### Task 41: `/tasks` 列表页

**Files:**
- Create: `web/templates/bulk_translate_list.html`
- Modify: `web/routes/bulk_translate.py`(加 GET /tasks 页面路由)

- [ ] **Step 1: 路由**

在 `web/routes/bulk_translate.py`:

```python
from flask import render_template

@bp.get("/tasks", strict_slashes=False)  # 注意:与 /api 前缀分离
# 正确做法:新建一个不带 /api 前缀的页面路由 blueprint
```

更规范:新建 `web/routes/tasks_page.py`:

```python
from flask import Blueprint, render_template, g

bp_page = Blueprint("tasks_page", __name__)

@bp_page.get("/tasks")
def tasks_list():
    return render_template("bulk_translate_list.html", current_user_id=g.current_user_id)

@bp_page.get("/tasks/<task_id>")
def tasks_detail(task_id):
    return render_template("bulk_translate_detail.html", task_id=task_id)
```

注册到 app。

- [ ] **Step 2: 列表页模板**

```html
<!-- web/templates/bulk_translate_list.html -->
{% extends 'base.html' %}
{% block content %}
<div class="tasks-page">
  <header>
    <h1>🌐 翻译任务中心</h1>
    <nav class="tasks-tabs">
      <button data-tab="">全部</button>
      <button data-tab="running">进行中</button>
      <button data-tab="done">已完成</button>
      <button data-tab="error">失败</button>
      <button data-tab="cancelled">已取消</button>
    </nav>
  </header>
  <table class="tasks-table">
    <thead>
      <tr>
        <th>任务</th><th>产品</th><th>目标语言</th>
        <th>发起人</th><th>创建时间</th>
        <th>状态</th><th>进度</th><th>预估 / 实际</th><th>操作</th>
      </tr>
    </thead>
    <tbody data-task-rows></tbody>
  </table>
</div>
<script>
(async function(){
  async function load(status) {
    const url = '/api/bulk-translate/list' + (status ? `?status=${status}` : '');
    const rows = await (await fetch(url)).json();
    renderRows(rows);
  }
  function renderRows(rows) {
    const tbody = document.querySelector('[data-task-rows]');
    tbody.innerHTML = rows.map(r => `
      <tr>
        <td><code>${r.id.slice(0,8)}</code></td>
        <td>${r.product_id}</td>
        <td>${(r.target_langs||[]).join(', ')}</td>
        <td>${r.initiator?.user_name||''}</td>
        <td>${r.created_at||''}</td>
        <td>${r.status}</td>
        <td>${r.progress.done+r.progress.skipped}/${r.progress.total}</td>
        <td>¥${r.cost_estimate} / ¥${r.cost_actual}</td>
        <td><a href="/tasks/${r.id}">详情</a></td>
      </tr>
    `).join('');
  }
  document.querySelectorAll('.tasks-tabs button').forEach(b => b.onclick = () => load(b.dataset.tab));
  load('');
})();
</script>
{% endblock %}
```

- [ ] **Step 3: 手工验证 + 提交**

```bash
git add web/routes/tasks_page.py web/templates/bulk_translate_list.html web/app.py
git commit -m "feat(bulk-translate): /tasks 任务中心列表页"
```

---

### Task 42-48: 任务详情页 `/tasks/<id>`

- [ ] **Task 42:** 详情页顶部元信息卡(发起人/时间/IP/语言数)
- [ ] **Task 43:** 总进度卡(进度条/子项统计/费用/耗时)
- [ ] **Task 44:** 6 个操作按钮(▶ 继续 / ⏸ 暂停 / 🔁 重跑失败 / 📜 操作记录 / 🚫 取消)的 UI + 对接各 API
- [ ] **Task 45:** 按语言分组展开的 plan 项列表
- [ ] **Task 46:** 每 plan 项的"查看子任务 ↗"链接(根据 kind 跳不同 URL)+ 单项重跑按钮
- [ ] **Task 47:** 操作记录右侧抽屉(展示 audit_events 时间线)
- [ ] **Task 48:** SocketIO 实时更新详情页(监听 `bulk_translate_progress` 更新 DOM)

每 Task 都:先在模板/JS 里写结构 → 对接 API → 人工验证 → 提交。

**🎯 Phase 7 里程碑**:完整可用的任务详情页,所有操作按钮都能通过 API 完成对应动作。

---

## Phase 8 · 入口接入

### Task 49: 素材管理产品行按钮

**Files:**
- Modify: `web/templates/medias_list.html`
- Modify: `web/static/medias.js`

- [ ] **Step 1: 在产品行操作区加按钮**

```html
<!-- 在每行操作区域 -->
<button class="bt-row-btn" data-bulk-translate data-product-id="{{ p.id }}" data-product-name="{{ p.name }}">
  🌐 一键翻译
</button>
```

- [ ] **Step 2: JS 绑定**

```javascript
// 在 medias.js 或新建 medias_bulk_translate.js
document.addEventListener('click', async (e) => {
  if (!e.target.matches('[data-bulk-translate]')) return;
  const enabledLangs = await (await fetch('/api/languages/enabled')).json();
  window.BulkTranslateDialog.open({
    mode: 'multi-lang',
    productId: e.target.dataset.productId,
    productName: e.target.dataset.productName,
    enabledLangs,
  });
});
```

- [ ] **Step 3: 手工验证**

素材管理列表里点任意产品的"🌐 一键翻译"按钮,弹窗出现,目标语言默认全勾。

- [ ] **Step 4: 提交**

```bash
git add web/templates/medias_list.html web/static/medias.js
git commit -m "feat(medias): 产品行增加'一键翻译'按钮"
```

---

### Task 50: 视频翻译详情页按钮

**Files:**
- Modify: `web/templates/translate_lab_detail.html`

- [ ] **Step 1: 仅 de/fr 时渲染按钮**

```html
{% if target_language in ('de', 'fr') %}
<button class="bt-row-btn" data-bulk-translate-single
        data-product-id="{{ product_id }}"
        data-product-name="{{ product_name }}"
        data-fixed-lang="{{ target_language }}">
  🌐 一键从英文翻译
</button>
{% endif %}
```

- [ ] **Step 2: JS 绑定(复用弹窗)**

```javascript
document.addEventListener('click', (e) => {
  if (!e.target.matches('[data-bulk-translate-single]')) return;
  const lang = e.target.dataset.fixedLang;
  window.BulkTranslateDialog.open({
    mode: 'single-lang',
    productId: e.target.dataset.productId,
    productName: e.target.dataset.productName,
    fixedLang: lang,
    langName: lang === 'de' ? '德语' : '法语',
  });
});
```

- [ ] **Step 3: 验证 + 提交**

```bash
git add web/templates/translate_lab_detail.html web/static/...
git commit -m "feat(translate-lab): 详情页按钮(单语言入口,仅 de/fr)"
```

---

### Task 51: 两入口复用弹窗的集成测试

用 Playwright(项目已有)写两个 E2E 用例:
- 产品行按钮打开多语言弹窗
- 德语详情页按钮打开单语言弹窗
- 两种弹窗都能完成"开始翻译"流程

**🎯 Phase 8 里程碑**:两个入口接入完毕,用户可从 UI 完成触发。

---

## Phase 9 · 关联标识 UI

### Task 52: 徽章组件

**Files:**
- Create: `web/static/bulk_translate_badge.js`
- Create: `web/static/bulk_translate_badge.css`

- [ ] **Step 1: 实现徽章组件 + 悬浮卡**

```javascript
// bulk_translate_badge.js
(function(){
  function renderBadge(container, info) {
    const mini = info.iconOnly;
    container.innerHTML = `
      <span class="bt-badge-tl ${info.manuallyEdited?'bt-badge-tl--edited':''} ${mini?'bt-badge-tl--mini':''}" data-badge-hover>
        🔗${mini ? '' : ' 英文译本'}${info.manuallyEdited?' · ✏️':''}
      </span>
    `;
    const badge = container.querySelector('[data-badge-hover]');
    badge.onmouseenter = () => _showHoverCard(badge, info);
    badge.onmouseleave = () => _hideHoverCard();
  }

  function _showHoverCard(el, info) { /* ... 渲染悬浮卡 DOM */ }
  function _hideHoverCard() { /* ... */ }

  window.BulkTranslateBadge = { render: renderBadge };
})();
```

- [ ] **Step 2-3: 在文案/图片/视频渲染处调用 + CSS + 提交**

---

### Task 53-57: 其他关联 UI

- [ ] **Task 53:** 素材详情页"来源信息"折叠区
- [ ] **Task 54:** 列表筛选下拉("全部 / 原创 / 自动翻译")
- [ ] **Task 55:** 源条目/父任务被删时的灰态降级
- [ ] **Task 56:** 编辑自动翻译结果时的 info 提示条
- [ ] **Task 57:** 保存编辑时把 `manually_edited_at` 写入(API 增强)

**🎯 Phase 9 里程碑**:所有关联追踪在 UI 上可见、可交互、能安全退化。

---

## Phase 10 · 端到端验收

### Task 58: E2E 脚本 — 单产品翻译到 de/fr

**Files:**
- Create: `tests/e2e/test_bulk_translate_e2e.py`

- [ ] **Step 1: 场景化 E2E**

```python
"""端到端:创建产品 → 上传英文素材 → 触发一键翻译 → 验证结果。"""
import time
import pytest


def test_full_flow_de_fr(api_client, test_user):
    # 1. 创建产品 + 英文素材(文案 2 条 + 英文视频 1 个 + 详情图 2 张)
    product = api_client.create_product(name="E2E 测试产品")
    api_client.add_copy(product.id, lang="en", text="Welcome to our product")
    api_client.add_copy(product.id, lang="en", text="Easy to use")
    api_client.add_video(product.id, lang="en", path="fixtures/test_video.mp4")
    api_client.add_detail_image(product.id, lang="en", path="fixtures/img1.jpg")
    api_client.add_detail_image(product.id, lang="en", path="fixtures/img2.jpg")

    # 2. 估算
    est = api_client.bulk_translate_estimate(
        product_id=product.id, target_langs=["de", "fr"],
        content_types=["copy", "detail", "video"], force=False,
    )
    assert est.estimated_cost_cny > 0

    # 3. 创建父任务
    tid = api_client.bulk_translate_create(
        product_id=product.id, target_langs=["de", "fr"],
        content_types=["copy", "detail", "video"], video_params={},
    )

    # 4. 启动
    api_client.bulk_translate_start(tid)

    # 5. 轮询直到完成
    deadline = time.time() + 600  # 10 分钟超时
    while time.time() < deadline:
        task = api_client.bulk_translate_get(tid)
        if task.status in ("done", "error", "cancelled"):
            break
        time.sleep(5)

    # 6. 验证
    assert task.status == "done"
    # 文案各语种各 2 条
    de_copies = api_client.list_copies(product.id, lang="de")
    fr_copies = api_client.list_copies(product.id, lang="fr")
    assert len(de_copies) == 2
    assert len(fr_copies) == 2
    # 所有译本都有 auto_translated=1 和 source_ref_id
    for c in de_copies + fr_copies:
        assert c.auto_translated == 1
        assert c.source_ref_id is not None
        assert c.bulk_task_id == tid

    # 视频各语种 1 个
    de_videos = api_client.list_videos(product.id, lang="de")
    fr_videos = api_client.list_videos(product.id, lang="fr")
    assert len(de_videos) == 1
    assert len(fr_videos) == 1

    # 费用实际 vs 预估偏差 ≤ 30%(放宽一点,因为测试用小文件)
    assert abs(task.cost_tracking.actual.actual_cost_cny - est.estimated_cost_cny) \
           / est.estimated_cost_cny <= 0.3
```

- [ ] **Step 2: 运行**

```bash
pytest tests/e2e/test_bulk_translate_e2e.py -v -s
```

- [ ] **Step 3: 提交**

---

### Task 59: E2E — 失败场景

- [ ] 强制让某个子任务失败(mock LLM 返 500),验证父任务立即停在 error,其他 pending 项未被执行。

### Task 60: 铁律验证 — 重启进程不自动恢复

- [ ] 启动任务跑到中途 → kill -9 进程 → 重启 → 验证 DB 里任务状态不被改、气泡也不自动冒出 → 点"▶ 继续执行"才恢复。

### Task 61: 源变化安全退化

- [ ] 跑完任务 → 软删源英文文案 → 打开德语译本详情页 → 验证徽章显示"⚠️ 源已删除"且无崩溃。

### Task 62: 最终验收

按设计文档第 8 节"验收标准"的 checklist 逐项走一遍,所有项 ✓ 才算真正完成。

**🎯 Phase 10 里程碑**:完整验收,功能可上线。

---

## 自检与开发约定

### 一般性开发指引

- **每完成一个 Task 立刻提交**(`git commit`),避免堆积大量未提交代码
- **遇到不明确的地方先查设计文档** `docs/superpowers/specs/2026-04-18-bulk-translate-design.md`
- **Python 日志**复用项目现有 logging 配置,不新增 logger 层级
- **所有 SQL** 用参数化查询,避免 SQL 注入
- **所有 UI 文字用中文**
- **所有商业术语用 Ocean Blue Design System**(零紫色,OKLCH 色域 200-240)

### 每次执行前的环境检查

在每个 Phase 开始时执行:
```bash
pytest tests/ -x --collect-only | head -30   # 确认测试可发现
python -c "from appcore.db import get_engine; print(get_engine())" # DB 连接正常
```

### 迁移回滚

若本期迁移需回滚(紧急情况):
```sql
-- 反向迁移(视需要执行)
ALTER TABLE projects MODIFY COLUMN type ENUM(
  'translation','de_translate','fr_translate','copywriting',
  'video_creation','video_review','translate_lab',
  'image_translate','subtitle_removal'
) NOT NULL;
-- 四张素材表删除新加的列 + 删除 media_video_translate_profiles 表
```

---

## 依赖 Phase 关系图

```
Phase 1 (数据层) ───────────────▶ Phase 2 (copywriting_translate)
      │                               │
      ▼                               ▼
Phase 3 (估算) ──────────────▶ Phase 4 (调度器) ──▶ Phase 5 (API)
                                                        │
                                                        ▼
                                    Phase 6 (弹窗+气泡) ─▶ Phase 7 (任务中心/详情)
                                                                    │
                                                                    ▼
                                            Phase 8 (入口) ─▶ Phase 9 (关联 UI)
                                                                    │
                                                                    ▼
                                                            Phase 10 (端到端验收)
```

**串行执行**:1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10。每个 Phase 结束都是可上线的增量里程碑。

**End of Plan**
