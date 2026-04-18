# 推送管理模块 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 `media_items` 为粒度新增「推送管理」菜单，支持就绪判定 / 链接探活 / 浏览器直连内网系统 / 推送历史审计，仅管理员可推送或重置。

**Architecture:** 新建 `/pushes` 蓝图提供列表查询与推送工作流接口；状态实时从 `media_items` + 关联数据计算，推送历史落 `media_push_logs`；实际 POST 由浏览器端发起（避开内网隔离），后端仅负责组 payload、探活、写日志。

**Tech Stack:** Flask Blueprint + flask_login + pymysql（`appcore.db`）+ requests（HEAD 探活）+ Jinja2 + vanilla JS；测试用 pytest + conftest 已有的 `logged_in_client`（管理员）与 `authed_user_client_no_db`（普通用户）。

**Design Spec:** `docs/superpowers/specs/2026-04-18-push-management-design.md`

---

## File Structure

**新建**
- `db/migrations/2026_04_18_add_push_management.sql` — 迁移：新增 `media_push_logs` + `media_products.ad_supported_langs` + `media_items.pushed_at/latest_push_id`
- `appcore/pushes.py` — 推送 DAO / 业务逻辑（状态计算、payload 组装、探活、日志写入、状态变更）
- `web/routes/pushes.py` — 蓝图：页面 + JSON API
- `web/templates/pushes_list.html` — 列表页
- `web/static/pushes.js` — 前端交互
- `web/static/pushes.css` — 列表页样式（如需）
- `tests/test_appcore_pushes.py` — DAO 层单测
- `tests/test_pushes_routes.py` — 路由测试

**修改**
- `appcore/medias.py` — 扩展 `ad_supported_langs` 读写（`update_product` allowed 集合 + `parse_ad_supported_langs` 辅助）
- `config.py` — 新增 `PUSH_TARGET_URL` / `AD_URL_TEMPLATE` / `AD_URL_PROBE_TIMEOUT`
- `.env.example` — 同步
- `web/app.py` — 注册 `pushes_bp`
- `web/templates/layout.html` — 侧边栏加入口
- `web/templates/_medias_edit_modal.html` — 增「主站已适配语种」多选
- `web/static/medias.js` — 保存时携带 `ad_supported_langs`

---

## Task 1: 数据库迁移

**Files:**
- Create: `db/migrations/2026_04_18_add_push_management.sql`

- [ ] **Step 1: 写迁移 SQL**

```sql
-- db/migrations/2026_04_18_add_push_management.sql
-- 推送管理：push_logs 表 + products.ad_supported_langs + items.pushed_at / latest_push_id

-- 1. 产品级：已适配的投放语种（逗号分隔，如 "de,fr,ja"）
ALTER TABLE media_products
  ADD COLUMN ad_supported_langs VARCHAR(255) DEFAULT NULL AFTER source;

-- 2. 素材级：推送状态
ALTER TABLE media_items
  ADD COLUMN pushed_at DATETIME DEFAULT NULL,
  ADD COLUMN latest_push_id INT DEFAULT NULL,
  ADD KEY idx_pushed_at (pushed_at);

-- 3. 推送历史
CREATE TABLE IF NOT EXISTS media_push_logs (
  id INT AUTO_INCREMENT PRIMARY KEY,
  item_id INT NOT NULL,
  operator_user_id INT NOT NULL,
  status ENUM('success','failed') NOT NULL,
  request_payload JSON NOT NULL,
  response_body TEXT DEFAULT NULL,
  error_message TEXT DEFAULT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_item (item_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

- [ ] **Step 2: 在本机执行迁移**

Run: `mysql -u$DB_USER -p$DB_PASSWORD $DB_NAME < db/migrations/2026_04_18_add_push_management.sql`

Expected: 无报错，退出码 0。

- [ ] **Step 3: 验证 schema**

Run:
```bash
mysql -u$DB_USER -p$DB_PASSWORD $DB_NAME -e "SHOW COLUMNS FROM media_products LIKE 'ad_supported_langs'; SHOW COLUMNS FROM media_items LIKE 'pushed_at'; SHOW TABLES LIKE 'media_push_logs';"
```
Expected: 3 行非空输出，分别确认新字段和新表存在。

- [ ] **Step 4: 提交**

```bash
git add db/migrations/2026_04_18_add_push_management.sql
git commit -m "feat(push-management): 新增推送管理所需的数据库迁移"
```

---

## Task 2: `media_products.ad_supported_langs` DAO 扩展

**Files:**
- Modify: `appcore/medias.py`（`update_product` 的 `allowed` 集合；新增 `parse_ad_supported_langs`）
- Test: `tests/test_appcore_medias.py`（末尾追加两个用例）

- [ ] **Step 1: 写失败测试**

在 `tests/test_appcore_medias.py` 末尾追加：

```python
def test_update_product_ad_supported_langs(user_id):
    pid = medias.create_product(user_id, "适配语种测试")
    try:
        medias.update_product(pid, ad_supported_langs="de,fr,ja")
        p = medias.get_product(pid)
        assert p["ad_supported_langs"] == "de,fr,ja"
    finally:
        medias.soft_delete_product(pid)


def test_parse_ad_supported_langs():
    assert medias.parse_ad_supported_langs(None) == []
    assert medias.parse_ad_supported_langs("") == []
    assert medias.parse_ad_supported_langs("de,fr, ja") == ["de", "fr", "ja"]
    assert medias.parse_ad_supported_langs(" DE , FR ") == ["de", "fr"]
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_medias.py::test_parse_ad_supported_langs tests/test_appcore_medias.py::test_update_product_ad_supported_langs -v`
Expected: FAIL（`ad_supported_langs` 不在 `update_product` allowed 集合；`parse_ad_supported_langs` 函数不存在）

- [ ] **Step 3: 修改 `appcore/medias.py`**

将 `update_product` 的 `allowed` 集合从：
```python
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points",
               "product_code", "cover_object_key"}
```
扩展为：
```python
    allowed = {"name", "color_people", "source", "archived",
               "importance", "trend_score", "selling_points",
               "product_code", "cover_object_key",
               "ad_supported_langs"}
```

然后在 `# ---------- 产品 ----------` 区块末尾（`soft_delete_product` 之后）添加：

```python
def parse_ad_supported_langs(value: str | None) -> list[str]:
    """将 'de,fr,ja' 类逗号字符串规范化为 ['de','fr','ja']。空串 / None 返回 []。"""
    if not value:
        return []
    return [p.strip().lower() for p in value.split(",") if p.strip()]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_medias.py::test_parse_ad_supported_langs tests/test_appcore_medias.py::test_update_product_ad_supported_langs -v`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/medias.py tests/test_appcore_medias.py
git commit -m "feat(push-management): media_products.ad_supported_langs 读写 DAO"
```

---

## Task 3: `appcore/pushes.py` — 就绪判定与状态计算

**Files:**
- Create: `appcore/pushes.py`
- Create: `tests/test_appcore_pushes.py`

- [ ] **Step 1: 写失败测试（就绪判定）**

创建 `tests/test_appcore_pushes.py`：

```python
import pytest
from appcore import medias, pushes
from appcore.db import query_one, execute as db_execute


@pytest.fixture
def user_id():
    row = query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")
    assert row, "No users in DB"
    return row["id"]


