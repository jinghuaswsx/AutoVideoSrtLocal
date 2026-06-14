# 实时大盘加载优化设计（方案 A）

- 日期：2026-06-14
- 状态：设计已评审通过，待实现
- 关联模块：`appcore/order_analytics/realtime_cache.py`、`web/routes/order_analytics.py`、`appcore/scheduled_tasks.py`、`web/templates/order_analytics.html`
- 关联事故/规范：`appcore/order_analytics/CLAUDE.md`（实时大盘业务日 + 广告费分摊硬规则）、`appcore/order_analytics/_open_day_freshness.py`

## 1. 背景与问题（实测坐实）

公网访问实时大盘，刷新「今天 / 昨天」时常加载不出来。经代码分析 + Playwright 实测 + 并发压测（账号在 `14.103.60.217`）确认三条根因叠加：

### 1.1 缓存对常见操作命中不了
- 缓存（`realtime_cache.py`）**确实接入且工作正常**：命中（HIT）时 0.3–1.5s 秒开；24 并发立即重打全 HIT，1.5s 完成。
- 但 `get_freshness_marker()` 取的是**全局** `MAX(订单id / 利润行id / 快照id)`，**不分日期**。今天营业时间内任何一笔新订单写入 → 全局标记变化 → **所有日期（含昨天、上周等历史区间）已缓存结果被一起判失效**。历史数据没变却被今天的新订单「误伤」重算。这是 `yesterday`/`thisWeek` 也总 MISS 的真正原因。
- 今天的 `_ensure_open_day_profit_lines_for_realtime` → `backfill` 会写 `order_profit_lines`，**把自己的缓存标记也推变**（自我失效循环）。
- 首次进入、切换没缓存过的日期，都是新 cache_key → 必 MISS → 现算。

### 1.2 后端 MISS 现算在并发下严重劣化
- 单人空闲：单 scope MISS 现算 3–5s。
- 16 并发全 MISS 压测：p50=11.4s，p95=15.7s，**max=34.4s**（今天的 new scope），**已超过前端 30 秒超时**。
- 每个 scope 各跑一遍完整聚合（realtime.py 内 13 处查询点 / 90 SELECT）；今天还要 backfill 写库。MySQL 连接池 40（压测未顶满），慢源自并发查询互抢资源 + backfill 写锁。

### 1.3 前端「全或无」把单点慢放大成整块失败
- `loadRealtimeTopCards` 用 `Promise.all` 并发 global/new/old/unmatched 四个请求，**共享同一个 `AbortController`**。
- 任一请求 30 秒超时 `abort()` → 连带掐断其余三个 → `Promise.all` 整体 reject → 顶部卡片（含「订单/广告/快照时间」）全部落空显示 `-`。最慢一个决定全组生死。

## 2. 目标与验收

- **首要**：今天 / 昨天 / 本周 / 首次进入不再「整块加载不出来」。
- **验收标准**：
  1. 正常时段首次打开默认视图（全部店铺、新品窗口 7 天）≤2s 出数（命中预热缓存）。
  2. 后端某 scope 偶发慢/超时时，其余卡片照常渲染，不被连累。
  3. 历史区间（昨天 / 上周）不再被今天的新订单冲掉缓存。
  4. 数据新鲜度 ≤1 分钟（对齐用户选择）。

## 3. 方案总览

方案 A：**缓存按收盘分层 + 后台预热 + 前端 allSettled 解耦**，三组件独立可测。**不重构后端 4-scope 聚合结构**（那是方案 B，渗透 40+ 处、风险高，本次不做）。

## 4. 组件 1：缓存按「是否收盘」分层

修复 §1.1 的全局 marker 误伤。

### 4.1 open / closed 判定
- 由 route 计算 `current_business_date = current_meta_business_date()`。
- **open range**：`end_date >= current_business_date`（区间含今天）。→ today、thisWeek。
- **closed range**：`end_date < current_business_date`（纯历史）。→ yesterday、lastWeek、上月及更早。
- 该 `is_open_day` 布尔随 cache 读写一起传入 `realtime_cache.get/put`。

