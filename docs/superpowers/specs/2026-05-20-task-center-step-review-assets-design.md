# 任务中心步骤内审核素材设计

- **日期**：2026-05-20
- **上位锚点**：
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-review-process-view-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`

## 背景

任务详情抽屉已有中文审核流程和“通过 / 打回”操作，但审核节点没有展示对应的实际产出。管理员进入父任务 `raw_review` 或子任务 `review` 时，只能看到状态和流程记录，不能直接播放视频或查看图片，因此无法判断是否应该通过或打回。

审核素材必须和流程步骤对应：在哪一步提交了什么、系统产出了什么、当前需要审核什么，都应在同一条步骤卡片里可见。

## 目标

1. 当前待审核节点提供明确入口，管理员能直接看到当前要审核的具体素材。
2. 审核素材按流程步骤归档展示，而不是作为脱离流程的孤立列表。
3. 视频素材使用浏览器原生播放器，可点击播放。
4. 图片素材直接显示缩略图，可打开原图。
5. 只改任务中心展示和只读接口，不新增数据库表，不改变任务状态机。

## 审核资产规则

### 父任务原素材审核

- 当前审核状态：父任务 `status = raw_review`。
- 当前审核步骤：`raw_uploaded`。
- 关联产出步骤：
  - `raw_niuma_done`：牛马去字幕产出，展示父任务绑定的 `media_item_id` 视频。
  - `raw_manual_uploaded`：手动上传产出，展示父任务绑定的 `media_item_id` 视频。
  - `raw_uploaded`：提交原始视频审核，标记为“当前待审核”，展示同一视频。
- 视频 URL 使用已登录素材路由 `/medias/object?object_key=<encoded>`。

### 子任务翻译验收

- 当前审核状态：子任务 `status = review`。
- 当前审核步骤：`submitted`。
- 审核素材来源：
  - `media_items.task_id = child_task_id` 的翻译后视频。
  - 对应 `media_items.cover_object_key` 的封面图。
  - `media_product_detail_images` 中该产品 + 目标语种的详情图。
- `submitted` 步骤标记为“当前待审核”，同卡片展示视频、封面和详情图。
- 子任务仍保留“翻译产物状态”验收项，但审核入口必须能打开实际素材。

## 接口

新增 `GET /tasks/api/<task_id>/review-assets`。

返回示例：

```json
{
  "current_review": {
    "event_type": "submitted",
    "title": "当前待审核：翻译产物",
    "asset_count": 3
  },
  "steps": [
    {
      "event_type": "submitted",
      "title": "提交翻译验收",
      "review_target": true,
      "assets": [
        {
          "type": "video",
          "label": "翻译视频",
          "url": "/medias/object?object_key=1%2Fmedias%2F10%2Ffr.mp4",
          "filename": "fr.mp4",
          "file_size": 26214400
        },
        {
          "type": "image",
          "label": "封面",
          "url": "/medias/item-cover/88",
          "filename": "cover.jpg"
        },
        {
          "type": "image",
          "label": "详情图 1",
          "url": "/medias/detail-image/91",
          "filename": "detail_1.jpg"
        }
      ]
    }
  ]
}
```

当没有可预览素材时，接口仍返回对应步骤，`assets = []`，前端展示“暂无可预览素材，不能仅凭空白内容判断，请回到处理人确认产物是否已绑定”。

## 前端行为

- 任务详情打开后并行请求 `events` 与 `review-assets`。
- 审核流程渲染时按 `event_type` 把资产嵌入对应事件卡片。
- 抽屉操作按钮前显示“当前审核内容”入口：
  - 有当前审核步骤时，按钮滚动并聚焦到对应步骤。
  - 无当前审核步骤或无素材时，显示静态提示。
- 事件卡片中：
  - `video` 渲染为 `<video controls preload="metadata">`。
  - `image` 渲染为 `<a target="_blank"><img></a>`。
  - 文件名、大小、语言作为辅助信息显示。
- 产出素材汇总面板保留，用于全局查看；审核判断以步骤内“当前待审核”卡片为准。

## 权限与安全

- API 必须 `@login_required`。
- 素材 URL 复用既有已登录路由，避免新增无鉴权下载入口。
- 前端不拼接本地路径，只使用后端返回的 URL。

## 验证

1. `pytest tests/test_tasks_routes.py tests/test_task_center_closure_assets.py tests/test_task_review_assets_service.py -q`
2. `python3 -m compileall appcore/tasks.py web/routes/tasks.py`
3. 手工打开 `/tasks/`：父任务原素材审核能在 `raw_uploaded` 步骤播放视频；子任务翻译验收能在 `submitted` 步骤播放视频并查看图片。

本项目规则禁止连接 Windows 本机 MySQL；涉及 DB fixture 的验证不作为本次默认检查。
