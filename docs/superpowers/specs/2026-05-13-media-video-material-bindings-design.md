# Media Video Material Bindings Design

## Goal

素材管理新增视频素材维度的管理入口，并让今日推荐避开已经进入英文素材库的视频素材。

## Scope

- 素材管理页保留原产品维度内容，但 tab 文案改为“产品管理”。
- 新增“视频素材管理” tab，列表展示 `media_items` 中未删除的视频素材。
- 列表支持按产品 ID / 产品编码 / 素材名搜索，按素材语种筛选，按是否已有广告计划筛选。
- “有广告计划”以素材已有成功投放记录为准：`media_items.pushed_at IS NOT NULL` 或存在成功的 `media_push_logs`。
- 每条视频素材可绑定一条明空系统素材。绑定通过明空 `/api/marketing/medias` 搜索，人工选择具体视频后落库。
- 今日推荐生成时，排除已存在的英文素材：已绑定的明空视频路径、已绑定明空文件名、英文素材库中已有的文件名/对象名都不再推荐。

## Data Model

新增 `media_item_mk_bindings`：

- `media_item_id`：本地素材 ID，一条本地素材最多绑定一条明空素材。
- `mk_product_id / mk_product_name`：明空商品信息。
- `mk_video_path / mk_video_name / mk_video_image_path`：明空视频唯一线索。
- `mk_video_metadata_json`：保留 spends、ads_count、author、duration 等明空原始元数据。
- `bound_by / bound_at`：人工绑定审计字段。

## UI

素材管理页顶部增加两个 tab：

- “产品管理”：原来的产品列表、产品编辑、上传、推送等能力不改变。
- “视频素材管理”：新的素材列表，筛选条包括搜索、语种、广告计划状态。列表内有预览、产品信息、素材信息、投放状态、明空绑定状态和“绑定”按钮。

绑定按钮打开 modal：

- 输入框按素材文件名或关键词搜索明空素材。
- 结果按明空商品展开为视频行，显示文件名、花费、广告数和明空商品名。
- 点击“绑定”后保存关系，列表立即刷新。

## API

- `GET /medias/api/video-materials`
- `GET /medias/api/video-materials/mk-search`
- `POST /medias/api/video-materials/<item_id>/mk-binding`

## Testing

- Service tests cover filters, serialization, binding upsert, and recommendation exclusion keys.
- Route tests cover page tab/script rendering and three JSON endpoints.
- Existing product management behavior remains covered by current medias tests.
