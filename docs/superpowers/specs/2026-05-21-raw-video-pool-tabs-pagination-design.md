# 原始素材处理任务中心化重构设计

- **日期**：2026-05-21
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-26-raw-video-pool-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/plans/2026-05-20-task-center-raw-source-automation.md`

## 背景

原始素材处理页最初从“待认领 / 我已认领 / 已上传待审”三段工作池演进而来。当前业务已经改为管理员在任务中心创建或分配任务时指定原视频处理人，处理人不再从公共池认领任务。上一版虽然移除了待认领并保留了 4 个 TAB，但列表仍是 raw-video-pool 自己维护的查询和展示，脱离任务中心父任务逻辑，导致用户能看到列表，却不能像任务中心一样处理任务，也看不全任务状态进度。

2026-05-21 后续收敛见 `2026-05-21-task-menu-consolidation-design.md`：独立 `/raw-video-pool/` 页面不再作为工作台展示，登录后重定向到 `/tasks/`；任务处理统一在任务中心详情抽屉完成。本文件保留为历史上下文和 API 兼容说明。

## 目标

1. 原始素材处理页只保留 4 个 TAB：任务总览、待处理、待审核、已完成。
2. 列表数据复用任务中心列表逻辑，只展示父任务（原始视频段），权限、负责人、状态分桶与任务中心一致。
3. 用户必须能看到自己被指派的原始素材处理任务；管理员能看到全部父任务。
4. 用户必须能直接处理任务：待处理任务提供下载原视频、上传处理后视频、打开牛马去字幕详情、打开任务中心详情。
5. 用户必须能看到状态进度：列表显示任务中心状态、牛马/手动处理进度、原始库状态；详情抽屉显示任务事件时间线。

## 状态映射

| TAB | 后端 bucket | 父任务状态 |
| --- | --- | --- |
| 任务总览 | `overview` | 任务中心可见的所有父任务状态 |
| 待处理 | `todo` | `raw_in_progress` |
| 待审核 | `review` | `raw_review` |
| 已完成 | `done` | `raw_done`, `all_done` |

`pending` 和 `cancelled` 只在任务总览里出现，行为与任务中心一致。待处理 / 待审核 / 已完成三个 TAB 只展示对应处理阶段。

## 服务与接口

`appcore/tasks.py` 的 `list_task_center_items()` 增加可选参数 `parent_only`，默认 `False`，不影响任务中心现有调用。原始素材处理页调用时传 `parent_only=True`，从同一套任务中心 SQL、权限和状态分桶中取父任务。

`appcore/raw_video_pool.py` 的 `list_visible_tasks()` 变为任务中心父任务列表适配器：

```python
def list_visible_tasks(
    *,
    viewer_user_id: int,
    viewer_role: str,
    bucket: str = "overview",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    ...
```

返回：

```json
{
  "items": [],
  "page": 1,
  "page_size": 20,
  "total": 0,
  "total_pages": 0,
  "bucket": "overview",
  "counts": {
    "overview": 0,
    "todo": 0,
    "review": 0,
    "done": 0
  }
}
```

每条任务以任务中心 row 为基础，并补充原始素材处理页需要的字段：

- `status`：父任务状态。
- `high_level`、`assignee_display_name`、`product_code`、`source_media_filename`：与任务中心一致。
- `country_codes`：父任务下子任务语种聚合。
- `raw_processing_status`：最近牛马/手动上传进度。
- `raw_source_status`：原始库入库状态。
- `subtitle_detail_url`：最近一条 `raw_niuma_submitted/raw_niuma_done/raw_niuma_failed/raw_niuma_timeout` 事件中的 `subtitle_task_id` 转成 `/subtitle-removal/<id>`。没有对应事件时按钮禁用。
- `task_center_url`：`/tasks/?task_id=<id>`，始终可打开任务中心同一任务详情。

`GET /raw-video-pool/api/list` 接收 `bucket/page/page_size`，`page_size` 上限 100。

## 前端

`raw_video_pool_list.html` 改为任务中心风格的父任务工作台：

1. 顶部只保留标题和刷新按钮，不展示标题下方说明文案。
2. TAB 顺序为：任务总览、待处理、待审核、已完成。
3. 表格列为：任务、国家、状态、负责人、创建时间、处理进度、原始库、任务入口、操作。
4. “任务”列展示产品名、任务 id、product code、源素材文件名。
5. “任务入口”列展示“任务详情”按钮，有 `subtitle_detail_url` 时跳转牛马去字幕原始视频素材处理详情；没有时禁用。
6. “操作”列按任务中心父任务语义展示：
   - `raw_in_progress`：处理任务（打开本页详情抽屉）。
   - `raw_review`：查看进度；管理员可回任务中心审核。
   - `raw_done/all_done`：查看结果。
   - 其他状态：查看记录。
7. 详情抽屉展示：
   - 任务中心同口径身份信息、状态、负责人。
   - 待处理任务的下载原视频、上传替换视频按钮。
   - 牛马去字幕详情入口（有 `subtitle_detail_url` 时）。
   - 任务中心详情入口（始终可用）。
   - 任务事件时间线，展示所有状态进度。
8. 底部分页使用任务中心同口径：第 N / M 页、上一页、下一页、总数。

## 不做范围

1. 不改变任务状态机。
2. 不新增数据库表或迁移。
3. 不改任务创建、管理员指派和牛马自动提交流程。
4. 不改字幕移除详情页自身功能。
5. 不在本页实现管理员审核通过/打回；审核仍跳任务中心详情完成。

## 验证

1. `pytest tests/test_raw_video_pool_service_unit.py tests/test_raw_video_pool_routes.py -q`
2. 未登录访问 `/raw-video-pool/` 应返回 302 或 401。
3. 登录访问 `/raw-video-pool/` 应返回 200，页面包含 4 个 TAB、分页函数和“任务入口”列。
4. 不连接 Windows 本机 MySQL，不重启服务。
