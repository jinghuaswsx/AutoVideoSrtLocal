from __future__ import annotations


def media_item(item_type: str, label: str, artifact: str) -> dict:
    return {"type": item_type, "label": label, "artifact": artifact}


def text_item(label: str, content: str) -> dict:
    return {"type": "text", "label": label, "content": content or ""}


def action_item(label: str, url: str, method: str = "POST") -> dict:
    return {"type": "action", "label": label, "url": url, "method": method}


_LANG_LABELS = {
    "zh": "中文",
    "en": "英文",
    "de": "德语",
    "fr": "法语",
    "es": "西班牙语",
    "it": "意大利语",
    "pt": "葡萄牙语",
    "ja": "日语",
    "nl": "荷兰语",
    "sv": "瑞典语",
    "fi": "芬兰语",
}


def _lang(code: str) -> str:
    return _LANG_LABELS.get(code, code)


def build_variant_compare_artifact(title: str, variants: dict) -> dict:
    return {
        "title": title,
        "layout": "variant_compare",
        "variants": variants,
    }


def build_extract_artifact() -> dict:
    return {
        "title": "音频提取",
        "items": [media_item("audio", "提取音频", "audio_extract")],
    }


def build_asr_artifact(utterances: list[dict], source_full_text_zh: str = "", source_language: str = "zh") -> dict:
    # 标签不再硬编码源语言（LLM 看文本自己能辨）——避免英文视频被标"中文识别分段"
    left = {
        "type": "utterances",
        "label": "识别分段",
        "utterances": utterances or [],
    }
    right = text_item("整段识别文本", source_full_text_zh)
    if source_full_text_zh:
        return {"title": "语音识别", "items": [{"type": "side_by_side", "left": left, "right": right}]}
    return {"title": "语音识别", "items": [left]}


def build_asr_normalize_artifact(
    raw_artifact: dict | None,
    source_utterances: list[dict] | None = None,
    en_utterances: list[dict] | None = None,
) -> dict:
    """把 _step_asr_normalize 的原始 artifact 投影成左右对照的步骤预览。

    左侧：原文 utterances（小语种，带时间戳）；
    右侧：英文标准化 utterances（同时间戳，逐段对应）。

    展示完整 utterances 而非 200 字符截断，方便人工确认标准化未丢段。
    en_skip / zh_skip 路由没有翻译，右侧仅给一行说明。
    """
    if not raw_artifact:
        return {"title": "原文标准化", "items": []}
    src_label = (raw_artifact.get("input") or {}).get("language_label") or "原文"
    route = raw_artifact.get("route") or ""
    src_list = source_utterances or []
    left = {
        "type": "utterances",
        "label": f"原文（{src_label}）· {len(src_list)} 段",
        "utterances": src_list,
    }
    if route in ("en_skip", "zh_skip") or not en_utterances:
        right = text_item("英文标准化", "无需标准化（已为目标语言）")
    else:
        right = {
            "type": "utterances",
            "label": f"英文标准化 · {len(en_utterances)} 段",
            "utterances": en_utterances,
        }
    return {
        "title": "原文标准化",
        "items": [{
            "type": "side_by_side",
            "left": left,
            "right": right,
        }],
    }


def build_alignment_artifact(
    scene_cuts: list[float],
    script_segments: list[dict],
    break_after: list[bool],
) -> dict:
    return {
        "title": "分段确认",
        "items": [
            {
                "type": "segments",
                "label": "翻译分段",
                "segments": script_segments or [],
                "break_after": break_after or [],
            },
        ],
    }


def build_translate_artifact(source_or_segments, localized_translation: dict | None = None,
                             source_language: str = "zh", target_language: str = "en") -> dict:
    sl, tl = _lang(source_language), _lang(target_language)
    if localized_translation is None and isinstance(source_or_segments, list):
        return {
            "title": "翻译本土化",
            "items": [
                {
                    "type": "segments",
                    "label": "翻译结果",
                    "segments": source_or_segments,
                    "break_after": [],
                }
            ],
        }

    translation = localized_translation or {}
    return {
        "title": "翻译本土化",
        "items": [
            {
                "type": "side_by_side",
                "show_retranslate": True,
                "left": text_item(f"整段{sl}", str(source_or_segments or "")),
                "right": text_item(f"整段本土化{tl}", translation.get("full_text", "")),
            },
            {
                "type": "sentences",
                "label": f"{tl}句子映射",
                "sentences": translation.get("sentences", []),
            },
        ],
    }