### 4.2 失效策略改造（`realtime_cache.py`）
- **closed range**：数据已收盘 → **不再与全局 freshness marker 比较**，改用纯时间 TTL（`_CLOSED_TTL_SECONDS = 1800`，30 分钟）。30 分钟内永远 HIT，不被今天新订单冲掉。（收盘日仍可能被夜间 backfill 微调，30 分钟刷新一次可接受。）
- **open range**：维持现有 60 秒短窗口（`_MIN_RECHECK_SECONDS`，对齐「最多旧 1 分钟」）。超 60 秒按现有 marker 逻辑（基本会失效），靠组件 2 预热在窗口内续命。硬 TTL 1800s 保留。
- `get/put` 签名增加 `is_open_day: bool`。closed 分支走时间 TTL；open 分支走原逻辑。

### 4.3 cache_key / 计算入口提取为单一真相源
- 当前 route `realtime_overview()`（1364–1394）里「构造 cache_params → make_cache_key → get → 算 → put」内联。
- 提取为公共函数 `realtime.get_realtime_roas_overview_cached(**kwargs) -> (result, cache_state)`（或置于 route 模块的内部 helper），**route 与预热任务共用**。
- 目的：cache_key 构造逻辑单点，预热绝不会因 key 不一致而白做。

## 5. 组件 2：后台预热（`scheduled_tasks.py` + 新 runner）

### 5.1 range 解析（⚠️ 必须对齐前端，不可复用 weekly 周日逻辑）
- 预热的 `start_date / end_date` 必须与前端 `orderAnalyticsMetaCalendar.resolveRange()` **逐字一致**，否则 cache_key 不匹配。
- 关键差异：**前端 `startOfWeek` 是周一起算**（`day = getDay()||7; date - day + 1`），`endOfWeek = 周一 + 6 = 周日`。**后端 `weekly_ai_report._week_start_sunday` 是周日起算，禁止复用。**
- 业务日基准：Meta 业务日（Asia/Shanghai、16:00 切日），用现有 `current_meta_business_date()`。
- 在后端新增小工具 `_resolve_meta_calendar_range(range_name, today)`，复刻前端 today/yesterday/thisWeek/lastWeek 的边界，并加单测对拍前端定义。
- 注意：`thisWeek.end = 本周日`，常为未来日期，保持与前端一致（前端本就如此传参）。

### 5.2 预热范围与参数
- 预热 range：**today、yesterday、thisWeek、lastWeek**。**不预热**月度及以上（thisMonth/lastMonth/thisYear/lastYear）—— 长尾，访问时现算 + closed 长 TTL 兜底。
- 每个 range 预热 4 个 scope：global（无 `product_launch_scope`）、new、old、unmatched。
- 固定参数（对齐前端默认顶部卡片，最常见视图）：`include_profit_summary=True`、`include_details=False`、`product_launch_window_days=7`、**不带** `product_id` / `site_code` / 分页。带店铺/产品筛选的长尾视图不预热。

### 5.3 预热频率（分级，对齐各自 TTL）

| range | open/closed | 预热内容 | 目标间隔 | 约束 |
|---|---|---|---|---|
| today | open | global | ~45s | < 60s 短窗口 |
| today | open | new / old / unmatched | ~150s | allSettled 兜底 |
| thisWeek | open | global | ~45s | < 60s 短窗口 |
| thisWeek | open | new / old / unmatched | ~150s | allSettled 兜底 |
| yesterday | closed | global / new / old / unmatched | ~1200s | < 1800s TTL |
| lastWeek | closed | global / new / old / unmatched | ~1200s | < 1800s TTL |

