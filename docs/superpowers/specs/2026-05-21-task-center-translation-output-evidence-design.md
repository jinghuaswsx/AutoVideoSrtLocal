# 任务中心翻译产物可视化证据设计

- **日期**：2026-05-21
- **上位锚点**：
  - `docs/superpowers/specs/2026-05-16-task-center-e2e-flow-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-child-acceptance-design.md`
  - `docs/superpowers/specs/2026-05-20-task-center-step-review-assets-design.md`

## 背景

任务中心子任务详情已经展示“翻译产物状态”，但每一行主要是通过/未通过文字，用户无法直接看到对应的中间产物。管理员和翻译员需要在同一处确认：商品链接能否点击、封面图是否正确、翻译后视频是否能播放、详情图和文案是否已产出。

## 目标

1. 保留现有验收项，不改变任务状态机和提交门禁。
2. 每个验收项返回结构化 `evidence`，前端按类型渲染为链接、图片、视频或文字状态。
3. 链接类证据必须直接是可点击超链接。
4. 图片类证据直接显示缩略图，可打开原图。
5. 视频类证据直接使用浏览器原生播放器。
6. 产物证据嵌入“翻译产物状态”面板，并继续复用现有审核流程中的媒体展示能力。

## 状态行语义

| key | 页面文案 | 证据 |
| --- | --- | --- |
| `localized_media_item` | 目标语种素材 | 素材管理入口和目标语种 `media_item_id` |
| `translated_video` | 视频翻译结果 | `/medias/object?object_key=...` 视频播放器 |
| `translated_cover` | 封面翻译结果 | `/medias/item-cover/<item_id>` 图片缩略图 |
| `translated_copywriting` | 文案翻译结果 | 目标语种文案摘要 |
| `push_texts` | 推送文案格式 | 英文三段文案解析状态 |
| `product_listed` | 商品在架状态 | 在架/未在架状态 |
| `language_supported` | 广告语言配置 | 当前目标语种是否在广告语言配置中 |
| `detail_images` | 产品详情图翻译 | 目标语种详情图缩略图列表 |
| `shopify_images` | 链接商品图替换 | 按域名显示替换确认状态，并尽量附商品页链接 |
| `product_links` | 商品链接探活 | 按域名显示商品页超链接、探活状态和错误 |

## 接口约定

`GET /tasks/api/child/<task_id>/readiness` 的每个 `checks[]` 项新增可选字段：

```json
{
  "key": "translated_video",
  "label": "视频翻译结果",
  "ok": true,
  "required": true,
  "reason": "",
  "evidence": [
    {
      "type": "video",
      "label": "视频翻译结果",
      "url": "/medias/object?object_key=...",
      "filename": "de.mp4",
      "media_item_id": 5
    }
  ]
}
```

`evidence.type` 首期支持 `link`、`video`、`image`、`text`、`status`。没有可展示产物时不返回证据，前端只显示原有状态和原因。

## 前端行为

- `翻译产物状态` 每一行保留勾/叉、标题、原因、数量。
- 行内证据在标题下方显示：
  - `link` 渲染为 `<a target="_blank">`。
  - `video` 渲染为 `<video controls preload="metadata">`。
  - `image` 渲染为缩略图，点击打开原图。
  - `text/status` 渲染为紧凑信息块。
- 原 `review-assets` 审核流程展示保留；提交审核后的步骤内仍能直接播放视频和查看图片。

## 不做范围

- 不新增数据库表或迁移。
- 不改变 `submit_child` 的必需项规则。
- 不把状态行拆成新的子任务。
- 不新增无鉴权下载入口；所有资源 URL 复用现有登录态路由。

## 验证

1. `pytest tests/test_appcore_tasks_supporting_data.py::test_get_child_readiness_computes_payload tests/test_task_center_closure_assets.py::test_task_center_timeline_renders_review_assets_in_steps -q`
2. `python -m compileall appcore/tasks.py web/routes/tasks.py`
3. 手工打开 `/tasks/`，进入德语子任务详情，确认链接可点击、封面图可见、视频可播放。
