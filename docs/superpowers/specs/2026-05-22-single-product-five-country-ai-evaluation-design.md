# 单产品五国精细 AI 评估设计

日期：2026-05-22

## 范围

- 仅在单个素材库商品/选品卡片上触发，不做 product_catalog，不批量跑所有产品，不做 portfolio summary。
- 每次点击创建一个 `evaluation_run`，该 run 只属于一个 `product_id`。
- 每个 run 固定评估 `DE`、`FR`、`IT`、`ES`、`JP` 五个国家。
- 产品事实整理只执行一次；国家评估按 `DE -> FR -> IT -> ES -> JP` 串行执行。
- 每个国家一次完整大模型调用，调用内覆盖市场、竞品、价格、素材、落地页、风险和建议，不拆成多个国家子报告调用。
- 返回和持久化的主结果均为结构化 JSON，不输出 Markdown 报告，不把 prompt/API key/堆栈返回前端。

## LLM 通道

- provider：`gemini_vertex_adc`
- model：`gemini-3.5-flash`
- 产品事实：structured JSON，thinking level 记录为 `medium`。
- 国家评估：structured JSON，启用 Google Search，thinking level 记录为 `high`。
- 汇总优先代码聚合，避免额外模型调用。
- 素材存在时作为 media 传入；素材缺失或文件不存在只进入 `missing_data`/`warnings`，不得中断整个 run。

## API

- `POST /medias/api/products/<product_id>/ai-evaluation`
- `GET /medias/api/products/<product_id>/ai-evaluation/<evaluation_run_id>/status`
- `GET /medias/api/products/<product_id>/ai-evaluation/<evaluation_run_id>`
- `GET /medias/api/products/<product_id>/ai-evaluation/latest`
- `POST /medias/api/products/<product_id>/ai-evaluation/<evaluation_run_id>/countries/<country_code>/rerun`

响应外壳统一为：

```json
{"success": true, "data": {}, "error": null}
```

错误外壳统一为：

```json
{"success": false, "data": null, "error": {"code": "...", "message": "..."}}
```

## 数据表

- `ai_evaluation_runs`
- `ai_country_evaluations`
- `ai_evaluation_assets`

run 表保存产品快照、产品事实、五国 summary、frontend 映射、metadata。国家表保存国家完整 JSON、scores、decision、sources、raw_response、metadata。

## 前端

选品中心视频素材卡片在现有 `AI评估` 下方新增 `精细AI评估` 按钮。按钮打开同一类弹窗外壳，但渲染精细评估专用 JSON：

- running：显示当前步骤和五国状态，文案体现正在评估哪个国家。
- completed/partially_completed：渲染总览卡片、五国评分表、国家详情 tab、图表数据、action items。
- 单国 failed：显示错误摘要，不影响其他国家。
- 支持整体重新评估和单国重新评估。

## 验收约束

- 不硬编码具体产品信息、类目、素材文案或 claim。
- score 必须为 0-100 integer。
- `final_decision` 仅允许 `GO`、`TEST`、`HOLD`。
- 国家码仅允许 `DE`、`FR`、`IT`、`ES`、`JP`。
- 某个国家失败时其他国家继续，最终 run 为 `partially_completed`。
- 前端不依赖 Markdown 渲染。
