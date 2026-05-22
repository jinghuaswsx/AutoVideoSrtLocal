# 任务详情商品链接域名单列展示设计

- **日期**：2026-05-22
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-translation-output-evidence-design.md`
  - `docs/superpowers/specs/2026-05-21-task-center-translation-product-actions-design.md`

## 背景

任务中心任务详情页的“商品链接与图片状态”卡片把 `shopify_images` 和 `product_links` 两个验收项合并展示。每个验收项的域名 evidence 现在沿用通用 readiness 网格，在卡片里会横向排布。运营核对“链接商品图替换”和“商品链接探活”时，希望这两个卡片里的域名列表按一行一个域名展示，便于逐条确认。

## 目标

1. 只调整“商品链接与图片状态”组合卡片内的 `shopify_images` 和 `product_links` evidence 布局。
2. 每个域名证据项占满一行，一行只显示一个域名。
3. 保留现有域名链接、状态文案、错误提示、人工确认和重检操作。
4. 不改变后端 readiness 结构、提交门禁、产品链接探活规则或 Shopify 商品图替换状态规则。

## 前端行为

- `tcRenderReadinessCheckRow(..., {mode: 'product-link-combo'})` 渲染 `shopify_images` 和 `product_links` 时，给 evidence 容器追加专用单列类。
- 专用单列类设置 `grid-template-columns: 1fr`，让 link/status evidence 每条域名单独成行。
- 通用 readiness evidence 继续保持原有两列或图片紧凑网格，避免影响视频、封面、详情图、文案等其它验收项。

## 不做范围

- 不新增 API 字段。
- 不新增数据库表或迁移。
- 不改变 `appcore.tasks.get_child_readiness()` 的计算逻辑。
- 不把两个验收项拆回普通 readiness 列表；它们仍保留在组合卡片里。

## 验证

1. 前端字符串测试覆盖组合卡片调用单列 evidence 渲染模式。
2. `pytest tests/test_tasks_routes.py::test_task_detail_readiness_groups_product_link_checks_into_manager_card tests/test_task_center_closure_assets.py -q`
3. 如需手工验收，打开 `/tasks/detail/<id>`，确认“链接商品图替换”和“商品链接探活”卡片里的域名一行一个。