### 5.4 调度实现
- 单个 APScheduler 任务，`IntervalTrigger(seconds=15)` 高频 tick；runner 内部按各 (range, scope) 的「上次刷新时间 + 目标间隔」决定本 tick 刷哪些（参照 `_open_day_freshness` 的 in-process TTL 思路）。
- `max_instances=1` + `coalesce=True`，**串行**执行，避免预热自己并发压垮 DB。
- 按 `TASK_DEFINITIONS` 体系登记（code / name / description / source_ref / runner / log_table），遵守「新增定时任务必登记」规则。
- 预热异常**绝不**抛出影响调度器，按现有 `_controlled_job` 容错。

### 5.5 局限（诚实交代）
A 不重构后端聚合。若后端单 scope 高并发仍 15–30s，预热一轮串行（尤其 thisWeek 整周更慢）可能跟不上 45s 节奏，`new/old/unmatched` 会偶发 MISS。**A 的定位**：让 global 稳定秒开 + closed 历史稳定 HIT + allSettled 让偶发慢不再整块挂，而非 100% 消除所有慢。要 100%，需后续方案 B（后端 4-scope 合并）。

## 6. 组件 3：前端 allSettled 解耦（`order_analytics.html`）

- `loadRealtimeTopCards`：4 个 scope 改为**各自独立 `AbortController`**（不再共享），`Promise.all` → `Promise.allSettled`。
- 每个 scope 独立结算：成功渲染该 scope；失败/超时**只该卡片**显示「加载失败」（保留点按重试入口），其余正常。
- 单 scope 30s 超时 abort 只影响自身。
- `reconcileRealtimeGlobalScopeProfit` 改容错：当 new/old/unmatched 任一缺失时，**不前端重算**，global 用后端返回原值（保持 data_quality 链路）。
- `requestSeq` 防并发逻辑保留：仍按 `top` 维度判定 `isRealtimeRequestCurrent`，避免旧批次覆盖新批次。

## 7. 数据流

```
用户请求 /realtime-overview
  → route 解析 kwargs + 算 is_open_day
  → get_realtime_roas_overview_cached(kwargs, is_open_day)
       ├─ cache.get(key, is_open_day): closed→时间TTL / open→60s窗口+marker
       ├─ HIT → 返回（X-Realtime-Cache: HIT）
       └─ MISS → get_realtime_roas_overview(...) → cache.put → 返回（MISS）

后台预热 tick(15s)
  → 对 today/yesterday/thisWeek/lastWeek × 4 scope 中到期者
  → 串行调 get_realtime_roas_overview_cached(同一 kwargs 构造)
  → 写入与 route 完全一致的 cache_key
  ⇒ 用户请求几乎总命中预热结果
```

## 8. 测试与回归

### 新增
- `realtime_cache`：open/closed 分层；**closed 不被全局 marker 误伤**（构造「今天来新订单」场景，断言昨天 key 仍 HIT）；open 60s 窗口。
- `_resolve_meta_calendar_range`：today/yesterday/thisWeek/lastWeek 边界对拍前端定义（含周一起算、thisWeek.end=周日）。
- 预热 runner：到期判定、串行、kwargs 与 route 一致（同一 cache_key）；任务登记进 `TASK_DEFINITIONS`。
- route：`X-Realtime-Cache` HIT/MISS 行为。

### 回归（`appcore/order_analytics/CLAUDE.md` 硬规则）
```
pytest tests/test_order_analytics_realtime_site_filter.py \
       tests/test_order_analytics_true_roas.py \
       tests/test_order_analytics_data_quality.py \
       tests/test_order_profit_aggregation.py \
       tests/test_order_analytics_ads.py \
       tests/test_product_profit_report.py \
       tests/characterization/test_order_analytics_baseline.py -q
```
优先用 `python3 scripts/pytest_related.py --base origin/master --run` 选取改动相关测试。

### 前端（无 pytest）
用已编写的 Playwright 脚本复测 5 场景（初次进入 / 今天 / 昨天 / 整页刷新 / 本周），断言：偶发慢时其余卡片照常出、不整块 `-`；命中预热时秒开。

## 9. 风险与权衡