@pytest.fixture
def product_with_item(user_id):
    pid = medias.create_product(user_id, "推送测试产品")
    medias.update_product(pid, product_code="push-test-prod", ad_supported_langs="de,fr")
    item_id = medias.create_item(
        pid, user_id, filename="demo.mp4", object_key="u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=12345, duration_seconds=10.5, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    yield pid, item_id
    medias.soft_delete_product(pid)


def test_compute_readiness_all_satisfied(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r == {"has_object": True, "has_cover": True, "has_copywriting": True, "lang_supported": True}


def test_compute_readiness_missing_cover(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["has_cover"] is False
    assert r["has_object"] is True
    assert r["has_copywriting"] is True
    assert r["lang_supported"] is True


def test_compute_readiness_lang_not_supported(product_with_item):
    pid, item_id = product_with_item
    medias.update_product(pid, ad_supported_langs="fr")  # 没有 de
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    r = pushes.compute_readiness(item, product)
    assert r["lang_supported"] is False
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_pushes.py::test_compute_readiness_all_satisfied -v`
Expected: FAIL（模块 `appcore.pushes` 不存在）

- [ ] **Step 3: 实现 `appcore/pushes.py` 的就绪判定**

创建 `appcore/pushes.py`：

```python
"""推送管理：就绪判定、状态计算、payload 组装、探活、日志写入、状态变更。"""
from __future__ import annotations

import json
import logging
from typing import Any

import requests

import config
from appcore import medias, tos_clients
from appcore.db import query, query_one, execute

log = logging.getLogger(__name__)


# ---------- 就绪判定 ----------

def compute_readiness(item: dict, product: dict) -> dict:
    """返回 4 项就绪布尔。调用方再据此判定 pushable。"""
    has_object = bool((item or {}).get("object_key"))
    has_cover = bool((item or {}).get("cover_object_key"))

    lang = (item or {}).get("lang") or "en"
    pid = (item or {}).get("product_id")
    has_copywriting = False
    if pid and lang:
        row = query_one(
            "SELECT 1 AS ok FROM media_copywritings "
            "WHERE product_id=%s AND lang=%s LIMIT 1",
            (pid, lang),
        )
        has_copywriting = bool(row)

    supported = medias.parse_ad_supported_langs((product or {}).get("ad_supported_langs"))
    lang_supported = lang in supported

    return {
        "has_object": has_object,
        "has_cover": has_cover,
        "has_copywriting": has_copywriting,
        "lang_supported": lang_supported,
    }


def is_ready(readiness: dict) -> bool:
    return all(readiness.values())
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_pushes.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push-management): 就绪判定函数 compute_readiness"
```

---

## Task 4: 状态计算函数

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_appcore_pushes.py` 末尾追加：

```python
def test_compute_status_pushed(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "pushed"


def test_compute_status_failed(product_with_item):
    pid, item_id = product_with_item
    log_id = db_execute(
        "INSERT INTO media_push_logs (item_id, operator_user_id, status, request_payload, error_message) "
        "VALUES (%s, %s, 'failed', %s, %s)",
        (item_id, 1, "{}", "timeout"),
    )
    db_execute("UPDATE media_items SET latest_push_id=%s WHERE id=%s", (log_id, item_id))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "failed"


def test_compute_status_pending(product_with_item):
    pid, item_id = product_with_item
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    # 新建的就绪 + 无推送记录 => pending
    assert pushes.compute_status(item, product) == "pending"


def test_compute_status_not_ready(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    assert pushes.compute_status(item, product) == "not_ready"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_pushes.py -v -k compute_status`
Expected: 4 FAIL（`compute_status` 不存在）

- [ ] **Step 3: 实现 `compute_status`**

追加到 `appcore/pushes.py`：

```python
# ---------- 状态计算 ----------

STATUS_PUSHED = "pushed"
STATUS_FAILED = "failed"
STATUS_PENDING = "pending"        # 就绪 + 未推送
STATUS_NOT_READY = "not_ready"    # 任一就绪条件不满足


def compute_status(item: dict, product: dict) -> str:
    if (item or {}).get("pushed_at"):
        return STATUS_PUSHED
    latest_id = (item or {}).get("latest_push_id")
    if latest_id:
        row = query_one(
            "SELECT status FROM media_push_logs WHERE id=%s", (latest_id,),
        )
        if (row or {}).get("status") == "failed":
            readiness = compute_readiness(item, product)
            return STATUS_FAILED if is_ready(readiness) else STATUS_NOT_READY
    readiness = compute_readiness(item, product)
    return STATUS_PENDING if is_ready(readiness) else STATUS_NOT_READY
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_pushes.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push-management): 状态计算 compute_status"
```

---

## Task 5: 探活函数

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_probe_ad_url_success(monkeypatch):
    class FakeResp:
        status_code = 200
    monkeypatch.setattr(
        "appcore.pushes.requests.head",
        lambda url, timeout, allow_redirects: FakeResp(),
    )
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is True
    assert err is None


def test_probe_ad_url_404(monkeypatch):
    class FakeResp:
        status_code = 404
    monkeypatch.setattr(
        "appcore.pushes.requests.head",
        lambda url, timeout, allow_redirects: FakeResp(),
    )
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is False
    assert "404" in err


def test_probe_ad_url_timeout(monkeypatch):
    def boom(url, timeout, allow_redirects):
        raise requests.Timeout("timed out")
    monkeypatch.setattr("appcore.pushes.requests.head", boom)
    import requests
    ok, err = pushes.probe_ad_url("https://example.com/x")
    assert ok is False
    assert "timed out" in err.lower() or "timeout" in err.lower()
```

在文件顶部 import 补上 `import requests`。

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_pushes.py -v -k probe_ad_url`
Expected: 3 FAIL（`probe_ad_url` 不存在）

- [ ] **Step 3: 实现 `probe_ad_url` + `build_product_link`**

追加到 `appcore/pushes.py`：

```python
# ---------- 链接模板与探活 ----------

def build_product_link(lang: str, product_code: str) -> str:
    tpl = config.AD_URL_TEMPLATE or ""
    return tpl.format(lang=lang, product_code=product_code)


def probe_ad_url(url: str) -> tuple[bool, str | None]:
    """HEAD 请求探活。返回 (ok, error_message)。"""
    if not url:
        return False, "empty url"
    try:
        resp = requests.head(
            url,
            timeout=config.AD_URL_PROBE_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        return False, str(e)
    if 200 <= resp.status_code < 400:
        return True, None
    return False, f"HTTP {resp.status_code}"
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_pushes.py -v`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push-management): 链接模板与 HEAD 探活"
```

---

## Task 6: payload 组装

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_build_item_payload_basic(monkeypatch, product_with_item):
    pid, item_id = product_with_item
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )
    monkeypatch.setattr(
        "appcore.pushes.medias.list_enabled_language_codes",
        lambda: ["en", "de", "fr", "es", "pt", "ja", "it"],
    )
    monkeypatch.setattr(config, "AD_URL_TEMPLATE",
                        "https://example.com/{lang}/products/{product_code}")

    item = medias.get_item(item_id)
    product = medias.get_product(pid)
    payload = pushes.build_item_payload(item, product)

    assert payload["mode"] == "create"
    assert payload["author"] == "蔡靖华"
    assert payload["push_admin"] == "蔡靖华"
    assert len(payload["videos"]) == 1
    assert payload["videos"][0]["url"].startswith("https://signed/")
    assert payload["videos"][0]["image_url"].startswith("https://signed/")
    # 6 条非英文链接（排除 en）
    assert len(payload["product_links"]) == 6
    for link in payload["product_links"]:
        assert "/en/" not in link
        assert product["product_code"] in link
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_pushes.py -v -k build_item_payload_basic`
Expected: FAIL

- [ ] **Step 3: 实现 `build_item_payload`**

追加到 `appcore/pushes.py`：

```python
# ---------- payload 组装 ----------

_FIXED_AUTHOR = "蔡靖华"
_FIXED_TEXTS = [{"title": "tiktok", "message": "tiktok", "description": "tiktok"}]


def build_item_payload(item: dict, product: dict) -> dict:
    """按设计文档组装单条 item 的推送 JSON。"""
    object_key = item.get("object_key")
    cover_object_key = item.get("cover_object_key")
    product_code = (product.get("product_code") or "").strip().lower()

    video = {
        "name": item.get("display_name") or item.get("filename") or "",
        "size": int(item.get("file_size") or 0),
        "width": 1080,
        "height": 1920,
        "url": tos_clients.generate_signed_media_download_url(object_key) if object_key else None,
        "image_url": (
            tos_clients.generate_signed_media_download_url(cover_object_key)
            if cover_object_key else None
        ),
    }

    enabled_langs = [c for c in medias.list_enabled_language_codes() if c != "en"]
    product_links = [build_product_link(lang, product_code) for lang in enabled_langs]

    return {
        "mode": "create",
        "product_name": product.get("name") or "",
        "texts": list(_FIXED_TEXTS),
        "product_links": product_links,
        "videos": [video],
        "source": 0,
        "level": int(product.get("importance") or 3),
        "author": _FIXED_AUTHOR,
        "push_admin": _FIXED_AUTHOR,
        "roas": 1.6,
        "platforms": ["tiktok"],
        "selling_point": product.get("selling_points") or "",
        "tags": [],
    }
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_pushes.py -v`
Expected: 11 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push-management): 单 item 级 payload 组装"
```

---

## Task 7: 推送日志与状态变更 DAO

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_record_success_and_reset(product_with_item):
    pid, item_id = product_with_item
    log_id = pushes.record_push_success(
        item_id=item_id, operator_user_id=1,
        payload={"a": 1}, response_body="ok",
    )
    assert log_id > 0
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None
    assert it["latest_push_id"] == log_id

    pushes.reset_push_state(item_id)
    it2 = medias.get_item(item_id)
    assert it2["pushed_at"] is None
    assert it2["latest_push_id"] is None
    # 历史保留
    row = query_one("SELECT COUNT(*) AS c FROM media_push_logs WHERE item_id=%s", (item_id,))
    assert row["c"] == 1


def test_record_failure_does_not_mark_pushed(product_with_item):
    pid, item_id = product_with_item
    log_id = pushes.record_push_failure(
        item_id=item_id, operator_user_id=1,
        payload={"a": 1}, error_message="boom", response_body=None,
    )
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] == log_id


def test_list_logs(product_with_item):
    pid, item_id = product_with_item
    pushes.record_push_failure(item_id=item_id, operator_user_id=1,
                               payload={}, error_message="e1", response_body=None)
    pushes.record_push_success(item_id=item_id, operator_user_id=1,
                               payload={}, response_body="ok")
    logs = pushes.list_item_logs(item_id)
    assert len(logs) == 2
    # 按时间倒序
    assert logs[0]["status"] == "success"
    assert logs[1]["status"] == "failed"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_pushes.py -v -k "record_ or list_logs"`
