"""Prompt builders for single-product five-country AI evaluation.

Docs-anchor:
docs/superpowers/specs/2026-05-22-single-product-five-country-ai-evaluation-design.md
"""

from __future__ import annotations

from datetime import datetime
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
    "你是跨境电商市场研究员、广告素材审计专家和文化审美分析专家。\n"
    "当前任务不调用搜索工具；找不到可靠来源时写“未找到可靠来源”，不要猜。只输出符合 schema 的 JSON，不输出 Markdown。\n"
    "【重要国别评估准则：差异化与真实研判】\n"
    "1. 严禁不同国家的分数雷同或完全一致！你必须根据目标国家的消费文化、生活习惯、品类竞争激烈度以及法规限制，进行差异化的独立打分。禁止为了偷懒而无脑给所有国家打相同的默认高分（如 88 分）。\n"
    "2. 严禁不同国家使用相似的理由、句式或套路模板！严禁只是机械性替换国名、语言或季节词汇。必须深入探讨该国消费者的独特性。\n"
    "3. 必须结合目标国家的真实本土化人文与市场特征进行深刻研判：\n"
    "   - 德国(DE)：严谨、追求实用性和高品质，看重环保标识和材质安全认证（如CE/GS），消费偏理性保守。\n"
    "   - 法国(FR)：审美艺术感和设计要求高，看重情感连接，反感低质英美粗暴买量风格，重视法式优雅。\n"
    "   - 意大利(IT)：极重家族陪伴和家庭温情，审美水平高，但电商渗透相对保守，高单价决策慢，偏好感性共鸣。\n"
    "   - 西班牙(ES)：户外、出行和社交极度频繁，偏爱高性价比、鲜艳、便携的商品，客单价承受力低于德法。\n"
    "   - 日本(JP)：居住空间极度狭小，极度关注“静音”、“小巧/易折叠收纳”、“绝对无异味/无毒/安全”。视宠物如子嗣，追求极简或极致卡哇伊（Cute），对低质廉价塑料感和粗糙包装极其零容忍。\n"
    "   - 美国(US)：消费能力极强，但广告与产品买量竞争是极致红海。极度重视退换货便利性、包装质感、物流时效（如FBA/Prime）。对“BPA-free/安全无害”等产品宣称敏感。\n"
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
    eval_date_text = datetime.now().strftime("%Y-%m-%d")
    return (
        "请针对当前单个产品，在指定国家做一次完整 AI 评估。\n\n"
        "核心评估维度（按重要性排序）：\n"
        "A. 素材适配度：视频内容是否精准展示产品卖点、功能演示是否到位、镜头语言是否有吸引力。\n"
        "B. 审美适配：素材画面质感、色彩搭配、视觉风格是否符合目标国家消费者审美偏好。\n"
        "C. 产品需求：目标国家消费者是否有真实需求、品类热度、搜索趋势。\n"
        "D. 文化适配：是否存在文化冲突、禁忌、敏感内容、手势/颜色/符号在当地的含义。\n"
        "E. 合规风险：产品是否涉及当地法规禁令、安全标准、广告政策限制。\n\n"
        "重要规则：\n"
        "1. 只评估当前 product_id。\n"
        "2. 只评估当前 country_code。\n"
        "3. 不要分析其他国家或其他产品。\n"
        "4. 不要分析采购成本、物流成本、运费、税费、目标 ROAS、目标 CPA — 这些由运营团队解决。\n"
        "5. 不要分析落地页翻译、本地化翻译质量 — 视频翻译和链接本地化适配由运营团队处理。\n"
        "6. 不要因为素材语言是英文/中文就扣分 — 素材会被翻译为目标语言。\n"
        "7. 当前暂不调用搜索工具；只能基于输入、URL Context 可确认的信息和素材信息评估。\n"
        "8. 市场事实、竞品、法规、广告政策、消费者偏好的结论必须尽量提供 source_url。\n"
        "9. 找不到可靠来源时，写'未找到可靠来源'，不要猜。\n"
        "10. 如果有图片和视频素材，请从素材质量、表现力、审美、文化适配角度审计。\n"
        "11. 如果有视频，必须按 timestamp 输出 video_audit.timestamp_findings。\n"
        "12. 如果没有素材，creative_missing = true。\n"
        "13. 检查素材里的 claim、SKU、规格与产品事实是否一致。\n"
        "14. 输出必须是 JSON，必须符合 schema，不要 Markdown。\n\n"
        "【真实且具有显著差异的国别研判硬规则】\n"
        f"1. 当前评估日期：{eval_date_text}。所有季节、气候和消费场景判断必须严格以该日期为准。必须明确分析当前季节对该国商品需求的影响（例如：西欧5月底白昼漫长且户外活动爆发；日本5月正值梅雨季前期且室内潮湿防霉静音玩具是热点）。\n"
        "2. 各项评分（scores）必须呈现符合真实情况的差异与波动，综合分 overall_score 绝不能在不同国家完全相同！禁止敷衍性地在所有国家都打出默认高分（如 88）。如果某国竞争极度激烈（如美国）或该国消费者由于生活习惯（如日本极小户型）对该产品并不适合，必须坚决打低分（50-70），并将决策（decision.final_decision）定为 HOLD 或 TEST。只有确实完美无暇、低竞争高潜力的市场才允许打高分（85分以上）并判断为 GO。\n"
        "3. 详细说明（detailed_explanation）、why、风险（risks）和下一步建议（recommendations）的内容必须深度切合当前国家的文化、住宅环境（如日本的公寓极小户型对比美国的独栋大别墅）、消费心理和具体法规（如 CE、PSE 认证、GDPR），严禁在不同国家之间使用相似或重复的句式，严禁仅做国名/语言词汇的机械性替换！\n\n"
        f"{COUNTRY_EVALUATION_CHINESE_OUTPUT_RULES}\n"
        "需要完成：市场适配、竞品分析、素材适配、审美适配、文化适配、风险、结论、下一步建议。\n\n"
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
