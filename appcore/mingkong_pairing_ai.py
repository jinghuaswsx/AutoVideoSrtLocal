"""AI-assisted review for Mingkong SKU pairing candidates."""

from __future__ import annotations

import json
from typing import Any

from appcore import llm_client


USE_CASE_CODE = "mingkong_pairing.match_candidate"


REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_same_product": {"type": "boolean"},
        "confidence": {"type": "number"},
        "recommended_candidate_key": {"type": "string"},
        "requires_manual_review": {"type": "boolean"},
        "reason": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "variant_mapping_notes": {"type": "string"},
        "candidate_rankings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_key": {"type": "string"},
                    "score": {"type": "number"},
                    "reason": {"type": "string"},
                    "matched_sku_count": {"type": "integer"},
                    "risks": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["candidate_key", "score", "reason"],
                "additionalProperties": True,
            },
        },
        "variant_mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "shopify_variant_id": {"type": "string"},
                    "variant_title": {"type": "string"},
                    "recommended_sku": {"type": "string"},
                    "recommended_sku_code": {"type": "string"},
                    "recommended_name": {"type": "string"},
                    "confidence": {"type": "number"},
                    "reason": {"type": "string"},
                },
                "required": ["shopify_variant_id", "recommended_sku"],
                "additionalProperties": True,
            },
        },
    },
    "required": [
        "is_same_product",
        "confidence",
        "recommended_candidate_key",
        "requires_manual_review",
        "reason",
        "risks",
        "variant_mapping_notes",
        "candidate_rankings",
        "variant_mappings",
    ],
    "additionalProperties": True,
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _candidate_key(item: dict[str, Any]) -> str:
    mingkong = item.get("mingkong") or {}
    return (
        _clean(mingkong.get("shopify_product_id"))
        or _clean(item.get("shopify_product_id"))
        or _clean(mingkong.get("source"))
        or "unknown"
    )


def _compact_item(item: dict[str, Any]) -> dict[str, Any]:
    mingkong = item.get("mingkong") or {}
    dxm03 = item.get("dxm03") or {}
    pairing = dxm03.get("pairing") or {}
    return {
        "candidate_key": _candidate_key(item),
        "shopify_product_id": _clean(mingkong.get("shopify_product_id") or item.get("shopify_product_id")),
        "shopify_variant_id": _clean(item.get("shopify_variant_id")),
        "variant_title": _clean(item.get("shopify_variant_title") or item.get("variant_title") or mingkong.get("variant_title")),
        "mingkong_sku": _clean(mingkong.get("sku") or item.get("dianxiaomi_sku")),
        "mingkong_sku_code": _clean(mingkong.get("sku_code") or item.get("dianxiaomi_sku_code")),
        "mingkong_name": _clean(mingkong.get("name") or item.get("dianxiaomi_name")),
        "supplier_name": _clean(mingkong.get("supplier_name")),
        "purchase_1688_url": _clean(mingkong.get("purchase_1688_url") or item.get("purchase_1688_url")),
        "alibaba_product_id": _clean(mingkong.get("alibaba_product_id") or item.get("alibaba_product_id")),
        "sku_id_alibaba": _clean(mingkong.get("sku_id_alibaba") or pairing.get("sku_id_alibaba")),
        "image_url": _clean(mingkong.get("image_url") or item.get("image_url")),
        "status": _clean(item.get("status")),
        "is_combo": bool(item.get("is_combo") or mingkong.get("is_combo")),
    }


def _group_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in items[:80]:
        compact = _compact_item(item)
        key = compact["candidate_key"]
        group = grouped.setdefault(key, {
            "candidate_key": key,
            "shopify_product_id": compact["shopify_product_id"],
            "purchase_1688_url": compact["purchase_1688_url"],
            "supplier_name": compact["supplier_name"],
            "variants": [],
        })
        if not group.get("purchase_1688_url"):
            group["purchase_1688_url"] = compact["purchase_1688_url"]
        if not group.get("supplier_name"):
            group["supplier_name"] = compact["supplier_name"]
        group["variants"].append(compact)
    return list(grouped.values())


def _image_urls(
    product: dict[str, Any],
    candidates: list[dict[str, Any]],
    fuzzy_candidates: list[dict[str, Any]] = None,
) -> list[str]:
    urls: list[str] = []
    for value in (
        product.get("main_image"),
        product.get("cover_url"),
        product.get("product_image"),
        product.get("image_url"),
    ):
        text = _clean(value)
        if text and text.startswith(("http://", "https://")):
            urls.append(text)
    for candidate in candidates:
        for variant in candidate.get("variants") or []:
            text = _clean(variant.get("image_url"))
            if text and text.startswith(("http://", "https://")) and text not in urls:
                urls.append(text)
    if fuzzy_candidates:
        for cand in fuzzy_candidates:
            text = _clean(cand.get("image_url"))
            if text and text.startswith(("http://", "https://")) and text not in urls:
                urls.append(text)
    return urls[:15]


