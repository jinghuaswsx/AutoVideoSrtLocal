# Medias 多语种管理实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有产品/素材/文案三表结构上横向加 `lang` 维度，让一个产品管理 7 个语种（英/德/法/西/意/日/韩）的视频素材、文案、产品主图，英语主图硬必填，其他语种缺省 fallback 到英语。

**Architecture:** 新增 `media_languages` 配置表 + `media_product_covers` 子表；在 `media_items` 和 `media_copywritings` 各加 `lang` 列。API 层所有写接口带 `lang` 参数；列表接口返回每产品的 `lang_coverage` 覆盖度。UI 列表页每行 7 个语种 chip；编辑弹窗顶部加语种 tab，切换 tab 整套内容跟着切。

**Tech Stack:** Python 3 / Flask / MySQL / pytest；前端纯原生 JS + Jinja2 模板。

**Spec reference:** `docs/superpowers/specs/2026-04-16-medias-multi-language-design.md`

---

## File Structure

**新建：**
- `db/migrations/2026_04_16_medias_multi_lang.sql` — 迁移脚本
- `tests/test_appcore_medias_multi_lang.py` — DAO 层新功能测试

**修改：**
- `appcore/medias.py` — DAO 层加 lang 支持与新表 CRUD
- `web/routes/medias.py` — API 层所有写接口接 lang；列表返回 lang_coverage；封面改新表；新增 `/api/languages`
- `web/templates/medias_list.html` — 列表行加 7 chip 覆盖条样式 & 编辑弹窗 HTML 骨架加 tab 栏
- `web/static/medias.js` — 列表 chip 渲染 + 编辑弹窗语种 tab 逻辑 + 上传/保存带 lang

**暂不修改（保留只读兼容）：**
- `media_products.cover_object_key` 列本次不 DROP

---

## Task 1：迁移脚本

**Files:**
- Create: `db/migrations/2026_04_16_medias_multi_lang.sql`

- [ ] **Step 1: 写迁移 SQL**

```sql
-- db/migrations/2026_04_16_medias_multi_lang.sql

-- 1. 语种配置表
CREATE TABLE IF NOT EXISTS media_languages (
  code       VARCHAR(8)  PRIMARY KEY,
  name_zh    VARCHAR(32) NOT NULL,
  sort_order INT         NOT NULL DEFAULT 0,
  enabled    TINYINT(1)  NOT NULL DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO media_languages (code, name_zh, sort_order) VALUES
  ('en','英语',1),
  ('de','德语',2),
  ('fr','法语',3),
  ('es','西班牙语',4),
  ('it','意大利语',5),
  ('ja','日语',6),
  ('ko','韩语',7);

-- 2. 素材加 lang
ALTER TABLE media_items
  ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'en' AFTER product_id,
  ADD KEY idx_product_lang (product_id, lang, deleted_at);

-- 3. 文案加 lang
ALTER TABLE media_copywritings
  ADD COLUMN lang VARCHAR(8) NOT NULL DEFAULT 'en' AFTER product_id,
  ADD KEY idx_product_lang (product_id, lang, idx);

-- 4. 产品主图按语种分
CREATE TABLE IF NOT EXISTS media_product_covers (
  product_id INT          NOT NULL,
  lang       VARCHAR(8)   NOT NULL,
  object_key VARCHAR(255) NOT NULL,
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (product_id, lang)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 回填：已有产品主图作为英文主图
INSERT IGNORE INTO media_product_covers (product_id, lang, object_key)
SELECT id, 'en', cover_object_key
FROM media_products
WHERE cover_object_key IS NOT NULL AND deleted_at IS NULL;
```

- [ ] **Step 2: 在本地 MySQL 执行迁移**

```bash
mysql -u <user> -p <db> < db/migrations/2026_04_16_medias_multi_lang.sql
```

预期：无报错；`SELECT * FROM media_languages;` 返回 7 行；`DESCRIBE media_items;` 能看到 `lang` 列；旧产品的主图已在 `media_product_covers` 中以 `lang='en'` 存在。

- [ ] **Step 3: 提交**

```bash
git add db/migrations/2026_04_16_medias_multi_lang.sql
git commit -m "feat(medias): 多语种迁移 — media_languages + lang 列 + covers 子表"
```

---

## Task 2：DAO 层 — 语种 & 素材 & 文案加 lang

**Files:**
- Modify: `appcore/medias.py`
- Test: `tests/test_appcore_medias_multi_lang.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_appcore_medias_multi_lang.py`：

