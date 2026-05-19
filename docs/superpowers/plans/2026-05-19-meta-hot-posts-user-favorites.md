# Meta 热帖用户收藏夹 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Meta 热帖视频卡片增加按用户隔离的收藏夹，并提供收藏夹列表排序。

**Architecture:** 使用独立 `meta_hot_post_favorites` 表保存 `user_id + hot_post_id`。`store` 负责 SQL 查询与收藏写入，`service` 负责 hydrate 当前用户收藏状态，`xuanpin` 路由负责当前用户传递，模板负责 tab、排序控件和收藏按钮交互。

**Tech Stack:** Python 3.12, Flask, MySQL, pytest, Jinja/vanilla JS.

---

### Task 1: 数据层与迁移

**Files:**
- Create: `db/migrations/2026_05_19_meta_hot_posts_user_favorites.sql`
- Modify: `appcore/meta_hot_posts/store.py`
- Test: `tests/test_meta_hot_posts_store.py`
- Test: `tests/test_db_migration_meta_hot_posts_marked.py`

- [ ] **Step 1: 写失败测试**

新增 store 测试，覆盖：

```python
def test_set_hot_post_favorite_inserts_and_deletes_by_user():
    calls = []
    store.set_hot_post_favorite(7, user_id=88, favorited=True, execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1)
    store.set_hot_post_favorite(7, user_id=88, favorited=False, execute_fn=lambda sql, params=(): calls.append((sql, params)) or 1)
    assert "INSERT INTO meta_hot_post_favorites" in calls[0][0]
    assert calls[0][1] == (88, 7)
    assert "DELETE FROM meta_hot_post_favorites" in calls[1][0]
    assert calls[1][1] == (88, 7)
```

再新增收藏夹列表排序测试，断言 SQL 连接收藏表、按用户筛选，并根据 sort 选择排序。

- [ ] **Step 2: 跑红灯**

Run: `pytest tests/test_meta_hot_posts_store.py::test_set_hot_post_favorite_inserts_and_deletes_by_user -q`

Expected: `AttributeError` 或导入失败，因为函数尚未实现。

- [ ] **Step 3: 实现最小数据层**

在 `store.py` 增加 `set_hot_post_favorite()`、`list_favorite_hot_posts()`，并让现有列表查询在传入 `user_id` 时左连接收藏表返回 `favorited_at`。

- [ ] **Step 4: 跑绿灯**

Run: `pytest tests/test_meta_hot_posts_store.py tests/test_db_migration_meta_hot_posts_marked.py -q`

Expected: PASS.

### Task 2: 服务层与路由

**Files:**
- Modify: `appcore/meta_hot_posts/service.py`
- Modify: `web/routes/xuanpin.py`
- Test: `tests/test_meta_hot_posts_service.py`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] **Step 1: 写失败测试**

新增服务测试，覆盖 `build_list_response(args, user_id=88)` 会传递用户 ID 并 hydrate `is_favorited`。新增路由测试，覆盖收藏夹列表 API 和收藏 POST API 都传当前用户。

- [ ] **Step 2: 跑红灯**

Run: `pytest tests/test_meta_hot_posts_service.py::test_build_list_response_hydrates_user_favorite_state tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_favorite_api_passes_current_user -q`

Expected: FAIL，因为签名和路由尚未实现。

- [ ] **Step 3: 实现最小服务与路由**

给 `build_list_response()`、`build_today_new_response()`、`build_europe_top_response()`、`build_video_copyability_top50_response()` 增加可选 `user_id`。新增 `build_favorites_response()` 和 `build_favorite_response()`。路由新增 `GET /api/meta-hot-posts/favorites` 与 `POST /api/meta-hot-posts/<post_id>/favorite`。

- [ ] **Step 4: 跑绿灯**

Run: `pytest tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_routes.py -q`

Expected: PASS.

### Task 3: 前端交互

**Files:**
- Modify: `web/templates/meta_hot_posts.html`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] **Step 1: 写失败测试**

扩展页面渲染测试，断言存在“我的收藏夹”tab、收藏夹排序控件、`toggleMetaHotPostFavorite()`、收藏夹 API URL、收藏按钮文本。

- [ ] **Step 2: 跑红灯**

Run: `pytest tests/test_meta_hot_posts_routes.py::test_meta_hot_posts_page_renders_tabs_and_api -q`

Expected: FAIL，页面还没有收藏夹 UI。

- [ ] **Step 3: 实现最小前端**

增加收藏夹 tab、排序控件、卡片左上角按钮、收藏状态乐观更新、收藏夹取消后移除当前卡片。

- [ ] **Step 4: 跑绿灯与综合验证**

Run: `pytest tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_routes.py tests/test_db_migration_meta_hot_posts_marked.py -q`

Expected: PASS.
