"""AI 视频分析 - 多模态评估翻译后的视频。

通道：Vertex AI（ADC 凭据），多模态模型（Gemini 3.x Pro）。
输入 schema 是 union——caller 提供哪些资料就拿哪些；prompt 按可用性
动态裁剪维度。

被 appcore.video_ai_review 在后台 thread 调用；返回的 dict 由 service
层落库到 video_ai_reviews 表。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from appcore import llm_client

log = logging.getLogger(__name__)


_LANG_LABEL: dict[str, str] = {
    "zh": "中文", "en": "English", "es": "español", "pt": "português",
    "fr": "français", "it": "italiano", "ja": "日本語", "de": "Deutsch",
    "nl": "Nederlands", "sv": "svenska", "fi": "suomi", "ko": "한국어",
}


class VideoReviewResponseInvalidError(RuntimeError):
    """LLM 输出 schema 不合法。"""


def _system_prompt(*, has_source_video: bool, has_target_video: bool,
                   has_product_info: bool) -> str:
    base = (
        "你是一名短视频带货翻译质量评估师，针对一条视频从源语言翻译到目标语言后的整体质量进行打分。\n"
        "你会收到以下信息（部分可能缺失）：\n"
        "- ORIGINAL_SCRIPT：源语言文案（视频里说什么）\n"
        "- TARGET_SCRIPT：目标语言译文（要播报的最终文案）\n"
    )
    if has_source_video:
        base += "- SOURCE_VIDEO：源语言原视频（含画面 + 原始口播音频）\n"
    if has_target_video:
        base += "- TARGET_VIDEO：目标语言成品视频（含画面 + TTS 音频）\n"
    if has_product_info:
        base += "- PRODUCT_INFO：产品链接 / 主图 / 备注，用于判断视频与产品是否一致\n"
    base += (
        "\n按以下维度打分（每项 0-100），缺数据的维度返回 null：\n"
        "1. translation_fidelity：语义忠实度——目标译文是否完整准确传达源文意思，没有幻觉/遗漏关键卖点\n"
        "2. naturalness：目标语自然度——读起来是否地道，符合目标语言用户口语习惯\n"
        "3. tts_consistency：TTS 一致性——目标视频里的语音是否准确读出 TARGET_SCRIPT，是否有发音问题\n"
        "4. visual_text_alignment：画面与文案匹配——目标视频画面、节奏、口型与目标文案是否对得上（仅当有 TARGET_VIDEO）\n"
        "5. product_alignment：产品契合度——视频展示的内容与产品信息是否一致（仅当有 PRODUCT_INFO）\n"
        "\n输出 JSON：\n"
        "- dimensions: { 维度名: 0-100 或 null }\n"
        "- overall_score: 综合分（已打分维度的平均，0-100）\n"
        "- verdict: recommend / usable_with_minor_issues / needs_review / recommend_redo\n"
        "- verdict_reason: 一句简短中文，说明最差维度或最关键问题\n"
        "- issues: 最多 5 条问题点（中文短句，每条 ≤30 汉字）\n"
        "- highlights: 最多 5 条亮点（中文短句，每条 ≤30 汉字）\n"
        "\nverdict 规则参考：\n"
        "- 全部已打分维度 ≥85 → recommend\n"
        "- 任一维度 <60 → recommend_redo\n"
        "- 全部 ≥70 → usable_with_minor_issues\n"
        "- 否则 → needs_review\n"
        "所有可读字段（issues/highlights/verdict_reason）必须用简体中文。"
    )
    return base


def _response_schema() -> dict:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "video_ai_review",
            "schema": {
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "object",
                        "properties": {
                            "translation_fidelity":   {"type": ["integer", "null"]},
                            "naturalness":            {"type": ["integer", "null"]},
                            "tts_consistency":        {"type": ["integer", "null"]},
                            "visual_text_alignment":  {"type": ["integer", "null"]},
                            "product_alignment":      {"type": ["integer", "null"]},
                        },
                        "required": [
                            "translation_fidelity", "naturalness",
                            "tts_consistency", "visual_text_alignment",
                            "product_alignment",
                        ],
                        "additionalProperties": False,
                    },
                    "overall_score":   {"type": "integer", "minimum": 0, "maximum": 100},
                    "verdict":         {"type": "string"},
                    "verdict_reason":  {"type": "string"},
                    "issues":          {"type": "array", "items": {"type": "string"}},
                    "highlights":      {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "dimensions", "overall_score", "verdict",
                    "verdict_reason", "issues", "highlights",
                ],
                "additionalProperties": False,
            },
        },
    }


def _validate_media_path(label: str, path: str | None) -> str | None:
    """若提供了 media path 则确保文件存在 + 大小合理（≤20MB inline 上限）。"""
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        raise VideoReviewResponseInvalidError(f"media not found ({label}): {path}")
    size = p.stat().st_size
    if size > 20 * 1024 * 1024:
        log.warning(
            "video_ai_review: %s file %s is %.1f MB (>20MB inline limit), may fail",
            label, path, size / (1024 * 1024),
        )
    return str(p)


def assess(
    *,
    source_language: str,
    target_language: str,
    source_text: str,
    target_text: str,
    source_video_path: str | None = None,
    target_video_path: str | None = None,
    product_info: dict | None = None,
    product_image_paths: list[str] | None = None,
    task_id: str | None = None,
    user_id: int | None = None,
) -> dict[str, Any]:
    """同步执行一次评估，返回完整结果（service 负责落库）。"""
    t0 = time.monotonic()

    src_label = _LANG_LABEL.get(source_language, source_language or "?")
    tgt_label = _LANG_LABEL.get(target_language, target_language or "?")
    src_video = _validate_media_path("source_video", source_video_path)
    tgt_video = _validate_media_path("target_video", target_video_path)
    has_product = bool(product_info or product_image_paths)
    product_image_paths = list(product_image_paths or [])
    valid_product_imgs = [
        _validate_media_path(f"product_image[{i}]", p)
        for i, p in enumerate(product_image_paths)
    ]
    valid_product_imgs = [p for p in valid_product_imgs if p]

    system = _system_prompt(
        has_source_video=bool(src_video),
        has_target_video=bool(tgt_video),
        has_product_info=has_product,
    )

    text_blocks: list[str] = [
        f"ORIGINAL_SCRIPT ({src_label}):\n{(source_text or '').strip()}",
        f"TARGET_SCRIPT ({tgt_label}):\n{(target_text or '').strip()}",
    ]
    if has_product:
        product_str = json.dumps(product_info or {}, ensure_ascii=False, indent=2)
        text_blocks.append(f"PRODUCT_INFO:\n{product_str}")
    if src_video:
        text_blocks.append(f"SOURCE_VIDEO 文件已附加。")
    if tgt_video:
        text_blocks.append(f"TARGET_VIDEO 文件已附加。")
    user_text = "\n\n".join(text_blocks)

    media: list[str] = []
    if src_video:
        media.append(src_video)
    if tgt_video:
        media.append(tgt_video)
    media.extend(valid_product_imgs)

    log.info(
        "video_ai_review.assess starting (task=%s lang=%s→%s media=%d)",
        task_id, source_language, target_language, len(media),
    )
    try:
        result = llm_client.invoke_generate(
            "video_ai_review.assess",
            prompt=user_text,
            system=system,
            media=media or None,
            response_schema=_response_schema()["json_schema"]["schema"],
            temperature=0.0,
            user_id=user_id,
            project_id=task_id,
        )
    except Exception as exc:
        raise VideoReviewResponseInvalidError(f"LLM call failed: {exc}") from exc

    payload = result.get("json")
    if payload is None:
        raw_text = (result.get("text") or "").strip()
        try:
            payload = json.loads(raw_text)
        except Exception as exc:
            raise VideoReviewResponseInvalidError(
                f"non-JSON: {raw_text[:200]!r}"
            ) from exc

    if not isinstance(payload, dict) or "dimensions" not in payload:
        raise VideoReviewResponseInvalidError("response missing dimensions")

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return {
        "overall_score":   int(payload.get("overall_score") or 0),
        "dimensions":      payload.get("dimensions") or {},
        "verdict":         (payload.get("verdict") or "").strip(),
        "verdict_reason":  (payload.get("verdict_reason") or "").strip(),
        "issues":          list(payload.get("issues") or []),
        "highlights":      list(payload.get("highlights") or []),
        "raw_response":    payload,
        "system_prompt":   system,
        "user_text":       user_text,
        "media_count":     len(media),
        "usage":           result.get("usage") or {},
        "elapsed_ms":      elapsed_ms,
    }