Expected: FAIL

- [ ] **Step 3: 实现日志写入与状态变更**

追加到 `appcore/pushes.py`：

```python
# ---------- 推送日志与状态变更 ----------

def record_push_success(item_id: int, operator_user_id: int,
                        payload: dict, response_body: str | None) -> int:
    log_id = execute(
        "INSERT INTO media_push_logs "
        "(item_id, operator_user_id, status, request_payload, response_body) "
        "VALUES (%s, %s, 'success', %s, %s)",
        (item_id, operator_user_id, json.dumps(payload, ensure_ascii=False), response_body),
    )
    execute(
        "UPDATE media_items SET pushed_at=NOW(), latest_push_id=%s WHERE id=%s",
        (log_id, item_id),
    )
    return log_id


def record_push_failure(item_id: int, operator_user_id: int,
                        payload: dict, error_message: str | None,
                        response_body: str | None) -> int:
    log_id = execute(
        "INSERT INTO media_push_logs "
        "(item_id, operator_user_id, status, request_payload, response_body, error_message) "
        "VALUES (%s, %s, 'failed', %s, %s, %s)",
        (item_id, operator_user_id,
         json.dumps(payload, ensure_ascii=False), response_body, error_message),
    )
    execute(
        "UPDATE media_items SET latest_push_id=%s WHERE id=%s",
        (log_id, item_id),
    )
    return log_id


def reset_push_state(item_id: int) -> None:
    execute(
        "UPDATE media_items SET pushed_at=NULL, latest_push_id=NULL WHERE id=%s",
        (item_id,),
    )


def list_item_logs(item_id: int, limit: int = 50) -> list[dict]:
    return query(
        "SELECT id, item_id, operator_user_id, status, request_payload, "
        "response_body, error_message, created_at "
        "FROM media_push_logs WHERE item_id=%s "
        "ORDER BY created_at DESC, id DESC LIMIT %s",
        (item_id, limit),
    )
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_pushes.py -v`
Expected: 14 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push-management): 推送日志写入与状态重置"
```

---

## Task 8: 列表查询 DAO

**Files:**
- Modify: `appcore/pushes.py`
- Modify: `tests/test_appcore_pushes.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_list_items_for_push_default(product_with_item):
    pid, item_id = product_with_item
    rows, total = pushes.list_items_for_push(offset=0, limit=20)
    assert total >= 1
    assert any(r["id"] == item_id for r in rows)


def test_list_items_for_push_filter_by_lang(product_with_item):
    pid, item_id = product_with_item
    rows, total = pushes.list_items_for_push(langs=["fr"], offset=0, limit=20)
    # 我们的 item 是 de，过滤 fr 应该不包含
    assert all(r["id"] != item_id for r in rows)


def test_list_items_for_push_filter_by_keyword(product_with_item):
    pid, item_id = product_with_item
    db_execute("UPDATE media_items SET display_name='UNIQUEMARKER' WHERE id=%s", (item_id,))
    rows, _ = pushes.list_items_for_push(keyword="UNIQUEMARKER", offset=0, limit=20)
    assert len(rows) == 1
    assert rows[0]["id"] == item_id
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_appcore_pushes.py -v -k list_items_for_push`
Expected: FAIL

- [ ] **Step 3: 实现 `list_items_for_push`**

追加到 `appcore/pushes.py`：

```python
# ---------- 列表查询 ----------

