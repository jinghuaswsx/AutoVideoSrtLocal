# 素材 AI 评估按国家拆分设计

## 背景

`material_evaluation.evaluate` 目前一次请求要求模型同时返回 DE/FR/IT/ES/JA/EN 六个国家。生产记录显示 OpenRouter `google/gemini-3.5-flash` 在部分商品上只返回一个国家，后端补齐缺失国家为“模型未返回该语种结果，需人工复核”，但仍按成功落库，导致运营误以为 AI 评估已完整完成。

## 目标

- 素材管理、推送管理复用的产品 AI 评估改为按目标国家逐个调用 LLM。
- 每次 LLM 请求只携带一个目标国家/语种，响应 schema 也只要求一个国家，降低输出截断和结构化遗漏概率。
- 产品最终仍写回原有 `ai_score`、`ai_evaluation_result`、`ai_evaluation_detail.countries` 结构，前端表格和推送弹窗无需改契约。
- 默认模型改为 Gemini 3 Flash：OpenRouter 使用 `google/gemini-3-flash-preview`，Google 原生通道使用 `gemini-3-flash-preview`。

## 非目标

- 不改素材评估表结构。
- 不改 AI 评估前端表格渲染协议。
- 不调整精细 AI 评估链路。

## 实现方案

`evaluate_product_if_ready` 在完成商品链接、封面、英文视频和尝试次数预检后，遍历 `evaluation_target_languages()`。每个国家调用现有 `_invoke_evaluation_llm_with_recovery`，但使用该国家专属 prompt、response schema、usage `project_id` 和 billing extra。每国响应通过 `normalize_result(raw, [lang])` 归一化，再聚合成原来的 `countries` 数组，最后统一计算均分和总结果。

评估详情增加轻量元数据：`evaluation_mode: "per_country"`、`country_call_count`，并在有解析修复或重试时按国家保存 `llm_recovery`。

## 验证

- 单元测试覆盖默认模型切换到 Gemini 3 Flash。
- 单元测试覆盖 3 个目标国家时触发 3 次 LLM 调用，且每次 schema 只包含对应国家。
- 回归测试覆盖既有素材评估成功、JSON 修复、原始重试、预检失败路径。
