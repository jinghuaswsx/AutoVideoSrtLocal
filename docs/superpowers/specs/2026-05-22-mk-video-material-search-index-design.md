# 明空视频素材库搜索索引设计

日期：2026-05-22

## 背景

`/xuanpin/mk#videos` 的「视频素材库」已经从实时明空 API 批量搜索切到本地明空素材快照，事实来源是：

- `docs/superpowers/specs/2026-05-18-mingkong-video-material-local-index-design.md`
- `appcore/mingkong_materials.py::list_material_library()`
- `GET /xuanpin/api/mk-material-library`
- `web/templates/mk_selection.html`

当前搜索框文案只提示「搜索产品名」，后端虽已用 `keyword` 模糊匹配一部分字段，但缺少明确规则：运营需要同一个搜索框可以快速搜产品名、`product_code` 和视频素材文件名，同时分页总数、卡片列表和从产品库行点击「素材库」进入的行为不能互相覆盖。

2026-05-22 发布后补充：截图确认用户在「昨天消耗前100」tab 输入 `baseball-cap-organizer` 后仍看到无关卡片。根因是第一版只把 `keyword` 接到「视频素材库」的 `/xuanpin/api/mk-material-library`，没有把同一个搜索框接到「昨天消耗前100」的 `/xuanpin/api/mk-yesterday-top100`。本 spec 继续作为锚点，搜索框在两个明空视频卡片 tab 中都必须生效。

2026-05-24 更新：该 tab 的运营名称从「昨天消耗前100」扩为「昨天消耗前300」，新接口别名为 `/xuanpin/api/mk-yesterday-top300`；旧 `/xuanpin/api/mk-yesterday-top100` 保持兼容。

## 目标

1. 明空视频卡片搜索框支持以下输入，并在「视频素材库」与「昨天消耗前300」两个 tab 中生效：
   - 产品名：`product_name`、`mk_product_name`。
   - 产品 code：`product_code`，同时支持带/不带 `-rjc` 或 `_rjc` 的输入变体。
   - 视频素材文件名：`video_name`、`video_path` 的文件名或路径片段。
2. 搜索必须继续走本地 `mingkong_material_daily_snapshots`，不触发批量实时明空搜索。
3. 搜索分页必须稳定：`total` 和当前页 `items` 使用同一套过滤条件，避免页码显示与实际结果错位。
4. 搜索速度要保持可接受：优先利用 `snapshot_at` / `snapshot_date` 范围约束和新增索引；不为本期引入独立搜索表或新的定时任务。
5. 从「产品库」行点击「素材库」仍按该行 `product_code` 精确进入视频素材库；用户手动输入并点击搜索时，应清除该行跳转留下的 `activeMkProductCode` 状态。
6. 搜索框在桌面宽度加长到上一版约 2 倍，给长 `product_code` 和素材文件名留出可读空间。

## 非目标

- 不改变明空素材每日快照同步逻辑。
- 不改变「昨天消耗前300」的排序和数据来源。
- 不新增 fulltext/ngram 搜索表。
- 不访问 Windows 本机 MySQL 做验证。
- 不改变素材入库、AI 评估、小语种任务创建流程。

## 搜索语义

新增一个后端 helper 统一构造明空素材库搜索条件，供单日快照和范围查询共同使用。

输入归一化：

```text
kw = trim(user keyword)
kw_lower = lower(kw)
kw_without_rjc = remove trailing -rjc/_rjc from kw_lower
kw_with_rjc = kw_without_rjc + "-rjc" when kw_without_rjc is not empty
```

匹配字段：

- `s.product_code LIKE %kw%`
- `s.product_code LIKE %kw_without_rjc%`
- `s.product_code LIKE %kw_with_rjc%`
- `s.product_name LIKE %kw%`
- `s.mk_product_name LIKE %kw%`
- `s.video_name LIKE %kw%`
- `s.video_path LIKE %kw%`