def list_items_for_push(
    langs: list[str] | None = None,
    keyword: str = "",
    product_term: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """不过滤状态（状态在内存里算）。返回 (items join product 的原始行, total)。"""
    where = ["i.deleted_at IS NULL", "p.deleted_at IS NULL"]
    args: list[Any] = []

    if langs:
        placeholders = ",".join(["%s"] * len(langs))
        where.append(f"i.lang IN ({placeholders})")
        args.extend(langs)
    if keyword:
        where.append("(i.display_name LIKE %s OR i.filename LIKE %s)")
        like = f"%{keyword}%"
        args.extend([like, like])
    if product_term:
        where.append("(p.name LIKE %s OR p.product_code LIKE %s)")
        like = f"%{product_term}%"
        args.extend([like, like])
    if date_from:
        where.append("i.updated_at >= %s")
        args.append(date_from)
    if date_to:
        where.append("i.updated_at <= %s")
        args.append(date_to)

    where_sql = " AND ".join(where)

    total_row = query_one(
        f"SELECT COUNT(*) AS c FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"WHERE {where_sql}",
        tuple(args),
    )
    total = int((total_row or {}).get("c") or 0)

    rows = query(
        f"SELECT i.*, p.name AS product_name, p.product_code, "
        f"       p.ad_supported_langs, p.selling_points, p.importance "
        f"FROM media_items i "
        f"JOIN media_products p ON p.id = i.product_id "
        f"WHERE {where_sql} "
        f"ORDER BY i.updated_at DESC, i.id DESC "
        f"LIMIT %s OFFSET %s",
        tuple(args + [limit, offset]),
    )
    return rows, total
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_appcore_pushes.py -v`
Expected: 17 passed

- [ ] **Step 5: 提交**

```bash
git add appcore/pushes.py tests/test_appcore_pushes.py
git commit -m "feat(push-management): 列表查询 list_items_for_push"
```

---

## Task 9: 配置项与 .env.example

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`（末尾追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_config.py` 末尾追加：

```python
def test_push_management_config_defaults(monkeypatch):
    # 清理可能存在的环境变量
    for k in ["PUSH_TARGET_URL", "AD_URL_TEMPLATE", "AD_URL_PROBE_TIMEOUT"]:
        monkeypatch.delenv(k, raising=False)
    import importlib, config as cfg
    importlib.reload(cfg)
    assert cfg.PUSH_TARGET_URL == ""
    assert "{lang}" in cfg.AD_URL_TEMPLATE
    assert "{product_code}" in cfg.AD_URL_TEMPLATE
    assert cfg.AD_URL_PROBE_TIMEOUT == 5


def test_push_management_config_override(monkeypatch):
    monkeypatch.setenv("PUSH_TARGET_URL", "http://10.0.0.1/api/push")
    monkeypatch.setenv("AD_URL_TEMPLATE", "https://x.com/{lang}/{product_code}")
    monkeypatch.setenv("AD_URL_PROBE_TIMEOUT", "8")
    import importlib, config as cfg
    importlib.reload(cfg)
    assert cfg.PUSH_TARGET_URL == "http://10.0.0.1/api/push"
    assert cfg.AD_URL_TEMPLATE == "https://x.com/{lang}/{product_code}"
    assert cfg.AD_URL_PROBE_TIMEOUT == 8
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_config.py::test_push_management_config_defaults tests/test_config.py::test_push_management_config_override -v`
Expected: FAIL（属性不存在）

- [ ] **Step 3: 修改 `config.py`**

在 `OPENAPI_MEDIA_API_KEY` 一行后追加：

```python
# 推送管理
PUSH_TARGET_URL = _env("PUSH_TARGET_URL", "")
AD_URL_TEMPLATE = _env("AD_URL_TEMPLATE",
                       "https://newjoyloo.com/{lang}/products/{product_code}-rjc")
AD_URL_PROBE_TIMEOUT = int(_env("AD_URL_PROBE_TIMEOUT", "5"))
```

在 `.env.example` 末尾追加：

```
# 推送管理
PUSH_TARGET_URL=
AD_URL_TEMPLATE=https://newjoyloo.com/{lang}/products/{product_code}-rjc
AD_URL_PROBE_TIMEOUT=5
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_config.py -v -k push_management`
Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
git add config.py .env.example tests/test_config.py
git commit -m "feat(push-management): 新增 3 项配置（PUSH_TARGET_URL / AD_URL_TEMPLATE / AD_URL_PROBE_TIMEOUT）"
```

---

## Task 10: 蓝图骨架 + 列表页路由

**Files:**
- Create: `web/routes/pushes.py`
- Modify: `web/app.py`（注册蓝图）
- Create: `tests/test_pushes_routes.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_pushes_routes.py`：

```python
def test_pushes_index_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    resp = client.get("/pushes", follow_redirects=False)
    # 未登录应该跳转到登录页
    assert resp.status_code in (301, 302)


def test_pushes_index_loads_for_admin(authed_client_no_db):
    resp = authed_client_no_db.get("/pushes")
    assert resp.status_code == 200
    assert b"\xe6\x8e\xa8\xe9\x80\x81\xe7\xae\xa1\xe7\x90\x86" in resp.data  # "推送管理"


def test_pushes_api_items_requires_login():
    from web.app import create_app
    app = create_app()
    client = app.test_client()
    resp = client.get("/pushes/api/items")
    assert resp.status_code in (302, 401)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_pushes_routes.py -v`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 创建蓝图骨架**

创建 `web/routes/pushes.py`：

```python
"""推送管理 Blueprint。列表 + 推送工作流 API。"""
from __future__ import annotations

import logging
from functools import wraps

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

import config

log = logging.getLogger(__name__)
bp = Blueprint("pushes", __name__, url_prefix="/pushes")


def _is_admin() -> bool:
    return getattr(current_user, "role", "") == "admin"


def admin_required(fn):
    @wraps(fn)
    def _wrap(*a, **kw):
        if not _is_admin():
            return jsonify({"error": "仅管理员可操作"}), 403
        return fn(*a, **kw)
    return _wrap


@bp.route("/")
@login_required
def index():
    return render_template(
        "pushes_list.html",
        is_admin=_is_admin(),
        push_target_configured=bool((config.PUSH_TARGET_URL or "").strip()),
    )
```

创建最小 `web/templates/pushes_list.html`（后续 Task 填充内容）：

```html
{% extends "layout.html" %}
{% block title %}推送管理{% endblock %}
{% block content %}
<div class="page-header"><h1>推送管理</h1></div>
<div id="pushes-root"></div>
{% endblock %}
```

修改 `web/app.py` 导入并注册：

在 `from web.routes.openapi_materials import bp as openapi_materials_bp` 下一行插入：
```python
from web.routes.pushes import bp as pushes_bp
```

在 `app.register_blueprint(openapi_materials_bp)` 下一行插入：
```python
    app.register_blueprint(pushes_bp)
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_pushes_routes.py -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add web/routes/pushes.py web/templates/pushes_list.html web/app.py tests/test_pushes_routes.py
git commit -m "feat(push-management): 蓝图骨架与最小列表页"
```

---

## Task 11: 列表 JSON API

**Files:**
- Modify: `web/routes/pushes.py`
- Modify: `tests/test_pushes_routes.py`

- [ ] **Step 1: 追加失败测试**

```python
def test_pushes_api_items_returns_list(logged_in_client):
    resp = logged_in_client.get("/pushes/api/items?page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "items" in data
    assert "total" in data
    assert "page" in data
    assert data["page"] == 1


def test_pushes_api_items_filter_status(logged_in_client):
    resp = logged_in_client.get("/pushes/api/items?status=pending&page=1")
    assert resp.status_code == 200
    data = resp.get_json()
    # 所有条目状态都应是 pending
    for it in data["items"]:
        assert it["status"] == "pending"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_pushes_routes.py -v -k api_items`
Expected: FAIL（路由不存在）

- [ ] **Step 3: 实现 `/api/items`**

追加到 `web/routes/pushes.py`：

```python
from appcore import medias, pushes, tos_clients

_PAGE_SIZE_DEFAULT = 20


def _serialize_row(row: dict) -> dict:
    item_shape = dict(row)
    product_shape = {
        "id": row.get("product_id"),
        "name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "ad_supported_langs": row.get("ad_supported_langs"),
        "selling_points": row.get("selling_points"),
        "importance": row.get("importance"),
    }
    readiness = pushes.compute_readiness(item_shape, product_shape)
    status = pushes.compute_status(item_shape, product_shape)
    cover_key = row.get("cover_object_key")
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "product_name": row.get("product_name"),
        "product_code": row.get("product_code"),
        "lang": row.get("lang"),
        "filename": row.get("filename"),
        "display_name": row.get("display_name"),
        "duration_seconds": row.get("duration_seconds"),
        "file_size": row.get("file_size"),
        "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "pushed_at": row["pushed_at"].isoformat() if row.get("pushed_at") else None,
        "status": status,
        "readiness": readiness,
        "cover_url": (
            tos_clients.generate_signed_media_download_url(cover_key) if cover_key else None
        ),
    }


@bp.route("/api/items", methods=["GET"])
@login_required
def api_list():
    status_filter = request.args.getlist("status")  # 可多值
    langs = [l for l in request.args.getlist("lang") if l]
    keyword = (request.args.get("keyword") or "").strip()
    product_term = (request.args.get("product") or "").strip()
    date_from = (request.args.get("date_from") or "").strip() or None
    date_to = (request.args.get("date_to") or "").strip() or None

    page = max(1, int(request.args.get("page") or 1))
    limit = _PAGE_SIZE_DEFAULT

    # 先按 DB 可过滤的字段（语种/关键词/产品/时间）查出；状态过滤在内存里做
    rows, total = pushes.list_items_for_push(
        langs=langs or None,
        keyword=keyword,
        product_term=product_term,
        date_from=date_from,
        date_to=date_to,
        offset=(page - 1) * limit,
        limit=limit,
    )
    items = [_serialize_row(r) for r in rows]
    if status_filter:
        items = [it for it in items if it["status"] in status_filter]

    return jsonify({
        "items": items,
        "total": total,
        "page": page,
        "page_size": limit,
    })
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_pushes_routes.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add web/routes/pushes.py tests/test_pushes_routes.py
git commit -m "feat(push-management): /pushes/api/items 列表 JSON API"
```

---

## Task 12: payload / mark-pushed / mark-failed / reset / logs API

**Files:**
- Modify: `web/routes/pushes.py`
- Modify: `tests/test_pushes_routes.py`

- [ ] **Step 1: 追加失败测试**

```python
import pytest


@pytest.fixture
def seeded_item(user_id_int):
    from appcore import medias
    pid = medias.create_product(user_id_int, "路由测试产品")
    medias.update_product(pid, product_code="route-test", ad_supported_langs="de")
    item_id = medias.create_item(
        pid, user_id_int, "demo.mp4", "u/1/m/1/demo.mp4",
        cover_object_key="u/1/m/1/cover.jpg",
        file_size=100, duration_seconds=5.0, lang="de",
    )
    medias.replace_copywritings(pid, [{"title": "T", "body": "B"}], lang="de")
    yield pid, item_id
    medias.soft_delete_product(pid)


@pytest.fixture
def user_id_int():
    from appcore.db import query_one
    return int(query_one("SELECT id FROM users ORDER BY id ASC LIMIT 1")["id"])


def test_payload_requires_admin(authed_user_client_no_db):
    resp = authed_user_client_no_db.get("/pushes/api/items/99999/payload")
    assert resp.status_code == 403


def test_payload_rejects_already_pushed(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW() WHERE id=%s", (item_id,))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 409


def test_payload_rejects_not_ready(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET cover_object_key=NULL WHERE id=%s", (item_id,))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "not_ready"
    assert "has_cover" in data["missing"]


def test_payload_rejects_probe_fail(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (False, "HTTP 404"))
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "link_not_adapted"


def test_payload_success(logged_in_client, seeded_item, monkeypatch):
    pid, item_id = seeded_item
    monkeypatch.setattr("appcore.pushes.probe_ad_url", lambda url: (True, None))
    monkeypatch.setattr(
        "appcore.pushes.tos_clients.generate_signed_media_download_url",
        lambda key: f"https://signed/{key}",
    )
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/payload")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "payload" in data
    assert "push_url" in data
    assert data["payload"]["videos"][0]["url"].startswith("https://signed/")


def test_mark_pushed_updates_state(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(
        f"/pushes/api/items/{item_id}/mark-pushed",
        json={"request_payload": {"a": 1}, "response_body": "ok"},
    )
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is not None


def test_mark_failed_keeps_pushed_at_null(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    resp = logged_in_client.post(
        f"/pushes/api/items/{item_id}/mark-failed",
        json={"request_payload": {"a": 1}, "error_message": "boom"},
    )
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is not None


def test_reset_clears_state(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore.db import execute as db_execute
    db_execute("UPDATE media_items SET pushed_at=NOW(), latest_push_id=1 WHERE id=%s", (item_id,))
    resp = logged_in_client.post(f"/pushes/api/items/{item_id}/reset")
    assert resp.status_code == 204
    from appcore import medias
    it = medias.get_item(item_id)
    assert it["pushed_at"] is None
    assert it["latest_push_id"] is None


def test_logs_returns_history(logged_in_client, seeded_item):
    pid, item_id = seeded_item
    from appcore import pushes
    pushes.record_push_failure(item_id=item_id, operator_user_id=1,
                               payload={}, error_message="e", response_body=None)
    resp = logged_in_client.get(f"/pushes/api/items/{item_id}/logs")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["logs"]) >= 1
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/test_pushes_routes.py -v`
Expected: 新用例 FAIL（路由不存在）

- [ ] **Step 3: 实现剩余路由**

追加到 `web/routes/pushes.py`：

```python
@bp.route("/api/items/<int:item_id>/payload", methods=["GET"])
@login_required
@admin_required
def api_build_payload(item_id: int):
    item = medias.get_item(item_id)
    if not item:
        return jsonify({"error": "item_not_found"}), 404
    product = medias.get_product(item["product_id"])
    if not product:
        return jsonify({"error": "product_not_found"}), 404
    if item.get("pushed_at"):
        return jsonify({"error": "already_pushed"}), 409

    readiness = pushes.compute_readiness(item, product)
    if not pushes.is_ready(readiness):
        missing = [k for k, v in readiness.items() if not v]
        return jsonify({"error": "not_ready", "missing": missing}), 400

    lang = item.get("lang") or "en"
    product_code = (product.get("product_code") or "").strip().lower()
    ad_url = pushes.build_product_link(lang, product_code)
    ok, err = pushes.probe_ad_url(ad_url)
    if not ok:
        return jsonify({
            "error": "link_not_adapted",
            "url": ad_url,
            "detail": err,
        }), 400

    payload = pushes.build_item_payload(item, product)
    return jsonify({
        "payload": payload,
        "push_url": config.PUSH_TARGET_URL,
    })


@bp.route("/api/items/<int:item_id>/mark-pushed", methods=["POST"])
@login_required
@admin_required
def api_mark_pushed(item_id: int):
    body = request.get_json(silent=True) or {}
    payload = body.get("request_payload") or {}
    response_body = body.get("response_body")
    pushes.record_push_success(
        item_id=item_id,
        operator_user_id=current_user.id,
        payload=payload,
        response_body=response_body,
    )
    return ("", 204)


@bp.route("/api/items/<int:item_id>/mark-failed", methods=["POST"])
@login_required
@admin_required
def api_mark_failed(item_id: int):
    body = request.get_json(silent=True) or {}
    payload = body.get("request_payload") or {}
    pushes.record_push_failure(
        item_id=item_id,
        operator_user_id=current_user.id,
        payload=payload,
        error_message=body.get("error_message"),
        response_body=body.get("response_body"),
    )
    return ("", 204)


@bp.route("/api/items/<int:item_id>/reset", methods=["POST"])
@login_required
@admin_required
def api_reset(item_id: int):
    pushes.reset_push_state(item_id)
    return ("", 204)


@bp.route("/api/items/<int:item_id>/logs", methods=["GET"])
@login_required
def api_logs(item_id: int):
    logs = pushes.list_item_logs(item_id)
    serialized = []
    for row in logs:
        serialized.append({
            "id": row["id"],
            "operator_user_id": row["operator_user_id"],
            "status": row["status"],
            "request_payload": row["request_payload"],
            "response_body": row["response_body"],
            "error_message": row["error_message"],
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        })
    return jsonify({"logs": serialized})
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/test_pushes_routes.py -v`
Expected: 所有用例 passed

- [ ] **Step 5: 提交**

```bash
git add web/routes/pushes.py tests/test_pushes_routes.py
git commit -m "feat(push-management): payload/mark/reset/logs 工作流 API"
```

---

## Task 13: 前端列表页模板

**Files:**
- Modify: `web/templates/pushes_list.html`

- [ ] **Step 1: 写完整模板**

覆盖 `web/templates/pushes_list.html`：

```html
{% extends "layout.html" %}
{% block title %}推送管理{% endblock %}
{% block content %}
<div class="page-header">
  <h1>🚀 推送管理</h1>
  {% if not push_target_configured %}
  <div class="warning-banner">
    推送目标 URL 未配置（PUSH_TARGET_URL），推送按钮已禁用。请联系管理员在 .env 中配置后重启。
  </div>
  {% endif %}
</div>

<div class="push-toolbar">
  <div class="filter-group">
    <label>状态</label>
    <select id="f-status" multiple size="4">
      <option value="not_ready">未就绪</option>
      <option value="pending" selected>待推送</option>
      <option value="pushed">已推送</option>
      <option value="failed">推送失败</option>
    </select>
  </div>
  <div class="filter-group">
    <label>语种</label>
    <select id="f-lang" multiple size="4"></select>
  </div>
  <div class="filter-group">
    <label>产品</label>
    <input id="f-product" type="text" placeholder="产品名或 code" />
  </div>
  <div class="filter-group">
    <label>关键词</label>
    <input id="f-keyword" type="text" placeholder="素材文件名" />
  </div>
  <div class="filter-group">
    <label>更新时间</label>
    <input id="f-date-from" type="date" />
    <span>至</span>
    <input id="f-date-to" type="date" />
  </div>
  <div class="filter-actions">
    <button id="btn-apply" type="button">筛选</button>
    <button id="btn-reset" type="button">重置</button>
  </div>
</div>

<table class="push-table">
  <thead>
    <tr>
      <th>缩略图</th>
      <th>产品</th>
      <th>素材</th>
      <th>语种</th>
      <th>就绪</th>
      <th>状态</th>
      <th>更新时间</th>
      {% if is_admin %}<th>操作</th>{% endif %}
    </tr>
  </thead>
  <tbody id="push-tbody"><tr><td colspan="8">加载中…</td></tr></tbody>
</table>

<div class="pagination" id="push-pagination"></div>

<div id="push-log-drawer" class="drawer" hidden>
  <div class="drawer-inner">
    <h3>推送历史</h3>
    <button class="drawer-close" id="drawer-close">×</button>
    <div id="drawer-content"></div>
  </div>
</div>

<script>
  window.PUSH_IS_ADMIN = {{ 'true' if is_admin else 'false' }};
  window.PUSH_TARGET_CONFIGURED = {{ 'true' if push_target_configured else 'false' }};
</script>
<script src="/static/pushes.js"></script>
{% endblock %}
```

- [ ] **Step 2: 手工验证模板渲染**

Run: 启动开发服务器并访问 `/pushes`。
```bash
python main.py &
curl -s -c /tmp/cook -b /tmp/cook -XPOST -F 'username=admin' -F 'password=***' http://127.0.0.1:5000/login
curl -s -b /tmp/cook http://127.0.0.1:5000/pushes | grep -c "推送管理"
```
Expected: 输出 `>= 1`，HTML 含"推送管理"标题。

- [ ] **Step 3: 提交**

```bash
git add web/templates/pushes_list.html
git commit -m "feat(push-management): 列表页模板骨架"
```

---

## Task 14: 前端 JS —— 列表加载与筛选

**Files:**
- Create: `web/static/pushes.js`

- [ ] **Step 1: 实现 pushes.js**

创建 `web/static/pushes.js`：

```javascript
(function () {
  const STATUS_LABELS = {
    not_ready: { text: '未就绪', cls: 'badge-gray' },
    pending:   { text: '待推送', cls: 'badge-blue' },
    pushed:    { text: '已推送', cls: 'badge-green' },
    failed:    { text: '推送失败', cls: 'badge-red' },
  };
  const READINESS_LABELS = {
    has_object: '素材',
    has_cover: '封面',
    has_copywriting: '文案',
    lang_supported: '链接适配',
  };

  const state = { page: 1, pageSize: 20, total: 0 };

  async function fetchJSON(url, options) {
    const resp = await fetch(url, options);
    if (!resp.ok && resp.status !== 204) {
      const body = await resp.text();
      throw Object.assign(new Error(`HTTP ${resp.status}`), {
        status: resp.status, body,
      });
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  async function loadLanguages() {
    try {
      const data = await fetchJSON('/medias/api/languages');
      const sel = document.getElementById('f-lang');
      sel.innerHTML = '';
      (data.languages || []).forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.code;
        opt.textContent = `${l.name_zh} (${l.code})`;
        sel.appendChild(opt);
      });
    } catch (e) {
      console.warn('load languages failed', e);
    }
  }

  function buildQuery() {
    const params = new URLSearchParams();
    const statusSel = document.getElementById('f-status');
    [...statusSel.selectedOptions].forEach(o => params.append('status', o.value));
    const langSel = document.getElementById('f-lang');
    [...langSel.selectedOptions].forEach(o => params.append('lang', o.value));
    const product = document.getElementById('f-product').value.trim();
    if (product) params.set('product', product);
    const keyword = document.getElementById('f-keyword').value.trim();
    if (keyword) params.set('keyword', keyword);
    const df = document.getElementById('f-date-from').value;
    if (df) params.set('date_from', df);
    const dt = document.getElementById('f-date-to').value;
    if (dt) params.set('date_to', dt);
    params.set('page', String(state.page));
    return params.toString();
  }

  function renderReadinessDots(readiness) {
    return Object.entries(READINESS_LABELS).map(([key, label]) => {
      const ok = readiness[key];
      return `<span class="dot ${ok ? 'dot-green' : 'dot-gray'}" title="${label}${ok ? '✓' : '✗'}"></span>`;
    }).join('');
  }

  function renderStatusBadge(status) {
    const s = STATUS_LABELS[status] || { text: status, cls: '' };
    return `<span class="badge ${s.cls}">${s.text}</span>`;
  }

  function renderActionCell(it) {
    if (!window.PUSH_IS_ADMIN) return '';
    const disabled = !window.PUSH_TARGET_CONFIGURED;
    if (it.status === 'pushed') {
      const date = (it.pushed_at || '').slice(0, 10);
      return `<span class="pushed-text">✓ 已推送 ${date}</span>
              <div class="action-menu">
                <button class="btn-mini" data-action="view-logs" data-id="${it.id}">历史</button>
                <button class="btn-mini" data-action="reset" data-id="${it.id}">重置</button>
              </div>`;
    }
    if (it.status === 'not_ready') {
      const missing = Object.entries(it.readiness)
        .filter(([, v]) => !v).map(([k]) => READINESS_LABELS[k]).join(' / ');
      return `<button class="btn-push" disabled title="缺少：${missing}">推送</button>`;
    }
    const label = it.status === 'failed' ? '× 失败，重试' : '推送';
    return `<button class="btn-push ${it.status === 'failed' ? 'btn-failed' : ''}" 
                    data-action="push" data-id="${it.id}" ${disabled ? 'disabled' : ''}>${label}</button>`;
  }

  function renderRow(it) {
    const thumb = it.cover_url
      ? `<img class="thumb" src="${it.cover_url}" alt="">`
      : `<div class="thumb thumb-empty"></div>`;
    return `<tr data-id="${it.id}">
      <td>${thumb}</td>
      <td>
        <div class="product-name">${it.product_name || ''}</div>
        <div class="product-code">${it.product_code || ''}</div>
      </td>
      <td>
        <div class="item-name">${it.display_name || it.filename || ''}</div>
        <div class="item-meta">${it.duration_seconds ? it.duration_seconds.toFixed(1) + 's' : ''} · ${(it.file_size || 0).toLocaleString()} B</div>
      </td>
      <td><span class="lang-pill">${it.lang || ''}</span></td>
      <td class="dots">${renderReadinessDots(it.readiness)}</td>
      <td>${renderStatusBadge(it.status)}</td>
      <td class="time">${(it.updated_at || '').replace('T', ' ').slice(0, 16)}</td>
      ${window.PUSH_IS_ADMIN ? `<td>${renderActionCell(it)}</td>` : ''}
    </tr>`;
  }

  async function load() {
    const tbody = document.getElementById('push-tbody');
    tbody.innerHTML = `<tr><td colspan="8">加载中…</td></tr>`;
    try {
      const data = await fetchJSON('/pushes/api/items?' + buildQuery());
      state.total = data.total;
      if (!data.items.length) {
        tbody.innerHTML = `<tr><td colspan="8">无数据</td></tr>`;
      } else {
        tbody.innerHTML = data.items.map(renderRow).join('');
      }
      renderPagination();
    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="8">加载失败: ${e.message}</td></tr>`;
    }
  }

  function renderPagination() {
    const box = document.getElementById('push-pagination');
    const totalPages = Math.ceil(state.total / state.pageSize) || 1;
    const parts = [`共 ${state.total} 条`];
    for (let p = 1; p <= totalPages; p++) {
      if (p === state.page) parts.push(`<strong>${p}</strong>`);
      else parts.push(`<a href="#" data-page="${p}">${p}</a>`);
    }
    box.innerHTML = parts.join(' ');
    box.querySelectorAll('a').forEach(a => {
      a.addEventListener('click', ev => {
        ev.preventDefault();
        state.page = Number(ev.target.getAttribute('data-page'));
        load();
      });
    });
  }

  function bindFilters() {
    document.getElementById('btn-apply').addEventListener('click', () => {
      state.page = 1; load();
    });
    document.getElementById('btn-reset').addEventListener('click', () => {
      document.querySelectorAll('.push-toolbar input').forEach(i => (i.value = ''));
      const statusSel = document.getElementById('f-status');
      [...statusSel.options].forEach(o => { o.selected = o.value === 'pending'; });
      document.getElementById('f-lang').querySelectorAll('option').forEach(o => (o.selected = false));
      state.page = 1; load();
    });
  }

  window._pushesLoad = load;
  loadLanguages().then(() => { bindFilters(); load(); });
})();
```

- [ ] **Step 2: 手工验证列表加载**

浏览器打开 `/pushes`：表格应显示默认"待推送"状态下的条目，筛选和重置按钮工作。

- [ ] **Step 3: 提交**

```bash
git add web/static/pushes.js
git commit -m "feat(push-management): 前端列表加载、筛选、分页"
```

---

## Task 15: 前端 JS —— 推送按钮交互

**Files:**
- Modify: `web/static/pushes.js`

- [ ] **Step 1: 在 `pushes.js` 底部加入操作委托**

在 `window._pushesLoad = load;` 那行**上方**插入：

```javascript
  async function doPush(itemId, btn) {
    btn.disabled = true;
    btn.textContent = '推送中…';
    let payloadData;
    try {
      const data = await fetchJSON(`/pushes/api/items/${itemId}/payload`);
      payloadData = data.payload;
      const pushUrl = data.push_url;
      if (!pushUrl) throw new Error('推送目标未配置');

      let resp;
      try {
        resp = await fetch(pushUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payloadData),
        });
      } catch (e) {
        await fetchJSON(`/pushes/api/items/${itemId}/mark-failed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_payload: payloadData,
            error_message: `网络或 CORS 失败: ${e.message}`,
          }),
        });
        alert(`推送失败（网络/CORS）：${e.message}`);
        return load();
      }
      const body = await resp.text();
      if (resp.ok) {
        await fetchJSON(`/pushes/api/items/${itemId}/mark-pushed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_payload: payloadData, response_body: body }),
        });
        alert('推送成功');
      } else {
        await fetchJSON(`/pushes/api/items/${itemId}/mark-failed`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            request_payload: payloadData,
            response_body: body,
            error_message: `HTTP ${resp.status}`,
          }),
        });
        alert(`推送失败：HTTP ${resp.status}\n${body.slice(0, 200)}`);
      }
    } catch (e) {
      if (e.status === 400 || e.status === 409) {
        let info = '';
        try { info = JSON.parse(e.body).error || ''; } catch (_) {}
        alert(`无法推送：${info || e.message}`);
      } else {
        alert(`推送失败：${e.message}`);
      }
    } finally {
      await load();
    }
  }

  async function resetPush(itemId) {
    if (!confirm('确认重置这条素材的推送状态？之前的历史记录会保留。')) return;
    await fetchJSON(`/pushes/api/items/${itemId}/reset`, { method: 'POST' });
    await load();
  }

  async function viewLogs(itemId) {
    const drawer = document.getElementById('push-log-drawer');
    const content = document.getElementById('drawer-content');
    content.textContent = '加载中…';
    drawer.hidden = false;
    try {
      const data = await fetchJSON(`/pushes/api/items/${itemId}/logs`);
      if (!data.logs.length) {
        content.innerHTML = '<p>暂无记录</p>';
      } else {
        content.innerHTML = data.logs.map(l => `
          <div class="log-row">
            <div><strong>${l.status === 'success' ? '✓ 成功' : '✗ 失败'}</strong>
                 <span class="time">${l.created_at}</span></div>
            ${l.error_message ? `<div class="err">${l.error_message}</div>` : ''}
            ${l.response_body ? `<pre>${l.response_body.slice(0, 500)}</pre>` : ''}
          </div>
        `).join('');
      }
    } catch (e) {
      content.textContent = '加载失败: ' + e.message;
    }
  }

  document.getElementById('push-tbody').addEventListener('click', ev => {
    const btn = ev.target.closest('button[data-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-action');
    const id = Number(btn.getAttribute('data-id'));
    if (action === 'push') doPush(id, btn);
    else if (action === 'reset') resetPush(id);
    else if (action === 'view-logs') viewLogs(id);
  });

  document.getElementById('drawer-close').addEventListener('click', () => {
    document.getElementById('push-log-drawer').hidden = true;
  });
