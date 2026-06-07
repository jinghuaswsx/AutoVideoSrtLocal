# 任务中心新素材任务兼容现有产品设计

- 日期：2026-06-07
- 上位锚点：
  - `AGENTS.md`
  - `docs/任务中心需求文档-2026-04-26.md`
  - `docs/superpowers/specs/2026-06-06-task-center-new-product-task-video-flow-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-13-media-video-material-bindings-design.md`
  - `docs/superpowers/specs/2026-05-13-meta-hot-posts-selection-design.md`

## 背景

`docs/任务中心需求文档-2026-04-26.md` 已定义：明空选品或外部视频进入任务中心时，如果商品已在素材管理库存在，应在原产品下新增一条英文视频素材，而不是强制新建产品。2026-06-06 的新品任务流程实现了“外部视频/Meta 热帖 -> 产品 + 英文素材 + 父子任务”的统一入口，但页面和入口文案仍偏向“新品”，缺少明确的现有产品补素材分支。

## 目标

1. 任务中心子菜单和页面命名从“新品任务”调整为“新素材任务”。
2. 创建新素材任务时提供两种模式：
   - `新品任务`：创建新产品，上传视频作为该产品英文源素材。
   - `补充素材`：搜索并选择现有产品，上传视频作为该产品新的英文源素材。
3. 后端接口继续兼容 `POST /tasks/api/new-product`，新增字段区分模式：
   - `task_kind=new_product|supplement`
   - `target_product_id`：补充素材模式必填。
4. 补充素材模式必须沿用目标产品的负责人作为英文素材归属；不要求重新填写产品名称、链接、主图或产品负责人。
5. Meta 热帖“新素材任务”入口支持补充素材：弹窗中先选择新品任务或补充素材；补充素材时搜索选择目标产品，然后把热帖本地视频导入到该产品下并创建父子任务。
6. 素材管理视频卡片/产品视频工作台里对未入库视频提供“补素材任务”入口，沿用当前产品作为目标产品；已入库素材继续可直接创建小语种任务。

## 非目标

1. 不新增数据库表；继续复用 `media_products`、`media_items`、`tasks`、`task_events`。
2. 不改变父子任务状态机和去字幕/牛马提交流程。
3. 不改变已入库素材创建小语种任务的 `POST /tasks/api/parent` 契约。
4. 不实现新的 Meta 热帖视频下载能力；仍只消费 `local_video_status=downloaded` 且本地文件存在的视频。

## 后端契约

### 本地上传

`POST /tasks/api/new-product` 保持 `multipart/form-data`。

通用字段：

- `source=upload`
- `task_kind`：缺省 `new_product`；可选 `supplement`。
- `video_file`：必填。
- `raw_processor_id`：必填。
- `countries`：JSON 数组或逗号分隔目标语种。
- `language_assignments`：JSON object，必须覆盖所有目标语种。
- `is_urgent`：可选。
- `force`：可选。

`task_kind=new_product` 字段：

- `product_name`：必填。
- `owner_id`：必填。
- `product_link` / `product_main_image_url` / `product_code`：沿用旧逻辑。

`task_kind=supplement` 字段：

- `target_product_id`：必填，必须是未删除产品。
- `product_name` / `owner_id` 不必提交；后端从目标产品读取。
- 若提交 `product_link` / `product_main_image_url`，仅在字段非空时补写目标产品资料。

响应新增：

```json
{
  "ok": true,
  "task_kind": "supplement",
  "media_product_id": 1,
  "media_item_id": 2,
  "parent_task_id": 3,
  "is_new_product": false
}
```

### Meta 热帖

`POST /tasks/api/new-product` JSON 继续支持 `source=meta_hot_post`。

新增字段：

- `task_kind=new_product|supplement`
- `target_product_id`：补充素材模式必填。

`task_kind=new_product` 保持旧行为：若热帖已绑定本地产品和素材则复用；否则按热帖自动产品编码创建或复用产品。

`task_kind=supplement`：必须把热帖本地视频复制为目标产品下新的英文 `media_items`；不使用热帖上已有的 `local_media_item_id` 作为任务输入，避免把旧素材误当作本次补充素材。

## 前端行为

### 任务中心新素材任务页

- 路由保留 `/tasks/new-product`，菜单和标题显示“新素材任务”。
- 顶部用分段控件切换“新品任务 / 补充素材”。
- 新品任务显示产品信息表单；补充素材显示产品搜索框和结果列表。
- 选择目标产品后展示产品 ID、名称、Product Code、负责人，并允许本地上传视频。
- 提交成功后文案根据 `task_kind` 显示“新品任务已创建”或“补充素材任务已创建”。

### Meta 热帖页

- 卡片按钮文案改为“新素材任务”。
- 点击后打开任务创建弹窗，并可选择“新品任务 / 补充素材”。
- 补充素材时先搜索并选择目标产品，再选择原视频处理人、目标语种和负责人。
- 提交 JSON 到 `/tasks/api/new-product`，补充素材模式携带 `target_product_id`。

### 视频卡片 / 产品视频工作台

- 当前产品视频工作台中，未入库视频除“加入素材库”外，提供“补素材任务”。
- 点击后直接以当前产品为 `target_product_id`，复用同一个任务分配弹窗，后端把视频导入当前产品并创建任务。

## 验证

1. `tests/test_tasks_routes.py` 覆盖页面双模式标记、上传补充素材契约、Meta 热帖补充素材契约。
2. `tests/test_new_product_tasks.py` 覆盖补充素材服务层复用目标产品 owner、Meta 热帖目标产品透传和补充模式不覆盖产品链接。
3. `tests/test_meta_hot_posts_routes.py` 覆盖按钮文案、弹窗携带 `task_kind` 和 `target_product_id`。
4. `tests/test_medias_product_video_workbench.py` 覆盖视频工作台补素材任务入口。
5. `python -m compileall appcore/new_product_tasks.py appcore/meta_hot_posts/service.py web/routes/tasks.py tests/test_new_product_tasks.py tests/test_tasks_routes.py tests/test_meta_hot_posts_routes.py tests/test_medias_product_video_workbench.py`
6. `git diff --check`
