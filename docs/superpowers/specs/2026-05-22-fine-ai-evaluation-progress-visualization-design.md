# 精细 AI 评估执行可视化设计

日期：2026-05-22

## 背景

精细 AI 评估用于入库前判断商品是否值得加入素材库。任务必须在后端持续运行，前端弹窗只负责读取和展示进度，不能把执行状态寄存在页面里。

## 执行顺序

每个 run 固定按下面顺序推进：

1. 数据准备：整理商品链接、商品快照、素材数量、目标国家。
2. 商品事实整理：请求大模型抽取跨国家共享的商品事实。
3. 德国 DE：请求大模型进行单国家评估。
4. 法国 FR：请求大模型进行单国家评估。
5. 意大利 IT：请求大模型进行单国家评估。
6. 西班牙 ES：请求大模型进行单国家评估。
7. 日本 JP：请求大模型进行单国家评估。
8. 汇总：后端聚合五国结果并生成前端展示数据。

国家评估必须串行执行，后一国家只有在前一国家完成或失败后才开始。

## 后端状态

`ai_evaluation_runs.progress_json` 是执行可视化的唯一事实来源。每次进入步骤、请求大模型、收到结果、失败、汇总完成时，后端都要更新 `progress_json`。

`progress_json` 至少包含：

- `total_steps` / `completed_steps` / `current_step` / `current_country`
- `started_at` / `elapsed_seconds`
- `countries`：DE、FR、IT、ES、JP 的 pending/running/completed/failed 状态
- `steps`：从上往下的步骤卡片数据，每个步骤带 `key`、`title`、`status`、`message`、`started_at`、`completed_at`、`logs`、`debug`
- `events`：全局执行明细，按时间倒序或顺序都可以，但必须带时间、级别、步骤 key、消息

调试信息只能放安全摘要，例如 provider、model、商品链接、素材数量、国家、语言、货币、分数、决策、缺失数据数量、token usage。不得把 API key、完整栈、未脱敏密钥写入前端响应。

## 前端展示

弹窗顶部展示任务进度条和时间进度，参考素材管理 AI 评估弹窗：

- queued/running：显示“正在请求中/正在评估中”和已运行秒数。
- completed/partially_completed/failed：显示最终状态和总耗时。

主体从上往下展示：

1. 数据准备卡片。
2. 商品事实整理卡片。
3. 一行一个国家卡片，顺序为 DE、FR、IT、ES、JP。
4. 汇总卡片。

不同状态使用不同颜色。当前执行卡片必须有 loading 效果。每个卡片展示最近日志，并能看到调试字段。前端只轮询 status/result API 并渲染后端返回的 `progress_json`，不参与真实任务执行。

## 验收

- 创建任务后，不依赖弹窗是否打开，后端仍持续更新 run 状态。
- status API 能看到当前步骤、国家状态、步骤日志和调试字段。
- result API 保留最终 `progress_json`，完成后仍能复盘执行过程。
- 单国家失败时，失败国家卡片变为 failed，后续国家继续执行，最终 run 为 `partially_completed`。
- 服务重启或 worker 消失后，启动恢复只做状态收口，不自动续跑：无活跃线程的 queued/running run 标为 `interrupted`，未完成国家写入 failed 结果以便用户手动重跑；前端把 `interrupted` 视为终态并停止轮询。
