# Meta 广告同步 XHR Token 通道（2026-05-09）

## 背景与触发事件

- 现有 Meta 广告实时 / 收盘同步生产链路只走「browser CSV export」一条通道：Playwright 接管 DXM01-Meta（CDP 9222）→ 打开 Ads Manager `manage/{level}` → 点击「导出」按钮 → 拦截 download 响应保存 CSV → 解析入库。
- 2026-05-08 17:00 起 newjoyloo_bak 浏览器导出连续 600s timeout。CSV 导出依赖「按钮 → 弹窗 → 后台生成 → 下载流」整条链路，任何一步卡 10 分钟就拉爆整轮 sync。看板「已分摊广告费」整列归零，无法核账。
- 现有 Marketing API 通道（`_fetch_meta_marketing_api_insights`）已实现端到端，但生产从未启用，原因是它要求 `META_MARKETING_API_ACCESS_TOKEN` 这种 app-bound token，需申请 `ads_read` scope，公司没配。
- 2026-05-09 用 [drafts/meta_ads_xhr_probe.py](../../../drafts/meta_ads_xhr_probe.py) 做的只读探针发现：Ads Manager 页面渲染表格时，浏览器自身在调 `https://adsmanager-graph.facebook.com/v22.0/act_<id>/am_tabular`，URL query string 里直接带 `access_token` —— 这是 user-bound token，从已登录的页面 session 里发出来的，**完全可以拦截下来转手给 Marketing API 直接 HTTP 调用**，不需要任何 app token / scope 申请流程。

## 目标

1. 新增「XHR Token API」通道作为 CSV 导出通道的**并行备选**。两条通道**共存**，账户级开关切换，互不干扰。
2. CSV 导出通道**保持原状**，不重构、不改字段口径、不删除。导出弹窗恢复后可一键切回。
3. 新通道复用现有 `_fetch_meta_marketing_api_insights` + import path + DB schema，不改 schema、不加 migration。
4. 新通道支持 Campaign / Ad Set / Ad 三个层级（覆盖现有 CSV 通道的范围；现有 `_fetch_meta_marketing_api_insights` 只做 campaign，需扩到三层级）。
5. Token 失效时自动重开页面捕获新 token，不需要人工介入。
6. 单账户失败不影响其他账户，遵循 [2026-05-07 多账户 spec](2026-05-07-meta-ads-multi-account-design.md) 已确立的隔离原则。
7. 抓取频率可在配置层降低（可选每小时 / 每两小时 / 仅收盘日跑），减小 token 刷新成本与封号风险。

## 非目标

- 不替换现有 CSV export 通道；它继续作为默认 fallback。
- 不引入 OAuth 应用注册 / app token 申请流程。
- 不改 `meta_ad_accounts` 以外的配置 schema；不改 systemd timer / service。
- 不做 DOM 表格抓取（virtualized scroll + locale 风险高，已通过探针证明 XHR 通道更优，DOM 抓取从此排除）。
- 本期不接入「广告创意预览图 / 视频缩略图」等 metadata（GraphQL 端点能拿，但与"算账"目标无关，留给后续）。

## 关键发现（探针实测，2026-05-09）

探针报告：[drafts/meta_ads_xhr_probe_campaigns_20260509_111636.summary.txt](../../../drafts/meta_ads_xhr_probe_campaigns_20260509_111636.summary.txt) + 同名 `.jsonl`。

### `am_tabular`（Ads Manager 内部表格端点）

- URL: `https://adsmanager-graph.facebook.com/v22.0/act_<account_id>/am_tabular?...`
- 同一个 endpoint 通过 `level` 参数支持 `account` / `campaign` / `adset` / `ad` 四个维度。
- query 参数全部暴露在 URL 上：`access_token`、`level`、`column_fields`、`time_range`、`filtering`、`limit`、`locale`、`action_attribution_windows` 等。
- 返回 `{"data":[{"headers":{...},"rows":[{"dimension_values":[...],"atomic_values":[...],"action_values":[...]}]}]}`。

### `/insights` 公共 Marketing API（已有调用代码）

