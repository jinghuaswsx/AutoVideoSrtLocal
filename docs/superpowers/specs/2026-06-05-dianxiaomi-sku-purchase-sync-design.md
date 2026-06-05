# 店小秘 SKU 与采购价两小时同步设计

日期：2026-06-05

## 背景

素材管理 `/medias` 的 SKU 按钮只根据 `media_product_skus` 判断是否存在 SKU 配对；SKU 详情弹窗再通过 `media_product_skus.dianxiaomi_sku` 去补充采购价和 ROAS。线上排查发现，`dianxiaomi_sku` 最后一次成功同步停在 2026-05-08 12:21:31，之后新增且已有 `shopifyid` 的产品没有写入 `media_product_skus`。

店小秘后台 `https://www.dianxiaomi.com/yuncangWarehouseSku/index.htm` 的云仓货品页已经能提供部分采购价，例如 `0513-18188604` 和 `0511-15101221`。这些数据已缓存到 `dianxiaomi_yuncang_skus`，但现有采购价刷新只遍历 `xmyc_storage_skus.product_id IS NOT NULL` 的产品；如果产品没有小秘云仓手动/自动匹配行，即使订单行 `dianxiaomi_order_lines.product_display_sku` 能命中店小秘云仓 SKU，也不会回填 `media_products.purchase_price`。

## 事实来源

- `docs/server_browser_runtime.md`：服务端 CDP、DXM03-RJC、xmyc-storage 浏览器运行层与定时任务约束。
- `docs/superpowers/specs/2026-05-07-dianxiaomi-sku-variant-source-and-cdp-recovery-design.md`：`dianxiaomi_sku` 同步必须用店小秘在线商品库 + Shopify 公开 variants 补齐配对，并记录失败。
- `docs/superpowers/specs/2026-06-05-medias-list-sku-and-time-columns-design.md`：素材列表 SKU 列只显示一个按钮，点击后打开 SKU 配对详情。
- `AGENTS.md`：新增或调整 systemd timer 必须登记到 `appcore/scheduled_tasks.py`。

## 目标

1. 每 2 小时从店小秘获取最新 Shopify 在线商品与 ERP SKU 配对，写入 `media_product_skus`，让素材管理能看到新增产品的 SKU 配对。
2. 每 2 小时同步小秘云仓与店小秘云仓采购价数据，保证系统内 SKU、采购价和产品级成本尽快跟上店小秘后台变化。
3. 采购价回填覆盖两类产品：
   - 已通过 `xmyc_storage_skus.product_id` 匹配到产品的 SKU。
   - 未匹配 `xmyc_storage_skus`，但 `dianxiaomi_order_lines.product_display_sku` 能命中 `dianxiaomi_yuncang_skus.sku` 的产品。
4. 所有定时任务在 Web 后台“定时任务”模块可见，并能通过 `scheduled_task_runs` 追踪失败。

## 非目标

- 不改变素材管理主表 SKU 列的交互形态。
- 不在本次重做店小秘商品、订单、云仓的抓取协议。
- 不直接在代码变更阶段重启线上服务；部署执行需用户明确要求“发测试 / 上线”。

## 设计

### 店小秘 SKU 配对同步

新增仓库内 systemd unit：

- `deploy/server_browser/autovideosrt-dianxiaomi-sku-sync.service`
- `deploy/server_browser/autovideosrt-dianxiaomi-sku-sync.timer`
- `deploy/server_browser/install_dianxiaomi_sku_sync_timer.sh`

timer 使用 `OnCalendar=*-*-* 00/2:21:00`，即北京时间每 2 小时的第 21 分钟执行一次。错开 Shopify ID 的 `12:11`、xmyc-storage 的 `:33`、listing ranking 的 `12:40` 和 ROI 的 `:00/:20/:40`。

service 调用：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/dianxiaomi_sku_sync.py \
  --skip-login-prompt \
  --browser-mode server-cdp \
  --browser-cdp-url http://127.0.0.1:9225 \
  --db-mode local