```

- [ ] **Step 2: 手工验证**

在浏览器开 DevTools Network：
- 点"推送"按钮 → 应看到 `GET /payload` → `POST` 到内网 → `POST /mark-pushed` 或 `/mark-failed`
- 点"重置" → `POST /reset` → 列表刷新
- 点"历史" → `GET /logs` → 抽屉显示记录

- [ ] **Step 3: 提交**

```bash
git add web/static/pushes.js
git commit -m "feat(push-management): 推送按钮、重置、查看历史交互"
```

---

## Task 16: 素材编辑弹窗新增「主站已适配语种」

**Files:**
- Modify: `web/templates/_medias_edit_modal.html`
- Modify: `web/static/medias.js`
- Modify: `web/routes/medias.py`（确保 `PUT /api/products/<id>` 接收并写入 `ad_supported_langs`）

- [ ] **Step 1: 在 `_medias_edit_modal.html` 的产品基础字段区插入多选**

定位到产品"来源"字段 input 之后，插入：

```html
<div class="field field-ad-supported">
  <label>主站已适配语种</label>
  <div id="ad-supported-langs-box" class="lang-checkbox-group">
    <!-- 动态填充：由 medias.js 根据 /medias/api/languages 生成 checkbox -->
  </div>
  <p class="field-help">勾选的语种将在推送时视为"链接已适配"。</p>
