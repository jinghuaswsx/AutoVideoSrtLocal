# 素材管理视频素材决策台设计

Date: 2026-06-06

## Anchors

- `AGENTS.md`：素材管理路由验证、登录/管理员门禁、POST CSRF 要求。
- `2026-05-28-medias-product-ad-status-cache-design.md`：产品与语种广告汇总缓存、广告明细匹配口径、同语种多素材去重原则。
- `2026-04-26-mk-import-design.md`：`POST /mk-import/video` 负责把明空视频加入素材库。
- `2026-05-16-task-center-e2e-flow-design.md`：已入库素材走 `POST /tasks/api/parent` 创建任务中心父任务。
- `2026-05-20-task-center-per-language-assignment-design.md`：创建小语种任务时支持 `language_assignments` 按语种指派负责人。

## Context

`/medias/product/addvideo/<pid>` 当前以明空素材卡片为主，只能粗略看到是否已入库和语种投放概览。运营需要一个产品级工作台，在同一页完成素材判断、广告数据核对、加入素材库和创建小语种任务。

现有页面还存在一个展示问题：语种广告缓存是 `product_id + lang` 聚合，但补素材页按 `media_items` 每条素材生成“国家投放数据”。同一语种有多条已入库素材时，同一份 DE/FR/IT 等广告数据会重复显示。

## Goals

1. 新增路由 `/medias/product/video_workbench/<pid>`，作为“视频素材决策台”第一版。
2. 顶部展示产品总览：产品信息、明空素材数量、已入库数量、未入库数量、产品整体广告状态和语种覆盖。
3. 卡片展示每条明空视频的素材情况：视频预览、明空来源、广告热度、是否已入库、绑定本地素材、去重后的语种投放概览。
4. 未入库素材可直接调用现有 `POST /mk-import/video` 加入素材库。
5. 已入库素材可在本页选择语种和负责人，调用现有 `POST /tasks/api/parent` 创建小语种任务。
6. 每张视频卡提供“广告数据”入口，弹窗支持日期范围，展示该视频相关广告汇总和明细。

## Non-Goals

1. 不替代任务中心详情页；任务创建后仍跳转任务中心跟进。
2. 不新增数据库表。
3. 不改变广告匹配算法总口径；首版沿用素材广告详情口径。
4. 不在卡片列表实时扫广告大表；列表只读缓存和明空快照，广告明细仅在弹窗懒加载。
5. 不改 `/medias/product/addvideo/<pid>` 旧页面本身；产品列表保留“补素材”按钮，并在其下方新增“素材工作台”按钮跳转到新工作台。

## Data

### Product And Material

- `media_products`：产品基础信息。
- `mingkong_material_daily_snapshots`：明空视频快照、90 天消耗、昨日消耗、广告数量、封面和视频路径。
- `media_items`：本地素材库视频。
- `media_item_mk_bindings`：明空视频路径与本地素材绑定关系。

工作台判断“已入库”的来源：

1. 首选 `media_item_mk_bindings.mk_video_path = mingkong_material_daily_snapshots.video_path`，标记为 `media_item_mk_bindings`。
2. 对早期没有绑定表记录的历史素材，允许产品内高置信度兜底：
   - 明空 `video_name` 精确等于本地素材 `filename` 或 `display_name`。
   - 明空 `video_path` basename 精确等于本地素材 `filename`、`display_name` 或 `object_key` basename。
   - 兜底只在当前 `product_id` 的 `media_items` 内匹配，不跨产品，不做关键词、日期、人员等模糊推断。
   - 兜底命中标记为 `media_items_legacy_product_scope`，前端展示为“历史匹配”，与绑定表命中区分。

该兜底只改变工作台展示与操作入口，不自动写入 `media_item_mk_bindings`。批量历史绑定回填需另开审核流程，先展示候选再确认，避免误绑。

### Summary Ads

- `media_product_ad_summary_cache`：产品整体广告状态、整体 ROAS、广告消耗。
- `media_product_lang_ad_summary_cache`：产品语种广告消耗、购买金额、ROAS、推送视频数。

### Ad Detail

