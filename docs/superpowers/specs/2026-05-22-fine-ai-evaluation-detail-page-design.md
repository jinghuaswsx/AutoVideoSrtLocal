# 精细 AI 评估独立落地页

最后更新：2026-05-22

## 背景

`AI精细评估` 是选品成败判断的关键流程。Modal 适合快速预览，但长时间评估、国家逐步执行、调试日志和结果复盘需要一个稳定的独立页面，不能依赖原选品页面或弹窗生命周期。

## 目标

- 保留现有 modal 弹窗体验。
- 为每个评估 run 提供独立落地页：
  - 外部选品卡片 run：`/xuanpin/fine-ai-evaluation/<evaluation_run_id>`
  - 已入库产品 run：`/medias/products/<product_id>/ai-evaluation/<evaluation_run_id>/detail`
- Modal 顶部新增“打开独立页”按钮，创建 run 后可跳转到对应页面。
- 独立页持续轮询现有 status/result API，展示顶部进度、步骤卡片、执行日志、国家结果、重跑国家按钮。
- 修正“数据准备已完成但顶部仍显示 queued / 等待开始”的状态映射。只要已有步骤推进，顶部应显示正在请求中或当前执行位置。

## 数据流

1. 视频卡片点击 `精细AI评估`。
2. Modal 先查询存档结果：
   - 已入库产品：`GET /medias/api/products/<product_id>/ai-evaluation/latest`
   - 外部商品链接卡片：`GET /xuanpin/api/fine-ai-evaluation/latest?product_link=...&card_video_path=...`
3. 如果已有结果，Modal 直接展示历史 run；不自动创建新任务。
4. 如果没有结果，才创建新 run 并显示启动进度，同时保留“打开独立页”按钮。
5. 用户需要重跑时，必须在 Modal 或独立页内点击“重新评估”或国家重跑按钮。
6. Modal 和独立页都使用同一套 API：
   - status：`GET .../<run_id>/status`
   - result：`GET .../<run_id>`
   - rerun：`POST .../<run_id>/countries/<country>/rerun`
7. 独立页不重新创建任务，只读取和操作已有 run。

## 状态规则

- `status=queued` 且 `progress.completed_steps > 0` 或 `progress.current_step !== queued` 时，前端按运行态展示。
- `data_preparation` 完成后，`current_step` 应指向 `product_fact_extraction`，避免顶部仍显示 queued。
- 页面端计时每秒刷新，并以服务端 `started_at` / `elapsed_seconds` 为基准。

## 权限

- 所有新页面都必须 `@login_required`。
- 外部选品 run 页面沿用选品中心 admin gate。
- 已入库产品 run 页面沿用产品访问权限与 admin gate。