def _prompt(
    product: dict[str, Any],
    candidates: list[dict[str, Any]],
    fuzzy_candidates: list[dict[str, Any]] = None,
) -> str:
    payload = {
        "our_product": {
            "id": product.get("id"),
            "product_code": _clean(product.get("product_code")),
            "name": _clean(product.get("name")),
            "shopify_title": _clean(product.get("shopify_title")),
            "product_link": _clean(product.get("product_link")),
            "purchase_1688_url": _clean(product.get("purchase_1688_url")),
            "shopifyid": _clean(product.get("shopifyid")),
        },
        "mingkong_candidates": candidates,
    }
    if fuzzy_candidates:
        payload["fuzzy_candidates"] = fuzzy_candidates

    prompt = (
        "你是电商 SKU 采购配对审核助手。请判断明空候选商品是否与我们新品为同一个商品，"
        "并给出最可信候选。只能基于标题、图片、SKU 变体、采购链接、供应商和 1688 规格判断；"
        "不要编造字段，不要输出 DXM03 写入参数。\n"
        "如果多个候选只是不同店铺/不同 Shopify 商品 ID 下复用同一批店小秘 SKU，请指出重复关系，"
        "推荐采购配对最完整的一组，并说明最终仍需要人工确认。\n"
        "\n【重要要求】\n"
        "为了方便用户阅读，所有的分析结果、理由、原因、风险和备注（包括 JSON schema 中的 `reason`、`risks`、`variant_mapping_notes` 以及子项中的 `reason`、`risks`）必须全部使用简体中文编写。即使输入数据中有英文，也必须在分析后用简体中文撰写理由和说明，绝对不能使用英文输出！\n"
    )
    
    if fuzzy_candidates:
        prompt += (
            "\n特别任务：检测到有未配对的模糊匹配明空 ERP SKU 列表（`fuzzy_candidates`）。\n"
            "请仔细对比我们 Shopify 商品的各个变体规格（例如：'1 launcher + 3 rockets'）与 `fuzzy_candidates` 中的 ERP SKU（例如：'发射底座+3个火箭'）。\n"
            "分析中英文数量、颜色、特征，并在 `variant_mappings` 中给出每个 Shopify 变体对应的推荐明空 ERP SKU（`recommended_sku`）。\n"
            "对于没匹配上的变体，请设置 `recommended_sku` 为空字符串。每一项必须包含 `shopify_variant_id` 和 `recommended_sku`，即使没有 100% 确定，也请根据数量/规格相似度给出最有可能的匹配项，并设置适当的置信度和理由（理由也必须使用简体中文）。\n"
        )
        
    prompt += (
        "\n请严格输出符合 JSON schema 的对象。\n\n"
        f"输入数据：\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )
    return prompt


def review_pairing_candidates(
    product: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    user_id: int | None = None,
    invoke_chat_fn=None,
) -> dict[str, Any]:
    # Extract fuzzy candidates if available in the first item's list
    fuzzy_candidates = []
    if items and isinstance(items[0], dict):
        fuzzy_candidates = items[0].get("fuzzy_candidates") or []

    candidates = _group_candidates([item for item in items or [] if isinstance(item, dict)])
    if not candidates and not fuzzy_candidates:
        return {
            "ok": False,
            "error": "missing_candidates",
            "message": "没有可供 AI 判断的明空候选 SKU",
            "review": None,
            "logs": [{"level": "warn", "message": "没有可供 AI 判断的明空候选 SKU"}],
        }

    content: list[dict[str, Any]] = [{"type": "text", "text": _prompt(product, candidates, fuzzy_candidates)}]
    for url in _image_urls(product, candidates, fuzzy_candidates):
        content.append({"type": "image_url", "image_url": {"url": url}})

    invoke = invoke_chat_fn or llm_client.invoke_chat
    response = invoke(
        USE_CASE_CODE,
        messages=[
            {
                "role": "system",
                "content": "你只做候选匹配审核，输出 JSON，不执行任何写入动作。",
            },
            {"role": "user", "content": content},
        ],
        user_id=user_id,
        temperature=0.1,
        max_tokens=1600,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "mingkong_pairing_match_candidate",
                "schema": REVIEW_RESPONSE_SCHEMA,
            },
        },
        timeout_seconds=90,
    )
    review = response.get("json")
    if not isinstance(review, dict):
        text = _clean(response.get("text"))
        try:
            review = json.loads(text) if text else {}
        except json.JSONDecodeError:
            review = {}
    confidence = review.get("confidence") if isinstance(review, dict) else None
    requires_review = bool((review or {}).get("requires_manual_review", True))
    level = "warn" if requires_review or float(confidence or 0) < 0.85 else "ok"
    return {
        "ok": True,
        "message": "AI 辅助判断完成",
        "review": review,
        "usage_log_id": response.get("usage_log_id"),
        "logs": [
            {
                "level": level,
                "message": (
                    f"AI 建议候选 {(review or {}).get('recommended_candidate_key') or '—'}，"
                    f"置信度 {confidence if confidence is not None else '—'}；"
                    f"{'需要人工复核' if requires_review else '可作为默认候选'}"
                ),
            }
        ],
    }