视频广告数据弹窗读取 `meta_ad_daily_ad_metrics`，首版按以下条件匹配：

1. `product_id` 必须等于当前产品。
2. `spend_usd > 0`。
3. 日期范围使用 `DATE(COALESCE(meta_business_date, report_date))`。
4. 优先按本地素材 `filename` / `display_name` 命中 `ad_name` 或 `normalized_ad_code`。
5. 明空未入库素材按明空视频名命中 `ad_name` 或 `normalized_ad_code`。

弹窗明细返回 `match_reason`，用于区分 `filename`、`display_name`、`mk_video_name` 等匹配来源。若未来加入国家兜底匹配，必须显式标注，避免把产品级广告误认为视频级广告。

2026-06-08 修订：如果素材名 / 明空视频名精确匹配返回 0 条，但 `media_product_lang_ad_summary_cache` 已经存在该产品的语种广告消耗，则弹窗允许按缓存一致口径兜底读取产品国家广告明细：

1. 兜底仍必须限制 `product_id`、`spend_usd > 0` 和日期范围。
2. 兜底国家来自有广告消耗的缓存语种，使用与产品语种 ROAS 缓存一致的国家到语种映射；当前弹窗读取日终明细表时按 `market_country` 过滤。
3. 返回行的 `match_reason` 必须标记为 `product_lang_country_fallback`，前端明细可看出这是产品语种国家兜底，不是素材名命中。
4. 仅在素材名匹配为空时启用兜底，不能覆盖已有的素材级精确匹配结果。

2026-06-11 修订：工作台右侧“翻译版本”卡片与“投放消耗 / ROAS”概览不得只依赖完整文件名包含匹配。若本地素材文件名保留中文产品名，但 Meta 广告名把产品名段替换为英文 handle / 英文标题，且日期、素材类型、语种、上传/指派尾段仍一致，概览必须把该广告行归属到对应翻译素材。该素材尾段兜底优先于产品语种国家兜底；仍无法命中具体素材时，才允许按已有语种缓存/国家映射提示为产品语种口径，避免把真实投放错误显示为“未投放”。

实时广告明细兜底读取 `meta_ad_realtime_daily_ad_metrics` 时，国家字段使用 `country_code`；日终表继续使用 `market_country`。同一逻辑对外统一序列化为 `market_country`，前端不区分来源表。

### Task

- 任务创建使用 `POST /tasks/api/parent`。
- 请求体包含 `media_product_id`、`media_item_id`、`raw_processor_id`、`countries`、`language_assignments`。
- `countries` 与 `language_assignments` 使用大写语种/国家码。

## UX Flow

1. 用户打开 `/medias/product/video_workbench/<pid>`。
2. 页面请求 workbench overview API，加载产品总览和视频卡片。
3. 用户按 90 天消耗、昨日消耗或广告数排序。
4. 用户点击视频卡“广告数据”，弹窗默认近 30 天，可切换日期范围。
5. 未入库视频点击“加入素材库”，成功后卡片更新为已入库。
6. 已入库视频点击“创建小语种任务”，弹窗选择国家、原视频处理人和各语种负责人。
7. 创建成功后显示父任务 ID，并提供任务中心跳转。

产品列表入口：

1. “补素材”按钮继续打开 `/medias/product/addvideo/<pid>`。
2. “素材工作台”按钮位于“补素材”下方，打开 `/medias/product/video_workbench/<pid>`。

## API

### Page

- `GET /medias/product/video_workbench/<pid>`
  - `@login_required`
  - `@permission_required("medias")`
  - 渲染 `medias_product_video_workbench.html`

### Overview

- `GET /medias/api/product/<pid>/video-workbench?sort_by=spend_90|spend_yesterday|ads_count`
  - `@login_required`
  - `@admin_required`
  - 返回产品、汇总、语种覆盖和卡片列表。
  - 卡片中的语种投放概览必须按 `lang` 去重。

### Video Ad Detail

- `GET /medias/api/product/<pid>/video-workbench/ad-detail`
  - Query:
    - `video_path`
    - `media_item_id`
    - `date_from`
    - `date_to`
  - 返回 `summary` 与 `rows`。
  - 日期最多允许 180 天，避免一次扫全表。

