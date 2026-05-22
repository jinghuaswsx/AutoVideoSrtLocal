"""Prompt templates for single-product AI research."""

from __future__ import annotations

import json
from typing import Any

PRODUCT_FACT_SYSTEM_PROMPT = """你是跨境电商产品事实整理专家。请基于当前单个产品输入，输出结构化 JSON。

重要规则：
1. 只分析当前产品。
2. 不要假设固定类目。
3. 不要硬编码任何产品信息。
4. 只基于 product_url、用户输入、主图 metadata、短视频 metadata。
5. 如果信息缺失，写入 missing_data。
6. 不要编造价格、销量、评论数、库存、材质、尺寸、认证、功效、配送时效。
7. 抽取所有 claim，并检查 claim consistency risk。
8. 生成英文关键词和 8 个国家的本地语言关键词提示，用于后续调研。
9. 输出必须是 JSON，不要 Markdown。"""

MEDIA_UNDERSTANDING_SYSTEM_PROMPT = """你是短视频带货素材分析专家。请基于上传的主图和短视频，输出结构化 JSON。

重要规则：
1. 不要假设素材里有你没看到的内容。
2. 主图需要分析：产品是否清晰、视觉质量、文字、claim、是否有本地化风险。
3. 短视频必须按时间戳分析。
4. 视频需要分析：前 3 秒 hook、痛点、解决方案、使用演示、before/after、CTA、字幕、旁白、claim、视觉风格。
5. 抽取视频中的文字和 claim。
6. 识别哪些镜头可以保留，哪些需要优化。
7. 输出必须是 JSON，不要 Markdown。"""

COUNTRY_EVALUATION_SYSTEM_PROMPT = """你是跨境电商市场研究员、竞品定价分析师、短视频投放顾问和落地页本地化专家。请针对当前产品在指定国家做完整评估。

重要规则：
1. 只评估当前国家。
2. 只评估当前产品。
3. 使用该国家本地语言关键词、英文关键词、产品事实生成的关键词进行网络搜索。
4. 必须调研竞品定价、竞品平台、竞品功能、竞品来源 URL。
5. 涉及市场事实、竞品价格、消费者偏好、法规、平台政策的结论，必须尽量提供 source_url。
6. 找不到可靠来源时，写"未找到可靠来源"，不要猜。
7. 不要编造销量、评论数、运费、汇率、税费、认证。
8. 如果成本、运费、包裹信息缺失，不要做确定性利润判断。
9. 评估当前短视频是否适合该国家带货。
10. 评估当前主图是否适合该国家投放。
11. 给出本地定价策略和运费策略。
12. 给出落地页本地化建议。
13. 输出必须是 JSON，不要 Markdown。"""

JSON_REPAIR_SYSTEM_PROMPT = """你只负责修复 JSON 格式错误。
以下内容本应符合指定 JSON schema，但解析失败。请在不改变业务含义、不新增事实、不删除事实的前提下，把它修复为合法 JSON。
只输出 JSON，不要 Markdown，不要解释。"""


def build_product_fact_prompt(*, input_snapshot: dict[str, Any], countries: list[dict[str, Any]]) -> str:
    return f"""请分析以下产品并输出结构化 JSON。

产品输入：
{json.dumps(input_snapshot, ensure_ascii=False, indent=2)}

目标国家（仅用于生成搜索关键词，不对每个国家做评估）：
{json.dumps(countries, ensure_ascii=False, indent=2)}

请输出产品事实 JSON，包含：
- product_name: 产品名称
- brand: 品牌
- category_detected: 检测到的类目
- subcategory_detected: 子类目
- description_summary: 描述摘要
- key_selling_points: 核心卖点
- features_and_specs: 功能规格
- materials: 材质
- claims: 所有 claim
- claim_consistency_risk: claim 一致性风险（low/medium/high）
- target_audience: 目标受众
- use_cases: 使用场景
- search_keywords_en: 英文搜索关键词
- search_keywords_by_country: 按国家代码的本地语言搜索关键词
- missing_data: 缺失信息列表
- warnings: 警告列表"""


def build_media_understanding_prompt(*, input_snapshot: dict[str, Any], product_facts: dict[str, Any]) -> str:
    return f"""请分析以下产品的主图和短视频素材，输出结构化 JSON。

输入信息：
{json.dumps(input_snapshot, ensure_ascii=False, indent=2)}

已抽取的产品事实：
{json.dumps(product_facts, ensure_ascii=False, indent=2)}

请分析：
1. 主图：产品清晰度、视觉质量、文字内容、claim、本地化风险
2. 短视频：按时间戳分析（前3秒hook、痛点、解决方案、使用演示、before/after、CTA、字幕语言、旁白语言、视觉风格）
3. 抽取视频中的文字和claim
4. 识别保留镜头和需要优化/补拍的镜头

注意：如果视频或图片未提供（input_snapshot 中对应字段为空），在 missing_data 中标注并在 video_analysis 或 main_image_analysis 中说明素材缺失。"""


def build_country_evaluation_prompt(
    *,
    country: dict[str, Any],
    input_snapshot: dict[str, Any],
    product_facts: dict[str, Any],
    media_understanding: dict[str, Any],
) -> str:
    return f"""请针对当前产品在 {country['country_name_zh']}（{country['country_code']}）做完整评估。

=== 目标国家 ===
{json.dumps(country, ensure_ascii=False, indent=2)}

=== 用户输入 ===
{json.dumps(input_snapshot, ensure_ascii=False, indent=2)}

=== 产品事实 ===
{json.dumps(product_facts, ensure_ascii=False, indent=2)}

=== 素材分析 ===
{json.dumps(media_understanding, ensure_ascii=False, indent=2)}

请使用 Google Search 搜索该国家的本地语言和英文关键词，评估以下维度：

1. 市场适配度：该产品在 {country['country_name_zh']} 的需求、定位、目标客群、使用场景、季节性
2. 竞品价格：在 {', '.join(country['marketplaces'])} 等平台的竞品价格、功能对比、评分，必须提供 source URL
3. 定价策略：当前价格换算为 {country['currency']}、推荐售价范围、价格尾数建议
4. 运费策略：推荐运费模式（免邮/固定运费/门槛免邮）
5. 短视频带货适配度：hook 适配、语言适配、文化适配、claim 风险、需保留/替换的镜头、本地化 hook/CTA 方向
6. 主图投放适配度：是否可用、需本地化的内容
7. 落地页本地化：本地化难度、hero 方向、信任元素、需避免的 claim、单位/货币注意事项
8. 风险：claim 风险、合规风险、履约风险、信任风险、本地化风险
9. 30 天测试计划

请对每个评分维度给出 0-100 的得分，并给出最终决策 GO/TEST/HOLD。

输出完整 JSON，不要 Markdown。"""


def build_json_repair_prompt(raw_text: str, error: str) -> str:
    return f"""以下内容本应符合指定 JSON schema，但解析失败。请在不改变业务含义、不新增事实、不删除事实的前提下，把它修复为合法 JSON。

错误信息：
{error}

原始内容：
{raw_text}

只输出 JSON，不要 Markdown，不要解释。"""