```python
import pytest
from appcore import medias
from appcore.db import query_one


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB; create one before running these tests."
    return row["id"]


def test_list_languages_returns_enabled_sorted():
    langs = medias.list_languages()
    codes = [l["code"] for l in langs]
    assert codes[0] == "en"
    assert set(codes) >= {"en", "de", "fr", "es", "it", "ja", "ko"}
    assert all(l["enabled"] == 1 for l in langs)


def test_is_valid_language():
    assert medias.is_valid_language("de") is True
    assert medias.is_valid_language("xx") is False


def test_create_item_with_lang(user_id):
    pid = medias.create_product(user_id, "多语素材测试", product_code="mlt-items-01")
    try:
        iid_en = medias.create_item(pid, user_id, "en.mp4", "k/en", lang="en")
        iid_de = medias.create_item(pid, user_id, "de.mp4", "k/de", lang="de")
        items = medias.list_items(pid)
        by_id = {i["id"]: i for i in items}
        assert by_id[iid_en]["lang"] == "en"
        assert by_id[iid_de]["lang"] == "de"
        en_only = medias.list_items(pid, lang="en")
        assert [i["id"] for i in en_only] == [iid_en]
    finally:
        medias.soft_delete_product(pid)


def test_replace_copywritings_per_lang(user_id):
    pid = medias.create_product(user_id, "多语文案测试", product_code="mlt-copy-01")
    try:
        medias.replace_copywritings(pid, [{"title": "T_en", "body": "B"}], lang="en")
        medias.replace_copywritings(pid, [
            {"title": "T_de_1", "body": "B1"},
            {"title": "T_de_2", "body": "B2"},
        ], lang="de")
        en = medias.list_copywritings(pid, lang="en")
        de = medias.list_copywritings(pid, lang="de")
        assert [c["title"] for c in en] == ["T_en"]
        assert [c["title"] for c in de] == ["T_de_1", "T_de_2"]
        # 替换 de 不应影响 en
        medias.replace_copywritings(pid, [], lang="de")
        assert medias.list_copywritings(pid, lang="en")[0]["title"] == "T_en"
        assert medias.list_copywritings(pid, lang="de") == []
    finally:
        medias.soft_delete_product(pid)


def test_product_covers_per_lang(user_id):
    pid = medias.create_product(user_id, "多语主图测试", product_code="mlt-cover-01")
    try:
        medias.set_product_cover(pid, "en", "covers/en.jpg")
        medias.set_product_cover(pid, "de", "covers/de.jpg")
        covers = medias.get_product_covers(pid)
        assert covers["en"] == "covers/en.jpg"
        assert covers["de"] == "covers/de.jpg"
        # 解析：其他语种回退英文
        assert medias.resolve_cover(pid, "fr") == "covers/en.jpg"
        assert medias.resolve_cover(pid, "de") == "covers/de.jpg"
        medias.delete_product_cover(pid, "de")
        assert medias.resolve_cover(pid, "de") == "covers/en.jpg"
    finally:
        medias.soft_delete_product(pid)


def test_lang_coverage_map(user_id):
    pid = medias.create_product(user_id, "覆盖度测试", product_code="mlt-cov-01")
    try:
        medias.create_item(pid, user_id, "a.mp4", "k/a", lang="en")
        medias.create_item(pid, user_id, "b.mp4", "k/b", lang="en")
        medias.create_item(pid, user_id, "c.mp4", "k/c", lang="de")
        medias.replace_copywritings(pid, [{"title": "x"}], lang="en")
        medias.set_product_cover(pid, "en", "covers/en.jpg")
        cov = medias.lang_coverage_by_product([pid])[pid]
        assert cov["en"]["items"] == 2
        assert cov["en"]["copy"] == 1
        assert cov["en"]["cover"] is True
        assert cov["de"]["items"] == 1
        assert cov["de"]["cover"] is False
        assert cov["fr"]["items"] == 0
    finally:
        medias.soft_delete_product(pid)
```

- [ ] **Step 2: 运行验证失败**

```bash
pytest tests/test_appcore_medias_multi_lang.py -v
```

预期：AttributeError 或 TypeError（新函数未实现，或 `create_item/replace_copywritings` 不接受 `lang`）。

- [ ] **Step 3: 在 `appcore/medias.py` 顶部加语种相关函数**

在现有 imports 后、"---- 产品 ----" 注释前插入：

```python
# ---------- 语种 ----------

def list_languages() -> list[dict]:
    return query(
        "SELECT code, name_zh, sort_order, enabled FROM media_languages "
        "WHERE enabled=1 ORDER BY sort_order ASC, code ASC"
    )


def is_valid_language(code: str) -> bool:
    if not code:
        return False
    row = query_one(
        "SELECT 1 AS ok FROM media_languages WHERE code=%s AND enabled=1",
        (code,),
    )
    return bool(row)
```

- [ ] **Step 4: 改 `create_item` 签名加 `lang`**

原 `create_item` 函数签名新增 `lang: str = "en"` 形参，SQL 改为：

```python
def create_item(product_id: int, user_id: int, filename: str, object_key: str,
                display_name: str | None = None, file_url: str | None = None,
                thumbnail_path: str | None = None, duration_seconds: float | None = None,
                file_size: int | None = None,
                cover_object_key: str | None = None,
                lang: str = "en") -> int:
    return execute(
        "INSERT INTO media_items "
        "(product_id, lang, user_id, filename, display_name, object_key, file_url, "
        " thumbnail_path, cover_object_key, duration_seconds, file_size) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (product_id, lang, user_id, filename, display_name or filename, object_key,
         file_url, thumbnail_path, cover_object_key, duration_seconds, file_size),
    )
```

- [ ] **Step 5: 改 `list_items` 加可选 lang 过滤**

```python
def list_items(product_id: int, lang: str | None = None) -> list[dict]:
    if lang:
        return query(
            "SELECT * FROM media_items "
            "WHERE product_id=%s AND lang=%s AND deleted_at IS NULL "
            "ORDER BY sort_order ASC, id ASC",
            (product_id, lang),
        )
    return query(
        "SELECT * FROM media_items WHERE product_id=%s AND deleted_at IS NULL "
        "ORDER BY sort_order ASC, id ASC",
        (product_id,),
    )
```

- [ ] **Step 6: 改 `list_copywritings` / `replace_copywritings` 按 lang 操作**

```python
def list_copywritings(product_id: int, lang: str | None = None) -> list[dict]:
    if lang:
        return query(
            "SELECT * FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s ORDER BY idx ASC, id ASC",
            (product_id, lang),
        )
    return query(
        "SELECT * FROM media_copywritings WHERE product_id=%s "
        "ORDER BY lang ASC, idx ASC, id ASC",
        (product_id,),
    )


def replace_copywritings(product_id: int, items: list[dict], lang: str = "en") -> None:
    """整体替换某语种的文案列表。"""
    execute(
        "DELETE FROM media_copywritings WHERE product_id=%s AND lang=%s",
        (product_id, lang),
    )
    for idx, item in enumerate(items, start=1):
        execute(
            "INSERT INTO media_copywritings "
            "(product_id, lang, idx, title, body, description, ad_carrier, ad_copy, ad_keywords) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (product_id, lang, idx,
             item.get("title"), item.get("body"), item.get("description"),
             item.get("ad_carrier"), item.get("ad_copy"), item.get("ad_keywords")),
        )
```