- URL: `https://graph.facebook.com/v25.0/act_<account_id>/insights?fields=...&level=...&time_range=...`
- Bearer `Authorization: Bearer <token>` 头，token 即为从 am_tabular URL 抓到的同一个。
- 响应 `{"data":[{...row...}], "paging":{"next":"..."}}`，已在 `_fetch_meta_marketing_api_insights` 处理分页。
- 我们用这个端点而不是 `am_tabular`，因为：(a) 它是 Meta 官方公开 API，schema 文档明确，跨版本稳定；(b) 现有代码已经全套就绪；(c) `am_tabular` 是内部接口，列名是 ID（如 `OUTCOME_SALES`）和 enum，需要额外字典翻译，反而麻烦。

### `/api/graphql/`

- 提供 campaign/adset/ad 的 metadata（name、creative、link 等）。
- **本期不使用**：name 字段 `/insights` 端点已经返回（`campaign_name`），不需要走 GraphQL。

### Token 形态

- `access_token` 是 user-bound 的 Page Token，长度约 200 字符，前缀 `EAABsbCS1...`。
- 来自页面 session，过期时间观察约 **1-2 小时**（具体由 Meta 服务端控制，无法从 token 本身解码）。
- Token 与 user 绑定，与 `act=` 参数解耦——同一个 token 可以查询 user 有权限的所有广告账户（如 newjoyloo + Omurio），不需要每个账户单独取一个 token。

### Token **不能搬出页面**单独 HTTP 调用（2026-05-09 实测修正）

原 spec 假设 harvest 出来的 token 可以走独立 `urllib` 直接调 `graph.facebook.com/.../insights`，这条假设**作废**。实测：

| 调用方式 | 结果 |
|---|---|
| 外部 urllib `graph.facebook.com/v25.0/.../insights` Bearer header | HTTP 400 OAuth code 1 "Invalid request" |
| 外部 urllib `adsmanager-graph.facebook.com/v22.0/.../insights` query param | HTTP 400 OAuth code 1 |
| 外部 urllib `/me`（最基础认证测试） | HTTP 400 |
| `page.evaluate` fetch `graph.facebook.com/...`（页面 JS 上下文） | TypeError: Failed to fetch（CORS 拒） |
| **`page.evaluate` fetch `adsmanager-graph.facebook.com/v22.0/.../insights?access_token=...` `credentials:'include'`** | **HTTP 200，真实数据** |

原因：Meta 把这种 Page Token 当 first-party session token，服务端校验 cookie + Origin/Referer。token 单独搬出去缺上下文必然被拒。`graph.facebook.com` 没给 Ads Manager 域 CORS 白名单；只有 `adsmanager-graph.facebook.com` 子域允许跨源 + 接受 user token。

**修正后的调用约束**：

1. 数据获取**必须**通过已打开的 Ads Manager Playwright 页面里 `page.evaluate(... fetch ...)` 发出。
2. host 固定 `https://adsmanager-graph.facebook.com/v22.0/`（不是公共 `graph.facebook.com`）。
3. token 以 query param `access_token=...` 传（不是 Bearer header）。
4. fetch 选项必须带 `credentials: 'include'`，让浏览器自动附加 user 的 c_user / xs / datr 等 session cookie。
5. 现有 `_fetch_meta_marketing_api_insights`（urllib + Bearer header + 环境变量 token）**保持原状**，保留作为「app token API channel」的另一条独立通道；本次新通道叫「browser-context API channel」，不复用它的 HTTP 实现。
6. Token harvester 仍然必要（access_token 是 fetch URL 必须的 query param），但是只在 page 已经存在的情况下，从同一个 page 触发的 am_tabular request 里拿。

## 数据模型变更

### `system_settings.meta_ad_accounts` 新增字段 `sync_mode`

```diff
 [
   {
     "code": "newjoyloo",
     "label": "Newjoyloo",
     "account_id": "1861285821213497",
     "business_id": "476723373113063",
     "csv_prefix": "newjoyloo",
     "store_codes": ["newjoy"],
     "enabled": true,
+    "sync_mode": "csv_export",
     "note": "..."
   }
 ]
```

