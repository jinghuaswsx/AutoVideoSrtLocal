# AI 精细评估中文输出设计

日期：2026-05-23

## 范围

AI 精细评估的产品事实整理、单国家评估、JSON 修复三类大模型请求，都必须要求模型把返回 JSON 中面向运营阅读的字符串值输出为简体中文。

为了不破坏现有接口契约，以下内容保持 schema 约定不翻译：

- JSON 字段名。
- 国家代码，如 `DE`、`FR`、`IT`、`ES`、`JP`。
- 固定枚举值，如 `completed`、`GO`、`TEST`、`HOLD`、`high`、`medium`、`low`。
- 货币代码、URL、`source_url`、时间戳、文件路径、ID。

历史字段名 `generated_search_keywords.english_keywords` 保持不改名，但字段值也输出中文关键词。`country_name` 和 `country_name_zh` 都要求输出中文国家名，避免前端或 trace 面板出现英文国家名。

## 实现

集中在 `appcore/fine_ai_evaluation_prompts.py` 增加统一中文输出规则，并注入：

- `PRODUCT_FACT_SYSTEM_PROMPT`
- `COUNTRY_EVALUATION_SYSTEM_PROMPT`
- `JSON_REPAIR_SYSTEM_PROMPT`
- `build_product_fact_prompt`
- `build_country_evaluation_prompt`
- `build_json_repair_prompt`

不修改 schema、数据库结构、前端渲染和聚合逻辑。

## 验收

- prompt 测试必须确认三类请求都包含中文输出规则。
- 现有 fine AI schema/client/pipeline 测试继续通过。