- [ ] **Step 7: 加产品主图（per-lang）CRUD**

在文件末尾加：

```python
# ---------- 产品主图（per-lang） ----------

def set_product_cover(product_id: int, lang: str, object_key: str) -> None:
    execute(
        "INSERT INTO media_product_covers (product_id, lang, object_key) "
        "VALUES (%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE object_key=VALUES(object_key)",
        (product_id, lang, object_key),
    )


def delete_product_cover(product_id: int, lang: str) -> int:
    return execute(
        "DELETE FROM media_product_covers WHERE product_id=%s AND lang=%s",
        (product_id, lang),
    )


def get_product_covers(product_id: int) -> dict[str, str]:
    rows = query(
        "SELECT lang, object_key FROM media_product_covers WHERE product_id=%s",
        (product_id,),
    )
    return {r["lang"]: r["object_key"] for r in rows}


def resolve_cover(product_id: int, lang: str) -> str | None:
    """返回该语种主图；缺失时回退到 en；都没有返回 None。"""
    covers = get_product_covers(product_id)
    return covers.get(lang) or covers.get("en")


def has_english_cover(product_id: int) -> bool:
    row = query_one(
        "SELECT 1 AS ok FROM media_product_covers WHERE product_id=%s AND lang='en'",
        (product_id,),
    )
    return bool(row)


# ---------- 覆盖度统计 ----------

def lang_coverage_by_product(product_ids: list[int]) -> dict[int, dict[str, dict]]:
    """返回 { pid: { lang: {items, copy, cover} } }，仅包含启用语种。"""
    if not product_ids:
        return {}
    langs = [l["code"] for l in list_languages()]
    placeholders = ",".join(["%s"] * len(product_ids))

    item_rows = query(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_items "
        f"WHERE product_id IN ({placeholders}) AND deleted_at IS NULL "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )
    copy_rows = query(
        f"SELECT product_id, lang, COUNT(*) AS c FROM media_copywritings "
        f"WHERE product_id IN ({placeholders}) "
        f"GROUP BY product_id, lang",
        tuple(product_ids),
    )
    cover_rows = query(
        f"SELECT product_id, lang FROM media_product_covers "
        f"WHERE product_id IN ({placeholders})",
        tuple(product_ids),
    )

    out: dict[int, dict[str, dict]] = {
        pid: {lang: {"items": 0, "copy": 0, "cover": False} for lang in langs}
        for pid in product_ids
    }
    for r in item_rows:
        pid = int(r["product_id"])
        lang = r["lang"]
        if pid in out and lang in out[pid]:
            out[pid][lang]["items"] = int(r["c"])
    for r in copy_rows:
        pid = int(r["product_id"])
        lang = r["lang"]
        if pid in out and lang in out[pid]:
            out[pid][lang]["copy"] = int(r["c"])
    for r in cover_rows:
        pid = int(r["product_id"])
        lang = r["lang"]
        if pid in out and lang in out[pid]:
            out[pid][lang]["cover"] = True
    return out
```

- [ ] **Step 8: 运行测试验证通过**

```bash
pytest tests/test_appcore_medias_multi_lang.py -v
pytest tests/test_appcore_medias.py -v
```

预期：新测试全部 PASS，老测试仍 PASS。

- [ ] **Step 9: 提交**

```bash
git add appcore/medias.py tests/test_appcore_medias_multi_lang.py
git commit -m "feat(medias): DAO 层支持多语种 — items/copy 加 lang、产品主图 per-lang"
```

---

## Task 3：API 层 — 写接口接 lang + 列表 coverage + 封面改新表 + `/api/languages`

**Files:**
- Modify: `web/routes/medias.py`

- [ ] **Step 1: 新增 `GET /api/languages`**

在 `web/routes/medias.py` 文件末尾加：

```python
@bp.route("/api/languages", methods=["GET"])
@login_required
def api_list_languages():
    return jsonify({"items": medias.list_languages()})
```

- [ ] **Step 2: 加 lang 校验辅助**

在文件顶部（`_ALLOWED_IMAGE_TYPES` 之后）加：

```python
def _parse_lang(body: dict, default: str = "en") -> tuple[str | None, str | None]:
    """返回 (lang, error)。lang 校验不通过返回 (None, error msg)。"""
    lang = (body.get("lang") or default or "en").strip().lower()
    if not medias.is_valid_language(lang):
        return None, f"不支持的语种: {lang}"
    return lang, None
```

- [ ] **Step 3: `_serialize_item` 增加 lang 字段**

```python
def _serialize_item(it: dict) -> dict:
    has_user_cover = bool(it.get("cover_object_key"))
    return {
        "id": it["id"],
        "lang": it.get("lang") or "en",
        "filename": it["filename"],
        # ... 其余字段保持不变
        ...
    }
```

（在现有字段 dict 里插入 `"lang": it.get("lang") or "en",`，其余行不变。）

- [ ] **Step 4: `_serialize_product` 去除 cover_object_key 列依赖，改为统一用 resolve_cover**

将原函数改为：

