# Materials List Created Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让素材管理产品列表按添加时间倒序展示，最后添加的产品显示在最前面。

**Architecture:** 保持前端与接口结构不变，只修改 `appcore.medias.list_products()` 的 SQL 排序字段。用一个不依赖 MySQL 的 DAO 单测锁定排序语义，避免被 `updated_at` 回顶。

**Tech Stack:** Python, pytest, Flask DAO layer

---

### Task 1: Lock And Update Product List Ordering

**Files:**
- Modify: `tests/test_appcore_medias.py`
- Modify: `appcore/medias.py`

- [ ] **Step 1: Write the failing test**

```python
def test_list_products_orders_by_created_at_desc(monkeypatch):
    captured = {}

    def fake_query_one(sql, args=()):
        captured["count_sql"] = sql
        captured["count_args"] = args
        return {"c": 0}

    def fake_query(sql, args=()):
        captured["list_sql"] = sql
        captured["list_args"] = args
        return []

    monkeypatch.setattr(medias, "query_one", fake_query_one)
    monkeypatch.setattr(medias, "query", fake_query)

    rows, total = medias.list_products(None, archived=False, offset=20, limit=20)

    assert rows == []
    assert total == 0
    assert "ORDER BY created_at DESC, id DESC" in captured["list_sql"]
    assert captured["list_args"][-2:] == (20, 20)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_appcore_medias.py::test_list_products_orders_by_created_at_desc -q`
Expected: FAIL because SQL still uses `ORDER BY updated_at DESC`

- [ ] **Step 3: Write minimal implementation**

```python
rows = query(
    f"SELECT * FROM media_products WHERE {where_sql} "
    "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
    tuple(args + [limit, offset]),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_appcore_medias.py::test_list_products_orders_by_created_at_desc -q`
Expected: PASS

- [ ] **Step 5: Run focused regression verification**

Run: `pytest tests/test_appcore_medias.py::test_list_products_orders_by_created_at_desc tests/test_appcore_medias.py::test_add_detail_image_records_translate_provenance -q`
Expected: PASS
