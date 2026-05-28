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
from urllib.parse import quote, urlparse

from appcore import llm_bindings, llm_client, local_media_storage, medias, pushes, tos_clients
from appcore.db import execute, query, query_one
from appcore.llm_media_optimizer import SHORT_CLIP_AUDIO, prepare_video_for_llm

logger = logging.getLogger(__name__)

USE_CASE_CODE = "material_evaluation.evaluate"
EVALUATION_PROVIDER = "openrouter"
EVALUATION_MODEL = "google/gemini-3-flash-preview"
EVALUATION_SEARCH_ENABLED = False
EVALUATION_CLIP_SECONDS = 30
MAX_AUTOMATIC_ATTEMPTS = 1
RAW_RESPONSE_PREVIEW_CHARS = 1200
EVAL_CLIPS_ROOT = Path("instance") / "eval_clips"
_ACTIVE_PRODUCT_IDS: set[int] = set()
_ACTIVE_LOCK = threading.Lock()
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
_SUPPORTED_EVALUATION_PROVIDERS = {
    "openrouter",
    "gemini_aistudio",
    "gemini_vertex",
    "gemini_vertex_adc",
}
_TARGET_EVALUATION_LANGUAGES = (
    {"code": "de", "name": "德语", "country": "德国"},
    {"code": "fr", "name": "法语", "country": "法国"},
    {"code": "it", "name": "意大利语", "country": "意大利"},
    {"code": "es", "name": "西班牙语", "country": "西班牙"},
    {"code": "ja", "name": "日语", "country": "日本"},
    {"code": "en", "name": "英语", "country": "美国"},
)


def evaluation_target_languages() -> list[dict[str, str]]:
    return [dict(item) for item in _TARGET_EVALUATION_LANGUAGES]


def _search_tools_for_provider(provider: str) -> list[dict]:
    if (provider or "").strip().lower() == "openrouter":
        return [{"type": "openrouter:web_search"}]
    return [{"google_search": {}}]


def _country_project_id_suffix(lang: dict[str, str]) -> str:
    raw = str(lang.get("code") or "").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    return safe or "country"


def _evaluation_project_id(product_id: int, lang: dict[str, str] | None = None) -> str:
    base = f"media-product-{int(product_id)}"
    if not lang:
        return base
    return f"{base}-{_country_project_id_suffix(lang)}"