```python
def _serialize_product(p: dict, items_count: int | None = None,
                       cover_item_id: int | None = None,
                       items_filenames: list[str] | None = None,
                       lang_coverage: dict | None = None) -> dict:
    covers = medias.get_product_covers(p["id"])
    has_en_cover = "en" in covers
    cover_url = f"/medias/cover/{p['id']}?lang=en" if has_en_cover else (
        f"/medias/thumb/{cover_item_id}" if cover_item_id else None
    )
    return {
        "id": p["id"],
        "name": p["name"],
        "product_code": p.get("product_code"),
        "has_en_cover": has_en_cover,
        "color_people": p.get("color_people"),
        "source": p.get("source"),
        "archived": bool(p.get("archived")),
        "created_at": p["created_at"].isoformat() if p.get("created_at") else None,
        "updated_at": p["updated_at"].isoformat() if p.get("updated_at") else None,
        "items_count": items_count,
        "items_filenames": items_filenames or [],
        "cover_thumbnail_url": cover_url,
        "lang_coverage": lang_coverage or {},
    }
```

- [ ] **Step 5: 列表接口装入 lang_coverage**

把 `api_list_products` 里的返回计算改为：

```python
@bp.route("/api/products", methods=["GET"])
@login_required
def api_list_products():
    keyword = (request.args.get("keyword") or "").strip()
    archived = request.args.get("archived") in ("1", "true", "yes")
    scope_all = request.args.get("scope") == "all" and _is_admin()
    page = max(1, int(request.args.get("page") or 1))
    limit = 20
    offset = (page - 1) * limit

    user_id = None if scope_all else current_user.id
    rows, total = medias.list_products(user_id, keyword=keyword, archived=archived,
                                        offset=offset, limit=limit)
    pids = [r["id"] for r in rows]
    counts = medias.count_items_by_product(pids)
    covers = medias.first_thumb_item_by_product(pids)
    filenames = medias.list_item_filenames_by_product(pids, limit_per=5)
    coverage = medias.lang_coverage_by_product(pids)
    data = [
        _serialize_product(
            r, counts.get(r["id"], 0), covers.get(r["id"]),
            items_filenames=filenames.get(r["id"], []),
            lang_coverage=coverage.get(r["id"], {}),
        )
        for r in rows
    ]
    return jsonify({"items": data, "total": total, "page": page, "page_size": limit})
```

- [ ] **Step 6: 产品保存硬校验英文主图**

把 `api_update_product` 里 `items = medias.list_items(pid)` 之前插入：

```python
    if not medias.has_english_cover(pid):
        return jsonify({"error": "必须先上传英文（EN）产品主图才能保存"}), 400
```

同时把现有 `if "cover_object_key" in body` 分支**删除**（产品主图已不走该字段，改由封面接口写 `media_product_covers`）。

更新 copywritings 分支要支持按语种结构：

```python
    if isinstance(body.get("copywritings"), dict):
        for lang_code, items in body["copywritings"].items():
            if not medias.is_valid_language(lang_code):
                continue
            if isinstance(items, list):
                medias.replace_copywritings(pid, items, lang=lang_code)
```

（老的扁平 list 分支删除，前端改为发 `{lang: items[]}`。）

- [ ] **Step 7: 产品详情接口返回 covers + 扁平 items/copy（含 lang）**

把 `api_get_product` 改为：

```python
@bp.route("/api/products/<int:pid>", methods=["GET"])
@login_required
def api_get_product(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    return jsonify({
        "product": _serialize_product(p, None),
        "covers": medias.get_product_covers(pid),
        "copywritings": medias.list_copywritings(pid),  # 扁平，每条带 lang
        "items": [_serialize_item(i) for i in medias.list_items(pid)],
    })
```

- [ ] **Step 8: 素材 complete 接口接 lang**

`api_item_complete` 内 `body = request.get_json(...)` 之后加：

```python
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
```

并把 `medias.create_item(...)` 的调用加上 `lang=lang`。

- [ ] **Step 9: 产品封面 3 个接口改走新表，加 lang 参数**

`api_cover_bootstrap`：body 可选 `lang`（默认 en），object_key 前缀改为 `cover_{lang}_{filename}`：

```python
@bp.route("/api/products/<int:pid>/cover/bootstrap", methods=["POST"])
@login_required
def api_cover_bootstrap(pid: int):
    if not tos_clients.is_media_bucket_configured():
        return jsonify({"error": "TOS_MEDIA_BUCKET 未配置"}), 503
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    filename = os.path.basename((body.get("filename") or "cover.jpg").strip())
    if not filename:
        return jsonify({"error": "filename required"}), 400
    object_key = tos_clients.build_media_object_key(
        current_user.id, pid, f"cover_{lang}_{filename}",
    )
    return jsonify({
        "object_key": object_key,
        "upload_url": tos_clients.generate_signed_media_upload_url(object_key),
        "expires_in": TOS_SIGNED_URL_EXPIRES,
    })
```

`api_cover_complete` 改写新表：

```python
@bp.route("/api/products/<int:pid>/cover/complete", methods=["POST"])
@login_required
def api_cover_complete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    body = request.get_json(silent=True) or {}
    lang, err = _parse_lang(body)
    if err:
        return jsonify({"error": err}), 400
    object_key = (body.get("object_key") or "").strip()
    if not object_key:
        return jsonify({"error": "object_key required"}), 400
    if not tos_clients.media_object_exists(object_key):
        return jsonify({"error": "对象不存在"}), 400

    old = medias.get_product_covers(pid).get(lang)
    if old and old != object_key:
        try:
            tos_clients.delete_media_object(old)
        except Exception:
            pass

    medias.set_product_cover(pid, lang, object_key)

    try:
        product_dir = THUMB_DIR / str(pid)
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{lang}{ext}"
        tos_clients.download_media_file(object_key, str(local))
    except Exception:
        pass

    return jsonify({"ok": True, "cover_url": f"/medias/cover/{pid}?lang={lang}"})
```

