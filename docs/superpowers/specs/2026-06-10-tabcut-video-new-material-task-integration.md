# TABCUT 视频库新素材任务集成设计

- 日期：2026-06-10
- 上位锚点：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-05-12-tabcut-crawler-design.md`
  - `docs/superpowers/specs/2026-06-06-task-center-new-product-task-video-flow-design.md`
  - `docs/superpowers/specs/2026-06-07-task-center-new-material-task-existing-product-design.md`

## 背景

`/xuanpin/tabcut` 已经提供 TABCUT 视频库、商品信息、本地视频播放和精细 AI 评估。任务中心的新素材任务已经支持本地上传和 Meta 热帖来源，但 TABCUT 视频卡还不能直接创建“新品任务”或“补充素材任务”，运营需要手工下载/上传，流程割裂。

## 目标

1. TABCUT 视频卡增加“创建新品任务”入口，使用该视频作为英文源素材，创建素材管理产品、英文视频素材、去字幕父任务和小语种子任务。
2. 同一入口支持切换“补充素材”，搜索并选择现有产品后，把 TABCUT 视频复制为该产品新的英文素材，再创建父子任务。
3. 后端复用 `POST /tasks/api/new-product`，新增 JSON 来源 `source=tabcut_video`。
4. 仅允许消费 `tabcut_videos.local_video_status='success'` 且本地视频文件真实存在的视频；未本地化或文件缺失时返回明确错误。
5. 新品模式使用 TABCUT 商品标题、商品链接、商品图和视频 ID 生成或复用素材管理产品；补充素材模式必须沿用目标产品负责人，不覆盖目标产品已有链接资料。
6. 创建成功后页面显示任务号，并返回素材管理产品、英文素材和任务中心父任务 ID。

## 非目标

1. 不新增新的任务中心状态机。
2. 不新增独立任务创建页面；TABCUT 页面复用 Meta 热帖已有的任务分配弹窗交互。
3. 不实现新的 TABCUT 下载能力；仍依赖现有本地化任务先把视频下载到本地。
4. 不改变 TABCUT 商品榜表格，只接入视频库卡片。

## 后端契约

`POST /tasks/api/new-product` JSON 新增：

```json
{
  "source": "tabcut_video",
  "video_id": "7500000000000000000",
  "task_kind": "new_product",
  "owner_id": 9,
  "raw_processor_id": 9,
  "countries": ["DE", "FR"],
  "language_assignments": {"DE": 9, "FR": 10},
  "is_urgent": false,
  "force": false
}
```

补充素材模式：

```json
{
  "source": "tabcut_video",
  "video_id": "7500000000000000000",
  "task_kind": "supplement",
  "target_product_id": 88,
  "owner_id": 9,
  "raw_processor_id": 9,
  "countries": ["DE"],
  "language_assignments": {"DE": 9}
}
```

响应沿用新素材任务响应，并额外返回 `tabcut_video_id`：

```json
{
  "ok": true,
  "source": "tabcut_video",
  "task_kind": "new_product",
  "tabcut_video_id": "7500000000000000000",
  "media_product_id": 12,
  "media_item_id": 34,
  "parent_task_id": 56
}
```

## 数据与导入规则

1. TABCUT 导入时读取 `tabcut_video_candidates` 最新候选行、`tabcut_videos` 本地视频字段和 `tabcut_goods` 商品字段。
2. 新品模式产品编码使用 `tabcut-<video_id>`，若产品已存在则复用；补充素材模式使用 `target_product_id`。
3. 英文素材文件名使用 `tabcut_<video_id>.mp4`，如冲突则追加递增后缀。
4. 视频文件从 `OUTPUT_DIR/tabcut/videos` 相对路径复制到 `local_media_storage`；封面存在时复制为 `cover_object_key` 并写入缩略图。
5. `tabcut_videos` 记录本次导入产生的 `local_product_id/local_media_item_id`，用于卡片刷新后展示“任务 #xxx”并避免新品模式重复入库。
6. 补充素材模式每次创建新的英文素材，不复用 `tabcut_videos.local_media_item_id`。

## 前端行为

1. TABCUT 视频卡操作区增加“创建新品任务”按钮。
2. 点击后打开任务分配弹窗，顶部支持“新品任务 / 补充素材”切换。
3. 补充素材模式使用任务中心现有 `/tasks/api/material-products` 搜索目标产品。
4. 未本地化成功的视频按钮禁用或弹出“本地视频未就绪”。
5. 提交到 `/tasks/api/new-product`，携带 `source=tabcut_video`、`video_id`、`task_kind`、`target_product_id`、语种和负责人。

## 验证

1. `tests/test_new_product_tasks.py` 覆盖 TABCUT 新品和补充素材服务层分发。
2. `tests/test_tasks_routes.py` 覆盖 `source=tabcut_video` 路由契约和审计字段。
3. `tests/test_tabcut_selection_routes.py` / `tests/test_xuanpin_routes.py` 覆盖 TABCUT 页面按钮、弹窗和提交 payload。
4. `python -m compileall appcore/new_product_tasks.py appcore/tabcut_selection/service.py appcore/tabcut_selection/video_localization.py web/routes/tasks.py`
5. `python3 scripts/pytest_related.py --base origin/master --run`，如脚本无目标则运行上述相关测试文件。
6. `git diff --check`
