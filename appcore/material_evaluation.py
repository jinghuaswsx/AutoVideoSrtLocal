from __future__ import annotations

import json
import logging
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from appcore import llm_client, local_media_storage, medias, pushes, tos_clients
from appcore.db import query

logger = logging.getLogger(__name__)

USE_CASE_CODE = "material_evaluation.evaluate"
_ACTIVE_PRODUCT_IDS: set[int] = set()
_ACTIVE_LOCK = threading.Lock()


def _normalize_languages(languages: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in languages or []:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip().lower()
            name = str(item.get("name") or item.get("name_zh") or code).strip()
        else:
            code = str((item or ["", ""])[0]).strip().lower()
            name = str((item or ["", ""])[1] if len(item) > 1 else code).strip()
        if not code or code == "en" or code in seen:
            continue
        seen.add(code)
        normalized.append({"code": code, "name": name or code})
    return normalized


def build_response_schema(languages: list[Any]) -> dict:
    langs = _normalize_languages(languages)
    lang_codes = [item["code"] for item in langs]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["countries"],
        "properties": {
            "countries": {
                "type": "array",
                "minItems": len(lang_codes),
                "maxItems": len(lang_codes),
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "lang",
                        "country",
                        "is_suitable",
                        "score",
                        "risk_level",
                        "decision",
                        "reason",
                        "suggestions",
                    ],
                    "properties": {
                        "lang": {"type": "string", "enum": lang_codes},
                        "country": {"type": "string"},
                        "is_suitable": {"type": "boolean"},
                        "score": {"type": "number", "minimum": 0, "maximum": 100},
                        "risk_level": {
                            "type": "string",
                            "enum": ["low", "medium", "high"],
                        },
                        "decision": {
                            "type": "string",
                            "enum": ["适合推广", "谨慎推广", "不适合推广"],
                        },
                        "reason": {"type": "string", "maxLength": 100},
                        "suggestions": {
                            "type": "array",
                            "maxItems": 3,
                            "items": {"type": "string"},
                        },
                    },
                },
            },
        },
    }


def build_system_prompt() -> str:
    return (
        "你是跨境电商欧洲市场选品评估专家，熟悉欧盟消费文化、广告合规、"
        "平台短视频转化和小语种本地化风险。请只输出符合 schema 的 JSON，"
        "不要输出 Markdown。"
    )


def build_prompt(product: dict, product_url: str, languages: list[Any]) -> str:
    langs = _normalize_languages(languages)
    lang_text = "、".join(f"{item['name']}({item['code']})" for item in langs)
    product_name = str(product.get("name") or "").strip() or "未命名商品"
    product_code = str(product.get("product_code") or "").strip() or "无"
    return f"""请基于随消息附上的两个素材和商品链接，评估该产品是否适合在欧洲市场的小语种国家推广。

输入素材顺序：
1. 商品主图：判断品类、外观、卖点、潜在合规风险。
2. 推广视频：取系统中该商品第一条英语视频素材，判断短视频内容、使用场景、口播/画面表达是否适合本地化推广。

商品信息：
- 商品名称：{product_name}
- 商品编码：{product_code}
- 商品链接：{product_url}

需要覆盖的小语种国家/语种：{lang_text}

请逐一判断每个国家/语种的推广适配度，重点考虑：
- 欧洲消费者是否有明确需求和购买场景。
- 商品主图与视频卖点是否清晰、可信、容易本地化。
- 是否存在医疗、功效夸大、安全、儿童、隐私、环保等广告合规风险。
- 价格敏感度、季节性、文化接受度、视频内容与当地审美是否匹配。

输出要求：
- 必须覆盖上面列出的每一个语种，不能遗漏或新增。
- 每个语种返回结构化 JSON 字段：lang、country、is_suitable、score、risk_level、decision、reason、suggestions。
- reason 必须是中文，100 字以内。
- score 为 0-100，越高代表越适合推广。
"""