def build_shot_translate_artifact(
    shots: list[dict] | None,
    translations: list[dict] | None,
    source_full_text: str,
    localized_translation: dict | None,
    *,
    source_language: str = "zh",
    target_language: str = "en",
) -> dict:
    """Build a translate preview for shot_char_limit with per-shot process details."""
    shot_rows = _merge_shot_translation_rows(shots or [], translations or [])
    translated_count = sum(1 for row in shot_rows if row.get("translated_text"))
    over_limit_count = sum(1 for row in shot_rows if row.get("over_limit"))
    retry_count = sum(int(row.get("retries") or 0) for row in shot_rows)
    items = [
        {
            "type": "shot_translation_summary",
            "label": "时间轴分段翻译过程",
            "total": len(shot_rows),
            "translated_count": translated_count,
            "over_limit_count": over_limit_count,
            "retry_count": retry_count,
        },
        {
            "type": "shot_translations",
            "label": "时间轴分段过程和结果",
            "shots": shot_rows,
        },
    ]
    if source_full_text or localized_translation:
        items.extend(
            build_translate_artifact(
                source_full_text,
                localized_translation or {},
                source_language=source_language,
                target_language=target_language,
            )["items"]
        )
    return {
        "title": "翻译本土化",
        "layout": "shot_translate",
        "items": items,
    }


def _has_asr_translation_rows(translations: list[dict]) -> bool:
    return any(
        isinstance(tr, dict) and str(tr.get("source_text") or "").strip()
        for tr in translations
    )


def _shot_context_description(shot_context: list[dict] | None) -> str:
    descriptions = [
        str(item.get("description") or "").strip()
        for item in (shot_context or [])
        if isinstance(item, dict) and str(item.get("description") or "").strip()
    ]
    return " / ".join(descriptions)


