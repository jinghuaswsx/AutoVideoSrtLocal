# Meta 广告 XHR 通道账户时区 + Playwright 线程隔离（2026-05-09）

承接 [2026-05-09 XHR Token Channel](2026-05-09-meta-ads-xhr-token-channel.md) + [2026-05-07 多账户](2026-05-07-meta-ads-multi-account-design.md) + [2026-05-08 业务日对齐](2026-05-08-analytics-business-date-alignment-fix.md)。Issue 锚点：[AUT-23](mention://issue/5e3453c6-7a8e-4452-a831-fb38425c462b)。

## 背景与触发事件

2026-05-09 当天 BJ 业务日下 `meta_ad_realtime_import_runs` 在 newjoyloo_bak（`act=1861285821213497`）账户上出现两类生产故障：

1. **成功但空**：5/9 多次 tick `status=success`、`channel=xhr_api`，但 `rows_imported=0` / `spend_usd=0`，与 Meta Ads Manager UI 上同时段显示的 `成效金额 ~$200~$500` 完全对不上。
2. **session 失败**：5/9 15:18 一次 tick 抛 `session: It looks like you are using Playwright Sync API inside the asyncio loop. Please use the Async API instead.`，session 整体失败拖累所有 xhr_api 账户。

两类失败都集中在 XHR 通道，CSV 通道未受影响。看板「已分摊广告费」当天连续多个 tick 读到 0。

## 根因

### 根因 1：XHR `time_range` 没带账户时区

[`tools/roi_hourly_sync.py::_sync_meta_account_in_page_api`](../../../tools/roi_hourly_sync.py) 与 [`tools/meta_daily_final_sync.py::_sync_account_via_xhr_api`](../../../tools/meta_daily_final_sync.py) 都直接用：

```python
time_range = {"since": business_date.isoformat(), "until": business_date.isoformat()}
```

这里 `business_date` 是按 BJ 16:00 cutover 算出来的纯日期字符串，被原样塞给 Meta `/insights` 端点。Meta 的 `/insights` 把 `time_range.since/until` 当成**账户配置时区**下的 calendar date 解释（`account.timezone_name`，UI 默认按 `America/Los_Angeles`）。两条信息不在同一个时区坐标下，造成的后果：

- 当 BJ 业务日的 cutover 边界刚过、PDT 自然日还没真正开始时（如 BJ 16:00–17:00 ≈ PDT 01:00–02:00），Meta 在该 PDT 自然日下的累计 spend 极小或为 0 → 我们看到的 row_count = 0。
- Meta UI 上看到的「累计 $200~$500」是**当前 PDT 自然日**或上一个 PDT 自然日的 cumulative，跟我们查询的日期错位 → 看板对不上。

CSV 通道没踩到这条是因为它从 Ads Manager UI 抓 CSV，UI 本身按账户时区渲染当日数据，等价于隐式带上了正确时区。

### 根因 2：`open_meta_ads_session()` 无法在 asyncio loop 线程里运行

[`appcore/meta_ads_in_page_fetch.py::open_meta_ads_session`](../../../appcore/meta_ads_in_page_fetch.py) 直接用 `playwright.sync_api.sync_playwright`。Playwright 内部有一个硬断言：调用 `sync_playwright()` 的线程上不能有 running asyncio loop，否则抛 `It looks like you are using Playwright Sync API inside the asyncio loop`。

虽然现网默认调用栈是：

- 实时 cron：`tools/roi_hourly_sync.py` 主进程 → 没有 asyncio loop。
- 收盘日 cron：`tools/meta_daily_final_sync.py` 主进程 → 同上。
- Web 手动同步：`web/routes/order_analytics.py::meta_ad_account_manual_sync_start` → `socketio.start_background_task(...)`（async_mode=`threading`）→ 普通 `threading.Thread` → 同上。

但 5/9 15:18 实测确实命中过这条断言。可能的原因：上游某条 path（手动同步启动器、临时调试入口、新接的 watchdog、某条带 `asyncio.run()` 的 tooling）在同一个线程里先建过 loop；或某个第三方依赖（包括 Playwright 自身 `async_api` 的协程残留）未清理 thread-local loop。无论上游是谁，`open_meta_ads_session()` 都不应该假设自己运行在「干净的同步线程」上。

## 设计

### 数据模型变更

`MetaAdAccount` 新增 `timezone` 字段：

```diff
@dataclass(frozen=True)
class MetaAdAccount:
    code: str
    account_id: str
    business_id: str
    csv_prefix: str
    store_codes: tuple[str, ...]
    enabled: bool
    label: str = ""
    note: str = ""
    column_preset: str = LEGACY_COLUMN_PRESET
    sync_mode: str = DEFAULT_SYNC_MODE
+   timezone: str = DEFAULT_ACCOUNT_TIMEZONE  # IANA name, e.g. "America/Los_Angeles"
```

- 缺省值：`America/Los_Angeles`。Meta US 账户最常见的默认时区，覆盖 newjoyloo / newjoyloo_bak / Omurio 三户。
- 取值：任意合法 IANA 时区字符串（`Asia/Shanghai`、`America/New_York`、`UTC` 等）。`_coerce_account` 在写入前用 `zoneinfo.ZoneInfo(value)` 校验；非法值降级到默认值并打 warning。
- `system_settings.meta_ad_accounts` JSON 列表里每条 account 多一个 `timezone` 字段；老配置无 `timezone` 字段时默认到 `America/Los_Angeles`，零迁移。
- UI（「广告账户」Tab）后续 PR 接入下拉选择；本 PR 只落字段 + 后端逻辑。

### `_account_xhr_time_range(account, business_date)` 共享 helper

写在 [`appcore/meta_ad_accounts.py`](../../../appcore/meta_ad_accounts.py)，realtime + daily_final 两条 path 共用一份实现。

```python
def account_xhr_time_range(account, business_date) -> dict[str, str]:
    """把 BJ 业务日 [BJ 16:00 D, BJ 16:00 D+1) 映射到账户时区下的
    Meta /insights time_range（calendar date 范围，inclusive）。"""
```

算法：

1. 把 BJ 业务窗口起始 / 结束（aware）转成账户时区下的 aware datetime。
2. `since` = 起始时刻所在的账户时区 calendar date。
3. `until` = 结束时刻所在的账户时区 calendar date；当结束时刻恰好落在午夜（offset 与 BJ 完全对称的边界场景）时回退到上一日，避免无意义地多覆盖一整天。
4. 返回 `{"since": "YYYY-MM-DD", "until": "YYYY-MM-DD"}`。

实测口径：

| 账户时区 | BJ 业务日 D 在该时区下的窗口 | 返回值 |
|---|---|---|
| `America/Los_Angeles`（PDT, UTC-7） | [PDT 01:00 D, PDT 01:00 D+1) | `{since=D, until=D+1}`（覆盖 PDT 自然日 D 全天 + D+1 凌晨 1 小时） |
| `America/Los_Angeles`（PST, UTC-8） | [PST 00:00 D, PST 00:00 D+1) | `{since=D, until=D}`（恰好对齐 PST 自然日 D） |
| `Asia/Shanghai`（UTC+8） | [BJ 16:00 D, BJ 16:00 D+1) | `{since=D, until=D+1}`（跨 BJ 自然日 D 的下半天 + D+1 的上半天） |
| `UTC` | [UTC 08:00 D, UTC 08:00 D+1) | `{since=D, until=D+1}` |

宁可多覆盖一天也不少覆盖：Meta `/insights` 在 `time_increment=1` 下按账户时区自然日分行返回，多出来的那天若实际无 spend 仍然只是 0 行，不会污染我们的 BJ 业务日聚合（聚合在 import 侧统一按 BJ business_date 入库）。

### Playwright 线程隔离

`open_meta_ads_session()` 内部包一层「跑 sync_playwright 的工人线程」：

```python
@contextmanager
def open_meta_ads_session(...):
    if _has_running_asyncio_loop():
        with _isolated_playwright_session(...) as session:
            yield session
    else:
        with _direct_playwright_session(...) as session:
            yield session
```

- `_has_running_asyncio_loop()`：用 `asyncio.events._get_running_loop()`（Py3.10+ 公认的探测口径，CPython internals 长期稳定；不依赖 `asyncio.get_event_loop()` 的 deprecated 行为）。返回非 None 即认为命中。
- `_isolated_playwright_session(...)`：起一个 `concurrent.futures.ThreadPoolExecutor(max_workers=1)`，在那个线程里完成 `sync_playwright().start()` → 拿 `Playwright` 对象 → 连 CDP → 开 page → harvest token → 返回 `MetaAdsSession`。`MetaAdsSession.fetch_insights()` 以及 `session.page.evaluate(...)` 都通过这个 executor 调度（线程亲和：所有 sync_playwright 操作必须在同一个线程上）。
- 退出 ctx 时同步关闭 page、`Playwright.stop()`、shutdown executor；任何一步异常都不能阻塞 lock 释放。
- `MetaAdsSession.runner` 已经是测试注入点（见 [`appcore/meta_ads_in_page_fetch.py`](../../../appcore/meta_ads_in_page_fetch.py)）；线程隔离场景下 `runner` 默认值改为「调度到那个 executor 上跑 page.evaluate」，保持 `fetch_insights` 调用方的语义零变更。

### CDP 锁顺序

锁仍然在 `meta_ads_cdp_lock(task_code="meta_ads_in_page_session", ...)` 外层；线程隔离只发生在锁内部（拿到锁后才起工人线程跑 sync_playwright）。锁文件 `/data/autovideosrt/browser/runtime-meta-ads/automation.lock` 不变，与 [AUT-21](mention://issue/61856106-f124-4e60-8f4b-85fec835aa71) 锁治理解耦。

## 测试

新增：

- `tests/test_meta_ads_account_timezone.py`
  - `MetaAdAccount.timezone` 默认值 + 序列化 / 反序列化 / 非法值降级
  - `account_xhr_time_range(account, business_date)` 在 PDT / PST / Asia/Shanghai / UTC 四种时区下的 since/until
  - DST 切换边界（2026-03 第二个周日 / 2026-11 第一个周日，PDT ⇄ PST 切换日）的稳定性
- 在 `tests/test_meta_ads_in_page_fetch.py` 扩 case：模拟「线程内已有 running asyncio loop」，确认 `open_meta_ads_session` 走线程隔离 fallback、`fetch_insights` 仍然能走通 mock runner。

扩展：

- `tests/test_roi_hourly_sync_meta_multi_account.py`：xhr_api 账户带不同 timezone 时 `session.fetch_insights` 收到的 `time_range` 与 `account_xhr_time_range(account, business_date)` 严格相等。
- `tests/test_meta_server_sync_tools.py` 的 daily_final 测试同步覆盖 `account.timezone` 的 time_range 推导。

最少必跑：

```
pytest tests/test_meta_ads_account_timezone.py \
       tests/test_roi_hourly_sync.py \
       tests/test_roi_hourly_sync_meta_multi_account.py \
       tests/test_meta_ads_xhr_token.py \
       tests/test_meta_ads_in_page_fetch.py \
       tests/test_meta_server_sync_tools.py \
       tests/test_meta_ad_manual_sync.py \
       tests/test_order_analytics_ads.py -q
```

端到端冒烟：

1. 在 `system_settings.meta_ad_accounts` 给 newjoyloo_bak 加 `"timezone": "America/Los_Angeles"`，`sync_mode` 维持 `xhr_api`。
2. 跑 `python tools/roi_hourly_sync.py --once`。
3. 在 `meta_ad_realtime_import_runs.summary_json.account_results[*]` 验证：`channel=xhr_api`、`status=success`、`spend_usd > 0`。
4. 把账户 `timezone` 临时改成 `Asia/Shanghai` 再跑一轮，验证 helper 推出的 `time_range` 是 `{since=D, until=D+1}`、Meta 仍然返回有 spend。
5. 在某线程（如临时 `python -c` + `asyncio.run`）里强制存在 running loop，调用 `open_meta_ads_session()` → 不再触发 `Playwright Sync API inside the asyncio loop`。

## 不在本期做的事

- 不重写 `meta_ads_in_page_fetch` 为 `async_api`（变更面太大，会破现有测试 fixture）。
- 不在本 PR 加 UI 时区下拉；CLAUDE.md 文案 + JSON 字段先就位，UI 下个 PR。
- 不动 `_meta_business_date` / BJ 16:00 cutover 计算，不改 DB schema。
- 不重做 runtime-meta-ads 锁治理（与 [AUT-21](mention://issue/61856106-f124-4e60-8f4b-85fec835aa71) 同源，单独处理）。
- 不动 CSV 通道（保持「dashboard 渲染时区 = 账户时区」隐式正确性）。

## 文档锚点

- 本 spec：`docs/superpowers/specs/2026-05-09-meta-ads-account-timezone-and-async-fix.md`
- 上游 XHR token：[`docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md`](2026-05-09-meta-ads-xhr-token-channel.md)
- 多账户：[`docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md`](2026-05-07-meta-ads-multi-account-design.md)
- 业务日对齐：[`docs/superpowers/specs/2026-05-08-analytics-business-date-alignment-fix.md`](2026-05-08-analytics-business-date-alignment-fix.md)
- CLAUDE.md「Meta 广告多账户同步」段需追加 `timezone` 字段约束 + Playwright 线程隔离 SOP。
