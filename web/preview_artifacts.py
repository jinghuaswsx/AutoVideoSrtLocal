from __future__ import annotations


def media_item(item_type: str, label: str, artifact: str) -> dict:
    return {"type": item_type, "label": label, "artifact": artifact}


def text_item(label: str, content: str) -> dict:
    return {"type": "text", "label": label, "content": content or ""}


def action_item(label: str, url: str, method: str = "POST") -> dict:
    return {"type": "action", "label": label, "url": url, "method": method}


_LANG_LABELS = {"zh": "中文", "en": "英文", "de": "德文", "fr": "法文"}


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