</div>
```

- [ ] **Step 2: 在 `web/static/medias.js` 的编辑弹窗打开/保存逻辑处理该字段**

找到打开编辑弹窗的函数（`openEditModal` 或类似），在已渲染产品字段的位置后追加：

```javascript
// 动态渲染"主站已适配语种"多选
async function renderAdSupportedLangsBox(selected) {
  const box = document.getElementById('ad-supported-langs-box');
  if (!box) return;
  try {
    const data = await fetchJSON('/medias/api/languages');
    const langs = (data.languages || []).filter(l => l.code !== 'en');
    const selectedSet = new Set((selected || '').split(',').map(s => s.trim()).filter(Boolean));
    box.innerHTML = langs.map(l => `
      <label class="lang-checkbox">
        <input type="checkbox" name="ad_supported_langs" value="${l.code}"
               ${selectedSet.has(l.code) ? 'checked' : ''}/>
        ${l.name_zh}
      </label>
    `).join('');
  } catch (e) {
    box.innerHTML = '<span class="err">加载语种失败</span>';
  }
}
```

在打开弹窗时调用（通常在加载产品详情后）：

```javascript
renderAdSupportedLangsBox(product.ad_supported_langs);
```

在保存函数中，收集勾选值并随 PUT 一起提交：

```javascript
const checkedLangs = [...document.querySelectorAll('input[name="ad_supported_langs"]:checked')]
  .map(i => i.value).join(',');