`api_cover_from_url` 同理加 lang（下载完成后 `medias.set_product_cover(pid, lang, object_key)`，本地缓存写 `cover_{lang}{ext}`）。

新增 `DELETE /api/products/:pid/cover?lang=xx`：

```python
@bp.route("/api/products/<int:pid>/cover", methods=["DELETE"])
@login_required
def api_cover_delete(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p, write=True):
        abort(404)
    lang = (request.args.get("lang") or "").strip().lower()
    if not medias.is_valid_language(lang):
        return jsonify({"error": f"不支持的语种: {lang}"}), 400
    if lang == "en":
        return jsonify({"error": "英文主图不能删除"}), 400
    old = medias.get_product_covers(pid).get(lang)
    if old:
        try:
            tos_clients.delete_media_object(old)
        except Exception:
            pass
    medias.delete_product_cover(pid, lang)
    return jsonify({"ok": True})
```

- [ ] **Step 10: `/medias/cover/:pid` 改按 lang 读新表并 fallback**

```python
@bp.route("/cover/<int:pid>")
@login_required
def cover(pid: int):
    p = medias.get_product(pid)
    if not _can_access_product(p):
        abort(404)
    lang = (request.args.get("lang") or "en").strip().lower()
    object_key = medias.resolve_cover(pid, lang)
    if not object_key:
        abort(404)
    # 如果找的是 lang 自己的主图，缓存文件名带 lang；fallback 到 en 的情况用 en
    covers = medias.get_product_covers(pid)
    actual_lang = lang if lang in covers else "en"
    product_dir = THUMB_DIR / str(pid)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        f = product_dir / f"cover_{actual_lang}{ext}"
        if f.exists():
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
            return send_file(str(f), mimetype=mime)
    try:
        product_dir.mkdir(parents=True, exist_ok=True)
        ext = Path(object_key).suffix or ".jpg"
        local = product_dir / f"cover_{actual_lang}{ext}"
        tos_clients.download_media_file(object_key, str(local))
        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext[1:]}"
        return send_file(str(local), mimetype=mime)
    except Exception:
        abort(404)
```

- [ ] **Step 11: 手工冒烟验证**

启动 Flask 开发服：

```bash
python -m web.app
```

在浏览器打开 `/medias/`，打开浏览器 devtools Network 标签。

验证：
1. `GET /medias/api/languages` → 返回 7 条语种
2. `GET /medias/api/products?page=1` → 列表项含 `lang_coverage` 字段
3. `GET /medias/api/products/<id>` → 响应含 `covers` 字段，items 每条带 `lang`
4. `GET /medias/cover/<id>?lang=de` → 如无 de 主图，fallback 返回 en 图片

老 UI 此时列表页和编辑弹窗可能因为字段改动（`cover_object_key` 去除、`copywritings` 结构变化）部分功能暂时失效——Task 4/5 会补回。

- [ ] **Step 12: 提交**

```bash
git add web/routes/medias.py
git commit -m "feat(medias): API 层接 lang — 列表返回 lang_coverage、封面 per-lang、新增 /api/languages"
```

---

## Task 4：前端列表页 — 7 chip 覆盖条

**Files:**
- Modify: `web/templates/medias_list.html`（仅 CSS + 编辑弹窗骨架）
- Modify: `web/static/medias.js`（列表行渲染 + 加载语种清单）

- [ ] **Step 1: 在 `medias_list.html` 的 `<style>` 中加 chip 样式**

在 `.oc-table` 相关样式附近加：

```css
.oc-lang-bar { display:flex; gap:4px; flex-wrap:wrap; align-items:center; }
.oc-lang-chip {
  display:inline-flex; align-items:center; justify-content:center;
  min-width:30px; height:22px; padding:0 6px;
  border-radius:var(--oc-r-md);
  font-size:11px; font-weight:600; letter-spacing:0.02em;
  font-variant-numeric:tabular-nums;
  transition:all var(--oc-dur-fast) var(--oc-ease);
  cursor:default;
}
.oc-lang-chip.filled {
  background:var(--oc-accent); color:#fff;
}
.oc-lang-chip.empty {
  background:transparent; color:var(--oc-fg-subtle);
  border:1px solid var(--oc-border-strong);
}
.oc-lang-chip .count { margin-left:3px; opacity:0.9; }
tr.oc-row-warn { box-shadow: inset 3px 0 0 0 var(--oc-danger); }
```

- [ ] **Step 2: 列表表头加"语种覆盖"列**

改 `renderGrid` 里 `<thead>` 块：

```javascript
    grid.innerHTML = `
      <table class="oc-table oc-table-fixed">
        <thead>
          <tr>
            <th style="width:56px">ID</th>
            <th style="width:220px">主图</th>
            <th>产品名称</th>
            <th>产品 ID</th>
            <th>素材数</th>
            <th style="width:260px">语种覆盖</th>
            <th>创建时间</th>
            <th>修改时间</th>
            <th style="width:110px">操作</th>
          </tr>
        </thead>
        <tbody>
          ${items.map(rowHTML).join('')}
        </tbody>
      </table>`;
```

（把"素材文件名"列替换为"语种覆盖"。）

- [ ] **Step 3: 加语种列表缓存和渲染函数**

在 `medias.js` 顶部 `const state = ...` 下方加：

