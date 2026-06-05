# xmyc.com 下线与店小秘云仓采购价迁移设计

日期：2026-06-05

## 背景

采购价同步已经改为店小秘后台 `https://www.dianxiaomi.com/yuncangWarehouseSku/index.htm` 的小秘云仓货品数据。旧的 `xmyc.com` 独立站不再使用，但仓库里仍保留了 xmyc 独立浏览器、systemd timer、同步脚本、素材管理手动匹配弹窗，以及部分成本聚合对 `xmyc_storage_skus` 的读取/写入。

这些活跃路径会造成两个问题：

1. 线上仍可能继续拉取旧站或把旧表作为采购价 fallback，采购价来源不唯一。
2. 素材管理里仍显示“小秘云仓匹配”旧入口，容易让运营以为需要继续维护旧站 SKU 绑定。

## 事实来源

- `AGENTS.md`：定时任务必须登记到 `appcore/scheduled_tasks.py` 与 Web 定时任务模块。
- `docs/server_browser_runtime.md`：服务端 CDP 运行环境与 systemd timer 是线上定时同步的运维事实来源。
- `docs/superpowers/specs/2026-06-05-dianxiaomi-sku-purchase-sync-design.md`：SKU 与采购价每 2 小时同步，采购价需覆盖素材管理 SKU 与产品成本。

## 目标

1. 下线所有活跃 `xmyc.com` 入口：独立浏览器、同步脚本、timer、组合安装入口、任务中心登记和素材手动匹配 UI/API。
2. 必要的采购价能力全部切到店小秘小秘云仓表 `dianxiaomi_yuncang_skus`。
3. `dianxiaomi_yuncang_skus` 承接 SKU 维度成本聚合字段，供 SKU ROAS、小包成本、素材 SKU 弹窗继续使用。
4. 产品级 `media_products.purchase_price` 只从店小秘云仓 SKU 价格刷新，不再 fallback 到 `xmyc_storage_skus`。

## 非目标

- 不在本次删除历史迁移文件或直接 `DROP TABLE xmyc_storage_skus`；生产库历史数据保持只读闲置，避免破坏回滚和审计。
- 不重做店小秘云仓页面抓取协议；仍复用 DXM03-RJC CDP `127.0.0.1:9225`。
- 不改变素材 SKU 按钮的主交互；只移除旧 xmyc 手动匹配弹窗。

## 设计

### 数据与同步

新增 `appcore/dianxiaomi_yuncang.py` 作为唯一活跃云仓采购价模块。它负责：

- 确保 `dianxiaomi_yuncang_skus` 存在。
- 补齐 SKU 维度成本聚合列：
  - `standalone_price_sku`
  - `standalone_shipping_fee_sku`
  - `packet_cost_actual_sku`
  - `sku_orders_count`
- 抓取 `dianxiaomi.com/yuncangWarehouseSku/index.htm` 并 upsert SKU、商品名、库存、采购价。
- 按 `media_product_skus.dianxiaomi_sku` 和 `dianxiaomi_order_lines.product_display_sku` 刷新产品采购价。

采购价优先级调整为：

1. variant 人工采购价 `media_product_skus.manual_unit_price_rmb`。
2. 店小秘云仓 SKU 采购价 `dianxiaomi_yuncang_skus.unit_price > 0`。
3. 产品级采购价 `media_products.purchase_price`。

不再读取 `xmyc_storage_skus` 作为 fallback。

### 定时任务与部署

保留：

- `autovideosrt-dianxiaomi-sku-sync.timer`
- `autovideosrt-dianxiaomi-yuncang-sync.timer`

移除活跃安装/登记：

- `autovideosrt-xmyc-browser.service`
- `autovideosrt-xmyc-storage-sync.service`
- `autovideosrt-xmyc-storage-sync.timer`
- `tools/xmyc_storage_sync.py`
- `install_xmyc_browser.sh`
- `install_xmyc_storage_sync_timer.sh`

组合安装脚本 `install_sku_purchase_sync_timers.sh` 只安装店小秘 SKU 与店小秘云仓两个 timer。

### 素材管理

- 移除旧“小秘云仓匹配”按钮、弹窗、`/medias/api/xmyc-skus*` API 与对应静态资源。
- 素材列表与详情的 SKU 采购价索引改名为 `yuncang_index`，JSON 字段改为 `yuncang_unit_price_rmb`、`yuncang_goods_name` 等。
- `xmyc_match` 筛选不再参与产品列表查询；前端不再展示该筛选。

## 验收

1. `rg "xmyc\\.com|autovideosrt-xmyc|xmyc-storage|tools/xmyc_storage_sync|xmyc_storage_sync"` 不再命中活跃代码/部署脚本。
2. `tests/test_dianxiaomi_yuncang_storage.py` 覆盖采购价刷新与 SKU 聚合写入只访问 `dianxiaomi_yuncang_skus`。
3. `tests/test_server_browser_runtime.py` 覆盖组合安装入口只安装店小秘 SKU 与店小秘云仓 timer。
4. `tests/test_appcore_scheduled_tasks.py` 覆盖任务中心不再登记 `xmyc_storage_sync`。
5. 聚焦回归通过：

```bash
pytest tests/test_dianxiaomi_yuncang_storage.py tests/test_storage_sync_tool_run_records.py tests/test_server_browser_runtime.py tests/test_appcore_scheduled_tasks.py tests/test_media_product_detail_service.py tests/test_media_shopify_sku_refresh_service.py -q
```

## 回滚

- 代码回滚即可恢复旧入口文件与任务登记。
- 本次不删除生产表，回滚不需要数据恢复。
- 如线上已禁用旧 timer 后需要临时恢复，可通过旧版本 `deploy/server_browser/install_xmyc_storage_sync_timer.sh` 重新安装，但恢复前必须确认 `xmyc.com` 登录态仍可用。
