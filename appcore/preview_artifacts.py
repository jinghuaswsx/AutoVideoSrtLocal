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
        "title": "Audio Extract",
        "items": [media_item("audio", "Extracted Audio", "audio_extract")],
    }


def build_asr_artifact(utterances: list[dict], source_full_text_zh: str = "") -> dict:
    items = [
        {
            "type": "utterances",
            "label": "ASR Segments",
            "utterances": utterances or [],
        }
    ]
    if source_full_text_zh:
        items.append(text_item("Full Source Text", source_full_text_zh))
    return {"title": "ASR", "items": items}


def build_alignment_artifact(
    scene_cuts: list[float],
    script_segments: list[dict],
    break_after: list[bool],
) -> dict:
    return {
        "title": "Alignment",
        "items": [
            {
                "type": "scene_cuts",
                "label": "Scene Cuts",
                "values": scene_cuts or [],
            },
            {
                "type": "segments",
                "label": "Aligned Segments",
                "segments": script_segments or [],
                "break_after": break_after or [],
            },
        ],
    }


def build_translate_artifact(source_or_segments, localized_translation: dict | None = None) -> dict:
    if localized_translation is None and isinstance(source_or_segments, list):
        return {
            "title": "Translation",
            "items": [
                {
                    "type": "segments",
                    "label": "Translated Segments",
                    "segments": source_or_segments,
                    "break_after": [],
                }
            ],
        }

    translation = localized_translation or {}
    translated_full_text = translation.get("full_text", "")
    return {
        "title": "Translation",
        "items": [
            {
                "type": "side_by_side",
                "show_retranslate": True,
                "left": text_item("Source", str(source_or_segments or "")),
                "right": text_item("Localized", translated_full_text),
            },
            text_item("Localized Full Text", translated_full_text),
            {
                "type": "sentences",
                "label": "Sentence Mapping",
                "sentences": translation.get("sentences", []),
            },
        ],
    }


def build_tts_artifact(tts_script_or_segments, segments: list[dict] | None = None) -> dict:
    if segments is None and isinstance(tts_script_or_segments, list):
        return {
            "title": "TTS",
            "items": [
                media_item("audio", "Full Audio", "tts_full_audio"),
                {
                    "type": "segments",
                    "label": "TTS Segments",
                    "segments": tts_script_or_segments,
                    "break_after": [],
                },
            ],
        }

    tts_script = tts_script_or_segments or {}
    items = [
        media_item("audio", "Full Audio", "tts_full_audio"),
        text_item("TTS Script", tts_script.get("full_text", "")),
        {
            "type": "tts_blocks",
            "label": "TTS Blocks",
            "blocks": tts_script.get("blocks", []),
        },
        {
            "type": "subtitle_chunks",
            "label": "Subtitle Chunks",
            "chunks": tts_script.get("subtitle_chunks", []),
        },
    ]
    if segments:
        items.append(
            {
                "type": "segments",
                "label": "Rendered Segments",
                "segments": segments,
                "break_after": [],
            }
        )
    return {"title": "TTS", "items": items}


def build_subtitle_artifact(
    asr_or_srt,
    corrected_chunks: list[dict] | None = None,
    srt_content: str | None = None,
) -> dict:
    if corrected_chunks is None and isinstance(asr_or_srt, str):
        return {
            "title": "Subtitle",
            "items": [text_item("SRT", asr_or_srt)],
        }

    asr_result = asr_or_srt or {}
    return {
        "title": "Subtitle",
        "items": [
            {
                "type": "utterances",
                "label": "Subtitle ASR",
                "utterances": asr_result.get("utterances", []),
            },
            text_item("Subtitle ASR Full Text", asr_result.get("full_text", "")),
            {
                "type": "subtitle_chunks",
                "label": "Corrected Subtitle Chunks",
                "chunks": corrected_chunks or [],
            },
            text_item("Final SRT", srt_content or ""),
        ],
    }


def build_compose_artifact() -> dict:
    return {
        "title": "Compose",
        "items": [
            media_item("video", "Soft Subtitle Video", "soft_video"),
            media_item("video", "Hard Subtitle Video", "hard_video"),
        ],
    }


def build_export_artifact(manifest_text: str, archive_url: str = "", deploy_url: str = "") -> dict:
    items = [{"type": "download", "label": "Download CapCut Archive", "url": archive_url}]
    if deploy_url:
        items.append(action_item("Deploy to Jianying", deploy_url))
    items.append(text_item("Export Manifest", manifest_text or ""))
    return {"title": "CapCut Export", "items": items}
