from __future__ import annotations

import base64
import json
import logging
import hashlib
import mimetypes
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from appcore import llm_client, local_media_storage, medias, pushes, tos_clients
from appcore.db import execute, query, query_one

logger = logging.getLogger(__name__)

USE_CASE_CODE = "material_evaluation.evaluate"
EVALUATION_PROVIDER = "gemini_aistudio"
EVALUATION_MODEL = "gemini-3.1-pro-preview"
EVALUATION_GOOGLE_SEARCH = True
MAX_AUTOMATIC_ATTEMPTS = 1
EVAL_CLIPS_ROOT = Path("instance") / "eval_clips"
_ACTIVE_PRODUCT_IDS: set[int] = set()
_ACTIVE_LOCK = threading.Lock()
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _has_suffix(value: Any, suffixes: set[str]) -> bool:
    suffix = Path(str(value or "").strip().split("?", 1)[0]).suffix.lower()
    return suffix in suffixes


def _looks_like_video_item(item: dict | None) -> bool:
    if not isinstance(item, dict):
        return False
    return (
        _has_suffix(item.get("object_key"), _VIDEO_SUFFIXES)
        or _has_suffix(item.get("filename"), _VIDEO_SUFFIXES)
    )


def _looks_like_image_key(object_key: str) -> bool:
    return _has_suffix(object_key, _IMAGE_SUFFIXES)


def _make_eval_clip_15s(
    product_id: int,
    item: dict,
    *,
    clips_root: Path | None = None,
) -> Path:
    src_path = _materialize_media(item["object_key"])
    duration = item.get("duration_seconds")
    try:
        if duration is not None and float(duration) <= 15:
            return src_path
    except (TypeError, ValueError):
        pass

    item_id = int(item["id"])
    root = clips_root or EVAL_CLIPS_ROOT
    out_dir = root / str(int(product_id))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{item_id}_15s.mp4"
    if out_path.is_file() and out_path.stat().st_size > 0:
        return out_path

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0",
        "-i",
        str(src_path),
        "-t",
        "15",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "1",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        if result.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0:
            return out_path
        logger.warning(
            "ffmpeg eval clip cut failed, fallback to original. cmd=%s stderr=%s",
            cmd,
            result.stderr.decode("utf-8", errors="replace")[:500],
        )
        try:
            if out_path.is_file():
                out_path.unlink()
        except Exception:
            pass
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg eval clip cut timed out, fallback to original")
    except FileNotFoundError as exc:
        logger.warning("ffmpeg not found for eval clip, fallback to original: %s", exc)
    return src_path


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


