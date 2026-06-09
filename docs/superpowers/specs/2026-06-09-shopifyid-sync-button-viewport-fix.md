# Shopify ID 回填同步按钮视窗外点击修复

日期：2026-06-09

## 背景

2026-06-09 12:11 的 `autovideosrt-shopifyid-sync.service` 执行失败。日志显示脚本已打开 DXM03-RJC 店小秘 Shopify 在线商品页，并在执行“同步产品”前置动作时失败：

- Playwright 找到了唯一的“同步产品”按钮。
- 按钮可见、可用且稳定。
- Playwright 自动滚动后仍判断元素在 viewport 外，`Locator.click` 超时。

该失败会阻断当天 Shopify ID 回填。后续 `dianxiaomi_sku` 两小时同步只处理本地已有 `media_products.shopifyid` 的产品，因此新产品可能无法进入 SKU 配对同步范围。

2026-06-09 手动重跑时进一步确认 DXM03 常驻页 `window.innerWidth/window.innerHeight` 可变成 `1x1`。这种状态下 Playwright 即使找到按钮，也无法稳定打开下拉菜单。因此修复需要同时处理页面 viewport 尺寸和按钮点击兜底。

## 事实来源

- `docs/server_browser_runtime.md#Shopify ID 回填定时任务`：Shopify ID 回填使用 DXM03-RJC `127.0.0.1:9225`，每天 12:11 运行。
- `docs/server_browser_runtime.md#CDP 连接恢复`：`shopifyid` 依赖 DXM03-RJC 常驻 CDP，并在连接异常时记录失败。
- `journalctl -u autovideosrt-shopifyid-sync.service`：2026-06-09 失败原因为“同步产品”按钮在 viewport 外导致 `Locator.click` 超时。

## 行为要求

1. 点击“同步产品”前必须显式把按钮滚动到视窗中间，避免店小秘页面布局变化导致 Playwright 默认滚动不稳定。
2. 如果普通 click 仍失败，应先复用现有公告弹窗清理逻辑，再滚动并重试一次普通 click。
3. 如果重试仍失败，应使用 locator 级 JS click 作为兜底；JS click 前也必须滚动到视窗中间。
4. 兜底失败时，错误信息需要保留普通 click 的失败上下文，便于从 `scheduled_task_runs` 和 journal 定位。
5. 连接 DXM03 常驻浏览器后，如果当前页 viewport 小于可操作尺寸，应把 Playwright viewport 设置为 `1440x1000`，再进入店小秘页面和点击流程。
6. 不改变 Shopify 在线商品拉取、产品匹配、数据库写入和 SKU/采购价同步逻辑。

## 验收

1. 单元测试覆盖：第一次 click 被公告遮挡时，清理公告后重试成功。
2. 单元测试覆盖：普通 click 连续失败且错误包含 viewport 外信息时，执行 locator 级 JS click 兜底。
3. 单元测试覆盖：页面 viewport 为 `1x1` 时，脚本会设置自动化 viewport。
4. 聚焦测试通过：

```bash
pytest tests/test_shopifyid_dianxiaomi_sync.py::test_click_sync_products_button_retries_after_notice_overlay_cleanup tests/test_shopifyid_dianxiaomi_sync.py::test_click_sync_products_button_falls_back_to_js_click_when_outside_viewport tests/test_shopifyid_dianxiaomi_sync.py::test_ensure_page_automation_viewport_expands_tiny_cdp_viewport tests/test_server_browser_runtime.py::test_browser_automation_timers_are_staggered_to_reduce_lock_contention -q
```

5. 服务器上手动触发一次 `autovideosrt-shopifyid-sync.service`，最终结果为 `success`，并输出 Shopify ID 回填摘要。
