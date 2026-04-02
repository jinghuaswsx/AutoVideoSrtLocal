from __future__ import annotations


def media_item(item_type: str, label: str, artifact: str) -> dict:
    return {"type": item_type, "label": label, "artifact": artifact}


def text_item(label: str, content: str) -> dict:
    return {"type": "text", "label": label, "content": content or ""}


def action_item(label: str, url: str, method: str = "POST") -> dict:
    return {"type": "action", "label": label, "url": url, "method": method}


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


def build_asr_artifact(utterances: list[dict], source_full_text_zh: str = "") -> dict:
    items = [
        {
            "type": "utterances",
            "label": "中文识别分段",
            "utterances": utterances or [],
        }
    ]
    if source_full_text_zh:
        items.append(text_item("整段中文", source_full_text_zh))
    return {"title": "语音识别", "items": items}


def build_alignment_artifact(
    scene_cuts: list[float],
    script_segments: list[dict],
    break_after: list[bool],
) -> dict:
    return {
        "title": "分段确认",
        "items": [
            {
                "type": "scene_cuts",
                "label": "镜头切换",
                "values": scene_cuts or [],
            },
            {
                "type": "segments",
                "label": "分段预览",
                "segments": script_segments or [],
                "break_after": break_after or [],
            },
        ],
    }


def build_translate_artifact(source_or_segments, localized_translation: dict | None = None) -> dict:
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
                "left": text_item("整段中文", str(source_or_segments or "")),
                "right": text_item("整段本土化英文", translation.get("full_text", "")),
            },
            {
                "type": "sentences",
                "label": "英文句子映射",
                "sentences": translation.get("sentences", []),
            },
        ],
    }


def build_tts_artifact(tts_script_or_segments, segments: list[dict] | None = None) -> dict:
    if segments is None and isinstance(tts_script_or_segments, list):
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
    return {"title": "语音生成", "items": items}


def build_subtitle_artifact(
    asr_or_srt,
    corrected_chunks: list[dict] | None = None,
    srt_content: str | None = None,
) -> dict:
    if corrected_chunks is None and isinstance(asr_or_srt, str):
        return {
            "title": "字幕生成",
            "items": [text_item("英文字幕 SRT", asr_or_srt)],
        }

    asr_result = asr_or_srt or {}
    return {
        "title": "字幕生成",
        "items": [
            {
                "type": "utterances",
                "label": "英文 ASR",
                "utterances": asr_result.get("utterances", []),
            },
            text_item("英文 ASR 全文", asr_result.get("full_text", "")),
            {
                "type": "subtitle_chunks",
                "label": "校正后字幕块",
                "chunks": corrected_chunks or [],
            },
            text_item("最终英文 SRT", srt_content or ""),
        ],
    }


def build_compose_artifact() -> dict:
    return {
        "title": "视频合成",
        "items": [
            media_item("video", "软字幕视频", "soft_video"),
            media_item("video", "硬字幕视频", "hard_video"),
        ],
    }


def build_export_artifact(manifest_text: str, archive_url: str = "", deploy_url: str = "") -> dict:
    items = []
    if archive_url:
        items.append({"type": "download", "label": "下载 CapCut 工程包", "url": archive_url})
    if deploy_url:
        items.append(action_item("部署到剪映目录", deploy_url))
    items.append(text_item("导出清单", manifest_text or ""))
    return {"title": "CapCut 导出", "items": items}