def _clean_product_url_override(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return raw


def _resolve_evaluation_product_url(
    product: dict,
    *,
    product_url_override: str | None = None,
) -> str:
    override = _clean_product_url_override(product_url_override)
    if override:
        return override
    return pushes.resolve_product_page_url("en", product)


def _normalize_model_for_provider(provider: str, model: str) -> str:
    provider = (provider or "").strip().lower()
    model = (model or "").strip()
    if provider == "openrouter" and model.startswith("gemini-"):
        return f"google/{model}"
    if provider in {"gemini_aistudio", "gemini_vertex", "gemini_vertex_adc"} and model.startswith("google/"):
        return model.split("/", 1)[1]
    return model


def resolve_evaluation_llm_config() -> dict:
    try:
        binding = llm_bindings.resolve(USE_CASE_CODE)
    except Exception:
        logger.debug("resolve material evaluation LLM binding failed; using defaults", exc_info=True)
        binding = {"provider": EVALUATION_PROVIDER, "model": EVALUATION_MODEL}
    provider = str(binding.get("provider") or EVALUATION_PROVIDER).strip() or EVALUATION_PROVIDER
    if provider not in _SUPPORTED_EVALUATION_PROVIDERS:
        raise ValueError(
            "material_evaluation.evaluate only supports openrouter, "
            "gemini_aistudio, gemini_vertex or gemini_vertex_adc; "
            f"current provider is {provider}"
        )
    model = _normalize_model_for_provider(
        provider,
        str(binding.get("model") or EVALUATION_MODEL).strip() or EVALUATION_MODEL,
    )
    return {
        "provider": provider,
        "model": model,
        "search_enabled": EVALUATION_SEARCH_ENABLED,
        "search_tools": _search_tools_for_provider(provider) if EVALUATION_SEARCH_ENABLED else [],
    }


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


def _make_eval_clip_seconds(
    product_id: int,
    item: dict,
    *,
    seconds: int,
    clips_root: Path | None = None,
) -> Path:
    src_path = _materialize_media(item["object_key"])
    item_id = int(item["id"])
    root = clips_root or EVAL_CLIPS_ROOT
    out_dir = root / str(int(product_id))
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_seconds = max(1, int(seconds))
    out_path = out_dir / f"{item_id}_{clip_seconds}s.mp4"
    llm_out_path = out_dir / f"{item_id}_{clip_seconds}s_llm.mp4"
    if llm_out_path.is_file() and llm_out_path.stat().st_size > 0:
        return llm_out_path

    duration = item.get("duration_seconds")
    try:
        if duration is not None and float(duration) <= clip_seconds:
            return _optimize_eval_clip_for_llm(
                src_path,
                llm_out_path=llm_out_path,
                out_dir=out_dir,
            )
    except (TypeError, ValueError):
        pass

    if out_path.is_file() and out_path.stat().st_size > 0:
        return _optimize_eval_clip_for_llm(
            out_path,
            llm_out_path=llm_out_path,
            out_dir=out_dir,
        )

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        "0",
        "-i",
        str(src_path),
        "-t",
        str(clip_seconds),
        "-c",
        "copy",
        "-avoid_negative_ts",
        "1",
        str(out_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        if result.returncode == 0 and out_path.is_file() and out_path.stat().st_size > 0:
            return _optimize_eval_clip_for_llm(
                out_path,
                llm_out_path=llm_out_path,
                out_dir=out_dir,
            )
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


def _make_eval_clip_30s(
    product_id: int,
    item: dict,
    *,
    clips_root: Path | None = None,
) -> Path:
    return _make_eval_clip_seconds(
        product_id,
        item,
        seconds=EVALUATION_CLIP_SECONDS,
        clips_root=clips_root,
    )


def _make_eval_clip_15s(
    product_id: int,
    item: dict,
    *,
    clips_root: Path | None = None,
) -> Path:
    return _make_eval_clip_seconds(
        product_id,
        item,
        seconds=15,
        clips_root=clips_root,
    )


def _evaluation_clip_preview_url(product_id: int, media_item_id: int | None) -> str:
    url = f"/medias/api/products/{int(product_id)}/evaluate/clip"
    if media_item_id:
        url += f"?media_item_id={int(media_item_id)}"
    return url


def _video_processing_debug(policy=SHORT_CLIP_AUDIO) -> dict:
    return {
        "policy_name": policy.name,
        "max_height": policy.max_height,
        "fps": policy.fps,
        "video_bitrate": policy.video_bitrate,
        "maxrate": policy.maxrate,
        "bufsize": policy.bufsize,
        "drop_audio": policy.drop_audio,
        "audio_bitrate": policy.audio_bitrate,
    }


def _optimize_eval_clip_for_llm(
    clip_path: Path,
    *,
    llm_out_path: Path,
    out_dir: Path,
) -> Path:
    """Prepare the material-evaluation video for LLM upload.

    Docs-anchor:
    docs/superpowers/specs/2026-05-14-llm-video-upload-optimization-design.md
    """
    media = prepare_video_for_llm(
        clip_path,
        SHORT_CLIP_AUDIO,
        output_dir=out_dir,
        output_path=llm_out_path,
    )
    if media.optimized and Path(media.llm_path).is_file():
        return Path(media.llm_path)
    if media.error:
        logger.warning(
            "material evaluation clip optimization failed, fallback to raw clip. path=%s error=%s",
            clip_path,
            media.error,
        )
    return clip_path


def _normalize_languages(languages: list[Any]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in languages or []:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip().lower()
            name = str(item.get("name") or item.get("name_zh") or code).strip()
            country = str(item.get("country") or "").strip()
        else:
            code = str((item or ["", ""])[0]).strip().lower()
            name = str((item or ["", ""])[1] if len(item) > 1 else code).strip()
            country = ""
        if not code or code in seen:
            continue
        seen.add(code)
        normalized.append({"code": code, "name": name or code, "country": country or name or code})
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
                        "recommendation",
                        "summary",
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
                        "recommendation": {
                            "type": "string",
                            "enum": ["做", "不做"],
                        },
                        "summary": {
                            "type": "string",
                            "maxLength": 120,
                            "description": "必须使用中文说明，不得使用目标国家语言。",
                        },
                        "reason": {
                            "type": "string",
                            "maxLength": 100,
                            "description": "必须使用中文说明，不得使用德语、法语、意大利语、西班牙语、日语或英语。",
                        },
                        "suggestions": {
                            "type": "array",
                            "maxItems": 3,
                            "items": {
                                "type": "string",
                                "description": "必须使用中文建议。",
                            },
                        },
                    },
                },
            },
        },
    }


