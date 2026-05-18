# Meta 热帖 PC 卡片放大与 AI 分析结果透出

日期：2026-05-18

## 背景

PC 端 `/xuanpin/meta-hot-posts` 的四个子 tab（素材库、今日新增、欧洲Top50、美国Top50）共用同一套卡片网格。运营刷视频时，默认卡片和 267x476 的视频区域偏小，需要在卡片区上方右侧增加一个一键放大入口。

美国Top50 卡片已经通过 `row.video_copyability` 展示 AI 视频可抄分析分数和摘要；素材库、今日新增、欧洲Top50 目前复用同一卡片模板，但列表查询未带出已完成的 AI 分析结果，因此同一视频在这些 tab 里看不到分析结论。

## 目标

- 在四个子 tab 共用的状态栏右侧提供「卡片放大」按钮。
- PC 端点击后切换为 2x 卡片浏览模式，再次点击恢复默认大小。
- 放大状态在四个子 tab 间保持一致，并通过 `localStorage` 记住。
- 对素材库、今日新增、欧洲Top50 的每张卡片，如果该视频已完成美国可抄 AI 分析，则透出与美国Top50 相同的 `video_copyability` 数据块。
- 美国Top50 现有排序、卡片结构和 AI 数据展示保持不变。

## 设计

- `web/templates/meta_hot_posts.html`：
  - 在 `mh-status` 右侧增加按钮 `mhCardZoomButton`。
  - 用页面级 class `mh-zoomed` 控制 PC 端卡片网格列宽、卡片字体间距和视频尺寸。
  - 默认网格保持 `minmax(360px, 1fr)`；放大模式在 `min-width: 769px` 下使用 `minmax(720px, 1fr)`，视频区域变为 534x952，并限制在卡片可用宽度内。
  - 移动端不启用 2x 尺寸，避免破坏单列浏览。
- `appcore.meta_hot_posts.store`：
  - 素材库、今日新增、欧洲Top50 查询 `LEFT JOIN meta_hot_post_video_copyability_analyses`，只取 `status='done'` 的分析结果。
  - 使用 `video_copyability_*` 前缀字段，避免和商品分析字段冲突。
- `appcore.meta_hot_posts.service`：
  - `_hydrate_item()` 识别前缀字段并生成 `video_copyability`，结构与美国Top50 已有卡片一致。
  - `_hydrate_video_copyability_item()` 继续兼容美国Top50 的无前缀字段。

## 非目标

- 不新增 AI 分析任务、不改变队列调度、不重跑历史视频分析。
- 不改变美国Top50 排名 SQL。
- 不调整移动端卡片尺寸。

## 验证

- `pytest tests/test_meta_hot_posts_service.py tests/test_meta_hot_posts_store.py tests/test_meta_hot_posts_routes.py -q`
- 未登录 `/xuanpin/meta-hot-posts` 继续 302。
- 已登录且有权限用户访问 `/xuanpin/meta-hot-posts` 继续 200。