def _rows_from_asr_translations(translations: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for pos, tr in enumerate(translations):
        if not isinstance(tr, dict):
            continue
        source_text = str(tr.get("source_text") or "").strip()
        if not source_text:
            continue
        shot_context = tr.get("shot_context") if isinstance(tr.get("shot_context"), list) else []
        rows.append({
            "index": tr.get("asr_index", tr.get("unit_index", tr.get("shot_index", pos))),
            "start_time": tr.get("start_time", tr.get("start")),
            "end_time": tr.get("end_time", tr.get("end")),
            "duration": tr.get("duration"),
            "source_text": source_text,
            "description": (
                tr.get("description")
                or _shot_context_description(shot_context)
            ),
            "translated_text": tr.get("translated_text") or "",
            "char_limit": tr.get("char_limit"),
            "char_count": tr.get("char_count"),
            "over_limit": bool(tr.get("over_limit")),
            "retries": int(tr.get("retries") or 0),
            "silent": False,
            "shot_context": shot_context,
        })
    return rows


def _merge_shot_translation_rows(shots: list[dict], translations: list[dict]) -> list[dict]:
    if _has_asr_translation_rows(translations):
        return _rows_from_asr_translations(translations)
    by_index = {
        tr.get("shot_index"): tr
        for tr in translations
        if isinstance(tr, dict) and tr.get("shot_index") is not None
    }
    rows: list[dict] = []
    total = max(len(shots), len(translations))
    for pos in range(total):
        shot = shots[pos] if pos < len(shots) and isinstance(shots[pos], dict) else {}
        fallback_tr = translations[pos] if pos < len(translations) and isinstance(translations[pos], dict) else {}
        shot_index = shot.get("index", fallback_tr.get("shot_index", pos))
        tr = by_index.get(shot_index) or fallback_tr or {}
        start_time = shot.get("start", shot.get("start_time"))
        end_time = shot.get("end", shot.get("end_time"))
        rows.append({
            "index": shot_index,
            "start_time": start_time,
            "end_time": end_time,
            "duration": shot.get("duration"),
            "source_text": (
                shot.get("source_text")
                or shot.get("overlap_source_text")
                or shot.get("asr_text")
                or ""
            ),
            "description": shot.get("description") or shot.get("visual_description") or "",
            "translated_text": tr.get("translated_text") or "",
            "char_limit": tr.get("char_limit"),
            "char_count": tr.get("char_count"),
            "over_limit": bool(tr.get("over_limit")),
            "retries": int(tr.get("retries") or 0),
            "silent": bool(shot.get("silent")),
            "shot_context": shot.get("shot_context") or tr.get("shot_context") or [],
        })
    return rows


def build_tts_artifact(tts_script_or_segments, segments: list[dict] | None = None,
                       duration_rounds: list[dict] | None = None) -> dict:
    if segments is None and isinstance(tts_script_or_segments, list):
        # 时长迭代面板由 _task_workbench.html 里的 #ttsDurationLog 专用容器独立渲染，
        # 这里不再把 rounds 塞进 artifact 避免前端出现"暂不支持的预览类型"。
        return {
            "title": "语音生成",
            "items": [
                media_item("audio", "整段配音", "tts_full_audio"),
                {
                    "type": "segments",
                    "label": "配音段落",
                    "segments": tts_script_or_segments,
                    "break_after": [],
                },
            ],
        }

    tts_script = tts_script_or_segments or {}
    items = [
        media_item("audio", "整段配音", "tts_full_audio"),
        text_item("ElevenLabs 文案", tts_script.get("full_text", "")),
        {
            "type": "tts_blocks",
            "label": "朗读块",
            "blocks": tts_script.get("blocks", []),
        },
        {
            "type": "subtitle_chunks",
            "label": "字幕块",
            "chunks": tts_script.get("subtitle_chunks", []),
        },
    ]
    if segments:
        items.append(
            {
                "type": "segments",
                "label": "配音段落映射",
                "segments": segments,
                "break_after": [],
            }
        )
    # 时长迭代面板由前端 #ttsDurationLog 专用容器渲染，不混入 items 列表
    return {"title": "语音生成", "items": items}


def build_subtitle_artifact(
    asr_or_srt,
    corrected_chunks: list[dict] | None = None,
    srt_content: str | None = None,
    target_language: str = "en",
) -> dict:
    tl = _lang(target_language)
    if corrected_chunks is None and isinstance(asr_or_srt, str):
        return {
            "title": "字幕生成",
            "items": [text_item(f"{tl}字幕 SRT", asr_or_srt)],
        }

    asr_result = asr_or_srt or {}
    return {
        "title": "字幕生成",
        "items": [
            {
                "type": "utterances",
                "label": f"{tl} ASR",
                "utterances": asr_result.get("utterances", []),
            },
            text_item(f"{tl} ASR 全文", asr_result.get("full_text", "")),
            {
                "type": "subtitle_chunks",
                "label": "校正后字幕块",
                "chunks": corrected_chunks or [],
            },
            text_item(f"最终{tl} SRT", srt_content or ""),
        ],
    }


def build_compose_artifact() -> dict:
    return {
        "title": "视频合成",
        "items": [
            media_item("video", "硬字幕视频", "hard_video"),
        ],
    }


def build_analysis_artifact(score: dict | None, csk: dict | None,
                            score_prompt: str = "", csk_prompt: str = "",
                            score_error: str = "", csk_error: str = "",
                            model_label: str = "") -> dict:
    """AI 视频分析产物（评分 + CSK 深度分析）。前端按 layout='analysis' 自定义渲染。"""
    return {
        "title": "AI 视频分析",
        "layout": "analysis",
        "score": score,
        "csk": csk,
        "score_prompt": score_prompt,
        "csk_prompt": csk_prompt,
        "score_error": score_error,
        "csk_error": csk_error,
        "model_label": model_label,
    }


def build_export_artifact(manifest_text: str, archive_url: str = "", deploy_url: str = "") -> dict:
    items = []
    if archive_url:
        items.append({"type": "download", "label": "下载 CapCut 工程包", "url": archive_url})
    if deploy_url:
        items.append(action_item("部署到剪映目录", deploy_url))
    items.append(text_item("导出清单", manifest_text or ""))
    return {"title": "CapCut 导出", "items": items}
