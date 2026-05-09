# 实时大盘广告费选源修复 + 收盘日 guard（2026-05-09）

承接 [XHR Token Channel](2026-05-09-meta-ads-xhr-token-channel.md) + [多账户](2026-05-07-meta-ads-multi-account-design.md) + [业务日对齐](2026-05-08-analytics-business-date-alignment-fix.md)。

## 背景与触发事件

2026-05-09 23:26 BJ 实时大盘当天（business_date=2026-05-09，BJ 16:00 5/9 → BJ 16:00 5/10）显示 **广告消耗费用 $41.56**，而 `meta_ad_realtime_daily_campaign_metrics` 当天最新 23:20 实时 snapshot 已经累计到 **$958.99**。dashboard 数字偏小 ~$917。

复盘：

- `roi_realtime_daily_snapshots` 表里 biz=2026-05-09 同时存在两条候选：
  - id=865 `snapshot_at=2026-05-09 23:20:00` ad_spend=$958.99（实时 hourly tick 写入，正确）
  - id=863 `snapshot_at=2026-05-10 16:00:00` ad_spend=$41.56（daily-final 写入；source_run_id=3723，task=meta_daily_final，22:09:41 完成）
- dashboard 查询 `ORDER BY snapshot_at DESC LIMIT 1`，**未来时间戳的 daily-final 行先选**，把实时 partial 数据挤掉 → 显示 $41.56。

为什么 daily-final 会写入一条还没收盘的 BJ 业务日？因为 [`drafts/backfill_omurio_history.py`](../../../drafts/backfill_omurio_history.py) 把 2026-05-09 当回填范围最后一天 `meta_daily_final_sync.run_final_sync(date(2026, 5, 9), mode='run', include_adsets=True)` 跑了一次。当时是 BJ 22:09，BJ 业务日 5/9 才走过 6 小时还没收盘，daily-final 路径不该跑。但 `run_final_sync` 没有"日是否已收盘"的前置 guard，照常拿了 LA 7:09 的 partial Omurio 数据（$41.56），并通过 `_refresh_final_roas_snapshot` 写下 `snapshot_at=BJ 5/10 16:00` 这条未来时间戳。

## 根因

### 根因 1：`run_final_sync` 缺乏"业务日已收盘"前置 guard

[`tools/meta_daily_final_sync.py::run_final_sync(target_date, ...)`](../../../tools/meta_daily_final_sync.py) 当前不校验 `target_date` 是否已收盘，任何 caller（自动 cron / 手动脚本 / Web UI）传入未收盘 BJ 业务日都会被照单接受、跑出 partial 数据并写入 daily / 大盘 snapshot 表。

### 根因 2：`get_realtime_roas_overview` 当天分支没排除未来时间戳的 daily-final 行

[`appcore/order_analytics/realtime.py::get_realtime_roas_overview`](../../../appcore/order_analytics/realtime.py)（约 line 1527）：

```python
latest_snapshot = query(
    "SELECT * FROM roi_realtime_daily_snapshots "
    "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
    "ORDER BY snapshot_at DESC, id DESC LIMIT 1",
    (target,),
)
```

没有 `AND snapshot_at <= now`。当天的 daily-final 行（`snapshot_at = day_end` = 5/10 16:00）在时间维度上排在所有真实 partial 行之前，所以一旦根因 1 让它存在，dashboard 必然挑错的那条。

历史日期不受影响：历史日 `day_end < now`，daily-final 行天然就是最大 `snapshot_at`，且语义上确实就是该日权威数据，选它正确。

## 设计

### Layer 1 — `run_final_sync` 拒绝未收盘 BJ 业务日

```python
def run_final_sync(target_date: date, *, mode: str = "run", ...) -> dict[str, Any]:
    closed_through = completed_meta_business_date()
    if target_date > closed_through:
        raise ValueError(
            f"meta_daily_final cannot run for target_date={target_date}: BJ business day "
            f"not yet closed (last closed = {closed_through}); use roi_hourly_sync realtime channel"
        )
    ...
```

- 校验放在最早，连 lock / start_run / DB 写入都不进。
- `completed_meta_business_date()` 已经存在，返回最后一个完全收盘的 BJ 业务日（cutover 16:00 后才会前推一天）。
- `mode='check'` 也走同款校验，避免 cron 在 16:00 前误触。
- CLI `python tools/meta_daily_final_sync.py --date 2026-05-09 --mode run` 会显式 fail 并打印清晰文案。

### Layer 2 — Dashboard 当天分支排除未来 / 跨日 snapshot