```javascript
let LANGUAGES = [];

async function ensureLanguages() {
  if (LANGUAGES.length) return LANGUAGES;
  const data = await fetchJSON('/medias/api/languages');
  LANGUAGES = data.items || [];
  return LANGUAGES;
}

function renderLangBar(coverage) {
  if (!LANGUAGES.length) return '';
  return `<div class="oc-lang-bar">` + LANGUAGES.map(l => {
    const c = (coverage || {})[l.code] || { items: 0, copy: 0, cover: false };
    const filled = c.items > 0;
    const cls = filled ? 'filled' : 'empty';
    const title = `${l.name_zh}: ${c.items} 视频 / ${c.copy} 文案 / ${c.cover ? '有主图' : '无主图'}`;
    return `<span class="oc-lang-chip ${cls}" title="${escapeHtml(title)}">`
         + `${l.code.toUpperCase()}${filled ? `<span class="count">${c.items}</span>` : ''}`
         + `</span>`;
  }).join('') + `</div>`;
}
```

- [ ] **Step 4: `rowHTML` 改用语种列 + 缺 en 主图行级警示**

替换 `rowHTML` 中原"素材文件名"单元为"语种覆盖"，并给 `<tr>` 加条件 class：

```javascript
function rowHTML(p) {
  const warn = !p.has_en_cover ? ' oc-row-warn' : '';
  const cover = p.cover_thumbnail_url
    ? `<img src="${p.cover_thumbnail_url}" alt="" style="width:200px;height:200px;object-fit:cover;border-radius:8px"/>`
    : `<div class="oc-cover-ph">无主图</div>`;
  return `
    <tr data-pid="${p.id}" class="oc-row${warn}">
      <td>${p.id}</td>
      <td>${cover}</td>
      <td class="name"><a href="#" data-pid="${p.id}">${escapeHtml(p.name)}</a></td>
      <td>${escapeHtml(p.product_code || '')}</td>
      <td>${p.items_count ?? 0}</td>
      <td>${renderLangBar(p.lang_coverage)}</td>
      <td>${fmtDate(p.created_at)}</td>
      <td>${fmtDate(p.updated_at)}</td>
      <td>
        <button class="oc-btn ghost" data-edit="${p.id}">${icon('edit', 14)}编辑</button>
        <button class="oc-btn ghost danger" data-del="${p.id}">${icon('trash', 14)}删除</button>
      </td>
    </tr>`;
}
```

（原文中具体的按钮/列写法以现有代码为准——只新增 `warn` class、替换"文件名"列为 `renderLangBar`。）

- [ ] **Step 5: `loadList` 启动前确保语种已加载**

```javascript
async function loadList() {
  await ensureLanguages();
  // ... 原有逻辑
}
```

- [ ] **Step 6: 浏览器手工验证**

```bash
python -m web.app
```

打开 `/medias/`，确认：
- 每行显示 7 个 chip：有视频的语种是实心蓝并带数字；无视频的是灰描边
- 缺英文主图的产品行左侧有红色竖条
- Hover chip 看到 tooltip

- [ ] **Step 7: 提交**

```bash
git add web/templates/medias_list.html web/static/medias.js
git commit -m "feat(medias): 列表页每行加 7 语种覆盖 chip 与缺 EN 主图警示"
```

---

## Task 5：前端编辑弹窗 — 语种 tab + tab 切换过滤 + EN 主图硬约束

**Files:**
- Modify: `web/templates/medias_list.html`（编辑弹窗 HTML 骨架 + tab 样式）
- Modify: `web/static/medias.js`（`openEdit` / 保存 / 上传 / 文案编辑 全链路带 lang）

- [ ] **Step 1: 在样式块加语种 tab 样式**

```css
.oc-lang-tabs { display:flex; gap:4px; padding:var(--oc-sp-2) 0; border-bottom:1px solid var(--oc-border); margin-bottom:var(--oc-sp-4); flex-wrap:wrap; }
.oc-lang-tab {
  display:inline-flex; align-items:center; gap:6px;
  padding:6px 14px; border-radius:var(--oc-r-md);
  font-size:13px; font-weight:600;
  background:var(--oc-bg-subtle); color:var(--oc-fg);
  border:1px solid var(--oc-border); cursor:pointer;
  transition:all var(--oc-dur-fast) var(--oc-ease);
}
.oc-lang-tab:hover { background:var(--oc-bg-muted); }
.oc-lang-tab.active { background:oklch(38% 0.085 230); color:#fff; border-color:transparent; }
.oc-lang-tab .badge {
  display:inline-flex; align-items:center; justify-content:center;
  min-width:18px; height:18px; padding:0 5px;
  border-radius:9999px; font-size:11px; font-weight:700;
  background:var(--oc-danger); color:#fff;
}
.oc-lang-tab .badge.has { background:var(--oc-success-fg); }
.oc-lang-tab.active .badge { background:rgba(255,255,255,0.25); color:#fff; }
.oc-en-cover-warn {
  background:var(--oc-danger-bg); color:var(--oc-danger-fg);
  padding:var(--oc-sp-3) var(--oc-sp-4); border-radius:var(--oc-r-md);
  margin-bottom:var(--oc-sp-4); font-size:13px; font-weight:500;
}
.oc-cover-fallback-hint { font-size:11px; color:var(--oc-fg-subtle); margin-top:4px; }
```

- [ ] **Step 2: 在编辑弹窗 HTML 骨架顶部加 tab 容器**

找到编辑弹窗内容区（保存按钮所在的容器），在"产品主图"段之前插入：

```html
<div id="editLangTabs" class="oc-lang-tabs"></div>
<div id="editEnCoverWarn" class="oc-en-cover-warn" hidden>
  必须先上传英文（EN）产品主图才能保存。
</div>
```

- [ ] **Step 3: 扩展 state 记录当前 lang 和缓存数据**

```javascript
const state = {
  page: 1,
  current: null,           // 当前编辑产品 id
  activeLang: 'en',        // 当前 tab
  productData: null,       // { product, covers, items, copywritings }
  pendingItemCover: null,
};
```