def build_system_prompt() -> str:
    return (
        "你是跨境电商全球市场选品评估专家，极其熟悉欧美及亚太消费文化、广告合规、\n"
        "平台短视频转化和各国本地化风险。请只输出符合 schema 的 JSON，不要输出 Markdown。\n"
        "【重要国别评估准则：差异化与真实研判】\n"
        "1. 严禁不同国家的分数（score）雷同或完全一致！你必须根据目标国家的消费文化、生活习惯、品类竞争激烈度以及法规限制，进行差异化的独立打分。禁止为了偷懒而无脑给所有国家打相同的默认高分（如 88 分）。\n"
        "2. 严禁不同国家使用相似的理由、句式或套路模板！严禁只是机械性替换国名、语言或季节词汇。必须深入探讨该国消费者的独特性。\n"
        "3. 必须结合目标国家的真实本土化人文与市场特征进行深刻研判：\n"
        "   - 德国(DE)：严谨、追求实用性和高品质，看重环保标识和材质安全认证（如CE/GS），消费偏理性保守。\n"
        "   - 法国(FR)：审美艺术感和设计要求高，看重情感连接，反感低质英美粗暴买量风格，重视法式优雅。\n"
        "   - 意大利(IT)：极重家族陪伴和家庭温情，审美水平高，但电商渗透相对保守，高单价决策慢，偏好感性共鸣。\n"
        "   - 西班牙(ES)：户外、出行和社交极度频繁，偏爱高性价比、鲜艳、便携的商品，客单价承受力低于德法。\n"
        "   - 日本(JP/ja)：居住空间极度狭小，极度关注“静音”、“小巧/易折叠收纳”、“绝对无异味/无毒/安全”。视宠物如子嗣，追求极简或极致卡哇伊（Cute），对低质廉价塑料感和粗糙包装极其零容忍。\n"
        "   - 美国(US/en)：消费能力极强，但广告与产品买量竞争是极致红海。极度重视退换货便利性、包装质感、物流时效（如FBA/Prime）。对“BPA-free/安全无害”等产品宣称敏感。\n"
    )


def build_prompt(
    product: dict,
    product_url: str,
    languages: list[Any],
    *,
    as_of_date: date | None = None,
) -> str:
    langs = _normalize_languages(languages)
    lang_text = "、".join(
        f"{item.get('country') or item['name']} / {item['name']}({item['code']})"
        for item in langs
    )
    product_name = str(product.get("name") or "").strip() or "未命名商品"
    product_code = str(product.get("product_code") or "").strip() or "无"
    eval_date = as_of_date or datetime.now().date()
    eval_date_text = eval_date.isoformat()
    return f"""请基于随消息附上的两个素材和商品链接，评估该产品是否适合在目标国家/语种市场推广；业务覆盖全球主要市场，必须按每个目标国家的真实季节和消费场景判断。

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

【真实且具有显著差异的国别研判硬规则】：
1. 当前评估日期：{eval_date_text}。所有季节、气候和消费场景判断必须严格以该日期为准。必须明确分析当前季节对该国商品需求的影响（例如：西欧5月底白昼漫长且户外活动爆发；日本5月正值梅雨季前期且室内潮湿防霉静音玩具是热点）。
2. 各项评分（scores）必须呈现符合真实情况的差异与波动，综合分 score 绝不能在不同国家完全相同！禁止敷衍性地在所有国家都打出默认高分（如 88）。如果某国竞争极度激烈（如美国）或该国消费者由于生活习惯（如日本极小户型）对该产品并不适合，必须坚决打低分（50-70），并将决策（decision）定为“不适合推广”或“谨慎推广”，同时将建议（recommendation）定为“不做”。只有确实完美无暇、低竞争高潜力的市场才允许打高分（85分以上）并判断为“适合推广”与“做”。
3. 详细原因（reason）、结论摘要（summary）和改进建议（suggestions）的内容必须深度切合当前国家的文化、住宅环境（如日本的公寓极小户型对比美国的独栋大别墅）、消费心理和具体法规（如 CE、PSE 认证、GDPR），严禁在不同国家之间使用相似或重复的句式，严禁仅做国名/语言词汇的机械性替换！所有文本输出必须是简体中文，严禁使用英文、拼音或目标国家语言表述原因或建议。

输出要求：
- 必须覆盖上面列出的每一个语种，不能遗漏或新增。
- 每个语种返回结构化 JSON 字段：lang、country、is_suitable、score、risk_level、decision、recommendation、summary、reason、suggestions。
- recommendation 只能返回“做”或“不做”；若不建议搬运视频素材本土化后投放 Meta，则 recommendation 必须是“不做”。
- summary、reason、suggestions 中的所有文字都必须是中文说明；即使评估对象是德国、法国、意大利、西班牙、日本或美国，也绝不能用德语、法语、意大利语、西班牙语、日语或英语写原因。
- summary 为一句中文结论摘要，reason 为 100 字以内的中文核心判断依据。
- reason 必须是中文，100 字以内。
- score 为 0-100，越高代表越适合当前日期在该目标国家推广。
"""


