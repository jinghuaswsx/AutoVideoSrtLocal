# Tabcut 选品采集 V1 设计

最后更新：2026-05-12

## 背景

AutoVideoSrtLocal 已有「选品中心」入口，当前主要承接明空 / 店小秘数据。新增 TABCUT 选品模块，用用户已购买的 Tabcut 旗舰版账号，在服务器 `cjh` 桌面 Chrome 会话中自动采集美国站爆款视频和商品，供内部选品、素材入库和后续任务中心使用。

本设计遵守项目规则：

- 不连接 Windows 本机 MySQL `127.0.0.1:3306`。
- 不在主工作目录改代码，开发走 git worktree。
- Tabcut 数据只使用用户付费账号有权限访问的页面和接口，不绕过会员、登录、权限和风控。
- 每个 Tabcut 页面点击或接口请求之间至少间隔 3 秒。

## 范围

V1 只做美国站 `US`，每天北京时间 08:00 采集前一天的日榜快照。

采集目标：

- 视频 Top500：来自 Tabcut「视频榜」，按播放量榜和销量榜采集并合并去重。
- 商品 Top500：来自 Tabcut「商品榜」和「选品引擎」，按商品热销、销售额、近 7 天销量、增长率等采集并合并评分。
- 商品反查视频：对高价值商品按需调用「发现视频」接口，补 `tkVideoUrl`、视频带货销量、视频分摊 GMV。

不在 V1 做：

- 多国家采集。
- 绕过或模拟登录。
- 长期保存 Tabcut 返回的带签名 `auth_key` 视频地址。
- 自动下载全部视频。只有管理员在内部页面选择素材入库时，再即时获取可用视频源。

## 已探索数据源

### 视频榜

接口：`GET /api/ranking/videos`

关键参数：

- `region=US`
- `regionId=1`
- `rankDay=1`，日榜；`7` 周榜；`30` 月榜。
- `itemCategoryId=0`，全部类目。
- `pageNo`
- `pageSize=100`
- `sort=10` 播放量，`20` 获赞，`30` 分享，`40` 评论，`60` 销量。

关键字段：

- `videoId`
- `authorName`
- `authorAvatarUrl`
- `videoCoverUrl`
- `videoUrl`，短期签名地址，仅运行时使用，不长期保存。
- `videoDesc`
- `videoDuration`
- `createTime`
- `playCount`
- `likeCount`
- `shareCount`
- `commentCount`
- `itemSoldCount`
- `hashtags`
- `itemList[]`：`itemId`、`itemName`、`itemCoverUrl`、`skuPrice`、`soldCount`、`currencySymbol`

### 商品榜

接口：`GET /api/trpc/ranking.goods.rankingData?input=<json>`

关键参数：

- `region=US`
- `bizDate=YYYYMMDD`
- `rankType=1`
- `orderType=1`
- `categoryId=0`
- `pageNo`
- `pageSize=100`

关键字段：

- `itemId`
- `itemName`
- `itemPicUrl`
- `categoryId`
- `categoryName`
- `soldCountGrowthRate`
- `rank`
- `priceList`
- `gmvInfo.periodCurrent`
- `soldCountInfo.periodCurrent`
- `soldCountInfo.total`
- `relatedCreatorInfo.period90d`
- `relatedVideoInfo.period90d`
- `relatedLiveInfo.period90d`
- `commissionRate`
- `sellerId`
- `sellerName`
- `sellerType`

### 选品引擎

接口：`GET /api/trpc/ranking.goods.search?input=<json>`

关键参数：

- `region=US`
- `pageNo`
- `pageSize=100`
- `sortField=item_sold_count_7d`，近 7 天销量。
- 可扩展排序：`sold_count_growth`、`sold_count_total`、`gmv_total`、`relatedVideoCount`、`relatedCreatorCount`。
- `sellerTypes=["full_managed","over_sea","local"]`

关键字段：

- `itemId`
- `itemName`
- `region`
- `categoryId`
- `categoryName`
- `categoryLv1Id/categoryLv1Name`
- `categoryLv2Id/categoryLv2Name`
- `categoryLv3Id/categoryLv3Name`
- `priceOrigin`
- `soldCount1d`
- `soldGrowthRate1d`
- `soldCount7d`
- `soldGrowthRate7d`
- `soldCount30d`
- `soldGrowthRate30d`
- `soldCountTotal`
- `gmvInfo.period1d/period7d/period30d/total`
- `priceList`
- `commissionRate`
- `sellerName`
- `sellerId`
- `sellerType`
- `relatedVideoCount`
- `relatedCreatorCount`
- `discoverTime`

### 商品反查视频

接口：`POST /api/analysis/video-search/videoListV2`

用途：对已选商品补充关联视频，拿到 TikTok 原始链接和视频分摊成交数据。

关键请求：

```json
{
  "region": "US",
  "pageNo": 1,
  "pageSize": 100,
  "videoDesc": "<商品标题或关键词>",
  "itemVideoFlag": "1",
  "sortField": "video_sold_count"
}
```

关键字段：

- `videoId`
- `videoCoverUrl`
- `videoPlayUrl`，短期签名地址，不长期保存。
- `tkVideoUrl`
- `videoDesc`
- `videoDuration`
- `createTime`
- `playCountTotal`
- `likeCountTotal`
- `shareCountTotal`
- `commentCountTotal`
- `interactionRate`
- `authorNickname`
- `authorUniqueId`
- `authorFollowerCountTotal`
- `itemId`
- `itemName`
- `videoSplitSoldCount`
- `videoSplitGmv`