- **预热资源消耗**：每 ~45s 重算 open range 的 global、每 ~150s 算其余 scope、每 ~20min 算 closed。后台串行、慢不影响前台，但占 Web 进程 + DB。通过分级 + 串行 + 仅默认视图限制总量。
- **预热跟不上**（见 §5.5）：thisWeek 整周现算慢，极端时仍偶发 MISS，由 allSettled 兜底，不回归到「整块加载不出来」。
- **closed 30 分钟陈旧**：收盘日被夜间 backfill 微调时最多旧 30 分钟，可接受。
- **range 解析漂移**：前端若改 `resolveRange`，后端 `_resolve_meta_calendar_range` 须同步；用对拍单测兜住。

## 10. 非目标（YAGNI）

- 不做后端 4-scope 合并计算（方案 B）。
- 不预热月度 / 年度区间，不预热带店铺/产品筛选的长尾视图。
- 不改 30 秒前端超时值、不加自动整页重试。

---

## 后续调整（2026-06-14 v2，已实现并发布）

按用户反馈在初版基础上调整：

1. **缓存改 per-range 纯时间 TTL**（取代 is_open_day + 全局 marker）：单日今天 `TTL_SINGLE_DAY_OPEN`=60s、多日含今天（本周/本月）`TTL_MULTI_DAY_OPEN`=660s、历史 `TTL_CLOSED`=1800s。每条目带自己的 TTL，预热主动刷新，彻底消除全局 marker 误伤。
2. **预热频率分档**：today/yesterday **15s**（配 60s TTL，保鲜 ≤1 分钟）；本周/上周/本月/上月 **600s**（配 660s TTL，数据旧 ≤10 分钟）。**年度（今年/去年）不预热**，点击时现算。
3. **预热覆盖新品投放分析**：npl 复用 `/realtime-overview`（带 `include_details` + 分页 + scope=new/old/unmatched），cache_key 与实时大盘不同，单独预热（6 范围 × 3 scope）。总预热目标 42 个（实时大盘 24 + npl 18）。
4. **前端加载进度指示**：加粗「加载中……」+ 实时已用秒数（每 100ms 更新），文字非浮窗、明显；实时大盘 + 新品投放分析各一个（`#realtimeLoadingIndicator` / `#nplLoadingIndicator` + `startOaLoadingTimer/stopOaLoadingTimer`）。

---

## 后续调整（2026-06-14 v3，二次修复"点昨天 30s 超时、数据横杠出不来"）

v2 发布后用户仍报「刷新秒出，但刷新后切昨天就卡十几秒、最终"加载失败：请求超时"、数据全横杠」。
**上服务器拿到 APScheduler 日志铁证**后，定位到 v2 自己引入的新根因——不是缓存没命中，是预热把后端压垮了：

### 根因：单进程被预热重算饿死
- `deploy/gunicorn.conf.py` 是 **`workers=1` 单进程**（有意：in-process Socket.IO / in-memory task state / 预热 APScheduler 都依赖单进程，**不能靠加 worker 摊负载**）。
- v2 为「修缓存被动过期」加的 `force_refresh=True` 让预热**每轮重算所有到期目标**。日志铁证：
  `run_warmup_fast skipped: maximum number of running instances reached (1)`
  —— fast（15s 间隔）单轮 16s 跑不完、持续 skip；预热在单进程里几乎一刻不停地重算，霸占 GIL + DB 连接。
- 用户点昨天（cold 现算仅 ~2s、多数时候命中）的请求线程**抢不到被预热占满的资源** → 拖到前端 30s 超时。GCP 直连偶尔撞空隙就秒回，故黑盒压测复现不了。

### 修复：touch 续期（把"防过期"与"重算更新数据"分开）
- `realtime_cache.touch(key, ttl)`：仅 `UPDATE expires_at`（一次轻量写），不重算 payload。
- 预热 `_warm_one`：命中且数据未超 TTL（`_last_refresh` 记上次重算时间）→ 只 touch 续期；数据超 TTL 没重算过 / 缓存被清 → 才重算一次。
- 预热资源占用从「持续重算 ≈100%」降到「轻量 touch + 每 TTL 一次重算」；缓存靠 touch 不被动过期，用户**永远命中、秒回**。
- 配套：前端切换不再主动 `abort` 旧请求（避免 WebKit 连接池被 abort 污染、释放慢），靠 `requestSeq` 忽略旧批次结果。