```

`appcore/scheduled_tasks.py` 中 `dianxiaomi_sku` 的 schedule 和 deployment 必须同步改为“每 2 小时，线上已启用”。

### 云仓采购价同步

保留现有 `autovideosrt-xmyc-storage-sync.service`，把 timer 从每天 12:33 调整为 `OnCalendar=*-*-* 00/2:33:00`，每 2 小时抓取小秘云仓 `xmyc_storage_skus` 并执行现有自动匹配。

新增店小秘云仓货品同步 unit：

- `deploy/server_browser/autovideosrt-dianxiaomi-yuncang-sync.service`
- `deploy/server_browser/autovideosrt-dianxiaomi-yuncang-sync.timer`
- `deploy/server_browser/install_dianxiaomi_yuncang_sync_timer.sh`

timer 使用 `OnCalendar=*-*-* 01/2:03:00`，即与 xmyc-storage 错开 30 分钟，避免同时占用 DXM03-RJC 浏览器。service 调用：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/dianxiaomi_yuncang_sync.py \
  --cdp-url http://127.0.0.1:9225
```

`appcore/scheduled_tasks.py` 中 `dianxiaomi_yuncang_sync` 的 schedule 和 deployment 必须同步改为“每 2 小时，线上已启用”。

### 采购价刷新覆盖范围

调整 `appcore/xmyc_storage.refresh_purchase_prices_for_matched()`：

1. 保留现有 `xmyc_storage_skus.product_id IS NOT NULL` 产品集合。
2. 追加从订单行推导的产品集合：

```sql
SELECT DISTINCT d.product_id
FROM dianxiaomi_order_lines d
JOIN dianxiaomi_yuncang_skus y ON y.sku = d.product_display_sku
WHERE d.product_id IS NOT NULL
  AND d.product_display_sku IS NOT NULL
  AND d.product_display_sku <> ''
```

3. 对合并后的产品集合调用 `_refresh_product_purchase_price(product_id)`。

`_refresh_product_purchase_price()` 保持价格优先级：先查 `dianxiaomi_yuncang_skus.unit_price > 0`，再 fallback 到 `xmyc_storage_skus.unit_price > 0`。如果一个产品有多个订单 SKU，仍按订单行数量最多的 SKU 选主力采购价。

## 验收

1. `tests/test_xmyc_storage.py` 覆盖：当产品只有订单 SKU 命中 `dianxiaomi_yuncang_skus`、没有 `xmyc_storage_skus.product_id` 时，`refresh_purchase_prices_for_matched()` 也会刷新该产品采购价。
2. `tests/test_server_browser_runtime.py` 覆盖：
   - `autovideosrt-dianxiaomi-sku-sync.timer` 是每 2 小时。
   - `autovideosrt-xmyc-storage-sync.timer` 是每 2 小时。
   - `autovideosrt-dianxiaomi-yuncang-sync.timer` 是每 2 小时。
   - 对应 install 脚本会安装 service 和 timer。
3. `tests/test_appcore_scheduled_tasks.py` 覆盖：`dianxiaomi_sku`、`dianxiaomi_yuncang_sync`、`xmyc_storage_sync` 在任务中心登记为每 2 小时。
4. 聚焦回归通过：

```bash
pytest tests/test_xmyc_storage.py tests/test_dianxiaomi_sku_sync.py tests/test_server_browser_runtime.py tests/test_appcore_scheduled_tasks.py -q
```

## 回滚

- 停止新增或调整后的 timer：

```bash
systemctl disable --now autovideosrt-dianxiaomi-sku-sync.timer
systemctl disable --now autovideosrt-dianxiaomi-yuncang-sync.timer
systemctl disable --now autovideosrt-xmyc-storage-sync.timer
```

- 如需恢复旧频率，将 `autovideosrt-xmyc-storage-sync.timer` 恢复为每天 12:33。
- 数据层不新增 schema，回滚不需要迁移。