- [ ] **Step 4: `openEdit` 拉数据后初始化 tab**

把原 `openEdit` 改为：

```javascript
async function openEdit(pid) {
  await ensureLanguages();
  state.current = pid;
  state.activeLang = 'en';
  const data = await fetchJSON(`/medias/api/products/${pid}`);
  state.productData = data;
  $('editModal').hidden = false;
  renderLangTabs();
  renderActiveLangView();
}
```

- [ ] **Step 5: 加 `renderLangTabs` / `renderActiveLangView` / tab 切换**

```javascript
function langTallies(lang) {
  const d = state.productData || { items: [], copywritings: [], covers: {} };
  const items = (d.items || []).filter(i => (i.lang || 'en') === lang);
  const copy = (d.copywritings || []).filter(c => (c.lang || 'en') === lang);
  const hasCover = lang in (d.covers || {});
  return { items: items.length, copy: copy.length, cover: hasCover };
}

function renderLangTabs() {
  const bar = $('editLangTabs');
  bar.innerHTML = LANGUAGES.map(l => {
    const t = langTallies(l.code);
    const active = l.code === state.activeLang ? ' active' : '';
    const badgeCls = t.items > 0 ? 'badge has' : 'badge';
    const badgeTxt = t.items > 0 ? t.items : 0;
    return `<button type="button" class="oc-lang-tab${active}" data-lang="${l.code}">`
         + `${l.code.toUpperCase()}<span class="${badgeCls}">${badgeTxt}</span>`
         + `</button>`;
  }).join('');
  bar.querySelectorAll('.oc-lang-tab').forEach(b =>
    b.addEventListener('click', () => switchLang(b.dataset.lang)));
}

function switchLang(lang) {
  if (lang === state.activeLang) return;
  state.activeLang = lang;
  renderLangTabs();
  renderActiveLangView();
}

function renderActiveLangView() {
  renderCoverBlock();
  renderItemsBlock();
  renderCopyBlock();
  const warn = $('editEnCoverWarn');
  const hasEn = 'en' in (state.productData.covers || {});
  warn.hidden = hasEn;
}
```

- [ ] **Step 6: `renderCoverBlock` 按 activeLang 展示**

找到原先渲染产品主图的函数，改写为：

```javascript
function renderCoverBlock() {
  const lang = state.activeLang;
  const covers = state.productData.covers || {};
  const key = covers[lang];
  const pid = state.current;
  const box = $('editCoverBox');  // 假设这是原主图容器 id，没有则按既有 DOM id 替换
  if (key) {
    box.innerHTML = `
      <img src="/medias/cover/${pid}?lang=${lang}&t=${Date.now()}"
           style="width:270px;height:480px;object-fit:cover;border-radius:8px"/>
      <div style="margin-top:8px;display:flex;gap:8px;">
        <button class="oc-btn ghost" id="coverUploadBtn">重新上传</button>
        ${lang === 'en' ? '' : `<button class="oc-btn ghost danger" id="coverDeleteBtn">删除（回退到 EN）</button>`}
        <button class="oc-btn ghost" id="coverUrlBtn">从 URL 导入</button>
      </div>`;
  } else {
    const fallbackHint = (lang !== 'en' && covers.en)
      ? `<div class="oc-cover-fallback-hint">当前使用 EN 默认主图，点击上传本语种专属主图</div>`
      : '';
    box.innerHTML = `
      <div class="oc-cover-ph" style="width:270px;height:480px;display:flex;align-items:center;justify-content:center;border:1px dashed var(--oc-border-strong);border-radius:8px;cursor:pointer" id="coverUploadBtn">
        + 上传 ${lang.toUpperCase()} 主图
      </div>
      ${fallbackHint}
      <div style="margin-top:8px">
        <button class="oc-btn ghost" id="coverUrlBtn">从 URL 导入</button>
      </div>`;
  }
  const up = $('coverUploadBtn'); if (up) up.addEventListener('click', () => triggerCoverUpload(lang));
  const urlBtn = $('coverUrlBtn'); if (urlBtn) urlBtn.addEventListener('click', () => triggerCoverFromUrl(lang));
  const del = $('coverDeleteBtn'); if (del) del.addEventListener('click', () => deleteCover(lang));
}
```

（`editCoverBox` 这一 id 按当前模板实际容器 id 替换；如没有独立 id，则包裹出一个新 id `editCoverBox`。）

- [ ] **Step 7: 封面上传 / URL 导入 / 删除 3 个函数带 lang**

```javascript
async function triggerCoverUpload(lang) {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.onchange = async () => {
    const file = input.files[0];
    if (!file) return;
    const pid = state.current;
    const boot = await fetchJSON(`/medias/api/products/${pid}/cover/bootstrap`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ filename: file.name, lang }),
    });
    await fetch(boot.upload_url, { method: 'PUT', body: file });
    await fetchJSON(`/medias/api/products/${pid}/cover/complete`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ object_key: boot.object_key, lang }),
    });
    const data = await fetchJSON(`/medias/api/products/${pid}`);
    state.productData = data;
    renderLangTabs();
    renderActiveLangView();
  };
  input.click();
}

async function triggerCoverFromUrl(lang) {
  const url = prompt('图片 URL:');
  if (!url) return;
  const pid = state.current;
  await fetchJSON(`/medias/api/products/${pid}/cover/from-url`, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ url, lang }),
  });
  const data = await fetchJSON(`/medias/api/products/${pid}`);
  state.productData = data;
  renderLangTabs();
  renderActiveLangView();
}

async function deleteCover(lang) {
  if (lang === 'en') return;
  if (!confirm(`删除 ${lang.toUpperCase()} 主图，回退使用 EN 默认？`)) return;
  const pid = state.current;
  await fetchJSON(`/medias/api/products/${pid}/cover?lang=${lang}`, { method: 'DELETE' });
  const data = await fetchJSON(`/medias/api/products/${pid}`);
  state.productData = data;
  renderLangTabs();
  renderActiveLangView();
}
```

