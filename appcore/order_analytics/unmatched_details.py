"""Realtime unmatched detail product asset enrichment.

Docs-anchor: docs/superpowers/specs/2026-06-07-realtime-unmatched-mobile-assets-design.md
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable
from urllib.parse import quote

from appcore import llm_client, product_name_dictionary
from appcore.db import query

log = logging.getLogger(__name__)

TRANSLATE_USE_CASE = "order_analytics.unmatched_title_translate"
TRANSLATE_PROVIDER = "openrouter"
TRANSLATE_MODEL = "google/gemini-3.1-flash-lite"

_MAX_TRANSLATE_ITEMS = 50


def enrich_rows(
    rows: list[dict[str, Any]],
    *,
    detail_type: str,
    user_id: int | None = None,
    query_fn: Callable[[str, tuple], list[dict[str, Any]]] | None = None,
    invoke_generate_fn: Callable[..., dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Attach product image/name fields to realtime unmatched detail rows."""
    if not rows:
        return []
    q_fn = query_fn or query
    specs = [_row_spec(row, detail_type=detail_type) for row in rows]
    assets = _lookup_assets(specs, query_fn=q_fn)
    enriched: list[dict[str, Any]] = []
    missing_translation: dict[str, str] = {}

    for row, spec in zip(rows, specs):
        item = dict(row)
        info = _resolve_product_info(spec, assets)
        title = info.get("product_title") or spec["fallback_title"]
        cn_name = info.get("product_cn_name") or ""
        source = info.get("product_title_zh_source") or "none"
        if not cn_name and title and not _contains_cjk(title):
            missing_translation[spec["translation_key"]] = title

        item.update({
            "product_image_url": info.get("product_image_url") or "",
            "product_image_object_key": info.get("product_image_object_key") or "",
            "product_image_local_url": _local_media_url(info.get("product_image_object_key")),
            "product_cn_name": cn_name,
            "product_title": title,
            "product_title_zh_source": source,
            "product_code_hint": spec["code_hint"],
        })
        enriched.append(item)

    translations = _translate_missing_titles(
        missing_translation,
        user_id=user_id,
        invoke_generate_fn=invoke_generate_fn or llm_client.invoke_generate,
    )
    if translations:
        for item, spec in zip(enriched, specs):
            if item.get("product_cn_name"):
                continue
            translated = translations.get(spec["translation_key"])
            if translated:
                item["product_cn_name"] = translated
                item["product_title_zh_source"] = "gemini_3_1_flash_lite"
    return enriched


def _row_spec(row: dict[str, Any], *, detail_type: str) -> dict[str, Any]:
    if detail_type == "ads":
        code_values = _split_values(row.get("normalized_campaign_code"))
        title_values = _split_values(row.get("campaign_name"))
        fallback_title = _first(title_values) or _first(code_values)
    else:
        code_values = _split_values(row.get("skus") or row.get("product_ids"))
        title_values = _split_values(row.get("product_names"))
        fallback_title = _first(title_values) or _first(code_values)
    normalized_codes = [_normalize_code(value) for value in code_values]
    normalized_codes = [value for value in normalized_codes if value]
    titles = [value for value in title_values if value]
    code_hint = _first(normalized_codes) or _first(code_values)
    translation_key = "|".join([detail_type, code_hint or "", fallback_title or ""])
    return {
        "codes": normalized_codes,
        "titles": titles,
        "fallback_title": fallback_title or "",
        "code_hint": code_hint or "",
        "translation_key": translation_key,
    }


def _lookup_assets(
    specs: list[dict[str, Any]],
    *,
    query_fn: Callable[[str, tuple], list[dict[str, Any]]],
) -> dict[str, dict[str, dict[str, Any]]]:
    codes = sorted({code for spec in specs for code in spec["codes"] if code})
    titles = sorted({_normalize_title(title) for spec in specs for title in spec["titles"] if title})
    by_code: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}

    for row in _query_dianxiaomi_assets(codes, titles, query_fn=query_fn):
        info = _info_from_dianxiaomi_asset(row)
        _index_info(info, by_code=by_code, by_title=by_title)

    for row in _query_media_products(codes, titles, query_fn=query_fn):
        info = _info_from_media_product(row)
        _index_info(info, by_code=by_code, by_title=by_title)

    for code, names in product_name_dictionary.get_names(codes, query_fn=query_fn).items():
        if not names:
            continue
        existing = by_code.setdefault(code, {"product_code": code})
        if names.get("cn_name") and not existing.get("product_cn_name"):
            existing["product_cn_name"] = names["cn_name"]
            existing["product_title_zh_source"] = "product_name_dictionary"
        if names.get("en_name") and not existing.get("product_title"):
            existing["product_title"] = names["en_name"]

    return {"by_code": by_code, "by_title": by_title}


