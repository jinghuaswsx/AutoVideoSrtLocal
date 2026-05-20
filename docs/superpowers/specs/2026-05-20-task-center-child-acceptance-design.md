# 任务中心子任务验收闭环设计

- **日期**：2026-05-20
- **上位锚点**：
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/plans/2026-05-20-task-center-raw-source-automation.md`

## 背景

当前任务中心子任务只在提交时调用 `pushes.compute_readiness()` 做最终产物门禁。这个逻辑可以挡住缺视频、缺封面、缺文案等明显不完整状态，但无法在任务详情里清晰回答：

- 翻译员应该跳到哪个素材管理产品页继续工作。
- 任务系统如何知道该语言的视频、文案、封面、详情图、商品图替换和商品链接是否已经完成。
- 管理员审核时如何看到可核定的验收证据。

## 目标

1. 子任务“翻译”跳转到素材管理时，使用产品编码搜索页：`/medias/?q=<product_code>&from_task=<child_task_id>&lang=<lang>&action=translate`。
2. 子任务详情展示结构化验收项，而不是只展示零散 readiness 布尔值。
3. 子任务“提交完成”必须通过同一套验收项；缺任一必需项时返回 `readiness_failed` 和缺项 key。
4. 验收逻辑保留在 `appcore.tasks`，路由只委托 service，避免把业务 SQL 写进 `web/routes/tasks.py`。

## 验收项

子任务验收项按产品 + 目标语种计算：

| key | 含义 | 来源 |
| --- | --- | --- |
| `localized_media_item` | 目标语种 `media_items` 存在 | `tasks._find_target_lang_item()` |
| `translated_video` | 目标语种素材有视频对象 | `pushes.compute_readiness().has_object` |
| `translated_cover` | 目标语种素材有封面 | `pushes.compute_readiness().has_cover` |
| `translated_copywriting` | 目标语种文案存在 | `pushes.compute_readiness().has_copywriting` |
| `push_texts` | 推送所需英文三段文案可解析 | `pushes.compute_readiness().has_push_texts` |
| `product_listed` | 商品处于在架状态 | `pushes.compute_readiness().is_listed` |
| `language_supported` | 商品广告语言包含该语种 | `pushes.compute_readiness().lang_supported` |
| `detail_images` | 若英文静态详情图存在，目标语种详情图也必须存在 | `medias.list_detail_images()` |
| `shopify_images` | Shopify 商品图替换已确认且链接状态 normal | `pushes.compute_readiness().shopify_image_confirmed` |
| `product_links` | 目标语种商品链接最近探活正常，避免 404 链接进入推送 | `link_availability.list_results()` + `pushes.resolve_product_page_urls()` |

详情图规则：如果英文没有可翻译的静态详情图，`detail_images` 视为不要求并通过；如果英文有静态详情图，目标语种至少要有一张非 GIF 详情图。

链接规则：不在提交时实时发 HTTP 探活，避免提交操作被外部网络拖慢；提交依据产品链接管理已有探活缓存。没有可解析链接、没有探活记录或最近结果非 ok，均视为未通过。

## 前端行为

任务详情抽屉对每个子任务显示：

- 素材管理入口：优先使用 `q=<product_code>`，同时保留 `from_task`、`product`、`lang`、`action` 上下文。
- 验收面板：逐项显示通过/未通过、原因、数量和域名链接状态。
- 产出素材面板：产出素材链接同样使用产品编码搜索，避免只按内部 product id 打开后用户定位不到商品。

## 不做范围

- 不新增数据库表或迁移。
- 不改素材管理内部翻译任务创建流程。
- 不把视频、文案、封面、详情图各自拆成新的任务子状态。
- 不在子任务提交时主动发起外部 HTTP 探活。

## 验证

1. `pytest tests/test_appcore_tasks_supporting_data.py tests/test_tasks_routes.py::test_task_center_child_translate_jump_uses_product_code_search tests/test_tasks_routes.py::test_child_readiness_delegates_to_tasks_service tests/test_tasks_routes.py::test_child_readiness_maps_missing_child_to_404 tests/test_appcore_tasks.py::test_submit_child_fails_when_detail_images_not_ready -q`
2. `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
3. 手工检查 `/tasks/` 页面 JS 中素材管理跳转包含 `q=<product_code>`、`from_task`、`lang`。

不运行会连接本机 MySQL 的 DB fixture 测试；本项目规则禁止用本机 MySQL 做验证。
