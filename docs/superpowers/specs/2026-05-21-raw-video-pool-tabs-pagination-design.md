# 原始素材处理列表重构设计

- **日期**：2026-05-21
- **上位锚点**：
  - `AGENTS.md`
  - `docs/superpowers/specs/2026-04-26-raw-video-pool-design.md`
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/plans/2026-05-20-task-center-raw-source-automation.md`

## 背景

原始素材处理页仍保留早期“待认领 / 我已认领 / 已上传待审”结构。当前业务已经改为管理员在创建或分配任务时指定原视频处理人，处理人不再从公共池认领任务。继续展示“待认领”会误导处理流程，也让列表无法按任务中心的处理阶段统一查看。

## 目标

1. 原始素材处理页移除“待认领”模块和认领按钮。
2. 页面改为 4 个 TAB：任务总览、待处理、待审核、已完成。
3. 列表参考任务中心，使用表格化展示和分页。
4. 表格统一新增“任务入口”列，内容为“任务详情”按钮。
5. “任务详情”跳转到该父任务对应的牛马去字幕原始视频素材处理详情页，即 `/subtitle-removal/<subtitle_task_id>`。

## 状态映射

| TAB | 后端 bucket | 父任务状态 |
| --- | --- | --- |
| 任务总览 | `overview` | `raw_in_progress`, `raw_review`, `raw_done`, `all_done` |
| 待处理 | `todo` | `raw_in_progress` |
| 待审核 | `review` | `raw_review` |
| 已完成 | `done` | `raw_done`, `all_done` |

`pending` 不再进入原始素材处理页，因为任务来源必须是管理员指派后的任务。

## 服务与接口

`appcore/raw_video_pool.py` 增加分页列表能力：

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

每条任务保留现有字段，并增加：

- `status`：父任务状态。
- `updated_at`：用于列表排序和显示。
- `task_detail_url`：最近一条 `raw_niuma_submitted/raw_niuma_done/raw_niuma_failed/raw_niuma_timeout` 事件中的 `subtitle_task_id` 转成 `/subtitle-removal/<id>`。没有对应事件时返回空字符串，前端按钮禁用。

`GET /raw-video-pool/api/list` 接收 `bucket/page/page_size`，`page_size` 上限 100。

## 前端

`raw_video_pool_list.html` 改为：

1. 顶部保留标题和刷新按钮。
2. TAB 顺序为：任务总览、待处理、待审核、已完成。
3. 表格列为：产品、国家、文件名、大小、创建时间、处理进度、原始库、任务入口、操作。
4. “任务入口”列始终渲染“任务详情”按钮；无 `task_detail_url` 时禁用。
5. 待处理行保留下载和上传替换视频操作。
6. 待审核行保留禁用态“待人工审核，通过后入库”。
7. 已完成行不提供上传操作，只显示“已完成”。
8. 底部分页使用任务中心同口径：第 N / M 页、上一页、下一页、总数。

## 不做范围

1. 不改变任务状态机。
2. 不新增数据库表或迁移。
3. 不改任务创建、管理员指派和牛马自动提交流程。
4. 不改字幕移除详情页自身功能。

## 验证

1. `pytest tests/test_raw_video_pool_service_unit.py tests/test_raw_video_pool_routes.py -q`
2. 未登录访问 `/raw-video-pool/` 应返回 302 或 401。
3. 登录访问 `/raw-video-pool/` 应返回 200，页面包含 4 个 TAB、分页函数和“任务入口”列。
4. 不连接 Windows 本机 MySQL，不重启服务。