def _contains_han_text(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _contains_non_chinese_script(text: str) -> bool:
    return any(
        ("\u3040" <= ch <= "\u30ff")
        or ("\uac00" <= ch <= "\ud7af")
        for ch in text
    )


def _looks_like_chinese_explanation(text: Any) -> bool:
    raw = str(text or "").strip()
    return bool(raw) and _contains_han_text(raw) and not _contains_non_chinese_script(raw)


def _format_score_for_reason(score: float) -> str:
    return str(int(score)) if float(score).is_integer() else f"{score:.1f}".rstrip("0").rstrip(".")


def _chinese_target_label(row: dict, lang: dict[str, str]) -> str:
    row_country = str(row.get("country") or "").strip()
    if _looks_like_chinese_explanation(row_country):
        return row_country
    return str(lang.get("country") or lang.get("name") or lang.get("code") or "该市场").strip()


def _chinese_reason_fallback(
    *,
    row: dict,
    lang: dict[str, str],
    score: float,
    decision: str,
) -> str:
    target = _chinese_target_label(row, lang)
    score_text = _format_score_for_reason(score)
    conclusion = decision or "需人工复核"
    return f"模型返回的原因不是中文，{target}评分{score_text}，结论为{conclusion}，需人工复核。"


def _normalize_chinese_explanation(
    value: Any,
    *,
    row: dict,
    lang: dict[str, str],
    score: float,
    decision: str,
    max_length: int,
) -> str:
    text = str(value or "").strip()
    if _looks_like_chinese_explanation(text):
        return text[:max_length]
    return _chinese_reason_fallback(
        row=row,
        lang=lang,
        score=score,
        decision=decision,
    )[:max_length]


def _normalize_chinese_suggestions(value: Any) -> list[str]:
    suggestions = value or []
    if not isinstance(suggestions, list):
        suggestions = [suggestions]
    has_non_empty_input = any(str(item or "").strip() for item in suggestions)
    cleaned = [
        str(item).strip()
        for item in suggestions
        if _looks_like_chinese_explanation(item)
    ]
    if cleaned:
        return cleaned[:3]
    if has_non_empty_input:
        return ["模型返回的建议不是中文，需人工复核。"]
    return []


def normalize_result(raw: dict | str, languages: list[Any]) -> dict:
    if isinstance(raw, str):
        raw = json.loads(raw)
    langs = _normalize_languages(languages)
    expected_codes = [item["code"] for item in langs]
    if isinstance(raw, list):
        rows = raw
    else:
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
                "recommendation": "不做",
                "summary": "模型未返回该语种结果，需人工复核。",
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
        recommendation = str(row.get("recommendation") or "").strip()
        if recommendation not in {"做", "不做"}:
            recommendation = "做" if is_suitable and score >= 60 else "不做"
        reason = _normalize_chinese_explanation(
            row.get("reason") or "模型未提供明确判断依据。",
            row=row,
            lang=lang,
            score=score,
            decision=decision,
            max_length=100,
        )
        summary = _normalize_chinese_explanation(
            row.get("summary") or reason or "模型未提供明确结论。",
            row=row,
            lang=lang,
            score=score,
            decision=decision,
            max_length=120,
        )
        suggestions = _normalize_chinese_suggestions(row.get("suggestions"))
        countries.append({
            "lang": lang["code"],
            "language": lang["name"],
            "country": str(row.get("country") or lang["name"]).strip(),
            "is_suitable": is_suitable,
            "score": score,
            "risk_level": risk_level,
            "decision": decision,
            "recommendation": recommendation,
            "summary": summary,
            "reason": reason,
            "suggestions": suggestions,
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


def _has_existing_successful_evaluation(product: dict | None) -> bool:
    if not isinstance(product, dict):
        return False
    result = str(product.get("ai_evaluation_result") or "").strip()
    if not result or result == "评估失败":
        return False
    detail = product.get("ai_evaluation_detail")
    if isinstance(detail, str) and detail.strip():
        try:
            detail = json.loads(detail)
        except json.JSONDecodeError:
            return False
    if not isinstance(detail, dict):
        return False
    countries = detail.get("countries")
    if isinstance(countries, list):
        return bool(countries)
    if isinstance(countries, dict):
        return bool(countries)
    return False


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


def _repair_json_text(raw: str) -> str:
    text = str(raw or "").strip()
    if text.startswith("```"):
        parts = text.split("```")
        if len(parts) >= 2:
            text = parts[1]
            if text.lstrip().startswith("json"):
                text = text.lstrip()[4:]
    object_start = text.find("{")
    array_start = text.find("[")
    starts = [idx for idx in (object_start, array_start) if idx >= 0]
    start = min(starts) if starts else -1
    end = -1
    if start >= 0:
        end = text.rfind("}" if text[start] == "{" else "]")
    if start >= 0 and end >= start:
        text = text[start:end + 1]
    return text.strip()


def _parse_json_with_local_repair(raw: str) -> Any:
    candidate = str(raw or "")
    last_error: Exception | None = None
    for _attempt in range(3):
        try:
            return json.loads(candidate.strip())
        except Exception as exc:
            last_error = exc
            candidate = _repair_json_text(candidate)
    raise ValueError(f"JSON parse failed after local repair: {last_error}") from last_error


def _structured_payload_from_llm_result(result: dict[str, Any]) -> Any:
    payload = result.get("json")
    if payload is not None:
        return payload
    raw_text = str(result.get("text") or "").strip()
    if not raw_text:
        message = str(result.get("json_parse_error") or "LLM returned empty JSON response")
        raise ValueError(message)
    return _parse_json_with_local_repair(raw_text)


def _json_repair_prompt(*, raw_response: str, parse_error: str) -> str:
    return (
        "请修复下面这段大模型原始响应，使其成为一个合法 JSON object，"
        "并保持原始字段含义。\n"
        "要求：\n"
        "1. 只输出 JSON object，不输出解释或 Markdown。\n"
        "2. 不要新增原始响应中没有依据的商品、国家或市场事实。\n"
        "3. 如果某个字段无法修复，使用空字符串、空数组或 null，保持 schema 结构。\n"
        "4. countries 必须尽量保留原始响应中已有的国家条目。\n\n"
        f"解析错误：{parse_error}\n\n"
        "原始响应：\n"
        f"{raw_response}"
    )


def _llm_result_recovery_summary(result: dict[str, Any], parse_error: Exception | str) -> dict[str, Any]:
    raw_text = str(result.get("text") or "")
    return {
        "usage_log_id": result.get("usage_log_id"),
        "json_parse_error": str(result.get("json_parse_error") or parse_error or "")[:500],
        "text_preview": raw_text[:RAW_RESPONSE_PREVIEW_CHARS],
        "text_length": len(raw_text),
    }


def _invoke_evaluation_llm_with_recovery(
    *,
    prompt: str,
    system: str,
    media: list[Path],
    product: dict,
    product_id: int,
    response_schema: dict,
    llm_config: dict,
    recovery: dict[str, Any],
    project_id: str | None = None,
    billing_extra: dict[str, Any] | None = None,
) -> Any:
    project_id = project_id or f"media-product-{product_id}"
    country_billing_extra = dict(billing_extra or {})

    def invoke_original(*, retry_attempt: int = 1) -> dict[str, Any]:
        attempt_project_id = project_id if retry_attempt == 1 else f"{project_id}-retry-{retry_attempt}"
        invoke_billing_extra = {
            "google_search": llm_config["search_enabled"],
            "tools": llm_config["search_tools"],
            "structured_retry_attempt": retry_attempt,
        }
        invoke_billing_extra.update(country_billing_extra)
        return llm_client.invoke_generate(
            USE_CASE_CODE,
            prompt=prompt,
            system=system,
            media=media,
            user_id=product.get("user_id"),
            project_id=attempt_project_id,
            response_schema=response_schema,
            temperature=0.2,
            max_output_tokens=4096,
            provider_override=llm_config["provider"],
            model_override=llm_config["model"],
            google_search=llm_config["search_enabled"],
            billing_extra=invoke_billing_extra,
        )

    first_result = invoke_original()
    try:
        return _structured_payload_from_llm_result(first_result)
    except Exception as exc:
        recovery["initial_usage_log_id"] = first_result.get("usage_log_id")
        recovery["initial_json_parse_error"] = str(first_result.get("json_parse_error") or exc)[:500]
        recovery["initial_raw_response"] = _llm_result_recovery_summary(first_result, exc)
        raw_text = str(first_result.get("text") or "")
        if raw_text.strip():
            recovery["json_repair_attempted"] = True
            try:
                repair_billing_extra = {
                    "google_search": False,
                    "tools": [],
                    "json_repair": True,
                }
                repair_billing_extra.update(country_billing_extra)
                repair_result = llm_client.invoke_generate(
                    USE_CASE_CODE,
                    prompt=_json_repair_prompt(
                        raw_response=raw_text,
                        parse_error=str(first_result.get("json_parse_error") or exc),
                    ),
                    system="你是严格的 JSON 修复器。只输出合法 JSON，不输出解释。",
                    media=None,
                    user_id=product.get("user_id"),
                    project_id=f"{project_id}-json-repair",
                    response_schema=response_schema,
                    temperature=0.0,
                    max_output_tokens=4096,
                    provider_override=llm_config["provider"],
                    model_override=llm_config["model"],
                    google_search=False,
                    billing_extra=repair_billing_extra,
                )
                payload = _structured_payload_from_llm_result(repair_result)
                recovery["json_repair_succeeded"] = True
                recovery["repair_usage_log_id"] = repair_result.get("usage_log_id")
                return payload
            except Exception as repair_exc:
                recovery["json_repair_succeeded"] = False
                recovery["json_repair_error"] = str(repair_exc)[:500]
        else:
            recovery["json_repair_attempted"] = False

    recovery["original_retry_attempted"] = True
    retry_result = invoke_original(retry_attempt=2)
    try:
        payload = _structured_payload_from_llm_result(retry_result)
    except Exception as retry_exc:
        recovery["retry_usage_log_id"] = retry_result.get("usage_log_id")
        recovery["retry_json_parse_error"] = str(retry_result.get("json_parse_error") or retry_exc)[:500]
        recovery["retry_raw_response"] = _llm_result_recovery_summary(retry_result, retry_exc)
        raise
    recovery["retry_usage_log_id"] = retry_result.get("usage_log_id")
    return payload


def _evaluate_countries_with_llm(
    *,
    product: dict,
    product_id: int,
    product_url: str,
    languages: list[dict[str, str]],
    system: str,
    media: list[Path],
    llm_config: dict,
) -> tuple[dict[str, Any], dict[str, Any]]:
    country_rows: list[dict[str, Any]] = []
    recovery_by_lang: dict[str, Any] = {}
    has_incomplete_country = False

    for lang in languages:
        single_language = [lang]
        code = str(lang.get("code") or "").strip().lower()
        country_recovery: dict[str, Any] = {}
        raw_json = _invoke_evaluation_llm_with_recovery(
            prompt=build_prompt(product, product_url, single_language),
            system=system,
            media=media,
            product=product,
            product_id=product_id,
            response_schema=build_response_schema(single_language),
            llm_config=llm_config,
            recovery=country_recovery,
            project_id=_evaluation_project_id(product_id, lang),
            billing_extra={
                "evaluation_mode": "per_country",
                "target_lang": code,
                "target_country": lang.get("country") or lang.get("name") or code,
            },
        )
        normalized = normalize_result(raw_json, single_language)
        country_rows.extend(normalized["countries"])
        if normalized.get("ai_evaluation_result") == "需人工复核":
            has_incomplete_country = True
        if country_recovery:
            recovery_by_lang[code or _country_project_id_suffix(lang)] = country_recovery

    normalized_all = normalize_result({"countries": country_rows}, languages)
    if has_incomplete_country:
        normalized_all["ai_evaluation_result"] = "需人工复核"
    return normalized_all, recovery_by_lang


def _product_link_preflight_error(product_id: int, product_url: str) -> dict | None:
    try:
        ok, error = pushes.probe_ad_url(product_url)
    except Exception as exc:
        logger.info("material evaluation product link probe failed: %s", product_url, exc_info=True)
        ok = False
        error = str(exc) or exc.__class__.__name__
    if ok:
        return None
    return {
        "status": "product_link_unavailable",
        "product_id": product_id,
        "product_url": product_url,
        "error": error or "product link unavailable",
    }


def _is_ready_local_file(path: Path | str | None) -> bool:
    if path is None:
        return False
    try:
        p = Path(path)
        return p.is_file() and p.stat().st_size > 0
    except OSError:
        return False


def _materialize_required_media(
    product_id: int,
    object_key: str,
    *,
    missing_status: str,
) -> tuple[Path | None, dict | None]:
    try:
        path = _materialize_media(object_key)
    except Exception:
        logger.info(
            "material evaluation preflight media materialize failed: product_id=%s object_key=%s",
            product_id,
            object_key,
            exc_info=True,
        )
        return None, {
            "status": missing_status,
            "product_id": product_id,
            "object_key": object_key,
        }
    if not _is_ready_local_file(path):
        return None, {
            "status": missing_status,
            "product_id": product_id,
            "object_key": object_key,
        }
    return Path(path), None


def _materialize_required_eval_video(
    product_id: int,
    video: dict,
) -> tuple[Path | None, dict | None]:
    video_key = str(video.get("object_key") or "").strip()
    try:
        path = _make_eval_clip_30s(product_id, video)
    except Exception:
        logger.info(
            "material evaluation preflight video materialize failed: product_id=%s object_key=%s",
            product_id,
            video_key,
            exc_info=True,
        )
        return None, {
            "status": "missing_video_file",
            "product_id": product_id,
            "object_key": video_key,
        }
    if not _is_ready_local_file(path):
        return None, {
            "status": "missing_video_file",
            "product_id": product_id,
            "object_key": video_key,
        }
    return Path(path), None


def evaluation_clip_preview_file(
    product_id: int,
    *,
    media_item_id: int | None = None,
) -> Path:
    video = _selected_english_video(product_id, media_item_id=media_item_id)
    if not video:
        raise ValueError("missing_video")
    path = _make_eval_clip_30s(int(product_id), video)
    if not _is_ready_local_file(path):
        raise ValueError("missing_video_file")
    return Path(path)


def _debug_media_entry(
    *,
    role: str,
    label: str,
    object_key: str,
    preview_url: str,
    path: Path | None = None,
    item: dict | None = None,
    original_preview_url: str | None = None,
    clip_seconds: int | None = None,
    processing: dict | None = None,
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
    if original_preview_url:
        entry["original_preview_url"] = original_preview_url
    if clip_seconds is not None:
        entry["clip_seconds"] = int(clip_seconds)
    if processing:
        entry["processing"] = dict(processing)
    if item:
        entry.update({
            "item_id": item.get("id"),
            "duration_seconds": item.get("duration_seconds"),
            "file_size": item.get("file_size"),
        })
    if path:
        entry["submitted_filename"] = Path(path).name
        entry["submitted_path"] = str(path)
    if include_base64 and path:
        data = path.read_bytes()
        entry["byte_size"] = len(data)
        entry["base64"] = base64.b64encode(data).decode("ascii")
    return entry


def build_request_debug_payload(
    product_id: int,
    *,
    include_base64: bool = False,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
) -> dict:
    product_id = int(product_id)
    product = medias.get_product(product_id)
    if not product:
        raise ValueError("product_missing")

    languages = evaluation_target_languages()
    if not languages:
        raise ValueError("missing_languages")

    product_url = _resolve_evaluation_product_url(
        product,
        product_url_override=product_url_override,
    )
    if not product_url:
        raise ValueError("missing_product_link")

    cover_key = _resolve_product_cover_key(product_id, product)
    if not cover_key or not _looks_like_image_key(cover_key):
        raise ValueError("missing_cover")

    video = _selected_english_video(product_id, media_item_id=media_item_id)
    if not video:
        raise ValueError("missing_video")
    video_key = str(video.get("object_key") or "").strip()
    video_item_id = int(video.get("id") or 0) or None
    video_processing = _video_processing_debug()

    cover_path = _materialize_media(cover_key) if include_base64 else None
    video_path = _make_eval_clip_30s(product_id, video) if include_base64 else None
    system_prompt = build_system_prompt()
    user_prompt = build_prompt(product, product_url, languages)
    response_schema = build_response_schema(languages)
    llm_config = resolve_evaluation_llm_config()
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
            preview_url=_evaluation_clip_preview_url(product_id, video_item_id),
            original_preview_url=f"/medias/object?object_key={quote(video_key, safe='')}",
            path=video_path,
            item=video,
            clip_seconds=EVALUATION_CLIP_SECONDS,
            processing=video_processing,
            include_base64=include_base64,
        ),
    ]
    country_requests = [
        {
            "lang": lang.get("code"),
            "country": lang.get("country") or lang.get("name") or lang.get("code"),
            "project_id": _evaluation_project_id(product_id, lang),
            "prompt": build_prompt(product, product_url, [lang]),
            "response_schema": build_response_schema([lang]),
            "media": "[same as request.media]",
        }
        for lang in languages
    ]
    request_payload = {
        "use_case": USE_CASE_CODE,
        "evaluation_mode": "per_country",
        "provider": llm_config["provider"],
        "model": llm_config["model"],
        "system": system_prompt,
        "prompt": user_prompt,
        "media": [
            {
                "role": item["role"],
                "filename": item["filename"],
                "submitted_filename": item.get("submitted_filename"),
                "mime_type": item["mime_type"],
                "object_key": item["object_key"],
                "preview_url": item.get("preview_url"),
                "original_preview_url": item.get("original_preview_url"),
                "clip_seconds": item.get("clip_seconds"),
                "processing": item.get("processing"),
                "data_base64": item.get("base64") if include_base64 else "[omitted]",
            }
            for item in media
        ],
        "user_id": product.get("user_id"),
        "project_id": f"media-product-{product_id}",
        "response_schema": response_schema,
        "temperature": 0.2,
        "max_output_tokens": 4096,
        "google_search": llm_config["search_enabled"],
        "tools": llm_config["search_tools"],
        "country_requests": country_requests,
    }
    return {
        "product": {
            "id": product_id,
            "name": product.get("name") or "",
            "product_code": product.get("product_code") or "",
            "product_url": product_url,
            "user_id": product.get("user_id"),
        },
        "media_item_id": int(media_item_id) if media_item_id else video.get("id"),
        "evaluation_mode": "per_country",
        "languages": languages,
        "prompts": {
            "system": system_prompt,
            "user": user_prompt,
        },
        "response_schema": response_schema,
        "llm": {
            "use_case": USE_CASE_CODE,
            "evaluation_mode": "per_country",
            "provider": llm_config["provider"],
            "model": llm_config["model"],
            "temperature": 0.2,
            "max_output_tokens": 4096,
            "project_id": f"media-product-{product_id}",
            "country_project_ids": [
                _evaluation_project_id(product_id, lang)
                for lang in languages
            ],
            "google_search": llm_config["search_enabled"],
            "tools": llm_config["search_tools"],
        },
        "media": media,
        "request": request_payload,
        "country_requests": country_requests,
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


def evaluate_product_if_ready(
    product_id: int,
    *,
    force: bool = False,
    manual: bool = False,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
) -> dict:
    pid = int(product_id)
    if not _enter_product(pid):
        return {"status": "running", "product_id": pid}
    try:
        return _evaluate_product_if_ready(
            pid,
            force=force,
            manual=manual,
            media_item_id=media_item_id,
            product_url_override=product_url_override,
        )
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


def _evaluate_product_if_ready(
    product_id: int,
    *,
    force: bool = False,
    manual: bool = False,
    media_item_id: int | None = None,
    product_url_override: str | None = None,
) -> dict:
    product = medias.get_product(product_id)
    if not product:
        return {"status": "product_missing", "product_id": product_id}
    if not force and str(product.get("ai_evaluation_result") or "").strip():
        return {"status": "already_evaluated", "product_id": product_id}

    languages = evaluation_target_languages()
    if not languages:
        return {"status": "missing_languages", "product_id": product_id}

    product_url = _resolve_evaluation_product_url(
        product,
        product_url_override=product_url_override,
    )
    if not product_url:
        return {"status": "missing_product_link", "product_id": product_id}
    link_error = _product_link_preflight_error(product_id, product_url)
    if link_error:
        return link_error

    cover_key = _resolve_product_cover_key(product_id, product)
    if not cover_key:
        return {"status": "missing_cover", "product_id": product_id}
    if not _looks_like_image_key(cover_key):
        return {"status": "missing_cover", "product_id": product_id}

    video = _selected_english_video(product_id, media_item_id=media_item_id)
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

    cover_path, media_error = _materialize_required_media(
        product_id,
        cover_key,
        missing_status="missing_cover_file",
    )
    if media_error:
        return media_error
    video_path, media_error = _materialize_required_eval_video(product_id, video)
    if media_error:
        return media_error

    llm_config = {
        "provider": EVALUATION_PROVIDER,
        "model": EVALUATION_MODEL,
        "search_enabled": EVALUATION_SEARCH_ENABLED,
        "search_tools": _search_tools_for_provider(EVALUATION_PROVIDER) if EVALUATION_SEARCH_ENABLED else [],
    }
    attempt_id = None
    llm_recovery: dict[str, Any] = {}
    try:
        llm_config = resolve_evaluation_llm_config()
        attempt_id = _record_attempt_start(
            product_id,
            cover_key,
            video_key,
            trigger="manual" if manual else "auto",
        )
        normalized, llm_recovery = _evaluate_countries_with_llm(
            product=product,
            product_id=product_id,
            product_url=product_url,
            languages=languages,
            system=build_system_prompt(),
            media=[cover_path, video_path],
            llm_config=llm_config,
        )
        detail = {
            "schema_version": 1,
            "use_case": USE_CASE_CODE,
            "evaluation_mode": "per_country",
            "country_call_count": len(languages),
            "provider": llm_config["provider"],
            "model": llm_config["model"],
            "search_enabled": llm_config["search_enabled"],
            "search_tools": llm_config["search_tools"],
            "evaluated_at": datetime.now(UTC).isoformat(),
            "product_id": product_id,
            "requested_media_item_id": int(media_item_id) if media_item_id else None,
            "product_url_override": _clean_product_url_override(product_url_override) or None,
            "product_url": product_url,
            "cover_object_key": cover_key,
            "video_item_id": video.get("id"),
            "video_object_key": video_key,
            "video_clip_path": str(video_path),
            "countries": normalized["countries"],
        }
        if llm_recovery:
            detail["llm_recovery"] = llm_recovery
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
            "ai_evaluation_detail": detail,
        }
    except Exception as exc:
        logger.exception("material evaluation LLM call failed for product_id=%s", product_id)
        error_message = str(exc)[:500] or exc.__class__.__name__
        _record_attempt_finish(attempt_id, success=False, error=str(exc))
        preserve_existing = _has_existing_successful_evaluation(product)
        if not preserve_existing:
            try:
                detail = {
                    "schema_version": 1,
                    "use_case": USE_CASE_CODE,
                    "provider": llm_config["provider"],
                    "model": llm_config["model"],
                    "search_enabled": llm_config["search_enabled"],
                    "search_tools": llm_config["search_tools"],
                    "evaluated_at": datetime.now(UTC).isoformat(),
                    "product_id": product_id,
                    "requested_media_item_id": int(media_item_id) if media_item_id else None,
                    "product_url_override": _clean_product_url_override(product_url_override) or None,
                    "product_url": product_url,
                    "cover_object_key": cover_key,
                    "video_item_id": video.get("id"),
                    "video_object_key": video_key,
                    "error": error_message,
                }
                if llm_recovery:
                    detail["llm_recovery"] = llm_recovery
                medias.update_product(
                    product_id,
                    ai_evaluation_result="评估失败",
                    ai_evaluation_detail=json.dumps(detail, ensure_ascii=False),
                )
            except Exception:
                logger.exception("failed to save evaluation failure status for product_id=%s", product_id)
        result = {"status": "failed", "product_id": product_id, "error": error_message}
        if preserve_existing:
            result["preserved_existing_evaluation"] = True
        return result


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


def _selected_english_video(
    product_id: int,
    *,
    media_item_id: int | None = None,
) -> dict | None:
    item_id = int(media_item_id or 0)
    if item_id > 0:
        try:
            item = medias.get_item(item_id)
        except Exception:
            item = None
        if item and int(item.get("product_id") or 0) == int(product_id):
            lang = str(item.get("lang") or "").strip().lower()
            object_key = str(item.get("object_key") or "").strip()
            if lang == "en" and object_key and _looks_like_video_item(item):
                return {**item, "object_key": object_key}
    return _first_english_video(product_id)


def _materialize_media(object_key: str) -> Path:
    key = str(object_key or "").strip()
    if not key:
        raise ValueError("object_key required")
    if local_media_storage.exists(key):
        local_path = local_media_storage.safe_local_path_for(key)
        if not local_path.is_file():
            local_media_storage.download_to(key, local_path)
        return local_path
    local_path = local_media_storage.safe_local_path_for(key)
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
