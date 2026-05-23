"""Prompt builders for single-product five-country AI evaluation.

Docs-anchor:
docs/superpowers/specs/2026-05-22-single-product-five-country-ai-evaluation-design.md
"""

from __future__ import annotations

import json
from typing import Any


CHINESE_OUTPUT_RULES = (
    "中文输出硬规则：\n"
    "1. 所有面向运营阅读的字符串值必须使用简体中文。\n"
    "2. 字段名、国家代码、固定枚举值、货币代码、URL/source_url、时间戳、文件路径、ID 按 schema 和输入原样保留。\n"
    "3. 不要把结论、原因、建议、风险、素材审计、落地页建议写成英文、目标国家语言或拼音。\n"
)

PRODUCT_FACT_CHINESE_OUTPUT_RULES = (
    CHINESE_OUTPUT_RULES
    + "4. generated_search_keywords.english_keywords 字段名保持不变，但字段值也输出中文关键词。\n"
)

COUNTRY_EVALUATION_CHINESE_OUTPUT_RULES = (
    CHINESE_OUTPUT_RULES
    + "4. country_name 和 country_name_zh 都输出中文国家名。\n"
)


PRODUCT_FACT_SYSTEM_PROMPT = (
    "你是跨境电商产品事实整理专家。只输出符合 schema 的 JSON，不输出 Markdown。\n"
    f"{CHINESE_OUTPUT_RULES}"
)

COUNTRY_EVALUATION_SYSTEM_PROMPT = (
    "你是跨境电商市场研究员、广告素材审计专家和落地页本土化专家。"
    "当前任务不调用搜索工具；找不到可靠来源时写未找到可靠来源，不要猜。"
    "只输出符合 schema 的 JSON，不输出 Markdown。\n"
    f"{CHINESE_OUTPUT_RULES}"
)

JSON_REPAIR_SYSTEM_PROMPT = (
    "你是 JSON 修复器。只根据输入的原始响应修复为符合 schema 的 JSON。"
    "不要补充新事实，不要调用工具，不要输出 Markdown。\n"
    f"{CHINESE_OUTPUT_RULES}"
)


def build_product_fact_prompt(*, product_snapshot: dict[str, Any], countries: list[dict[str, Any]]) -> str:
    payload = {
        "product_snapshot": product_snapshot,
        "countries": countries,
    }
    return (
        "请基于当前单个产品的产品快照，输出结构化 JSON。\n\n"
        "重要规则：\n"
        "1. 这是通用产品任务，不要假设固定类目。\n"
        "2. 不要使用任何硬编码产品信息。\n"
        "3. 只基于输入的 product_snapshot、product_url、landing_page_url、素材 metadata。\n"
        "4. 如果信息缺失，写入 missing_data。\n"
        "5. 不要编造价格、销量、评价数、库存、材质、尺寸、认证、功效、配送时效。\n"
        "6. 抽取所有 claim，并检查 claim consistency risk。\n"
        "7. 根据产品事实生成中文关键词和五个国家的中文关键词提示，用于后续国家评估。\n"
        "8. 输出必须是 JSON，必须符合 schema，不要 Markdown。\n\n"
        f"{PRODUCT_FACT_CHINESE_OUTPUT_RULES}\n"
        "输入：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def build_country_evaluation_prompt(
    *,
    product_snapshot: dict[str, Any],
    product_facts: dict[str, Any],
    country: dict[str, Any],
    asset_snapshot: dict[str, Any],
) -> str:
    payload = {
        "product_snapshot": product_snapshot,
        "product_facts": product_facts,
        "country": country,
        "asset_snapshot": asset_snapshot,
    }
    return (
        "请针对当前单个产品，在指定国家做一次完整 AI 评估。\n\n"
        "重要规则：\n"
        "1. 只评估当前 product_id。\n"
        "2. 只评估当前 country_code。\n"
        "3. 不要分析其他国家。\n"
        "4. 不要分析其他产品。\n"
        "5. 不要硬编码任何产品信息。\n"
        "6. 当前暂不调用搜索工具；只能基于输入、URL Context 可确认的信息和素材信息评估。\n"
        "7. 市场事实、竞品、价格、法规、广告政策、消费者偏好的结论必须尽量提供 source_url。\n"
        "8. 找不到可靠来源时，写“未找到可靠来源”，不要猜。\n"
        "9. 成本、运费、税费、目标 ROAS、目标 CPA 缺失时，不要做确定性利润判断。\n"
        "10. 如果有图片和视频素材，请审计素材是否适合当地投放。\n"
        "11. 如果有视频，必须按 timestamp 输出 video_audit.timestamp_findings。\n"
        "12. 如果没有素材，creative_missing = true。\n"
        "13. 检查素材里的文字、claim、SKU、价格、规格与产品事实是否一致。\n"
        "14. 输出必须是 JSON，必须符合 schema，不要 Markdown。\n\n"
        f"{COUNTRY_EVALUATION_CHINESE_OUTPUT_RULES}\n"
        "需要完成：市场适配、竞品分析、价格适配、素材适配、落地页本土化、风险、结论、30 天测试计划。\n\n"
        "输入：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )


def build_json_repair_prompt(*, raw_response: str, parse_error: str) -> str:
    return (
        "请修复下面这段大模型原始响应，使其成为一个合法 JSON object，并保持原始字段含义。\n"
        "要求：\n"
        "1. 只输出 JSON object，不输出解释或 Markdown。\n"
        "2. 不要新增原始响应中没有依据的事实。\n"
        "3. 如果某个字段无法修复，使用空字符串、空数组或 null，保持 schema 结构。\n\n"
        f"{CHINESE_OUTPUT_RULES}\n"
        f"解析错误：{parse_error}\n\n"
        "原始响应：\n"
        f"{raw_response}"
    )