- 取值：`csv_export`（默认，等于现有行为）/ `xhr_api`（新通道）。
- 缺省值为 `csv_export`，老配置无感升级。
- UI：「广告账户」Tab 每行加一个 sync_mode 下拉，文案「同步方式：CSV 导出 / 网页接口拦截」。
- 后端校验：枚举两个值之一；其他字符串拒绝写入。
- 切换 sync_mode 不需要重启服务；下一轮 hourly sync 自动按新值走。

### `system_settings.meta_xhr_token_cache`（新键，单条 JSON）

token 缓存写到 `system_settings`，不引入新表：

```json
{
  "access_token": "EAABsbCS1...",
  "harvested_at": "2026-05-09T11:16:36",
  "expires_hint_at": "2026-05-09T13:16:36",
  "harvested_via_account": "newjoyloo"
}
```

- `expires_hint_at`：从 `harvested_at` 起 +90 分钟（保守值，比观察到的 1-2 小时短，留余量）。到期或更早遇 401 即刷新。
- `harvested_via_account`：harvest 时打开的页面对应的 account code，仅做日志归因。

### DB 表 schema

**不改任何表**。新通道写入完全复用：

- `meta_ad_realtime_import_runs`
- `meta_ad_realtime_daily_campaign_metrics`
- `meta_ad_realtime_daily_adset_metrics`（如已存在；本期视情况补一个 migration 仅当未建）
- `meta_ad_realtime_daily_ad_metrics`
- 收盘表 `meta_ad_daily_*`

需要在合并前确认 adset / ad 实时表是否存在。如果只有 campaign 表，本期实时通道只落 campaign，adset / ad 仅在收盘日同步；以后再补实时表 migration。

## 字段口径映射

`/insights` 返回字段 → DB 列：

| Marketing API 字段 | DB 列 | 备注 |
|---|---|---|
| `account_id` | `ad_account_id` | 直接落 |
| `account_name` | `ad_account_name` | |
| `campaign_id` | `campaign_id` | |
| `campaign_name` | `campaign_name` + `normalized_campaign_code` | normalized_campaign_code 沿用现有 normalize 函数 |
| `date_start` | 用于校验 = `business_date` | |
| `spend` | `spend_usd` | API 返回字符串数字，`Decimal()` cast；货币校验 `META_MARKETING_API_EXPECTED_CURRENCY` |
| `impressions` | `impressions` | int |
| `clicks` | `clicks` | int |
| `actions[].action_type ∈ META_PURCHASE_ACTION_TYPES` | `result_count` | 沿用 `_extract_purchase_metric` |
| `action_values[]` 同上类型 | `purchase_value_usd` | |
| 整条原始 row | `raw_json` | 留给后续审计 |

Adset / Ad 层级用 `level=adset` / `level=ad`，并把 `fields` 加上 `adset_id`/`adset_name` 或 `ad_id`/`ad_name`。

CSV 通道目前抓的「video_avg_play_time、link_clicks、add_to_cart_count、initiate_checkout_count、ecpm、cpc、cpm」等扩展指标：
- `clicks` 已包含。
- `link_clicks / add_to_cart / initiate_checkout` 都从 `actions` 数组里取（与 purchase 同机制，新增几个 action_type 常量）。
- `cpc / cpm` 由 `spend / clicks` / `spend / impressions * 1000` 推导，不再单独存储。
- `video_avg_play_time` 加进 `META_INSIGHTS_FIELDS`，在 schema 已有列 `video_avg_play_time` 的表上落。

## Token Harvester 设计

文件：`appcore/meta_ads_xhr_token.py`（新增）。

```python
def harvest_meta_ads_access_token(*, force_refresh: bool = False) -> str:
    """Return a usable Marketing API access_token.

    Reads from system_settings.meta_xhr_token_cache when fresh; otherwise
    opens a Playwright page against any enabled meta account, listens for
    an am_tabular request, extracts access_token from URL, writes cache,
    returns the token.
    """
```

流程：