- [ ] **Step 8: 视频素材区按 activeLang 过滤与上传**

`renderItemsBlock`：

```javascript
function renderItemsBlock() {
  const lang = state.activeLang;
  const items = (state.productData.items || []).filter(i => (i.lang || 'en') === lang);
  const box = $('editItemsBox');  // 原视频素材容器 id
  box.innerHTML = items.map(it => `
    <div class="oc-media-card">
      <img src="${it.cover_url || it.thumbnail_url || ''}" style="width:135px;height:240px;object-fit:cover"/>
      <div class="meta">
        <div class="name">${escapeHtml(it.display_name || it.filename)}</div>
        <button class="oc-btn ghost danger" data-del-item="${it.id}">删除</button>
      </div>
    </div>
  `).join('') || `<div class="oc-muted">${lang.toUpperCase()} 语种下暂无视频，点击上方上传按钮添加。</div>`;
  box.querySelectorAll('[data-del-item]').forEach(b =>
    b.addEventListener('click', () => deleteItem(+b.dataset.delItem)));
}
```

原视频上传处理函数（`complete` 调用）加入 `lang: state.activeLang`：

```javascript
await fetchJSON(`/medias/api/products/${pid}/items/complete`, {
  method: 'POST',
  headers: {'Content-Type':'application/json'},
  body: JSON.stringify({
    object_key,
    filename: file.name,
    file_size: file.size,
    cover_object_key: state.pendingItemCover,
    lang: state.activeLang,
  }),
});
```

- [ ] **Step 9: 文案区按 activeLang 渲染与保存**

`renderCopyBlock`（按现有文案卡片形式，但过滤 `lang === state.activeLang`；新增时默认 `lang = state.activeLang`）。保存产品时，把各语种文案 dict 打包提交：

```javascript
async function saveProduct() {
  const pid = state.current;
  // 从当前 UI 收集各语种文案。简单做法：state.productData.copywritings 里每条已带 lang，
  // 编辑时直接修改该对象内对应条目；保存时按 lang 分组。
  const byLang = {};
  for (const c of state.productData.copywritings || []) {
    const k = c.lang || 'en';
    (byLang[k] = byLang[k] || []).push({
      title: c.title, body: c.body, description: c.description,
      ad_carrier: c.ad_carrier, ad_copy: c.ad_copy, ad_keywords: c.ad_keywords,
    });
  }
  const body = {
    name: state.productData.product.name,
    product_code: state.productData.product.product_code,
    copywritings: byLang,
  };
  await fetchJSON(`/medias/api/products/${pid}`, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  closeModal();
  loadList();
}
```

- [ ] **Step 10: EN 主图未上传时禁用保存按钮**

在 `renderActiveLangView` 末尾加：

```javascript
  const hasEn = 'en' in (state.productData.covers || {});
  const saveBtn = $('editSaveBtn');
  if (saveBtn) {
    saveBtn.disabled = !hasEn;
    saveBtn.title = hasEn ? '' : '必须先上传英文主图';
  }
```

- [ ] **Step 11: 浏览器手工验证（golden path + 边界）**

启动 `python -m web.app`，打开 `/medias/`，点一个产品进入编辑弹窗。

验证：
1. 顶部出现 7 个语种 tab，en 默认高亮；每 tab 角标显示视频数，0 为红，>0 为绿
2. 切 tab → 主图区/视频区/文案区全部跟着切
3. 非 en tab，若未上传该语种主图，占位区提示"当前使用 EN 默认主图"；点击上传后界面刷新，chip 视频数不变，但 covers 增加
4. 删除某非 en 主图 → 回退到 EN fallback 图
5. en tab 下若未上传主图 → 红色警示条可见，保存按钮 disabled；上传 en 主图后警示消失、保存可点
6. 上传一个视频选到 de tab 下 → 返回主页后该产品的 DE chip 变实心蓝
7. DELETE en 主图（通过 URL 直接构造）→ 服务端返回 400"英文主图不能删除"

- [ ] **Step 12: 提交**

```bash
git add web/templates/medias_list.html web/static/medias.js
git commit -m "feat(medias): 编辑弹窗加语种 tab 与 EN 主图硬校验"
```

---

## Task 6：回归测试与手工巡检

- [ ] **Step 1: 跑全量后端测试**

```bash
pytest tests/ -q
```

预期：全部通过（新老测试）。

- [ ] **Step 2: 手工过三条完整业务流**

启动 `python -m web.app`：

1. **新建产品流**：创建 → 先上传 EN 主图 → 保存成功 → 再切 DE 补视频和文案 → 列表看 DE chip 填充
2. **老产品流**：选一个迁移前就存在的产品 → 列表应显示 EN chip 实心（因为迁移落 en），其他 chip 空 → 打开编辑确认 EN 主图从 `cover_object_key` 迁移过来
3. **容错流**：尝试保存一个没有 EN 主图的产品 → 应被 400 拒绝 → UI 保存按钮应 disabled

- [ ] **Step 3: 所有测试通过后提交最终标记**

```bash
git log --oneline | head -6
```

确认 6 个 commit（迁移、DAO、API、列表 chip、编辑 tab、可选最终修复）有序排列。

---

## 后续（不在本计划内）

- 观察 1-2 周后，追加迁移 DROP `media_products.cover_object_key` 列
- 下游视频生成 pipeline 按语种取素材的改造
- 语种级归档 / 一键从 EN 复制文案到其他语种
