# Meta 实时广告 XHR fetch 瞬断重试修复

日期：2026-06-12

## 文档锚点

- [AGENTS.md](../../../AGENTS.md)：实时大盘 / Meta 多账户同步硬规则，实时表 fallback 必须按 `(business_date, ad_account_id)` 取各账户最新 snapshot。
- [appcore/order_analytics/CLAUDE.md](../../../appcore/order_analytics/CLAUDE.md)：实时大盘业务日、店铺筛选和数据质量水位规则。
- [2026-05-09-meta-ads-xhr-token-channel.md](2026-05-09-meta-ads-xhr-token-channel.md)：Meta Ads Manager in-page XHR API 通道。
- [2026-06-04-ad-order-sync-schedule-design.md](2026-06-04-ad-order-sync-schedule-design.md)：`autovideosrt-roi-realtime-sync.timer` 每 20 分钟触发 ROI / 订单 / 日内广告同步。
- [docs/analytics-data-quality-guardrails.md](../../analytics-data-quality-guardrails.md)：实时大盘必须返回广告数据水位。

## 生产现象

用户反馈「数据分析 -> 实时大盘」选择今天时，广告同步时间停在上一轮，已超过一个同步周期未更新。

2026-06-12 生产只读排查：

- `autovideosrt-roi-realtime-sync.timer` 仍按 20 分钟触发。
- `meta_ad_realtime_import_runs` 在 `2026-06-12 21:40` 和 `22:00` 两轮全账户失败。
- 失败原因一致：`in-page /insights failed: Page.evaluate: TypeError: Failed to fetch`。
- `roi_realtime_daily_snapshots` 仍写入 `ad_data_status='pending_source'` 的快照，并沿用各账户最近成功 spend，避免金额归零。
- `2026-06-12 22:20` 下一轮自动恢复，`get_realtime_roas_overview('2026-06-12')` 返回 `freshness.last_ad_updated_at=2026-06-12 22:20:55`。

## 根因判断

这不是实时大盘读取 SQL 或缓存失效错误；页面时间停滞是上游 Meta in-page XHR 通道连续两轮没有写入新的广告源表行。

`appcore.meta_ads_in_page_fetch.MetaAdsSession.fetch_insights()` 当前只把 OAuth code 190 分类成 token 失效；对 Playwright / browser page 里临时出现的 `TypeError: Failed to fetch` 只抛普通 `MetaAdsInPageFetchError`。`tools.roi_hourly_sync._sync_meta_realtime_daily()` 捕获后直接把该账户标失败，不会在同一轮重新打开 Ads Manager session 或刷新 token 重试。

## 修复目标

1. 对 in-page `/insights` 的临时 fetch 失败增加一次受控重试。
2. 重试必须重新打开 `open_meta_ads_session()`，避免复用可能坏掉的 page / cookie context。
3. 重试只覆盖 `MetaAdsInPageFetchError` 中的 transient fetch 失败，不吞掉登录验证、权限、参数错误等确定性异常。
4. 保持多账户隔离：第一次 session 内成功的账户不重复导入；失败账户进入第二个 session 单独重试。
5. 不改变 systemd timer 频率、不改 Meta 业务日口径、不改 DB schema。

## 实现范围

- `appcore/meta_ads_in_page_fetch.py`
  - 增加 helper 判断 transient in-page fetch failure，识别 `TypeError: Failed to fetch` 这类浏览器 fetch 瞬断。
- `tools/roi_hourly_sync.py`
  - `_sync_meta_realtime_daily()` 对 xhr_api 账户先跑第一轮 session。
  - 第一轮中仅 transient 失败的账户进入第二轮 session 重试一次。
  - 其它异常仍按现有路径记录失败。
- `tests/test_roi_hourly_sync_meta_multi_account.py`
  - 增加回归测试：第一个 session 对账户 A 抛 `TypeError: Failed to fetch`，第二个 session 成功，最终 run status 为 success，且打开了两次 session。

## 非目标

- 不在本次调整中改 `roi_realtime_daily_snapshots` 写入逻辑；失败时沿用各账户最近成功 spend 是既有多账户防丢失策略。
- 不把失败快照伪装成成功快照；`ad_data_status='pending_source'` 继续提示数据源未更新。
- 不改账号同步模式、登录凭据或浏览器 profile。

## 验证方式

Focused tests：

```bash
python3 scripts/pytest_related.py --base origin/master --run
pytest tests/test_roi_hourly_sync_meta_multi_account.py -k "xhr" -q
pytest tests/test_meta_ads_in_page_fetch.py -q
```

生产只读验证：

```bash
/opt/autovideosrt/venv/bin/python - <<'PY'
from appcore.order_analytics import get_realtime_roas_overview
result = get_realtime_roas_overview('2026-06-12')
print(result['freshness'])
print(result['summary']['ad_spend'])
PY
```

预期：当前页面返回的 `freshness.last_ad_updated_at` 为最近成功同步时间；后续若单轮 `TypeError: Failed to fetch`，同一轮会打开新 session 重试一次。
