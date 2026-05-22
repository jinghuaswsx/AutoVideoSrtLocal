# AI 精细评估大模型请求 Trace 设计

日期：2026-05-23

## 背景

选品中心 / 明空选品 / 视频素材库的视频卡片中，AI 精细评估弹窗会按步骤展示：商品链接检测、数据准备、商品事实整理、DE/FR/IT/ES/JP 国家评估、汇总。运营排查评估质量时，需要看到涉及大模型调用的每个步骤实际使用的 provider、model、完整提示词、请求报文、结构化结果和原始返回报文。

本设计补充 `2026-05-22-fine-ai-evaluation-progress-visualization-design.md`。之前的“调试信息只放安全摘要”仍适用于默认卡片摘要；本次新增的 trace 面板只面向后台管理员，且不得包含 API key、Authorization、Cookie、密钥环境变量或二进制媒体内容。

## 范围

- 覆盖 AI 精细评估弹窗和独立页的步骤卡片。
- 只有实际调用大模型的步骤展示 trace 入口：`product_fact_extraction`、`country_DE`、`country_FR`、`country_IT`、`country_ES`、`country_JP`。
- `product_link_check`、`data_preparation`、`summary` 不展示大模型请求按钮；汇总仍是代码聚合。
- 后端继续通过现有 status/result API 返回 `progress_json`，不新增独立数据库表。

## 数据结构

每个 LLM 步骤在 `progress.steps[]` 内增加：

- `provider`：实际 provider code。
- `model_id`：实际模型 ID。
- `llm_trace`：
  - `provider` / `model_id` / `use_case_code` / `project_id`。
  - `request.summary`：用于可视化的字段摘要。
  - `request.system_prompt`：完整 system prompt。
  - `request.prompt`：完整 user prompt。
  - `request.payload`：发给 `appcore.llm_client.invoke_generate` 的无密钥请求报文，media 只保留路径/URL 字符串，不内联文件内容。
  - `response.summary`：usage、JSON 是否解析成功、返回类型等摘要。
  - `response.parsed_json`：解析后的结构化 JSON。
  - `response.raw_payload`：`invoke_generate` 返回值的 JSON-safe 版本。
  - `error`：失败时保存错误类型和脱敏错误消息。

## 前端

- 步骤卡片标题右侧展示 `provider · model_id` 小标签。
- 有 `llm_trace` 的步骤卡片右上角展示「大模型请求」按钮。
- 点击按钮在弹窗内打开 trace 详情视图：
  - 顶部展示 provider、model、use case、project、media 数量、token usage。
  - 请求区展示完整 system prompt、user prompt、请求 payload。
  - 结果区展示结构化结果摘要、完整 parsed JSON、完整 raw payload。
- 弹窗和独立页复用相同字段名，避免两套后端契约。

## 验收

- 商品事实整理和每个国家评估卡片展示 provider/model 标签。
- 只有 LLM 步骤显示「大模型请求」按钮。
- 点击按钮可以看到完整提示词、请求 payload、结构化结果和 raw payload。
- 前端响应中不出现 API key、Authorization、Cookie 或二进制媒体内容。