payload.ad_supported_langs = checkedLangs;
```

- [ ] **Step 3: 确保 `web/routes/medias.py` 的 `PUT /api/products/<id>` 接收该字段**

查看 `update` 或 `put_product` 处理函数，确保调用 `medias.update_product(pid, ...)` 时包含 `ad_supported_langs=body.get("ad_supported_langs")`。Task 2 已扩展 `update_product` 的 `allowed` 集合，因此只需路由处理层把 body 里的字段透传即可。

若现有代码用 `**body` 方式批量透传，无需改动。若白名单了字段，需要在白名单里追加 `ad_supported_langs`。

- [ ] **Step 4: 手工验证**

1. 打开 `/medias` 列表，编辑某个产品
2. 能看到"主站已适配语种"6 个复选框
3. 勾选 DE / FR → 保存
4. 重新打开编辑弹窗 → 应保持勾选状态
5. 查 DB：`SELECT ad_supported_langs FROM media_products WHERE id=<pid>;` 应为 `"de,fr"`

- [ ] **Step 5: 提交**

```bash
git add web/templates/_medias_edit_modal.html web/static/medias.js web/routes/medias.py
git commit -m "feat(push-management): 素材编辑弹窗新增主站已适配语种多选"
```

---

## Task 17: 侧边栏加入口

**Files:**
- Modify: `web/templates/layout.html`

- [ ] **Step 1: 定位并插入菜单项**

在 `layout.html` 中搜索现有的「📦 素材管理」链接（类似 `<a href="/medias"`），**在其紧随其后的位置**插入：

```html
<a href="/pushes" {% if request.path.startswith('/pushes') %}class="active"{% endif %}>
  <span class="nav-icon">🚀</span> 推送管理