## 架构

### 采集器

新增 `tools/tabcut_crawler/`：

- `client.py`：连接服务器桌面 Chrome CDP，封装 Tabcut API 请求、登录态检查、3 秒节流和失败退避。
- `models.py`：定义标准化视频、商品和候选评分结构。
- `ranking.py`：计算综合爆款分。
- `storage.py`：把采集结果 upsert 到数据库。
- `runner.py` / `main.py`：命令行入口，支持 dry run 和单次采集。

采集器默认配置：

- Chrome CDP：`http://127.0.0.1:9227`
- Chrome profile：服务器 `cjh` 桌面用户的 `chrome-tabcut` 专用目录。
- 请求间隔：默认 3.2 秒，不能低于 3 秒。
- 目标地区：固定 `US`。
- 目标日期：默认前一天，支持 `--biz-date YYYYMMDD` 手动补跑。

### 数据库

新增迁移 `db/migrations/2026_05_12_tabcut_selection.sql`：

- `tabcut_crawl_runs`：记录每次采集运行、目标日期、状态、请求数、错误。
- `tabcut_videos`：视频维表，按 `video_id` upsert。
- `tabcut_video_snapshots`：每日视频指标快照，按 `(biz_date, region, video_id, source_sort)` 唯一。
- `tabcut_goods`：商品维表，按 `item_id` upsert。
- `tabcut_goods_snapshots`：每日商品指标快照，按 `(biz_date, region, item_id, source)` 唯一。
- `tabcut_video_candidates`：每日综合候选结果，按 `(biz_date, region, video_id)` 唯一。

短期签名视频地址不入库；原始响应只保存脱敏后的 JSON 摘要。

### Web 模块

在「选品中心」新增 `TABCUT 选品` Tab：

- 页面路由：`/medias/tabcut-selection`
- API：
  - `GET /medias/api/tabcut-selection/videos`
  - `GET /medias/api/tabcut-selection/goods`
  - `POST /medias/api/tabcut-selection/refresh`
- 权限：`@login_required` + admin-only，沿用 `mk_selection` 权限。
- POST 请求必须带 `X-CSRFToken`。

筛选：

- 类目：一级、二级、三级类目。
- 数据来源：视频列表可按 `rankDay=1/7/30` 选择日榜、周榜、月榜；后端只接受白名单值并映射到 `tabcut_video_snapshots.source_sort`。
- 销量：视频带货销量、商品 1/7/30 天销量、总销量。
- 销售额：商品 1/7/30 天 GMV、总 GMV、视频分摊 GMV。
- 排序：综合分、播放量、视频销量、商品 7 日销量、商品 7 日 GMV、增长率、佣金、关联视频数。

### 调度与发布

新增定时任务登记：

- `task_code=tabcut_daily_selection`
- runner：`tools/tabcut_crawler/main.py`
- schedule：每天北京时间 08:00，采集前一天美国站数据。
- source：生产服务器 `cjh` 用户 systemd timer。
- 日志：`scheduled_task_runs`

部署到生产后创建或更新：

- `deploy/tabcut-daily-selection.service`
- `deploy/tabcut-daily-selection.timer`

## 评分

V1 综合分只用可解释的线性规则：

- 视频热度：播放量、点赞、分享、评论。
- 带货强度：视频 `itemSoldCount`、反查视频 `videoSplitSoldCount`。
- 商品强度：商品 7 日销量、7 日 GMV、总销量。
- 增长：商品 7 日增长率、商品榜增长率。
- 供给密度：关联视频数、关联达人数，用于过滤和解释，不让其单独压过成交指标。

评分结果保存在候选表中，同时保留每个分项，页面可解释为什么某条视频排在前面。

## 验收

- 不连接 Windows 本机 MySQL。
- 单元测试覆盖：Tabcut 参数构造、请求节流、响应标准化、评分、查询筛选、定时任务登记。
- `pytest` 只跑不依赖本机 MySQL 的目标测试。
- Web 未登录访问 302，登录但非 admin 403，admin 200。
- `POST /medias/api/tabcut-selection/refresh` 需要 CSRF。
- 生产部署后服务 active，HTTP 200/302，定时任务能在 `scheduled_tasks` 后台看到。

## 2026-05-12 补充：发布日期筛选与月度回采

- `TABCUT 选品`视频卡片按 Tabcut 原站视频榜卡片复刻：作者头像/昵称/标签在顶部，视频封面居中，封面左下角显示国家和时长，封面下方显示发布时间、播放/点赞/分享/评论四项指标，底部显示关联商品缩略图、标题、价格和销量。
- 页面新增发布日期起止日期选择，过滤字段为 `tabcut_videos.create_time`，只作用于视频列表；未选择时不限制发布时间。
- 采集默认窗口从近 7 天改为近 30 天。
- 视频榜必须同时采集日榜、周榜、月榜。每个榜单至少抓 1000 条原始视频数据；当前以 `pageSize=100`、每个榜单每个核心排序至少 10 页实现。
- 核心排序先覆盖播放量榜和销量榜：`rankDay=1/7/30` × `sort=10/60`。后续如需要点赞、分享、评论榜，可在同一计划里追加 source，不改变入库结构。