`get_realtime_roas_overview` 在选 latest_snapshot 时加 `now` 上限：

```python
if target == current_business_date:
    snapshot_filter_until = now
else:
    snapshot_filter_until = day_end  # 历史日：daily-final 的 day_end 永远 <= now
latest_snapshot = query(
    "SELECT * FROM roi_realtime_daily_snapshots "
    "WHERE business_date=%s AND store_scope='newjoy,omurio' AND ad_platform_scope='meta' "
    "AND snapshot_at <= %s "
    "ORDER BY snapshot_at DESC, id DESC LIMIT 1",
    (target, snapshot_filter_until),
)
```

- 当天：`snapshot_at <= now` 把未来时间戳的脏 daily-final 过滤掉，永远拿真实最新 partial。
- 历史日：`snapshot_at <= day_end`，daily-final 行（snap_at=day_end）正好等于上限通过，仍是最大 snapshot，选它正确。
- 行为兼容：未发生根因 1 写入时 query 行为不变（`snapshot_at <= now` 对所有 realtime partial 都成立）。
- `_should_try_realtime_snapshot` 决定走 snapshot 路径还是回落到明细路径，本次不动。

### Layer 4 — `_load_realtime_ad_snapshot_fallback` 防御性 open-day 兜底

[`appcore/order_analytics/order_profit_aggregation.py::_load_realtime_ad_snapshot_fallback`](../../../appcore/order_analytics/order_profit_aggregation.py)（订单利润核算的 ad spend 源）当前逻辑：

```python
finalized_dates: set[date] = set()
for row in daily_rows or []:
    business_date = _date_value(row.get("business_date"))
    finalized_dates.add(business_date)
    ...
fallback_dates = [d for d in _business_dates(date_from, date_to) if d not in finalized_dates]
```

只要 `meta_ad_daily_campaign_metrics` 里**任何一行**对应某个 BJ 业务日，该日就被视为 finalized，realtime fallback 跳过。这意味着根因 1 注入 1 行 $41.56 后，订单利润看到的 5/9 ad spend 就 = $41.56（不再 fallback 到 realtime $958.99）。

修复：在 finalized_dates 集合外加一道 close 校验：

```python
closed_through = completed_meta_business_date()
...
for row in daily_rows or []:
    business_date = _date_value(row.get("business_date"))
    if business_date and business_date <= closed_through:
        finalized_dates.add(business_date)
    # 未收盘日不进 finalized_dates，无论 daily 表里有没有行
    ...
```

效果：
- 收盘日（target ≤ closed_through）：daily 表有数据 → finalized → 走 daily 路径（与现状一致）
- 未收盘日（target > closed_through）：永远进 fallback_dates → 走 realtime 路径，绕过 daily 表上任何脏数据
- Layer 1 之后理论上不会再有未收盘日数据写入 daily 表，但这道防御保证即便手动 SQL / 手动上传 / 代码回归再次注入也不会污染订单利润口径

### Layer 5 — 产品盈亏看板加 realtime fallback 路径

[`appcore/order_analytics/product_profit_list.py::_load_ad_spend`](../../../appcore/order_analytics/product_profit_list.py) 当前**只读** `meta_ad_daily_*` 表，没有 realtime fallback。Layer 3 清完脏数据后，5/9 这天 daily 表无数据，产品盈亏看板会显示 0 广告费——比 $41.56 还糟。

修复：把已经成熟的 `_load_realtime_ad_snapshot_fallback` 提为可复用 helper（保留原 import 兼容），让 `product_profit_list._load_ad_spend` 在收盘日走 daily、在未收盘日 union 一份 realtime fallback：

```python
# product_profit_list._load_ad_spend
def _load_ad_spend(date_from, date_to, country=None):
    daily = _load_daily_ad_spend(date_from, date_to, country)  # 现有逻辑
    closed_through = completed_meta_business_date()
    open_dates = [d for d in _business_dates(date_from, date_to) if d > closed_through]
    if open_dates:
        # 直接复用 order_profit 的 fallback；country 过滤本期暂不支持
        # （ad-level country 数据只存在 meta_ad_daily_ad_metrics，realtime 表只有 campaign 层），
        # country 选项下 open day 的 ad spend 会显示为空 + warning，留给后续。
        rt = _load_realtime_ad_snapshot_fallback(
            date_from=min(open_dates), date_to=max(open_dates),
        )
        # 把 spend_by_product 合并进 daily 结果，业务日维度 sum
        for (_business_date, product_id), spend in rt["spend_by_product"].items():
            daily[product_id] = daily.get(product_id, Decimal(0)) + Decimal(str(spend))
    return daily
```