`video_path` 匹配保留路径片段能力，方便用户直接粘贴素材对象路径或文件名的一部分。当前 MySQL 无法对前置 `%` 的所有模糊匹配完全走 B-tree，但搜索范围始终被 `snapshot_at`、`snapshot_date` 或日期区间先收窄；本期通过索引减少快照/日期定位成本，并保持后续可升级空间。

## API 行为

`GET /xuanpin/api/mk-material-library` 保持参数兼容：

- `keyword`：统一搜索产品名、product code、视频素材文件名。
- `range`：范围查询继续支持本周、上周、本月、上月。
- `snapshot` / `snapshot_at`：单日快照查询保持现有语义。
- `page` / `page_size`：保持现有分页。

响应结构不新增字段。`total`、`page`、`page_size` 和 `items` 必须来自同一组搜索条件。

`GET /xuanpin/api/mk-yesterday-top300` 新增兼容参数，`/xuanpin/api/mk-yesterday-top100` 为兼容别名：

- `keyword`：同样统一搜索产品名、product code、视频素材文件名。

响应结构不新增字段。`total`、`page`、`page_size` 和 `items` 必须来自同一组搜索条件。

## 前端行为

`web/templates/mk_selection.html` 调整：

- 搜索框 placeholder 改为「搜索产品名 / product code / 视频文件名」。
- 手动点击「搜索」或按 Enter 时，如果当前 tab 是 `videos`，先清空 `activeMkProductCode`，再用输入框内容调用 `/xuanpin/api/mk-material-library?keyword=...`。
- 当前 tab 是 `yesterday-top300` 时，用输入框内容调用 `/xuanpin/api/mk-yesterday-top300?keyword=...`。
- 从产品库行点击「素材库」时继续设置 `activeMkProductCode`，并把输入框填成该 code；该入口的状态文案继续显示按产品 code 搜索。
- 「重置」清空输入框和 `activeMkProductCode` 后重新加载视频素材库。
- 桌面搜索框宽度从 240px 调整到 480px；中窄桌面同步放宽；竖屏布局仍以不挤破工具栏为准。

## 数据库索引

新增幂等 migration，只补索引，不改字段：

- `mingkong_material_daily_snapshots`
  - `(snapshot_at, product_code)`
  - `(snapshot_at, video_name)`
  - `(snapshot_at, product_name)`
  - `(snapshot_at, mk_product_name)`
  - `(snapshot_at, video_path)`
  - `(snapshot_date, product_code)`
  - `(snapshot_date, video_name)`

这些索引用于在单日快照和日期范围入口先按快照/日期收窄，再进行字段匹配和排序。已有索引存在时 migration 必须跳过。

## 验收标准

1. 输入产品中文名或明空产品名能返回匹配视频卡片。
2. 输入 `product_code`、`product-code-rjc` 或 `product_code_rjc` 变体能返回对应产品视频卡片。
3. 输入视频素材文件名或 `video_path` basename 能返回对应视频卡片。
4. 在「昨天消耗前300」输入上述任一关键词，也只返回匹配卡片。
5. 手动搜索不会被上一次产品库「素材库」按钮设置的 `activeMkProductCode` 覆盖。
6. `COUNT(*)` 和列表查询共享搜索条件，分页总数正确。
7. 搜索框桌面宽度加长到 480px。
8. migration 幂等，已有索引环境可重复执行。
9. 不触发 `/xuanpin/api/mk-video-materials` 作为视频素材库主列表。

## 验证

```bash
pytest tests/test_mingkong_materials.py tests/test_mk_selection_routes.py tests/test_xuanpin_routes.py -q
python -m compileall appcore web tests -q
git diff --check
```

人工验收：

1. 登录后打开 `/xuanpin/mk#videos`。
2. 分别用产品名、product code、视频素材文件名搜索。
3. 从「产品库」点某行「素材库」，确认会切到视频素材库并按该产品 code 加载。
4. 在视频素材库中改输入词并点「搜索」，确认不再沿用刚才的产品 code。
