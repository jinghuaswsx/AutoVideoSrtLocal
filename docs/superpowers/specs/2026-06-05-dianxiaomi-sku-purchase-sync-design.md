# 店小秘 SKU 与采购价两小时同步设计

日期：2026-06-05

## 背景

素材管理 `/medias` 的 SKU 按钮只根据 `media_product_skus` 判断是否存在 SKU 配对；SKU 详情弹窗再通过 `media_product_skus.dianxiaomi_sku` 补充采购价和 ROAS。线上排查发现，部分新增且已有 `shopifyid` 的产品没有及时写入 `media_product_skus`，导致素材管理显示无 SKU。

店小秘后台 `https://www.dianxiaomi.com/yuncangWarehouseSku/index.htm` 的小秘云仓货品页已经能提供采购价和库存。2026-06-05 追加的 `docs/superpowers/specs/2026-06-05-xmyc-retirement-dianxiaomi-yuncang-design.md` 明确旧 xmyc 独立站下线，采购价唯一活跃来源改为 `dianxiaomi_yuncang_skus`。

## 事实来源

- `docs/server_browser_runtime.md`：服务端 CDP、DXM03-RJC 与定时任务约束。
- `docs/superpowers/specs/2026-05-07-dianxiaomi-sku-variant-source-and-cdp-recovery-design.md`：`dianxiaomi_sku` 同步必须用店小秘在线商品库 + Shopify 公开 variants 补齐配对，并记录失败。
- `docs/superpowers/specs/2026-06-05-medias-list-sku-and-time-columns-design.md`：素材列表 SKU 列只显示一个按钮，点击后打开 SKU 配对详情。
- `docs/superpowers/specs/2026-06-05-xmyc-retirement-dianxiaomi-yuncang-design.md`：旧 xmyc 入口下线，必要能力切到店小秘云仓。
- `AGENTS.md`：新增或调整 systemd timer 必须登记到 `appcore/scheduled_tasks.py`。

## 目标

1. 每 2 小时从店小秘获取最新 Shopify 在线商品与 ERP SKU 配对，写入 `media_product_skus`。
2. 每 2 小时同步店小秘小秘云仓采购价数据，写入 `dianxiaomi_yuncang_skus`。
3. 产品级采购价刷新覆盖：
   - `media_product_skus.dianxiaomi_sku` 能命中云仓 SKU 的产品。
   - `dianxiaomi_order_lines.product_display_sku` 能命中云仓 SKU 的产品。
4. 所有定时任务在 Web 后台“定时任务”模块可见，并能通过 `scheduled_task_runs` 追踪失败。

## 非目标

- 不改变素材管理主表 SKU 列的交互形态。
- 不在本次重做店小秘商品、订单、云仓的抓取协议。
- 不直接在代码变更阶段重启线上服务；部署执行需用户明确要求“发测试 / 上线”。

## 设计

### 店小秘 SKU 配对同步

systemd unit：

- `deploy/server_browser/autovideosrt-dianxiaomi-sku-sync.service`
- `deploy/server_browser/autovideosrt-dianxiaomi-sku-sync.timer`
- `deploy/server_browser/install_dianxiaomi_sku_sync_timer.sh`

timer 使用 `OnCalendar=*-*-* 00/2:21:00`，即北京时间每 2 小时的第 21 分钟执行一次。

service 调用：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/dianxiaomi_sku_sync.py \
  --skip-login-prompt \
  --browser-mode server-cdp \
  --browser-cdp-url http://127.0.0.1:9225 \
  --db-mode local
```

### 店小秘云仓采购价同步

systemd unit：

- `deploy/server_browser/autovideosrt-dianxiaomi-yuncang-sync.service`
- `deploy/server_browser/autovideosrt-dianxiaomi-yuncang-sync.timer`
- `deploy/server_browser/install_dianxiaomi_yuncang_sync_timer.sh`

timer 使用 `OnCalendar=*-*-* 01/2:03:00`，错开 SKU 配对同步。service 调用：

```bash
/opt/autovideosrt/venv/bin/python /opt/autovideosrt/tools/dianxiaomi_yuncang_sync.py \
  --cdp-url http://127.0.0.1:9225
```

`appcore/scheduled_tasks.py` 中 `dianxiaomi_sku` 与 `dianxiaomi_yuncang_sync` 必须登记为每 2 小时、线上已启用。

### 采购价刷新

`appcore/dianxiaomi_yuncang.refresh_purchase_prices_for_matched()` 从两个入口找产品集合：

```sql
SELECT DISTINCT mps.product_id
FROM media_product_skus mps
JOIN dianxiaomi_yuncang_skus y ON y.sku = mps.dianxiaomi_sku
WHERE mps.product_id IS NOT NULL
```

```sql
SELECT DISTINCT d.product_id
FROM dianxiaomi_order_lines d
JOIN dianxiaomi_yuncang_skus y ON y.sku = d.product_display_sku
WHERE d.product_id IS NOT NULL
```

单个产品的采购价优先级为：

1. 订单行数量最多的云仓 SKU 采购价。
2. 如果订单行尚不足以排序，则使用命中的云仓价格中位值。
3. 没有云仓正价时清空产品级采购价，避免旧数据继续误导 ROAS。

## 验收

1. `tests/test_dianxiaomi_yuncang_storage.py` 覆盖云仓解析、写入、采购价刷新。
2. `tests/test_server_browser_runtime.py` 覆盖两个 systemd timer 均为每 2 小时，组合安装入口不再安装旧 xmyc timer。
3. `tests/test_appcore_scheduled_tasks.py` 覆盖任务中心只登记 `dianxiaomi_sku` 和 `dianxiaomi_yuncang_sync`。
4. 聚焦回归通过：

```bash
pytest tests/test_dianxiaomi_yuncang_storage.py tests/test_dianxiaomi_sku_sync.py tests/test_storage_sync_tool_run_records.py tests/test_server_browser_runtime.py tests/test_appcore_scheduled_tasks.py -q
```

## 回滚

停止新增或调整后的 timer：

```bash
systemctl disable --now autovideosrt-dianxiaomi-sku-sync.timer
systemctl disable --now autovideosrt-dianxiaomi-yuncang-sync.timer
```

本设计不删除历史表，回滚不需要数据恢复。
