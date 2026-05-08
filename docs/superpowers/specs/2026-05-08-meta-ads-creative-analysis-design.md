# Meta 广告素材表现分析设计（2026-05-08）

## 背景

`/order-analytics` 的“广告分析”当前主要围绕广告系列和订单关联，能看产品维度的广告费、购买、ROAS 和店小秘订单，但不足以回答“哪个素材跑得好”。2026-05-08 的 `newjoyloo_old` 历史回填需求要求按 `campaign / ad_set / ad` 三个层级逐日抓取，并把结果放到“数据分析 → 广告分析”里用于素材判断。

现有锚点：

- `docs/superpowers/specs/2026-04-24-data-analysis-ad-analytics-design.md`：广告分析 Tab 的长期导入和产品关联基础设计。
- `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md`：Meta 多账户、`newjoyloo_old` 历史账户、显式旧户同步入口和账户映射。
- `Meta 数据导出.md`：历史 CSV 导出、CDP 端口、每日 campaign/ad 导入经验。

## 目标

1. “广告分析”默认进入“素材榜”，直接展示 ad 层级素材表现。
2. 支持 `campaign / ad_set / ad` 三层日数据，可按时间范围、广告账户、产品、匹配状态筛选。
3. 能从素材行反查所属 Campaign 和 Ad Set，并向上汇总判断哪个广告系列、广告组、素材组合表现最好。
4. 同周期关联店小秘订单指标，用于判断 Meta 站内转化和真实订单表现是否一致。
5. `newjoyloo_old` 历史回填进度可见，抓取失败日和三层金额不一致要能被发现。
6. 临时历史回填任务不接入 Web “定时任务”管理，但必须有独立进度记录和 CDP 锁，避免和现有 Meta 同步冲突。

## 非目标

- 不把本次 10 分钟一轮的历史回填注册到 `appcore/scheduled_tasks.py` 或 Web 定时任务模块。
- 不改广告账户配置模型；继续使用 `system_settings.meta_ad_accounts`。
- 不删除或覆盖旧账户历史数据；所有数据按 `ad_account_id` 分行。
- 不做复杂归因模型，本期仍按 Meta business date 和现有店小秘订单窗口口径关联。

## 数据模型

现有日表包含：

- `meta_ad_daily_campaign_metrics`
- `meta_ad_daily_ad_metrics`

为了完整支持三层分析，新增：

- `meta_ad_daily_adset_metrics`

`campaign / ad_set / ad` 三张日表应保持共同字段口径：

- 账户：`ad_account_id`、`ad_account_name`
- 日期：`report_date`、`meta_business_date`、`meta_window_start_at`、`meta_window_end_at`
- 层级标识：
  - Campaign：`campaign_id`、`campaign_name`
  - Ad Set：`campaign_id`、`campaign_name`、`adset_id`、`adset_name`
  - Ad：`campaign_id`、`campaign_name`、`adset_id`、`adset_name`、`ad_id`、`ad_name`
- 产品匹配：`product_code`、`matched_product_code`、`product_id`
- 指标：`result_count`、`result_metric`、`spend_usd`、`purchase_value_usd`、`roas_purchase`、`link_clicks`、`add_to_cart_count`、`initiate_checkout_count`、`impressions`、`raw_json`

唯一键按 `ad_account_id + meta_business_date + 层级 id/name` 建立，重复抓同一天同一层级时覆盖更新，不叠加。

## 抓取与进度

历史回填按用户指定范围 `2026-01-01` 到 `2026-05-08`，账户 code 为 `newjoyloo_old`，显式选择 disabled 旧户。

临时任务行为：

- 每 10 分钟启动一轮。
- 每轮最多抓 5 个自然日。
- 每天依次抓 `campaigns`、`adsets`、`ads` 三个层级。
- 每成功导入一天后记录进度，任务可中断后续跑。
- 到 `2026-05-08` 全部成功后自动结束，不再继续调度。

进度建议写到独立 JSON 或临时表，至少包含：

- `account_code`
- `account_id`
- `start_date`
- `end_date`
- `next_date`
- `completed_dates`
- `failed_dates`
- `last_run_started_at`
- `last_run_finished_at`
- `last_error`
- 每日三层文件路径、导入行数、花费合计

## CDP 锁

Meta Ads Manager CDP 是共享资源，临时回填不得和现有广告同步并发操作浏览器。

锁规则：

- 所有会连接 `META_AD_EXPORT_CDP_URL` 并点击 Meta 导出的流程，共用同一把 Meta CDP 锁。
- 临时回填拿不到锁时，本轮直接记录 `skipped_lock_busy`，等待下一轮，不强行抢占。
- 锁必须带超时和持有者信息，避免进程异常退出后永久锁死。
- 锁日志要记录持有者、账户 code、目标日期、层级、开始结束时间。

## 页面结构

“数据分析 → 广告分析”新增子 Tab：

1. **素材榜**（默认）
2. **层级树**
3. **产品汇总**
4. **数据诊断**

