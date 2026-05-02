"""Gemini 分镜拆解 + ASR 对齐。

调用 Gemini 视觉模型识别视频镜头切换，输出首尾相接的分镜列表；
再按时间重叠度把 ASR 片段归并到对应分镜上。
"""
from __future__ import annotations

from typing import Any, Dict, List

# 用 alias 便于测试 mock（patch 本模块的 gemini_generate，不触发真实调用）
from appcore.llm_client import invoke_generate as gemini_generate

SHOT_DECOMPOSE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "description": {"type": "string"},
                },
                "required": ["index", "start", "end", "description"],
            },
        },
    },
    "required": ["shots"],
}

SHOT_DECOMPOSE_PROMPT = """你是专业的视频分镜师。请分析这段视频，识别所有镜头切换点，输出分镜列表。

要求：
1. 每个分镜有明确的起止时间（秒，保留 2 位小数）
2. 每个分镜附带一句画面内容描述（20-40字中文）
3. 分镜的 end 必须等于下一个分镜的 start，即首尾相连
4. 第一个分镜从 0.0 开始
5. 最后一个分镜的 end 等于视频总时长 {duration:.2f} 秒

输出 JSON 格式：
{{
  "shots": [
    {{"index": 1, "start": 0.0, "end": 5.2, "description": "..."}}
  ]
}}
"""

DEFAULT_MODEL = "gemini-3.1-pro-preview"


def decompose_shots(
    video_path: str,
    *,
    user_id: int,
    duration_seconds: float,
    model: str = DEFAULT_MODEL,
) -> List[Dict[str, Any]]:
    """调用 Gemini 拆分分镜，返回归一化（首尾对齐、相邻衔接、附 duration）的 shots。"""
    prompt = SHOT_DECOMPOSE_PROMPT.format(duration=duration_seconds)
    # appcore.gemini.generate 的 media 参数接受路径字符串/Path 或其列表。
    # 测试通过 patch pipeline.shot_decompose.gemini_generate 拦截整条调用，
    # 不会真实走到 Gemini。
    invoked = gemini_generate(
        "shot_decompose.run",
        prompt=prompt,
        media=[video_path],
        user_id=user_id,
        model_override=model,
        response_schema=SHOT_DECOMPOSE_SCHEMA,
    )
    response = invoked.get("json") or {}
    shots = response.get("shots") or []
    _normalize_shots(shots, duration_seconds)
    return shots


def _normalize_shots(
    shots: List[Dict[str, Any]], duration_seconds: float,
) -> None:
    """强制分镜首尾衔接 + 每个分镜加 duration 字段。就地修改。"""
    if not shots:
        raise ValueError("Gemini 未返回任何分镜")
    shots[0]["start"] = 0.0
    shots[-1]["end"] = float(duration_seconds)
    for i in range(len(shots) - 1):
        shots[i + 1]["start"] = shots[i]["end"]
    for shot in shots:
        shot["duration"] = round(
            float(shot["end"]) - float(shot["start"]), 3,
        )


def align_asr_to_shots(
    shots: List[Dict[str, Any]],
    asr_segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """把 ASR 片段按时间重叠度归并到分镜中。

    每个分镜附带 source_text（拼接）、asr_segments（原始条目）、silent 标志。
    """
    enriched = [
        dict(s, source_text="", asr_segments=[])
        for s in shots
    ]
    for seg in asr_segments:
        s_start = float(seg.get("start") or 0.0)
        s_end = float(seg.get("end") or 0.0)
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        best_idx = None
        best_overlap = 0.0
        for i, shot in enumerate(enriched):
            ov = max(
                0.0,
                min(s_end, shot["end"]) - max(s_start, shot["start"]),
            )
            if ov > best_overlap:
                best_overlap = ov
                best_idx = i
        if best_idx is None:
            continue
        if enriched[best_idx]["source_text"]:
            enriched[best_idx]["source_text"] += " " + text
        else:
            enriched[best_idx]["source_text"] = text
        enriched[best_idx]["asr_segments"].append(seg)
    for shot in enriched:
        shot["silent"] = not shot["source_text"]
    return enriched
