from __future__ import annotations


def media_item(kind: str, label: str, artifact: str) -> dict:
    return {"type": kind, "label": label, "artifact": artifact}


def text_item(label: str, content: str) -> dict:
    return {"type": "text", "label": label, "content": content}


def build_extract_artifact() -> dict:
    return {
        "title": "音频提取",
        "items": [
            media_item("audio", "提取音频", "audio_extract"),
        ],
    }


def build_asr_artifact(utterances: list[dict]) -> dict:
    return {
        "title": "语音识别",
        "items": [
            {"type": "utterances", "label": "识别结果", "utterances": utterances},
        ],
    }


def build_alignment_artifact(scene_cuts: list[float], script_segments: list[dict], break_after: list[bool]) -> dict:
    return {
        "title": "分段确认",
        "items": [
            {"type": "scene_cuts", "label": "镜头切换", "values": scene_cuts},
            {
                "type": "segments",
                "label": "分段预览",
                "segments": script_segments,
                "break_after": break_after,
            },
        ],
    }


def build_translate_artifact(segments: list[dict]) -> dict:
    return {
        "title": "翻译本土化",
        "items": [
            {"type": "segments", "label": "翻译预览", "segments": segments},
        ],
    }


def build_tts_artifact(segments: list[dict]) -> dict:
    return {
        "title": "语音生成",
        "items": [
            media_item("audio", "整段配音", "tts_full_audio"),
            {"type": "segments", "label": "配音分段", "segments": segments},
        ],
    }


def build_subtitle_artifact(srt_content: str) -> dict:
    return {
        "title": "字幕生成",
        "items": [
            text_item("SRT 预览", srt_content),
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


def build_export_artifact(manifest_text: str) -> dict:
    return {
        "title": "CapCut 导出",
        "items": [
            {"type": "download", "label": "CapCut 工程包", "url": "__CAPCUT_DOWNLOAD__"},
            text_item("导出清单", manifest_text),
        ],
    }
