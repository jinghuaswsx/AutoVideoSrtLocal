"""视频评分模块：用 Gemini 3.1 Pro 分析成品硬字幕视频，按美国短视频带货要素打分（满分 100）。"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from appcore import gemini
from pipeline.llm_util import parse_json_response

logger = logging.getLogger(__name__)

SCORE_MODEL = "gemini-3.1-pro-preview"

# 评分维度：(key, 中文名, 满分权重)
DIMENSIONS: list[tuple[str, str, int]] = [
    ("hook",            "前 3 秒钩子",     15),
    ("pain_point",      "痛点建立",         10),
    ("product_clarity", "产品清晰度",       15),
    ("selling_point",   "卖点与差异化",     15),
    ("social_proof",    "说服力/社交证明",  10),
    ("cta",             "行动号召 CTA",    10),
    ("pace",            "节奏与剪辑",       10),
    ("subtitle",        "字幕可读性",       8),
    ("av_quality",      "音画质量",         7),
]

_DIM_BLOCK = "\n".join(f"- `{k}`（{n}，满分 {w}）" for k, n, w in DIMENSIONS)

SYSTEM_PROMPT = f"""你是资深的美国短视频带货（TikTok / IG Reels / YouTube Shorts）评审专家。
你从"把这个产品卖给美国消费者"的视角评估视频，面向的是静音浏览、快节奏、注意力短的美国短视频用户，
对前 3 秒钩子、硬字幕可读性、CTA 引导尤其敏感。

评分维度（每一项给 0 到满分之间的整数分）：
{_DIM_BLOCK}

严格要求：
- 每个维度 score ≤ 该维度的满分权重
- total = 各维度 score 之和（0-100），不允许偏差
- 客观直给，不说套话；差的地方要敢给低分并指出原因
- dimensions 的 key 必须与上面列表完全一致
- comment：1-2 句中文，明确指出本维度"好/差在哪里"
- suggestions：3 条具体、可执行的中文改进建议（不要空话）
- summary：1-2 句中文整体印象

只返回一个 JSON 对象，字段：total (int), summary (str), dimensions (array of {{key, score, comment}}), suggestions (array of str)。不要任何其它文字、不要 markdown code fence。"""

USER_PROMPT = "请按系统指令评估这个成品带货视频（已内嵌硬字幕），返回纯 JSON。"


def score_video(video_path: str | Path, *, user_id: int | None = None,
                project_id: str | None = None) -> dict:
    """对硬字幕视频打分，返回：
    {total, summary, dimensions:[{key,name,weight,score,comment}], suggestions, model, scored_at}
    """
    p = Path(video_path)
    if not p.is_file():
        raise FileNotFoundError(f"视频不存在：{p}")

    raw = gemini.generate(
        USER_PROMPT,
        system=SYSTEM_PROMPT,
        media=p,
        temperature=0.2,
        max_output_tokens=4096,
        user_id=user_id,
        project_id=project_id,
        service="gemini_video_analysis",
        default_model=SCORE_MODEL,
    )
    if isinstance(raw, dict):
        data = raw
    else:
        data = parse_json_response(raw)

    weights = {k: w for k, _, w in DIMENSIONS}
    names = {k: n for k, n, _ in DIMENSIONS}

    dims_out: list[dict] = []
    computed_total = 0
    seen: set[str] = set()
    for d in data.get("dimensions", []):
        key = (d.get("key") or "").strip()
        if key not in weights or key in seen:
            continue
        seen.add(key)
        try:
            score = int(d.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        score = max(0, min(score, weights[key]))
        dims_out.append({
            "key": key,
            "name": names[key],
            "weight": weights[key],
            "score": score,
            "comment": (d.get("comment") or "").strip(),
        })
        computed_total += score

    # 模型漏掉的维度补 0 分，保证前端维度展示完整
    for k, n, w in DIMENSIONS:
        if k not in seen:
            dims_out.append({"key": k, "name": n, "weight": w, "score": 0,
                             "comment": "模型未评估该维度"})

    return {
        "total": computed_total,
        "summary": (data.get("summary") or "").strip(),
        "dimensions": dims_out,
        "suggestions": [s.strip() for s in (data.get("suggestions") or [])
                        if isinstance(s, str) and s.strip()][:5],
        "model": SCORE_MODEL,
        "scored_at": datetime.utcnow().isoformat() + "Z",
    }
