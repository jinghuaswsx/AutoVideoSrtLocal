# 推送历史广告数据日终 + 实时合并口径

- 状态：active
- 背景：推送历史按当前 Meta 业务日读取实时广告数据。业务日切换后、前一日 daily final 尚未同步完成前，前一日实时累计会从推送历史里消失，导致素材管理与推送历史的广告费/ROAS 不一致。

## 目标

推送历史里的广告费、广告 ROAS、广告详情必须体现同一素材的整体投放数据：

1. 已有日终数据时，以 `meta_ad_daily_ad_metrics` 为准。
2. daily final 尚未覆盖的业务日，使用 `meta_ad_realtime_daily_ad_metrics` 中每个 `(business_date, ad_account_id)` 的最新 `snapshot_at`。
3. 同一 `(business_date, ad_account_id)` 不允许 daily 与 realtime 双重计入。
4. 产品与素材匹配规则沿用现有推送历史：产品 code/product_id 匹配，且广告名包含推送素材文件名或展示名。

## 非目标

- 不改 Meta 同步任务。
- 不把浏览、加购、view_content 等非购买 action value 当作购买金额。
- 不改变推送历史的日期筛选含义；日期筛选仍只筛 `media_push_logs.created_at`。

## 验证

- 单元测试覆盖“当前业务日已切到新一天，但前一日 daily final 未覆盖时，推送历史仍合并前一日最新 realtime + 当前日最新 realtime”。
- 保留已有“当前实时广告数据可进入推送历史”的回归测试。
