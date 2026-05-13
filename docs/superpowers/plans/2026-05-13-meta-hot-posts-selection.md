# Meta 热帖选品 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在选品中心新增 Meta 热帖卡片页，并把 wedev 热帖、商品页 SKU 价格和 Gemini 分类沉淀为可筛选数据。

**Architecture:** 用 `tools/meta_hot_posts` 负责 wedev 采集，`appcore/meta_hot_posts` 负责标准化、落库、商品页分析、LLM 分类和 Web API 响应。页面只读取本地库并按 wedev 卡片样式渲染，定时任务通过 APScheduler 串行执行，所有外部请求强制最小 3 秒间隔。

**Tech Stack:** Python 3.12, Flask, MySQL, requests, BeautifulSoup, APScheduler, `appcore.llm_client`, pytest.

---

### Task 1: 文档与迁移

**Files:**
- Create: `docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md`
- Create: `db/migrations/2026_05_13_meta_hot_posts.sql`

- [ ] 写入设计 spec，覆盖接口探索、字段、频率、商品分析、Gemini 分类、筛选项。
- [ ] 新增三张表：`meta_hot_post_sync_runs`、`meta_hot_posts`、`meta_hot_post_product_analyses`。
- [ ] 在迁移里写明 Docs-anchor。

### Task 2: wedev 客户端

**Files:**
- Create: `tools/meta_hot_posts/client.py`
- Test: `tests/test_meta_hot_posts_client.py`

- [ ] 先写 throttle、登录失效识别、字段归一化测试。
- [ ] 实现 `MetaHotPostsClient`，默认 base URL 取 wedev 设置，headers 取现有明空凭据。
- [ ] 强制 `min_interval_seconds=max(3.0, value)`。

### Task 3: 商品页分析与分类

**Files:**
- Create: `appcore/meta_hot_posts/product_analysis.py`
- Create: `appcore/meta_hot_posts/categories.py`
- Modify: `appcore/llm_use_cases.py`
- Test: `tests/test_meta_hot_posts_product_analysis.py`

- [ ] 先写 Shopify JSON、JSON-LD、HTML meta、SKU 价格归一化测试。
- [ ] 实现 `ProductAnalysisResult` 和提取函数。
- [ ] 实现 Gemini 分类 prompt 与 JSON 解析，类目不命中时归为 `Other`。
- [ ] 注册 `meta_hot_posts.categorize` use case。

### Task 4: 落库与查询服务

**Files:**
- Create: `appcore/meta_hot_posts/store.py`
- Create: `appcore/meta_hot_posts/service.py`
- Test: `tests/test_meta_hot_posts_store.py`

- [ ] 先写 upsert SQL、筛选 SQL、分析队列查询测试。
- [ ] 实现热帖 upsert、商品分析 upsert、列表筛选、类目选项。
- [ ] API 响应补齐派生链接和分析字段。

### Task 5: 同步与分析 runner

**Files:**
- Create: `appcore/meta_hot_posts/scheduler.py`
- Create: `tools/meta_hot_posts/main.py`
- Modify: `appcore/scheduler.py`
- Modify: `appcore/scheduled_tasks.py`
- Test: `tests/test_appcore_scheduled_tasks.py`

- [ ] 登记 `meta_hot_posts_sync_tick` 和 `meta_hot_posts_analysis_tick`。
- [ ] 同步 runner 每天 07:00 拉 wedev 热帖，目标 500 条，并 upsert。
- [ ] 分析 runner 每次少量处理 pending 商品链接，串行商品页提取与 Gemini 分类。

### Task 6: Web 路由与卡片页面

**Files:**
- Modify: `web/routes/xuanpin.py`
- Create: `web/templates/meta_hot_posts.html`
- Modify: `web/templates/mk_selection.html`
- Modify: `web/templates/new_product_review_list.html`
- Modify: `web/templates/today_recommendations.html`
- Modify: `web/templates/tabcut_selection.html`
- Test: `tests/test_xuanpin_routes.py`
- Test: `tests/test_meta_hot_posts_routes.py`

- [ ] 新增 `/xuanpin/meta-hot-posts` 页面和 `/xuanpin/api/meta-hot-posts*` API。
- [ ] 所有选品中心 Tab 增加「Meta热帖」。
- [ ] 页面按 wedev 卡片样式展示热帖卡片，并提供类目、价格、交互、评论、创建时间筛选。

### Task 7: 验证

**Files:**
- No production edits.

- [ ] 运行目标测试：

```bash
pytest tests/test_meta_hot_posts_client.py tests/test_meta_hot_posts_product_analysis.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py tests/test_appcore_scheduled_tasks.py tests/test_xuanpin_routes.py -q
```

- [ ] 如模板或路由改动影响较大，启动 dev server 做页面 smoke。