1. **Cache hit**：`now < expires_hint_at` 且 `force_refresh=False` → 返回缓存 token。
2. **Cache miss / expired / force**：
   - `meta_ads_cdp_lock(task_code="meta_ads_xhr_token_harvest", timeout=120s)` 拿锁
   - `playwright.chromium.connect_over_cdp(DEFAULT_META_ADS_CDP_URL)`
   - 选第一个 enabled account，构造 `manage/campaigns?act=...` URL
   - **新开 tab**（不复用已有 tab，避免污染用户工作状态）
   - `page.on("request", ...)` 监听包含 `am_tabular` 的 URL（Playwright 高层 API，不需要原生 Fetch.enable）
   - `page.goto(target_url, wait_until="domcontentloaded")`，让 ReactJS 自然触发 am_tabular 请求
   - 一旦命中即 `parse_qs(url).get("access_token")`，关闭 tab
   - 上限 30s，超时 → `TokenHarvestError`
   - 把 token + harvest 时间写 `system_settings.meta_xhr_token_cache`

错误处理：

- 401 / OAuth error from in-page fetch（见下节） → 上层 caller 捕获后 `force_refresh=True` 重抓一次；再 401 → 标 failed，写日志，下一轮再试。
- 页面登录态失效（goto 后重定向到 `facebook.com/login`）：harvester 30s 内捕不到 am_tabular → raise `TokenHarvestError`，sync 标 failed，告警「需要人工登录 DXM01-Meta」（沿用现有告警通道）。
- harvester 不能持锁太久，因为 hourly sync 也要拿这把锁。把 harvest 操作压缩在 30s 内。

PR 1 已实现 + 16 个单测全部通过；端到端实测在 DXM01-Meta 上拿到 207 字符的有效 token。

## In-Page Fetcher 设计（取代原 spec 的 urllib 方案）

文件：`appcore/meta_ads_in_page_fetch.py`（新增）。

提供一个上下文管理器 + 批量取数函数，把"开浏览器 / 拿 token / 调多次 /insights / 翻页"封装成一次。

```python
@contextmanager
def open_meta_ads_session(*, cdp_url=None, lock_timeout=600) -> Iterator[MetaAdsSession]:
    """Acquire CDP lock, connect via Playwright, open Ads Manager,
    harvest token (or reuse cached), yield a session object that can
    be reused for many fetch calls in one browser visit."""

class MetaAdsSession:
    page: playwright.sync_api.Page
    access_token: str
    def fetch_insights(
        self,
        account_id: str,
        *,
        level: Literal["campaign", "adset", "ad"],
        time_range: dict,            # {"since": "...", "until": "..."}
        fields: list[str],
        time_increment: str = "1",
        limit: int = 500,
        max_pages: int = 200,
    ) -> list[dict]: ...
```

`fetch_insights` 实现：

1. 拼 URL：`https://adsmanager-graph.facebook.com/v22.0/act_<id>/insights?access_token=...&fields=...&level=...&time_range=...&time_increment=...&limit=...`
2. `self.page.evaluate(<JS>, params)` 在页面里发 fetch：
   ```js
   async ({url}) => {
     const all = [];
     let next = url, pages = 0;
     while (next && pages < MAX) {
       pages++;
       const r = await fetch(next, {credentials: 'include'});
       if (!r.ok) {
         const errBody = (await r.text()).slice(0, 800);
         throw new Error(`HTTP ${r.status}: ${errBody}`);
       }
       const j = await r.json();
       all.push(...(j.data || []));
       next = j.paging?.next || null;
     }
     return all;
   }
   ```
3. 单次 `fetch_insights` 失败抛 `MetaAdsInPageFetchError`，包含 status + body。上层 catch OAuth code 190（token 失效）后会 `harvest_meta_ads_access_token(force_refresh=True)` 然后重试一次。

session 跨多次调用复用同一个 page、同一个 token。一轮 sync 里 newjoyloo 三层 + Omurio 三层 = 6 次 `fetch_insights`，全在一个 `open_meta_ads_session()` 块内完成；锁只持有一次。

## 多账户复用同一 Token

实测确认 token 与 user 绑定（Ads Manager 用户授权过的所有 act 都能查），不与 `act=` 解耦。一次 harvest 即可服务一轮 sync 中所有 enabled 账户的 fetch_insights 调用。session 里只需要把 `account_id` 当参数传给 `fetch_insights`。

## Channel 调度

修改 `tools/roi_hourly_sync.py::_sync_meta_realtime_daily`：