`_load_unallocated_ad_spend` 同步加同款 open-day 兜底（取 fallback 的 unallocated_spend）。

边界：
- 国家筛选模式（`country` 非空）：现有 `meta_ad_daily_ad_metrics` 是 ad 层，realtime 表是 campaign 层，country 维度不全。本期保持现状，open day + country 筛选时 ad_spend 显示 0，UI 加 note；spec 里把它列为已知 limitation，后续 PR 单独解决。
- 不引入新表 / 新 schema；纯 Python aggregation。

### Layer 3 — 清数据

为让 dashboard 立刻恢复，回滚根因 1 写入的脏数据：

1. `DELETE FROM roi_realtime_daily_snapshots WHERE id=863`（业务上等价于：删除 biz=2026-05-09 + snapshot_at=2026-05-10 16:00 + source_run_id=3723 这一行）。
2. `DELETE FROM meta_ad_daily_campaign_metrics WHERE meta_business_date='2026-05-09' AND ad_account_id='1253003326160754'`。
3. 同款清理 `meta_ad_daily_adset_metrics` / `meta_ad_daily_ad_metrics` 的 biz=2026-05-09 相关行（同一 batch 写的）。
4. 对应 `meta_ad_import_batches` 行（report_start_date=report_end_date=2026-05-09 + import_frequency='daily_final'）也删一下，保持外键一致性。
5. `roi_daily_roas_nodes` 里 biz=2026-05-09 各 node_hour 的 ad_spend_usd 是基于实时 snapshot 算的，删 id=863 后下一次实时 tick 会重新 `_upsert_daily_roas_node`，自愈。本期不强行 backfill 这张表。

清理后 dashboard 立刻显示 id=865 的 $958.99（最新真实 partial）。

scheduled_task_runs id=3723 自身保留作为审计记录（以及 commit/spec 引用），不删。

## 测试计划

新增：

- `tests/test_meta_daily_final_sync_guard.py`：
  - `target_date == current_meta_business_date(now)` → raise ValueError，且 `start_run` 没被调
  - `target_date > current_meta_business_date(now)` → raise ValueError
  - `target_date == completed_meta_business_date(now)` → 正常进入主路径（不 raise）
  - `target_date < completed_meta_business_date(now)` → 正常
- `tests/test_realtime_dashboard_snapshot_filter.py`：
  - 当天 snapshot 表里同时存在 `snap_at=23:20` 和 `snap_at=tomorrow 16:00`（脏 daily-final）→ dashboard 应取 23:20 的
  - 历史日 snapshot 表里只有 `snap_at=day_end` 的 daily-final → dashboard 仍正常取它
  - 历史日 snapshot 表里同时有 daily-final 和某个时段的 partial → 取 daily-final

最少必跑：

```
pytest tests/test_meta_daily_final_sync_guard.py \
       tests/test_realtime_dashboard_snapshot_filter.py \
       tests/test_meta_server_sync_tools.py \
       tests/test_roi_hourly_sync_meta_multi_account.py \
       tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py -q
```

部署后端到端验证：刷新大盘 `http://14.103.220.208/order-analytics`（实时大盘 tab），「广告消耗费用」应显示 ≈ $958.99（截至 5/9 23:20 实时 snapshot），不再是 $41.56。

## 部署 / 回滚

- 部署：`git push origin master`（包含 Layer 1 + Layer 2 代码）→ `sudo cd /opt/autovideosrt && git pull && systemctl restart autovideosrt`。
- 数据清理（Layer 3）：手动 SQL 删除指定行（脚本在 commit message 里附）。
- 回滚 Layer 1：commit revert 即可，恢复"无 guard"行为；脏数据已清，新行不会再产生。
- 回滚 Layer 2：commit revert，恢复"`ORDER BY snapshot_at DESC` 不带 now 上限"；老脏数据若没清干净会重新出问题。

## 文档锚点

- 本 spec：[docs/superpowers/specs/2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md](2026-05-09-realtime-dashboard-ad-spend-source-of-truth.md)
- 触发的 backfill 脚本：[drafts/backfill_omurio_history.py](../../../drafts/backfill_omurio_history.py)（限 START..END，未来再用要先确认 END < `completed_meta_business_date()`，否则末尾一天会被 Layer 1 guard 拒）
- CLAUDE.md「Meta 广告多账户同步」节追加：daily-final 不会再吞下未收盘 BJ 业务日；实时大盘当天 ad spend 永远来自当时点 partial，不会被未来时间戳的 daily-final 行干扰。