def _query_dianxiaomi_assets(
    codes: list[str],
    titles: list[str],
    *,
    query_fn: Callable[[str, tuple], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    where_parts: list[str] = []
    args: list[Any] = []
    if codes:
        placeholders = ",".join(["%s"] * len(codes))
        where_parts.append(f"LOWER(product_code) IN ({placeholders})")
        args.extend(codes)
    if titles:
        placeholders = ",".join(["%s"] * len(titles))
        where_parts.append(
            f"(LOWER(product_name) IN ({placeholders}) OR LOWER(product_english_title) IN ({placeholders}))"
        )
        args.extend(titles)
        args.extend(titles)
    if not where_parts:
        return []
    try:
        return query_fn(
            "SELECT product_code, product_name, product_english_title, product_cn_name, "
            "product_main_image_url, product_main_image_object_key, product_url "
            "FROM dianxiaomi_product_assets "
            "WHERE " + " OR ".join(f"({part})" for part in where_parts),
            tuple(args),
        ) or []
    except Exception:
        log.debug("failed to query dianxiaomi_product_assets", exc_info=True)
        return []


def _query_media_products(
    codes: list[str],
    titles: list[str],
    *,
    query_fn: Callable[[str, tuple], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    where_parts: list[str] = []
    args: list[Any] = []
    if codes:
        placeholders = ",".join(["%s"] * len(codes))
        where_parts.append(f"LOWER(product_code) IN ({placeholders})")
        args.extend(codes)
    if titles:
        placeholders = ",".join(["%s"] * len(titles))
        where_parts.append(f"(LOWER(name) IN ({placeholders}) OR LOWER(shopify_title) IN ({placeholders}))")
        args.extend(titles)
        args.extend(titles)
    if not where_parts:
        return []
    try:
        return query_fn(
            "SELECT product_code, name, shopify_title, main_image, product_link "
            "FROM media_products "
            "WHERE deleted_at IS NULL AND (" + " OR ".join(f"({part})" for part in where_parts) + ")",
            tuple(args),
        ) or []
    except Exception:
        log.debug("failed to query media_products", exc_info=True)
        return []


def _resolve_product_info(
    spec: dict[str, Any],
    assets: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    for code in spec["codes"]:
        info = assets["by_code"].get(code)
        if info:
            return info
    for title in spec["titles"]:
        info = assets["by_title"].get(_normalize_title(title))
        if info:
            return info
    return {
        "product_title": spec["fallback_title"],
        "product_title_zh_source": "source_title" if _contains_cjk(spec["fallback_title"]) else "none",
    }


def _info_from_dianxiaomi_asset(row: dict[str, Any]) -> dict[str, Any]:
    cn_name = str(row.get("product_cn_name") or "").strip()
    title = _first([
        str(row.get("product_english_title") or "").strip(),
        str(row.get("product_name") or "").strip(),
    ])
    return {
        "product_code": _normalize_code(row.get("product_code")),
        "product_title": title,
        "product_cn_name": cn_name,
        "product_title_zh_source": "dianxiaomi_product_assets" if cn_name else "none",
        "product_image_url": str(row.get("product_main_image_url") or "").strip(),
        "product_image_object_key": str(row.get("product_main_image_object_key") or "").strip(),
    }


def _info_from_media_product(row: dict[str, Any]) -> dict[str, Any]:
    name = str(row.get("name") or "").strip()
    title = _first([
        str(row.get("shopify_title") or "").strip(),
        name,
    ])
    cn_name = name if _contains_cjk(name) else ""
    return {
        "product_code": _normalize_code(row.get("product_code")),
        "product_title": title,
        "product_cn_name": cn_name,
        "product_title_zh_source": "media_products" if cn_name else "none",
        "product_image_url": str(row.get("main_image") or "").strip(),
        "product_image_object_key": "",
    }


def _index_info(
    info: dict[str, Any],
    *,
    by_code: dict[str, dict[str, Any]],
    by_title: dict[str, dict[str, Any]],
) -> None:
    code = info.get("product_code") or ""
    if code and code not in by_code:
        by_code[code] = info
    title = _normalize_title(info.get("product_title") or "")
    if title and title not in by_title:
        by_title[title] = info


def _translate_missing_titles(
    items: dict[str, str],
    *,
    user_id: int | None,
    invoke_generate_fn: Callable[..., dict[str, Any]],
) -> dict[str, str]:
    key_to_normalized_title: dict[str, str] = {}
    request_id_to_normalized_title: dict[str, str] = {}
    deduped: dict[str, str] = {}
    for key, title in items.items():
        normalized = _normalize_title(title)
        if not normalized:
            continue
        key_to_normalized_title[key] = normalized
        if normalized in request_id_to_normalized_title:
            continue
        if len(deduped) < _MAX_TRANSLATE_ITEMS:
            deduped[key] = title
            request_id_to_normalized_title[key] = normalized
    if not deduped:
        return {}
    request_items = [
        {"id": key, "title": title}
        for key, title in deduped.items()
    ]
    try:
        response = invoke_generate_fn(
            TRANSLATE_USE_CASE,
            prompt=_build_translate_prompt(request_items),
            response_schema=_translate_response_schema(),
            user_id=user_id,
            temperature=0.0,
            max_output_tokens=2048,
            provider_override=TRANSLATE_PROVIDER,
            model_override=TRANSLATE_MODEL,
            billing_extra={"source": "realtime_unmatched_detail_title"},
            timeout_seconds=30,
        )
    except Exception as exc:
        log.warning("failed to translate realtime unmatched product titles: %s", exc)
        return {}
    payload = response.get("json")
    if not isinstance(payload, dict):
        payload = _parse_json_object(response.get("text") or "")
    translations_by_normalized_title: dict[str, str] = {}
    for row in (payload.get("translations") or []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("id") or "").strip()
        value = _clean_translation(row.get("zh") or row.get("translation") or "")
        normalized = request_id_to_normalized_title.get(key)
        if normalized and value:
            translations_by_normalized_title[normalized] = value
    return {
        key: translations_by_normalized_title[normalized]
        for key, normalized in key_to_normalized_title.items()
        if normalized in translations_by_normalized_title
    }


def _build_translate_prompt(items: list[dict[str, str]]) -> str:
    return (
        "你是跨境电商商品标题翻译助手。把商品英文标题翻译成自然、准确、简洁的简体中文。"
        "保留品牌名、型号、规格、数量、材质和颜色，不要扩写卖点。"
        "只返回 JSON，不要 Markdown。\n\n"
        "输入 JSON：\n"
        + json.dumps({"items": items}, ensure_ascii=False)
    )


def _translate_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "zh": {"type": "string"},
                    },
                    "required": ["id", "zh"],
                },
            }
        },
        "required": ["translations"],
    }


def _parse_json_object(text: str) -> dict[str, Any]:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    start = value.find("{")
    end = value.rfind("}")
    if start >= 0 and end >= start:
        value = value[start : end + 1]
    try:
        payload = json.loads(value)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _split_values(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"\s*/\s*|\s*[，,]\s*|\n+", text)
    out: list[str] = []
    for part in parts:
        cleaned = part.strip()
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"\s+", "-", text)
    text = text.strip("-_")
    if text.endswith("-rjc") or text.endswith("_rjc"):
        text = text[:-4]
    return text.strip("-_")


def _normalize_title(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _contains_cjk(text: Any) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in str(text or ""))


def _clean_translation(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    return text.strip("`'\"“”‘’ ")


def _local_media_url(object_key: Any) -> str:
    key = str(object_key or "").strip()
    if not key:
        return ""
    return "/medias/object?object_key=" + quote(key, safe="")


def _first(values: Any) -> str:
    if isinstance(values, list):
        for value in values:
            text = str(value or "").strip()
            if text:
                return text
        return ""
    return str(values or "").strip()