```python
xhr_accounts = [a for a in accounts if a.sync_mode == "xhr_api"]
csv_accounts = [a for a in accounts if a.sync_mode != "xhr_api"]

# 1) 一次开浏览器，喂完所有 xhr_api 账户
if xhr_accounts:
    try:
        with open_meta_ads_session() as session:
            for account in xhr_accounts:
                try:
                    rows_by_level = {
                        level: session.fetch_insights(
                            account.account_id,
                            level=level,
                            time_range={"since": day, "until": day},
                            fields=META_INSIGHTS_FIELDS_BY_LEVEL[level],
                        )
                        for level in ("campaign", "adset", "ad")
                    }
                    _import_meta_realtime_api_rows(account, business_date, snapshot_at, rows_by_level)
                    summary.append({"code": account.code, "channel": "xhr_api", "status": "success"})
                except Exception as exc:
                    summary.append({"code": account.code, "channel": "xhr_api", "status": "failed", "error": str(exc)})
    except Exception as exc:  # session 整体失败（锁超时 / 浏览器挂 / token harvest 失败）
        for account in xhr_accounts:
            summary.append({"code": account.code, "channel": "xhr_api", "status": "failed", "error": str(exc)})

# 2) csv_export 账户走原路径，不动
for account in csv_accounts:
    try:
        _run_meta_ads_manager_export(...)
        _import_meta_realtime_campaign_rows(...)
        summary.append({"code": account.code, "channel": "csv_export", "status": "success"})
    except Exception as exc:
        summary.append({"code": account.code, "channel": "csv_export", "status": "failed", "error": str(exc)})
```

要点：

- xhr_api 账户**统一在一次 session 里**喂完，避免每个账户都各自开关浏览器。
- csv_export 账户走原 `_run_meta_ads_manager_export` 路径，**0 修改**。
- 两条通道相互独立，xhr_api session 整体失败不影响 csv_export 账户继续跑。
- `META_INSIGHTS_FIELDS_BY_LEVEL[level]` 三套字段集；campaign 沿用现有 `META_INSIGHTS_FIELDS`，adset / ad 各加 `adset_id/adset_name` / `ad_id/ad_name`。
- `_import_meta_realtime_api_rows(account, business_date, snapshot_at, rows_by_level)` 是新写的入库函数，把三层级 rows 落到对应表（campaign 落 `meta_ad_realtime_daily_campaign_metrics`，adset/ad 落各自实时表如已存在；不存在则该层级数据本期只写 raw_json + log，由后续 PR 补 migration）。

`tools/meta_daily_final_sync.py` 同步改造，逻辑相同。

## 入库

- API 通道返回的 row 与 CSV 通道的 row 经过 `_extract_purchase_metric` 后产生的字段集合一致。
- 写库走与 CSV 通道相同的 `_import_meta_realtime_*_rows` 函数，仅在调用方组装 row 时多一步 `_normalize_api_row(raw_dict)`，把 API 返回的 dict 映射到 CSV 通道用的字段名。
- `meta_ad_realtime_import_runs.summary_json.account_results[].channel = "xhr_api"`，方便监控两条通道的成功率对比。

## 测试计划

仓库现有 fixture：`tests/test_roi_hourly_sync.py`、`tests/test_roi_hourly_sync_meta_multi_account.py`。

新增：

- `tests/test_meta_ads_xhr_token.py`：
  - `parse_qs` 提取 `access_token` 的纯函数（无需 Playwright）。
  - cache 命中 / miss / 过期 / force_refresh 四个分支。
  - 401 → force_refresh 重试一次的语义。
- `tests/test_roi_hourly_sync_xhr_channel.py`（基于现有 multi-account 测试扩展）：
  - account.sync_mode='xhr_api' → 走 API 路径，mock token harvester + mock urlopen。
  - account.sync_mode='csv_export' → 走 browser export 路径，确保不被新通道污染。
  - 同一轮 sync 同时存在两种 sync_mode 账户的混合场景，两条通道独立成功 / 失败。
- 字段映射 unit test：`_normalize_api_row` 输入官方 API 示例 JSON → 输出与 CSV row 等价的 dict。

