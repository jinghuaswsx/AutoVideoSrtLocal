# 任务中心原始视频素材复用设计

- 日期：2026-05-21
- 上位锚点：
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-assignment-and-niuma-automation-fix.md`
  - `docs/superpowers/specs/2026-04-21-medias-raw-sources-design.md`

## 背景

明空视频卡片加入素材库后会生成一条英文 `media_items`。任务中心创建小语种任务时，父任务绑定这条 `media_items`，并自动把它提交给牛马做原始视频处理。

当前问题是：同一个视频素材多次创建小语种任务时，系统每次都会重新提交牛马。第一次审核通过后生成的 `media_raw_sources` 只停留在产品级列表里，没有稳定绑定回该视频卡片对应的 `media_items`，因此后续创建任务无法判断“原始视频素材已经处理过”。

## 目标

1. 原始视频处理审核通过后，把生成或更新的 `media_raw_sources.id` 回写到父任务绑定的 `media_items.source_raw_id`。
2. 创建新的小语种父任务前，优先检查 `media_items.source_raw_id` 是否指向有效原始素材。
3. 若旧数据没有 `source_raw_id`，允许按同产品 + 同文件名找到已有 `media_raw_sources`，并立即补回 `media_items.source_raw_id`。
4. 已有有效原始素材时，新父任务直接进入 `raw_done`，子任务直接进入 `assigned`，不再启动牛马。
5. 任务时间线记录 `raw_source_reused`，响应里返回 `raw_processing.status = "skipped"`，便于前端和排查理解这次为什么没有处理原视频。

## 非目标

- 不新增数据库表或字段；复用已有 `media_items.source_raw_id`。
- 不改变牛马处理链路本身。
- 不改变明空卡片的入库流程和素材绑定表结构。

## 验证

- `task_raw_source_bridge` 单测覆盖：审核通过后绑定 `media_items.source_raw_id`；历史同名原始素材可被发现并补绑定。
- `tasks.create_parent_task` 单测覆盖：传入复用原始素材时父任务 `raw_done`、子任务 `assigned`、写入 `raw_source_reused`。
- `/tasks/api/parent` 路由单测覆盖：已存在原始素材时不调用牛马启动函数，返回 skipped。