</a>
```

- [ ] **Step 2: 手工验证**

浏览器打开任意页面 → 侧边栏应能看到「🚀 推送管理」紧跟在「📦 素材管理」后；点击跳转到 `/pushes`，菜单项高亮。

- [ ] **Step 3: 提交**

```bash
git add web/templates/layout.html
git commit -m "feat(push-management): 侧边栏加入推送管理入口"
```

---

## Task 18: 列表页最小样式

**Files:**
- Create: `web/static/pushes.css`
- Modify: `web/templates/pushes_list.html`（引入 css）

- [ ] **Step 1: 创建 `pushes.css`**

```css
.page-header h1 { margin: 0 0 12px; }
.warning-banner {
  background: #fff8e1; border: 1px solid #ffd54f;
  padding: 8px 12px; border-radius: 4px; margin: 8px 0;
}
.push-toolbar {
  display: flex; gap: 16px; flex-wrap: wrap;
  padding: 12px; background: #f7f7fa; border-radius: 6px; margin-bottom: 12px;
}
.filter-group { display: flex; flex-direction: column; gap: 4px; }
.filter-group label { font-size: 12px; color: #666; }
.filter-group select, .filter-group input { min-width: 140px; padding: 4px 8px; }
.filter-actions { align-self: flex-end; display: flex; gap: 8px; }

.push-table { width: 100%; border-collapse: collapse; }
.push-table th, .push-table td {
  padding: 10px 8px; border-bottom: 1px solid #eee; text-align: left;
  vertical-align: middle;
}
.thumb { width: 80px; height: 80px; object-fit: cover; border-radius: 6px; }
.thumb-empty { background: #e0e0e0; }

.product-name { font-weight: 500; }
.product-code, .item-meta { color: #888; font-size: 12px; }

.lang-pill {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  background: #e3f2fd; color: #1976d2; font-size: 12px;
}

.dots { display: flex; gap: 4px; }
.dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; }
.dot-green { background: #43a047; }
.dot-gray  { background: #bdbdbd; }

.badge {
  display: inline-block; padding: 2px 10px; border-radius: 10px;
  font-size: 12px; white-space: nowrap;
}
.badge-gray  { background: #eee;       color: #555; }
.badge-blue  { background: #e3f2fd;    color: #1565c0; }
.badge-green { background: #e8f5e9;    color: #2e7d32; }
.badge-red   { background: #ffebee;    color: #c62828; }

.btn-push {
  padding: 6px 12px; border: none; border-radius: 4px;
  background: #1976d2; color: white; cursor: pointer;
}
.btn-push:disabled { background: #bdbdbd; cursor: not-allowed; }
.btn-failed { background: #c62828; }
.pushed-text { color: #2e7d32; font-weight: 500; }
.btn-mini {
  padding: 2px 8px; font-size: 12px; margin-left: 4px;
  background: white; border: 1px solid #ccc; border-radius: 3px; cursor: pointer;
}

.pagination { margin: 16px 0; text-align: center; }
.pagination a, .pagination strong {
  display: inline-block; padding: 4px 10px; margin: 0 2px;
  border-radius: 3px; text-decoration: none;
}
.pagination strong { background: #1976d2; color: white; }

.drawer {
  position: fixed; right: 0; top: 0; bottom: 0; width: 480px;
  background: white; box-shadow: -2px 0 8px rgba(0,0,0,0.15);
  overflow-y: auto; z-index: 1000;
}
.drawer-inner { padding: 20px; }
.drawer-close { position: absolute; right: 12px; top: 12px; border: none; background: none; font-size: 24px; cursor: pointer; }
.log-row { border-bottom: 1px solid #eee; padding: 10px 0; }
.log-row .err { color: #c62828; margin: 4px 0; }
.log-row pre { background: #f7f7fa; padding: 8px; font-size: 12px; white-space: pre-wrap; }

.lang-checkbox-group { display: flex; flex-wrap: wrap; gap: 12px; }
.lang-checkbox { display: inline-flex; gap: 4px; align-items: center; }
```

- [ ] **Step 2: 在 `pushes_list.html` 的 `<script src="/static/pushes.js">` 前插入**

```html
<link rel="stylesheet" href="/static/pushes.css">
```

- [ ] **Step 3: 手工验证**

打开 `/pushes`：表格、筛选区、徽章、按钮都应该有基本样式（不是默认浏览器默认样式）。

- [ ] **Step 4: 提交**

```bash
git add web/static/pushes.css web/templates/pushes_list.html
git commit -m "feat(push-management): 列表页最小样式"
```

---

## Task 19: 冒烟与端到端自测

**Files:** 无（手工验证）

- [ ] **Step 1: 启动应用**

```bash
python main.py
```

- [ ] **Step 2: 逐项验证**

用管理员账号登录，按序：

1. 侧边栏看到「🚀 推送管理」
2. 进入 `/pushes`，默认显示"待推送"状态条目
3. 筛选"已推送" → 显示对应条目（可能为空）
4. 筛选"语种 = DE" → 只显示德语 item
5. 关键词搜素材文件名 → 过滤正确
6. 打开 `/medias` → 编辑某产品 → 能看到"主站已适配语种"多选框
7. 勾选 `de` → 保存 → 回 `/pushes` → 对应 DE item 状态变"待推送"
8. 点推送按钮 → DevTools Network 应出现 3 次请求：`GET /payload`、`POST` 到内网、`POST /mark-pushed`
9. 刷新 → 该条目状态变"✓ 已推送 YYYY-MM-DD"
10. 点"历史" → 抽屉显示 1 条成功记录
11. 点"重置" → 确认 → 条目回到"待推送"
12. 再次推送，模拟失败：手动把 `PUSH_TARGET_URL` 配置为不可达地址 → 推送 → 状态显示"推送失败"，历史新增 1 条 failed 记录
13. 切换普通用户登录 → 列表能看到但操作列不可见

- [ ] **Step 3: 跑所有新增测试**

```bash
pytest tests/test_appcore_pushes.py tests/test_pushes_routes.py tests/test_appcore_medias.py -v
```

Expected: 全部 passed

- [ ] **Step 4: 如通过全部验证，创建打包 commit**

```bash
git commit --allow-empty -m "chore(push-management): 冒烟验证通过"
```

---

## Self-Review Checklist（作者自检）

- [x] 设计文档每节都有对应 Task：表结构 → T1；状态机 → T3+T4；探活 → T5；payload → T6；日志 → T7；列表查询 → T8；API → T10+T11+T12；UI → T13+T14+T15+T18；编辑弹窗 → T16；侧边栏 → T17；配置 → T9
- [x] 所有 Task 含完整代码，无 TBD/TODO
- [x] 函数命名在任务间一致：`compute_readiness` / `is_ready` / `compute_status` / `build_item_payload` / `probe_ad_url` / `record_push_success|failure` / `reset_push_state` / `list_item_logs` / `list_items_for_push`
- [x] 测试用例覆盖：DAO 单测（pushes + medias 扩展）+ 路由 auth 与业务分支（not_ready / already_pushed / probe_fail / success / failed / reset / logs）
- [x] YAGNI：未引入批量推送、队列、重试、通知等首版范围外的特性
- [x] 提交粒度：每个 Task 独立提交，便于 review 和回滚