## First Usable Version

第一版必须可用但保持收敛：

1. 新页面能打开并加载卡片。
2. 国家投放数据不重复。
3. 绑定表命中和历史高置信度命中的素材都能在工作台显示为已入库，并展示匹配来源。
4. 未入库素材可以加入素材库。
5. 已入库素材可以创建小语种任务。
6. 广告弹窗能按日期范围加载汇总和明细。
7. 新路由未登录返回 302，登录后页面 200。

## V2 Video Card Data Panel

2026-06-08 用户确认：素材工作台的视频卡片展现要改为“补素材”页同款左右分栏效果。左侧是一张完整的视频素材卡，视觉与 `/medias/product/addvideo/<pid>` 的选品中心视频素材库卡片保持一致；右侧展示该原始素材对应的全部翻译素材和运营决策数据。

卡片右侧必须覆盖：

1. 翻译版本：展示该原始素材已翻译了哪些国家/语种版本，按 `source_raw_id` / `source_ref_id` / 历史绑定关系归并到同一原始素材下。
2. 广告数据：展示各翻译版本及汇总的投放消耗、ROAS，窗口包含今天、昨天、7 天、30 天。
3. 订单数据：展示订单量窗口数据，包含今天、昨天、7 天、30 天；产品级订单汇总可复用 `appcore.media_product_ad_orders_report.get_product_ad_orders_report`，并按语种回填到对应翻译版本。
4. AI 评估：右侧展示 8 国建议，国家口径固定为 `DE/FR/IT/ES/NL/PT/SE/JP`。已有结果从产品 AI 评估详情读取；未评估国家显示待评估，并提供入口触发评估。

2026-06-11 修订：未入库明空视频还没有本地 `media_item_id`，因此右侧“翻译版本”和国家卡片只能展示 8 国缺失状态，不得回填产品级 `by_lang` 订单/消耗聚合。产品语种订单与消耗数据只属于已入库且已绑定到该原始素材的翻译版本，不能显示在未入库视频卡片上，避免把同产品其他素材的翻译投放数据误认为当前视频的数据。

AI 评估口径说明：

- 本工作台 V2 的“8 国”采用产品研究国家集：德国、法国、意大利、西班牙、荷兰、葡萄牙、瑞典、日本。
- 素材管理评估链路同步改为 8 国逐国调用：`de/fr/it/es/nl/pt/sv/ja`；历史 6 国 `DE/FR/IT/ES/JA/EN` 结果只能按已命中国家展示，缺失国家必须显示“待评估”。
- 页面调用现有产品评估启动接口并展示进度；单国补评有 run id 时走国家重跑接口，无 run id 时回退为整组 8 国评估。

V2 布局要求：

1. 列表从窄卡网格改为宽卡列表：桌面端每张卡使用左侧素材卡 + 右侧数据面板，移动端上下堆叠。
2. 左侧必须保留补素材卡片关键信息：入库状态、产品名、产品链接、视频文件名、投放热度、90 天消耗、昨日消耗、图片/视频预览、广告数、上传者、上传时间和操作按钮。
3. 右侧必须始终有清晰的数据区块，不因无广告或无评估而空白；无数据时显示“暂无/待评估/未投放”状态。
4. POST 入口继续走现有 CSRF 规则：`/mk-import/video`、`/tasks/api/parent`、`/medias/api/products/<pid>/evaluate` 及评估国家重跑接口都必须带 `X-CSRFToken`。

## Verification

1. 路由测试覆盖新页面登录门禁与模板渲染。
2. service/API 测试覆盖语种投放数据按 lang 去重。
3. 广告详情测试覆盖日期参数、匹配条件和汇总字段。
4. 模板静态测试覆盖：
   - `/medias/api/product/${productId}/video-workbench`
   - 广告数据弹窗
   - `POST /mk-import/video`
   - `POST /tasks/api/parent`
   - CSRF header
5. `python -m compileall web/routes/medias appcore`
6. V2 测试覆盖右侧数据面板存在翻译版本、订单窗口、AI 8 国入口和 8 国代码。