def build_prompt(
    product: dict,
    product_url: str,
    languages: list[Any],
    *,
    as_of_date: date | None = None,
) -> str:
    langs = _normalize_languages(languages)
    lang_text = "、".join(f"{item['name']}({item['code']})" for item in langs)
    product_name = str(product.get("name") or "").strip() or "未命名商品"
    product_code = str(product.get("product_code") or "").strip() or "无"
    eval_date = as_of_date or datetime.now().date()
    eval_date_text = eval_date.isoformat()
    return f"""请基于随消息附上的两个素材和商品链接，评估该产品是否适合在目标国家/语种市场推广；业务以欧洲市场为主，如果语种配置或 country 判断涉及非欧洲国家，也必须按该国家的真实季节和消费场景判断。

输入素材顺序：
1. 商品主图：判断品类、外观、卖点、潜在合规风险。
2. 推广视频：取系统中该商品第一条英语视频素材，判断短视频内容、使用场景、口播/画面表达是否适合本地化推广。

商品信息：
- 商品名称：{product_name}
- 商品编码：{product_code}
- 商品链接：{product_url}

需要覆盖的小语种国家/语种：{lang_text}

当前时间与季节判断：
- 当前评估日期：{eval_date_text}。所有季节、节日、气候和消费场景判断都必须以这个日期为准。
- 必须先判断每个目标国家/地区所处半球及当前季节，再判断产品是否处在当地当季需求窗口。
- 北半球季节参考：3-5 月春季，6-8 月夏季，9-11 月秋季，12-2 月冬季。
- 南半球季节相反：澳大利亚、新西兰、南非、智利、阿根廷等地在 12-2 月为夏季，6-8 月为冬季。
- 如果目标语种对应多个国家，请按该语种最常见的主要投放国家判断；如果模型判断为澳大利亚等南半球国家，必须使用南半球季节。
- 对强季节性产品必须严格扣分：冬季服饰/保暖/大衣羽绒服护理/毛球修剪器等冬季使用场景，在当地春末或夏季通常不适合当前投放；夏季降温、防晒、户外水上用品在当地秋冬通常不适合当前投放。
- 如果产品的核心使用场景与当地当前季节明显错配，除非素材中有明确的反季促销、全年刚需或礼品场景证据，否则 is_suitable 应为 false，decision 应为“不适合推广”或“谨慎推广”，score 通常不应高于 55，并在 reason 中说明季节错配。
- 例：毛球修剪器主要用于大衣、毛衣、羽绒服等冬季衣物护理。当前若目标国家位于北半球且接近夏季，应判定当前投放需求弱，不应仅因素材清晰就给出“适合推广”。

市场时点 Gate（不要一刀切）：
- 先判断产品属于哪类时点模型：全年常青型、强季节型、节日/礼品节点型、气候触发型、短趋势型、问题解决型。
- 判断“当前是否适合开始投放”，而不只是“产品理论上有没有需求”。需考虑投放准备提前量：素材本地化、上架、广告学习、物流履约通常需要时间；如果距离需求峰值太近或已经错过，应降分。
- 节日/礼品节点：母亲节、父亲节、返校季、万圣节、黑五、圣诞、复活节、斋月等，需要判断当前日期距离节点是否足以启动投放，以及是否仍在购买决策窗口内。
- 气候触发因素：雨季、防晒、降温、取暖、除湿、驱虫、园艺、过敏季、空气质量等，要结合目标国家当前或即将到来的气候窗口判断。
- 品类生命周期：判断该品类是长期常青、季节复购、节日短峰、短视频趋势品，还是热点已过；短趋势型若缺少近期热度证据，应谨慎。
- 竞争和价格敏感度：若该品类在目标市场同质化严重、低价竞争强、差异化卖点弱，即使季节正确也应降分或给出谨慎推广。
- 物流履约限制：带电池、液体、粉末、磁性、刀片、加热、儿童接触、宠物健康等产品，要考虑跨境运输、清关、退货和平台限制对当前市场转化的影响。
- 这些时点因素不是一票否决。不要因为产品存在季节性就自动判为不适合；如果当前处于提前预热期、目标国家正进入旺季、产品有全年刚需、礼品属性、提前预热、反季市场、清仓促销或素材中有强使用场景证据，可以判为“适合推广”或“谨慎推广”，但必须在 reason 中说明依据。

请逐一判断每个国家/语种的推广适配度，重点考虑：
- 目标国家消费者是否有明确需求和购买场景。
- 商品主图与视频卖点是否清晰、可信、容易本地化。
- 是否存在医疗、功效夸大、安全、儿童、隐私、环保等广告合规风险。
- 价格敏感度、当前当地季节、市场时点、文化接受度、视频内容与当地审美是否匹配。

输出要求：
- 必须覆盖上面列出的每一个语种，不能遗漏或新增。
- 每个语种返回结构化 JSON 字段：lang、country、is_suitable、score、risk_level、decision、reason、suggestions。
- reason 必须是中文，100 字以内。
- score 为 0-100，越高代表越适合当前日期在该目标国家推广。
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
        name_by_code = {item["code"]: item["name"] for item in langs}
        for code in missing:
            by_lang[code] = {
                "lang": code,
                "country": name_by_code.get(code) or code,
                "is_suitable": False,
                "score": 50,
                "risk_level": "high",
                "decision": "谨慎推广",
                "reason": "模型未返回该语种结果，需人工复核。",
                "suggestions": ["补充人工判断"],
            }

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
    if missing:
        evaluation_result = "需人工复核"
    elif suitable_count == len(countries) and countries:
        evaluation_result = "适合推广"
    elif suitable_count > 0:
        evaluation_result = "部分适合推广"
    else:
        evaluation_result = "不适合推广"
    return {
        "countries": countries,
        "ai_score": avg_score,
        "ai_evaluation_result": evaluation_result,
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


def _debug_media_entry(
    *,
    role: str,
    label: str,
    object_key: str,
    preview_url: str,
    path: Path | None = None,
    item: dict | None = None,
    include_base64: bool = False,
) -> dict:
    filename = Path(str(object_key or "")).name or (path.name if path else "")
    mime_type = mimetypes.guess_type(filename or str(path or ""))[0] or "application/octet-stream"
    entry = {
        "role": role,
        "label": label,
        "object_key": object_key,
        "filename": filename,
        "mime_type": mime_type,
        "preview_url": preview_url,
    }
    if item:
        entry.update({
            "item_id": item.get("id"),
            "duration_seconds": item.get("duration_seconds"),
            "file_size": item.get("file_size"),
        })
    if include_base64 and path:
        data = path.read_bytes()
        entry["byte_size"] = len(data)
        entry["base64"] = base64.b64encode(data).decode("ascii")
    return entry


def build_request_debug_payload(product_id: int, *, include_base64: bool = False) -> dict:
    product_id = int(product_id)
    product = medias.get_product(product_id)
    if not product:
        raise ValueError("product_missing")

    languages = _normalize_languages(medias.list_enabled_languages_kv())
    if not languages:
        raise ValueError("missing_languages")

    product_url = pushes.resolve_product_page_url("en", product)
    if not product_url:
        raise ValueError("missing_product_link")

    cover_key = _resolve_product_cover_key(product_id, product)
    if not cover_key or not _looks_like_image_key(cover_key):
        raise ValueError("missing_cover")

    video = _first_english_video(product_id)
    if not video:
        raise ValueError("missing_video")
    video_key = str(video.get("object_key") or "").strip()

    cover_path = _materialize_media(cover_key) if include_base64 else None
    video_path = _make_eval_clip_15s(product_id, video) if include_base64 else None
    system_prompt = build_system_prompt()
    user_prompt = build_prompt(product, product_url, languages)
    response_schema = build_response_schema(languages)
    media = [
        _debug_media_entry(
            role="product_cover",
            label="商品主图",
            object_key=cover_key,
            preview_url=f"/medias/cover/{product_id}?lang=en",
            path=cover_path,
            include_base64=include_base64,
        ),
        _debug_media_entry(
            role="english_video",
            label="英文视频",
            object_key=video_key,
            preview_url=f"/medias/object?object_key={quote(video_key, safe='')}",
            path=video_path,
            item=video,
            include_base64=include_base64,
        ),
    ]
    request_payload = {
        "use_case": USE_CASE_CODE,
        "provider": EVALUATION_PROVIDER,
        "model": EVALUATION_MODEL,
        "system": system_prompt,
        "prompt": user_prompt,
        "media": [
            {
                "role": item["role"],
                "filename": item["filename"],
                "mime_type": item["mime_type"],
                "object_key": item["object_key"],
                "data_base64": item.get("base64") if include_base64 else "[omitted]",
            }
            for item in media
        ],
        "user_id": product.get("user_id"),
        "project_id": f"media-product-{product_id}",
        "response_schema": response_schema,
        "temperature": 0.2,
        "max_output_tokens": 4096,
        "google_search": EVALUATION_GOOGLE_SEARCH,
        "tools": [{"google_search": {}}],
    }
    return {
        "product": {
            "id": product_id,
            "name": product.get("name") or "",
            "product_code": product.get("product_code") or "",
            "product_url": product_url,
            "user_id": product.get("user_id"),
        },
        "languages": languages,
        "prompts": {
            "system": system_prompt,
            "user": user_prompt,
        },
        "response_schema": response_schema,
        "llm": {
            "use_case": USE_CASE_CODE,
            "provider": EVALUATION_PROVIDER,
            "model": EVALUATION_MODEL,
            "temperature": 0.2,
            "max_output_tokens": 4096,
            "project_id": f"media-product-{product_id}",
            "google_search": EVALUATION_GOOGLE_SEARCH,
            "tools": [{"google_search": {}}],
        },
        "media": media,
        "request": request_payload,
        "include_base64": include_base64,
    }


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
            "     AND ("
            "       LOWER(i.object_key) LIKE '%%.mp4'"
            "       OR LOWER(i.object_key) LIKE '%%.mov'"
            "       OR LOWER(i.object_key) LIKE '%%.m4v'"
            "       OR LOWER(i.object_key) LIKE '%%.webm'"
            "       OR LOWER(i.object_key) LIKE '%%.avi'"
            "       OR LOWER(i.object_key) LIKE '%%.mkv'"
            "     )"
            " ) "
            "ORDER BY p.updated_at ASC, p.id ASC LIMIT %s",
            (int(limit),),
        )
    except Exception:
        logger.exception("material evaluation ready-product scan failed")
        return []
    return [int(row["id"]) for row in rows]


def evaluate_product_if_ready(product_id: int, *, force: bool = False,
                              manual: bool = False) -> dict:
    pid = int(product_id)
    if not _enter_product(pid):
        return {"status": "running", "product_id": pid}
    try:
        return _evaluate_product_if_ready(pid, force=force, manual=manual)
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


def _evaluate_product_if_ready(product_id: int, *, force: bool = False,
                               manual: bool = False) -> dict:
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
    if not _looks_like_image_key(cover_key):
        return {"status": "missing_cover", "product_id": product_id}

    video = _first_english_video(product_id)
    if not video:
        return {"status": "missing_video", "product_id": product_id}
    video_key = str(video.get("object_key") or "").strip()

    if not manual:
        attempts = _automatic_attempt_count(product_id, cover_key, video_key)
        if attempts >= MAX_AUTOMATIC_ATTEMPTS:
            return {
                "status": "auto_attempt_limit_reached",
                "product_id": product_id,
                "attempts": attempts,
            }

    attempt_id = None
    try:
        cover_path = _materialize_media(cover_key)
        video_path = _make_eval_clip_15s(product_id, video)
        attempt_id = _record_attempt_start(
            product_id,
            cover_key,
            video_key,
            trigger="manual" if manual else "auto",
        )
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
            provider_override=EVALUATION_PROVIDER,
            model_override=EVALUATION_MODEL,
            google_search=EVALUATION_GOOGLE_SEARCH,
            billing_extra={"google_search": EVALUATION_GOOGLE_SEARCH},
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
            "video_object_key": video_key,
            "video_clip_path": str(video_path),
            "countries": normalized["countries"],
        }
        medias.update_product(
            product_id,
            ai_score=normalized["ai_score"],
            ai_evaluation_result=normalized["ai_evaluation_result"],
            ai_evaluation_detail=json.dumps(detail, ensure_ascii=False),
        )
        _record_attempt_finish(attempt_id, success=True, error="")
        return {
            "status": "evaluated",
            "product_id": product_id,
            "ai_score": normalized["ai_score"],
            "ai_evaluation_result": normalized["ai_evaluation_result"],
        }
    except Exception as exc:
        logger.exception("material evaluation LLM call failed for product_id=%s", product_id)
        error_message = str(exc)[:500] or exc.__class__.__name__
        _record_attempt_finish(attempt_id, success=False, error=str(exc))
        try:
            detail = {
                "schema_version": 1,
                "use_case": USE_CASE_CODE,
                "evaluated_at": datetime.now(UTC).isoformat(),
                "product_id": product_id,
                "product_url": product_url,
                "cover_object_key": cover_key,
                "video_item_id": video.get("id"),
                "video_object_key": video_key,
                "error": error_message,
            }
            medias.update_product(
                product_id,
                ai_evaluation_result="评估失败",
                ai_evaluation_detail=json.dumps(detail, ensure_ascii=False),
            )
        except Exception:
            logger.exception("failed to save evaluation failure status for product_id=%s", product_id)
        return {"status": "failed", "product_id": product_id, "error": error_message}


def _automatic_attempt_count(product_id: int, cover_key: str, video_key: str) -> int:
    """Count automatic attempts, including historical usage logs before this guard."""
    table_count = 0
    try:
        row = query_one(
            "SELECT automatic_attempts FROM material_evaluation_attempts "
            "WHERE product_id=%s AND cover_key_hash=%s AND video_key_hash=%s "
            "AND cover_object_key=%s AND video_object_key=%s "
            "LIMIT 1",
            (int(product_id), _key_hash(cover_key), _key_hash(video_key), cover_key, video_key),
        )
        table_count = int((row or {}).get("automatic_attempts") or 0)
    except Exception:
        logger.debug("material evaluation attempt table count failed", exc_info=True)

    logged_count = 0
    try:
        row = query_one(
            "SELECT COUNT(*) AS cnt FROM usage_logs "
            "WHERE use_case_code=%s AND project_id=%s",
            (USE_CASE_CODE, f"media-product-{int(product_id)}"),
        )
        logged_count = int((row or {}).get("cnt") or 0)
    except Exception:
        logger.debug("material evaluation historical usage count failed", exc_info=True)
    return max(table_count, logged_count)


def _record_attempt_start(product_id: int, cover_key: str, video_key: str,
                          *, trigger: str) -> int | None:
    trigger = "manual" if trigger == "manual" else "auto"
    try:
        execute(
            "INSERT INTO material_evaluation_attempts "
            "(product_id, cover_object_key, video_object_key, cover_key_hash, "
            " video_key_hash, automatic_attempts, manual_attempts, last_trigger, "
            " last_status, last_started_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'running', NOW()) "
            "ON DUPLICATE KEY UPDATE "
            " automatic_attempts=automatic_attempts+VALUES(automatic_attempts), "
            " manual_attempts=manual_attempts+VALUES(manual_attempts), "
            " last_trigger=VALUES(last_trigger), last_status='running', "
            " last_started_at=NOW(), updated_at=NOW()",
            (
                int(product_id),
                cover_key,
                video_key,
                _key_hash(cover_key),
                _key_hash(video_key),
                0 if trigger == "manual" else 1,
                1 if trigger == "manual" else 0,
                trigger,
            ),
        )
        row = query_one(
            "SELECT id FROM material_evaluation_attempts "
            "WHERE product_id=%s AND cover_key_hash=%s AND video_key_hash=%s "
            "AND cover_object_key=%s AND video_object_key=%s "
            "LIMIT 1",
            (int(product_id), _key_hash(cover_key), _key_hash(video_key), cover_key, video_key),
        )
        return int(row["id"]) if row else None
    except Exception:
        logger.debug("record material evaluation attempt start failed", exc_info=True)
        return None


def _record_attempt_finish(attempt_id: int | None, *, success: bool, error: str) -> None:
    if not attempt_id:
        return
    try:
        execute(
            "UPDATE material_evaluation_attempts "
            "SET last_status=%s, last_error=%s, last_finished_at=NOW(), updated_at=NOW() "
            "WHERE id=%s",
            ("success" if success else "failed", (error or "")[:500], int(attempt_id)),
        )
    except Exception:
        logger.debug("record material evaluation attempt finish failed", exc_info=True)


def _key_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _resolve_product_cover_key(product_id: int, product: dict) -> str:
    try:
        cover_key = medias.resolve_cover(product_id, "en")
    except Exception:
        cover_key = None
    return str(cover_key or product.get("cover_object_key") or "").strip()


def _first_english_video(product_id: int) -> dict | None:
    for item in medias.list_items(product_id, "en"):
        object_key = str(item.get("object_key") or "").strip()
        if object_key and _looks_like_video_item(item):
            return {**item, "object_key": object_key}
    return None


def _materialize_media(object_key: str) -> Path:
    key = str(object_key or "").strip()
    if not key:
        raise ValueError("object_key required")
    if local_media_storage.exists(key):
        local_path = local_media_storage.local_path_for(key)
        if not local_path.is_file():
            local_media_storage.download_to(key, local_path)
        return local_path
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