def normalize_result(raw: dict | str, languages: list[Any]) -> dict:
    if isinstance(raw, str):
        raw = json.loads(raw)
    langs = _normalize_languages(languages)
    expected_codes = [item["code"] for item in langs]
    rows = raw.get("countries") if isinstance(raw, dict) else None
    if isinstance(rows, dict):
        rows = list(rows.values())
    if not isinstance(rows, list):
        raise ValueError("material evaluation result missing countries array")

    by_lang: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("lang") or "").strip().lower()
        if code in expected_codes and code not in by_lang:
            by_lang[code] = row

    missing = [code for code in expected_codes if code not in by_lang]
    if missing:
        raise ValueError(f"material evaluation missing languages: {', '.join(missing)}")

    countries: list[dict[str, Any]] = []
    for lang in langs:
        row = by_lang[lang["code"]]
        score = _coerce_score(row.get("score"))
        is_suitable = bool(row.get("is_suitable"))
        decision = str(row.get("decision") or "").strip()
        if decision not in {"适合推广", "谨慎推广", "不适合推广"}:
            decision = _decision_from_score(score, is_suitable)
        risk_level = str(row.get("risk_level") or "").strip().lower()
        if risk_level not in {"low", "medium", "high"}:
            risk_level = "medium"
        reason = str(row.get("reason") or "模型未提供明确判断依据。").strip()[:100]
        suggestions = row.get("suggestions") or []
        if not isinstance(suggestions, list):
            suggestions = [str(suggestions)]
        countries.append({
            "lang": lang["code"],
            "language": lang["name"],
            "country": str(row.get("country") or lang["name"]).strip(),
            "is_suitable": is_suitable,
            "score": score,
            "risk_level": risk_level,
            "decision": decision,
            "reason": reason,
            "suggestions": [str(item).strip() for item in suggestions if str(item).strip()][:3],
        })

    scores = [row["score"] for row in countries]
    avg_score = round(sum(scores) / len(scores), 1) if scores else None
    suitable_count = sum(1 for row in countries if row["is_suitable"])
    if suitable_count == len(countries) and countries:
        evaluation_result = "适合推广"
    elif suitable_count > 0:
        evaluation_result = "部分适合推广"
    else:
        evaluation_result = "不适合推广"
    listing_status = "上架" if suitable_count > 0 else "下架"
    return {
        "countries": countries,
        "ai_score": avg_score,
        "ai_evaluation_result": evaluation_result,
        "listing_status": listing_status,
    }


def _coerce_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(100.0, score))


def _decision_from_score(score: float, is_suitable: bool) -> str:
    if is_suitable and score >= 70:
        return "适合推广"
    if score >= 50:
        return "谨慎推广"
    return "不适合推广"


def find_ready_product_ids(limit: int = 5) -> list[int]:
    try:
        rows = query(
            "SELECT p.id FROM media_products p "
            "LEFT JOIN media_product_covers c ON c.product_id=p.id AND c.lang='en' "
            "WHERE p.deleted_at IS NULL "
            " AND COALESCE(p.archived, 0)=0 "
            " AND (p.ai_evaluation_result IS NULL OR p.ai_evaluation_result='') "
            " AND (COALESCE(p.product_code, '')<>'' OR COALESCE(p.localized_links_json, '')<>'') "
            " AND (c.object_key IS NOT NULL OR COALESCE(p.cover_object_key, '')<>'') "
            " AND EXISTS ("
            "   SELECT 1 FROM media_items i "
            "   WHERE i.product_id=p.id AND i.lang='en' AND i.deleted_at IS NULL"
            " ) "
            "ORDER BY p.updated_at ASC, p.id ASC LIMIT %s",
            (int(limit),),
        )
    except Exception:
        logger.exception("material evaluation ready-product scan failed")
        return []
    return [int(row["id"]) for row in rows]


def evaluate_product_if_ready(product_id: int, *, force: bool = False) -> dict:
    pid = int(product_id)
    if not _enter_product(pid):
        return {"status": "running", "product_id": pid}
    try:
        return _evaluate_product_if_ready(pid, force=force)
    finally:
        _leave_product(pid)