### ⚠️ 不要回退
**严禁**把预热改回「每轮 `force_refresh` 重算」——那是 30s 超时的直接原因。预热在 `workers=1` 下必须自身轻量（touch 为主、重算为辅）。

### 验证（关键教训：不能只黑盒压测）
- **必须上服务器看 APScheduler 日志 + 进程模型**：稳定后 `run_warmup_fast` skip 从「持续」→ **0**。
- WebKit 引擎（= iOS Chrome/Safari 同引擎）+ iPhone 视口才接近真机；chromium 复现不了 WebKit 连接行为。
- 复测：刷新 / 点昨天 / 连续切 / 快速狂点 8 次全部 1.1–1.6s 秒回、abort 0。

---

## 后续调整（2026-06-14 v4，真凶定位：封面大图洪流 + 子Tab全渲染）

v1–v3 一直在查缓存 / 预热（后端实测 **1ms**、根本没病），都没解决用户"点击切换 20s 出不来
数据、刷新秒出"。按用户提示上 nginx 隧道入口 `14.103.60.217` 看日志，才定位到真正的两层根因。

### 真凶①：封面原图（最大 7MB）洪流堵死窄 SSH 隧道
- 链路：用户 iPhone → `14.103.60.217` nginx → `ssh -R` 反向隧道(:18080) → 内网机 gunicorn。
- 实时大盘产品列表渲染上百张 `/medias/cover/<pid>`（原图最大 7MB），全挤在一条 SSH 隧道。
- nginx error log 铁证：满屏 `/medias/cover ... upstream prematurely closed`，连
  `realtime-overview` 数据请求也被一起掐断 → 卡 20s；刷新重置连接故秒出。
- 修复：`/medias/cover/<pid>?thumb=1` 返回缩略图（最长边 240px，7MB→10KB，失败回退原图；
  `web/routes/medias/covers.py` + `web/services/media_covers.py::product_cover_thumb_flask_response`）。

### 真凶②：子 Tab 一次全渲染 + iOS lazy 不拦 display:none
- `loadRealtimeSubTabs()` 不管当前在哪个子 Tab，一次性渲染全部 5 个子 Tab，产品销量
  几百张 cover 即使 `display:none` 也被创建。
- **iOS WebKit 的 `loading="lazy"` 对 `display:none` 不拦截**（和桌面 Chrome 不同），
  隐藏子 Tab 的图照样下载。Playwright 实测 trend 默认就拉 187 张。
- 修复：`layout.html` 全局 IntersectionObserver 懒加载——`img[data-cover-src]` 进视口才
  设 src + MutationObserver 自动接管动态渲染的图。全局 6 处列表封面改 `data-cover-src` +
  `thumb=1`（order_analytics×3 + tasks_list + pushes_history + pushes.js）；medias 编辑
  弹窗单张 480px 大图保持原图。实测 trend 默认 cover **187→0**、切到产品销量只加载视口内 4 张。

### ⚠️ 诊断教训（最重要，避免重蹈覆辙）
- **实时大盘性能问题别只盯后端/缓存**：后端 1ms 时瓶颈在传输层（隧道带宽、连接数、大图）。
- **必须上 nginx 隧道入口 `14.103.60.217` 看 access/error log**（`ssh avsl` 再嵌套
  `sudo ssh -i /home/cjh/.ssh/CC.pem root@14.103.60.217`），能一刀切开"用户→隧道"和
  "隧道→后端"；内网机 `ssh avsl` 看不到隧道层。
- **图片懒加载在 iOS 上必须用 IntersectionObserver**，`loading="lazy"` 对 display:none 不可靠。
- 新增任何产品主图渲染点，统一走 `img[data-cover-src]` + `?thumb=1`（全局工具自动接管）。