最少必跑：

```
pytest tests/test_roi_hourly_sync.py \
       tests/test_roi_hourly_sync_meta_multi_account.py \
       tests/test_meta_ads_xhr_token.py \
       tests/test_roi_hourly_sync_xhr_channel.py \
       tests/test_meta_server_sync_tools.py \
       tests/test_order_analytics_ads.py -q
```

端到端冒烟：

1. 把 newjoyloo 的 `sync_mode` 切到 `xhr_api`。
2. 跑 `python tools/roi_hourly_sync.py --once`。
3. 确认 token 已写入 `system_settings.meta_xhr_token_cache`。
4. 确认 `meta_ad_realtime_import_runs.summary_json` 里 newjoyloo 那条 `channel="xhr_api"`、`status="success"`、`rows>0`、`spend_usd>0`。
5. Order Profit 看板「已分摊广告费」恢复非 0。
6. 切回 `csv_export`，跑一轮，确认现有 CSV 通道仍正常。

## 部署 / 回滚

- 部署：`/opt/autovideosrt` git pull + restart 即可。新增 `system_settings.meta_xhr_token_cache` 是动态键，无 migration。
- 灰度：先把 newjoyloo（受影响最严重）切 `xhr_api`，Omurio 保持 `csv_export` 跑一周。两边数据交叉对比。
- 回滚：把账户的 `sync_mode` 改回 `csv_export`，下一轮 sync 自动走 CSV 路径。代码侧无需 revert。

## 开放问题

1. token 实际过期时间需要观察一周，看 90min `expires_hint_at` 是否合适，过短 → 频繁 harvest 浪费锁；过长 → 401 重试代价上升。
2. Meta 是否对单 token 高频调用 `/insights` 限频？现有代码已经从响应头读 `x-app-usage` / `x-ad-account-usage` / `x-business-use-case-usage`；上线后需要监控这些指标。
3. 三层级（campaign / adset / ad）数据量：单账户每天 ad 行数估计上限 ~1000；按 limit=500 分页 ≤ 2 页；现有 `META_MARKETING_API_MAX_PAGES=200` 完全够。
4. 实时表是否已存在 `meta_ad_realtime_daily_adset_metrics` / `meta_ad_realtime_daily_ad_metrics`？合并前需要扫描 `db/migrations/` 确认；如果只有 campaign 表，本期 xhr_api 通道实时只落 campaign 层级，adset / ad 仅在收盘日落到 daily 表。

## 文档锚点

- 本 spec：[docs/superpowers/specs/2026-05-09-meta-ads-xhr-token-channel.md](2026-05-09-meta-ads-xhr-token-channel.md)
- 上游多账户 spec：[docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md](2026-05-07-meta-ads-multi-account-design.md)
- 探针报告：[drafts/meta_ads_xhr_probe.py](../../../drafts/meta_ads_xhr_probe.py) + 输出 `drafts/meta_ads_xhr_probe_*_20260509_*.jsonl`
- CLAUDE.md 待补段落：「Meta 广告多账户同步（2026-05-07 起）」节末追加「2026-05-09 起 sync_mode 可选 csv_export / xhr_api」

## 实施顺序（提交粒度建议）

1. **PR 1（已完成）**：`appcore/meta_ads_xhr_token.py` harvest + cache + 16 个单测。**不改 sync 主路径**，单独可灰度。
2. **PR 2**：`appcore/meta_ads_in_page_fetch.py`：`open_meta_ads_session()` ctx + `MetaAdsSession.fetch_insights()`，含 page.evaluate fetch + 分页 + 错误处理 + 单测。
3. **PR 3**：`MetaAdAccount.sync_mode` 字段 + 「广告账户」Tab UI 切换 + `tools/roi_hourly_sync.py` 调度分支 + 入库函数 `_import_meta_realtime_api_rows`。
4. **PR 4**：`tools/meta_daily_final_sync.py` 同步改造。
5. **PR 5**：CLAUDE.md 文档更新（多账户节追加 sync_mode 说明）+ 监控告警接入（in-page fetch 失败率 / token harvest 失败率）。

每个 PR 上线后线上观察一两轮 sync，再推下一个。