通用筛选区：

- 日期范围：今天、昨天、本周、上周、本月、上月、自定义。
- 广告账户：默认全部，可选 `newjoyloo`、`newjoyloo_old`、`Omurio`。
- 产品/素材搜索：匹配产品名、product_code、campaign/adset/ad 名称。
- 匹配状态：全部、已匹配产品、未匹配产品。
- 最小花费/最小购买数：用于排除样本太小的数据。

## 素材榜

默认视图按 ad 层级聚合，按“有效素材评分”降序展示。

核心列：

- 素材名
- 产品 / product_code
- Campaign
- Ad Set
- 活跃天数
- 花费
- Meta 购买
- 购物价值
- Meta ROAS
- CPA
- 展示
- 链接点击
- CTR
- 加购
- 发起结账
- 店小秘订单数
- 店小秘销售件数
- 店小秘销售额
- 店小秘 ROAS
- 状态标签

状态标签：

- `放量中`：花费和订单量都达到阈值，店小秘 ROAS 达标。
- `高效`：ROAS 高、CPA 低，但花费未达到放量阈值。
- `潜力`：点击、加购或发起结账好，但购买不足。
- `烧钱无转化`：花费达到阈值且 Meta/店小秘购买都差。
- `数据不足`：花费或展示太低，不参与主要排序。

评分不直接替代原始指标，只用于默认排序。页面必须允许用户按花费、购买、ROAS、CPA、店小秘 ROAS 等列手动排序。

## 层级树

层级树用于解释素材表现来源：

```text
Campaign
  Ad Set
    Ad
```

每一层展示同一套汇总指标。展开 Campaign 后能看到它下面 Ad Set 的花费、ROAS 和订单贡献；展开 Ad Set 后能看到具体素材。这样可以判断：

- 好素材是否集中在某个 Campaign。
- 同一素材在不同 Ad Set 中是否表现不同。
- 某个 Ad Set 是否整体拖累 Campaign。

## 产品汇总

产品汇总保留现有“广告 × 订单关联分析”的价值，但数据源改为日表聚合，并允许从产品行钻到该产品下的素材榜。

产品汇总适合回答：

- 哪个产品整体广告费最高。
- 哪个产品店小秘 ROAS 最好。
- 未匹配广告费是否影响产品判断。

## 数据诊断

诊断区用于运维和数据可信度检查：

- 历史回填进度：目标范围、下一天、已完成天数、失败天数、最后错误。
- 每日三层抓取状态：campaign/ad_set/ad 是否都成功。
- 三层金额校验：同一账户同一天 campaign、ad_set、ad 花费是否一致或接近。
- 未匹配列表：Campaign、Ad Set、Ad 三层未匹配产品的记录。
- CDP 锁跳过记录：展示最近 `skipped_lock_busy` 事件。

## 接口设计

新增或扩展 API：

- `GET /order-analytics/ad-creative-summary`
  - 返回素材榜数据。
  - 参数：`start_date`、`end_date`、`account_code`、`product_id`、`q`、`match_status`、`min_spend`、`min_purchases`、`sort`、`direction`。
- `GET /order-analytics/ad-hierarchy`
  - 返回 Campaign → Ad Set → Ad 树形汇总。
- `GET /order-analytics/ad-diagnostics`
  - 返回回填进度、缺失日期、三层金额校验和锁跳过记录。

现有 `GET /order-analytics/ad-summary` 可继续服务产品汇总，后续内部改用同一批日表聚合 helper，避免页面之间指标不一致。

## 验收标准

- 用户进入“广告分析”默认看到“素材榜”。
- 选择 `newjoyloo_old` 和 `2026-01-01 ~ 2026-05-08` 后，可以看到 ad 层素材排名。
- 素材行能显示所属 Campaign 和 Ad Set。
- 层级树能从 Campaign 展开到 Ad Set，再展开到 Ad。
- 产品汇总与素材榜在相同时间范围内的总花费一致。
- 诊断区能显示回填进度和三层金额校验结果。
- 临时回填任务不会出现在 Web “定时任务”模块。
- 临时回填拿不到 Meta CDP 锁时不并发导出，只记录跳过并等下一轮。

## 验证

后续实现至少覆盖：

- 数据聚合单测：ad 层素材榜、Campaign → Ad Set → Ad 树、产品汇总金额一致。
- 路由测试：广告分析默认素材榜、API 参数校验、空/加载/错误状态。
- 导入测试：三层 CSV 解析、重复日期覆盖更新、`newjoyloo_old` disabled 账户显式回填。
- 锁测试：锁占用时临时任务跳过，不启动 Playwright CDP 导出。
- 手工验证：测试环境打开 `/order-analytics`，筛选旧户历史日期范围，确认素材榜、层级树、诊断区可用。

## Docs-anchor

- `docs/superpowers/specs/2026-04-24-data-analysis-ad-analytics-design.md`
- `docs/superpowers/specs/2026-05-07-meta-ads-multi-account-design.md`
- 本文件