def _enter_product(product_id: int) -> bool:
    with _ACTIVE_LOCK:
        if product_id in _ACTIVE_PRODUCT_IDS:
            return False
        _ACTIVE_PRODUCT_IDS.add(product_id)
        return True


def _leave_product(product_id: int) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE_PRODUCT_IDS.discard(product_id)


def _evaluate_product_if_ready(product_id: int, *, force: bool = False) -> dict:
    product = medias.get_product(product_id)
    if not product:
        return {"status": "product_missing", "product_id": product_id}
    if not force and str(product.get("ai_evaluation_result") or "").strip():
        return {"status": "already_evaluated", "product_id": product_id}

    languages = _normalize_languages(medias.list_enabled_languages_kv())
    if not languages:
        return {"status": "missing_languages", "product_id": product_id}

    product_url = pushes.resolve_product_page_url("en", product)
    if not product_url:
        return {"status": "missing_product_link", "product_id": product_id}

    cover_key = _resolve_product_cover_key(product_id, product)
    if not cover_key:
        return {"status": "missing_cover", "product_id": product_id}

    video = _first_english_video(product_id)
    if not video:
        return {"status": "missing_video", "product_id": product_id}

    cover_path = _materialize_media(cover_key)
    video_path = _materialize_media(video["object_key"])
    prompt = build_prompt(product, product_url, languages)
    llm_result = llm_client.invoke_generate(
        USE_CASE_CODE,
        prompt=prompt,
        system=build_system_prompt(),
        media=[cover_path, video_path],
        user_id=product.get("user_id"),
        project_id=f"media-product-{product_id}",
        response_schema=build_response_schema(languages),
        temperature=0.2,
        max_output_tokens=4096,
    )
    raw_json = llm_result.get("json")
    if raw_json is None:
        raw_json = llm_result.get("text") or "{}"
    normalized = normalize_result(raw_json, languages)
    detail = {
        "schema_version": 1,
        "use_case": USE_CASE_CODE,
        "evaluated_at": datetime.now(UTC).isoformat(),
        "product_id": product_id,
        "product_url": product_url,
        "cover_object_key": cover_key,
        "video_item_id": video.get("id"),
        "video_object_key": video.get("object_key"),
        "countries": normalized["countries"],
    }
    medias.update_product(
        product_id,
        ai_score=normalized["ai_score"],
        ai_evaluation_result=normalized["ai_evaluation_result"],
        ai_evaluation_detail=json.dumps(detail, ensure_ascii=False),
        listing_status=normalized["listing_status"],
    )
    return {
        "status": "evaluated",
        "product_id": product_id,
        "ai_score": normalized["ai_score"],
        "ai_evaluation_result": normalized["ai_evaluation_result"],
        "listing_status": normalized["listing_status"],
    }


def _resolve_product_cover_key(product_id: int, product: dict) -> str:
    try:
        cover_key = medias.resolve_cover(product_id, "en")
    except Exception:
        cover_key = None
    return str(cover_key or product.get("cover_object_key") or "").strip()


def _first_english_video(product_id: int) -> dict | None:
    for item in medias.list_items(product_id, "en"):
        object_key = str(item.get("object_key") or "").strip()
        if object_key:
            return {**item, "object_key": object_key}
    return None


def _materialize_media(object_key: str) -> Path:
    key = str(object_key or "").strip()
    if not key:
        raise ValueError("object_key required")
    if local_media_storage.exists(key):
        return local_media_storage.local_path_for(key)
    local_path = local_media_storage.local_path_for(key)
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tos_clients.download_media_file(key, str(local_path))
        if local_path.is_file():
            return local_path
    except Exception:
        logger.exception("download media object to cache failed: %s", key)

    fallback_dir = Path(tempfile.gettempdir()) / "autovideosrt_material_eval"
    fallback_dir.mkdir(parents=True, exist_ok=True)
    fallback = fallback_dir / f"{uuid.uuid4().hex}{Path(key).suffix or '.bin'}"
    tos_clients.download_media_file(key, str(fallback))
    return fallback
